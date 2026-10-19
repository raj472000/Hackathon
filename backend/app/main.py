"""FastAPI application: lifespan wiring, middleware, error handling, health and metrics."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import market, user
from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.db.models import ConcallTranscript, Instrument, PriceBar
from app.db.session import create_all
from app.observability import metrics as om
from app.observability.logging import configure
from app.services import jobs, pipeline
from app.workers.scheduler import Scheduler

log = structlog.get_logger()
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Cache-Control": "no-store"}


class SchemaNotReady(RuntimeError):
    """The database schema is missing or older than this code expects."""


def _expected_revision() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.core.config import BACKEND_DIR

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def init_database(c: Container) -> None:
    """Make sure the schema is usable before serving.

    With AUTO_CREATE_SCHEMA=false (the default) the schema must come from Alembic. Instead of failing
    later on a missing table, start-up stops with the exact command to run."""
    if c.settings.auto_create_schema:
        await create_all(c.engine)
        await _record_revision(c, _expected_revision())
        return
    expected = _expected_revision()
    fix = "Run `cd backend && uv run alembic upgrade head` (scripts/dev.sh does this for you), then start again."
    try:
        async with c.engine.connect() as conn:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception as e:
        if "alembic_version" not in str(e) and "does not exist" not in str(e) and "no such table" not in str(e):
            raise SchemaNotReady(f"Cannot reach the database at {_safe_db(c)}: {type(e).__name__}: {str(e)[:200]}. "
                                 "Check DATABASE_URL and that PostgreSQL is running (`docker compose up -d postgres`).") from e
        raise SchemaNotReady(f"The database at {_safe_db(c)} has no tables: no migration has been applied. {fix}") from e
    if current != expected:
        raise SchemaNotReady(f"The database schema is at migration {current} but this code expects {expected}. {fix}")


async def _record_revision(c: Container, revision: str | None) -> None:
    """Tables created straight from the models match the latest migration, so record it. Otherwise a
    later `alembic upgrade head` tries to create them again and fails with "already exists"."""
    if revision is None:
        return
    async with c.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL, "
                                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
        if (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar() == 0:
            await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revision})


def _safe_db(c: Container) -> str:
    return c.engine.url.render_as_string(hide_password=True)


async def seed(c: Container) -> None:
    """The seed is idempotent, but the API and worker start together against a fresh database and can
    both insert the built-in themes at once. The loser gets a unique violation; running again sees
    the winner's rows."""
    try:
        await _seed(c)
    except IntegrityError:
        log.info("seed_retry_after_concurrent_insert")
        await _seed(c)


async def _seed(c: Container) -> None:
    async with c.sessions() as s:
        # Start-up must not wait on NSE: the scheduled instruments job refreshes industry labels.
        await pipeline.instruments_job(c, s, refresh_labels=False)
        if c.settings.is_demo:
            from app.devtools import demo_concalls as dc
            from app.services import concall

            inst = await s.scalar(select(Instrument).where(Instrument.symbol == dc.SYMBOL))
            have = await s.scalar(select(func.count()).select_from(ConcallTranscript))
            if inst is not None and not have:
                for fy, q, body in dc.TRANSCRIPTS:
                    await concall.store_transcript(s, inst, fy, q, body, dc.SOURCE, is_demo=True)


def create_app(settings: Settings | None = None, container: Container | None = None, run_startup_jobs: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure(settings.log_level)
        c = container or build_container(settings)
        app.state.container = c
        await init_database(c)
        await c.start()
        await jobs.mark_interrupted(c)
        await seed(c)
        if settings.scheduler_enabled:
            c.scheduler = Scheduler(c)
            c.scheduler.start()
        if run_startup_jobs and settings.auto_initial_refresh:
            async with c.sessions() as s:
                empty = not await s.scalar(select(func.count()).select_from(PriceBar))
            if empty:
                await jobs.start_background(c, "full_refresh", "full_refresh", pipeline.full_refresh, "initial")
            if c.llm.available:
                app.state.llm_check = asyncio.create_task(c.llm.check_availability())
            if c.kite.client is not None and not settings.is_demo:
                # Ask Kite for the session state now, so the login link is ready when the app first asks for it.
                app.state.broker_check = asyncio.create_task(c.kite.auth_state())
        log.info("startup", data_mode=settings.data_mode, app_env=settings.app_env, scheduler=settings.scheduler_enabled)
        try:
            yield
        finally:
            if c.scheduler:
                c.scheduler.shutdown()
            if container is None:
                await c.close()

    app = FastAPI(title="Capital Markets Intelligence API", version="0.1.0", lifespan=lifespan,
                  description="Research and screening API. It never places orders and its output is not investment advice.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["X-Request-ID", "Retry-After"])

    def error(status: int, code: str, message: str, request: Request, details: Any = None, headers: dict[str, str] | None = None) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return JSONResponse({"error": {"code": code, "message": message, "details": details, "request_id": rid}}, status_code=status,
                            headers={**(headers or {}), **SECURITY_HEADERS, "X-Request-ID": rid or ""})

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        rid = uuid.uuid4().hex[:16]
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        length = request.headers.get("content-length")
        limit = max(settings.max_request_bytes, user.MAX_UPLOAD_BYTES + 100_000) if "/concalls" in request.url.path else settings.max_request_bytes
        if length and length.isdigit() and int(length) > limit:
            return error(413, "request_too_large", "Request body is too large.", request)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        dur = time.perf_counter() - t0
        om.HTTP_REQUESTS.labels(method=request.method, route=path, status=str(response.status_code)).inc()
        om.HTTP_LATENCY.labels(route=path).observe(dur)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path not in ("/health/live", "/health", "/api/v1/health", "/metrics"):
            log.info("request", method=request.method, path=path, status=response.status_code, ms=round(dur * 1000, 1), request_id=rid)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        d = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail), "details": None}
        return error(exc.status_code, d.get("code", "http_error"), d.get("message", ""), request, d.get("details"), getattr(exc, "headers", None))  # type: ignore[arg-type]

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()[:10]]
        return error(422, "validation_error", "The request is invalid.", request, details)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=f"{type(exc).__name__}: {exc}"[:500], path=request.url.path)
        return error(500, "internal_error", "An unexpected error occurred. Quote the request id when reporting it.", request)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        # This port serves only the API; a browser opened here should be told where the app is, not shown a 404.
        return {"service": "api", "message": "This is the API. Open the web app instead.",
                "web_app": "http://127.0.0.1:5173 (local dev) or the frontend service port (Docker)", "docs": "/docs"}

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    # Aliases for the paths monitors, load balancers and people try first; same answer as /health/live.
    app.add_api_route("/health", live, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/v1/health", live, methods=["GET"], include_in_schema=False)

    @app.get("/health/ready", tags=["health"])
    async def ready(request: Request) -> JSONResponse:
        c: Container = request.app.state.container
        checks: dict[str, Any] = {}
        try:
            async with c.sessions() as s:
                await s.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {type(e).__name__}"
        try:
            await c.cache.set("health:ping", 1, 5)
            checks["cache"] = f"ok (in-process fallback: {c.cache_note})" if c.cache_note else "ok"
        except Exception as e:
            checks["cache"] = f"error: {type(e).__name__}"
        # Upstream data sources are reported but do not fail readiness: the API still serves stored data.
        checks["sources"] = {"nse_bhavcopy": c.nse.health()["nse_bhavcopy"]["state"], "nse_cm_market": c.nse.health()["nse_cm_market"]["state"],
                             "kite_mcp": c.kite.health()["state"]}
        ok = checks["database"] == "ok" and checks["cache"].startswith("ok")
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks, "data_mode": c.settings.data_mode},
                            status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus(request: Request) -> Response:
        c: Container = request.app.state.container
        for name, h in (("nse_bhavcopy", c.nse.health()["nse_bhavcopy"]), ("nse_cm_market", c.nse.health()["nse_cm_market"]), ("kite_mcp", c.kite.health())):
            om.CONNECTOR_STATE.labels(connector=name).set(1 if h.get("state") == "ok" else 0)
        u = c.llm.status()["usage"]
        om.LLM_TOKENS.set(u["total_tokens"])
        om.LLM_CALLS.set(u["calls"])
        hits, misses = getattr(c.cache, "hits", 0), getattr(c.cache, "misses", 0)
        om.CACHE_HIT_RATIO.set(hits / (hits + misses) if hits + misses else 0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(market.router, prefix="/api/v1")
    app.include_router(user.router, prefix="/api/v1")
    return app


app = create_app()
