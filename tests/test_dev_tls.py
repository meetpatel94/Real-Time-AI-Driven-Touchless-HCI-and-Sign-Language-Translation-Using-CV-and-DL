"""Tests for the development TLS transport selection (Connect LAN HTTPS).

Covers the exact startup contract for the two-device camera test:

* Without certificate files the dev server stays on plain HTTP
  (desktop localhost development keeps working).
* When ``certs/gestureforge-lan-cert.pem`` / ``gestureforge-lan-key.pem``
  (or the env-configured pair) exist and are loadable, the server uses
  HTTPS automatically — no certificate generation inside Python, no ad-hoc
  self-signed fallback.
* ``GESTUREFORGE_DISABLE_HTTPS=1`` always forces plain HTTP.
* A broken certificate pair degrades to plain HTTP with an explanation.
* The startup banner clearly distinguishes HTTP and HTTPS mode and prints
  Local / LAN / Connect URLs with the right scheme.
* The certificate SAN list always covers 127.0.0.1 + localhost and the
  dynamically detected LAN IP (no hardcoded addresses).
* ``GET /api/connect/health`` reports the active transport.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from services import dev_tls
from services.dev_tls import (
    certificate_sans,
    detect_lan_ip,
    print_startup_banner,
    resolve_ssl_context,
)

OPENSSL_AVAILABLE = shutil.which("openssl") is not None

# Env vars that would leak from the host into these tests.
_TLS_ENV_VARS = (
    "GESTUREFORGE_DISABLE_HTTPS",
    "GESTUREFORGE_FORCE_HTTPS",
    "GESTUREFORGE_SSL_CERT",
    "GESTUREFORGE_SSL_KEY",
)


def _clean_env(**overrides):
    env = {name: "" for name in _TLS_ENV_VARS}
    env.update(overrides)
    return env


def _make_cert_pair(directory):
    """Create a real self-signed pair with openssl (skip when unavailable)."""
    cert_path = os.path.join(directory, "gestureforge-lan-cert.pem")
    key_path = os.path.join(directory, "gestureforge-lan-key.pem")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "2", "-nodes",
            "-subj", "/CN=gestureforge-lan-test",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path, key_path


class CertificateSansTest(unittest.TestCase):
    def test_always_covers_localhost_and_loopback(self):
        sans = certificate_sans(None)
        self.assertEqual(sans[:2], ["127.0.0.1", "localhost"])

    def test_appends_detected_lan_ip(self):
        sans = certificate_sans("192.168.29.98")
        self.assertEqual(sans, ["127.0.0.1", "localhost", "192.168.29.98"])

    def test_does_not_duplicate_or_accept_loopback_as_lan_ip(self):
        self.assertEqual(certificate_sans("127.0.0.1"), ["127.0.0.1", "localhost"])
        self.assertEqual(certificate_sans("localhost"), ["127.0.0.1", "localhost"])
        self.assertEqual(certificate_sans("  "), ["127.0.0.1", "localhost"])

    def test_no_hardcoded_lan_address(self):
        with open(dev_tls.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("192.168.29.98", source)


class DetectLanIpTest(unittest.TestCase):
    def test_returns_none_or_ipv4_string(self):
        value = detect_lan_ip()
        if value is None:
            return
        self.assertIsInstance(value, str)
        parts = value.split(".")
        self.assertEqual(len(parts), 4)
        self.assertTrue(all(part.isdigit() and 0 <= int(part) <= 255 for part in parts))
        self.assertFalse(value.startswith("127."))


class ResolveSslContextTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="gf-dev-tls-test-")
        self.cert_path = os.path.join(self.tempdir, "gestureforge-lan-cert.pem")
        self.key_path = os.path.join(self.tempdir, "gestureforge-lan-key.pem")
        self.env_patch = None

    def tearDown(self):
        if self.env_patch:
            self.env_patch.stop()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _resolve(self, **env):
        base = _clean_env(**env)
        self.env_patch = mock.patch.dict(os.environ, base, clear=False)
        self.env_patch.start()
        # Point the configured pair at the temp dir for determinism.
        os.environ["GESTUREFORGE_SSL_CERT"] = self.cert_path
        os.environ["GESTUREFORGE_SSL_KEY"] = self.key_path
        return resolve_ssl_context("192.168.29.98")

    def test_default_is_plain_http_without_certificates(self):
        ssl_context, mode, detail = self._resolve()
        self.assertIsNone(ssl_context)
        self.assertEqual(mode, "http")
        self.assertIn("plain HTTP", detail)

    @unittest.skipUnless(OPENSSL_AVAILABLE, "openssl binary not available")
    def test_valid_certificate_pair_enables_https_automatically(self):
        _make_cert_pair(self.tempdir)
        ssl_context, mode, detail = self._resolve()
        self.assertEqual(mode, "cert")
        self.assertEqual(ssl_context, (self.cert_path, self.key_path))
        self.assertIn(self.cert_path, detail)

    @unittest.skipUnless(OPENSSL_AVAILABLE, "openssl binary not available")
    def test_disable_https_flag_wins_over_valid_certificate(self):
        _make_cert_pair(self.tempdir)
        ssl_context, mode, _ = self._resolve(GESTUREFORGE_DISABLE_HTTPS="1")
        self.assertIsNone(ssl_context)
        self.assertEqual(mode, "http")

    def test_force_https_without_certificates_stays_http_no_adhoc(self):
        ssl_context, mode, detail = self._resolve(GESTUREFORGE_FORCE_HTTPS="1")
        self.assertIsNone(ssl_context)
        self.assertEqual(mode, "http")
        self.assertIn("ad-hoc", detail)

    def test_broken_certificate_pair_falls_back_to_http(self):
        with open(self.cert_path, "w", encoding="utf-8") as handle:
            handle.write("not a certificate\n")
        with open(self.key_path, "w", encoding="utf-8") as handle:
            handle.write("not a key\n")
        ssl_context, mode, detail = self._resolve()
        self.assertIsNone(ssl_context)
        self.assertEqual(mode, "http")
        self.assertIn("invalid", detail)

    def test_one_missing_file_stays_http(self):
        with open(self.cert_path, "w", encoding="utf-8") as handle:
            handle.write("only a cert file\n")
        ssl_context, mode, _ = self._resolve()
        self.assertIsNone(ssl_context)
        self.assertEqual(mode, "http")


class StartupBannerTest(unittest.TestCase):
    def _capture_banner(self, mode, detail):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print_startup_banner("0.0.0.0", 5000, mode, detail, "192.168.29.98")
        return buffer.getvalue()

    def test_https_banner(self):
        text = self._capture_banner("cert", "certs/gestureforge-lan-cert.pem")
        self.assertIn("GestureForge server started", text)
        self.assertIn("Transport mode: HTTPS", text)
        self.assertIn("https://127.0.0.1:5000", text)
        self.assertIn("https://192.168.29.98:5000", text)
        self.assertIn("https://192.168.29.98:5000/connect", text)
        self.assertNotIn("http://192.168.29.98", text)

    def test_http_banner(self):
        text = self._capture_banner("http", "plain HTTP (no development certificate in certs/)")
        self.assertIn("GestureForge server started", text)
        self.assertIn("Transport mode: HTTP", text)
        self.assertIn("http://127.0.0.1:5000", text)
        self.assertIn("http://192.168.29.98:5000/connect", text)
        # Guidance to the mkcert helper is present for the camera step.
        self.assertIn("generate_lan_certificate", text)

    def test_banner_mentions_local_lan_and_connect_blocks(self):
        text = self._capture_banner("cert", "x")
        for label in ("Local:", "LAN:", "Connect:"):
            self.assertIn(label, text)


@unittest.skipUnless(dev_tls is not None, "dev_tls module import failed")
class ConnectHealthTransportTest(unittest.TestCase):
    """GET /api/connect/health reports the active http/https transport."""

    def setUp(self):
        try:
            from flask import Flask
        except ImportError:  # pragma: no cover
            self.skipTest("Flask is not installed")
        from routes.connect_routes import connect_bp

        self.app = Flask(__name__)
        self.app.register_blueprint(connect_bp)
        self.client = self.app.test_client()

    def _health(self, **extra):
        response = self.client.get("/api/connect/health", **extra)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)

    def test_health_reports_http_transport_by_default(self):
        payload = self._health()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["ws_path"], "/ws/connect")

    def test_health_reports_https_transport_over_tls(self):
        # base_url's scheme sets wsgi.url_scheme, mirroring a request the
        # dev server answers through its TLS socket.
        payload = self._health(base_url="https://gestureforge.local/")
        self.assertEqual(payload["transport"], "https")


if __name__ == "__main__":
    unittest.main()
