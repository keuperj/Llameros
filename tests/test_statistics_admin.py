"""Owner-only PAM UI, session security, open submissions and moderation isolation."""

import os
import pwd
import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_benchmark_exchange import local_result

from llameros.benchmark_exchange import shareable
from statistics_server.admin import COOKIE
from statistics_server.app import create_app
from statistics_server.database import initialize, review_entries


@pytest.fixture
def dashboard(tmp_path):
    calls = []
    available = [True]

    def authenticate(username, password, service):
        calls.append((username, password, service))
        return password == "private-password"

    app = create_app(
        tmp_path,
        authenticator=authenticate,
        account_checker=lambda username, service: available[0],
        pam_service="test-pam",
    )
    with TestClient(
        app, base_url="https://statistics", headers={"Origin": "https://statistics"}
    ) as client:
        yield app, client, calls, available


def login(client):
    result = client.post(
        "/admin/api/login",
        json={"username": pwd.getpwuid(os.geteuid()).pw_name, "password": "private-password"},
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    return result


def test_admin_requires_process_owner_not_environment_or_other_pam_users(dashboard, monkeypatch):
    app, client, calls, available = dashboard
    monkeypatch.setenv("USER", "other-account")
    monkeypatch.setenv("SUDO_USER", "other-account")
    assert client.get("/admin").status_code == 200
    assert client.get("/admin.js").status_code == 200
    assert client.get("/admin/api/overview").status_code == 401
    assert (
        client.post(
            "/admin/api/login", json={"username": "other-account", "password": "private-password"}
        ).status_code
        == 401
    )
    assert not calls  # Do not invoke PAM for any account except the captured owner.
    response = login(client)
    assert calls == [(pwd.getpwuid(os.geteuid()).pw_name, "private-password", "test-pam")]
    cookie = response.headers["set-cookie"].lower()
    for setting in ("secure", "httponly", "samesite=strict", "path=/"):
        assert setting in cookie
    assert "domain=" not in cookie
    assert COOKIE in client.cookies
    assert client.get("/admin/api/session").json()["csrf"] == response.json()["csrf"]
    assert client.get("/admin/api/overview").status_code == 200
    assert client.post("/admin/api/logout", json={}).status_code == 200
    assert client.get("/admin/api/overview").status_code == 401


def test_csrf_origin_https_and_validation_do_not_echo_secrets(dashboard):
    app, client, calls, available = dashboard
    for origin in ("https://evil.example", "http://statistics", "https://statistics/path"):
        assert (
            client.post(
                "/admin/api/login",
                headers={"Origin": origin},
                json={"username": "x", "password": "private-password"},
            ).status_code
            == 403
        )
    assert not calls
    invalid = client.post(
        "/admin/api/login",
        json={"username": "x", "password": "private-password", "extra": "private-value"},
    )
    assert invalid.status_code == 422
    assert "private" not in invalid.text
    login(client)
    csrf = client.headers.pop("X-CSRF-Token")
    decision = {"confirmed": True, "status": "approved", "expected_status": "pending"}
    assert client.post("/admin/api/benchmarks/missing", json=decision).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert (
        client.post(
            "/admin/api/benchmarks/missing",
            json=decision,
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/admin/api/benchmarks/missing", json={**decision, "confirmed": False}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/admin/api/benchmarks/missing", json={**decision, "status": "other"}
        ).status_code
        == 422
    )
    with TestClient(app, base_url="http://statistics") as insecure:
        assert insecure.get("/admin").status_code == 400


def test_login_throttle_is_global_without_ip_or_username_tracking(dashboard):
    app, client, calls, available = dashboard
    for index in range(5):
        assert (
            client.post(
                "/admin/api/login", json={"username": f"private-{index}", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/admin/api/login", json={"username": "another", "password": "wrong"}
        ).status_code
        == 429
    )
    assert not calls
    keys = str(app.state.admin_sessions.attempts)
    assert "private" not in keys and "testclient" not in keys


def test_session_expiry_account_disable_and_rotation(dashboard):
    app, client, calls, available = dashboard
    login(client)
    old = client.cookies.get(COOKIE)
    login(client)
    assert old != client.cookies.get(COOKIE)
    assert app.state.admin_sessions.get(old) is None
    available[0] = False
    assert client.get("/admin/api/overview").status_code == 401
    available[0] = True
    login(client)
    token = client.cookies.get(COOKIE)
    app.state.admin_sessions.sessions[token]["expires"] = 0
    assert client.get("/admin/api/overview").status_code == 401


def test_ui_open_submission_monitoring_pagination_and_moderation(dashboard, tmp_path):
    app, client, calls, available = dashboard
    login(client)
    entry = shareable(local_result()).model_dump()
    with TestClient(app, base_url="https://statistics") as contributor:
        assert contributor.post("/api/v1/benchmarks", json={"entries": [entry]}).status_code == 200
        for path in ("overview", "benchmarks", "session"):
            assert contributor.get("/admin/api/" + path).status_code == 401
        assert (
            contributor.post("/admin/api/benchmarks/missing", json={"confirmed": True}).status_code
            == 401
        )
    summary = client.get("/admin/api/overview").json()
    assert summary["entries"] == {"pending": 1, "approved": 0, "rejected": 0}
    queue = client.get("/admin/api/benchmarks?status=pending&limit=1&offset=0").json()
    assert queue["total"] == 1
    record = queue["entries"][0]
    assert "missing-model-digest" in record["flags"]
    digest = record["digest"]
    assert client.get("/admin/api/benchmarks?offset=1").json()["entries"] == []
    assert client.get("/admin/api/benchmarks?limit=101").status_code == 422
    assert client.get("/admin/api/benchmarks?status=other").status_code == 422
    body = {"status": "approved", "expected_status": "pending", "confirmed": True}
    assert (
        client.post(
            f"/admin/api/benchmarks/{digest}", json={**body, "confirmed": False}
        ).status_code
        == 400
    )
    assert client.post(f"/admin/api/benchmarks/{digest}", json=body).status_code == 200
    assert client.get("/api/v1/benchmarks").json()["total"] == 1
    assert (
        client.post(f"/admin/api/benchmarks/{digest}", json=body).status_code == 409
    )  # stale decision
    assert (
        client.post(
            f"/admin/api/benchmarks/{digest}",
            json={"status": "rejected", "expected_status": "approved", "confirmed": True},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/benchmarks").json()["total"] == 0
    assert client.get("/admin/api/benchmarks?status=rejected").json()["total"] == 1
    with sqlite3.connect(initialize(tmp_path)) as db:
        dump = "\n".join(db.iterdump())
        assert "private-password" not in dump
        assert client.headers["X-CSRF-Token"] not in dump


def test_admin_budgets_separate_from_public_traffic(tmp_path):
    app = create_app(
        tmp_path,
        uploads_per_minute=1,
        requests_per_minute=1,
        authenticator=lambda *args: True,
        account_checker=lambda *args: True,
    )
    with TestClient(
        app, base_url="https://statistics", headers={"Origin": "https://statistics"}
    ) as client:
        assert client.post("/api/v1/benchmarks", json={}).status_code == 422
        assert client.post("/api/v1/benchmarks", json={}).status_code == 429
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 429
        login(client)
        assert client.get("/admin/api/overview").status_code == 200


def test_legacy_cannot_be_approved_from_ui(dashboard, tmp_path):
    app, client, calls, available = dashboard
    with sqlite3.connect(initialize(tmp_path)) as db:
        db.execute("INSERT INTO entries VALUES ('legacy', '{}', 'pending', '[]')")
    login(client)
    assert (
        client.post(
            "/admin/api/benchmarks/legacy",
            json={"status": "approved", "expected_status": "pending", "confirmed": True},
        ).status_code
        == 409
    )
    assert review_entries(tmp_path)[0]["status"] == "pending"
