"""Admin policy validation, live application, persistence and explicit runtime recreation."""

import json

import pytest
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker
from test_users import login
from test_users import multiuser as multiuser

from llameros.app import create_app
from llameros.config import Settings, current_user


def test_scheduler_authorization_live_settings_and_persistence(multiuser):
    app, admin, user = multiuser
    defaults = {
        "queue_size": 16,
        "queue_timeout": 120,
        "queue_users": 0,
        "ollama_num_parallel": 2,
        "max_loaded_models": 2,
    }
    assert admin.get("/api/scheduler").json() == defaults
    assert user.get("/api/scheduler").status_code == 403
    assert user.post("/api/scheduler", json=defaults).status_code == 403
    csrf = admin.headers.pop("X-CSRF-Token")
    assert admin.post("/api/scheduler", json=defaults).status_code == 403
    admin.headers["X-CSRF-Token"] = csrf
    policy = {**defaults, "queue_size": 3, "queue_timeout": 15, "queue_users": 2}
    assert admin.post("/api/scheduler", json=policy).json() == policy
    scheduler = app.state.jobs.scheduler
    assert (scheduler.capacity, scheduler.timeout, scheduler.queue_users) == (3, 15, 2)
    path = app.state.store.path.parent
    assert json.loads((path / "scheduler.json").read_text()) == policy
    restored = create_app(
        Settings(state_dir=path),
        docker=FakeDocker(),
        hardware_provider=lambda: HARDWARE,
        sampling=False,
    )
    assert restored.state.jobs.scheduler.capacity == 3
    assert restored.state.jobs.scheduler.queue_users == 2
    restored.state.jobs.pool.shutdown()


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue_size", -1),
        ("queue_timeout", 0),
        ("queue_users", -1),
        ("ollama_num_parallel", 17),
        ("max_loaded_models", 0),
        ("max_loaded_models", 17),
        ("max_loaded_models", True),
        ("max_loaded_models", 1.5),
        ("queue_size", True),
        ("queue_timeout", 1.5),
    ],
)
def test_scheduler_rejects_invalid_policy(multiuser, field, value):
    _, admin, _ = multiuser
    policy = admin.get("/api/scheduler").json()
    policy[field] = value
    assert admin.post("/api/scheduler", json=policy).status_code == 422


@pytest.mark.parametrize("field", ["ollama_num_parallel", "max_loaded_models"])
def test_parallelism_requires_idle_stopped_and_confirmed_recreation(multiuser, monkeypatch, field):
    app, admin, _ = multiuser
    runtime_settings = []
    monkeypatch.setattr(FakeDocker, "settings", Settings(), raising=False)
    policy = {**admin.get("/api/scheduler").json(), field: 4}
    assert admin.post("/api/scheduler", json=policy).status_code == 400
    policy["confirmed"] = True
    assert admin.post("/api/scheduler", json=policy).status_code == 409
    monkeypatch.setattr(FakeDocker, "status", lambda self: {"running": False})
    app.state.jobs.job = {"state": "running", "action": "benchmark"}
    assert admin.post("/api/scheduler", json=policy).status_code == 409
    app.state.jobs.job = {"state": "idle"}
    recreated = []
    monkeypatch.setattr(
        FakeDocker,
        "configure_storage",
        lambda self, directory, confirmed: (
            recreated.append((directory, confirmed)),
            runtime_settings.append(self),
        ),
    )
    response = admin.post("/api/scheduler", json=policy)
    assert response.status_code == 200, response.text
    assert recreated == [("", True)]
    assert getattr(runtime_settings[0].settings, field) == 4
    assert app.state.jobs.scheduler.parallel == policy["ollama_num_parallel"]
    assert "confirmed" not in response.json()
    restored = create_app(
        Settings(state_dir=app.state.store.path.parent, max_loaded_models=1),
        docker=FakeDocker(),
        hardware_provider=lambda: HARDWARE,
        sampling=False,
        authenticator=lambda *args: True,
    )
    assert restored.state.jobs.scheduler.parallel == policy["ollama_num_parallel"]
    with TestClient(
        restored, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        login(client, current_user())
        assert (
            client.get("/api/scheduler").json()["max_loaded_models"] == policy["max_loaded_models"]
        )
    assert json.loads((app.state.store.path.parent / "scheduler.json").read_text())[field] == 4
    restored.state.jobs.pool.shutdown()


def test_legacy_scheduler_file_uses_configured_model_limit(tmp_path):
    (tmp_path / "scheduler.json").write_text(json.dumps({"queue_size": 8}))
    app = create_app(
        Settings(state_dir=tmp_path, max_loaded_models=5),
        docker=FakeDocker(),
        hardware_provider=lambda: HARDWARE,
        sampling=False,
        authenticator=lambda *args: True,
    )
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        login(client, current_user())
        assert client.get("/api/scheduler").json()["max_loaded_models"] == 5
