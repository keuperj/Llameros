"""Admin request audit privacy, durable acknowledgement and admission failures."""

import json
import time

from test_gateway import gateway as gateway
from test_gateway import issue as gateway_issue
from test_gateway import post
from test_users import issue
from test_users import multiuser as multiuser

from llameros.api_keys import ApiKeys
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


def test_timeframes_and_scheduled_retention(multiuser):
    app, admin, user = multiuser
    key = issue(user)
    store = app.state.store
    for age in (0, 2, 15, 45, 91):
        app.state.api_keys.record(key["id"], "model", "cpu", "failed", {}, 1)
        store.event("load:failed")
        with store.connect() as db:
            for table in ("events", "api_usage"):
                db.execute(
                    f"UPDATE {table} SET timestamp=? WHERE id=(SELECT MAX(id) FROM {table})",
                    (time.time() - age * 86400,),
                )
    store.sample({}, 30)
    for frame, count in (("24h", 1), ("week", 2), ("month", 3), ("all", 4)):
        data = admin.get(f"/api/activity?timeframe={frame}").json()
        assert data["request_counts"]["total"] == count
        assert data["admin_counts"]["total"] == count
    assert admin.get("/api/activity?timeframe=invalid").status_code == 422
    assert store.activity()["alert"]["failures"] == 5


def test_hourly_totals_survive_row_cleanup_and_are_owner_scoped(multiuser):
    app, admin, user = multiuser
    key = issue(user)
    keys, store = app.state.api_keys, app.state.store
    for _ in range(2):
        keys.record(key["id"], "model", "cpu", "completed", {"prompt": 4, "output": 7}, 1)
    other = issue(admin)
    keys.record(other["id"], "model", "cpu", "completed", {"prompt": 10, "output": 20}, 1)
    data = user.get("/api/token-history").json()
    assert data["users"] == ["alice"]
    assert data["rows"][0]["input_tokens"] == 8
    assert data["rows"][0]["hour"] % 3600 == 0
    assert len(admin.get("/api/token-history").json()["users"]) == 2
    with store.connect() as db:
        db.execute("DELETE FROM api_usage")
    ApiKeys(store)
    assert store.token_history("alice")["rows"][0]["output_tokens"] == 14
