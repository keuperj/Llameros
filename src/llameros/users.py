"""Persisted dashboard roles and account blocks for Llameros multi-user authorization.

Persisted dashboard roles and blocks; passwords remain exclusively with PAM.
"""

import json
import pwd
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import current_user


class UserUpdate(BaseModel):
    """Strict admin mutation payload for a dashboard role and blocked state."""

    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["admin", "user"]
    blocked: bool
    models: list[str] | None = Field(default=None, max_length=100)
    targets: list[str] | None = Field(default=None, max_length=100)
    grant_days: int | None = Field(default=None, ge=1, le=90)

    @field_validator("models", "targets")
    @classmethod
    def scopes(cls, value, info):
        if value is None:
            return value
        from .api_keys import KeyRequest

        if "*" in value:
            raise ValueError("User grants require explicit scopes")
        if not value:
            return []
        return getattr(KeyRequest(name="grant", **{info.field_name: value}), info.field_name)


def login_accounts():
    # Include named local/NSS accounts with a login shell, not daemon identities.
    """Return local/NSS account names with login shells, excluding daemon-only shells."""
    return {
        entry.pw_name
        for entry in pwd.getpwall()
        if entry.pw_shell and entry.pw_shell.rsplit("/", 1)[-1] not in {"nologin", "false"}
    }


class Users:
    """Persist role overrides and blocks while defaulting other login accounts to users."""

    def __init__(self, store):
        """Create the role table and seed the process account as admin only when absent."""
        self.store = store
        self.owner = current_user()
        with store.connect() as db:
            bootstrap = not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    blocked INTEGER NOT NULL DEFAULT 0
                )
            """)
            if bootstrap:
                db.execute("INSERT INTO users(username,role) VALUES(?, 'admin')", (self.owner,))
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            for name, definition in {
                "models": "TEXT NOT NULL DEFAULT '[]'",
                "targets": "TEXT NOT NULL DEFAULT '[]'",
                "grant_expires": "REAL NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    def get(self, username):
        """Return persisted account policy or an unblocked user default for an unknown name."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        result = (
            dict(row)
            if row
            else {
                "username": username,
                "role": "user",
                "blocked": 0,
                "models": "[]",
                "targets": "[]",
                "grant_expires": 0,
            }
        )
        return {
            **result,
            "models": json.loads(result["models"]),
            "targets": json.loads(result["targets"]),
        }

    def active(self, username):
        """Missing policy and failed host account checks invalidate existing credentials."""
        with self.store.connect() as db:
            row = db.execute("SELECT blocked FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            self.offboard(username)
            return False
        if row["blocked"]:
            return False
        if self.store.account_check is not None and not self.store.account_check(username):
            self.offboard(username)
            return False
        return True

    def offboard(self, username, actor="system"):
        """Persist a deny tombstone and revoke keys; re-enabling requires an administrator."""
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO users(username,role,blocked) VALUES(?,'user',1) ON CONFLICT(username) DO UPDATE SET blocked=1",
                (username,),
            )
            db.execute("UPDATE api_keys SET revoked=1 WHERE user=?", (username,))
            self.store.security_event(actor, "user.offboard", username, db=db)

    def list(self):
        """Combine discovered login accounts, the process owner, and remembered policy records."""
        names = login_accounts() | {self.owner}
        with self.store.connect() as db:
            names.update(row[0] for row in db.execute("SELECT username FROM users"))
        return [self.get(name) for name in sorted(names)]

    def remember(self, username):
        """Persist a successful login without overwriting an existing role or block."""
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO users(username,role) VALUES(?, 'user')", (username,))

    def update(self, username, body, actor):
        """Change account policy and revoke all owned keys atomically when blocking.

        Reject unknown accounts and self-block/demotion; the route separately invalidates active
        sessions.
        """
        if username not in {user["username"] for user in self.list()}:
            raise HTTPException(404, "System user not found")
        if username == actor and (body.blocked or body.role != "admin"):
            raise HTTPException(400, "You cannot block or demote yourself")
        with self.store.connect() as db:
            # The role/block update and revocation are one transaction. Key creation
            # also checks this table in its write transaction to avoid block races.
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO users(username,role,blocked) VALUES(?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET role=excluded.role,blocked=excluded.blocked",
                (username, body.role, body.blocked),
            )
            if body.blocked:
                db.execute("UPDATE api_keys SET revoked=1 WHERE user=?", (username,))
            for field in ("models", "targets"):
                value = getattr(body, field)
                if value is not None:
                    db.execute(
                        f"UPDATE users SET {field}=? WHERE username=?",
                        (json.dumps(value), username),
                    )
            if body.grant_days is not None:
                db.execute(
                    "UPDATE users SET grant_expires=? WHERE username=?",
                    (time.time() + body.grant_days * 86400, username),
                )
            self.store.security_event(actor, "user.update", username, db=db)
        return self.get(username)
