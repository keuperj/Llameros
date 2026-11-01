"""Admission control, atomic voucher redemption, moderation, migration and privacy."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_benchmark_exchange import local_result

from llameros.benchmark_exchange import shareable
from statistics_server.app import create_app
from statistics_server.database import initialize, issue_voucher, moderate, review_entries
from statistics_server.manage import main


def test_voucher_replay_expiry_limits_and_no_linkage(tmp_path):
    app = create_app(tmp_path)
    entry = shareable(local_result()).model_dump()
    token = issue_voucher(tmp_path, max_entries=1)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with TestClient(app, base_url="https://statistics") as client:
        assert client.post("/api/v1/benchmarks", json={"entries": [entry]}).status_code == 422
        assert (
            client.post(
                "/api/v1/benchmarks", json={"entries": [entry], "voucher": "A" * 43}
            ).status_code
            == 403
        )
        too_large = client.post(
            "/api/v1/benchmarks", json={"entries": [entry, entry], "voucher": token}
        )
        assert too_large.status_code == 400
        payload = {"entries": [entry], "voucher": token}
        assert client.post("/api/v1/benchmarks", json=payload).status_code == 200
        assert client.post("/api/v1/benchmarks", json=payload).status_code == 403
        expired = issue_voucher(tmp_path)
        with sqlite3.connect(initialize(tmp_path)) as db:
            db.execute(
                "UPDATE vouchers SET expires=0 WHERE digest=?",
                (hashlib.sha256(expired.encode()).hexdigest(),),
            )
        assert (
            client.post(
                "/api/v1/benchmarks", json={"entries": [entry], "voucher": expired}
            ).status_code
            == 403
        )
    with sqlite3.connect(initialize(tmp_path)) as db:
        stored_entries = json.dumps(db.execute("SELECT * FROM entries").fetchall())
        assert token not in stored_entries and digest not in stored_entries
        vouchers = db.execute("SELECT * FROM vouchers WHERE digest=?", (digest,)).fetchall()
        assert vouchers[0][3] == 1
        assert [row[1] for row in db.execute("PRAGMA table_info(vouchers)")] == [
            "digest",
            "expires",
            "max_entries",
            "redeemed",
        ]


def test_capacity_failure_and_bad_metrics_do_not_consume_voucher(tmp_path):
    token = issue_voucher(tmp_path)
    entry = shareable(local_result()).model_dump()
    payload = {"entries": [entry], "voucher": token}
    with TestClient(create_app(tmp_path, max_entries=0), base_url="https://statistics") as client:
        assert client.post("/api/v1/benchmarks", json=payload).status_code == 503
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        invalid = {**entry, "tokens_per_second": 99999.0}
        assert (
            client.post("/api/v1/benchmarks", json={**payload, "entries": [invalid]}).status_code
            == 422
        )
        assert client.post("/api/v1/benchmarks", json=payload).status_code == 200


def test_concurrent_redemption_across_app_instances(tmp_path):
    token = issue_voucher(tmp_path)
    payload = {"entries": [shareable(local_result()).model_dump()], "voucher": token}
    apps = [create_app(tmp_path), create_app(tmp_path)]

    def upload(app):
        with TestClient(app, base_url="https://statistics") as client:
            return client.post("/api/v1/benchmarks", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(upload, apps)) == [200, 403]


def test_approval_rejection_public_exclusion_and_disagreement_flags(tmp_path):
    app = create_app(tmp_path)
    entry = shareable(local_result()).model_dump()
    with TestClient(app, base_url="https://statistics") as client:

        def submit(row):
            return client.post(
                "/api/v1/benchmarks", json={"entries": [row], "voucher": issue_voucher(tmp_path)}
            )

        assert submit(entry).status_code == 200
        pending = review_entries(tmp_path)
        assert pending[0]["flags"] == ["missing-model-digest"]
        assert client.get("/api/v1/benchmarks").json() == {"entries": [], "total": 0}
        digest = pending[0]["digest"]
        moderate(tmp_path, digest, "approved")
        assert client.get("/api/v1/benchmarks").json()["total"] == 1
        fast = json.loads(json.dumps(entry))
        for case in fast["cases"]:
            case["seconds"] = 0.01
        fast["tokens_per_second"] = 4200.0
        assert submit(fast).status_code == 200
        flags = review_entries(tmp_path)[0]["flags"]
        assert "high-throughput-review" in flags and "disagrees-with-approved-results" in flags
        assert client.get("/api/v1/benchmarks").json()["total"] == 1
        moderate(tmp_path, digest, "rejected")
        assert client.get("/api/v1/benchmarks").json()["total"] == 0
        assert review_entries(tmp_path, "rejected")[0]["digest"] == digest
        assert client.get("/api/v1/benchmarks?status=pending").json()["total"] == 0


def test_legacy_migration_fails_closed_and_is_idempotent(tmp_path):
    path = tmp_path / "benchmarks.sqlite3"
    legacy = {"model": "gemma3:270m", "protocol": "llameros-six-cases-v1"}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE entries (digest TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO entries VALUES (?,?)", ("legacy", json.dumps(legacy)))
    initialize(tmp_path)
    initialize(tmp_path)
    assert review_entries(tmp_path)[0]["status"] == "pending"
    with pytest.raises(ValueError):
        moderate(tmp_path, "legacy", "approved")
    moderate(tmp_path, "legacy", "rejected")
    assert review_entries(tmp_path, "rejected")[0]["entry"] == legacy


def test_upload_limit_does_not_exhaust_read_budget(tmp_path):
    app = create_app(tmp_path, uploads_per_minute=1, requests_per_minute=4)
    with TestClient(app, base_url="https://statistics") as client:
        assert client.post("/api/v1/benchmarks", json={}).status_code == 422
        assert client.post("/api/v1/benchmarks", json={}).status_code == 429
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/benchmarks").status_code == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"tokens_per_second": 123.0},
        {"timing": "request"},
        {"protocol": "llameros-six-cases-v1"},
        {"model_digest": "private-name"},
    ],
)
def test_inconsistent_metrics_rejected(tmp_path, patch):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        entry = {**shareable(local_result()).model_dump(), **patch}
        response = client.post(
            "/api/v1/benchmarks", json={"entries": [entry], "voucher": issue_voucher(tmp_path)}
        )
        assert response.status_code == 422
        assert "private-name" not in response.text


def test_moderation_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["manage", "--state-dir", str(tmp_path), "issue-voucher", "--max-entries", "1"]
    )
    main()
    token = capsys.readouterr().out.strip()
    assert len(token) == 43
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        response = client.post(
            "/api/v1/benchmarks",
            json={"entries": [shareable(local_result()).model_dump()], "voucher": token},
        )
        assert response.status_code == 200
    monkeypatch.setattr("sys.argv", ["manage", "--state-dir", str(tmp_path), "review"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert token not in json.dumps(result)
    monkeypatch.setattr(
        "sys.argv", ["manage", "--state-dir", str(tmp_path), "approve", result[0]["digest"]]
    )
    main()
    assert "not proof" in capsys.readouterr().out
    assert len(review_entries(tmp_path, "approved")) == 1
