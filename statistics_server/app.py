"""SQLite benchmark collection and public browsing. No client metadata is persisted."""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

import llameros
from llameros.benchmark_exchange import PublicUpload
from llameros.body_limits import BodyLimits

from .admin import install_admin
from .database import approved_averages, flags_for, initialize


def create_app(
    state_dir: Path,
    *,
    max_entries=100000,
    requests_per_minute=120,
    uploads_per_minute=20,
    pam_service="login",
    session_seconds=1800,
    authenticator=None,
    account_checker=None,
):
    database = initialize(state_dir)
    lock = threading.Lock()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimits, inference_bytes=131072)
    budgets = {"read": [time.monotonic(), 0], "upload": [time.monotonic(), 0]}
    budgets["admin"] = [time.monotonic(), 0]

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        # Global limiter: deliberately no IP-based tracking, not even hashed IPs.
        upload = request.method not in {"GET", "HEAD"}
        admin = (
            request.url.path == "/admin"
            or request.url.path == "/admin.js"
            or request.url.path.startswith("/admin/")
        )
        with lock:
            budget = budgets["admin" if admin else "upload" if upload else "read"]
            if time.monotonic() - budget[0] >= 60:
                budget[:] = [time.monotonic(), 0]
            budget[1] += 1
            limited = budget[1] > (
                120 if admin else uploads_per_minute if upload else requests_per_minute
            )
        if request.url.scheme != "https":
            response = JSONResponse({"detail": "HTTPS required"}, 400)
        elif limited:
            response = JSONResponse({"detail": "Server busy; try later"}, 429)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Strict-Transport-Security": "max-age=31536000",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not echo submitted private values, even in invalid requests.
        return JSONResponse({"detail": "Invalid benchmark payload"}, 422)

    @app.get("/")
    def website():
        return FileResponse(Path(__file__).parent / "index.html")

    @app.get("/table.js")
    def script():
        return FileResponse(Path(__file__).parent / "table.js", media_type="text/javascript")

    @app.get("/table.css")
    def style():
        return FileResponse(Path(__file__).parent / "table.css", media_type="text/css")

    @app.get("/brand/{name}")
    def branding(name: str):
        """Reuse packaged dashboard branding without exposing other application assets."""
        assets = {
            "style.css": "text/css",
            "logo.svg": "image/svg+xml",
            "bmftr.png": "image/png",
            "forschung-an-haw.png": "image/png",
        }
        if name not in assets:
            raise HTTPException(404, "Asset not found")
        return FileResponse(
            Path(llameros.__file__).parent / "static" / name, media_type=assets[name]
        )

    @app.get("/api/v1/benchmarks")
    def entries(offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        with sqlite3.connect(database) as db:
            total = db.execute("SELECT count(*) FROM entries WHERE status='approved'").fetchone()[0]
            rows = db.execute(
                "SELECT data FROM entries WHERE status='approved' ORDER BY digest LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return {"entries": [json.loads(row[0]) for row in rows], "total": total}

    @app.get("/api/v1/averages")
    def averages(offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        with sqlite3.connect(database) as db:
            rows = approved_averages(db)
        return {"entries": rows[offset : offset + limit], "total": len(rows)}

    @app.post("/api/v1/benchmarks")
    def collect(body: PublicUpload):
        records = {}
        for entry in body.entries:
            data = json.dumps(entry.model_dump(), sort_keys=True, separators=(",", ":"))
            records[hashlib.sha256(data.encode()).hexdigest()] = (data, entry)
        with lock, sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            new = [
                (key, data, "pending", json.dumps(flags_for(entry, db)))
                for key, (data, entry) in records.items()
                if not db.execute("SELECT 1 FROM entries WHERE digest=?", (key,)).fetchone()
            ]
            count = db.execute("SELECT count(*) FROM entries").fetchone()[0]
            if count + len(new) > max_entries:
                raise HTTPException(503, "Database capacity reached")
            db.executemany("INSERT INTO entries(digest,data,status,flags) VALUES (?,?,?,?)", new)
        return {"accepted": len(new), "duplicates": len(records) - len(new), "status": "pending"}

    install_admin(
        app,
        state_dir,
        database,
        max_entries,
        pam_service=pam_service,
        session_seconds=session_seconds,
        authenticator=authenticator,
        account_checker=account_checker,
    )
    return app
