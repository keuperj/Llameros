import asyncio
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

from .auth import COOKIE, Sessions, verify_password
from .catalog import Catalog, validate_model
from .config import Settings, current_user
from .docker import Docker, DockerError
from .hardware import assess_fit, snapshot
from .storage import Store

log = logging.getLogger(__name__)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=210)


class GenerateRequest(ModelRequest):
    prompt: str = Field(min_length=1, max_length=32000)
    max_tokens: int = Field(default=256, ge=1, le=4096)


class StorageRequest(BaseModel):
    directory: str = Field(default="", max_length=4096)
    confirmed: bool = False


class Jobs:
    def __init__(self, store: Store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llameros-job")
        self.lock = threading.Lock()
        self.job = {"state": "idle"}

    def status(self):
        with self.lock:
            return dict(self.job)

    def submit(self, action, model, function):
        with self.lock:
            if self.job["state"] == "running":
                raise HTTPException(409, "Another lifecycle or inference job is running")
            self.job = {
                "state": "running",
                "action": action,
                "model": model,
                "started": time.time(),
            }

        def progress(value):
            with self.lock:
                self.job["progress"] = value

        def work():
            try:
                self.store.event(f"{action}:started", model)
                result = function(progress)
                self.store.event(f"{action}:completed", model)
                with self.lock:
                    self.job.update(state="completed", result=result)
            except Exception as exc:
                log.warning("Job %s failed: %s", action, exc)
                with self.lock:
                    self.job.update(state="failed", error=str(exc)[:2000])
                self.store.event(f"{action}:failed", model, str(exc)[:2000])

        self.pool.submit(work)
        return {"accepted": True}


def create_app(
    settings: Settings | None = None,
    *,
    docker=None,
    catalog=None,
    authenticator=None,
    hardware_provider=None,
    sampling=True,
) -> FastAPI:
    cfg = settings or Settings()
    cfg.prepare()
    store = Store(cfg.state_dir / "history.sqlite3")
    docker = docker or Docker(cfg)
    catalog = catalog or Catalog()
    authenticate = authenticator or verify_password
    hardware = hardware_provider or snapshot
    sessions = Sessions(cfg.session_seconds)
    jobs = Jobs(store)

    def sample_once():
        data = {"hardware": hardware()}
        try:
            data["ollama"] = docker.status()
        except Exception as exc:
            data["ollama"] = {"running": False, "error": str(exc)[:2000]}
        store.sample(data, cfg.retention_days)

    async def sampler():
        while True:
            try:
                await run_in_threadpool(sample_once)
            except Exception:
                log.exception("Resource sampling failed")
            await asyncio.sleep(cfg.sample_seconds)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(sampler()) if sampling else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await run_in_threadpool(lambda: jobs.pool.shutdown(wait=True))

    app = FastAPI(
        title="Llameros", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store, app.state.jobs = store, jobs

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS is required"}, status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
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
        response = await call_next(request)
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
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        return JSONResponse({"detail": f"Ollama/catalogue request failed: {exc}"}, status_code=502)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def require_session(request: Request):
        session = sessions.get(request.cookies.get(COOKIE, ""))
        if not session:
            raise HTTPException(401, "Sign in with the Linux account running Llameros")
        return session

    protected = [Depends(require_session)]

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if not sessions.allow_attempt():
            raise HTTPException(429, "Too many login attempts; wait one minute")
        if not authenticate(body.username, body.password, cfg.pam_service):
            raise HTTPException(401, "Linux authentication failed")
        sessions.remove(request.cookies.get(COOKIE, ""))
        token, session = sessions.create(body.username)
        response.set_cookie(
            COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=cfg.session_seconds,
            path="/",
        )
        return {"username": session["username"], "csrf": session["csrf"]}

    @app.get("/api/session")
    def session_info(session=Depends(require_session)):
        return {"username": session["username"], "csrf": session["csrf"]}

    @app.post("/api/logout", dependencies=protected)
    def logout(request: Request, response: Response):
        sessions.remove(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/status", dependencies=protected)
    def status():
        try:
            runtime = docker.status()
        except (DockerError, httpx.HTTPError) as exc:
            runtime = {"running": False, "error": str(exc)}
        return {
            "hardware": hardware(),
            "ollama": runtime,
            "job": jobs.status(),
            "user": current_user(),
            "gpu_mode": cfg.gpu,
            "context_length": cfg.context_length,
        }

    @app.get("/api/catalog", dependencies=protected)
    def list_catalog(q: str = ""):
        return {"models": catalog.list(q[:100])}

    @app.get("/api/catalog/{family}/tags", dependencies=protected)
    def tags(family: str):
        return {"tags": catalog.tags(family)}

    @app.get("/api/fit", dependencies=protected)
    def fit(model: str):
        size = catalog.size(validate_model(model))
        return {"model": model, "download_bytes": size, **assess_fit(size, hardware(), cfg.gpu)}

    @app.get("/api/history", dependencies=protected)
    def history():
        return store.history()

    @app.get("/api/storage", dependencies=protected)
    def storage_info():
        return docker.storage_info()

    @app.post("/api/storage", dependencies=protected, status_code=202)
    def configure_storage(body: StorageRequest):
        return jobs.submit(
            "storage-change",
            "",
            lambda progress: docker.configure_storage(body.directory, body.confirmed),
        )

    @app.get("/api/models", dependencies=protected)
    def downloaded_models():
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
            error = str(exc)
        else:
            error = ""
        try:
            inventory = docker.inventory()
        except (OSError, ValueError) as exc:
            inventory = {
                "models": [],
                "source": "unavailable",
                "cached_at": None,
                "warnings": [str(exc)],
            }
        if error:
            inventory["warnings"].append(error)
        return {**inventory, "storage": docker.storage_info()}

    @app.get("/api/job", dependencies=protected)
    def job():
        return jobs.status()

    @app.post("/api/container/{action}", dependencies=protected, status_code=202)
    def container(action: str):
        if action not in {"start", "stop"}:
            raise HTTPException(404, "Unknown container action")
        return jobs.submit(f"container-{action}", "", lambda progress: getattr(docker, action)())

    def installed(name: str) -> dict:
        validate_model(name)
        docker.ensure_running()
        models = docker.api("GET", "/api/tags").get("models", [])
        normalized = name if ":" in name else name + ":latest"
        for model in models:
            if model.get("name") == normalized or model.get("model") == normalized:
                return model
        raise ValueError("Download this model before loading or running it")

    def check_fit(size):
        if not assess_fit(size, hardware(), cfg.gpu)["fits"]:
            raise ValueError(
                "Insufficient currently available memory for the conservative fit estimate"
            )

    @app.post("/api/models/{action}", dependencies=protected, status_code=202)
    def model_action(action: str, body: ModelRequest):
        name = validate_model(body.model)
        if action not in {"pull", "load", "unload"}:
            raise HTTPException(404, "Unknown model action")

        def work(progress):
            if action == "pull":
                check_fit(catalog.size(name))
                docker.pull(name, progress)
                return {"message": "Model downloaded"}
            info = installed(name)
            if action == "load":
                check_fit(info["size"])
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

        return jobs.submit(action, name, work)

    @app.post("/api/generate", dependencies=protected, status_code=202)
    def generate(body: GenerateRequest):
        name = validate_model(body.model)

        def work(progress):
            info = installed(name)
            # Loaded weights already occupy RAM; do not double-count them.
            loaded = docker.api("GET", "/api/ps").get("models", [])
            normalized = name if ":" in name else name + ":latest"
            if not any(model.get("name") == normalized for model in loaded):
                check_fit(info["size"])
            result = docker.api(
                "POST",
                "/api/generate",
                {
                    "model": name,
                    "prompt": body.prompt,
                    "stream": False,
                    "options": {"num_ctx": cfg.context_length, "num_predict": body.max_tokens},
                    "keep_alive": "5m",
                },
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

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
