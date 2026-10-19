"""Shared fixtures. The MCP servers started here are the DEMO mocks (synthetic data); tests that
use them are contract tests of our connector code, not proof that the real upstreams work."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Iterator

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


def _free_base_port() -> int:
    """Three consecutive free ports, chosen below the OS ephemeral range."""
    import random

    for _ in range(200):
        port = random.randint(20000, 45000)
        try:
            for off in (0, 1, 2):
                with socket.socket() as t:
                    t.bind(("127.0.0.1", port + off))
            return port
        except OSError:
            continue
    raise RuntimeError("no free port triple")


@pytest.fixture(scope="session")
def mock_mcp() -> Iterator[dict[str, str]]:
    from app.devtools import mock_mcp as mm

    port = _free_base_port()
    servers = mm.make_uvicorn_servers(port)
    loop = asyncio.new_event_loop()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(*(s.serve() for s in servers)))

    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 20
    while not all(s.started for s in servers):
        if time.time() > deadline:
            raise RuntimeError("mock MCP servers did not start")
        time.sleep(0.05)
    yield {"bhavcopy": f"http://127.0.0.1:{port}/mcp", "cm": f"http://127.0.0.1:{port + 1}/mcp",
           "kite": f"http://127.0.0.1:{port + 2}/mcp", "kite_authorize": f"http://127.0.0.1:{port + 2}/authorize?token=demo",
           "port": str(port)}
    for s in servers:
        s.should_exit = True
    th.join(timeout=5)


@pytest.fixture
def mock_state(mock_mcp):
    from app.devtools.mock_mcp import STATE

    STATE.kite_logged_in = False
    STATE.forbidden_calls.clear()
    STATE.calls.clear()
    STATE.fail_next.clear()
    STATE.kite_forget_session = False
    STATE.link_ttl_seconds = 300.0
    return STATE


# ---------------------------------------------------------------- full demo application
import contextlib  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402


@dataclass
class Demo:
    container: Any
    app: Any
    client: httpx.AsyncClient
    settings: Any
    refresh: dict
    make_client: Any


@pytest_asyncio.fixture(scope="session")
async def demo(mock_mcp, tmp_path_factory):
    """The real application in demo mode: MCP connectors -> mock servers, SQLite, full pipeline run once."""
    from app.core.config import Settings
    from app.core.container import build_container
    from app.devtools.mock_mcp import STATE
    from app.main import create_app
    from app.services import jobs, pipeline

    tmp = tmp_path_factory.mktemp("demo")
    # TEST_DATABASE_URL runs the same suite on another database (e.g. PostgreSQL). That database must
    # be empty and already migrated with Alembic; the app then does not create tables itself.
    external = os.environ.get("TEST_DATABASE_URL")
    base = dict(_env_file=None, app_env="test", data_mode="demo", database_url=external or f"sqlite+aiosqlite:///{tmp}/app.db",
                auto_create_schema=not external, redis_url=os.environ.get("TEST_REDIS_URL") or None,
                mock_mcp_port=int(mock_mcp["port"]), scheduler_enabled=False, fundamentals_dir=tmp / "fund", history_years=2,
                rate_limit_research_per_minute=10_000, rate_limit_refresh_per_minute=10_000, rate_limit_default_per_minute=100_000)
    settings = Settings(**base)
    STATE.kite_logged_in = True
    container = build_container(settings)
    app = create_app(settings, container, run_startup_jobs=False)
    stack = contextlib.AsyncExitStack()
    await stack.enter_async_context(app.router.lifespan_context(app))
    result = await jobs.run(container, "full_refresh", "full_refresh", pipeline.full_refresh, "test")
    assert result["status"] == "succeeded", result
    client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"))

    @contextlib.asynccontextmanager
    async def make_client(**overrides):
        s2 = Settings(**{**base, **overrides})
        c2 = build_container(s2)
        app2 = create_app(s2, c2, run_startup_jobs=False)
        async with app2.router.lifespan_context(app2):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as cl:
                yield cl, c2
        await c2.close()

    yield Demo(container, app, client, settings, result, make_client)
    await stack.aclose()
    await container.close()


@pytest.fixture
def broker_logged_in(mock_mcp):
    from app.devtools.mock_mcp import STATE

    STATE.kite_logged_in = True
    yield
    STATE.kite_logged_in = True
