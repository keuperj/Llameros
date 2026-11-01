"""Local moderation and anonymous single-use admission. Never link vouchers to entries."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
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
        db.execute(
            "CREATE TABLE IF NOT EXISTS vouchers (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL, max_entries INTEGER NOT NULL, redeemed INTEGER NOT NULL DEFAULT 0)"
        )
    return path


def issue_voucher(state_dir, *, max_entries=10, hours=72):
    if not 1 <= max_entries <= 10 or not 1 <= hours <= 720:
        raise ValueError("Voucher limit must be 1–10 entries and expiry 1–720 hours")
    path = initialize(Path(state_dir))
    token = secrets.token_urlsafe(32)
    with sqlite3.connect(path) as db:
        # Expired hashes are no longer needed to prevent replay.
        db.execute("DELETE FROM vouchers WHERE expires <= ?", (int(time.time()),))
        db.execute(
            "INSERT INTO vouchers VALUES (?,?,?,0)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                int(time.time()) + hours * 3600,
                max_entries,
            ),
        )
    return token


def review_entries(state_dir, status="pending", limit=100, offset=0):
    if status not in {"pending", "approved", "rejected"} or not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("Invalid moderation filter")
    with sqlite3.connect(initialize(Path(state_dir))) as db:
        return [
            {
                "digest": digest,
                "status": status,
                "flags": json.loads(flags),
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


def flags_for(entry, db):
    flags = []
    if entry.model_digest is None:
        flags.append("missing-model-digest")
    if entry.tokens_per_second > (1000 if entry.kind == "chat" else 100000):
        flags.append("high-throughput-review")
    # Compare only like-for-like hardware, artifact, workload, settings and timing.
    key_fields = (
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
    candidate = entry.model_dump()
    rates = []
    for (data,) in db.execute("SELECT data FROM entries WHERE status='approved'"):
        row = json.loads(data)
        if all(row.get(key) == candidate.get(key) for key in key_fields):
            rates.append(row["tokens_per_second"])
    if rates:
        import statistics

        baseline = statistics.median(rates)
        if entry.tokens_per_second > baseline * 3 or entry.tokens_per_second < baseline / 3:
            flags.append("disagrees-with-approved-results")
    return flags
