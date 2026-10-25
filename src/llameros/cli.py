"""Command-line entry point that starts the Llameros HTTPS service as an ordinary Linux user."""

import argparse
import os
from dataclasses import replace
from pathlib import Path

import uvicorn

from .config import load_settings
from .tls import certificates


def main():
    """Parse local settings, refuse root/setuid execution, and start Uvicorn with mandatory TLS."""
    parser = argparse.ArgumentParser(
        description="Llameros HTTPS dashboard (run as your Linux user, not root)"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        parser.error("Llameros must run as an ordinary user, without sudo/setuid")
    os.umask(0o077)
    try:
        settings = load_settings(args.config)
        overrides = {
            key: value
            for key, value in {"host": args.host, "port": args.port}.items()
            if value is not None
        }
        settings = replace(settings, **overrides)
        settings.prepare()
        cert, key = certificates(settings)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    from .app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        proxy_headers=False,
        server_header=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
