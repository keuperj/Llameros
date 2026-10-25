"""Allowlisted inference proxy. Never forwards credentials or lifecycle commands."""

import asyncio
import json
import math
import time

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .api_keys import ApiKeys, KeyRequest, normalize
from .docker import DockerError

PREFIX = "/inference"


class InferenceStream(StreamingResponse):
    """Release the inference lock even if disconnect precedes generator iteration."""

    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(asyncio.create_task(self.cleanup()))


def install_gateway(
    app, cfg, store, jobs, protected, runtime_status, runtimes, docker, transport=None
):
    keys = ApiKeys(store)
    app.state.api_keys = keys

    @app.get("/api/keys", dependencies=protected)
    def list_keys():
        return {"keys": [key for key in keys.list() if not key["deleted"]]}

    @app.get("/api/keys/usage", dependencies=protected)
    def key_usage():
        return {"keys": keys.list()}

    @app.delete("/api/keys/{identity}", dependencies=protected)
    def delete_key(identity: str):
        keys.delete(identity)
        return {"ok": True}

    @app.post("/api/keys", dependencies=protected, status_code=201)
    def create_key(body: KeyRequest):
        return keys.create(body)

    @app.post("/api/keys/{identity}/revoke", dependencies=protected)
    def revoke_key(identity: str):
        keys.revoke(identity)
        return {"ok": True}

    def bearer(request: Request):
        return keys.authenticate(request.headers.get("authorization", ""))

    def permitted_models(key):
        state = runtime_status()
        return [
            {**model, "target": model.get("target", "cpu")}
            for model in state.get("loaded", [])
            if keys.allowed(key, model.get("name", model.get("model")), model.get("target", "cpu"))
        ]

    @app.get(PREFIX + "/api/tags")
    @app.get(PREFIX + "/v1/models")
    def models(request: Request, key=Depends(bearer)):
        loaded = permitted_models(key)
        if request.url.path.endswith("/v1/models"):
            return {
                "object": "list",
                "data": [
                    {
                        "id": normalize(m.get("name", m.get("model"))),
                        "object": "model",
                        "created": 0,
                        "owned_by": "llameros",
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

    async def infer(request: Request, key=Depends(bearer)):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 131072:
                raise HTTPException(413, "Request too large")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
            model = normalize(body.get("model", ""))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Valid JSON object and official model name required") from None
        path = request.url.path.removeprefix(PREFIX)
        openai = path == "/v1/chat/completions"
        embedding = path in {"/api/embed", "/v1/embeddings"}
        if embedding:
            allowed = {"model", "input", "target", "keep_alive", "truncate", "dimensions"}
            if path == "/v1/embeddings":
                allowed |= {"encoding_format", "user"}
                if body.get("encoding_format", "float") != "float":
                    raise HTTPException(400, "Only float embedding encoding is supported")
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
                if name in {"model", "input", "target", "truncate", "dimensions"}
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
                if openai
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
            if set(body) - allowed:
                raise HTTPException(400, "Unsupported request fields")
            # Frameworks send residency hints; clients cannot unload or change residency.
            body.pop("keep_alive", None)
            stream = body.get("stream", not openai)
            if not isinstance(stream, bool):
                raise HTTPException(400, "stream must be boolean")
            if openai or path == "/api/chat":
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
            }:
                raise HTTPException(400, "Unsupported runtime options")
            limit = body.get(
                "max_completion_tokens",
                body.get(
                    "max_tokens", options.get("num_predict", min(256, key["max_tokens"] or 4096))
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
        upstream = None
        client = None
        finished = False

        async def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            try:
                if upstream is not None:
                    await upstream.aclose()
                if client is not None:
                    await client.aclose()
            finally:
                try:
                    keys.record(key["id"], model, target, outcome, counts, time.monotonic() - start)
                    if outcome == "completed" and counts:
                        store.generation(
                            {
                                "model": model,
                                "prompt_eval_count": counts.get("prompt", 0),
                                "eval_count": counts.get("output", 0),
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
            if not isinstance(event, dict):
                raise ValueError("Invalid upstream event")
            if event.get("error"):
                raise ValueError("Upstream error")
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
            values = event.get("usage") if openai else event if event.get("done") else None
            if values:
                if not isinstance(values, dict):
                    raise ValueError("Invalid usage data")
                for source, dest in (
                    ("prompt_tokens" if openai else "prompt_eval_count", "prompt"),
                    ("completion_tokens" if openai else "eval_count", "output"),
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
            ticket = await jobs.scheduler.acquire(target, model, request)
            # Keys may be revoked while waiting; do not start new upstream work afterward.
            await run_in_threadpool(keys.authenticate, request.headers.get("authorization", ""))
            runtime = (
                await run_in_threadpool(runtimes.model_client, model, target)
                if runtimes
                else docker
            )
            base_url = runtime.settings.ollama_url if runtimes else cfg.ollama_url
            if embedding:
                body.update(model=model, keep_alive=-1, options={"num_ctx": cfg.context_length})
                if target == "cpu":
                    body["options"]["num_gpu"] = 0
            else:
                body.update(model=model, stream=stream)
            if openai:
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
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(600, connect=5),
            )
            upstream = await client.send(
                client.build_request("POST", "/api/embed" if embedding else path, json=body),
                stream=True,
            )
            if upstream.status_code != 200:
                raise HTTPException(502, "Model runtime rejected the request")
            if not stream:
                data = json.loads(await upstream.aread())
                usage(data)
                outcome = "completed"
                if embedding and path == "/v1/embeddings":
                    data = {
                        "object": "list",
                        "model": model,
                        "data": [
                            {"object": "embedding", "index": index, "embedding": vector}
                            for index, vector in enumerate(data["embeddings"])
                        ],
                        "usage": {
                            "prompt_tokens": counts.get("prompt", 0),
                            "total_tokens": counts.get("prompt", 0),
                        },
                    }
                await finish()
                return JSONResponse(data)

            async def events():
                nonlocal outcome
                terminal = False
                try:
                    async for line in upstream.aiter_lines():
                        if not line:
                            continue
                        if openai:
                            if not line.startswith("data: "):
                                continue
                            value = line[6:]
                            if value == "[DONE]":
                                terminal = True
                                outcome = "completed"
                                yield "data: [DONE]\n\n"
                                break
                            event = json.loads(value)
                            usage(event)
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
                        if openai
                        else {"error": "Model stream failed or ended unexpectedly"}
                    )
                    yield (
                        ("data: " + json.dumps(error) + "\n\n")
                        if openai
                        else json.dumps(error) + "\n"
                    )
                finally:
                    # Cleanup/accounting must survive a disconnected streaming client.
                    task = asyncio.create_task(finish())
                    await asyncio.shield(task)

            return InferenceStream(
                events(),
                cleanup=finish,
                media_type="text/event-stream" if openai else "application/x-ndjson",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
        except HTTPException:
            await finish()
            raise
        except (httpx.HTTPError, ValueError, TypeError, DockerError):
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
    ):
        app.add_api_route(PREFIX + path, infer, methods=["POST"])
