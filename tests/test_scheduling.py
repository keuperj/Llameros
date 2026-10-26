"""Offline scheduling regression tests for Llameros behavior and integration boundaries."""

import asyncio

import pytest
from fastapi import HTTPException

from llameros.app import Jobs


@pytest.mark.parametrize("parallel", [2, 4])
def test_parallel_per_device_fifo_and_cleanup(parallel):
    async def scenario():
        jobs = Jobs(None, parallel=parallel)
        scheduler = jobs.scheduler
        scheduler.capacity = 2
        active = [await scheduler.acquire("gpu", "model") for _ in range(parallel)]
        assert all(ticket.active for ticket in active)
        waiting = asyncio.create_task(scheduler.acquire("gpu", "other-model"))
        await asyncio.sleep(0.03)
        later = asyncio.create_task(scheduler.acquire("gpu", "model"))
        await asyncio.sleep(0.03)
        assert not waiting.done() and not later.done()
        with pytest.raises(HTTPException) as error:
            await scheduler.acquire("gpu", "overflow")
        assert error.value.status_code == 429
        cpu = await scheduler.acquire("cpu", "model")
        # Release out of order: any completed active request frees one permit.
        scheduler.release(active.pop(), "completed")
        admitted = await asyncio.wait_for(waiting, 1)
        assert admitted.active and not later.done()
        with pytest.raises(HTTPException):
            jobs.submit("unload", "model", lambda p: None, targets={"gpu"})
        later.cancel()
        with pytest.raises(asyncio.CancelledError):
            await later
        for ticket in [*active, admitted, cpu]:
            scheduler.release(ticket, "completed")
        assert not scheduler.tickets
        jobs.pool.shutdown()

    asyncio.run(scenario())


def test_fifo_separate_devices_queue_limit_timeout_and_cancellation():
    """Verify fifo separate devices queue limit timeout and cancellation."""

    async def scenario():
        jobs = Jobs(None, parallel=1)
        scheduler = jobs.scheduler
        scheduler.capacity, scheduler.timeout = 1, 0.06
        first = await scheduler.acquire("gpu", "llm")
        waiting = asyncio.create_task(scheduler.acquire("gpu", "embed"))
        await asyncio.sleep(0.01)
        with pytest.raises(HTTPException) as error:
            await scheduler.acquire("gpu", "third")
        assert error.value.status_code == 429
        cpu = await scheduler.acquire("cpu", "embed")
        assert cpu.active and not waiting.done()
        with pytest.raises(HTTPException) as error:
            jobs.submit("unload", "llm", lambda p: None, targets={"gpu"})
        assert error.value.status_code == 409
        scheduler.release(first, "completed")
        second = await waiting
        assert second.active
        with pytest.raises(HTTPException) as error:
            await scheduler.acquire("gpu", "timeout")
        assert error.value.status_code == 504
        cancelled = asyncio.create_task(scheduler.acquire("gpu", "cancel"))
        await asyncio.sleep(0.01)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        scheduler.release(cpu, "completed")
        scheduler.release(second, "completed")
        assert not scheduler.tickets
        assert jobs.status()["state"] == "completed"
        jobs.pool.shutdown()

    asyncio.run(scenario())


def test_scoped_lifecycle_and_disconnected_queue():
    """Verify scoped lifecycle and disconnected queue."""

    class Disconnected:
        async def is_disconnected(self):
            return True

    async def scenario():
        jobs = Jobs(None)
        jobs.job = {"state": "running", "action": "unload", "targets": ["gpu"]}
        with pytest.raises(HTTPException) as error:
            await jobs.scheduler.acquire("gpu", "llm")
        assert error.value.status_code == 409
        ticket = await jobs.scheduler.acquire("cpu", "embed")
        assert jobs.status()["action"] == "unload"
        with pytest.raises(HTTPException) as error:
            await jobs.scheduler.acquire("cpu", "embed", Disconnected())
        assert error.value.status_code == 499
        jobs.scheduler.release(ticket, "completed")
        assert not jobs.scheduler.tickets
        jobs.pool.shutdown()

    asyncio.run(scenario())
