"""Bearer-key storage, ownership, permissions, and usage for the Llameros inference gateway.

High-entropy bearer credentials; only digests and content-free usage persist.
"""

import hashlib
import json
import re
import secrets
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .limits import LimitConfig, Limits
from .users import Users


class KeyRequest(BaseModel):
    """Validated key-creation payload; omitted owner is resolved from the authenticated session."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    user: str | None = Field(default=None, min_length=1, max_length=256)
    models: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)
    max_tokens: int | None = Field(default=None, ge=1, le=4096, strict=True)
    # Accepted for older clients; hardware scopes are no longer enforced.
    targets: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)
    expires_days: int | None = Field(default=None, ge=1, le=365, strict=True)
    limits: LimitConfig = Field(default_factory=LimitConfig)

    @field_validator("models")
    @classmethod
    def model_permissions(cls, values):
        """Normalize and deduplicate model permissions while preserving the wildcard."""
        return sorted(set("*" if value == "*" else normalize(value) for value in values))

    @field_validator("targets")
    @classmethod
    def target_permissions(cls, values):
        """Validate CPU/GPU slot identifiers and deduplicate hardware permissions."""
        import re

        if any(
            value != "*" and not re.fullmatch(r"cpu|gpu-[a-f0-9]{16}", value) for value in values
        ):
            raise ValueError("Targets must be cpu, a GPU slot ID, or *")
        return sorted(set(values))


def normalize(model):
    # Inference accepts namespaced local models; catalogue/lifecycle rules stay stricter.
    """Return a model name with an explicit tag, accepting inference namespaces.

    Raise ValueError for malformed names; catalogue/lifecycle validation is deliberately
    stricter.
    """
    if (
        not isinstance(model, str)
        or len(model) > 256
        or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._-]*(?:/[a-zA-Z0-9][a-zA-Z0-9._-]*)*(?::[a-zA-Z0-9][a-zA-Z0-9._-]*)?",
            model,
        )
        or any(part in {".", ".."} for part in model.split("/"))
    ):
        raise ValueError("Valid local model name required")
    name = model
    return name if ":" in name else name + ":latest"


class ApiKeys:
    """Store hashed bearer secrets and retain owner-scoped, content-free usage tombstones."""

    def __init__(self, store):
        """Create key/usage tables and migrate legacy keys to the process account."""
        self.store = store
        self.users = Users(store)
        self.limits = Limits(store)
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
                    models TEXT NOT NULL, targets TEXT NOT NULL, created REAL NOT NULL,
                    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    id INTEGER PRIMARY KEY, key_id TEXT NOT NULL, timestamp REAL NOT NULL,
                    model TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
                    prompt_tokens INTEGER, output_tokens INTEGER, duration REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS api_usage_key ON api_usage(key_id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(api_keys)")}
            if "max_tokens" not in columns:
                db.execute("ALTER TABLE api_keys ADD COLUMN max_tokens INTEGER")
            if "deleted" not in columns:
                db.execute("ALTER TABLE api_keys ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
            if "user" not in columns:
                db.execute("ALTER TABLE api_keys ADD COLUMN user TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE api_keys SET user=?", (self.users.owner,))
            db.execute("CREATE INDEX IF NOT EXISTS api_keys_user ON api_keys(user)")

    def create(self, body, user=None, actor=None):
        """Persist a new key and return its ID and one-time plaintext secret.

        The caller authorizes the owner; a write transaction rejects blocked owners atomically
        with insertion.
        """
        token = "llm_" + secrets.token_urlsafe(32)
        identity = secrets.token_hex(12)
        now = time.time()
        owner = user or body.user or self.users.owner
        if not self.users.active(owner):
            raise HTTPException(403, "User is unavailable or blocked")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM users WHERE username=?", (owner,)).fetchone()
            if not account or account["blocked"]:
                raise HTTPException(403, "User is blocked")
            expires = now + body.expires_days * 86400 if body.expires_days is not None else 0
            db.execute(
                "INSERT INTO api_keys(id,name,digest,models,targets,created,expires,max_tokens,user) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    body.name,
                    hashlib.sha256(token.encode()).hexdigest(),
                    json.dumps(body.models),
                    json.dumps(["*"]),  # Legacy targets payloads no longer restrict hardware.
                    now,
                    expires,
                    body.max_tokens,
                    owner,
                ),
            )
            self.store.security_event(actor or owner, "key.create", identity, db=db)
            db.execute(
                "INSERT INTO token_limits VALUES('key',?,?,?,?)",
                (identity, body.limits.input_k, body.limits.output_k, body.limits.period),
            )
        return {"id": identity, "key": token}

    def authenticate(self, authorization):
        """Resolve a bearer header to an unexpired, unrevoked key with decoded permissions.

        Blocked owners are rejected too; failures raise HTTP 401 without exposing stored
        digests.
        """
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.startswith("llm_") or len(token) > 128:
            raise HTTPException(
                401, "Valid bearer API key required", headers={"WWW-Authenticate": "Bearer"}
            )
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM api_keys WHERE digest=? AND revoked=0 AND (expires=0 OR expires>?)",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        if not row or not self.users.active(row["user"]):
            raise HTTPException(
                401, "Invalid, expired or revoked API key", headers={"WWW-Authenticate": "Bearer"}
            )
        return {
            **dict(row),
            "models": json.loads(row["models"]),
            "targets": ["*"],
        }

    def allowed(self, key, model, target):
        """Authorize the model on any hardware; target is only a routing selection."""
        return "*" in key["models"] or normalize(model) in key["models"]

    def revoke(self, identity, user=None, actor=None):
        """Disable a key, optionally constrained to its owner; raise HTTP 404 on mismatch."""
        with self.store.connect() as db:
            if not db.execute(
                "UPDATE api_keys SET revoked=1 WHERE id=?" + (" AND user=?" if user else ""),
                (identity, user) if user else (identity,),
            ).rowcount:
                raise HTTPException(404, "API key not found")
            self.store.security_event(
                actor or user or self.users.owner, "key.revoke", identity, db=db
            )

    def record(self, identity, model, target, status, counts, duration):
        """Append status, known token counts, and elapsed seconds without storing request content."""
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO api_usage(key_id,timestamp,model,target,status,prompt_tokens,output_tokens,duration) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    time.time(),
                    model,
                    target,
                    status,
                    counts.get("prompt"),
                    counts.get("output"),
                    duration,
                ),
            )
            self.store.trim(db, "api_usage")

            if status != "completed":
                db.execute("UPDATE activity_alert SET failures=failures+1 WHERE id=1")

    def delete(self, identity, user=None, actor=None):
        # Retain a tombstone and usage for history, including in-flight requests.
        """Tombstone and revoke a key while preserving usage, optionally enforcing ownership."""
        with self.store.connect() as db:
            if not db.execute(
                "UPDATE api_keys SET deleted=1,revoked=1 WHERE id=? AND deleted=0"
                + (" AND user=?" if user else ""),
                (identity, user) if user else (identity,),
            ).rowcount:
                raise HTTPException(404, "API key not found")
            self.store.security_event(
                actor or user or self.users.owner, "key.delete", identity, db=db
            )

    def list(self, user=None):
        """Return safe key metadata and usage aggregates, optionally restricted to one owner."""
        with self.store.connect() as db:
            self.store.trim(db, "api_usage")
            db.execute(
                "DELETE FROM api_keys WHERE (deleted=1 OR revoked=1 OR (expires>0 AND expires<?)) AND created<? AND id NOT IN (SELECT key_id FROM api_usage)",
                (time.time(), time.time() - self.store.retention_days * 86400),
            )
            keys = db.execute(
                """
                SELECT k.id,k.name,k.user,k.models,k.targets,k.created,k.expires,k.revoked,k.deleted,k.max_tokens,
                    COUNT(u.id) AS requests,
                    COALESCE(SUM(u.prompt_tokens),0) AS prompt_tokens,
                    COALESCE(SUM(u.output_tokens),0) AS output_tokens,
                    COALESCE(SUM(u.duration),0) AS seconds,
                    SUM(CASE WHEN u.id IS NOT NULL AND u.status!='completed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN u.id IS NOT NULL AND (u.prompt_tokens IS NULL OR u.output_tokens IS NULL) THEN 1 ELSE 0 END) AS unknown_usage
                FROM api_keys k LEFT JOIN api_usage u ON k.id=u.key_id
                WHERE (? IS NULL OR k.user=?) GROUP BY k.id
                ORDER BY k.created DESC
            """,
                (user, user),
            ).fetchall()
        return [
            {
                **dict(row),
                "limits": self.limits.snapshot("key", row["id"]),
                "user_limits": self.limits.snapshot("user", row["user"]),
                "models": json.loads(row["models"]),
                "targets": ["*"],
            }
            for row in keys
        ]
