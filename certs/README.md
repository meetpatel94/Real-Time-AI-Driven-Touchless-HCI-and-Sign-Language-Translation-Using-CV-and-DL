# certs/

This folder holds **local development TLS certificates** used only to serve
GestureForge over `https://` for the Connect two-device LAN test (mobile
browsers require a secure context for camera access).

**Never commit real certificate/key files here** — `*.pem`, `*.key`, `*.crt`,
`*.p12`, `*.pfx` are all git-ignored.

## Generate a certificate with mkcert (recommended)

1. Install mkcert: https://github.com/FiloSottile/mkcert#installation
2. Install the local CA once: `mkcert -install`
3. Generate a certificate that covers localhost **and** your PC's LAN IP
   (find the LAN IP with `ipconfig` on Windows or `ip addr` / `ifconfig` on
   macOS/Linux, e.g. `192.168.29.98`):

   ```bash
   mkcert -cert-file certs/gestureforge-lan-cert.pem \
          -key-file certs/gestureforge-lan-key.pem \
          127.0.0.1 localhost 192.168.29.98
   ```

4. Start the app (`python app.py`). It automatically detects and uses
   `certs/gestureforge-lan-cert.pem` / `certs/gestureforge-lan-key.pem`.

If your LAN IP changes (different Wi-Fi network), regenerate the certificate
with the new IP.

## No certificate yet?

If these files are missing, `python app.py` automatically falls back to
Flask's **ad-hoc self-signed certificate** (requires `pip install pyopenssl`)
so HTTPS still works for LAN testing — DEVELOPMENT/LAN TESTING ONLY, browsers
will show a one-time "connection is not private" warning to accept.
