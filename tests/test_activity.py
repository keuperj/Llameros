"""Admin request audit privacy, durable acknowledgement and admission failures."""

import json

from test_gateway import gateway as gateway
from test_gateway import issue as gateway_issue
from test_gateway import post
from test_users import issue
from test_users import multiuser as multiuser

from llameros.storage import Store


def test_activity_access_counts_and_acknowledgement(multiuser):
    app, admin, user = multiuser
    key = issue(user)
    keys = app.state.api_keys
    keys.record(key["id"], "model", "cpu", "completed", {"prompt": 4, "output": 20}, 2)
    keys.record(key["id"], "model", "cpu", "failed", {}, 1)
    app.state.store.event("load:completed", "model")
    app.state.store.event("load:failed", "model", "Unavailable")
    assert user.get("/api/activity").status_code == 403
    assert user.post("/api/activity/acknowledge", json={"failures": 1}).status_code == 403
    data = admin.get("/api/activity").json()
    assert data["request_counts"] == {"total": 2, "successful": 1, "failed": 1}
    assert data["requests"][1]["tokens_per_second"] == 10
    assert data["requests"][0]["username"] == "alice"
    assert data["requests"][0]["prompt_tokens"] is None
    assert key["key"] not in json.dumps(data)
    assert data["admin_counts"]["failed"] == 1
    keys.record(key["id"], "model", "cpu", "failed", {}, 1)
    assert admin.post("/api/activity/acknowledge", json={"failures": 1}).status_code == 200
    assert Store(app.state.store.path).activity()["alert"] == {"failures": 2, "acknowledged": 1}
    admin.post("/api/activity/acknowledge", json={"failures": 2})
    assert app.state.store.activity()["alert"]["acknowledged"] == 2
    keys.delete(key["id"])
    assert app.state.store.activity()["requests"][0]["key_name"] == "Personal key"


def test_early_gateway_errors_are_logged_without_secrets(gateway):
    app, client, _, _, _ = gateway
    key, _ = gateway_issue(client)
    assert post(client, key["key"], prompt="").status_code == 400
    assert post(client, "invalid-private-secret").status_code == 401
    data = app.state.store.activity()
    assert data["request_counts"]["failed"] == 2
    assert len(data["requests"]) == 2
    assert data["requests"][1]["key_id"] == key["id"]
    assert "invalid-private-secret" not in json.dumps(data)
