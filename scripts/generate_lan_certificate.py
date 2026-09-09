#!/usr/bin/env python3
"""Generate the GestureForge LAN development certificate with mkcert.

This is the developer helper for the two-device camera test (PC + phone on
the same Wi-Fi). It does two things:

1. Dynamically detects the PC's current LAN IP (no hardcoded addresses)
   and builds the SAN list ``127.0.0.1 localhost <LAN-IP>``.
2. Runs **mkcert** with that list and writes the pair to the exact file
   names the server auto-detects:

       certs/gestureforge-lan-cert.pem
       certs/gestureforge-lan-key.pem

When those files exist, ``python app.py`` starts with HTTPS
automatically (see services/dev_tls.py). No certificate is ever generated
by Python itself and no ad-hoc/self-signed SSL is used for LAN camera
testing — mkcert creates a real local CA-signed certificate that a PC
browser (and a phone that trusts the mkcert CA) accepts as a secure
context, which is what unlocks ``getUserMedia`` on the phone.

Usage:
    python scripts/generate_lan_certificate.py
    python scripts/generate_lan_certificate.py --ip 192.168.29.98
    python scripts/generate_lan_certificate.py --ip 192.168.29.98 --add 10.0.0.5
    python scripts/generate_lan_certificate.py --print-command

After generating, restart the server (``python app.py``) and open
``https://<LAN-IP>:5000/connect`` on the PC and the phone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from services.dev_tls import (  # noqa: E402
    CERT_DIR,
    DEFAULT_CERT_PATH,
    DEFAULT_KEY_PATH,
    _cert_pair_loadable,
    certificate_sans,
    detect_lan_ip,
)


def _print(text: str = "") -> None:
    print(text)


def _mkcert_command(mkcert, cert_file, key_file, sans):
    return [
        mkcert,
        "-cert-file", cert_file,
        "-key-file", key_file,
        *sans,
    ]


def _format_command(cmd) -> str:
    # Keep it copy-pasteable (from the repo root) on Windows cmd and POSIX
    # shells alike. Paths inside the repo are shown relative to it.
    parts = []
    for part in cmd:
        if len(part) > len(BASE_DIR) and part.startswith(BASE_DIR + os.sep):
            part = os.path.relpath(part, BASE_DIR)
            if os.sep != "/":
                part = part.replace(os.sep, "/")
        if part != os.path.basename(part) and (" " in part or "\\" in part):
            quoted = f'"{part}"' if "\\" in part else f"'{part}'"
            parts.append(quoted)
        else:
            parts.append(part)
    return " ".join(parts)


def _require_mkcert() -> str:
    mkcert = shutil.which("mkcert")
    if mkcert:
        return mkcert
    _print("mkcert was not found on this machine.")
    _print()
    _print("Install it once (pick your OS):")
    _print("  * Windows:  winget install FiloSottile.mkcert   (or: choco install mkcert)")
    _print("  * macOS:    brew install mkcert")
    _print("  * Linux:    see https://github.com/FiloSottile/mkcert#installation")
    _print()
    _print("Then install the local mkcert CA once:")
    _print("    mkcert -install")
    _print()
    _print("Re-run this script afterwards, or run the exact command it prints.")
    raise SystemExit(1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate certs/gestureforge-lan-cert.pem + -key.pem with "
                    "mkcert for GestureForge LAN camera testing."
    )
    parser.add_argument(
        "--ip", action="append", default=[],
        help="LAN IPv4 address to include (repeatable). By default the current "
             "LAN IP is detected automatically.",
    )
    parser.add_argument(
        "--add", action="append", default=[], metavar="SAN",
        help="Extra SAN to include (repeatable), e.g. a second LAN IP or a "
             "hostname. 127.0.0.1 and localhost are always included.",
    )
    parser.add_argument(
        "--cert-file", default=DEFAULT_CERT_PATH,
        help=f"Certificate output path (default: {os.path.relpath(DEFAULT_CERT_PATH, BASE_DIR)})",
    )
    parser.add_argument(
        "--key-file", default=DEFAULT_KEY_PATH,
        help=f"Private key output path (default: {os.path.relpath(DEFAULT_KEY_PATH, BASE_DIR)})",
    )
    parser.add_argument(
        "--print-command", action="store_true",
        help="Only print the exact mkcert command; do not run it.",
    )
    args = parser.parse_args(argv)

    detected = detect_lan_ip()
    if detected:
        _print(f"Detected LAN IP: {detected}")
    else:
        _print("Could not auto-detect a LAN IP — find it with `ipconfig` "
               "(Windows) or `ip addr` (Linux/macOS) and pass it with --ip.")

    ips = list(args.ip) or ([detected] if detected else [])
    sans = certificate_sans(ips[0] if ips else None)
    for extra in args.add:
        extra = extra.strip()
        if extra and extra not in sans:
            sans.append(extra)
    if len(ips) > 1:
        for extra_ip in ips[1:]:
            if extra_ip and extra_ip not in sans:
                sans.append(extra_ip)

    _print()
    _print(f"Certificate will cover: {', '.join(sans)}")
    _print(f"  cert: {os.path.relpath(args.cert_file, BASE_DIR)}")
    _print(f"  key:  {os.path.relpath(args.key_file, BASE_DIR)}")

    mkcert = None if args.print_command else _require_mkcert()
    if not args.print_command and not os.path.isdir(CERT_DIR):
        os.makedirs(CERT_DIR, exist_ok=True)

    cmd = _mkcert_command(mkcert or "mkcert", args.cert_file, args.key_file, sans)
    _print()
    _print("Exact command:")
    _print(f"    {_format_command(cmd)}")

    if args.print_command:
        return 0

    result = subprocess.run(cmd)
    if result.returncode != 0:
        _print()
        _print("mkcert failed — check that `mkcert -install` has been run once.")
        return result.returncode

    if not (os.path.isfile(args.cert_file) and os.path.isfile(args.key_file)):
        _print()
        _print("mkcert finished but the expected files are missing in certs/.")
        return 1

    loadable, problem = _cert_pair_loadable(args.cert_file, args.key_file)
    _print()
    if not loadable:
        _print(f"The new certificate pair failed the local sanity check: {problem}")
        return 1

    lan_display = ips[0] if ips else "<LAN-IP>"
    _print("Certificate generated and verified successfully.")
    _print()
    _print("Next steps:")
    _print("  1. Restart the server:  python app.py   (it switches to HTTPS automatically)")
    _print(f"  2. PC opens:           https://{lan_display}:5000/connect")
    _print(f"  3. Phone (same Wi-Fi): https://{lan_display}:5000/connect —")
    _print("     the phone must trust the mkcert CA (see the README section")
    _print('     "Two-Device Camera Testing over LAN" for Android instructions).')
    _print("  4. Allow the camera permission prompt on both devices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
