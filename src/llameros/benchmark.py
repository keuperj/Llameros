"""Exclusive, bounded synthetic benchmarks; persist metrics, never generated content."""

import asyncio
import threading
import time
from concurrent.futures import Future

import httpx

from .runtime_transport import RUNTIME_BASE_URL

TEXT = "A local library lends books, offers quiet study rooms, and teaches digital skills. "
CHAT_CASES = [
    ("short-short", "Explain what a library does in two sentences.", 64),
    ("short-long", "Write a detailed practical guide to organizing a community library.", 384),
    ("medium-short", TEXT * 20 + "Summarize in two sentences.", 64),
    ("medium-medium", TEXT * 20 + "Suggest improvements with explanations.", 192),
    ("long-short", TEXT * 70 + "Summarize the main ideas briefly.", 64),
    ("long-long", TEXT * 70 + "Write a detailed plan to improve these services.", 384),
]
EMBED_CASES = [
    ("short", 1, 1),
    ("medium", 12, 1),
    ("long", 50, 1),
    ("batch-short", 1, 8),
    ("batch-medium", 12, 4),
    ("batch-long", 50, 2),
]


async def measure(
    runtime, model, target, context, parallel, progress, *, transport=None, budget=285
):
    """Cancel all benchmark HTTP work at an absolute deadline, including slow streams.

    Runs sequential requests to measure single-request speed at the configured runtime
    parallelism. The budget includes connection setup and capability discovery.
    """
    started = time.monotonic()
    result = {
        "model": model,
        "target": target,
        "timestamp": time.time(),
        "context_length": context,
        "parallel": parallel,
        "kind": "unknown",
        "status": "failed",
        "cases": [],
        "tokens_per_second": None,
        "method": "single-request-v1",
    }

    async def run():
        nonlocal transport
        if transport is None:
            # Docker inspection is blocking. Do not let asyncio.run's executor
            # shutdown extend the benchmark deadline if Docker itself stalls.
            ready = Future()

            def prepare():
                try:
                    ready.set_result(runtime.socket_path())
                except Exception as exc:
                    ready.set_exception(exc)

            threading.Thread(target=prepare, daemon=True).start()
            while not ready.done():
                await asyncio.sleep(0.02)
            socket = ready.result()
            transport = httpx.AsyncHTTPTransport(uds=socket)
        async with httpx.AsyncClient(
            base_url=RUNTIME_BASE_URL, transport=transport, trust_env=False, timeout=budget
        ) as client:

            async def post(path, body):
                response = await client.post(path, json=body)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise ValueError("Invalid benchmark response")
                return data

            info = await post("/api/show", {"model": model})
            capabilities = info.get("capabilities", [])
            if "completion" in capabilities:
                result["kind"] = "chat"
            elif "embedding" in capabilities:
                result["kind"] = "embedding"
            else:
                raise ValueError("Unsupported benchmark capability")
            cases = CHAT_CASES if result["kind"] == "chat" else EMBED_CASES
            for index, (label, content, size) in enumerate(cases):
                progress(
                    {
                        "status": f"Benchmark {index + 1}/{len(cases)} · {label}",
                        "completed": index,
                        "total": len(cases),
                    }
                )
                options = {"num_ctx": context}
                if target == "cpu":
                    options["num_gpu"] = 0
                body = {"model": model, "keep_alive": -1, "options": options}
                if result["kind"] == "chat":
                    body.update(messages=[{"role": "user", "content": content}], stream=False)
                    options.update(num_predict=size, temperature=0, seed=42)
                    path = "/api/chat"
                else:
                    body.update(input=[TEXT * content for _ in range(size)], truncate=True)
                    path = "/api/embed"
                before = time.monotonic()
                data = await post(path, body)
                elapsed = time.monotonic() - before
                token_field = "eval_count" if result["kind"] == "chat" else "prompt_eval_count"
                tokens = data.get(token_field)
                if type(tokens) is not int or tokens <= 0:
                    raise ValueError("Missing benchmark token counts")
                if result["kind"] == "chat" and not data.get("done"):
                    raise ValueError("Incomplete benchmark response")
                if result["kind"] == "embedding" and len(data.get("embeddings", [])) != size:
                    raise ValueError("Incomplete embedding response")
                duration = data.get("eval_duration") if result["kind"] == "chat" else None
                native = type(duration) is int and duration > 0
                seconds = duration / 1e9 if native else elapsed
                result["cases"].append(
                    {
                        "name": label,
                        "tokens": tokens,
                        "seconds": seconds,
                        "wall_seconds": elapsed,
                        "timing": "generation" if native else "request",
                        "tokens_per_second": tokens / seconds,
                        "output_limit": size if result["kind"] == "chat" else None,
                        "batch_size": size if result["kind"] == "embedding" else 1,
                    }
                )
            result["status"] = "completed"

    try:
        async with asyncio.timeout(budget):
            await run()
    except TimeoutError:
        result["status"] = "timed-out"
        result["error"] = "Benchmark deadline reached; partial results retained."
    except Exception:
        result["error"] = (
            "Benchmark failed: unsupported capability, missing metrics, or runtime error."
        )
    result["wall_seconds"] = time.monotonic() - started
    cases = result["cases"]
    if cases:
        result["tokens_per_second"] = sum(c["tokens"] for c in cases) / sum(
            c["seconds"] for c in cases
        )
    return result
