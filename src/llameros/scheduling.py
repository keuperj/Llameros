"""Bounded per-device inference queues coordinated with Llameros lifecycle jobs.

Bounded, cancellation-safe per-device inference queues and lifecycle reservations.
"""

import asyncio
import time
from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class SchedulerSettings(BaseModel):
    """Persistent admin policy; zero distinct users preserves unrestricted admission."""

    model_config = ConfigDict(extra="forbid")
    queue_size: int = Field(default=16, ge=0, le=10000, strict=True)
    queue_timeout: int = Field(default=120, ge=1, le=86400, strict=True)
    ollama_num_parallel: int = Field(default=2, ge=1, le=16, strict=True)
    max_loaded_models: int = Field(default=2, ge=1, le=16, strict=True)
    queue_users: int = Field(default=0, ge=0, le=10000, strict=True)
    confirmed: bool = Field(default=False, strict=True, exclude=True)


@dataclass(eq=False)
class Ticket:
    """Identity-based reservation for one queued or executing request on a hardware target."""

    target: str
    model: str
    queued: float
    active: bool = False
    username: str = ""


class InferenceScheduler:
    """FIFO admission per device using the lifecycle job lock to prevent conflicting operations."""

    def __init__(self, jobs, capacity=16, timeout=120, parallel=2):
        """Bind job coordination with a waiting capacity per target and timeout in seconds."""
        self.jobs = jobs
        self.capacity = capacity
        self.timeout = timeout
        self.parallel = parallel
        self.queue_users = 0
        self.tickets = []
        self.dispatch_sequence = 0

    def conflicts(self, targets=None):
        """Check outstanding reservations against targets; callers coordinate with the job lock."""
        return any(targets is None or ticket.target in targets for ticket in self.tickets)

    async def acquire(self, target, model, request=None, username=""):
        """Reserve a target or the least busy eligible replica, then wait for its FIFO permit.

        Reject lifecycle conflicts, queue overflow, disconnects, or timeout with HTTP errors;
        cancellation releases the reservation.
        """
        candidates = (target,) if isinstance(target, str) else tuple(target)
        with self.jobs.lock:
            job = self.jobs.job
            if job["state"] == "running" and job.get("action") == "benchmark":
                raise HTTPException(
                    503, "Temporarily unavailable due to benchmark", headers={"Retry-After": "5"}
                )
            if job["state"] == "running" and job.get("action") != "api-inference":
                targets = job.get("targets")
                candidates = tuple(
                    t for t in candidates if targets is not None and t not in targets
                )
                if not candidates:
                    raise HTTPException(409, "A lifecycle operation is using this hardware")
            if len(candidates) > 1:
                offset = self.dispatch_sequence % len(candidates)
                candidates = candidates[offset:] + candidates[:offset]
                self.dispatch_sequence += 1
            eligible = []
            user_limited = False
            for candidate in candidates:
                active = sum(t.target == candidate and t.active for t in self.tickets)
                waiting = [t for t in self.tickets if t.target == candidate and not t.active]
                if active + len(waiting) >= self.capacity + self.parallel:
                    continue
                will_wait = bool(waiting) or active >= self.parallel
                users = {t.username for t in waiting}
                if (
                    will_wait
                    and self.queue_users
                    and username not in users
                    and len(users) >= self.queue_users
                ):
                    user_limited = True
                    continue
                eligible.append((will_wait, active + len(waiting), candidate))
            if not eligible:
                raise HTTPException(
                    429,
                    "Distinct-user queue limit reached"
                    if user_limited
                    else "Hardware inference queue is full",
                    headers={"Retry-After": "1"},
                )
            will_wait, _, target = min(eligible, key=lambda entry: entry[:2])
            ticket = Ticket(target, model, time.monotonic(), username=username)
            if will_wait and self.jobs.store is not None:
                self.jobs.store.queue_peak(
                    [t.username for t in self.tickets if not t.active] + [username]
                )
            self.tickets.append(ticket)
        try:
            while True:
                if request is not None and await request.is_disconnected():
                    raise HTTPException(499, "Client disconnected while queued")
                with self.jobs.lock:
                    active = sum(t.target == target and t.active for t in self.tickets)
                    first = next(t for t in self.tickets if t.target == target and not t.active)
                    if first is ticket and active < self.parallel:
                        ticket.active = True
                        job = self.jobs.job
                        if job["state"] != "running" or job.get("action") == "api-inference":
                            self.jobs.job = {
                                "state": "running",
                                "action": "api-inference",
                                "model": model,
                                "started": time.time(),
                                "progress": {"status": "API inference"},
                            }
                        return ticket
                if time.monotonic() - ticket.queued >= self.timeout:
                    raise HTTPException(504, "Timed out waiting for hardware inference queue")
                await asyncio.sleep(0.02)
        except BaseException:
            self.release(ticket, "failed")
            raise

    def release(self, ticket, outcome):
        """Idempotently remove a ticket and finish API job status when no reservations remain."""
        with self.jobs.lock:
            if ticket in self.tickets:
                self.tickets.remove(ticket)
            if self.jobs.job.get("action") == "api-inference" and not self.tickets:
                self.jobs.job.update(
                    state="completed" if outcome == "completed" else "failed",
                    finished=time.time(),
                    error="" if outcome == "completed" else "API request failed or interrupted",
                )
