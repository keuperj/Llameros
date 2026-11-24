"""Accelerated sustained-load tests using real FIFO admission and mock HTTP."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
from test_users import multiuser as multiuser

from llameros.benchmark import measure, stress_test
from llameros.scheduling import InferenceScheduler


def scheduler(capacity=0, timeout=1):
    jobs = SimpleNamespace(lock=threading.Lock(), job={"state": "idle"}, store=None)
    return InferenceScheduler(jobs, capacity=capacity, timeout=timeout, parallel=1)


def test_replica_stress_uses_both_transports_concurrently_without_mixing_speed_scores(monkeypatch):
    from llameros import benchmark

    calls = []
    active = set()
    overlapped = asyncio.Event()
    pool_running = False
    first_round_finished = False
    closed = []

    class ReplicaTransport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)

    def transport(*, uds):
        async def handle(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion"]})
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": []})
            calls.append((pool_running, uds))
            if first_round_finished:
                return httpx.Response(500)
            if pool_running:
                active.add(uds)
                if len(active) == 2:
                    overlapped.set()
                try:
                    await asyncio.wait_for(overlapped.wait(), 1)
                finally:
                    active.discard(uds)
            return httpx.Response(
                200, json={"done": True, "eval_count": 10, "eval_duration": 10**9}
            )

        return ReplicaTransport(handle)

    def checkpoint(result):
        nonlocal pool_running, first_round_finished
        pool_running = True
        first_round_finished = bool(result.get("stress", {}).get("rounds"))

    async def accelerated(*args, **kwargs):
        await stress_test(*args, **kwargs, minute=0.015)

    monkeypatch.setattr(benchmark.httpx, "AsyncHTTPTransport", transport)
    monkeypatch.setattr(benchmark, "stress_test", accelerated)
    first = SimpleNamespace(socket_path=lambda: "gpu-a")
    second = SimpleNamespace(socket_path=lambda: "gpu-b")
    result = asyncio.run(
        measure(
            first,
            "model",
            "deployment",
            4096,
            1,
            lambda _: None,
            policy={"queue_size": 2, "queue_timeout": 2, "queue_users": 0},
            checkpoint=checkpoint,
            replica_workers={"a": first, "b": second},
            budget=4,
        )
    )
    assert result["status"] == "completed"
    assert result["tokens_per_second"] == 10
    assert [node for stress, node in calls if not stress] == ["gpu-a"] * 6
    assert {node for stress, node in calls if stress} == {"gpu-a", "gpu-b"}
    assert overlapped.is_set()
    stress = result["stress"]
    assert (stress["scope"], stress["worker_count"], stress["status"]) == (
        "replica-pool",
        2,
        "completed",
    )
    assert stress["rounds"][0]["completed"] == 6
    assert stress["best_rpm"] == 1
    assert closed == [True, True]


def test_open_loop_stops_at_first_scaling_up_failure_and_cleans_up():
    async def scenario():
        queue = scheduler()
        result, calls, rounds = {}, [], []

        async def send(worker, path, body):
            calls.append(body)
            await asyncio.sleep(0.003)

        def checkpoint(data):
            rounds.append(dict(data["stress"]["rounds"][-1]))

        try:
            await stress_test(
                send,
                [("/chat", i) for i in range(6)],
                queue,
                lambda p: None,
                result,
                checkpoint,
                workers=("cpu",),
                minute=0.01,
            )
        except asyncio.CancelledError:
            pass
        assert [row["rpm"] for row in rounds[:3]] == [1, 2, 4]
        assert rounds[0]["successful"] and rounds[1]["successful"]
        assert rounds[2]["overflow"] > 0
        assert len(rounds) == 3
        assert result["stress"]["status"] == "completed"
        assert result["stress"]["stop_reason"] == "failure-scaling-up"
        assert set(calls[:6]) == set(range(6))
        assert not queue.tickets

    asyncio.run(scenario())


def test_scaling_down_stops_at_first_success():
    async def scenario():
        queue = scheduler()
        result, rounds = {}, []

        async def send(*args):
            if len(rounds) < 2:
                raise RuntimeError("Synthetic failure")

        def checkpoint(data):
            rounds.append(dict(data["stress"]["rounds"][-1]))

        await asyncio.wait_for(
            stress_test(
                send,
                [("/chat", {})] * 6,
                queue,
                lambda p: None,
                result,
                checkpoint,
                workers=("cpu",),
                minute=0.001,
            ),
            1,
        )
        assert [row["rpm"] for row in rounds] == [1, 0.5, 0.25]
        assert [row["successful"] for row in rounds] == [False, False, True]
        assert result["stress"]["best_rpm"] == 0.25
        assert result["stress"]["status"] == "completed"
        assert result["stress"]["stop_reason"] == "success-scaling-down"
        assert not queue.tickets

    asyncio.run(scenario())


def test_timeout_is_a_metric_not_an_exception_and_rate_halves():
    async def scenario():
        queue = scheduler(capacity=2, timeout=0.002)
        result, rounds = {}, []

        async def send(*args):
            await asyncio.sleep(1)

        def checkpoint(data):
            rounds.append(dict(data["stress"]["rounds"][-1]))
            if len(rounds) == 2:
                raise asyncio.CancelledError()

        try:
            await stress_test(
                send,
                [("/chat", {})] * 6,
                queue,
                lambda p: None,
                result,
                checkpoint,
                workers=("cpu",),
                minute=0.01,
            )
        except asyncio.CancelledError:
            pass
        assert [row["rpm"] for row in rounds] == [1, 0.5]
        assert all(row["timeouts"] for row in rounds)
        assert not queue.tickets

    asyncio.run(scenario())


def test_manual_stop_interrupts_slow_request_without_overall_deadline():
    async def scenario():
        stop = threading.Event()
        entered = asyncio.Event()
        closed = []

        async def handle(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion"]})
            entered.set()
            try:
                await asyncio.sleep(100)
            finally:
                closed.append(True)

        task = asyncio.create_task(
            measure(
                None,
                "model",
                "cpu",
                4096,
                1,
                lambda p: None,
                stop,
                transport=httpx.MockTransport(handle),
            )
        )
        await entered.wait()
        stop.set()
        result = await asyncio.wait_for(task, 1)
        assert result["status"] == "stopped"
        assert closed == [True]

    asyncio.run(scenario())


def test_manual_stop_in_stress_keeps_completed_speed_results():
    async def scenario():
        stop = threading.Event()
        checkpoints = []

        def handle(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion"]})
            return httpx.Response(
                200, json={"done": True, "eval_count": 10, "eval_duration": 1_000_000_000}
            )

        def progress(value):
            if value.get("phase") == "stress":
                stop.set()

        result = await measure(
            None,
            "model",
            "cpu",
            4096,
            1,
            progress,
            stop,
            {"queue_size": 1, "queue_timeout": 1, "queue_users": 0},
            checkpoints.append,
            transport=httpx.MockTransport(handle),
        )
        assert result["status"] == "completed"
        assert len(result["cases"]) == 6
        assert result["stress"]["status"] == "stopped"
        assert result["tokens_per_second"] == 10
        assert checkpoints

    asyncio.run(scenario())


def test_stop_api_is_admin_only_and_releases_exclusive_job(multiuser, monkeypatch):
    import time

    app, admin, user = multiuser
    assert user.post("/api/benchmark/stop").status_code == 403
    assert admin.post("/api/benchmark/stop").status_code == 409
    entered = threading.Event()

    async def fake(runtime, model, target, context, parallel, progress, stop, *args):
        entered.set()
        while not stop.is_set():
            await asyncio.sleep(0.01)
        return {"model": model, "target": target, "status": "stopped", "kind": "chat", "cases": []}

    monkeypatch.setattr("llameros.app.measure", fake)
    assert (
        admin.post("/api/benchmark", json={"model": "gemma3:270m", "target": "cpu"}).status_code
        == 202
    )
    assert entered.wait(1)
    assert admin.post("/api/benchmark/stop").status_code == 200
    for _ in range(100):
        if app.state.jobs.status()["state"] != "running":
            break
        time.sleep(0.01)
    assert app.state.jobs.status()["state"] == "completed"
    assert not app.state.jobs.scheduler.tickets
    assert app.state.store.activity()["admin_counts"]["failed"] == 0
    assert app.state.store.activity()["request_counts"]["total"] == 0
