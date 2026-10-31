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
                CREATE TABLE IF NOT EXISTS activity_alert (
                    id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL,
                    acknowledged INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO activity_alert VALUES(1,0,0);
                CREATE TABLE IF NOT EXISTS token_hours (
                    hour INTEGER NOT NULL, username TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    PRIMARY KEY(hour,username)
                );
                CREATE TABLE IF NOT EXISTS activity_migrations (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS container_observations (
                    name TEXT PRIMARY KEY, container_id TEXT NOT NULL,
                    restart_count INTEGER NOT NULL, started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS queue_hours (
                    hour INTEGER NOT NULL, username TEXT NOT NULL,
                    max_size INTEGER NOT NULL, PRIMARY KEY(hour,username)
                );
                CREATE TABLE IF NOT EXISTS benchmarks (
                    model TEXT NOT NULL, target TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(model, target)
                );
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

    def observe_container(self, name, data, expected_start=False):
        """Atomically deduplicate observed restarts, including while the service was offline.

        A new container establishes a baseline (existing restart counts are reported).
        Controlled starts refresh the baseline without treating a user action as a fault.
        """
        identity = data.get("Id")
        if not identity:
            return
        count = max(0, int(data.get("RestartCount", 0)))
        started = data.get("State", {}).get("StartedAt", "")
        if started.startswith("0001-"):
            started = ""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM container_observations WHERE name=?", (name,)
            ).fetchone()
            same = previous is not None and previous["container_id"] == identity
            delta = max(0, count - previous["restart_count"]) if same else count
            changed = (
                same and previous["started_at"] and started and previous["started_at"] != started
            )
            if delta or (changed and not expected_start):
                detail = f"Container {name}: observed {delta or 1} restart(s); Docker restart count={count}; started at={started or 'unknown'}."
                db.execute(
                    "INSERT INTO events(timestamp,model,kind,detail) VALUES(?,?,?,?)",
                    (time.time(), "", "container-restart:failed", detail),
                )
                self.trim(db, "events")
            db.execute(
                """INSERT INTO container_observations VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET container_id=excluded.container_id,
                restart_count=excluded.restart_count,started_at=excluded.started_at""",
                (name, identity, count, started),
            )

    def trim(self, db, table):
        """Apply age and row bounds in the writer transaction, independently of sampling."""
        if table not in {"events", "samples", "generations", "api_usage", "security_events"}:
            raise ValueError("Invalid retention table")
        days = 90 if table in {"events", "api_usage", "security_events"} else self.retention_days
        db.execute(f"DELETE FROM {table} WHERE timestamp < ?", (time.time() - days * 86400,))
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
                if table != "events":
                    db.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                self.trim(db, table)
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "api_usage" in tables:
                self.trim(db, "api_usage")
            self.trim(db, "security_events")
            db.execute(
                "DELETE FROM token_hours WHERE hour<?",
                (int(time.time() - 90 * 86400) // 3600 * 3600,),
            )

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

    def activity(self, days=0):
        """Admin-only retained request/lifecycle records and durable failure watermark."""
        with self.connect() as db:
            cutoff = time.time() - days * 86400 if days else 0
            self.trim(db, "api_usage")
            self.trim(db, "events")
            requests = [
                dict(row)
                for row in db.execute(
                    """
                SELECT u.*, k.name AS key_name, k.user AS username,
                       CASE WHEN u.duration>0 AND u.output_tokens IS NOT NULL
                            THEN u.output_tokens/u.duration ELSE NULL END AS tokens_per_second
                FROM api_usage u LEFT JOIN api_keys k ON k.id=u.key_id
                WHERE u.timestamp>=?
                ORDER BY u.id DESC LIMIT 1000
            """,
                    (cutoff,),
                )
            ]
            admin = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE timestamp>=? ORDER BY id DESC LIMIT 1000", (cutoff,)
                )
            ]
            counts = dict(
                db.execute(
                    """SELECT COUNT(*) AS total,
                COALESCE(SUM(status='completed'),0) AS successful,
                COALESCE(SUM(status!='completed'),0) AS failed FROM api_usage WHERE timestamp>=?""",
                    (cutoff,),
                ).fetchone()
            )
            actions = dict(
                db.execute(
                    """SELECT COUNT(*) AS total,
                COALESCE(SUM(kind LIKE '%:completed'),0) AS successful,
                COALESCE(SUM(kind LIKE '%:failed'),0) AS failed FROM events WHERE timestamp>=?""",
                    (cutoff,),
                ).fetchone()
            )
            alert = dict(
                db.execute("SELECT failures,acknowledged FROM activity_alert WHERE id=1").fetchone()
            )
        return {
            "requests": requests,
            "admin": admin,
            "request_counts": counts,
            "admin_counts": actions,
            "alert": alert,
        }

    def queue_peak(self, users):
        """Record simultaneous waiting depth (global and per account), not active requests.

        Called under the scheduler lock on queue admission so brief spikes are retained.
        The empty username denotes the all-account total; peaks must never be summed.
        """
        hour = int(time.time()) // 3600 * 3600
        counts = {"": len(users)}
        for user in users:
            if user:
                counts[user] = counts.get(user, 0) + 1
        with self.connect() as db:
            db.executemany(
                """INSERT INTO queue_hours(hour,username,max_size) VALUES(?,?,?)
                ON CONFLICT(hour,username) DO UPDATE SET max_size=MAX(max_size,excluded.max_size)""",
                [(hour, user, count) for user, count in counts.items()],
            )
            db.execute("DELETE FROM queue_hours WHERE hour<?", (hour - 90 * 86400,))

    def token_history(self, username=None):
        """Hourly UTC totals; administrators may aggregate across all accounts."""
        with self.connect() as db:
            cutoff = int(time.time() - 90 * 86400) // 3600 * 3600
            db.execute("DELETE FROM token_hours WHERE hour<?", (cutoff,))
            db.execute("DELETE FROM queue_hours WHERE hour<?", (cutoff,))
            queues = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM queue_hours"
                    + (" WHERE username=?" if username is not None else "")
                    + " ORDER BY hour,username",
                    (username,) if username is not None else (),
                )
            ]
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM token_hours"
                    + (" WHERE username=?" if username is not None else "")
                    + " ORDER BY hour,username",
                    (username,) if username is not None else (),
                )
            ]
        return {
            "rows": rows,
            "queues": queues,
            "queue_username": username or "",
            "users": sorted({row["username"] for row in [*rows, *queues] if row["username"]}),
            "start": cutoff,
            "end": int(time.time()) // 3600 * 3600,
        }

    def acknowledge_activity(self, failures):
        """Acknowledge only the displayed watermark, never failures arriving afterward."""
        with self.connect() as db:
            db.execute(
                "UPDATE activity_alert SET acknowledged=MAX(acknowledged,MIN(failures,?)) WHERE id=1",
                (failures,),
            )

    def save_benchmark(self, result):
        """Keep the latest benchmark per model/device independently of telemetry retention."""
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO benchmarks(model,target,data) VALUES(?,?,?)",
                (result["model"], result["target"], json.dumps(result, allow_nan=False)),
            )

    def benchmarks(self):
        """Return content-free latest results, including incomplete attempts."""
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM benchmarks ORDER BY model,target")
            ]

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
            "benchmarks": self.benchmarks(),
        }
