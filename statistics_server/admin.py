"""Process-owner-only PAM administration. Credentials and sessions never enter SQLite."""

import os
import pwd
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field

from llameros.auth import Sessions, account_available, verify_password
from llameros.benchmark_exchange import StrictModel

from .database import issue_voucher, moderate, review_entries

COOKIE = "__Host-llameros-statistics-admin"


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class VoucherRequest(StrictModel):
    max_entries: int = Field(default=10, ge=1, le=10)
    hours: int = Field(default=72, ge=1, le=720)
    confirmed: bool = False


class ModerationRequest(StrictModel):
    status: Literal["approved", "rejected"]
    expected_status: Literal["pending", "approved", "rejected"]
    confirmed: bool = False


def install_admin(
    app,
    state_dir,
    database,
    max_entries,
    *,
    pam_service="login",
    session_seconds=1800,
    authenticator=None,
    account_checker=None,
):
    """Capture the effective process owner, not USER/SUDO_USER or a submitted identity."""
    owner = pwd.getpwuid(os.geteuid()).pw_name
    sessions = Sessions(session_seconds)
    authenticate = authenticator or verify_password
    account_check = account_checker or account_available
    # Exposed for deterministic expiry tests; not a network endpoint or persistent state.
    app.state.admin_sessions = sessions

    def same_origin(request: Request):
        origin = urlsplit(request.headers.get("origin", ""))
        if (
            request.url.scheme != "https"
            or origin.scheme != "https"
            or origin.netloc != request.headers.get("host")
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise HTTPException(403, "Same-origin HTTPS request required")

    def require_admin(request: Request):
        token = request.cookies.get(COOKIE, "")
        session = sessions.get(token)
        if not session or session["username"] != owner:
            raise HTTPException(401, "Administrator sign-in required")
        try:
            active = account_check(owner, pam_service)
        except Exception:
            active = False
        if not active:
            sessions.remove(token)
            raise HTTPException(401, "Administrator account unavailable")
        if request.method not in {"GET", "HEAD"}:
            same_origin(request)
            if not secrets.compare_digest(session["csrf"], request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "Invalid CSRF token")
        return session

    protected = [Depends(require_admin)]

    @app.get("/admin", include_in_schema=False)
    def page():
        return FileResponse(Path(__file__).parent / "admin.html")

    @app.get("/admin.js", include_in_schema=False)
    def script():
        return FileResponse(Path(__file__).parent / "admin.js", media_type="text/javascript")

    @app.post("/admin/api/login", dependencies=[Depends(same_origin)])
    def login(body: Login, request: Request, response: Response):
        # One global five-attempt/minute bucket. No IP addresses or arbitrary
        # submitted usernames are retained, even in the in-memory throttle.
        if not sessions.allow_attempt(
            "statistics-admin", "owner"
        ) or not sessions.pam_slots.acquire(blocking=False):
            raise HTTPException(
                429, "Too many login attempts; retry later", headers={"Retry-After": "60"}
            )
        try:
            try:
                valid = (
                    body.username == owner
                    and account_check(owner, pam_service)
                    and authenticate(owner, body.password, pam_service)
                )
            except Exception:
                valid = False
        finally:
            sessions.pam_slots.release()
        if not valid:
            raise HTTPException(401, "Administrator authentication failed")
        sessions.remove(request.cookies.get(COOKIE, ""))
        token, session = sessions.create(owner)
        response.set_cookie(
            COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=session_seconds,
        )
        return {"csrf": session["csrf"], "session_seconds": session_seconds}

    @app.get("/admin/api/session")
    def session_info(session=Depends(require_admin)):
        return {"csrf": session["csrf"]}

    @app.post("/admin/api/logout", dependencies=protected)
    def logout(request: Request, response: Response):
        sessions.remove(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return {"signed_out": True}

    @app.get("/admin/api/overview", dependencies=protected)
    def overview():
        with sqlite3.connect(database) as db:
            counts = {
                status: count
                for status, count in db.execute(
                    "SELECT status,count(*) FROM entries GROUP BY status"
                )
            }
            now = int(time.time())
            vouchers = db.execute(
                "SELECT coalesce(sum(CASE WHEN redeemed=0 AND expires>? THEN 1 ELSE 0 END),0), coalesce(sum(redeemed),0) FROM vouchers",
                (now,),
            ).fetchone()
        return {
            "entries": {
                status: counts.get(status, 0) for status in ("pending", "approved", "rejected")
            },
            "capacity": max_entries,
            "used": sum(counts.values()),
            "vouchers": {"available": vouchers[0], "redeemed": vouchers[1]},
        }

    @app.get("/admin/api/benchmarks", dependencies=protected)
    def benchmarks(
        status: Literal["pending", "approved", "rejected"] = "pending",
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        with sqlite3.connect(database) as db:
            total = db.execute("SELECT count(*) FROM entries WHERE status=?", (status,)).fetchone()[
                0
            ]
        return {"entries": review_entries(state_dir, status, limit, offset), "total": total}

    @app.post("/admin/api/vouchers", dependencies=protected)
    def vouchers(body: VoucherRequest):
        if not body.confirmed:
            raise HTTPException(400, "Confirm permission to upload one batch")
        return {
            "voucher": issue_voucher(state_dir, max_entries=body.max_entries, hours=body.hours),
            "max_entries": body.max_entries,
            "hours": body.hours,
        }

    @app.post("/admin/api/benchmarks/{digest}", dependencies=protected)
    def review(digest: str, body: ModerationRequest):
        if not body.confirmed:
            raise HTTPException(400, "Confirm moderation decision")
        try:
            moderate(state_dir, digest, body.status, expected_status=body.expected_status)
        except ValueError:
            raise HTTPException(
                409,
                "Cannot moderate: entry changed, missing, or lacks valid v2 evidence. Refresh and review.",
            ) from None
        return {"status": body.status}
