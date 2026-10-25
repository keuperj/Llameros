"""Persisted dashboard roles and blocks; passwords remain exclusively with PAM."""

import pwd
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .config import current_user


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["admin", "user"]
    blocked: bool


def login_accounts():
    # Include named local/NSS accounts with a login shell, not daemon identities.
    return {
        entry.pw_name
        for entry in pwd.getpwall()
        if entry.pw_shell and entry.pw_shell.rsplit("/", 1)[-1] not in {"nologin", "false"}
    }


class Users:
    def __init__(self, store):
        self.store = store
        self.owner = current_user()
        with store.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    blocked INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO users(username,role) VALUES(?, 'admin')", (self.owner,)
            )

    def get(self, username):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else {"username": username, "role": "user", "blocked": 0}

    def list(self):
        names = login_accounts() | {self.owner}
        with self.store.connect() as db:
            names.update(row[0] for row in db.execute("SELECT username FROM users"))
        return [self.get(name) for name in sorted(names)]

    def remember(self, username):
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO users(username,role) VALUES(?, 'user')", (username,))

    def update(self, username, body, actor):
        if username not in {user["username"] for user in self.list()}:
            raise HTTPException(404, "System user not found")
        if username == actor and (body.blocked or body.role != "admin"):
            raise HTTPException(400, "You cannot block or demote yourself")
        with self.store.connect() as db:
            # The role/block update and revocation are one transaction. Key creation
            # also checks this table in its write transaction to avoid block races.
            db.execute(
                "INSERT INTO users(username,role,blocked) VALUES(?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET role=excluded.role,blocked=excluded.blocked",
                (username, body.role, body.blocked),
            )
            if body.blocked:
                db.execute("UPDATE api_keys SET revoked=1 WHERE user=?", (username,))
        return self.get(username)
