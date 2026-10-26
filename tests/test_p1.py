"""P1 credential lifecycle, retention and provenance regressions."""

import json
import runpy
import time
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from test_gateway import gateway as gateway
from test_gateway import issue as gateway_issue
from test_gateway import post
from test_users import issue
from test_users import multiuser as multiuser

from llameros.api_keys import ApiKeys, KeyRequest
from llameros.auth import Sessions, account_available
from llameros.config import Settings
from llameros.storage import Store


def test_layered_throttling_isolates_sources_and_bounds_state():
    sessions = Sessions(60)
    assert all(sessions.allow_attempt("attacker", "admin") for _ in range(5))
    assert not sessions.allow_attempt("attacker", "admin")
    assert sessions.allow_attempt("other-source", "admin")
    for index in range(10000):
        sessions.allow_attempt(str(index), str(index))
    assert len(sessions.attempts) <= 4096


def test_missing_policy_and_host_disable_revoke_credentials(multiuser):
    app, admin, user = multiuser
    key = issue(user)
    app.state.store.account_check = lambda name: name != "alice"
    assert user.get("/api/session").status_code == 401
    with pytest.raises(HTTPException):
        app.state.api_keys.authenticate("Bearer " + key["key"])
    app.state.store.account_check = lambda name: True
    assert app.state.users.get("alice")["blocked"]
    with app.state.store.connect() as db:
        db.execute("DELETE FROM users WHERE username='alice'")
    with pytest.raises(HTTPException):
        app.state.api_keys.authenticate("Bearer " + key["key"])
    assert user.get("/api/security-events").status_code == 401


def test_user_grants_restrict_existing_and_new_keys(multiuser):
    app, admin, user = multiuser
    assert user.post("/api/keys", json={"name": "unrestricted"}).status_code == 403
    for fields in (
        {"models": ["*"]},
        {"expires_days": None},
        {"expires_days": 31},
    ):
        payload = {
            "name": "bad",
            "models": ["gemma3:270m"],
            "targets": ["cpu"],
            "expires_days": 7,
            **fields,
        }
        assert user.post("/api/keys", json=payload).status_code == 403
    key = issue(user)
    assert app.state.api_keys.authenticate("Bearer " + key["key"])
    assert (
        admin.post(
            "/api/users/alice", json={"role": "user", "blocked": False, "models": []}
        ).status_code
        == 200
    )
    with pytest.raises(HTTPException):
        app.state.api_keys.authenticate("Bearer " + key["key"])
    events = admin.get("/api/security-events").json()["events"]
    assert any(e["action"] == "key.create" and e["actor"] == "alice" for e in events)
    assert key["key"] not in json.dumps(events)


def test_keys_ignore_legacy_hardware_scopes_but_keep_model_grants(multiuser):
    app, admin, user = multiuser
    response = user.post(
        "/api/keys", json={"name": "all hardware", "models": ["gemma3:270m"], "expires_days": 7}
    )
    assert response.status_code == 201
    created = response.json()
    # Simulate a pre-upgrade CPU-only key and an empty account target grant.
    with app.state.store.connect() as db:
        db.execute("UPDATE api_keys SET targets=? WHERE id=?", ('["cpu"]', created["id"]))
        db.execute("UPDATE users SET targets='[]' WHERE username='alice'")
    keys = app.state.api_keys
    key = keys.authenticate("Bearer " + created["key"])
    assert key["targets"] == ["*"]
    for target in ("cpu", "gpu-0123456789abcdef", "gpu-fedcba9876543210"):
        assert keys.allowed(key, "gemma3:270m", target)
        assert not keys.allowed(key, "ungranted:latest", target)
    assert next(row for row in keys.list() if row["id"] == created["id"])["targets"] == ["*"]
    assert user.get("/api/security-events").status_code == 403


def test_retention_without_sampler_and_login_write_budget(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    store.history_rows = 3
    keys = ApiKeys(store)
    key = keys.create(KeyRequest(name="test"))
    for _ in range(8):
        keys.record(key["id"], "model", "cpu", "completed", {}, 1)
        store.event("test")
        store.generation({"model": "model"})
    for _ in range(100):
        store.security_event("unknown", "login", "source", "denied")
    assert store.security_window[1] == 30
    with store.connect() as db:
        for table in ("api_usage", "events", "generations", "security_events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 3
        db.execute("UPDATE api_usage SET timestamp=0")
    assert keys.list()[0]["requests"] == 0


def test_runtime_pins_and_osv_pagination(tmp_path):
    for name in ("ollama_image", "ollama_rocm_image"):
        with pytest.raises(ValueError, match="digest"):
            Settings(**{name: "ollama/ollama:latest"})
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_security.py"))
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname="example"\nversion="1"\nsource={registry="https://pypi.org/simple"}\n'
    )
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"results": [{"next_page_token": "next"}]}
            if len(calls) == 1
            else {"vulns": [{"id": "TEST-1"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert script["scan_lock"](client, lock)[0]["advisories"] == ["TEST-1"]
    assert calls[1]["page_token"] == "next"


def test_account_checker_rejects_missing_and_nologin(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "llameros.auth.pwd.getpwnam", lambda name: SimpleNamespace(pw_shell="/usr/sbin/nologin")
    )
    assert not account_available("alice")

    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr("llameros.auth.pwd.getpwnam", missing)
    assert not account_available("alice")


def test_expired_grant_denied(multiuser):
    app, _, user = multiuser
    key = issue(user)
    with app.state.store.connect() as db:
        db.execute("UPDATE users SET grant_expires=? WHERE username='alice'", (time.time() - 1,))
    with pytest.raises(HTTPException):
        app.state.api_keys.authenticate("Bearer " + key["key"])


def test_offboarding_after_queue_admission_prevents_upstream(gateway, monkeypatch):
    app, client, _, calls, _ = gateway
    key, _ = gateway_issue(client)
    scheduler = app.state.jobs.scheduler
    original = scheduler.acquire

    async def acquire(*args, **kwargs):
        ticket = await original(*args, **kwargs)
        app.state.api_keys.users.offboard(app.state.api_keys.users.owner)
        return ticket

    monkeypatch.setattr(scheduler, "acquire", acquire)
    assert post(client, key["key"]).status_code == 401
    assert not calls
    assert not scheduler.tickets


def test_offboarding_does_not_abort_already_executing_request(gateway, monkeypatch):
    app, client, _, _, _ = gateway
    key, _ = gateway_issue(client)
    original = httpx.MockTransport.handle_async_request

    async def handle(self, request):
        app.state.api_keys.users.offboard(app.state.api_keys.users.owner)
        return await original(self, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", handle)
    assert post(client, key["key"]).status_code == 200
    assert post(client, key["key"]).status_code == 401


def test_failed_account_check_fails_closed_without_recreating_owner(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    keys = ApiKeys(store)
    key = keys.create(KeyRequest(name="test"))
    with store.connect() as db:
        db.execute("DELETE FROM users")
    keys = ApiKeys(store)
    with pytest.raises(HTTPException):
        keys.authenticate("Bearer " + key["key"])
    assert keys.users.get(keys.users.owner)["blocked"]
