"""Quota accounting, ownership, reset and gateway regression tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from test_gateway import gateway as gateway_fixture
from test_gateway import issue, post
from test_users import issue as user_issue
from test_users import multiuser as multiuser_fixture

from llameros.limits import LimitConfig, Limits, window
from llameros.storage import Store

gateway = gateway_fixture
multiuser = multiuser_fixture


def test_calendar_boundaries():
    now = datetime(2028, 2, 29, 12, tzinfo=UTC).timestamp()
    assert window("month", now)[1] == datetime(2028, 3, 1, tzinfo=UTC).timestamp()
    assert window("week", now)[0] == datetime(2028, 2, 28, tzinfo=UTC).timestamp()
    assert window("day", now)[1] == datetime(2028, 3, 1, tzinfo=UTC).timestamp()
    assert window("never", now) == (0, None)


def test_default_limits_for_unseen_users_and_overrides(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    limits = Limits(store)
    assert limits.snapshot("user", "unseen")["inherited"]
    assert limits.snapshot("user", "unseen")["input_k"] is None
    limits.set("user-default", "*", LimitConfig(input_k=1, period="day"), "admin")
    scopes = [("user", "unseen")]
    started = limits.check(scopes)
    limits.charge(scopes, {"prompt": 1000, "output": 10}, started)
    with pytest.raises(HTTPException):
        limits.check(scopes)
    limits.check([("user", "another-unseen")])  # Independent, not a shared pool.
    limits.set("user", "unseen", LimitConfig(), "admin")
    assert not limits.snapshot("user", "unseen")["inherited"]
    limits.check(scopes)  # Explicit unlimited overrides the default.
    limits.set("user-default", "*", LimitConfig(input_k=2, period="day"), "admin")
    assert limits.snapshot("user", "unseen")["input_k"] is None
    state = limits.inherit_user("unseen", "admin")
    assert state["inherited"] and state["input_remaining"] == 1000
    assert Limits(store).snapshot("user", "unseen")["input_used"] == 1000
    limits.set("user-default", "*", LimitConfig(input_k=1, period="day"), "admin")
    with pytest.raises(HTTPException):
        limits.check(scopes)


def test_default_limit_api_authorization_and_inheritance(multiuser):
    app, admin, user = multiuser
    url = "/api/limits/user-default?subject=*"
    assert user.get(url).status_code == 403
    assert user.post(url, json={"input_k": 1}).status_code == 403
    assert admin.post(url, json={}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert admin.post(url, json={"input_k": 1, "period": "month"}).status_code == 200
    assert admin.get(url).json()["input_k"] == 1
    assert admin.get("/api/limits/user-default?subject=alice").status_code == 400
    own = user_issue(user)
    key = next(key for key in user.get("/api/keys").json()["keys"] if key["id"] == own["id"])
    assert key["user_limits"]["inherited"] and key["user_limits"]["input_k"] == 1
    personal = "/api/limits/user?subject=alice"
    assert admin.post(personal, json={}).status_code == 200
    assert user.delete(personal).status_code == 403
    assert admin.delete(personal, headers={"X-CSRF-Token": "bad"}).status_code == 403
    result = admin.delete(personal)
    assert result.status_code == 200 and result.json()["inherited"]
    assert result.json()["input_k"] == 1
    assert admin.delete("/api/limits/user?subject=nonexistent").status_code == 404


def test_gateway_enforces_default_user_limits(gateway):
    app, client, docker, calls, response = gateway
    created, csrf = issue(client)
    client.headers.update({"Origin": "https://testserver", "X-CSRF-Token": csrf})
    assert (
        client.post("/api/limits/user-default?subject=*", json={"output_k": 1}).status_code == 200
    )
    response["eval_count"] = 1000
    assert post(client, created["key"]).status_code == 200
    assert post(client, created["key"]).status_code == 429
    assert len(calls) == 1


def test_persistent_atomic_accounting_and_policy_changes(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    limits = Limits(store)
    scopes = [("user", "alice"), ("model", "model:latest")]
    started = limits.check(scopes)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: limits.charge(scopes, {"prompt": 100, "output": 200}, started), range(20)
            )
        )
    limits.set("user", "alice", LimitConfig(input_k=1), "admin")
    assert limits.snapshot("user", "alice")["input_used"] == 2000
    with pytest.raises(HTTPException) as error:
        limits.check(scopes)
    assert error.value.status_code == 429
    assert Limits(store).snapshot("model", "model:latest")["output_used"] == 4000
    limits.set("user", "alice", LimitConfig(input_k=3, period="month"), "admin")
    limits.check(scopes)
    assert limits.snapshot("user", "alice")["input_used"] == 2000


def test_unknown_usage_blocks_finite_limits(tmp_path):
    limits = Limits(Store(tmp_path / "state.sqlite3"))
    scopes = [("key", "key")]
    started = limits.check(scopes)
    limits.charge(scopes, {}, started)
    limits.check(scopes)  # Unlimited remains unlimited.
    limits.set("key", "key", LimitConfig(output_k=1), "alice")
    with pytest.raises(HTTPException):
        limits.check(scopes)
    assert limits.snapshot("key", "key")["output_unknown"]


@pytest.mark.parametrize("scope", ["key", "user", "model"])
def test_gateway_enforces_all_scopes(gateway, scope):
    app, client, docker, calls, response = gateway
    created, csrf = issue(client)
    subject = {
        "key": created["id"],
        "user": app.state.api_keys.users.owner,
        "model": "gemma3:270m",
    }[scope]
    client.headers.update({"Origin": "https://testserver", "X-CSRF-Token": csrf})
    result = client.post(f"/api/limits/{scope}", params={"subject": subject}, json={"output_k": 1})
    assert result.status_code == 200, result.text
    response["eval_count"] = 1001
    assert post(client, created["key"]).status_code == 200  # Deliberate in-flight overshoot.
    assert post(client, created["key"]).status_code == 429
    assert len(calls) == 1
    state = client.get(f"/api/limits/{scope}", params={"subject": subject}).json()
    assert state["output_used"] == 1001
    assert state["output_remaining"] == 0


def test_key_creation_limits_and_validation(gateway):
    app, client, docker, calls, response = gateway
    created, _ = issue(client, limits={"input_k": 1, "period": "day"})
    response["prompt_eval_count"] = 1000
    assert post(client, created["key"]).status_code == 200
    denied = post(client, created["key"])
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) > 0
    for bad in (0, -1, True, 1.5, "1"):
        with pytest.raises(ValueError):
            LimitConfig(input_k=bad)


def test_owner_permissions_and_csrf(multiuser):
    app, admin, user = multiuser
    own = user_issue(user)
    other = user_issue(admin, user="bob")
    for method in (user.get, user.post):
        kwargs = {"json": {"input_k": 1}} if method == user.post else {}
        assert (
            method("/api/limits/key", params={"subject": other["id"]}, **kwargs).status_code == 404
        )
        for scope, subject in (("user", "alice"), ("model", "gemma3:270m")):
            assert (
                method(f"/api/limits/{scope}", params={"subject": subject}, **kwargs).status_code
                == 403
            )
    assert (
        user.post(
            "/api/limits/key", params={"subject": own["id"]}, json={"output_k": 1}
        ).status_code
        == 200
    )
    assert (
        user.post(
            "/api/limits/key",
            params={"subject": own["id"]},
            json={},
            headers={"X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )


def test_reset_late_completion_and_retention(tmp_path, monkeypatch):
    now = datetime(2028, 2, 29, 23, 59, tzinfo=UTC).timestamp()
    monkeypatch.setattr("llameros.limits.time.time", lambda: now)
    store = Store(tmp_path / "state.sqlite3")
    limits = Limits(store)
    scopes = [("user", "alice")]
    limits.set("user", "alice", LimitConfig(input_k=1, period="day"), "admin")
    started = limits.check(scopes)
    limits.charge(scopes, {"prompt": 1000, "output": 2}, started)
    with store.connect() as db:
        store.trim(db, "generations")
    with pytest.raises(HTTPException):
        limits.check(scopes)
    now += 120
    limits.check(scopes)
    limits.charge(scopes, {"prompt": 500, "output": 2}, started)
    assert limits.snapshot("user", "alice")["input_used"] == 0
    limits.set("user", "alice", LimitConfig(input_k=2), "admin")
    assert limits.snapshot("user", "alice")["input_used"] == 1500


def test_incomplete_stream_fails_closed(gateway):
    app, client, docker, calls, response = gateway
    created, _ = issue(client, limits={"input_k": 1})
    response.clear()
    response.update({"response": "partial"})
    result = post(client, created["key"], stream=True)
    assert "ended unexpectedly" in result.text
    assert post(client, created["key"]).status_code == 429
    assert app.state.api_keys.limits.snapshot("key", created["id"])["input_unknown"]
