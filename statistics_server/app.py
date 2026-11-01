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

from llameros.benchmark_exchange import VoucherUpload
from llameros.body_limits import BodyLimits

from .database import flags_for, initialize


def create_app(
    state_dir: Path, *, max_entries=100000, requests_per_minute=120, uploads_per_minute=20
):
    database = initialize(state_dir)
    lock = threading.Lock()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimits, inference_bytes=131072)
    budgets = {"read": [time.monotonic(), 0], "upload": [time.monotonic(), 0]}

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        # Global limiter: deliberately no IP-based tracking, not even hashed IPs.
        upload = request.method not in {"GET", "HEAD"}
        with lock:
            budget = budgets["upload" if upload else "read"]
            if time.monotonic() - budget[0] >= 60:
                budget[:] = [time.monotonic(), 0]
            budget[1] += 1
            limited = budget[1] > (uploads_per_minute if upload else requests_per_minute)
        if request.url.scheme != "https":
            response = JSONResponse({"detail": "HTTPS required"}, 400)
        elif limited:
            response = JSONResponse({"detail": "Server busy; try later"}, 429)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
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

    @app.get("/api/v1/benchmarks")
    def entries(offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        with sqlite3.connect(database) as db:
            total = db.execute("SELECT count(*) FROM entries WHERE status='approved'").fetchone()[0]
            rows = db.execute(
                "SELECT data FROM entries WHERE status='approved' ORDER BY digest LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return {"entries": [json.loads(row[0]) for row in rows], "total": total}

    @app.post("/api/v1/benchmarks")
    def collect(body: VoucherUpload):
        records = {}
        for entry in body.entries:
            data = json.dumps(entry.model_dump(), sort_keys=True, separators=(",", ":"))
            records[hashlib.sha256(data.encode()).hexdigest()] = (data, entry)
        with lock, sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            voucher_digest = hashlib.sha256(body.voucher.encode()).hexdigest()
            voucher = db.execute(
                "SELECT expires,max_entries,redeemed FROM vouchers WHERE digest=?",
                (voucher_digest,),
            ).fetchone()
            if not voucher or voucher[0] <= int(time.time()) or voucher[2]:
                raise HTTPException(403, "Invalid, expired or redeemed upload voucher")
            if len(body.entries) > voucher[1]:
                raise HTTPException(400, "Batch exceeds this voucher's entry limit")
            new = [
                (key, data, "pending", json.dumps(flags_for(entry, db)))
                for key, (data, entry) in records.items()
                if not db.execute("SELECT 1 FROM entries WHERE digest=?", (key,)).fetchone()
            ]
            count = db.execute("SELECT count(*) FROM entries").fetchone()[0]
            if count + len(new) > max_entries:
                raise HTTPException(503, "Database capacity reached")
            db.executemany("INSERT INTO entries(digest,data,status,flags) VALUES (?,?,?,?)", new)
            # Atomic with insertion; never store any voucher reference on an entry.
            db.execute("UPDATE vouchers SET redeemed=1 WHERE digest=?", (voucher_digest,))
        return {"accepted": len(new), "duplicates": len(records) - len(new), "status": "pending"}

    return app
