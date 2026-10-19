"""Job execution with unique keys, run history and an audit trail.

A job is identified by `job_key`; a second run with the same key while the first is active is
recorded as `skipped` rather than executed twice. Failures are recorded with the error and leave
previously stored snapshots untouched.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import LockNotAcquired
from app.core.container import Container
from app.db.models import AuditEvent, JobRun
from app.observability.metrics import JOB_DURATION, JOB_RUNS

log = structlog.get_logger()
JobFn = Callable[[Container, AsyncSession], Awaitable[dict[str, Any] | None]]
_tasks: set[asyncio.Task[Any]] = set()


async def audit(s: AsyncSession, actor: str, action: str, entity: str | None = None, entity_id: Any = None,
                detail: dict[str, Any] | None = None, request_id: str | None = None) -> None:
    s.add(AuditEvent(actor=actor, action=action, entity=entity, entity_id=None if entity_id is None else str(entity_id),
                     detail=detail, request_id=request_id))


async def create_run(c: Container, kind: str, job_key: str, trigger: str, requested_by: str | None = None) -> int:
    async with c.sessions() as s:
        run = JobRun(kind=kind, job_key=job_key, trigger=trigger, status="running", requested_by=requested_by, started_at=c.clock.now())
        s.add(run)
        await s.commit()
        return run.id


async def execute(c: Container, run_id: int, fn: JobFn) -> dict[str, Any]:
    async with c.sessions() as s:
        run = await s.get(JobRun, run_id)
        assert run is not None
        kind, key = run.kind, run.job_key
    status, detail, error = "succeeded", None, None
    t0 = c.clock.monotonic()
    try:
        async with c.cache.lock(f"job:{key}"):
            async with c.sessions() as s:
                detail = await fn(c, s)
    except LockNotAcquired:
        status, error = "skipped", "another run with the same job key is in progress"
    except Exception as e:  # recorded, never swallowed silently
        status, error = "failed", f"{type(e).__name__}: {e}"[:1000]
        log.error("job_failed", kind=kind, job_key=key, error=error, trace=traceback.format_exc()[-1500:])
    dur = c.clock.monotonic() - t0
    JOB_RUNS.labels(kind=kind, status=status).inc()
    JOB_DURATION.labels(kind=kind).observe(dur)
    async with c.sessions() as s:
        run = await s.get(JobRun, run_id)
        assert run is not None
        run.status, run.detail, run.error, run.finished_at = status, detail, error, c.clock.now()
        await s.commit()
    log.info("job_finished", kind=kind, job_key=key, status=status, seconds=round(dur, 2))
    return {"job_id": run_id, "kind": kind, "status": status, "detail": detail, "error": error}


async def run(c: Container, kind: str, job_key: str, fn: JobFn, trigger: str = "scheduled", requested_by: str | None = None) -> dict[str, Any]:
    return await execute(c, await create_run(c, kind, job_key, trigger, requested_by), fn)


async def start_background(c: Container, kind: str, job_key: str, fn: JobFn, trigger: str = "manual", requested_by: str | None = None) -> int:
    """Create the run record now and execute it in the background; the caller polls the job id."""
    run_id = await create_run(c, kind, job_key, trigger, requested_by)
    task = asyncio.create_task(execute(c, run_id, fn))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run_id


async def mark_interrupted(c: Container) -> int:
    """Record runs left 'running' by a process that stopped mid-job. A run whose job lock is still
    held (in this process, or in Redis for another process) is genuinely running and is left alone."""
    async with c.sessions() as s:
        stale = [r for r in (await s.scalars(select(JobRun).where(JobRun.status == "running"))).all()
                 if not await c.cache.is_locked(f"job:{r.job_key}")]
        for r in stale:
            r.status, r.finished_at = "failed", c.clock.now()
            r.error = "interrupted: the process stopped before this job finished; run it again"
        await s.commit()
    if stale:
        log.info("jobs_marked_interrupted", count=len(stale))
    return len(stale)


async def last_runs(s: AsyncSession, limit: int = 50, kind: str | None = None) -> list[JobRun]:
    q = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    if kind:
        q = q.where(JobRun.kind == kind)
    return list((await s.scalars(q)).all())


def to_dict(r: JobRun) -> dict[str, Any]:
    return {"job_id": r.id, "kind": r.kind, "job_key": r.job_key, "trigger": r.trigger, "status": r.status,
            "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "detail": r.detail, "error": r.error, "requested_by": r.requested_by}
