"""Development-only HTTP/HTTPS transport helpers for GestureForge Connect.

Plain-HTTP LAN reachability is the default contract of ``python app.py``:
the Flask dev server must answer ``http://`` on ``0.0.0.0:5000`` so the page
is reachable from the PC's LAN address (e.g.
``http://192.168.29.98:5000/connect``) and not only from ``127.0.0.1``.

Mobile browsers only expose ``navigator.mediaDevices.getUserMedia()`` (the
camera API) on a *secure context*. ``https://127.0.0.1`` counts as secure,
but a plain ``http://192.168.x.x`` LAN address does not, which is why the
phone shows "This browser does not provide a local camera." while the PC
(using 127.0.0.1) works fine. HTTPS for that second-device camera test is
therefore supported here, but it is strictly **opt-in** — it must never
silently take over port 5000, because a TLS-only port breaks every plain
``http://`` LAN URL (this exact failure made ``http://<LAN-IP>:5000``
unreachable while ``http://127.0.0.1:5000`` kept "working" in browsers that
transparently upgrade localhost to HTTPS).

Resolution order for the SSL context used by ``app.run(ssl_context=...)``:

1. ``GESTUREFORGE_DISABLE_HTTPS=1`` → plain HTTP (e.g. CI or environments
   where TLS is handled by an external reverse proxy).
2. Explicit certificate/key files (recommended — e.g. mkcert output).
   Configurable via the ``GESTUREFORGE_SSL_CERT`` / ``GESTUREFORGE_SSL_KEY``
   environment variables, defaulting to ``certs/gestureforge-lan-cert.pem``
   and ``certs/gestureforge-lan-key.pem`` in the project root.
3. ``GESTUREFORGE_FORCE_HTTPS=1`` → Flask/Werkzeug's ``adhoc`` self-signed
   certificate (requires the ``pyOpenSSL`` package) — DEVELOPMENT / LAN
   TESTING ONLY. Browsers show a "not private" warning that must be
   manually accepted once per device; this is expected for a self-signed
   dev certificate.
4. Default: plain HTTP on ``0.0.0.0``. This keeps ``http://127.0.0.1:5000``
   and ``http://<LAN-IP>:5000`` working identically. Phone-camera HTTPS
   testing (the next step after LAN reachability) just needs option 2 or 3.

This module only decides *how the Flask dev server binds its socket*
(HTTP vs HTTPS, and with which certificate). It does not touch gesture
recognition, MediaPipe, custom gestures, WebSocket routing/rooms, or any
existing UI behavior.
"""

import os
import socket

from services.logging_service import logger

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(BASE_DIR, "certs")

DEFAULT_CERT_PATH = os.path.join(CERT_DIR, "gestureforge-lan-cert.pem")
DEFAULT_KEY_PATH = os.path.join(CERT_DIR, "gestureforge-lan-key.pem")


def _configured_cert_paths():
    cert = os.environ.get("GESTUREFORGE_SSL_CERT", "").strip() or DEFAULT_CERT_PATH
    key = os.environ.get("GESTUREFORGE_SSL_KEY", "").strip() or DEFAULT_KEY_PATH
    return cert, key


def detect_lan_ip():
    """Best-effort LAN IPv4 address for the two-device Connect test."""
    candidates = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No packets are actually sent; connect() just makes the OS pick the
        # outbound route/interface it would use for that destination.
        probe.connect(("8.8.8.8", 80))
        candidates.append(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address.startswith(("192.168.", "10.", "172.")):
                candidates.append(address)
    except Exception:
        pass
    for address in candidates:
        if not address.startswith("127."):
            return address
    return None


def _pyopenssl_available():
    try:
        import OpenSSL  # noqa: F401
        return True
    except Exception:
        return False


def resolve_ssl_context(lan_ip=None):
    """Return (ssl_context, mode, detail) for ``app.run(ssl_context=...)``.

    ``mode`` is one of ``"cert"``, ``"adhoc"``, or ``"http"`` so the caller
    can print an accurate startup message. ``ssl_context`` is ``None`` when
    ``mode == "http"``.

    Plain HTTP is the default so ``http://<LAN-IP>:5000`` is always
    reachable. HTTPS activates only when explicitly requested: local
    certificate files on disk, or ``GESTUREFORGE_FORCE_HTTPS=1`` (ad-hoc
    self-signed). A TLS-only dev port must never be enabled implicitly —
    that silently breaks every plain-HTTP LAN URL.
    """
    if os.environ.get("GESTUREFORGE_DISABLE_HTTPS", "").strip() in ("1", "true", "True"):
        return None, "http", "HTTPS disabled via GESTUREFORGE_DISABLE_HTTPS"

    cert_path, key_path = _configured_cert_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return (cert_path, key_path), "cert", f"{cert_path}, {key_path}"

    if os.environ.get("GESTUREFORGE_FORCE_HTTPS", "").strip() in ("1", "true", "True"):
        if _pyopenssl_available():
            return "adhoc", "adhoc", "Flask adhoc self-signed certificate (pyOpenSSL)"
        return None, "http", "GESTUREFORGE_FORCE_HTTPS=1 but pyOpenSSL is not installed"

    return None, "http", "plain HTTP (default)"


def print_startup_banner(host, port, mode, detail, lan_ip):
    """Print the Local/LAN URLs in the exact format Connect testing needs."""
    scheme = "http" if mode == "http" else "https"
    lan_display = lan_ip if lan_ip else "(could not auto-detect — see ipconfig / ip addr)"

    print("")
    print("GestureForge server started")
    print("")
    print("Local:")
    print(f"{scheme}://127.0.0.1:{port}")
    print("")
    print("LAN:")
    print(f"{scheme}://{lan_display}:{port}")
    print("")
    print("Connect:")
    print(f"{scheme}://{lan_display}:{port}/connect")
    print("")

    if mode == "cert":
        print(f"HTTPS: enabled with local development certificate ({detail}).")
        print("Use the HTTPS LAN URL on the second device (phone camera needs a")
        print("secure context).")
    elif mode == "adhoc":
        print("HTTPS: enabled with Flask's ad-hoc self-signed certificate.")
        print("  >> DEVELOPMENT / LAN TESTING ONLY — do not use this in production. <<")
        print("  Each browser/device will show a private-connection warning once;")
        print("  accept/continue to proceed (this is expected for a self-signed cert).")
        print("Use the HTTPS LAN URL on the second device.")
    else:
        print(f"Transport: {detail}.")
        print("Phone-camera HTTPS testing comes next: drop a mkcert certificate")
        print("pair into certs/ (see certs/README.md) or set")
        print("GESTUREFORGE_FORCE_HTTPS=1, then restart — the URLs above become https://.")
        print("If the LAN URL is unreachable from another device but 127.0.0.1 works,")
        print("allow Python (inbound TCP port 5000) through the PC's firewall.")
    print("=" * 64)
    print("")
