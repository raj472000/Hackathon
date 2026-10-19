"""RedisCache against a real Redis. Runs only when TEST_REDIS_URL is set, e.g.
TEST_REDIS_URL=redis://127.0.0.1:6379/15 uv run pytest tests/services/test_redis_cache.py"""

import asyncio
import os

import pytest

from app.core.cache import LockNotAcquired, RedisCache

URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_REDIS_URL is not set")


async def test_values_round_trip_expire_and_clear_by_prefix():
    c = RedisCache(URL)
    await c.delete_prefix("t:")
    assert await c.get("t:a") is None
    await c.set("t:a", {"value": 1012.5, "provider": "nse_mcp", "fields": [1, 2]}, 30)
    await c.set("t:b", 2, 1)
    await c.set("other:c", 3, 30)
    assert await c.get("t:a") == {"value": 1012.5, "provider": "nse_mcp", "fields": [1, 2]}
    await asyncio.sleep(1.2)
    assert await c.get("t:b") is None, "the entry expired"
    await c.delete_prefix("t:")
    assert await c.get("t:a") is None and await c.get("other:c") == 3
    assert c.hits >= 2 and c.misses >= 3
    await c.delete_prefix("other:")
    await c.close()


async def test_lock_is_exclusive_across_connections_and_released():
    a, b = RedisCache(URL), RedisCache(URL)  # two clients, as the API and the worker would be
    async with a.lock("job:test-exclusive", ttl=30):
        with pytest.raises(LockNotAcquired):
            async with b.lock("job:test-exclusive", ttl=30):
                pass
    async with b.lock("job:test-exclusive", ttl=30):
        pass
    await a.close()
    await b.close()


async def test_lock_is_released_when_the_job_raises():
    c = RedisCache(URL)
    with pytest.raises(RuntimeError):
        async with c.lock("job:test-raises", ttl=30):
            raise RuntimeError("job failed")
    async with c.lock("job:test-raises", ttl=30):
        pass
    await c.close()
