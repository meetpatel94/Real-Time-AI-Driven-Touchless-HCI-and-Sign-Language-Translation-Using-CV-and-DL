"""Development-only HTTPS/TLS helpers for the GestureForge Connect LAN test.

Mobile browsers only expose ``navigator.mediaDevices.getUserMedia()`` (the
camera API) on a *secure context*. ``https://127.0.0.1`` counts as secure,
but a plain ``http://192.168.x.x`` LAN address does not, which is why the
phone shows "This browser does not provide a local camera." while the PC
(using 127.0.0.1) works fine.

This module only decides *how the Flask dev server binds its socket*
(HTTP vs HTTPS, and with which certificate). It does not touch gesture
recognition, MediaPipe, custom gestures, WebSocket routing/rooms, or any
existing UI behavior — those all keep working exactly the same, just now
reachable over ``https://``/``wss://`` on the LAN as well as on localhost.

Resolution order for the SSL context used by ``app.run(..., ssl_context=...)``:

1. Explicit certificate/key files (recommended — e.g. mkcert output).
   Configurable via the ``GESTUREFORGE_SSL_CERT`` / ``GESTUREFORGE_SSL_KEY``
   environment variables, defaulting to ``certs/gestureforge-lan-cert.pem``
   and ``certs/gestureforge-lan-key.pem`` in the project root.
2. Flask/Werkzeug's ``adhoc`` self-signed certificate (requires the
   ``pyOpenSSL`` package) — DEVELOPMENT / LAN TESTING ONLY. Browsers will
   show a "not private" warning that must be manually accepted once per
   device; this is expected for a self-signed dev certificate.
3. Plain HTTP, only if HTTPS is explicitly disabled or unavailable. Mobile
   camera access will then fail on any address other than
   ``127.0.0.1``/``localhost``, exactly the bug this module fixes.

Set ``GESTUREFORGE_DISABLE_HTTPS=1`` to force plain HTTP (e.g. for CI or
environments where TLS is handled by an external reverse proxy).
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
    """
    if os.environ.get("GESTUREFORGE_DISABLE_HTTPS", "").strip() in ("1", "true", "True"):
        return None, "http", "HTTPS disabled via GESTUREFORGE_DISABLE_HTTPS"

    cert_path, key_path = _configured_cert_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return (cert_path, key_path), "cert", f"{cert_path}, {key_path}"

    if _pyopenssl_available():
        return "adhoc", "adhoc", "Flask adhoc self-signed certificate (pyOpenSSL)"

    return None, "http", "no certificate found and pyOpenSSL is not installed"


def print_startup_banner(host, port, mode, detail, lan_ip):
    """Print the Local/LAN URLs in the exact format Connect testing needs."""
    scheme = "http" if mode == "http" else "https"

    print("")
    print("=" * 64)
    print("GestureForge Connect — LAN camera test URLs")
    print("=" * 64)
    print("Local URL:")
    print(f"  {scheme}://127.0.0.1:{port}/connect")
    print("")
    if lan_ip:
        print("LAN URL:")
        print(f"  {scheme}://{lan_ip}:{port}/connect")
    else:
        print("LAN URL:")
        print("  (could not auto-detect a LAN IP — run `ipconfig`/`ip addr`")
        print(f"   and use {scheme}://<LAN-IP>:{port}/connect)")
    print("")

    if mode == "cert":
        print(f"HTTPS: enabled with local development certificate ({detail}).")
        print("Use the HTTPS LAN URL on the second device.")
    elif mode == "adhoc":
        print("HTTPS: enabled with Flask's ad-hoc self-signed certificate.")
        print("  >> DEVELOPMENT / LAN TESTING ONLY — do not use this in production. <<")
        print("  Each browser/device will show a private-connection warning once;")
        print("  accept/continue to proceed (this is expected for a self-signed cert).")
        print("Use the HTTPS LAN URL on the second device.")
    else:
        print("HTTPS: NOT enabled (" + detail + ").")
        print("  Mobile browsers require a secure (HTTPS) context for camera access")
        print("  on any address other than 127.0.0.1/localhost, so the phone camera")
        print("  will likely fail with an HTTP LAN URL.")
        print("  Recommended: generate a local certificate, for example with mkcert:")
        print("")
        print(f"    mkcert -install")
        print(
            f"    mkcert -cert-file certs/gestureforge-lan-cert.pem "
            f"-key-file certs/gestureforge-lan-key.pem 127.0.0.1 localhost "
            f"{lan_ip or '<LAN-IP>'}"
        )
        print("")
        print("  Or install pyOpenSSL for a quick self-signed fallback:")
        print("    pip install pyopenssl")
    print("=" * 64)
    print("")
