"""Private original reports, moderation, and approved like-for-like averages."""

import json
import os
import sqlite3
import statistics
from pathlib import Path

from llameros.benchmark_exchange import SharedBenchmark


def initialize(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / "benchmarks.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    path.chmod(0o600)
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS entries (digest TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', flags TEXT NOT NULL DEFAULT '[]')"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(entries)")}
        # Existing public reports become pending; never grandfather unreviewed reports.
        if "status" not in columns:
            db.execute("ALTER TABLE entries ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "flags" not in columns:
            db.execute("ALTER TABLE entries ADD COLUMN flags TEXT NOT NULL DEFAULT '[]'")
        db.execute("CREATE INDEX IF NOT EXISTS entries_status ON entries(status, digest)")
        # Admission vouchers are obsolete; discard existing secret hashes on upgrade.
        db.execute("DROP TABLE IF EXISTS vouchers")
    return path


KEY_FIELDS = (
    "protocol",
    "model",
    "model_digest",
    "kind",
    "hardware",
    "context_length",
    "parallel",
    "flash_attention",
    "kv_cache_type",
    "timing",
)


def comparison_key(row):
    return json.dumps({key: row.get(key) for key in KEY_FIELDS}, sort_keys=True)


def approved_averages(db):
    """Compute from original approved reports, never average previous averages."""
    groups = {}
    for (data,) in db.execute("SELECT data FROM entries WHERE status='approved' ORDER BY digest"):
        row = json.loads(data)
        groups.setdefault(comparison_key(row), []).append(row["tokens_per_second"])
    return [
        {
            **json.loads(key),
            "tokens_per_second": statistics.mean(rates),
            "samples": len(rates),
            "minimum": min(rates),
            "maximum": max(rates),
        }
        for key, rates in sorted(groups.items())
    ]


def review_entries(state_dir, status="pending", limit=100, offset=0):
    if status not in {"pending", "approved", "rejected"} or not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("Invalid moderation filter")
    with sqlite3.connect(initialize(Path(state_dir))) as db:
        return [
            {
                "digest": digest,
                "status": status,
                "flags": flags_for(SharedBenchmark.model_validate(json.loads(data)), db, digest)
                if json.loads(data).get("protocol") == "llameros-six-cases-v2"
                else json.loads(flags),
                "entry": json.loads(data),
            }
            for digest, data, flags in db.execute(
                "SELECT digest,data,flags FROM entries WHERE status=? ORDER BY digest LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        ]


def moderate(state_dir, digest, status, *, expected_status=None):
    if status not in {"approved", "rejected"}:
        raise ValueError("Use approved or rejected")
    with sqlite3.connect(initialize(Path(state_dir))) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data,status FROM entries WHERE digest=?", (digest,)).fetchone()
        if not row:
            raise ValueError("Entry not found")
        if expected_status is not None and row[1] != expected_status:
            raise ValueError("Entry changed; refresh before reviewing again")
        if status == "approved":
            # Legacy v1 reports lack evidence; cannot approve until resubmitted as v2.
            SharedBenchmark.model_validate(json.loads(row[0]))
        db.execute("UPDATE entries SET status=? WHERE digest=?", (status, digest))


def flags_for(entry, db, exclude_digest=None):
    flags = []
    if entry.model_digest is None:
        flags.append("missing-model-digest")
    if entry.tokens_per_second > (1000 if entry.kind == "chat" else 100000):
        flags.append("high-throughput-review")
    # Compare only like-for-like hardware, artifact, workload, settings and timing.
    candidate = entry.model_dump()
    rates = []
    for digest, data in db.execute("SELECT digest,data FROM entries WHERE status='approved'"):
        if digest == exclude_digest:
            continue
        row = json.loads(data)
        if comparison_key(row) == comparison_key(candidate):
            rates.append(row["tokens_per_second"])
    if rates:
        baseline = statistics.mean(rates)
        if abs(entry.tokens_per_second - baseline) > baseline * 0.5:
            flags.append("disagrees-with-approved-results")
    return flags
