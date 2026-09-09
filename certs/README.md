# certs/

This folder holds the **local development TLS certificate** used to serve
GestureForge over `https://` for the Connect two-device camera test (mobile
browsers require a secure context for camera access). The server enables
HTTPS **automatically** when the two files below exist:

```
certs/gestureforge-lan-cert.pem
certs/gestureforge-lan-key.pem
```

**Never commit real certificate/key files here** — `*.pem`, `*.key`, `*.crt`,
`*.p12`, `*.pfx` are all git-ignored. Each developer generates their own
local certificate.

## Generate the certificate with mkcert (recommended)

1. Install mkcert: https://github.com/FiloSottile/mkcert#installation
2. Install the local CA once: `mkcert -install`
3. Generate the certificate — the helper script detects your current LAN IP
   automatically and writes the exact file names above:

   ```bash
   python scripts/generate_lan_certificate.py
   ```

   or run mkcert directly (the certificate must cover `127.0.0.1`,
   `localhost` **and** your PC's LAN IP — find it with `ipconfig` on Windows
   or `ip addr` / `ifconfig` on macOS/Linux, e.g. `192.168.29.98`):

   ```bash
   mkcert -cert-file certs/gestureforge-lan-cert.pem \
          -key-file certs/gestureforge-lan-key.pem \
          127.0.0.1 localhost 192.168.29.98
   ```

4. Start the app (`python app.py`). It detects the certificate pair and
   starts with HTTPS automatically. The startup banner prints:

   ```
   GestureForge server started

   Transport mode: HTTPS (local mkcert development certificate)

   Local:
   https://127.0.0.1:5000

   LAN:
   https://192.168.29.98:5000

   Connect:
   https://192.168.29.98:5000/connect
   ```

If your LAN IP changes (different Wi-Fi network), regenerate the certificate
with the new IP (`python scripts/generate_lan_certificate.py`).

## No certificate yet?

With no certificate files, `python app.py` serves **plain HTTP on
0.0.0.0:5000** — normal desktop development keeps working
(`http://127.0.0.1:5000`). A phone opening the HTTP LAN URL is **not** a
secure context, so its camera stays disabled there; generate the mkcert
certificate for the two-device camera test.

There is deliberately **no** in-Python certificate generation and **no**
ad-hoc self-signed fallback: an untrusted self-signed certificate does not
reliably unlock `getUserMedia` on Android/iOS, so it only produces
confusing certificate errors on the phone.

If you ever need to force plain HTTP while a certificate pair exists (e.g.
behind an external HTTPS-terminating proxy), set
`GESTUREFORGE_DISABLE_HTTPS=1` before starting the app.

## Troubleshooting

* **Phone shows a certificate error** — the phone does not trust the mkcert
  CA yet. Install/trust the mkcert CA on the test device (see the README
  section "Two-Device Camera Testing over LAN"). Do not just proceed past
  the warning: an untrusted certificate is not a valid secure context for
  camera access.
* **PC browser shows a warning** — `mkcert -install` was not run on the PC
  (it installs the local CA into the OS/browser trust store).
* **Certificate expired** — mkcert leaf certificates are valid for ~2 years;
  just regenerate them with the script.
