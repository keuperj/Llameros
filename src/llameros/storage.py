"""Content-free SQLite events, resource samples, and generation history for Llameros dashboards.

SQLite history. Prompts, responses, and passwords are deliberately not persisted.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    """Short-lived transactional SQLite access for telemetry; never persist prompts or passwords."""

    def __init__(self, path: Path):
        """Initialize WAL-backed history tables and restrict the database file to its owner."""
        self.path = path
        from .auth import account_available

        self.account_check = account_available
        self.retention_days = 30
        self.history_rows = 10000
        self.security_lock = threading.Lock()
        self.security_window = (0, 0)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
                    model TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generations (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    duration_ns INTEGER NOT NULL, eval_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp);
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE INDEX IF NOT EXISTS generations_time ON generations(timestamp);
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL,
                    subject TEXT NOT NULL, outcome TEXT NOT NULL
                );
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        """Yield a row-addressable SQLite connection, committing or rolling back and always closing."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind: str, model: str = "", detail: str = ""):
        """Append a timestamped lifecycle/action event without request or response bodies."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(timestamp,model,kind,detail) VALUES(?,?,?,?)",
                (time.time(), model, kind, detail),
            )
            self.trim(db, "events")

    def trim(self, db, table):
        """Apply age and row bounds in the writer transaction, independently of sampling."""
        if table not in {"events", "samples", "generations", "api_usage", "security_events"}:
            raise ValueError("Invalid retention table")
        db.execute(
            f"DELETE FROM {table} WHERE timestamp < ?", (time.time() - self.retention_days * 86400,)
        )
        db.execute(
            f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT ?)",
            (self.history_rows,),
        )

    def security_event(self, actor, action, subject="", outcome="success", *, db=None):
        """Persist fixed-schema, bounded metadata, never arbitrary bodies or secrets.

        Failed-login traffic has a global 30-row/minute write budget. Successful
        administrative mutations are recorded in their own database transaction.
        """
        if action not in {
            "login",
            "logout",
            "key.create",
            "key.revoke",
            "key.delete",
            "user.update",
            "user.offboard",
            "limits.update",
        }:
            raise ValueError("Unknown security event")
        if action == "login" and outcome != "success":
            with self.security_lock:
                window, count = self.security_window
                now = int(time.monotonic() // 60)
                count = count if window == now else 0
                if count >= 30:
                    return
                self.security_window = (now, count + 1)

        def write(connection):
            connection.execute(
                "INSERT INTO security_events(timestamp,actor,action,subject,outcome) VALUES(?,?,?,?,?)",
                (time.time(), actor[:256], action, subject[:256], outcome[:32]),
            )
            self.trim(connection, "security_events")

        if db is not None:
            write(db)
        else:
            with self.connect() as connection:
                write(connection)

    def security_history(self):
        with self.connect() as db:
            self.trim(db, "security_events")
            return [
                dict(row)
                for row in db.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT 1000")
            ]

    def sample(self, data: dict, retention_days: int):
        """Store one resource snapshot and prune history older than retention_days."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO samples(timestamp,data) VALUES(?,?)", (time.time(), json.dumps(data))
            )
            cutoff = time.time() - retention_days * 86400
            for table in ("samples", "events", "generations"):
                db.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                self.trim(db, table)

    def generation(self, data: dict):
        """Record model token counts and nanosecond timings from a completed generation."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO generations(timestamp,model,prompt_tokens,output_tokens,duration_ns,eval_ns) VALUES(?,?,?,?,?,?)",
                (
                    time.time(),
                    data["model"],
                    data.get("prompt_eval_count", 0),
                    data.get("eval_count", 0),
                    data.get("total_duration", 0),
                    data.get("eval_duration", 0),
                ),
            )
            self.trim(db, "generations")

    def history(self, limit: int = 240) -> dict:
        """Return recent samples/events and overall per-model generation totals.

        Samples are chronological, events newest first, and aggregate durations are converted to
        seconds.
        """
        with self.connect() as db:
            samples = [
                dict(row)
                for row in db.execute(
                    "SELECT timestamp,data FROM samples ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]
            events = [
                dict(row)
                for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
            ]
            models = [
                dict(row)
                for row in db.execute("""
                SELECT model, COUNT(*) AS requests, SUM(prompt_tokens) AS prompt_tokens,
                       SUM(output_tokens) AS output_tokens, SUM(duration_ns)/1e9 AS seconds,
                       CASE WHEN SUM(CASE WHEN output_tokens>0 THEN CASE WHEN eval_ns>0 THEN eval_ns ELSE duration_ns END ELSE 0 END)>0
                            THEN SUM(output_tokens)*1e9/SUM(CASE WHEN output_tokens>0 THEN CASE WHEN eval_ns>0 THEN eval_ns ELSE duration_ns END ELSE 0 END)
                            ELSE 0 END AS tokens_per_second
                FROM generations GROUP BY model ORDER BY requests DESC
            """)
            ]
        return {
            "samples": [
                {"timestamp": row["timestamp"], **json.loads(row["data"])}
                for row in reversed(samples)
            ],
            "events": events,
            "models": models,
        }
