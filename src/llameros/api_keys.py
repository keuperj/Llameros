"""High-entropy bearer credentials; only digests and content-free usage persist."""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .catalog import validate_model


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    models: list[str] = Field(min_length=1, max_length=100)
    targets: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)
    expires_days: int = Field(default=30, ge=1, le=365)

    @field_validator("models")
    @classmethod
    def model_permissions(cls, values):
        return sorted(set("*" if value == "*" else normalize(value) for value in values))

    @field_validator("targets")
    @classmethod
    def target_permissions(cls, values):
        import re

        if any(
            value != "*" and not re.fullmatch(r"cpu|gpu-[a-f0-9]{16}", value) for value in values
        ):
            raise ValueError("Targets must be cpu, a GPU slot ID, or *")
        return sorted(set(values))


def normalize(model):
    name = validate_model(model)
    return name if ":" in name else name + ":latest"


class ApiKeys:
    def __init__(self, store):
        self.store = store
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

    def create(self, body):
        token = "llm_" + secrets.token_urlsafe(32)
        identity = secrets.token_hex(12)
        now = time.time()
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO api_keys(id,name,digest,models,targets,created,expires) VALUES(?,?,?,?,?,?,?)",
                (
                    identity,
                    body.name,
                    hashlib.sha256(token.encode()).hexdigest(),
                    json.dumps(body.models),
                    json.dumps(body.targets),
                    now,
                    now + body.expires_days * 86400,
                ),
            )
        return {"id": identity, "key": token}

    def authenticate(self, authorization):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.startswith("llm_") or len(token) > 128:
            raise HTTPException(
                401, "Valid bearer API key required", headers={"WWW-Authenticate": "Bearer"}
            )
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM api_keys WHERE digest=? AND revoked=0 AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(
                401, "Invalid, expired or revoked API key", headers={"WWW-Authenticate": "Bearer"}
            )
        return {
            **dict(row),
            "models": json.loads(row["models"]),
            "targets": json.loads(row["targets"]),
        }

    def allowed(self, key, model, target):
        return ("*" in key["models"] or normalize(model) in key["models"]) and (
            "*" in key["targets"] or target in key["targets"]
        )

    def revoke(self, identity):
        with self.store.connect() as db:
            if not db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (identity,)).rowcount:
                raise HTTPException(404, "API key not found")

    def record(self, identity, model, target, status, counts, duration):
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

    def list(self):
        with self.store.connect() as db:
            keys = db.execute("""
                SELECT k.id,k.name,k.models,k.targets,k.created,k.expires,k.revoked,
                    COUNT(u.id) AS requests,
                    COALESCE(SUM(u.prompt_tokens),0) AS prompt_tokens,
                    COALESCE(SUM(u.output_tokens),0) AS output_tokens,
                    COALESCE(SUM(u.duration),0) AS seconds,
                    SUM(CASE WHEN u.id IS NOT NULL AND u.status!='completed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN u.id IS NOT NULL AND (u.prompt_tokens IS NULL OR u.output_tokens IS NULL) THEN 1 ELSE 0 END) AS unknown_usage
                FROM api_keys k LEFT JOIN api_usage u ON k.id=u.key_id GROUP BY k.id
                ORDER BY k.created DESC
            """).fetchall()
        return [
            {
                **dict(row),
                "models": json.loads(row["models"]),
                "targets": json.loads(row["targets"]),
            }
            for row in keys
        ]
