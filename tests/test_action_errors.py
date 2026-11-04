"""Offline action errors regression tests for Llameros behavior and integration boundaries."""

import threading

import pytest
from fastapi import HTTPException
from test_core import client as client  # noqa: F401
from test_core import sign_in, wait_job

from llameros.app import Jobs
from llameros.docker import DockerError
from llameros.storage import Store


def test_failed_job_waits_for_terminal_event_commit(tmp_path, monkeypatch):
    """Hold the failure log write to deterministically expose premature status publication."""
    store = Store(tmp_path / "history.sqlite3")
    jobs = Jobs(store)
    entered, release = threading.Event(), threading.Event()
    record = store.event

    def delayed_event(kind, model="", detail=""):
        if kind == "load:failed":
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release the failure event write")
        record(kind, model, detail)

    def fail(progress):
        raise DockerError("Runtime is stopped")

    monkeypatch.setattr(store, "event", delayed_event)
    try:
        accepted = jobs.submit("load", "gemma3:270m", fail)
        assert entered.wait(5)
        snapshot = jobs.status()
        assert snapshot["job_id"] == accepted["job_id"]
        assert snapshot["state"] == "running"
        with store.connect() as db:
            assert db.execute("SELECT 1 FROM events WHERE kind='load:failed'").fetchone() is None
        with pytest.raises(HTTPException) as conflict:
            jobs.submit("load", "other:latest", fail)
        assert conflict.value.status_code == 409
    finally:
        release.set()
        jobs.pool.shutdown(wait=True)
    snapshot = jobs.status()
    assert snapshot["state"] == "failed"
    assert snapshot["job_id"] == accepted["job_id"]
    with store.connect() as db:
        event = db.execute("SELECT * FROM events WHERE kind='load:failed'").fetchone()
    assert event["model"] == "gemma3:270m"
    assert event["detail"] == snapshot["error"]


def test_action_errors_require_session_csrf_and_persist_bounded_text(client):
    """Verify action errors require session csrf and persist bounded text."""
    payload = {"action": "refresh", "message": "Runtime unavailable"}
    assert client.post("/api/action-errors", json=payload).status_code == 403
    sign_in(client)
    assert client.post("/api/action-errors", json=payload).status_code == 200
    event = client.get("/api/history").json()["events"][0]
    assert event["kind"] == "dashboard-action:failed"
    assert event["model"] == "dashboard"
    assert event["detail"] == "Client action failed"
    assert (
        client.post("/api/action-errors", json={**payload, "message": "x" * 2001}).status_code
        == 422
    )
    client.headers["X-CSRF-Token"] = "incorrect"
    assert client.post("/api/action-errors", json=payload).status_code == 403


def test_failed_job_has_submission_identity_and_existing_log(client):
    """Verify failed job has submission identity and existing log."""
    sign_in(client)
    # Fake Docker is stopped: a load is accepted, then fails asynchronously.
    response = client.post("/api/models/load", json={"model": "gemma3:270m"})
    assert response.status_code == 202
    job = wait_job(client)
    assert job["job_id"] == response.json()["job_id"]
    assert job["state"] == "failed"
    events = client.get("/api/history").json()["events"]
    assert any(
        event["kind"] == "load:failed" and event["detail"] == job["error"] for event in events
    )


def test_login_failure_logged_without_submitted_credentials(client):
    """Verify login failure logged without submitted credentials."""
    result = client.post(
        "/api/login", json={"username": "private-name", "password": "private-password"}
    )
    assert result.status_code == 401
    sign_in(client)
    events = client.get("/api/security-events").json()["events"]
    failed = next(
        event for event in events if event["action"] == "login" and event["outcome"] == "denied"
    )
    assert failed["actor"] == "private-name"
    assert "private-password" not in str(events)
