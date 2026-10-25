from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_core import HARDWARE, FakeDocker

from llameros.api_keys import ApiKeys, KeyRequest
from llameros.app import create_app
from llameros.auth import verify_password
from llameros.config import Settings, current_user
from llameros.storage import Store
from llameros.users import Users


@pytest.fixture
def multiuser(tmp_path, monkeypatch):
    monkeypatch.setattr("llameros.users.login_accounts", lambda: {current_user(), "alice", "bob"})
    docker = FakeDocker()
    docker.running = True
    docker.loaded = [{"name": "gemma3:270m", "target": "cpu", "base_url": "SECRET_RUNTIME"}]
    app = create_app(
        Settings(state_dir=tmp_path),
        docker=docker,
        hardware_provider=lambda: HARDWARE,
        authenticator=lambda user, password, service: password == "test-secret",
        sampling=False,
    )
    with (
        TestClient(
            app, base_url="https://testserver", headers={"Origin": "https://testserver"}
        ) as admin,
        TestClient(
            app, base_url="https://testserver", headers={"Origin": "https://testserver"}
        ) as user,
    ):
        login(admin, current_user())
        login(user, "alice")
        yield app, admin, user


def login(client, username):
    response = client.post("/api/login", json={"username": username, "password": "test-secret"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return response.json()


def issue(client, **kwargs):
    response = client.post("/api/keys", json={"name": "Personal key", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def test_default_roles_and_admin_endpoints_are_server_protected(multiuser):
    app, admin, user = multiuser
    assert admin.get("/api/session").json()["role"] == "admin"
    assert user.get("/api/session").json()["role"] == "user"
    for route in (
        "/api/status",
        "/api/history",
        "/api/models",
        "/api/storage",
        "/api/job",
        "/api/catalog",
        "/api/users",
    ):
        assert user.get(route).status_code == 403, route
    for route, payload in (
        ("/api/container/start", {}),
        ("/api/models/load", {"model": "gemma3:270m"}),
        ("/api/generate", {"model": "gemma3:270m", "prompt": "test"}),
        ("/api/storage", {"directory": "", "confirmed": True}),
        ("/api/users/bob", {"role": "admin", "blocked": False}),
        ("/api/action-errors", {"action": "test", "message": "test"}),
    ):
        assert user.post(route, json=payload).status_code == 403, route
    assert user.post("/api/logout", json={}).status_code == 200
    assert user.get("/api/user/dashboard").status_code == 401


def test_owner_isolation_and_admin_user_board_scope(multiuser):
    app, admin, user = multiuser
    admin_key = issue(admin)
    alice_key = issue(user)
    bob_key = issue(admin, user="bob")
    assert {key["user"] for key in admin.get("/api/keys").json()["keys"]} == {
        current_user(),
        "alice",
        "bob",
    }
    assert [key["id"] for key in user.get("/api/keys?board=admin").json()["keys"]] == [
        alice_key["id"]
    ]
    assert [key["id"] for key in admin.get("/api/keys?board=user").json()["keys"]] == [
        admin_key["id"]
    ]
    assert user.post("/api/keys", json={"name": "Forged", "user": "bob"}).status_code == 403
    assert (
        admin.post("/api/keys?board=user", json={"name": "Wrong board", "user": "bob"}).status_code
        == 403
    )
    assert (
        admin.post("/api/keys", json={"name": "Unknown", "user": "no-such-account"}).status_code
        == 404
    )
    for key in (admin_key, bob_key):
        assert user.request("DELETE", f"/api/keys/{key['id']}", json={}).status_code == 404
        assert user.post(f"/api/keys/{key['id']}/revoke", json={}).status_code == 404
    response = user.request("DELETE", f"/api/keys/{alice_key['id']}", json={})
    assert response.status_code == 200
    assert user.get("/api/keys").json()["keys"] == []
    assert user.get("/api/keys/usage").json()["keys"][0]["deleted"] == 1


def test_usage_and_running_model_redaction(multiuser):
    app, admin, user = multiuser
    own = issue(user)
    other = issue(admin, user="bob")
    app.state.api_keys.record(
        own["id"], "own-model", "cpu", "completed", {"prompt": 2, "output": 3}, 1
    )
    app.state.api_keys.record(
        other["id"], "private-model", "cpu", "completed", {"prompt": 200, "output": 300}, 10
    )
    for model, output in (("own-model", 3), ("private-model", 300), ("playground-model", 12)):
        app.state.store.generation({"model": model, "eval_count": output})
    app.state.store.event("PRIVATE_HOST_EVENT", detail="SECRET_HOST_PATH")
    app.state.store.sample({"hardware": HARDWARE, "ollama": {"secret": "SECRET_RUNTIME"}}, 30)
    result = user.get("/api/user/dashboard")
    assert result.status_code == 200
    assert result.json()["history"]["models"] == admin.get("/api/history").json()["models"]
    assert {row["model"]: row["output_tokens"] for row in result.json()["history"]["models"]} == {
        "own-model": 3,
        "private-model": 300,
        "playground-model": 12,
    }
    assert len(result.json()["keys"]) == 1
    assert result.json()["keys"][0]["output_tokens"] == 3
    assert result.json()["models"] == [{"name": "gemma3:270m", "target": "cpu"}]
    assert set(result.json()["history"]["samples"][0]["hardware"]) == {
        "cpu_percent",
        "memory_percent",
    }
    for secret in (
        "SECRET_RUNTIME",
        "SECRET_HOST_PATH",
        "PRIVATE_HOST_EVENT",
        "digest",
    ):
        assert secret not in result.text
    assert {key["user"] for key in user.get("/api/keys/usage").json()["keys"]} == {"alice"}


def test_block_revokes_all_keys_sessions_and_persists(multiuser):
    app, admin, user = multiuser
    first = issue(user)
    second = issue(admin, user="alice")
    response = admin.post("/api/users/alice", json={"role": "user", "blocked": True})
    assert response.status_code == 200
    assert user.get("/api/session").status_code == 401
    assert (
        user.post("/api/login", json={"username": "alice", "password": "test-secret"}).status_code
        == 401
    )
    for key in (first, second):
        with pytest.raises(HTTPException) as exc:
            app.state.api_keys.authenticate("Bearer " + key["key"])
        assert exc.value.status_code == 401
    assert Users(app.state.store).get("alice")["blocked"] == 1
    with pytest.raises(HTTPException):
        app.state.api_keys.create(KeyRequest(name="Blocked"), "alice")
    assert (
        admin.post("/api/users/alice", json={"role": "user", "blocked": False}).status_code == 200
    )
    # Unblocking never resurrects old keys or sessions.
    assert user.get("/api/session").status_code == 401
    login(user, "alice")
    with pytest.raises(HTTPException):
        app.state.api_keys.authenticate("Bearer " + first["key"])
    issue(user)


def test_promotion_demotion_and_self_protection(multiuser):
    app, admin, user = multiuser
    assert (
        admin.post("/api/users/alice", json={"role": "admin", "blocked": False}).status_code == 200
    )
    assert user.get("/api/users").status_code == 200
    assert user.get("/api/session").json()["role"] == "admin"
    assert Users(app.state.store).get("alice")["role"] == "admin"
    for body in ({"role": "user", "blocked": False}, {"role": "admin", "blocked": True}):
        assert user.post("/api/users/alice", json=body).status_code == 400
    assert (
        admin.post("/api/users/alice", json={"role": "user", "blocked": False}).status_code == 200
    )
    assert user.get("/api/users").status_code == 403
    assert user.get("/api/session").json()["role"] == "user"
    assert (
        admin.post(
            "/api/users/no-such-account", json={"role": "admin", "blocked": False}
        ).status_code
        == 404
    )
    assert (
        admin.post("/api/users/alice", json={"role": "root", "blocked": False}).status_code == 422
    )
    assert (
        admin.post("/api/users/alice", json={"role": "user", "blocked": "false"}).status_code == 422
    )


def test_legacy_keys_migrate_owner_without_changing_secret(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    import hashlib

    token = "llm_legacy-secret"
    with store.connect() as db:
        db.execute("""CREATE TABLE api_keys (
            id TEXT PRIMARY KEY,name TEXT,digest TEXT,models TEXT,targets TEXT,
            created REAL,expires REAL,revoked INTEGER DEFAULT 0)""")
        db.execute(
            "INSERT INTO api_keys VALUES(?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "Legacy",
                hashlib.sha256(token.encode()).hexdigest(),
                '["*"]',
                '["*"]',
                1,
                0,
                0,
            ),
        )
    keys = ApiKeys(store)
    assert keys.authenticate("Bearer " + token)["user"] == current_user()
    assert ApiKeys(store).list()[0]["user"] == current_user()


def test_pam_supports_other_login_accounts_and_rejects_daemons(monkeypatch):
    import pam

    calls = []
    monkeypatch.setattr(
        "llameros.auth.pwd.getpwnam", lambda username: SimpleNamespace(pw_shell="/bin/bash")
    )
    monkeypatch.setattr(
        pam,
        "pam",
        lambda: SimpleNamespace(
            authenticate=lambda *args, **kwargs: calls.append((args, kwargs)) or True
        ),
    )
    assert verify_password("alice", "password", "login")
    assert calls[0] == (("alice", "password"), {"service": "login", "resetcreds": False})
    monkeypatch.setattr(
        "llameros.auth.pwd.getpwnam", lambda username: SimpleNamespace(pw_shell="/usr/sbin/nologin")
    )
    assert not verify_password("daemon", "password", "login")
    assert not verify_password("alice", "bad\x00password", "login")
    assert len(calls) == 1
