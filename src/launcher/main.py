"""Entry point: python src/launcher/main.py [--host H] [--port P] [--no-https]"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import ROOT, create_app  # noqa: E402

CERT_DIR = ROOT / ".local-certs"


def ensure_cert() -> tuple[str, str] | None:
    crt, key = CERT_DIR / "launcher.crt", CERT_DIR / "launcher.key"
    if crt.exists() and key.exists():
        return str(crt), str(key)
    CERT_DIR.mkdir(exist_ok=True)
    host = os.uname().nodename
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={host}",
            "-addext", f"subjectAltName=DNS:{host},DNS:localhost,IP:127.0.0.1",
        ], check=True, capture_output=True)
        os.chmod(key, 0o600)
        return str(crt), str(key)
    except Exception as e:                                       # noqa: BLE001
        print(f"warning: cannot create a TLS certificate ({e}); serving plain HTTP",
              file=sys.stderr)
        return None


def _lazy_handshake_context(crt, key):
    """Werkzeug wraps the listening socket, so each TLS handshake runs inside
    the single accept loop: one client that stalls mid-handshake (a phone going
    to sleep, a port scanner) freezes the whole launcher. Deferring the
    handshake moves it into that connection's own handler thread."""
    import ssl as _ssl

    class Ctx(_ssl.SSLContext):
        def wrap_socket(self, sock, *a, **k):
            k["do_handshake_on_connect"] = False
            return super().wrap_socket(sock, *a, **k)

    ctx = Ctx(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    return ctx


def main() -> None:
    ap = argparse.ArgumentParser(description="Neat Apps Launcher")
    ap.add_argument("--host", default=os.environ.get("LAUNCHER_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LAUNCHER_PORT", "8091")))
    ap.add_argument("--no-https", action="store_true",
                    default=os.environ.get("LAUNCHER_HTTPS", "1") == "0")
    args = ap.parse_args()

    app, sio = create_app()
    ssl = None if args.no_https else ensure_cert()
    scheme = "https" if ssl else "http"
    print(f"Neat Apps Launcher on {scheme}://{args.host}:{args.port}/", flush=True)
    kw = {"ssl_context": _lazy_handshake_context(*ssl)} if ssl else {}
    sio.run(app, host=args.host, port=args.port, allow_unsafe_werkzeug=True,
            use_reloader=False, log_output=False, **kw)


if __name__ == "__main__":
    main()
