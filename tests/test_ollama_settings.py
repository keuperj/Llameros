"""Ollama policy authorization, validation, persistence and container environment."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_core import HARDWARE, FakeDocker, sign_in
from test_users import multiuser as multiuser

from llameros.app import create_app
from llameros.config import Settings
from llameros.docker import Docker, DockerError
from llameros.ollama_settings import OllamaSettings, keep_alive_value


@pytest.mark.parametrize("value", ["-1", "0", "120", "5m", "1h30m", "-10m", "0.5s", "250ms"])
def test_keep_alive_allowed(value):
    policy = OllamaSettings(ollama_keep_alive=value)
    assert keep_alive_value(policy) == (int(value) if value.lstrip("-").isdigit() else value)


@pytest.mark.parametrize(
    "value", ["", "forever", "1d", "NaN", "1;rm", "1.5", "999999999999999999999h"]
)
def test_keep_alive_invalid(value):
    with pytest.raises(ValidationError):
        OllamaSettings(ollama_keep_alive=value)


def test_ollama_policy_permissions_validation_and_recreation(multiuser, monkeypatch):
    app, admin, user = multiuser
    defaults = {
        "ollama_keep_alive": "-1",
        "ollama_flash_attention": True,
        "ollama_kv_cache_type": "q4_0",
    }
    assert admin.get("/api/ollama").json() == defaults
    assert user.get("/api/ollama").status_code == 403
    assert user.post("/api/ollama", json=defaults).status_code == 403
    csrf = admin.headers.pop("X-CSRF-Token")
    assert admin.post("/api/ollama", json=defaults).status_code == 403
    admin.headers["X-CSRF-Token"] = csrf
    for invalid in (
        {"ollama_kv_cache_type": "q5_0"},
        {"ollama_flash_attention": "yes"},
        {"ollama_keep_alive": "forever"},
    ):
        assert admin.post("/api/ollama", json={**defaults, **invalid}).status_code == 422
    policy = {**defaults, "ollama_keep_alive": "10m", "confirmed": True}
    assert admin.post("/api/ollama", json=defaults).status_code == 400
    assert admin.post("/api/ollama", json=policy).status_code == 409
    monkeypatch.setattr(FakeDocker, "status", lambda self: {"running": False})
    app.state.jobs.job = {"state": "running", "action": "benchmark"}
    assert admin.post("/api/ollama", json=policy).status_code == 409
    app.state.jobs.job = {"state": "idle"}
    recreated = []
    monkeypatch.setattr(
        FakeDocker,
        "configure_storage",
        lambda self, directory, confirmed: recreated.append((directory, confirmed)),
    )
    result = admin.post("/api/ollama", json=policy)
    assert result.status_code == 200, result.text
    assert recreated == [("", True)]
    assert "confirmed" not in result.json()
    restored = create_app(
        Settings(state_dir=app.state.store.path.parent),
        docker=FakeDocker(),
        hardware_provider=lambda: HARDWARE,
        authenticator=lambda *args: True,
        sampling=False,
    )
    assert (
        json.loads((app.state.store.path.parent / "ollama.json").read_text())["ollama_keep_alive"]
        == "10m"
    )
    # The restored API exposes saved policy rather than TOML/default values.
    with TestClient(
        restored, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        sign_in(client)
        assert client.get("/api/ollama").json()["ollama_keep_alive"] == "10m"
    assert (app.state.store.path.parent / "ollama.json").exists()
    restored.state.jobs.pool.shutdown()


def test_cpu_gpu_environment_and_stale_container_refusal(tmp_path, monkeypatch):
    cfg = Settings(state_dir=tmp_path)
    for vendor in ("cpu", "nvidia"):
        client = Docker(replace(cfg, gpu=vendor), target={"id": vendor, "device": "GPU-test"})
        args = client.run_args()
        assert "OLLAMA_KEEP_ALIVE=-1" in args
        assert "OLLAMA_FLASH_ATTENTION=1" in args
        assert "OLLAMA_KV_CACHE_TYPE=q4_0" in args
    client = Docker(cfg)
    monkeypatch.setattr(client, "check_daemon", lambda: None)
    monkeypatch.setattr(
        client,
        "inspect",
        lambda: {"State": {"Running": False}, "Config": {"Image": client.image, "Env": []}},
    )
    monkeypatch.setattr(
        client, "command", lambda *args, **kwargs: pytest.fail("Must not mutate stale containers")
    )
    with pytest.raises(DockerError, match="environment differs"):
        client.start()
