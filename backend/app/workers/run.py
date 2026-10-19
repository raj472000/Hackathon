"""Standalone scheduler process (the `worker` service). Run: python -m app.workers.run"""

from __future__ import annotations

import asyncio
import signal

import structlog

from app.core.config import get_settings
from app.core.container import build_container
from app.main import init_database, seed
from app.observability.logging import configure
from app.services import jobs
from app.workers.scheduler import Scheduler


async def main() -> None:
    settings = get_settings()
    configure(settings.log_level)
    c = build_container(settings)
    await init_database(c)
    await c.start()
    await jobs.mark_interrupted(c)
    await seed(c)
    sched = Scheduler(c)
    sched.start()
    structlog.get_logger().info("worker_started", jobs=[j["id"] for j in sched.describe()])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    sched.shutdown()
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
