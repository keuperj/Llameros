"""Run the separate HTTPS statistics service: python -m statistics_server --help."""

import argparse
from pathlib import Path

import uvicorn

from llameros.config import Settings
from llameros.tls import certificates

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind IP; also included in generated certificate SAN"
    )
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--state-dir", type=Path, default=Path("statistics-state"))
    parser.add_argument("--cert-file", default="")
    parser.add_argument("--key-file", default="")
    parser.add_argument("--max-entries", type=int, default=100000)
    parser.add_argument(
        "--pam-service",
        default="login",
        help="Host PAM service for the process owner's admin login",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.max_entries < 1:
        parser.error("Invalid port or database capacity")
    state = args.state_dir.expanduser().absolute()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert, key = certificates(
        Settings(host=args.host, state_dir=state, cert_file=args.cert_file, key_file=args.key_file)
    )
    uvicorn.run(
        create_app(state, max_entries=args.max_entries, pam_service=args.pam_service),
        host=args.host,
        port=args.port,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        access_log=False,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=32,
        # Error tracebacks may contain request context; keep network metadata out of logs.
        log_level="critical",
    )


if __name__ == "__main__":
    main()
