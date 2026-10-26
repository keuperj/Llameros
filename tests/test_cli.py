"""Verify listener settings through the CLI without starting a service."""

import sys

import pytest

from llameros import app, cli
from llameros.config import Settings


@pytest.mark.parametrize(
    "flags,host,port",
    [
        ([], "127.0.0.1", 9443),
        (["--host", "192.0.2.10"], "192.0.2.10", 9443),
        (["--host", "0.0.0.0", "--port", "8444"], "0.0.0.0", 8444),
        (["--host", "::1"], "::1", 9443),
    ],
)
@pytest.mark.parametrize("config_first", [True, False])
def test_cli_listener_overrides_config(tmp_path, monkeypatch, flags, host, port, config_first):
    """CLI host/port win, independent of argument order, for app, TLS and listener."""
    config = tmp_path / "config.toml"
    config.write_text('host = "127.0.0.1"\nport = 9443\n')
    config_args = ["--config", str(config)]
    monkeypatch.setattr(
        sys, "argv", ["llameros", *(config_args + flags if config_first else flags + config_args)]
    )
    monkeypatch.setattr(cli.os, "getuid", lambda: 1000)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli.os, "umask", lambda value: None)
    monkeypatch.setattr(Settings, "prepare", lambda self: None)
    captured = {}

    def certificates(settings):
        captured["tls"] = settings
        return tmp_path / "cert.pem", tmp_path / "key.pem"

    def create_app(settings):
        captured["app"] = settings
        return "test-app"

    monkeypatch.setattr(cli, "certificates", certificates)
    monkeypatch.setattr(app, "create_app", create_app)
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda application, **kwargs: captured.update(listener=kwargs)
    )
    cli.main()
    assert captured["listener"]["host"] == host
    assert captured["listener"]["port"] == port
    assert captured["app"].host == captured["tls"].host == host
    assert captured["app"].port == captured["tls"].port == port
