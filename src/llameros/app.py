"""FastAPI composition root for Llameros dashboards, authorization, jobs, and gateway installation."""

import asyncio
import copy
import hashlib
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import COOKIE, Sessions, account_available, verify_password
from .benchmark import measure
from .body_limits import BodyLimits
from .catalog import Catalog, validate_model
from .config import Settings, current_user
from .diagnostics import diagnostic
from .docker import DockerError
from .gateway import PREFIX, install_gateway
from .hardware import assess_fit, snapshot
from .runtime_transport import close_relays
from .runtimes import Runtimes
from .scheduling import InferenceScheduler
from .storage import Store
from .tls import public_certificate
from .users import Users, UserUpdate

log = logging.getLogger(__name__)


class Login(BaseModel):
    """Bounded PAM username/password payload; credentials are never persisted."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class ActivityAcknowledgement(BaseModel):
    failures: int = Field(ge=0, le=2**63 - 1, strict=True)


class ModelRequest(BaseModel):
    """Validated lifecycle model selection with an optional hardware target."""

    model: str = Field(min_length=1, max_length=210)
    target: str = Field(default="", max_length=80, pattern=r"^[a-z0-9-]*$")


class GenerateRequest(ModelRequest):
    """Admin playground prompt and bounded output budget for a loaded model."""

    prompt: str = Field(min_length=1, max_length=32000)
    max_tokens: int = Field(default=256, ge=1, le=4096)


class StorageRequest(BaseModel):
    """Requested model directory and explicit destructive-container-recreation confirmation."""

    directory: str = Field(default="", max_length=4096)
    confirmed: bool = False


class ActionError(BaseModel):
    """Bounded dashboard action/error metadata for content-free activity reporting."""

    action: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=1, max_length=2000)


class Jobs:
    """Single-worker lifecycle execution coordinated with per-device inference reservations."""

    def __init__(self, store: Store, parallel=2):
        """Initialize the lifecycle executor, shared coordination lock, and inference scheduler."""
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llameros-job")
        self.lock = threading.Lock()
        self.job = {"state": "idle"}
        self.scheduler = InferenceScheduler(self, parallel=parallel)

    def status(self):
        """Return a dashboard status snapshot without exposing mutable internal job state."""
        with self.lock:
            return copy.deepcopy(self.job)

    def submit(self, action, model, function, targets=None):
        """Reserve lifecycle work and return an acceptance flag and job ID, rejecting conflicts.

        The worker publishes progress and terminal state and records content-free events;
        targets=None reserves all devices.
        """
        with self.lock:
            if (
                self.job["state"] == "running" and self.job.get("action") != "api-inference"
            ) or self.scheduler.conflicts(targets):
                raise HTTPException(409, "Another lifecycle or inference job is running")
            self.job = {
                "state": "running",
                "action": action,
                "model": model,
                "started": time.time(),
                "progress": {"status": "Preparing operation"},
                "targets": sorted(targets) if targets is not None else None,
                "job_id": secrets.token_hex(16),
            }
            job_id = self.job["job_id"]

        def progress(value):
            """Merge worker progress into job state while retaining per-layer download observations."""
            with self.lock:
                value = dict(value)
                layer_id = value.get("id") or value.get("digest")
                if layer_id:
                    layers = self.job.setdefault("layers", {})
                    phase = value.get("phase", "model-download")
                    key = f"{phase}:{layer_id}:{'extract' if 'extract' in value.get('status', '').lower() else 'download'}"
                    previous = layers.get(key, {})
                    layers[key] = {
                        **previous,
                        **{name: item for name, item in value.items() if item is not None},
                    }
                    value["layers"] = list(layers.values())
                self.job["progress"] = value

        def work():
            """Execute the enclosing lifecycle operation in the worker and publish progress/results."""
            try:
                self.store.event(f"{action}:started", model)
                result = function(progress)
                self.store.event(f"{action}:completed", model)
                with self.lock:
                    self.job.update(
                        state="completed",
                        result=result,
                        finished=time.time(),
                        progress={"status": "Completed", "completed": 1, "total": 1},
                    )
            except Exception as exc:
                log.warning("Job %s failed [%s]", action, type(exc).__name__)
                with self.lock:
                    self.job.update(state="failed", error=diagnostic(exc), finished=time.time())
                self.store.event(f"{action}:failed", model, diagnostic(exc))

        self.pool.submit(work)
        return {"accepted": True, "job_id": job_id}


def create_app(
    settings: Settings | None = None,
    *,
    docker=None,
    catalog=None,
    authenticator=None,
    hardware_provider=None,
    sampling=True,
    gateway_transport=None,
    account_checker=None,
) -> FastAPI:
    """Compose the HTTPS dashboard, authorization, telemetry, runtime services, and inference gateway.

    Optional collaborators support offline tests; sampling controls background hardware
    collection. Returns a configured FastAPI application.
    """
    cfg = settings or Settings()
    cfg.prepare()
    store = Store(cfg.state_dir / "history.sqlite3")
    store.retention_days = cfg.retention_days
    store.account_check = account_checker or (
        lambda username: account_available(username, cfg.pam_service)
    )
    users = Users(store)
    supplied_docker = docker
    catalog = catalog or Catalog()
    authenticate = authenticator or verify_password
    hardware = hardware_provider or snapshot
    runtimes = Runtimes(cfg, hardware) if supplied_docker is None else None
    docker = supplied_docker or runtimes.client("cpu", register=True)

    def runtime_status():
        """Read all isolated slots or the injected single-runtime test adapter."""
        return runtimes.status() if runtimes else docker.status()

    sessions = Sessions(cfg.session_seconds)
    jobs = Jobs(store, cfg.ollama_num_parallel)

    def sample_once():
        """Persist one host/runtime observation while containing expected probe failures."""
        data = {"hardware": hardware()}
        try:
            data["ollama"] = runtime_status()
        except Exception as exc:
            data["ollama"] = {"running": False, "error": diagnostic(exc)}
        store.sample(data, cfg.retention_days)

    async def sampler():
        """Collect periodic observations until application shutdown requests cancellation."""
        while True:
            try:
                await run_in_threadpool(sample_once)
            except Exception:
                log.exception("Resource sampling failed")
            await asyncio.sleep(cfg.sample_seconds)

    @asynccontextmanager
    async def lifespan(app):
        """Start optional telemetry and stop the sampler/executor during application shutdown."""
        task = asyncio.create_task(sampler()) if sampling else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await run_in_threadpool(lambda: jobs.pool.shutdown(wait=True))
        await run_in_threadpool(close_relays)

    app = FastAPI(
        title="Llameros", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store, app.state.jobs = store, jobs
    app.state.users = users
    app.add_middleware(BodyLimits, inference_bytes=cfg.inference_body_bytes)

    @app.middleware("http")
    async def security(request: Request, call_next):
        """Require HTTPS and same-origin CSRF protection for dashboard writes, then add security headers."""
        if request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS is required"}, status_code=400)
        gateway = request.url.path == PREFIX or request.url.path.startswith(PREFIX + "/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not gateway:
            origin = request.headers.get("origin")
            if (
                not origin
                or urlsplit(origin).netloc != request.headers.get("host")
                or urlsplit(origin).scheme != "https"
            ):
                return JSONResponse(
                    {"detail": "Same-origin HTTPS requests are required"}, status_code=403
                )
            try:
                if int(request.headers.get("content-length", "0")) > 131072:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            if request.url.path.startswith("/api/") and request.url.path != "/api/login":
                session = sessions.get(request.cookies.get(COOKIE, ""))
                csrf = request.headers.get("x-csrf-token", "")
                if not session or not secrets.compare_digest(session["csrf"], csrf):
                    return JSONResponse(
                        {"detail": "Invalid session or CSRF token"}, status_code=403
                    )
        activity_started = time.monotonic()
        response = await call_next(request)
        if (
            gateway
            and response.status_code >= 400
            and not getattr(request.state, "activity_recorded", False)
        ):
            # Early validation/auth/queue failures otherwise bypass gateway finalization.
            # Never record request bodies or bearer credentials.
            app.state.api_keys.record(
                getattr(request.state, "activity_key", ""),
                "",
                "",
                f"HTTP {response.status_code}",
                {},
                time.monotonic() - activity_started,
            )
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    @app.exception_handler(DockerError)
    async def docker_error(request, exc):
        """Map managed-runtime failures to a dashboard service-unavailable response."""
        return JSONResponse({"detail": diagnostic(exc)}, status_code=503)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        """Translate catalogue/Ollama HTTP failures to a bad-gateway response."""
        return JSONResponse({"detail": diagnostic(exc)}, status_code=502)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        """Expose validation failures as dashboard HTTP 400 responses."""
        return JSONResponse({"detail": diagnostic(exc)}, status_code=400)

    def require_session(request: Request):
        """Resolve the cookie and current account policy; reject expired or blocked sessions."""
        session = sessions.get(request.cookies.get(COOKIE, ""))
        account = users.get(session["username"]) if session else None
        if not session or not users.active(session["username"]):
            if session:
                sessions.remove(request.cookies.get(COOKIE, ""))
            raise HTTPException(401, "Sign in with an allowed Linux account")
        return {**session, "role": account["role"]}

    def require_admin(session=Depends(require_session)):
        """Require a current admin role independently of client-side board visibility."""
        if session["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return session

    protected = [Depends(require_admin)]
    signed_in = [Depends(require_session)]

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        """Rate-limit PAM login, remember account policy, and issue a secure session cookie and CSRF token."""
        source = request.client.host if request.client else "unknown"
        if not sessions.allow_attempt(source, body.username) or not sessions.pam_slots.acquire(
            blocking=False
        ):
            store.security_event(body.username, "login", source, "throttled")
            raise HTTPException(
                429, "Too many login attempts; retry later", headers={"Retry-After": "60"}
            )
        try:
            valid = (
                not users.get(body.username)["blocked"]
                and store.account_check(body.username)
                and authenticate(body.username, body.password, cfg.pam_service)
            )
        finally:
            sessions.pam_slots.release()
        if not valid:
            store.security_event(body.username, "login", source, "denied")
            raise HTTPException(401, "Linux authentication failed")
        sessions.remove(request.cookies.get(COOKIE, ""))
        users.remember(body.username)
        token, session = sessions.create(body.username)
        store.security_event(body.username, "login", source)
        response.set_cookie(
            COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=cfg.session_seconds,
            path="/",
        )
        return {
            "username": body.username,
            "csrf": session["csrf"],
            "role": users.get(body.username)["role"],
            "certificate_download_enabled": cfg.certificate_download_enabled,
        }

    @app.get("/api/session")
    def session_info(session=Depends(require_session)):
        """Return the signed-in identity, current role, and session CSRF token."""
        return {
            "username": session["username"],
            "csrf": session["csrf"],
            "role": session["role"],
            "certificate_download_enabled": cfg.certificate_download_enabled,
        }

    @app.get("/api/certificate", dependencies=signed_in)
    def download_certificate():
        """Offer the public certificate chain to signed-in users when explicitly enabled."""
        if not cfg.certificate_download_enabled:
            raise HTTPException(404, "Certificate download disabled")
        try:
            content = public_certificate(cfg)
        except (OSError, ValueError):
            raise HTTPException(503, "Public certificate unavailable") from None
        return Response(
            content,
            media_type="application/x-pem-file",
            headers={
                "Content-Disposition": 'attachment; filename="llameros-cert.pem"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/logout", dependencies=signed_in)
    def logout(request: Request, response: Response, session=Depends(require_session)):
        """Invalidate the server session and remove its secure browser cookie."""
        sessions.remove(request.cookies.get(COOKIE, ""))
        store.security_event(session["username"], "logout")
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/users", dependencies=protected)
    def list_users():
        """Expose discovered and remembered account policy to administrators only."""
        return {
            "users": [
                {**user, "limits": app.state.api_keys.limits.snapshot("user", user["username"])}
                for user in users.list()
            ]
        }

    @app.get("/api/security-events", dependencies=protected)
    def security_events():
        return {"events": store.security_history()}

    @app.post("/api/users/{username}", dependencies=protected)
    def update_user(username: str, body: UserUpdate, session=Depends(require_admin)):
        """Apply an admin role/block change and invalidate all sessions for a blocked account."""
        result = users.update(username, body, session["username"])
        if body.blocked:
            sessions.remove_user(username)
        return result

    @app.get("/api/user/dashboard", dependencies=signed_in)
    def user_dashboard(session=Depends(require_session)):
        """Return running-model allowlisted fields, overall model totals, and owner-only key usage.

        Include CPU/RAM samples but omit activity logs, direct runtime URLs, and other users'
        key metadata.
        """
        try:
            runtime = runtime_status()
        except (DockerError, httpx.HTTPError):
            runtime = {"running": False, "loaded": []}
        # Never expose direct unauthenticated runtime URLs or administrative data.
        models = (
            [
                {
                    field: model[field]
                    for field in ("name", "model", "target", "hardware")
                    if field in model
                }
                for model in runtime.get("loaded", [])
            ]
            if runtime.get("running")
            else []
        )
        history = store.history()
        own_keys = app.state.api_keys.list(session["username"])
        return {
            "models": models,
            "keys": own_keys,
            "context_length": cfg.context_length,
            "history": {
                "benchmarks": history["benchmarks"],
                "models": app.state.api_keys.limits.models(history["models"], session["username"]),
                "samples": [
                    {
                        "timestamp": sample["timestamp"],
                        "hardware": {
                            "gpus": [
                                {
                                    "id": hashlib.sha256(
                                        str(gpu.get("id", index)).encode()
                                    ).hexdigest()[:16],
                                    "utilization": gpu.get("utilization"),
                                }
                                for index, gpu in enumerate(
                                    sample.get("hardware", {}).get("gpus", [])
                                )
                            ],
                            **{
                                field: sample.get("hardware", {}).get(field, 0)
                                for field in ("cpu_percent", "memory_percent")
                            },
                        },
                    }
                    for sample in history["samples"]
                ],
            },
        }

    @app.get("/api/status", dependencies=protected)
    def status():
        """Return a dashboard status snapshot without exposing mutable internal job state."""
        try:
            runtime = runtime_status()
        except (DockerError, httpx.HTTPError) as exc:
            runtime = {"running": False, "error": diagnostic(exc)}
        return {
            "hardware": hardware(),
            "ollama": runtime,
            "job": jobs.status(),
            "user": current_user(),
            "gpu_mode": cfg.gpu,
            "context_length": cfg.context_length,
            "access": {"base_url": "/inference", "openai_url": "/inference/v1"},
        }

    @app.get("/api/catalog", dependencies=protected)
    def list_catalog(q: str = ""):
        """List public model families for the admin catalogue, bounding the search query."""
        return {"models": catalog.list(q[:100])}

    @app.get("/api/catalog/{family}/tags", dependencies=protected)
    def tags(family: str):
        """List selectable official tags for the requested model family."""
        return {"tags": catalog.tags(family)}

    @app.get("/api/model-info", dependencies=protected)
    def model_info(model: str):
        """Return sanitized readable catalogue information for a validated model name."""
        return catalog.description(validate_model(model))

    @app.get("/api/catalog/{family}/compatibility", dependencies=protected)
    def compatibility(family: str, cpu_only: bool = False):
        """Return family variants and an aggregate advisory fit for the selected CPU/GPU policy."""
        variants = catalog.variants(
            family,
            hardware(),
            "cpu" if cpu_only else cfg.gpu,
            cfg.ollama_num_parallel,
            cfg.context_length,
        )
        if cpu_only:
            for variant in variants:
                variant["fits"] = variant["cpu_fits"]
        return {
            "family": family,
            "cpu_only": cpu_only,
            "variants": variants,
            "fits": True
            if any(item["fits"] is True for item in variants)
            else None
            if any(item["fits"] is None for item in variants) or not variants
            else False,
        }

    @app.get("/api/fit", dependencies=protected)
    def fit(model: str, cpu_only: bool = False):
        """Estimate current memory fit from a model manifest and host hardware sample."""
        size = catalog.size(validate_model(model))
        return {
            "model": model,
            "download_bytes": size,
            **assess_fit(
                size,
                hardware(),
                "cpu" if cpu_only else cfg.gpu,
                cfg.ollama_num_parallel,
                cfg.context_length,
            ),
        }

    @app.get("/api/activity", dependencies=protected)
    def activity():
        return store.activity()

    @app.post("/api/activity/acknowledge", dependencies=protected)
    def acknowledge_activity(body: ActivityAcknowledgement):
        store.acknowledge_activity(body.failures)
        return {"acknowledged": True}

    @app.get("/api/history", dependencies=protected)
    def history(session=Depends(require_admin)):
        """Return full admin telemetry history and overall model-generation aggregates."""
        data = store.history()
        data["models"] = app.state.api_keys.limits.models(data["models"], session["username"])
        return data

    @app.post("/api/action-errors", dependencies=protected)
    def action_error(body: ActionError):
        """Persist bounded dashboard failure metadata without prompt or credential content."""
        store.event("dashboard-action:failed", "dashboard", "Client action failed")
        return {"ok": True}

    @app.get("/api/storage", dependencies=protected)
    def storage_info():
        """Describe the active model storage location for the admin configuration panel."""
        return docker.storage_info()

    @app.post("/api/storage", dependencies=protected, status_code=202)
    def configure_storage(body: StorageRequest):
        """Queue confirmed storage reconfiguration under lifecycle scheduling protection."""

        def work(progress):
            """Execute the enclosing lifecycle operation in the worker and publish progress/results."""
            progress({"status": "Validating storage directory and reconfiguring stopped container"})
            return (
                runtimes.configure_storage(body.directory, body.confirmed)
                if runtimes
                else docker.configure_storage(body.directory, body.confirmed)
            )

        return jobs.submit(
            "storage-change",
            "",
            work,
        )

    @app.get("/api/models", dependencies=protected)
    def downloaded_models():
        """Prefer live inventory, falling back to disk/cache with explicit provenance and warnings."""
        try:
            runtime = docker.status()
            if runtime["running"]:
                return {
                    "models": runtime["models"],
                    "source": "live",
                    "cached_at": None,
                    "warnings": [],
                    "storage": docker.storage_info(),
                }
        except (DockerError, httpx.HTTPError) as exc:
            error = diagnostic(exc)
        else:
            error = ""
        try:
            inventory = docker.inventory()
        except (OSError, ValueError) as exc:
            inventory = {
                "models": [],
                "source": "unavailable",
                "cached_at": None,
                "warnings": [diagnostic(exc)],
            }
        if error:
            inventory["warnings"].append(error)
        return {**inventory, "storage": docker.storage_info()}

    @app.get("/api/job", dependencies=protected)
    def job():
        """Return the current lifecycle/inference progress snapshot for admin polling."""
        return jobs.status()

    @app.post("/api/container/{action}", dependencies=protected, status_code=202)
    def container(action: str):
        """Queue an allowed start/stop action after rejecting an already-matching runtime state."""
        if action not in {"start", "stop"}:
            raise HTTPException(404, "Unknown container action")

        def work(progress):
            """Execute the enclosing lifecycle operation in the worker and publish progress/results."""
            running = runtime_status()["running"]
            if running == (action == "start"):
                raise ValueError(
                    "Ollama is already running" if running else "Ollama is already stopped"
                )
            if action == "start":
                return docker.start(progress=progress)
            progress({"status": "Stopping Ollama container (up to 20 seconds grace period)"})
            return runtimes.stop() if runtimes else docker.stop()

        return jobs.submit(f"container-{action}", "", work)

    def installed(name: str) -> dict:
        """Resolve a downloaded model from the running inventory or raise a validation error."""
        validate_model(name)
        docker.ensure_running()
        models = docker.api("GET", "/api/tags").get("models", [])
        normalized = name if ":" in name else name + ":latest"
        for model in models:
            if model.get("name") == normalized or model.get("model") == normalized:
                return model
        raise ValueError("Download this model before loading or running it")

    def check_fit(size):
        """Refuse lifecycle admission when the conservative current-memory estimate does not fit."""
        if not assess_fit(size, hardware(), cfg.gpu, cfg.ollama_num_parallel, cfg.context_length)[
            "fits"
        ]:
            raise ValueError(
                "Insufficient currently available memory for the conservative fit estimate"
            )

    def is_loaded(name: str) -> bool:
        """Check whether a normalized model is resident in the single-runtime adapter."""
        normalized = name if ":" in name else name + ":latest"
        return any(
            model.get("name", model.get("model")) == normalized
            for model in docker.api("GET", "/api/ps").get("models", [])
        )

    @app.post("/api/models/{action}", dependencies=protected, status_code=202)
    def model_action(action: str, body: ModelRequest):
        """Queue validated pull/load/unload work with the appropriate device reservation."""
        name = validate_model(body.model)
        if action not in {"pull", "load", "unload"}:
            raise HTTPException(404, "Unknown model action")

        def work(progress):
            """Execute the enclosing lifecycle operation in the worker and publish progress/results."""
            if action == "pull":
                progress({"status": "Checking model registry and available memory"})
                check_fit(catalog.size(name))
                docker.pull(name, progress)
                return {"message": "Model downloaded"}
            if runtimes and action == "unload":
                client = runtimes.model_client(name, body.target)
                progress({"status": "Unloading model from selected hardware"})
                return client.api(
                    "POST", "/api/generate", {"model": name, "stream": False, "keep_alive": 0}
                )
            info = installed(name)
            if runtimes and action == "load":
                return runtimes.load(
                    name, info["size"], body.target or "cpu", cfg.context_length, progress
                )
            active = is_loaded(name)
            if active == (action == "load"):
                raise ValueError(
                    "Model is already loaded" if active else "Model is already unloaded"
                )
            if action == "load":
                if docker.api("GET", "/api/ps").get("models", []):
                    raise ValueError(
                        "Only one model may be loaded. Unload the running model first."
                    )
                check_fit(info["size"])
            progress(
                {
                    "status": "Loading model into memory"
                    if action == "load"
                    else "Unloading model from memory"
                }
            )
            return docker.api(
                "POST",
                "/api/generate",
                {
                    "model": name,
                    "stream": False,
                    "keep_alive": 0 if action == "unload" else -1,
                    "options": {"num_ctx": cfg.context_length},
                },
            )

        return jobs.submit(
            action,
            name,
            work,
            targets={body.target} if body.target and action in {"load", "unload"} else None,
        )

    @app.post("/api/benchmark", dependencies=protected, status_code=202)
    def benchmark(body: ModelRequest):
        """Reserve all devices for a bounded admin benchmark of one resident instance."""
        name = validate_model(body.model)
        client = runtimes.model_client(name, body.target) if runtimes else docker
        if not runtimes and not is_loaded(name):
            raise HTTPException(409, "Load the model before benchmarking")
        target = client.target["id"] if runtimes else body.target or "cpu"

        def work(progress):
            result = asyncio.run(
                measure(client, name, target, cfg.context_length, cfg.ollama_num_parallel, progress)
            )
            store.save_benchmark(result)
            return result

        return jobs.submit("benchmark", name, work)

    @app.post("/api/generate", dependencies=protected, status_code=202)
    def generate(body: GenerateRequest, session=Depends(require_admin)):
        """Queue bounded admin playground inference against an already-loaded model and record totals."""
        name = validate_model(body.model)
        quota_scopes = [
            ("user", session["username"]),
            app.state.api_keys.limits.model_scope(session["username"], name),
        ]
        app.state.api_keys.limits.check(quota_scopes)

        def work(progress):
            """Execute the enclosing lifecycle operation in the worker and publish progress/results."""
            if not runtimes:
                installed(name)
            client = runtimes.model_client(name, body.target) if runtimes else docker
            if not runtimes and not is_loaded(name):
                raise ValueError("Load the model before using the inference playground")
            progress(
                {
                    "status": "Generating response; duration and token counts will be available on completion"
                }
            )
            options = {"num_ctx": cfg.context_length, "num_predict": body.max_tokens}
            if runtimes and client.target["id"] == "cpu":
                options.update(num_gpu=0)
            started = app.state.api_keys.limits.check(quota_scopes)
            result = {}
            try:
                result = client.api(
                    "POST",
                    "/api/generate",
                    {
                        "model": name,
                        "prompt": body.prompt,
                        "stream": False,
                        "options": options,
                        "keep_alive": -1,
                    },
                )
            finally:
                app.state.api_keys.limits.charge(
                    quota_scopes,
                    {
                        "prompt": result.get("prompt_eval_count"),
                        "output": result.get("eval_count"),
                    },
                    started,
                )
            store.generation(result)
            return {
                key: result.get(key)
                for key in (
                    "model",
                    "response",
                    "thinking",
                    "eval_count",
                    "eval_duration",
                    "total_duration",
                )
            }

        return jobs.submit("generate", name, work)

    install_gateway(
        app,
        cfg,
        store,
        jobs,
        signed_in,
        runtime_status,
        runtimes,
        docker,
        gateway_transport,
        require_session=require_session,
    )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        """Serve the public sign-in/dashboard shell; API dependencies enforce actual access."""
        return FileResponse(static / "index.html")

    return app
