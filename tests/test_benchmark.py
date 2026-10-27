"""Offline bounded benchmark, exclusivity, privacy and persistence tests."""

import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi import HTTPException
from test_users import issue
from test_users import multiuser as multiuser

from llameros.benchmark import measure
from llameros.storage import Store


@pytest.mark.parametrize("kind", ["chat", "embedding"])
def test_workloads_metrics_and_persistence(tmp_path, kind):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"capabilities": ["completion" if kind == "chat" else "embedding"]}
            )
        if kind == "chat":
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "eval_count": 20,
                    "eval_duration": 1_000_000_000,
                    "message": {"content": "PRIVATE"},
                },
            )
        return httpx.Response(
            200, json={"prompt_eval_count": 30, "embeddings": [[0.1] for _ in body["input"]]}
        )

    result = asyncio.run(
        measure(
            None, "model", "cpu", 4096, 2, lambda p: None, transport=httpx.MockTransport(handle)
        )
    )
    assert result["status"] == "completed" and result["kind"] == kind
    assert len(result["cases"]) == 6
    assert result["tokens_per_second"] > 0
    if kind == "chat":
        assert result["tokens_per_second"] == 20
        assert len({body["options"]["num_predict"] for _, body in calls[1:]}) == 3
        assert len({len(body["messages"][0]["content"]) for _, body in calls[1:]}) > 2
    else:
        assert len({len(body["input"]) for _, body in calls[1:]}) > 1
    assert "PRIVATE" not in json.dumps(result)
    store = Store(tmp_path / "history.sqlite")
    store.save_benchmark(result)
    store.save_benchmark(result)
    assert Store(store.path).benchmarks() == [result]


def test_absolute_deadline_partial_results_and_errors():
    completed = 0

    async def handle(request):
        nonlocal completed
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        completed += 1
        if completed > 1:
            await asyncio.sleep(10)
        return httpx.Response(
            200, json={"done": True, "eval_count": 10, "eval_duration": 1_000_000_000}
        )

    started = time.monotonic()
    result = asyncio.run(
        measure(
            None,
            "model",
            "cpu",
            4096,
            2,
            lambda p: None,
            transport=httpx.MockTransport(handle),
            budget=0.05,
        )
    )
    assert time.monotonic() - started < 1
    assert result["status"] == "timed-out" and len(result["cases"]) == 1
    result = asyncio.run(
        measure(
            None,
            "model",
            "cpu",
            4096,
            2,
            lambda p: None,
            transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        )
    )
    assert result["status"] == "failed" and not result["cases"]


@pytest.mark.parametrize("status", ["completed", "timed-out", "failed"])
def test_admin_exclusivity_gateway_rejection_and_results(multiuser, monkeypatch, status):
    app, admin, user = multiuser
    key = issue(user)["key"]
    payload = {"model": "gemma3:270m", "target": "cpu"}
    assert user.post("/api/benchmark", json=payload).status_code == 403
    scheduler = app.state.jobs.scheduler
    ticket = asyncio.run(scheduler.acquire("cpu", payload["model"]))
    assert admin.post("/api/benchmark", json=payload).status_code == 409
    scheduler.release(ticket, "completed")

    entered, release = threading.Event(), threading.Event()

    async def fake(*args):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {
            "model": payload["model"],
            "target": "cpu",
            "kind": "chat",
            "status": status,
            "cases": [],
        }

    monkeypatch.setattr("llameros.app.measure", fake)
    try:
        assert admin.post("/api/benchmark", json=payload).status_code == 202
        assert entered.wait(1)
        response = user.post(
            "/inference/api/generate",
            headers={"Authorization": "Bearer " + key},
            json={"model": payload["model"], "prompt": "test"},
        )
        assert response.status_code == 503 and "due to benchmark" in response.text
        assert response.headers["retry-after"] == "5"
        with pytest.raises(HTTPException) as error:
            asyncio.run(scheduler.acquire("other-gpu", payload["model"]))
        assert error.value.status_code == 503
        assert admin.post("/api/benchmark", json=payload).status_code == 409
    finally:
        release.set()
    for _ in range(100):
        if app.state.jobs.status()["state"] != "running":
            break
        time.sleep(0.01)
    assert app.state.jobs.status()["state"] == "completed"
    assert admin.get("/api/history").json()["benchmarks"]
    assert user.get("/api/user/dashboard").json()["history"]["benchmarks"]
    ticket = asyncio.run(scheduler.acquire("cpu", payload["model"]))
    scheduler.release(ticket, "completed")


def test_stalled_socket_setup_does_not_extend_deadline():
    release = threading.Event()

    class Runtime:
        def socket_path(self):
            release.wait(2)
            raise RuntimeError("unavailable")

    started = time.monotonic()
    try:
        result = asyncio.run(
            measure(Runtime(), "model", "cpu", 4096, 2, lambda p: None, budget=0.03)
        )
        assert result["status"] == "timed-out"
        assert time.monotonic() - started < 1
    finally:
        release.set()
