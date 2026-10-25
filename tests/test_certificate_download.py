"""Certificate export policy, public-only serialization, and dashboard session regressions."""

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker

from llameros.app import create_app
from llameros.config import Settings, current_user, load_settings
from llameros.tls import certificates


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("username", ["alice", current_user()])
def test_certificate_download_policy(tmp_path, monkeypatch, enabled, username):
    """Both roles require a session and obey the switch, including login/session metadata."""
    monkeypatch.setattr("llameros.users.login_accounts", lambda: {current_user(), "alice"})
    cfg = Settings(state_dir=tmp_path, certificate_download_enabled=enabled)
    cert, key = certificates(cfg)
    expected = cert.read_bytes()
    # Combined operator files must never disclose private PEM blocks or other text.
    cert.write_bytes(expected + key.read_bytes() + b"PRIVATE OPERATOR NOTES\n")
    app = create_app(
        cfg,
        docker=FakeDocker(),
        hardware_provider=lambda: HARDWARE,
        authenticator=lambda *args: True,
        account_checker=lambda name: True,
        sampling=False,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/certificate").status_code == 401
        login = client.post("/api/login", json={"username": username, "password": "fake"})
        assert login.status_code == 200
        assert login.json()["certificate_download_enabled"] is enabled
        assert client.get("/api/session").json()["certificate_download_enabled"] is enabled
        response = client.get("/api/certificate")
        assert response.status_code == (200 if enabled else 404)
        if enabled:
            assert response.content == expected
            assert "PRIVATE" not in response.text
            assert response.headers["cache-control"] == "no-store"
            assert (
                response.headers["content-disposition"]
                == 'attachment; filename="llameros-cert.pem"'
            )
            cert.unlink()
            unavailable = client.get("/api/certificate")
            assert unavailable.status_code == 503
            assert str(tmp_path) not in unavailable.text
            assert not cert.exists()


def test_custom_certificate_and_missing_file(tmp_path):
    """Export a configured public chain, fail safely if missing, and never create TLS files."""
    from llameros.tls import public_certificate

    cert, key = certificates(Settings(state_dir=tmp_path))
    custom = tmp_path / "custom.pem"
    custom.write_bytes(cert.read_bytes() * 2)
    cfg = Settings(state_dir=tmp_path, cert_file=str(custom), key_file=str(key))
    assert public_certificate(cfg) == custom.read_bytes()
    custom.unlink()
    with pytest.raises(OSError):
        public_certificate(cfg)
    custom.write_text("not a certificate")
    with pytest.raises(ValueError):
        public_certificate(cfg)


def test_certificate_setting_loading(tmp_path):
    """Default on; TOML must contain a real boolean."""
    assert Settings().certificate_download_enabled is True
    path = tmp_path / "config.toml"
    path.write_text("certificate_download_enabled = false\n")
    assert load_settings(path).certificate_download_enabled is False
    path.write_text('certificate_download_enabled = "false"\n')
    with pytest.raises(ValueError):
        load_settings(path)
