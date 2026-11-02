"""Offline privacy, protocol, storage, consent and recommendation regressions."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_core import HARDWARE
from test_users import multiuser as multiuser

from llameros.benchmark_exchange import (
    SharedBenchmark,
    SharingSettings,
    hardware_profile,
    rank_matches,
    shareable,
)
from statistics_server.app import create_app
from statistics_server.database import moderate, review_entries


def local_result():
    return {
        "model": "gemma3:270m",
        "target": "gpu-secret-uuid",
        "timestamp": 123456789,
        "kind": "chat",
        "status": "completed",
        "context_length": 4096,
        "parallel": 2,
        "tokens_per_second": 42.125,
        "cases": [
            {
                "name": name,
                "timing": "generation",
                "tokens": 42,
                "seconds": 1.0,
                "wall_seconds": 1.1,
            }
            for name in [
                "short-short",
                "short-long",
                "medium-short",
                "medium-medium",
                "long-short",
                "long-long",
            ]
        ],
        "share_hardware": hardware_profile(HARDWARE).model_dump(),
        "share_environment": {"flash_attention": True, "kv_cache_type": "q4_0"},
        "username": "private-user",
        "prompt": "private-prompt",
        "error": "private-path",
    }


def test_allowlist_and_old_or_incomplete_results():
    result = local_result()
    entry = shareable(result).model_dump()
    encoded = json.dumps(entry)
    for value in ("private-user", "private-prompt", "private-path", "uuid", "timestamp", "target"):
        assert value not in encoded
    assert entry["tokens_per_second"] == 42.0
    for patch in ({"status": "failed"}, {"cases": []}, {"share_hardware": {}}):
        with pytest.raises((ValueError, KeyError)):
            shareable({**result, **patch})


def test_hardware_is_coarse_and_no_names_or_devices():
    hw = {
        **HARDWARE,
        "cpu": "private-host-cpu",
        "logical_cores": 13,
        "memory_total": 31.3 * 1024**3,
    }
    profile = hardware_profile(
        hw,
        {
            "vendor": "nvidia",
            "name": "NVIDIA GeForce RTX 4090",
            "id": "GPU-private",
            "total": 23.9 * 1024**3,
        },
    )
    assert profile.logical_cores == 16
    assert profile.memory_gib == 32
    assert profile.accelerator == "nvidia-RTX 4090"
    assert profile.accelerator_memory_gib == 24
    assert "private" not in profile.model_dump_json()
    assert (
        hardware_profile(hw, {"vendor": "amd", "name": "private custom machine"}).accelerator
        == "amd-other"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"host": "https://evil.test/path"},
        {"host": "169.254.169.254"},
        {"host": "0.0.0.0"},
        {"port": 0},
        {"enabled": "yes"},
        {"ca_certificate": "not a certificate"},
        {"ca_certificate": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_config_rejects_unsafe_values(patch):
    with pytest.raises(ValidationError):
        SharingSettings(**patch)


def test_server_validation_deduplication_pagination_capacity_and_privacy(tmp_path):
    app = create_app(tmp_path, max_entries=2, requests_per_minute=1000)
    entry = shareable(local_result()).model_dump()
    with TestClient(app, base_url="https://statistics.test") as client:

        def upload(entries):
            return client.post("/api/v1/benchmarks", json={"entries": entries})

        assert client.get("/").status_code == 200
        assert client.get("/table.js").status_code == 200
        assert upload([entry]).json() == {"accepted": 1, "duplicates": 0, "status": "pending"}
        assert client.get("/api/v1/benchmarks").json()["total"] == 0
        assert upload([entry]).json() == {"accepted": 0, "duplicates": 1, "status": "pending"}
        bad = upload([{**entry, "username": "private-name"}])
        assert bad.status_code == 422 and "private-name" not in bad.text
        assert upload([{**entry, "tokens_per_second": -1.0}]).status_code == 422
        assert upload([entry] * 11).status_code == 422
        assert client.get("/api/v1/benchmarks?limit=0").status_code == 422
        faster = shareable(local_result()).model_copy(deep=True)
        for case in faster.cases:
            case.seconds = 0.6
        faster.tokens_per_second = 70.0
        assert upload([faster.model_dump()]).status_code == 200
        assert upload([{**entry, "model": "gemma3:1b"}]).status_code == 503
        for row in review_entries(tmp_path):
            moderate(tmp_path, row["digest"], "approved")
        data = client.get("/api/v1/benchmarks?limit=1&offset=1").json()
        assert data["total"] == 2 and len(data["entries"]) == 1
        assert client.get("/").headers["referrer-policy"] == "no-referrer"
    with sqlite3.connect(tmp_path / "benchmarks.sqlite3") as db:
        assert [r[1] for r in db.execute("PRAGMA table_info(entries)")] == [
            "digest",
            "data",
            "status",
            "flags",
        ]
        stored = json.dumps(db.execute("SELECT * FROM entries").fetchall())
        assert "testclient" not in stored and "private" not in stored


def test_server_https_and_global_rate_limit(tmp_path):
    with TestClient(create_app(tmp_path, requests_per_minute=1), base_url="https://test") as client:
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 429
    with TestClient(create_app(tmp_path), base_url="http://test") as client:
        assert client.get("/").status_code == 400


def test_local_ranking_separates_workloads_settings_and_timing():
    entry = shareable(local_result())
    embedding = entry.model_copy(update={"kind": "embedding", "tokens_per_second": 1000.0})
    incompatible = entry.model_copy(update={"context_length": 8192, "tokens_per_second": 9999.0})
    ranked = rank_matches(
        [entry, entry, embedding, incompatible], [entry.hardware], 4096, 2, True, "q4_0"
    )
    assert len(ranked) == 2
    assert ranked[0]["kind"] == "chat" and ranked[0]["tokens_per_second"] == 42.0
    assert ranked[0]["samples"] == 1


def test_admin_consent_disabled_preview_and_exact_payload(multiuser, monkeypatch):
    app, admin, user = multiuser
    calls = []
    monkeypatch.setattr(
        "llameros.app.exchange",
        lambda *args: calls.append(args) or {"accepted": 1, "duplicates": 0},
    )
    app.state.store.save_benchmark(local_result())
    assert admin.get("/api/benchmark-sharing").json()["enabled"] is False
    assert admin.get("/api/benchmark-sharing/recommendations").json()["models"] == []
    assert admin.get("/api/benchmark-sharing/preview").status_code == 409
    assert not calls
    for route in (
        "/api/benchmark-sharing",
        "/api/benchmark-sharing/preview",
        "/api/benchmark-sharing/recommendations",
    ):
        assert user.get(route).status_code == 403
    policy = {"enabled": True, "host": "127.0.0.1", "port": 9443, "ca_certificate": ""}
    assert user.post("/api/benchmark-sharing", json=policy).status_code == 403
    assert admin.post("/api/benchmark-sharing", json=policy).status_code == 200
    assert (app.state.store.path.parent / "benchmark-sharing.json").exists()
    preview = admin.get("/api/benchmark-sharing/preview").json()
    assert len(preview["entries"]) == 1
    payload = {
        "entries": preview["entries"],
        "destination": preview["destination"],
    }
    assert admin.post("/api/benchmark-sharing/share", json=payload).status_code == 400
    assert (
        user.post("/api/benchmark-sharing/share", json={**payload, "confirmed": True}).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/benchmark-sharing/share",
            json={**payload, "confirmed": True, "destination": "https://other"},
        ).status_code
        == 409
    )
    tampered = json.loads(json.dumps(payload))
    tampered["entries"][0]["tokens_per_second"] = 999.0
    invalid = admin.post("/api/benchmark-sharing/share", json={**tampered, "confirmed": True})
    assert "A" * 43 not in invalid.text
    assert (
        admin.post("/api/benchmark-sharing/share", json={**tampered, "confirmed": True}).status_code
        == 422
    )
    assert not calls
    assert (
        admin.post("/api/benchmark-sharing/share", json={**payload, "confirmed": True}).status_code
        == 200
    )
    assert calls[0][3] == {"entries": preview["entries"]}
    assert (
        admin.post("/api/benchmark-sharing", json={**policy, "enabled": False}).status_code == 200
    )
    assert (
        admin.post("/api/benchmark-sharing/share", json={**payload, "confirmed": True}).status_code
        == 409
    )


def test_recommendations_fetch_public_pages_without_hardware_queries(multiuser, monkeypatch):
    app, admin, user = multiuser
    calls = []
    entry = shareable(local_result()).model_dump()

    def fetch(*args):
        calls.append(args)
        return {"entries": [entry], "total": 1}

    monkeypatch.setattr("llameros.app.exchange", fetch)
    admin.post("/api/benchmark-sharing", json={"enabled": True})
    result = admin.get("/api/benchmark-sharing/recommendations").json()
    assert result["models"][0]["model"] == entry["model"]
    admin.get("/api/benchmark-sharing/recommendations")
    assert len(calls) == 1
    assert calls[0][1:] == ("GET", "/api/v1/benchmarks?limit=1000&offset=0")


def test_protocol_rejects_arbitrary_hardware_strings():
    entry = shareable(local_result()).model_dump()
    entry["hardware"]["accelerator"] = "alice workstation"
    with pytest.raises(ValidationError):
        SharedBenchmark.model_validate(entry)
