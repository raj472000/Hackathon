"""Clock, rate limiting, circuit breaking and retry helpers shared by the connectors."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

T = TypeVar("T")


class Clock:
    """Injectable time source so schedulers and breakers can be tested without sleeping."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._mono = 0.0
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)


class MinIntervalLimiter:
    """Serialises calls so consecutive ones are at least `interval` seconds apart."""

    def __init__(self, interval: float, clock: Clock | None = None) -> None:
        self.interval = interval
        self.clock = clock or Clock()
        self._next = 0.0
        self._lock = asyncio.Lock()
        self.waits = 0

    async def acquire(self) -> None:
        if self.interval <= 0:
            return
        async with self._lock:
            now = self.clock.monotonic()
            if now < self._next:
                self.waits += 1
                await self.clock.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


class SlidingWindowLimiter:
    """Per-key request limiter for API endpoints: at most `limit` hits per `window` seconds."""

    def __init__(self, limit: int, window: float = 60.0, clock: Clock | None = None) -> None:
        self.limit, self.window, self.clock = limit, window, clock or Clock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        now = self.clock.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, self.window - (now - q[0])
        q.append(now)
        return True, 0.0


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; allows one trial call after `reset_seconds`."""

    def __init__(self, threshold: int = 5, reset_seconds: float = 60.0, clock: Clock | None = None) -> None:
        self.threshold, self.reset_seconds, self.clock = threshold, reset_seconds, clock or Clock()
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock.monotonic() - self.opened_at >= self.reset_seconds else "open"

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError("circuit open after repeated failures; retrying later")

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock.monotonic()


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int,
    is_retryable: Callable[[BaseException], bool],
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    clock: Clock | None = None,
) -> T:
    """Exponential backoff with full jitter. Non-retryable errors are raised immediately."""
    clock = clock or Clock()
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if attempt >= retries or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * 2**attempt) * (0.5 + random.random() / 2)
            attempt += 1
            await clock.sleep(delay)
