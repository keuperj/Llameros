"""Approval-driven averages retain original evidence and exclude unreviewed reports."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_benchmark_exchange import local_result

from llameros.benchmark_exchange import rank_matches, shareable
from statistics_server.app import create_app
from statistics_server.database import initialize, moderate, review_entries


def measurement(rate, **changes):
    entry = shareable(local_result()).model_dump()
    for case in entry["cases"]:
        case["seconds"] = case["tokens"] / rate
        case["wall_seconds"] = max(case["wall_seconds"], case["seconds"])
    entry.update(tokens_per_second=float(rate), **changes)
    return entry


def test_confirmation_averages_originals_and_rejection_recomputes(tmp_path):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:

        def submit(rate, **changes):
            entry = measurement(rate, **changes)
            assert client.post("/api/v1/benchmarks", json={"entries": [entry]}).status_code == 200
            return next(
                row["digest"]
                for row in review_entries(tmp_path)
                if row["entry"]["tokens_per_second"] == rate
            )

        first = submit(40)
        second = submit(80)
        third = submit(120)
        assert client.get("/api/v1/averages").json()["entries"] == []
        moderate(tmp_path, first, "approved")
        assert "disagrees-with-approved-results" in next(
            row["flags"] for row in review_entries(tmp_path) if row["digest"] == second
        )  # Also refresh warnings for reports uploaded before a baseline existed.
        moderate(tmp_path, second, "approved")
        assert client.get("/api/v1/averages").json()["entries"][0]["tokens_per_second"] == 60
        moderate(tmp_path, third, "approved")
        average = client.get("/api/v1/averages").json()["entries"][0]
        assert (
            average["tokens_per_second"],
            average["samples"],
            average["minimum"],
            average["maximum"],
        ) == (80, 3, 40, 120)
        moderate(tmp_path, third, "approved")
        assert client.get("/api/v1/averages").json()["entries"][0] == average
        assert client.get("/api/v1/benchmarks").json()["total"] == 3
        moderate(tmp_path, second, "rejected")
        assert client.get("/api/v1/averages").json()["entries"][0]["samples"] == 2
        moderate(tmp_path, third, "rejected")
        assert client.get("/api/v1/averages").json()["entries"][0]["tokens_per_second"] == 40
        assert client.get("/api/v1/averages?limit=0").status_code == 422
        assert client.get("/api/v1/averages?offset=1").json()["entries"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "gemma3:1b"},
        {"model_digest": "sha256:" + "a" * 64},
        {"context_length": 8192},
        {"parallel": 1},
        {"flash_attention": False},
        {"kv_cache_type": "f16"},
        {"hardware": {**shareable(local_result()).hardware.model_dump(), "logical_cores": 32}},
    ],
)
def test_incompatible_measurements_are_not_averaged_or_flagged(tmp_path, changes):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        for rate, patch in [(40, {}), (120, changes)]:
            assert (
                client.post(
                    "/api/v1/benchmarks", json={"entries": [measurement(rate, **patch)]}
                ).status_code
                == 200
            )
            row = review_entries(tmp_path)[0]
            assert "disagrees-with-approved-results" not in row["flags"]
            moderate(tmp_path, row["digest"], "approved")
        assert client.get("/api/v1/averages").json()["total"] == 2


def test_retired_voucher_table_is_removed_without_losing_reports(tmp_path):
    path = initialize(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE vouchers (digest TEXT)")
        db.execute("INSERT INTO vouchers VALUES ('obsolete-secret-hash')")
    initialize(tmp_path)
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='vouchers'").fetchall()


def test_recommendations_use_arithmetic_mean():
    from llameros.benchmark_exchange import SharedBenchmark

    entries = [SharedBenchmark.model_validate(measurement(rate)) for rate in (40, 60, 140)]
    result = rank_matches(entries + [entries[0]], [entries[0].hardware], 4096, 2, True, "q4_0")[0]
    assert result["tokens_per_second"] == 80
    assert result["samples"] == 3


@pytest.mark.parametrize("rate, flagged", [(20, False), (60, False), (19, True), (61, True)])
def test_difference_threshold_both_directions(tmp_path, rate, flagged):
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        assert (
            client.post("/api/v1/benchmarks", json={"entries": [measurement(40)]}).status_code
            == 200
        )
        moderate(tmp_path, review_entries(tmp_path)[0]["digest"], "approved")
        assert (
            client.post("/api/v1/benchmarks", json={"entries": [measurement(rate)]}).status_code
            == 200
        )
        assert (
            "disagrees-with-approved-results" in review_entries(tmp_path)[0]["flags"]
        ) == flagged
