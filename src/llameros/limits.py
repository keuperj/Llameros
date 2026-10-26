"""Durable, content-free token allowances independent of usage-history retention."""

import calendar
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class LimitConfig(BaseModel):
    """Allowances are integer thousands of tokens; null means unlimited."""

    model_config = ConfigDict(extra="forbid", strict=True)
    input_k: int | None = Field(default=None, ge=1, le=1_000_000_000)
    output_k: int | None = Field(default=None, ge=1, le=1_000_000_000)
    period: Literal["day", "week", "month", "never"] = "never"


def window(period, now):
    """Return UTC calendar window boundaries; lifetime has no reset."""
    date = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "never":
        return 0, None
    if period == "week":
        date -= timedelta(days=date.weekday())
    elif period == "month":
        date = date.replace(day=1)
    days = {"day": 1, "week": 7, "month": calendar.monthrange(date.year, date.month)[1]}[period]
    return int(date.timestamp()), int((date + timedelta(days=days)).timestamp())


class Limits:
    """Persist policies and current calendar/lifetime counters, including unlimited scopes."""

    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS token_limits (
                    scope TEXT NOT NULL, subject TEXT NOT NULL,
                    input_k INTEGER, output_k INTEGER, period TEXT NOT NULL,
                    PRIMARY KEY(scope,subject)
                );
                CREATE TABLE IF NOT EXISTS token_counters (
                    scope TEXT NOT NULL, subject TEXT NOT NULL, period TEXT NOT NULL,
                    bucket INTEGER NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    unknown_input INTEGER NOT NULL DEFAULT 0,
                    unknown_output INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope,subject,period,bucket)
                );
            """)

    def snapshot(self, scope, subject, db=None, now=None):
        """Expose policy, actual usage, remaining allowance and reset time."""
        now = time.time() if now is None else now
        if db is None:
            with self.store.connect() as connection:
                return self.snapshot(scope, subject, connection, now)
        row = db.execute(
            "SELECT * FROM token_limits WHERE scope=? AND subject=?", (scope, subject)
        ).fetchone()
        inherited = scope == "user" and row is None
        if inherited:
            row = db.execute(
                "SELECT * FROM token_limits WHERE scope='user-default' AND subject='*'"
            ).fetchone()
        config = (
            {key: row[key] for key in ("input_k", "output_k", "period")}
            if row
            else LimitConfig().model_dump()
        )
        bucket, reset = window(config["period"], now)
        counter = db.execute(
            "SELECT * FROM token_counters WHERE scope=? AND subject=? AND period=? AND bucket=?",
            (scope, subject, config["period"], bucket),
        ).fetchone()
        result = {**config, "resets_at": reset}
        if scope == "user":
            result["inherited"] = inherited
        for kind in ("input", "output"):
            used = counter[kind + "_tokens"] if counter else 0
            unknown = bool(counter["unknown_" + kind]) if counter else False
            cap = config[kind + "_k"]
            result[kind + "_used"] = used
            result[kind + "_unknown"] = unknown
            result[kind + "_remaining"] = (
                None if cap is None else 0 if unknown else max(0, cap * 1000 - used)
            )
        return result

    def set(self, scope, subject, config, actor):
        """Update allowances without resetting counters, recording the administrator/owner."""
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO token_limits VALUES(?,?,?,?,?) ON CONFLICT(scope,subject) DO UPDATE SET input_k=excluded.input_k,output_k=excluded.output_k,period=excluded.period",
                (scope, subject, config.input_k, config.output_k, config.period),
            )
            self.store.security_event(actor, "limits.update", scope + ":" + subject, db=db)
        return self.snapshot(scope, subject)

    def inherit_user(self, subject, actor):
        """Remove only the user policy override; preserve all accumulated counters."""
        with self.store.connect() as db:
            db.execute("DELETE FROM token_limits WHERE scope='user' AND subject=?", (subject,))
            self.store.security_event(actor, "limits.update", "user:inherit:" + subject, db=db)
        return self.snapshot("user", subject)

    def check(self, scopes):
        """Check immediately before dispatch; admitted requests may overshoot, by policy."""
        now = time.time()
        with self.store.connect() as db:
            for scope, subject in scopes:
                state = self.snapshot(scope, subject, db, now)
                if any(state[kind + "_remaining"] == 0 for kind in ("input", "output")):
                    headers = (
                        {"Retry-After": str(max(1, int(state["resets_at"] - now)))}
                        if state["resets_at"]
                        else None
                    )
                    raise HTTPException(
                        429,
                        f"{scope.capitalize()} token allowance exhausted or usage unavailable",
                        headers=headers,
                    )
        return now

    def charge(self, scopes, counts, started):
        """Atomically charge all scopes to admission windows, even after disconnect or reset.

        Missing upstream counts fail closed for finite allowances; actual usage remains
        explicitly unknown rather than silently treating an interrupted request as free.
        """
        with self.store.connect() as db:
            for scope, subject in scopes:
                for period in ("day", "week", "month", "never"):
                    bucket, _ = window(period, started)
                    values = [counts.get("prompt"), counts.get("output")]
                    values = [
                        value if type(value) is int and value >= 0 else None for value in values
                    ]
                    db.execute(
                        "INSERT INTO token_counters VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(scope,subject,period,bucket) DO UPDATE SET "
                        "input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,"
                        "unknown_input=MAX(unknown_input,excluded.unknown_input),unknown_output=MAX(unknown_output,excluded.unknown_output)",
                        (
                            scope,
                            subject,
                            period,
                            bucket,
                            values[0] or 0,
                            values[1] or 0,
                            values[0] is None,
                            values[1] is None,
                        ),
                    )
            # Keep current windows only, plus lifetime. Late completions never overwrite a new window.
            for period in ("day", "week", "month"):
                db.execute(
                    "DELETE FROM token_counters WHERE period=? AND bucket<?",
                    (period, window(period, time.time())[0]),
                )

    def models(self, rows):
        """Include configured models without history and attach their shared allowances."""
        result = {row["model"]: dict(row) for row in rows}
        with self.store.connect() as db:
            for row in db.execute("SELECT subject FROM token_limits WHERE scope='model'"):
                result.setdefault(row[0], {"model": row[0]})
        return [{**row, "limits": self.snapshot("model", name)} for name, row in result.items()]
