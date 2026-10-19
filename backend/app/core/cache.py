"""Cache and lock abstraction: in-memory locally, Redis when REDIS_URL is set.

Only successful values are cached. Callers must not pass error results to `set`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    async def delete_prefix(self, prefix: str) -> None: ...
    def lock(self, key: str, ttl: int = 600) -> Any: ...
    async def is_locked(self, key: str) -> bool: ...
    async def close(self) -> None: ...


class LockNotAcquired(RuntimeError):
    pass


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: set[str] = set()
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self.hits = self.misses = 0

    async def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item and item[0] > time.monotonic():
            self.hits += 1
            return item[1]
        self._data.pop(key, None)
        self.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (time.monotonic() + ttl, value)

    async def delete_prefix(self, prefix: str) -> None:
        for k in [k for k in self._data if k.startswith(prefix)]:
            del self._data[k]

    @asynccontextmanager
    async def lock(self, key: str, ttl: int = 600) -> AsyncIterator[None]:
        if key in self._locks:
            raise LockNotAcquired(key)
        self._locks.add(key)
        try:
            yield
        finally:
            self._locks.discard(key)

    async def is_locked(self, key: str) -> bool:
        return key in self._locks

    async def close(self) -> None:
        self._data.clear()


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._r = redis.from_url(url, decode_responses=True)
        self.hits = self.misses = 0

    async def get(self, key: str) -> Any | None:
        raw = await self._r.get(key)
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self._r.set(key, json.dumps(value, default=str), ex=ttl)

    async def delete_prefix(self, prefix: str) -> None:
        async for k in self._r.scan_iter(match=prefix + "*"):
            await self._r.delete(k)

    @asynccontextmanager
    async def lock(self, key: str, ttl: int = 600) -> AsyncIterator[None]:
        if not await self._r.set(f"lock:{key}", "1", nx=True, ex=ttl):
            raise LockNotAcquired(key)
        try:
            yield
        finally:
            await self._r.delete(f"lock:{key}")

    async def is_locked(self, key: str) -> bool:
        return bool(await self._r.exists(f"lock:{key}"))

    async def close(self) -> None:
        await self._r.aclose()


def build_cache(redis_url: str | None) -> MemoryCache | RedisCache:
    return RedisCache(redis_url) if redis_url else MemoryCache()


class Coalescer:
    """Deduplicates concurrent identical fetches: callers for the same key share one in-flight call."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    async def run(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))  # type: ignore[misc]
        return await asyncio.shield(task)
