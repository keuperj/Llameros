"""TOML defaults and durable dashboard override precedence."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker
from test_users import login

from llameros.app import create_app
from llameros.config import Settings, current_user, load_settings
from llameros.limits import LimitConfig, Limits
from llameros.model_storage import ModelStorage
from llameros.storage import Store
from llameros.users import Users, UserUpdate


def test_example_and_all_dashboard_settings_load(tmp_path):
    from pathlib import Path

    load_settings(Path(__file__).resolve().parents[1] / "config.example.toml")
    path = tmp_path / "config.toml"
    path.write_text("""
queue_size = 7
queue_timeout = 45
queue_users = 3
ollama_keep_alive = "5m"
ollama_flash_attention = false
ollama_kv_cache_type = "f16"
model_directory = ""
[benchmark_sharing]
enabled = true
host = "127.0.0.1"
port = 9444
[token_limits.user-default."*"]
input_k = 20
period = "day"
[user_policies.alice]
role = "user"
blocked = false
models = ["gemma3:270m"]
targets = ["cpu"]
grant_days = 7
""")
    cfg = load_settings(path)
    assert cfg.queue_size == 7
    assert cfg.benchmark_sharing["enabled"]
    assert cfg.user_policies["alice"]["grant_days"] == 7


@pytest.mark.parametrize(
    "kwargs",
    [
        {"queue_size": -1},
        {"queue_timeout": 0},
        {"queue_users": True},
        {"benchmark_sharing": {"enabled": "yes"}},
        {"benchmark_sharing": {"ca_certificate": "not a certificate"}},
        {"token_limits": {"unknown": {}}},
        {"token_limits": {"user-default": {"alice": {}}}},
        {"token_limits": {"user": {"alice": {"input_k": True}}}},
        {"user_policies": {"alice": {"role": "root", "blocked": False}}},
    ],
)
def test_invalid_dashboard_defaults_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_dashboard_overrides_file_defaults_live_and_after_restart(tmp_path):
    cfg = Settings(
        state_dir=tmp_path,
        queue_size=7,
        queue_timeout=45,
        queue_users=3,
        ollama_keep_alive="5m",
        benchmark_sharing={"port": 9444},
        token_limits={"user-default": {"*": {"input_k": 20, "period": "day"}}},
    )

    def make_app(settings):
        return create_app(
            settings,
            docker=FakeDocker(),
            hardware_provider=lambda: HARDWARE,
            authenticator=lambda *args: True,
            sampling=False,
        )

    app = make_app(cfg)
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        login(client, current_user())
        assert client.get("/api/scheduler").json()["queue_size"] == 7
        assert client.get("/api/ollama").json()["ollama_keep_alive"] == "5m"
        assert client.get("/api/benchmark-sharing").json()["port"] == 9444
        policy = client.get("/api/scheduler").json()
        policy["queue_size"] = 2
        assert client.post("/api/scheduler", json=policy).status_code == 200
        assert app.state.jobs.scheduler.capacity == 2
        assert client.post("/api/limits/user-default?subject=*", json={}).status_code == 200
        assert app.state.api_keys.limits.snapshot("user", "alice")["input_k"] is None
    # Partial legacy JSON retains file defaults for fields not saved in that file.
    (tmp_path / "benchmark-sharing.json").write_text(json.dumps({"enabled": False}))
    app = make_app(replace(cfg, queue_size=99))
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        login(client, current_user())
        assert client.get("/api/scheduler").json()["queue_size"] == 2
        sharing = client.get("/api/benchmark-sharing").json()
        assert sharing["port"] == 9444 and not sharing["enabled"]
        assert app.state.api_keys.limits.snapshot("user", "alice")["input_k"] is None


def test_token_defaults_all_scopes_and_persisted_unlimited(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    limits = Limits(store)
    limits.defaults = {
        "user-default": {"*": {"input_k": 10}},
        "user": {"alice": {"input_k": 3}},
        "model": {"gemma3:270m": {"output_k": 2}},
        "key": {"abc": {"output_k": 1}},
    }
    assert limits.snapshot("user", "bob")["input_k"] == 10
    assert limits.snapshot("user", "alice")["input_k"] == 3
    assert not limits.snapshot("user", "alice")["inherited"]
    assert limits.snapshot(*limits.model_scope("alice", "gemma3:270m"))["output_k"] == 2
    assert limits.snapshot("key", "abc")["output_k"] == 1
    limits.set("user", "alice", LimitConfig(), "admin")
    assert limits.snapshot("user", "alice")["input_k"] is None
    assert limits.inherit_user("alice", "admin")["input_k"] == 10
    restored = Limits(store)
    restored.defaults = limits.defaults
    assert restored.snapshot("user", "alice")["inherited"]
    assert restored.snapshot("user", "alice")["input_k"] == 10


def test_storage_file_default_and_saved_empty_override(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cfg = Settings(state_dir=state, model_directory=str(tmp_path / "models"))
    storage = ModelStorage(cfg)
    assert storage.directory == cfg.model_directory
    storage.save("")
    assert ModelStorage(cfg).directory == ""


def test_user_policy_seed_does_not_reset_grants_or_override_admin(tmp_path, monkeypatch):
    monkeypatch.setattr("llameros.users.login_accounts", lambda: {current_user(), "alice"})
    store = Store(tmp_path / "history.sqlite3")
    defaults = {
        "alice": {
            "role": "user",
            "blocked": False,
            "models": ["gemma3:270m"],
            "targets": ["cpu"],
            "grant_days": 7,
        }
    }
    users = Users(store, defaults)
    expires = users.get("alice")["grant_expires"]
    assert expires > 0
    assert Users(store, defaults).get("alice")["grant_expires"] == expires
    users.update("alice", UserUpdate(role="admin", blocked=False), current_user())
    assert Users(store, defaults).get("alice")["role"] == "admin"
