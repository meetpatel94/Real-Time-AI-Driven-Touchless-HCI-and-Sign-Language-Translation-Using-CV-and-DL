"""Development-only HTTP/HTTPS transport helpers for GestureForge Connect.

Mobile browsers only expose ``navigator.mediaDevices.getUserMedia()`` (the
camera API) on a *secure context*. ``https://127.0.0.1`` counts as secure,
but a plain ``http://192.168.x.x`` LAN address does not, which is why the
phone shows a camera problem on the HTTP LAN URL while the PC (using
``127.0.0.1``) works fine. The two-device camera test (PC = User 1, phone =
User 2) therefore needs the app served over ``https://<LAN-IP>:5000``.

This module decides *how the Flask dev server binds its socket* (HTTP vs
HTTPS, and with which certificate). It does not touch gesture recognition,
MediaPipe, custom gestures, WebSocket routing/rooms, or any existing UI
behavior.

HTTPS policy for ``python app.py``:

1. ``GESTUREFORGE_DISABLE_HTTPS=1`` → plain HTTP (e.g. CI or an environment
   where TLS is terminated by an external reverse proxy).
2. A certificate/key pair present on disk → **automatic HTTPS** with that
   pair. The files are ``certs/gestureforge-lan-cert.pem`` and
   ``certs/gestureforge-lan-key.pem`` by default (override with
   ``GESTUREFORGE_SSL_CERT`` / ``GESTUREFORGE_SSL_KEY``). This is the
   supported LAN camera-testing path: the certificate is generated with
   **mkcert** (see ``scripts/generate_lan_certificate.py`` and
   ``certs/README.md``) and must cover ``127.0.0.1``, ``localhost`` and
   the current LAN IP (no hardcoded IPs).
3. Default (no certificate files) → plain HTTP on ``0.0.0.0``. Local
   desktop development keeps working exactly as before; the phone camera
   step simply needs a certificate dropped into ``certs/`` first.

This is **local LAN development infrastructure only** — it is not a
production TLS deployment and the server must never be exposed to the
public internet.

Deliberate constraints:

* No certificate is ever generated inside Python (no fake/self-signed
  certificates baked into the app).
* No ad-hoc SSL is used for LAN camera testing. An ad-hoc self-signed
  certificate is not trusted by Android/iOS in a way that unlocks
  ``getUserMedia``, so it would only produce confusing
  "certificate error" states on the phone. If a developer sets
  ``GESTUREFORGE_FORCE_HTTPS=1`` without certificate files, the server
  stays on plain HTTP and the startup banner explains why.
* A broken (unreadable/unloadable) certificate pair also falls back to
  plain HTTP with a clear warning instead of crashing the server.
"""

import os
import socket
import ssl

from services.logging_service import logger

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(BASE_DIR, "certs")

DEFAULT_CERT_PATH = os.path.join(CERT_DIR, "gestureforge-lan-cert.pem")
DEFAULT_KEY_PATH = os.path.join(CERT_DIR, "gestureforge-lan-key.pem")

# Hosts every development certificate must cover (the LAN IP is appended
# dynamically by :func:`certificate_sans` — it is never hardcoded here).
BASE_CERT_SANES = ("127.0.0.1", "localhost")


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


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


def certificate_sans(lan_ip=None):
    """Return the SAN list a development certificate should cover.

    Always includes ``127.0.0.1`` and ``localhost``; the detected (or
    supplied) LAN IP is appended when available so the same certificate
    serves the PC locally and the phone over the Wi-Fi LAN. Values are
    de-duplicated, order-preserving.
    """
    sans = list(BASE_CERT_SANES)
    candidate = str(lan_ip or "").strip()
    if candidate and candidate not in sans and not candidate.startswith("127."):
        sans.append(candidate)
    return sans


def _cert_pair_loadable(cert_path: str, key_path: str):
    """Validate the pair with the stdlib ``ssl`` module (no crypto libs)."""
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        return True, None
    except Exception as exc:  # broken pair → explain, never crash the server
        return False, str(exc)


def resolve_ssl_context(lan_ip=None):
    """Return (ssl_context, mode, detail) for ``app.run(ssl_context=...)``.

    ``mode`` is ``"cert"`` (automatic HTTPS with the local development
    certificate) or ``"http"`` (plain HTTP fallback). ``ssl_context`` is
    ``None`` when ``mode == "http"``.

    Plain HTTP stays the default so ``http://127.0.0.1:5000`` and
    ``http://<LAN-IP>:5000`` keep working for normal local development.
    HTTPS activates automatically — and only when a real certificate pair
    exists in ``certs/`` (or at the env-configured paths), which is the
    mkcert-generated pair used for the LAN camera test.
    """
    if _flag_enabled("GESTUREFORGE_DISABLE_HTTPS"):
        return None, "http", "HTTPS disabled via GESTUREFORGE_DISABLE_HTTPS"

    cert_path, key_path = _configured_cert_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        loadable, problem = _cert_pair_loadable(cert_path, key_path)
        if loadable:
            return (cert_path, key_path), "cert", f"{cert_path} + {key_path}"
        logger.warning(f"Connect HTTPS certificate is unusable ({problem}); "
                       "falling back to plain HTTP.")
        return None, "http", (
            f"certificate pair found but invalid ({problem}) — "
            "regenerate it with scripts/generate_lan_certificate.py"
        )

    if _flag_enabled("GESTUREFORGE_FORCE_HTTPS"):
        return None, "http", (
            "GESTUREFORGE_FORCE_HTTPS is set, but ad-hoc/self-signed SSL is "
            "no longer supported for LAN camera testing. Generate a mkcert "
            "certificate into certs/ (scripts/generate_lan_certificate.py) "
            "and restart; until then the server stays on plain HTTP."
        )

    return None, "http", "plain HTTP (no development certificate in certs/)"


def print_startup_banner(host, port, mode, detail, lan_ip):
    """Print the Local/LAN URLs and the active HTTP/HTTPS mode."""
    scheme = "http" if mode == "http" else "https"
    lan_display = lan_ip if lan_ip else "(could not auto-detect — see ipconfig / ip addr)"

    print("")
    print("GestureForge server started")
    print("")
    if mode == "cert":
        print("Transport mode: HTTPS (local mkcert development certificate)")
    else:
        print("Transport mode: HTTP (plain — no development certificate found)")
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
        print(f"HTTPS: enabled with {detail}.")
        print("Phone camera testing: open the HTTPS LAN URL on the second device")
        print("(the phone must trust the mkcert CA — see the README section")
        print('"Two-Device Camera Testing over LAN").')
    else:
        print(f"Transport: {detail}.")
        print("Plain HTTP keeps desktop development working, but a phone opening")
        print(f"{scheme}://{lan_display}:{port}/connect is NOT a secure context, so")
        print("the phone camera (getUserMedia) stays disabled there.")
        print("For the two-device camera test, generate a mkcert certificate")
        print("covering 127.0.0.1, localhost and your LAN IP into certs/:")
        print("    python scripts/generate_lan_certificate.py")
        print("then restart — the server switches to HTTPS automatically and the")
        print("URLs above become https://.")
    print("This is local LAN development infrastructure only — never expose this")
    print("dev server to the public internet.")
    print("If the LAN URL is unreachable from another device but 127.0.0.1 works,")
    print("allow Python (inbound TCP port 5000) through the PC's firewall.")
    print("=" * 64)
    print("")
