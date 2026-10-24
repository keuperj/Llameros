"""SQLite history. Prompts, responses, and passwords are deliberately not persisted."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
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
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind: str, model: str = "", detail: str = ""):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(timestamp,model,kind,detail) VALUES(?,?,?,?)",
                (time.time(), model, kind, detail),
            )

    def sample(self, data: dict, retention_days: int):
        with self.connect() as db:
            db.execute(
                "INSERT INTO samples(timestamp,data) VALUES(?,?)", (time.time(), json.dumps(data))
            )
            cutoff = time.time() - retention_days * 86400
            for table in ("samples", "events", "generations"):
                db.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))

    def generation(self, data: dict):
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

    def history(self, limit: int = 240) -> dict:
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
                       CASE WHEN SUM(eval_ns)>0 THEN SUM(output_tokens)*1e9/SUM(eval_ns) ELSE 0 END AS tokens_per_second
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
