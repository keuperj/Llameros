"""Offline action errors regression tests for Llameros behavior and integration boundaries."""

from test_core import client as client  # noqa: F401
from test_core import sign_in, wait_job


def test_action_errors_require_session_csrf_and_persist_bounded_text(client):
    """Verify action errors require session csrf and persist bounded text."""
    payload = {"action": "refresh", "message": "Runtime unavailable"}
    assert client.post("/api/action-errors", json=payload).status_code == 403
    sign_in(client)
    assert client.post("/api/action-errors", json=payload).status_code == 200
    event = client.get("/api/history").json()["events"][0]
    assert event["kind"] == "dashboard-action:failed"
    assert event["model"] == "refresh"
    assert event["detail"] == "Runtime unavailable"
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
    events = client.get("/api/history").json()["events"]
    failed = next(event for event in events if event["kind"] == "login:failed")
    assert failed["detail"] == "Linux authentication failed"
    assert "private-name" not in str(events) and "private-password" not in str(events)
