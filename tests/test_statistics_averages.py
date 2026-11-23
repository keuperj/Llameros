"""Approval-driven averages retain original evidence and exclude unreviewed reports."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_benchmark_exchange import local_result
from test_core import HARDWARE

from llameros.benchmark_exchange import SharedBenchmark, benchmark_profile, rank_matches, shareable
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
    profile = benchmark_profile(HARDWARE, "cpu")
    entries = [
        SharedBenchmark.model_validate(measurement(rate, placement=profile[1].model_dump()))
        for rate in (40, 60, 140)
    ]
    result = rank_matches(entries + [entries[0]], [profile], 4096, 2, True, "q4_0")[0]
    assert result["tokens_per_second"] == 80
    assert result["samples"] == 3


def gpu_report(rate, mode, *capacities):
    profile, placement = benchmark_profile(
        HARDWARE,
        mode,
        [
            {"name": "NVIDIA A100", "vendor": "nvidia", "total": gib * 1024**3, "id": "GPU-private"}
            for gib in capacities
        ],
    )
    return measurement(rate, hardware=profile.model_dump(), placement=placement.model_dump())


def test_multi_gpu_upload_moderation_averages_and_legacy_compatibility(tmp_path):
    legacy = gpu_report(30, "single-gpu", 80)
    legacy.pop("placement")
    single = gpu_report(40, "single-gpu", 80)
    replica = gpu_report(60, "replicated", 80)
    sharded = gpu_report(100, "sharded", 80, 80)
    four_gpus = gpu_report(200, "sharded", 80, 80, 80, 80)
    mixed_sizes = gpu_report(150, "sharded", 40, 80)
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        url = "/api/v1/benchmarks"
        # A row hashed before the upgrade still deduplicates, even with explicit null.
        assert client.post(url, json={"entries": [legacy]}).json()["accepted"] == 1
        assert (
            client.post(url, json={"entries": [{**legacy, "placement": None}]}).json()["duplicates"]
            == 1
        )
        for entry in (single, replica, sharded, four_gpus, mixed_sizes):
            assert client.post(url, json={"entries": [entry]}).json()["accepted"] == 1
            row = next(row for row in review_entries(tmp_path) if row["entry"] == entry)
            assert "disagrees-with-approved-results" not in row["flags"]
            moderate(tmp_path, row["digest"], "approved")
        reverse = {
            **mixed_sizes,
            "placement": {
                **mixed_sizes["placement"],
                "gpus": mixed_sizes["placement"]["gpus"][::-1],
            },
        }
        assert client.post(url, json={"entries": [reverse]}).json()["duplicates"] == 1
        moderate(tmp_path, review_entries(tmp_path)[0]["digest"], "approved")
        assert client.get(url).json() == {"entries": [legacy], "total": 1}
        assert client.get(url + "?include_placement=true").json()["total"] == 6
        assert (
            client.post(url, json={"entries": [gpu_report(120, "sharded", 80, 80)]}).json()[
                "accepted"
            ]
            == 1
        )
        moderate(tmp_path, review_entries(tmp_path)[0]["digest"], "approved")
        rows = client.get("/api/v1/averages").json()["entries"]
        assert {row["tokens_per_second"]: row["samples"] for row in rows} == {
            30: 1,
            40: 1,
            60: 1,
            110: 2,
            150: 1,
            200: 1,
        }
        assert next(row for row in rows if row["tokens_per_second"] == 30)["placement"] is None
        assert (
            next(row for row in rows if row["tokens_per_second"] == 110)["placement"]
            == sharded["placement"]
        )


def test_ranking_uses_exact_placement_and_measured_gpu_profile():
    single, replica, sharded, bigger = [
        SharedBenchmark.model_validate(row)
        for row in (
            gpu_report(40, "single-gpu", 80),
            gpu_report(60, "replicated", 80),
            gpu_report(100, "sharded", 80, 80),
            gpu_report(200, "sharded", 80, 80, 80, 80),
        )
    ]
    unknown = single.model_copy(update={"placement": None})
    entries = [unknown, single, replica, sharded, bigger]
    for entry in (single, replica, sharded):
        ranked = rank_matches(entries, [(entry.hardware, entry.placement)], 4096, 2, True, "q4_0")
        assert [row["tokens_per_second"] for row in ranked] == [entry.tokens_per_second]


@pytest.mark.parametrize(
    "patch",
    [
        {"gpu_count": 1},
        {"gpu_count": "2"},
        {"mode": "replicated"},
        {"mode": "pool-throughput"},
        {"combined_vram_gib": None},
        {"combined_vram_gib": 256},
        {
            "gpus": [
                {
                    "accelerator": "GPU-private",
                    "accelerator_memory_gib": 96,
                    "unified_memory": False,
                }
            ]
            * 2
        },
        {
            "gpus": [
                {"accelerator": "nvidia-A100", "accelerator_memory_gib": 96, "unified_memory": True}
            ]
            * 2
        },
        {
            "gpus": [
                {"accelerator": "amd-other", "accelerator_memory_gib": 96, "unified_memory": False},
                {
                    "accelerator": "nvidia-A100",
                    "accelerator_memory_gib": 96,
                    "unified_memory": False,
                },
            ]
        },
    ],
)
def test_invalid_multi_gpu_upload_is_rejected(tmp_path, patch):
    entry = gpu_report(100, "sharded", 80, 80)
    entry["placement"].update(patch)
    with TestClient(create_app(tmp_path), base_url="https://statistics") as client:
        response = client.post("/api/v1/benchmarks", json={"entries": [entry]})
        assert response.status_code == 422
        assert "GPU-private" not in response.text


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
