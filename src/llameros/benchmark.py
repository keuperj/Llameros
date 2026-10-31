"""Exclusive synthetic speed and adaptive sustained-load benchmarks."""

import asyncio
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import httpx
from fastapi import HTTPException

from .runtime_transport import RUNTIME_BASE_URL
from .scheduling import InferenceScheduler


async def stress_test(send, workloads, scheduler, progress, result, checkpoint, *, minute=60):
    """Open-loop arrivals; stop at the first outcome reversal or manual cancellation.

    Every stage offers at least six requests and one minute of arrivals. Outstanding
    work is drained before changing rate; response deadlines include queue wait.
    Synthetic scheduler/HTTP failures are metrics only, never activity log entries.
    """
    rate = 1.0
    direction = None
    stress = result["stress"] = {"status": "running", "best_rpm": 0, "rounds": [], "round_count": 0}
    while True:
        interval = minute / rate
        duration = max(minute, len(workloads) * interval)
        count = max(len(workloads), int(duration / interval))
        stats = {
            "rpm": rate,
            "offered": 0,
            "completed": 0,
            "failed": 0,
            "overflow": 0,
            "timeouts": 0,
        }
        stress["current_round"] = stats
        tasks = set()
        failed = asyncio.Event()
        started = time.monotonic()

        async def request(index, stats=stats, failed=failed):
            ticket = None
            try:
                async with asyncio.timeout(scheduler.timeout):
                    ticket = await scheduler.acquire("benchmark", "benchmark", username="synthetic")
                    await send(*workloads[index % len(workloads)])
                stats["completed"] += 1
            except Exception as exc:
                stats["failed"] += 1
                if isinstance(exc, HTTPException) and exc.status_code == 429:
                    stats["overflow"] += 1
                if (
                    isinstance(exc, (TimeoutError, httpx.TimeoutException))
                    or isinstance(exc, HTTPException)
                    and exc.status_code == 504
                ):
                    stats["timeouts"] += 1
                failed.set()
            finally:
                if ticket is not None:
                    scheduler.release(ticket, "completed")

        try:
            for index in range(count):
                delay = started + index * interval - time.monotonic()
                if delay > 0:
                    try:
                        await asyncio.wait_for(failed.wait(), delay)
                        break
                    except TimeoutError:
                        pass
                if failed.is_set():
                    break
                stats["offered"] += 1
                task = asyncio.create_task(request(index))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                progress(
                    {
                        "status": f"Stress test · {rate:g} requests/min · {stats['offered']}/{count} offered · {stats['failed']} failures",
                        "phase": "stress",
                        "rpm": rate,
                    }
                )
                await asyncio.sleep(0)
            if not failed.is_set():
                remaining = started + duration - time.monotonic()
                if remaining > 0:
                    try:
                        await asyncio.wait_for(failed.wait(), remaining)
                    except TimeoutError:
                        pass
            if tasks:
                await asyncio.gather(*list(tasks))
        finally:
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*list(tasks), return_exceptions=True)
        stats["wall_seconds"] = time.monotonic() - started
        stats["successful"] = stats["failed"] == 0
        stress["round_count"] += 1
        stress["rounds"] = [*stress["rounds"], stats][-200:]
        if stats["successful"]:
            stress["best_rpm"] = max(stress["best_rpm"], rate)
        if direction is None:
            direction = "up" if stats["successful"] else "down"
        elif (direction == "up" and not stats["successful"]) or (
            direction == "down" and stats["successful"]
        ):
            stress["status"] = "completed"
            stress["stop_reason"] = (
                "failure-scaling-up" if direction == "up" else "success-scaling-down"
            )
            checkpoint(result)
            return
        checkpoint(result)
        rate = rate * 2 if stats["successful"] else rate / 2


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
    runtime,
    model,
    target,
    context,
    parallel,
    progress,
    control=None,
    policy=None,
    checkpoint=lambda result: None,
    *,
    transport=None,
    budget=None,
):
    """Measure six sequential workloads then adapt load to its first outcome reversal.

    No production overall deadline; budget is an optional test-only deadline.
    Cancellation closes HTTP tasks and the isolated scheduler's reservations.
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
            # shutdown block manual cancellation if Docker itself stalls.
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
            base_url=RUNTIME_BASE_URL, transport=transport, trust_env=False, timeout=None
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
            workloads = []
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
                workloads.append((path, body))
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
            result["tokens_per_second"] = sum(c["tokens"] for c in result["cases"]) / sum(
                c["seconds"] for c in result["cases"]
            )
            checkpoint(result)
            if policy is not None:
                jobs = SimpleNamespace(lock=threading.Lock(), job={"state": "idle"}, store=None)
                scheduler = InferenceScheduler(
                    jobs,
                    capacity=policy["queue_size"],
                    timeout=policy["queue_timeout"],
                    parallel=parallel,
                )
                scheduler.queue_users = policy["queue_users"]
                result["stress_policy"] = dict(policy)

                async def stress_post(path, body):
                    data = await post(path, body)
                    if result["kind"] == "chat" and not data.get("done"):
                        raise ValueError("Incomplete stress response")
                    if result["kind"] == "embedding" and len(data.get("embeddings", [])) != len(
                        body["input"]
                    ):
                        raise ValueError("Incomplete embedding stress response")

                await stress_test(stress_post, workloads, scheduler, progress, result, checkpoint)

    async def cancellable_run():
        task = asyncio.create_task(run())
        try:
            while not task.done():
                if control is not None and control.is_set():
                    task.cancel()
                    break
                await asyncio.sleep(0.02)
            await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        async with asyncio.timeout(budget):
            await cancellable_run()
    except asyncio.CancelledError:
        if control is None or not control.is_set():
            raise
        if "stress" in result:
            result["stress"]["status"] = "stopped"
        else:
            result["status"] = "stopped"
    except TimeoutError:
        result["status"] = "timed-out"
        result["error"] = "Benchmark deadline reached; partial results retained."
    except Exception:
        if "stress" in result:
            result["stress"]["status"] = "failed"
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
