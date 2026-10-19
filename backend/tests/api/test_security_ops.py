"""Security, scheduling and refresh-integrity tests (demo application, synthetic data)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.resilience import FakeClock, SlidingWindowLimiter
from app.db.models import JobRun, SectorRotationSnapshot, ThemeSnapshot, ThemeSnapshotConstituent
from app.domain.rrg import RRGError
from app.observability.logging import redact
from app.services import jobs, pipeline, sector_rotation, themes
from app.workers.scheduler import JOBS, build_triggers

pytestmark = pytest.mark.usefixtures("broker_logged_in")
API = "/api/v1"


# ------------------------------------------------------------ authentication and limits
async def test_bearer_token_is_required_when_configured(demo):
    async with demo.make_client(api_auth_token="s3cret-token-value") as (cl, _c):
        assert (await cl.get("/health/live")).status_code == 200, "liveness needs no token"
        r = await cl.get(API + "/instruments")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized" and r.headers["www-authenticate"] == "Bearer"
        assert (await cl.get(API + "/instruments", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await cl.post(API + "/jobs/refresh-all", headers={"Authorization": "Basic s3cret-token-value"})).status_code == 401
        ok = await cl.get(API + "/instruments", headers={"Authorization": "Bearer s3cret-token-value"})
        assert ok.status_code == 200
        st = await cl.get(API + "/sources/status", headers={"Authorization": "Bearer s3cret-token-value"})
        assert "s3cret-token-value" not in st.text


async def test_costly_endpoints_are_rate_limited(demo):
    async with demo.make_client(rate_limit_research_per_minute=2) as (cl, _c):
        codes = [(await cl.post(API + "/research/stock", json={"instrument": "NSE:DEMOIT02"})).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = await cl.post(API + "/research/stock", json={"instrument": "NSE:DEMOIT02"})
        assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1


def test_sliding_window_limiter_recovers_after_the_window():
    clock = FakeClock()
    lim = SlidingWindowLimiter(2, 60, clock)
    assert [lim.allow("k")[0] for _ in range(3)] == [True, True, False]
    assert lim.allow("other")[0] is True
    clock.advance(61)
    assert lim.allow("k")[0] is True


async def test_oversized_request_is_rejected(demo):
    r = await demo.client.post(API + "/themes/interpret", content=b"x" * 10, headers={"content-length": str(50_000_000), "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"


async def test_no_secret_or_order_surface_is_exposed(demo):
    doc = (await demo.client.get("/openapi.json")).json()
    assert len(doc["paths"]) >= 35
    assert not [p for p in doc["paths"] if any(w in p for w in ("order", "trade", "holding", "position", "portfolio"))]
    assert "place_order" not in json.dumps(doc)
    st = json.dumps((await demo.client.get(API + "/sources/status")).json())
    for needle in ("llm_api_key", "api_secret", "access_token", "Authorization"):
        assert needle not in st


def test_log_redaction_removes_tokens_and_secret_fields():
    out = redact({"authorization": "Bearer abcdefghijklmnop", "note": "sent Bearer abcdefghijklmnop to host", "key": "sk-ABCDEFGHIJKL",
                  "nested": {"api_key": "k", "ok": 1}, "kite": "token key123:ACCESSTOKEN99"})
    s = json.dumps(out)
    assert "abcdefghijklmnop" not in s and "sk-ABCDEFGHIJKL" not in s and "ACCESSTOKEN99" not in s
    assert out["authorization"] == "[REDACTED]" and out["nested"] == {"api_key": "[REDACTED]", "ok": 1}


def test_settings_do_not_print_secrets():
    s = Settings(_env_file=None, llm_api_key="sk-SECRETVALUE", api_auth_token="AT", web_search_api_key="WK")
    for secret in ("sk-SECRETVALUE", "'AT'", "'WK'"):
        assert secret not in repr(s)


def test_live_mode_never_points_at_mock_servers():
    live = Settings(_env_file=None, data_mode="live")
    assert live.resolved_mcp_urls() == {"nse_bhavcopy": None, "nse_cm_market": None, "broker": None}
    demo_urls = Settings(_env_file=None, data_mode="demo").resolved_mcp_urls()
    assert all(u.startswith("http://127.0.0.1:") for u in demo_urls.values())


# ------------------------------------------------------------ scheduling with a controllable clock
def test_schedule_defaults_are_twelve_hourly_rrg_and_monthly_themes():
    trig = build_triggers(Settings(_env_file=None))
    assert set(trig) == set(JOBS) == {"instruments", "prices", "rrg", "themes", "alerts"}
    start = datetime(2026, 10, 12, 0, 30, tzinfo=UTC)
    fires, t = [], start
    for _ in range(4):
        t = trig["rrg"].get_next_fire_time(None, t + timedelta(seconds=1))
        fires.append(t)
    assert {(b - a) for a, b in zip(fires, fires[1:], strict=False)} == {timedelta(hours=12)}
    m, t = [], start
    for _ in range(3):
        t = trig["themes"].get_next_fire_time(None, t + timedelta(seconds=1))
        m.append(t)
    assert [(x.year, x.month, x.day) for x in m] == [(2026, 11, 1), (2026, 12, 1), (2027, 1, 1)]
    assert str(m[0].tzinfo) == "Asia/Kolkata"


def test_rrg_slot_is_a_twelve_hour_bucket():
    assert sector_rotation.slot_for(datetime(2026, 10, 12, 3, 0, tzinfo=UTC)) == "2026-10-12T00"
    assert sector_rotation.slot_for(datetime(2026, 10, 12, 11, 59, tzinfo=UTC)) == "2026-10-12T00"
    assert sector_rotation.slot_for(datetime(2026, 10, 12, 12, 0, tzinfo=UTC)) == "2026-10-12T12"


async def test_rrg_refresh_is_idempotent_within_a_slot_and_adds_a_snapshot_in_the_next(demo):
    c = demo.container
    real = c.clock
    clock = FakeClock(datetime(2026, 10, 10, 1, 0, tzinfo=UTC))  # in the past, so the live snapshot stays the latest
    c.clock = clock
    try:
        async with c.sessions() as s:
            before = await s.scalar(select(func.count()).select_from(SectorRotationSnapshot))
            a = await sector_rotation.refresh(c, s)
            b = await sector_rotation.refresh(c, s)
            assert a["snapshot_id"] == b["snapshot_id"] and b["created"] is False
            clock.advance(12 * 3600)
            n = await sector_rotation.refresh(c, s)
            assert n["snapshot_id"] != a["snapshot_id"] and n["created"] is True and n["slot"] == "2026-10-10T12"
            after = await s.scalar(select(func.count()).select_from(SectorRotationSnapshot))
            assert after - before <= 2
    finally:
        c.clock = real


async def test_failed_rrg_refresh_keeps_last_valid_snapshot_and_is_reported(demo, monkeypatch):
    c = demo.container
    before = (await demo.client.get(API + "/sector-rotation")).json()

    def boom(*a, **k):
        raise RRGError("benchmark history is unavailable")

    monkeypatch.setattr(sector_rotation, "compute", boom)
    res = await jobs.run(c, "rrg", "rrg", pipeline.rrg_job, "scheduled")
    assert res["status"] == "failed" and "benchmark history" in res["error"]
    monkeypatch.undo()
    after = (await demo.client.get(API + "/sector-rotation")).json()
    assert after["snapshot_id"] == before["snapshot_id"] and after["sectors"] == before["sectors"]
    assert "last valid snapshot" in after["last_refresh_failed"]["note"] and after["last_refresh_failed"]["error"]
    async with c.sessions() as s:
        run = await s.get(JobRun, res["job_id"])
        assert run.status == "failed" and run.finished_at is not None


async def test_concurrent_runs_of_one_job_key_do_not_both_execute(demo):
    c = demo.container
    started = asyncio.Event()
    release = asyncio.Event()
    ran = []

    async def slow(_c, _s):
        ran.append(1)
        started.set()
        await release.wait()
        return {"ok": True}

    first = asyncio.create_task(jobs.run(c, "test", "same-key", slow))
    await started.wait()
    second = await jobs.run(c, "test", "same-key", slow)
    release.set()
    assert (await first)["status"] == "succeeded" and second["status"] == "skipped" and len(ran) == 1
    assert "in progress" in second["error"]


async def test_monthly_theme_refresh_adds_snapshots_without_touching_history(demo):
    c = demo.container
    real = c.clock
    async with c.sessions() as s:
        theme = await s.scalar(select(themes.Theme).where(themes.Theme.slug == "price-momentum"))
        old = await themes.current_snapshot(s, theme.id)
        frozen = [(k.symbol, k.rank, str(k.score), str(k.price_at_snapshot)) for k in old.constituents]
        old_id, old_period = old.id, old.period
    c.clock = FakeClock(datetime(2026, 11, 1, 2, 0, tzinfo=UTC))
    try:
        async with c.sessions() as s:
            out = await themes.refresh_all(c, s, "scheduled")
            assert out["period"] == "2026-11" and out["refreshed"] >= 60 and out["failed"] == []
        async with c.sessions() as s:
            new = await themes.current_snapshot(s, theme.id)
            assert new.id != old_id and new.period == "2026-11" and new.revision == 1
            kept = (await s.scalars(select(ThemeSnapshotConstituent).where(ThemeSnapshotConstituent.snapshot_id == old_id)
                                    .order_by(ThemeSnapshotConstituent.rank))).all()
            assert [(k.symbol, k.rank, str(k.score), str(k.price_at_snapshot)) for k in kept] == frozen
            assert (await s.get(ThemeSnapshot, old_id)).period == old_period
    finally:
        c.clock = real


async def test_restart_does_not_duplicate_seed_data(demo):
    async with demo.make_client() as (cl, _c):
        d = (await cl.get(API + "/themes")).json()
        slugs = [t["slug"] for t in d["items"]]
        assert len(slugs) == len(set(slugs))
        inst = (await cl.get(API + "/instruments", params={"page_size": 200})).json()
        assert inst["total"] == 59
        assert (await cl.get(API + "/overview")).json()["universe"]["instruments"] == 48, "stored data is served after a restart"


async def test_unconfigured_live_mode_reports_every_source_and_shows_no_data(tmp_path):
    """Provider-outage scenario: live mode with no connector configured must start, say what is
    missing, and show no instruments or prices rather than inventing any."""
    import httpx

    from app.core.container import build_container
    from app.main import create_app

    s = Settings(_env_file=None, app_env="test", data_mode="live", database_url=f"sqlite+aiosqlite:///{tmp_path}/live.db", auto_create_schema=True,
                 scheduler_enabled=False, fundamentals_dir=tmp_path / "f", universe_file=tmp_path / "missing.csv")
    c = build_container(s)
    app = create_app(s, c, run_startup_jobs=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cl:
            st = (await cl.get(API + "/sources/status")).json()
            by = {x["key"]: x for x in st["connectors"]}
            assert by["nse_bhavcopy"]["state"] == "not_configured" and by["kite_mcp"]["state"] == "not_configured"
            assert st["is_demo"] is False and "not configured" in st["capabilities"]["nse_mcp"]["history"]["reason"]
            assert by["web_search"]["name"] == "ddgs" and by["web_search"]["state"] == "ok", "live mode uses the keyless provider by default"
            ov = (await cl.get(API + "/overview")).json()
            assert ov["data_state"] == "missing" and ov["universe"]["instruments"] == 0 and ov["benchmark"] is None
            assert (await cl.get(API + "/screener")).json()["items"] == []
            rot = (await cl.get(API + "/sector-rotation")).json()
            assert rot["data_state"] == "missing" and rot["sectors"] == {}
            run = await jobs.run(c, "full_refresh", "full_refresh", pipeline.full_refresh, "test")
            assert run["status"] == "succeeded" and run["detail"]["prices"]["bars"] == 0
            assert (await cl.get(API + "/instruments")).json()["total"] == 0
    await c.close()


def test_env_example_as_shipped_gives_safe_working_defaults():
    """`cp .env.example .env` must produce a runnable demo: blank values mean "not set"."""
    from pathlib import Path

    s = Settings(_env_file=Path(__file__).parents[3] / ".env.example")
    assert s.api_auth_token is None, "a blank API_AUTH_TOKEN must not switch authentication on"
    assert s.redis_url is None and s.llm_api_key is None and s.web_search_api_key is None
    assert s.database_url and s.llm_base_url and s.llm_model_id, "endpoints come from .env, not from code"
    assert s.data_mode == "demo" and s.llm_enabled is False and s.web_search_provider == "ddgs"
    assert s.rrg_refresh_cron == "0 */12 * * *" and s.theme_refresh_cron == "30 6 1 * *"


async def test_blank_auth_token_does_not_lock_the_api(demo):
    async with demo.make_client(api_auth_token="") as (cl, _c):
        assert (await cl.get(API + "/instruments")).status_code == 200


async def test_unreachable_redis_falls_back_locally_and_says_so(demo):
    async with demo.make_client(redis_url="redis://127.0.0.1:6399/0") as (cl, c):
        r = (await cl.get("/health/ready")).json()
        assert r["status"] == "ready" and "in-process" in r["checks"]["cache"] and "Redis" in r["checks"]["cache"]
        assert (await cl.get(API + "/overview")).status_code == 200
        ran = await jobs.run(c, "alerts", "alerts-fallback-test", pipeline.alerts_job, "test")
        assert ran["status"] == "succeeded", "job locks work on the fallback cache"


async def test_unreachable_redis_is_fatal_to_readiness_outside_local(demo):
    async with demo.make_client(redis_url="redis://127.0.0.1:6399/0", app_env="production") as (cl, _c):
        r = await cl.get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"]["cache"].startswith("error")
