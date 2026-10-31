"""Authenticated Ollama/OpenAI/Anthropic inference routes for Llameros model sharing.

Allowlisted inference proxy. Never forwards credentials or lifecycle commands.
"""

import asyncio
import base64
import json
import math
import struct
import time
from datetime import datetime

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .api_keys import ApiKeys, KeyRequest, normalize
from .docker import DockerError
from .limits import LimitConfig
from .multimodal import reject_documents, validate_images
from .runtime_transport import RUNTIME_BASE_URL

PREFIX = "/inference"


class InferenceStream(StreamingResponse):
    """Release the inference lock even if disconnect precedes generator iteration."""

    def __init__(self, *args, cleanup, **kwargs):
        """Attach an idempotent cleanup callback to the streaming response."""
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        """Run the ASGI stream and shield final cleanup even if the client disconnects early."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(asyncio.create_task(self.cleanup()))


def install_gateway(
    app,
    cfg,
    store,
    jobs,
    protected,
    runtime_status,
    runtimes,
    docker,
    transport=None,
    *,
    require_session,
):
    """Install key-management and inference routes with shared authorization/runtime services.

    Dashboard key routes use the supplied session dependency and owner scoping; inference uses
    bearer keys, bounded admission, and per-device scheduling.
    """
    keys = ApiKeys(store)
    app.state.api_keys = keys

    def limit_subject(scope, subject, request, session):
        """Authorize every read/write; never trust UI board or supplied key ownership."""
        if scope == "key":
            if not any(
                key["id"] == subject and not key["deleted"]
                for key in keys.list(key_owner(request, session))
            ):
                raise HTTPException(404, "API key not found")
        elif scope in {"user", "model", "user-default"}:
            if session["role"] != "admin":
                raise HTTPException(403, "Administrator required")
            if scope == "user-default" and subject != "*":
                raise HTTPException(400, "Default user limits require subject *")
            if scope == "user" and subject not in {user["username"] for user in keys.users.list()}:
                raise HTTPException(404, "System user not found")
            if scope == "model":
                try:
                    subject = normalize(subject)
                except ValueError:
                    raise HTTPException(400, "Invalid model") from None
        else:
            raise HTTPException(404, "Unknown limit scope")
        return subject

    @app.get("/api/limits/{scope}", dependencies=protected)
    def get_limits(scope: str, subject: str, request: Request, session=Depends(require_session)):
        subject = limit_subject(scope, subject, request, session)
        return keys.limits.snapshot(scope, subject)

    @app.post("/api/limits/{scope}", dependencies=protected)
    def set_limits(
        scope: str,
        subject: str,
        body: LimitConfig,
        request: Request,
        session=Depends(require_session),
    ):
        subject = limit_subject(scope, subject, request, session)
        return keys.limits.set(scope, subject, body, session["username"])

    @app.delete("/api/limits/user", dependencies=protected)
    def inherit_user_limits(subject: str, request: Request, session=Depends(require_session)):
        """Restore an administrator-selected user's inheritance without resetting usage."""
        subject = limit_subject("user", subject, request, session)
        return keys.limits.inherit_user(subject, session["username"])

    async def api_error(request, exc):
        """Render HTTP errors in dashboard, Ollama, OpenAI, or Anthropic-compatible envelopes."""
        if not request.url.path.startswith(PREFIX + "/"):
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
        if "/v1/" in request.url.path:
            kind = {
                401: "authentication_error",
                403: "permission_error",
                404: "not_found_error",
                429: "rate_limit_error",
            }.get(
                exc.status_code, "invalid_request_error" if exc.status_code < 500 else "api_error"
            )
            content = {"error": {"message": exc.detail, "type": kind}}
            if request.url.path.endswith("/v1/messages"):
                content["type"] = "error"
        else:
            content = {"error": exc.detail}
        return JSONResponse(content, status_code=exc.status_code, headers=exc.headers)

    app.add_exception_handler(HTTPException, api_error)

    def key_owner(request, session):
        """Return the enforced owner for users/user-board views, or None for unrestricted admins."""
        return (
            session["username"]
            if session["role"] != "admin" or request.query_params.get("board") == "user"
            else None
        )

    @app.get("/api/keys", dependencies=protected)
    def list_keys(request: Request, session=Depends(require_session)):
        """List nondeleted key metadata within the caller's permitted owner scope."""
        return {
            "keys": [key for key in keys.list(key_owner(request, session)) if not key["deleted"]]
        }

    @app.get("/api/keys/usage", dependencies=protected)
    def key_usage(request: Request, session=Depends(require_session)):
        """List scoped key usage including deleted-key tombstones, never stored digests."""
        return {"keys": keys.list(key_owner(request, session))}

    @app.delete("/api/keys/{identity}", dependencies=protected)
    def delete_key(identity: str, request: Request, session=Depends(require_session)):
        """Revoke and tombstone a key only within the caller's permitted owner scope."""
        keys.delete(identity, key_owner(request, session), actor=session["username"])
        return {"ok": True}

    @app.post("/api/keys", dependencies=protected, status_code=201)
    def create_key(body: KeyRequest, request: Request, session=Depends(require_session)):
        """Authorize the requested Linux owner and return a newly generated one-time key secret."""
        owner = body.user or session["username"]
        if key_owner(request, session) and owner != session["username"]:
            raise HTTPException(403, "You may only create your own keys")
        if (
            owner not in {user["username"] for user in keys.users.list()}
            and owner != session["username"]
        ):
            raise HTTPException(404, "System user not found")
        return keys.create(body, owner, actor=session["username"])

    @app.post("/api/keys/{identity}/revoke", dependencies=protected)
    def revoke_key(identity: str, request: Request, session=Depends(require_session)):
        """Disable a key within the caller's scope while retaining its usage history."""
        keys.revoke(identity, key_owner(request, session), actor=session["username"])
        return {"ok": True}

    def bearer(request: Request):
        """Authenticate a bearer header, also allowing x-api-key on Anthropic Messages requests."""
        state = jobs.status()
        if state["state"] == "running" and state.get("action") == "benchmark":
            raise HTTPException(
                503, "Temporarily unavailable due to benchmark", headers={"Retry-After": "5"}
            )
        authorization = request.headers.get("authorization", "")
        if not authorization and request.url.path.endswith("/v1/messages"):
            authorization = "Bearer " + request.headers.get("x-api-key", "")
        identity = keys.authenticate(authorization)
        request.state.activity_key = identity["id"]
        return identity

    async def read_body(request):
        """Read bounded streamed JSON and require an object before protocol-specific validation."""
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > cfg.inference_body_bytes:
                raise HTTPException(413, "Request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, "Valid JSON object required") from None

    def permitted_models(key):
        """Filter loaded model/device instances by key permissions and runtime availability."""
        state = runtime_status()
        return [
            {**model, "target": model.get("target", "cpu")}
            for model in state.get("loaded", [])
            if keys.allowed(key, model.get("name", model.get("model")), model.get("target", "cpu"))
        ]

    @app.get(PREFIX + "/api/tags")
    @app.get(PREFIX + "/api/ps")
    @app.get(PREFIX + "/v1/models")
    def models(request: Request, key=Depends(bearer)):
        """Return permission-filtered discovery in the requested Ollama or OpenAI representation."""
        loaded = permitted_models(key)

        def created(model):
            """Convert optional model timestamps into Unix seconds, tolerating malformed metadata."""
            try:
                return int(
                    datetime.fromisoformat(
                        model.get("modified_at", "").replace("Z", "+00:00")
                    ).timestamp()
                )
            except (ValueError, TypeError, AttributeError):
                return 0

        if request.url.path.endswith("/v1/models"):
            return {
                "object": "list",
                "data": [
                    {
                        "id": normalize(m.get("name", m.get("model"))),
                        "object": "model",
                        "created": created(m),
                        "owned_by": normalize(m.get("name", m.get("model"))).split("/")[0]
                        if "/" in normalize(m.get("name", m.get("model")))
                        else "library",
                        "target": m["target"],
                    }
                    for m in loaded
                ],
            }
        return {
            "models": [
                {k: v for k, v in model.items() if k not in {"base_url", "openai_url"}}
                for model in loaded
            ]
        }

    async def metadata(request, key, model, target, path, payload=None):
        """Resolve one permitted loaded instance and fetch validated metadata without forwarding credentials."""
        try:
            model = normalize(model)
        except (ValueError, TypeError):
            raise HTTPException(400, "Valid model required") from None
        matches = [
            m
            for m in await run_in_threadpool(permitted_models, key)
            if normalize(m.get("name", m.get("model"))) == model
            and (not target or m["target"] == target)
        ]
        if len(matches) != 1:
            raise HTTPException(
                404 if not matches else 409, "Choose one permitted loaded model and target"
            )
        runtime = (
            await run_in_threadpool(runtimes.model_client, model, matches[0]["target"])
            if runtimes
            else docker
        )
        async with httpx.AsyncClient(
            base_url=RUNTIME_BASE_URL,
            transport=transport
            or httpx.AsyncHTTPTransport(uds=await run_in_threadpool(runtime.socket_path)),
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        ) as client:
            try:
                response = await client.request(
                    "POST" if payload is not None else "GET", path, json=payload
                )
                if response.status_code != 200:
                    raise HTTPException(502, "Runtime metadata unavailable")
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError()
                return data
            except (httpx.HTTPError, ValueError):
                raise HTTPException(502, "Invalid runtime metadata") from None

    @app.post(PREFIX + "/api/show")
    async def show_model(request: Request, key=Depends(bearer)):
        """Validate an Ollama metadata request and route it to one permitted loaded runtime."""
        body = await read_body(request)
        from .protocol import validate_protocol

        validate_protocol(body)
        if (
            set(body) - {"model", "verbose", "target"}
            or type(body.get("verbose", False)) is not bool
        ):
            raise HTTPException(400, "Invalid model metadata request")
        return await metadata(
            request,
            key,
            body.get("model"),
            body.get("target", request.headers.get("x-llameros-target", "")),
            "/api/show",
            {"model": body.get("model"), "verbose": body.get("verbose", False)},
        )

    @app.get(PREFIX + "/v1/models/{model:path}")
    async def model_detail(model: str, request: Request, key=Depends(bearer)):
        """Fetch one permitted loaded model's OpenAI metadata using an optional target header."""
        return await metadata(
            request, key, model, request.headers.get("x-llameros-target", ""), "/v1/models/" + model
        )

    @app.get(PREFIX + "/api/version")
    async def version(request: Request, key=Depends(bearer)):
        """Read the selected permitted runtime version without exposing administrative discovery."""
        loaded = await run_in_threadpool(permitted_models, key)
        if not loaded:
            raise HTTPException(404, "No permitted loaded runtime")
        selected = next(
            (
                m
                for m in loaded
                if m["target"] == request.headers.get("x-llameros-target", loaded[0]["target"])
            ),
            None,
        )
        if selected is None:
            raise HTTPException(404, "No permitted loaded runtime")
        return await metadata(
            request,
            key,
            selected.get("name", selected.get("model")),
            selected["target"],
            "/api/version",
        )

    async def infer(request: Request, key=Depends(bearer)):
        """Validate and route a supported inference request to exactly one permitted loaded instance.

        Apply protocol/token/media restrictions before admission, recheck revocation after
        queueing, and account/clean up all completion, failure, and disconnect paths.
        """
        body = await read_body(request)
        original_size = len(json.dumps(body).encode())
        try:
            model = normalize(body.get("model", ""))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Valid JSON object and official model name required") from None
        path = request.url.path.removeprefix(PREFIX)
        responses = path == "/v1/responses"
        anthropic = path == "/v1/messages"
        completion = path == "/v1/completions"
        openai = path.startswith("/v1/") and not anthropic
        embedding = path in {"/api/embed", "/v1/embeddings", "/api/embeddings"}
        legacy_embedding = path == "/api/embeddings"
        if legacy_embedding:
            if "input" in body or not isinstance(body.get("prompt"), str):
                raise HTTPException(400, "Legacy embeddings require prompt")
            body["input"] = body.pop("prompt", None)
        encoding = body.get("encoding_format", "float")
        if embedding:
            allowed = {
                "model",
                "input",
                "target",
                "keep_alive",
                "truncate",
                "dimensions",
                "options",
            }
            if path == "/v1/embeddings":
                allowed |= {"encoding_format", "user"}
                if not isinstance(encoding, str) or encoding not in {"float", "base64"}:
                    raise HTTPException(400, "encoding_format must be float or base64")
                allowed -= {"options"}
            if set(body) - allowed:
                raise HTTPException(400, "Unsupported embedding request fields")
            inputs = body.get("input")
            texts = [inputs] if isinstance(inputs, str) else inputs
            if (
                not isinstance(texts, list)
                or not 1 <= len(texts) <= 64
                or any(
                    not isinstance(text, str) or not text.strip() or len(text) > 32000
                    for text in texts
                )
            ):
                raise HTTPException(400, "Provide 1–64 non-empty texts of at most 32000 characters")
            if type(body.get("truncate", False)) is not bool:
                raise HTTPException(400, "truncate must be boolean")
            if "dimensions" in body and (
                type(body["dimensions"]) is not int or not 1 <= body["dimensions"] <= 65536
            ):
                raise HTTPException(400, "dimensions must be 1–65536")
            body = {
                name: value
                for name, value in body.items()
                if name in {"model", "input", "target", "truncate", "dimensions", "options"}
            }
            body.setdefault("truncate", False)
            stream, stream_options = False, {}
        else:
            allowed = {"model", "stream", "target"}
            allowed |= (
                {
                    "messages",
                    "temperature",
                    "top_p",
                    "max_tokens",
                    "max_completion_tokens",
                    "stop",
                    "seed",
                    "tools",
                    "response_format",
                    "stream_options",
                    "n",
                }
                if openai or anthropic
                else {
                    "prompt",
                    "messages",
                    "options",
                    "format",
                    "think",
                    "tools",
                    "system",
                    "template",
                    "raw",
                    "suffix",
                    "images",
                    "keep_alive",
                }
            )
            if openai:
                allowed |= {
                    "frequency_penalty",
                    "presence_penalty",
                    "reasoning_effort",
                    "reasoning",
                    "tool_choice",
                    "logit_bias",
                    "user",
                    "logprobs",
                    "top_logprobs",
                }
            else:
                allowed |= {"logprobs", "top_logprobs", "context"}
            if completion:
                allowed |= {"prompt", "suffix", "echo", "best_of"}
            if responses:
                allowed = {
                    "model",
                    "target",
                    "input",
                    "instructions",
                    "tools",
                    "tool_choice",
                    "stream",
                    "temperature",
                    "top_p",
                    "max_output_tokens",
                    "reasoning",
                    "think",
                    "text",
                    "store",
                }
                if body.get("store") not in (None, False):
                    raise HTTPException(400, "Only stateless Responses requests are supported")
                body.pop("store", None)
            if anthropic:
                allowed = {
                    "model",
                    "target",
                    "messages",
                    "max_tokens",
                    "system",
                    "stream",
                    "temperature",
                    "top_p",
                    "top_k",
                    "stop_sequences",
                    "tools",
                    "tool_choice",
                    "thinking",
                    "metadata",
                    "output_config",
                }
            if set(body) - allowed:
                raise HTTPException(400, "Unsupported request fields")
            # Frameworks send residency hints; clients cannot unload or change residency.
            body.pop("keep_alive", None)
            stream = body.get("stream", not (openai or anthropic))
            if not isinstance(stream, bool):
                raise HTTPException(400, "stream must be boolean")
            if responses:
                if not isinstance(body.get("input"), (str, list)) or not body["input"]:
                    raise HTTPException(400, "Non-empty input required")
            elif (openai and not completion) or anthropic or path == "/api/chat":
                if not isinstance(body.get("messages"), list) or not body["messages"]:
                    raise HTTPException(400, "Non-empty messages array required")
            elif not isinstance(body.get("prompt"), str) or not body["prompt"]:
                raise HTTPException(400, "Non-empty prompt required")
            options = body.get("options", {})
            if not isinstance(options, dict) or set(options) - {
                "num_ctx",
                "num_predict",
                "temperature",
                "top_p",
                "top_k",
                "seed",
                "stop",
                "repeat_penalty",
                "min_p",
                "num_keep",
                "repeat_last_n",
                "presence_penalty",
                "frequency_penalty",
            }:
                raise HTTPException(400, "Unsupported runtime options")
            limit = body.get(
                "max_output_tokens",
                body.get(
                    "max_completion_tokens",
                    body.get(
                        "max_tokens",
                        options.get("num_predict", min(256, key["max_tokens"] or 4096)),
                    ),
                ),
            )
            if type(limit) is not int or not 1 <= limit <= 4096:
                raise HTTPException(400, "Output token limit must be 1–4096")
            if key["max_tokens"] is not None and limit > key["max_tokens"]:
                raise HTTPException(
                    403, "Requested output tokens exceed this key's per-request limit"
                )
            if "num_ctx" in options and options["num_ctx"] != cfg.context_length:
                raise HTTPException(400, "Context length must match the loaded runtime")
            if openai and body.get("n", 1) != 1:
                raise HTTPException(400, "Only n=1 is supported")
            if openai:
                body.pop("n", None)
            stream_options = body.get("stream_options", {})
            if not isinstance(stream_options, dict) or set(stream_options) - {"include_usage"}:
                raise HTTPException(400, "Unsupported stream options")
        options = body.get("options", {})
        if embedding and (
            not isinstance(options, dict)
            or set(options)
            - {
                "num_ctx",
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "seed",
                "stop",
                "repeat_penalty",
                "num_keep",
                "repeat_last_n",
                "presence_penalty",
                "frequency_penalty",
            }
        ):
            raise HTTPException(400, "Unsupported embedding runtime options")
        if "num_ctx" in options and options["num_ctx"] != cfg.context_length:
            raise HTTPException(400, "Context length must match the loaded runtime")
        # Reject unauthorized attachments before spending CPU on preprocessing.
        requested_target = body.get("target", request.headers.get("x-llameros-target", ""))
        if not isinstance(requested_target, str):
            raise HTTPException(400, "Invalid target")
        if "*" not in key["models"] and model not in key["models"]:
            raise HTTPException(403, "Model not permitted")
        if requested_target and not keys.allowed(key, model, requested_target):
            raise HTTPException(403, "Hardware target not permitted")
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict)
            or tool.get("type", "function" if anthropic else None) != "function"
            for tool in tools
        ):
            raise HTTPException(400, "Only client-executed function tools are supported")
        images = validate_images(body, cfg)
        reject_documents(body)
        if not images and original_size > 131072:
            raise HTTPException(413, "Text-only request too large")
        target = body.pop("target", request.headers.get("x-llameros-target", ""))
        if not isinstance(target, str):
            raise HTTPException(400, "Invalid target")
        if "*" not in key["models"] and model not in key["models"]:
            raise HTTPException(403, "Model not permitted")
        if target and not keys.allowed(key, model, target):
            raise HTTPException(403, "Hardware target not permitted")
        ticket = None
        start = time.monotonic()
        counts, outcome = {}, "failed"
        eval_duration = 0
        upstream = None
        client = None
        finished = False
        quota_started = None
        dispatched = False
        quota_scopes = [
            ("key", key["id"]),
            ("user", key["user"]),
            keys.limits.model_scope(key["user"], model),
        ]

        async def finish():
            """Idempotently close upstream resources, record content-free usage, and release the device permit."""
            nonlocal finished
            if finished:
                return
            finished = True
            request.state.activity_recorded = True
            try:
                if upstream is not None:
                    await upstream.aclose()
                if client is not None:
                    await client.aclose()
            finally:
                try:
                    if dispatched:
                        keys.limits.charge(quota_scopes, counts, quota_started)
                    keys.record(key["id"], model, target, outcome, counts, time.monotonic() - start)
                    if outcome == "completed" and counts:
                        store.generation(
                            {
                                "model": model,
                                "prompt_eval_count": counts.get("prompt", 0),
                                "eval_count": counts.get("output", 0),
                                "eval_duration": eval_duration,
                                "total_duration": int((time.monotonic() - start) * 1e9),
                            }
                        )
                finally:
                    if ticket is not None:
                        jobs.scheduler.release(ticket, outcome)
                    else:
                        with jobs.lock:
                            if jobs.job["state"] != "running":
                                jobs.job.update(state="failed", error="API routing failed")

        def usage(event):
            """Validate response/embedding structure and collect only nonnegative known token counts."""
            nonlocal eval_duration
            if not isinstance(event, dict):
                raise ValueError("Invalid upstream event")
            if event.get("error"):
                raise ValueError("Upstream error")
            duration = event.get("eval_duration")
            if type(duration) is int and duration > 0:
                eval_duration = duration
            if embedding:
                vectors = event.get("embeddings")
                if not isinstance(vectors, list) or len(vectors) != len(texts):
                    raise ValueError("Invalid embedding batch")
                dimension = None
                for vector in vectors:
                    if (
                        not isinstance(vector, list)
                        or not vector
                        or len(vector) > 65536
                        or any(
                            type(value) not in (int, float) or not math.isfinite(value)
                            for value in vector
                        )
                    ):
                        raise ValueError("Invalid embedding vector")
                    if dimension is not None and len(vector) != dimension:
                        raise ValueError("Inconsistent embedding dimensions")
                    dimension = len(vector)
                value = event.get("prompt_eval_count")
                if type(value) is int and value >= 0:
                    counts["prompt"] = value
                counts["output"] = 0
                return
            if responses:
                values = event.get("response", event).get("usage")
            elif anthropic:
                values = event.get("message", event).get("usage")
            else:
                values = event.get("usage") if openai else event if event.get("done") else None
            if values:
                if not isinstance(values, dict):
                    raise ValueError("Invalid usage data")
                for source, dest in (
                    (
                        "input_tokens"
                        if responses or anthropic
                        else "prompt_tokens"
                        if openai
                        else "prompt_eval_count",
                        "prompt",
                    ),
                    (
                        "output_tokens"
                        if responses or anthropic
                        else "completion_tokens"
                        if openai
                        else "eval_count",
                        "output",
                    ),
                ):
                    value = values.get(source)
                    if type(value) is int and value >= 0:
                        counts[dest] = value

        try:
            loaded = await run_in_threadpool(permitted_models, key)
            matches = [
                m
                for m in loaded
                if normalize(m.get("name", m.get("model"))) == model
                and (not target or m["target"] == target)
            ]
            if len(matches) != 1:
                raise HTTPException(
                    409, "Choose exactly one permitted loaded model and hardware target"
                )
            target = matches[0]["target"]
            keys.limits.check(quota_scopes)
            ticket = await jobs.scheduler.acquire(target, model, request, username=key["user"])
            # Keys may be revoked while waiting; do not start new upstream work afterward.
            await run_in_threadpool(bearer, request)
            runtime = (
                await run_in_threadpool(runtimes.model_client, model, target)
                if runtimes
                else docker
            )
            base_url = RUNTIME_BASE_URL
            if embedding:
                body.update(
                    model=model, keep_alive=-1, options={**options, "num_ctx": cfg.context_length}
                )
                if target == "cpu":
                    body["options"]["num_gpu"] = 0
            else:
                body.update(model=model, stream=stream)
            if responses:
                body["max_output_tokens"] = limit
            elif anthropic:
                body["max_tokens"] = limit
            elif openai and not embedding:
                body.pop("max_completion_tokens", None)
                body["max_tokens"] = limit
                if stream:
                    body["stream_options"] = {"include_usage": True}
            elif not embedding:
                body["options"] = {**options, "num_ctx": cfg.context_length, "num_predict": limit}
                if target == "cpu":
                    body["options"]["num_gpu"] = 0
                body["keep_alive"] = -1
            client = httpx.AsyncClient(
                base_url=base_url,
                transport=transport
                or httpx.AsyncHTTPTransport(uds=await run_in_threadpool(runtime.socket_path)),
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(600, connect=5),
            )
            if images:
                info = await client.post("/api/show", json={"model": model})
                if info.status_code != 200:
                    raise HTTPException(502, "Cannot determine model vision capability")
                capabilities = info.json().get("capabilities", [])
                if "vision" not in capabilities:
                    raise HTTPException(400, "Loaded model does not advertise vision capability")
            quota_started = keys.limits.check(quota_scopes)
            dispatched = True
            upstream = await client.send(
                client.build_request("POST", "/api/embed" if embedding else path, json=body),
                stream=True,
            )
            if upstream.status_code != 200:
                raise HTTPException(
                    upstream.status_code
                    if upstream.status_code in {400, 404, 422, 429, 501, 503}
                    else 502,
                    "Model runtime rejected the request",
                )
            if not stream:
                data = json.loads(await upstream.aread())
                usage(data)
                outcome = "completed"
                if embedding and path == "/v1/embeddings":
                    data = {
                        "object": "list",
                        "model": model,
                        "data": [
                            {
                                "object": "embedding",
                                "index": index,
                                "embedding": base64.b64encode(
                                    struct.pack("<" + "f" * len(vector), *vector)
                                ).decode()
                                if encoding == "base64"
                                else vector,
                            }
                            for index, vector in enumerate(data["embeddings"])
                        ],
                        "usage": {
                            "prompt_tokens": counts.get("prompt", 0),
                            "total_tokens": counts.get("prompt", 0),
                        },
                    }
                elif legacy_embedding:
                    data = {"embedding": data["embeddings"][0]}
                await finish()
                return JSONResponse(data)

            async def events():
                """Yield protocol-specific stream frames and require a valid terminal event.

                Represent upstream stream failures in-band and shield accounting/permit release
                during disconnect cleanup.
                """
                nonlocal outcome
                terminal = False
                try:
                    async for line in upstream.aiter_lines():
                        if not line:
                            continue
                        if openai or anthropic:
                            if line.startswith("event:"):
                                continue
                            if not line.startswith("data: "):
                                continue
                            value = line[6:]
                            if value == "[DONE]":
                                if responses or anthropic:
                                    raise ValueError("Missing protocol terminal event")
                                terminal = True
                                outcome = "completed"
                                yield "data: [DONE]\n\n"
                                break
                            event = json.loads(value)
                            usage(event)
                            if responses or anthropic:
                                kind = event.get("type", "")
                                terminal = kind in {
                                    "response.completed",
                                    "response.incomplete",
                                    "message_stop",
                                }
                                if kind == "response.failed":
                                    raise ValueError("Response failed")
                                yield "event: " + kind + "\ndata: " + json.dumps(event) + "\n\n"
                                if terminal:
                                    outcome = "completed"
                                    break
                                continue
                            if not stream_options.get("include_usage"):
                                if event.get("choices") == [] and event.get("usage"):
                                    continue
                                event.pop("usage", None)
                            yield "data: " + json.dumps(event) + "\n\n"
                        else:
                            event = json.loads(line)
                            usage(event)
                            yield json.dumps(event) + "\n"
                            if event.get("done"):
                                terminal = True
                                outcome = "completed"
                                break
                    if not terminal:
                        raise ValueError("Incomplete upstream stream")
                except (httpx.HTTPError, ValueError, TypeError):
                    error = (
                        {
                            "error": {
                                "message": "Model stream failed or ended unexpectedly",
                                "type": "upstream_error",
                            }
                        }
                        if openai or anthropic
                        else {"error": "Model stream failed or ended unexpectedly"}
                    )
                    if anthropic:
                        error["type"] = "error"
                    yield ("event: error\n" if anthropic or responses else "") + (
                        ("data: " + json.dumps(error) + "\n\n")
                        if openai or anthropic
                        else json.dumps(error) + "\n"
                    )
                finally:
                    # Cleanup/accounting must survive a disconnected streaming client.
                    task = asyncio.create_task(finish())
                    await asyncio.shield(task)

            return InferenceStream(
                events(),
                cleanup=finish,
                media_type="text/event-stream" if openai or anthropic else "application/x-ndjson",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
        except HTTPException:
            await finish()
            raise
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            AttributeError,
            OverflowError,
            struct.error,
            DockerError,
        ):
            await finish()
            raise HTTPException(502, "Model runtime unavailable or returned invalid data") from None
        except BaseException:
            await asyncio.shield(asyncio.create_task(finish()))
            raise

    for path in (
        "/api/generate",
        "/api/chat",
        "/v1/chat/completions",
        "/api/embed",
        "/v1/embeddings",
        "/api/embeddings",
        "/v1/completions",
        "/v1/responses",
        "/v1/messages",
    ):
        app.add_api_route(PREFIX + path, infer, methods=["POST"])
