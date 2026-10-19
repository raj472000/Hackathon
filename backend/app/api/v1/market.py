"""Market-data routes: sources, instruments, screener, sector rotation, overview, jobs, audit."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Response
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import case, func, select

from app.api.deps import Actor, ApiError, Ctx, Db, meta, rate_limit, resolve_instrument
from app.connectors.base import ProviderUnavailable
from app.connectors.fundamentals_file import FundamentalsValidationError
from app.db.models import (
    AuditEvent,
    CorporateActionRow,
    DataSourceRecord,
    FinancialPeriod,
    Instrument,
    JobRun,
    MetricSnapshot,
    PriceBar,
    ResearchReport,
    SectorRotationSnapshot,
    SourceConflict,
    Theme,
    ThemeSnapshot,
    ThemeSnapshotConstituent,
)
from app.domain import reconcile, rrg, scoring
from app.domain.theme_dsl import METRIC_CATALOG, Filter
from app.services import ask as ask_service
from app.services import ingestion, jobs, metrics, pipeline, screener, sector_rotation
from app.services.jobs import audit

router = APIRouter()
_filters = TypeAdapter(list[Filter])


def inst_dict(i: Instrument) -> dict[str, Any]:
    return {"id": i.id, "exchange": i.exchange, "symbol": i.symbol, "name": i.name, "isin": i.isin, "instrument_type": i.instrument_type,
            "currency": i.currency, "sector": i.sector, "industry": i.industry, "sector_kind": i.sector_kind, "lot_size": i.lot_size,
            "trading_status": i.trading_status, "classification_source": i.classification_source, "is_demo": i.is_demo,
            "identifiers": [{"provider": x.provider, "kind": x.kind, "value": x.value} for x in i.identifiers],
            "updated_at": i.updated_at.isoformat()}


# ------------------------------------------------------------------ sources
KITE_RECHECK_SECONDS = 30
KITE_AUTHORIZED_RECHECK_SECONDS = 600
KITE_RECHECK_TIMEOUT_SECONDS = 5.0


async def _kite_session(c: Any, actor: str, check: Any) -> dict[str, Any]:
    """Run a Kite session check; the first time it turns `authorized`, start the broker backfill."""
    before = c.kite.health().get("auth", {}).get("state")
    state = await check()
    if state.get("state") == "authorized" and before != "authorized":
        state = {**state, "backfill_job": await jobs.start_background(c, "broker_backfill", "broker_backfill", pipeline.broker_backfill_job,
                                                                      "broker_login", actor)}
    return state


async def _recheck_kite(c: Any, actor: str) -> None:
    """The status page must not show a stale "login required" after the user has approved access in Kite:
    when the session is not known to be authorised and the last check is older than KITE_RECHECK_SECONDS
    (KITE_AUTHORIZED_RECHECK_SECONDS when it is), ask Kite again (get_profile only; no new login link is issued). Bounded so the page never waits long."""
    auth = c.kite.health().get("auth") or {}
    if c.kite.client is None or auth.get("state") == "not_configured":
        return
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(auth["checked_at"])).total_seconds()  # the connector stamps wall-clock time
    except (KeyError, TypeError, ValueError):
        age = None
    # An authorised session is re-confirmed now and then too, so a session Kite has dropped is noticed.
    if age is not None and age < (KITE_AUTHORIZED_RECHECK_SECONDS if auth.get("state") == "authorized" else KITE_RECHECK_SECONDS):
        return
    try:
        await asyncio.wait_for(_kite_session(c, actor, c.kite.auth_state), KITE_RECHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        pass  # the cached state is shown; the next load tries again


@router.get("/sources/status")
async def sources_status(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    await _recheck_kite(c, actor)
    nse_h = c.nse.health()
    kite_h = c.kite.health()
    counts = dict((await s.execute(select(PriceBar.provider, func.count()).group_by(PriceBar.provider))).all())
    n_inst = await s.scalar(select(func.count()).select_from(Instrument).where(Instrument.instrument_type != "INDEX")) or 0
    n_fund = await s.scalar(select(func.count(func.distinct(FinancialPeriod.instrument_id)))) or 0
    last_bar = await s.scalar(select(func.max(PriceBar.date)))
    snaps = (await s.scalars(select(MetricSnapshot).where(MetricSnapshot.is_latest.is_(True)))).all()
    stale = sum(1 for m in snaps if m.score.get("freshness") == "stale")
    recent = (await s.execute(select(DataSourceRecord.provider, DataSourceRecord.dataset, DataSourceRecord.status, func.count())
                              .where(DataSourceRecord.retrieved_at >= c.clock.now() - timedelta(days=2))
                              .group_by(DataSourceRecord.provider, DataSourceRecord.dataset, DataSourceRecord.status))).all()
    connectors = [
        {"key": "nse_bhavcopy", "label": "NSE bhavcopy MCP", "role": "primary: end-of-day history, corporate actions", **nse_h["nse_bhavcopy"]},
        {"key": "nse_cm_market", "label": "NSE CM market MCP", "role": "primary: delayed live quotes", **nse_h["nse_cm_market"]},
        {"key": "kite_mcp", "label": "Zerodha Kite MCP", "role": "fallback: fills gaps, cross-checks NSE values", **kite_h},
        {"key": "fundamentals", "label": "Fundamentals files", "role": "financial statements (no MCP source provides them)", **c.fundamentals.status()},
        {"key": "web_search", "label": "Web search", "role": "news and filings for research", **c.search.status()},
        {"key": "llm", "label": f"LLM ({c.llm.label})", "role": "research synthesis and concall classification", **c.llm.status()},
    ]
    return {
        **meta(c), "connectors": connectors,
        "capabilities": {"nse_mcp": c.nse.capabilities(), "kite_mcp": c.kite.capabilities()},
        "precedence": {"default": reconcile.DEFAULT_PRECEDENCE, "policy": (reconcile.__doc__ or "").strip(),
                       "tolerances_pct": {f: reconcile.tolerance_pct(f) for f in ("close", "last_price", "volume")}},
        "coverage": {"instruments": n_inst, "bars_by_provider": counts, "last_bar": last_bar.isoformat() if last_bar else None,
                     "instruments_with_statements": n_fund, "stale_instruments": stale, "instruments_with_metrics": len(snaps),
                     "source_conflicts": await s.scalar(select(func.count()).select_from(SourceConflict)) or 0,
                     "recent_source_calls": [{"provider": p, "dataset": d, "status": st, "count": n} for p, d, st, n in recent]},
        "terms": ["NSE states its MCP data is delayed and for informational and educational use; it is not licensed for commercial "
                  "deployment or AI model training. Confirm NSE, broker and search-provider terms before any production use.",
                  "Bhavcopy prices are unadjusted; this system back-adjusts for splits and bonuses reported by the corporate-actions tool."],
        "cache": {"backend": type(c.cache).__name__, "note": c.cache_note},
        "scheduler": {"enabled": c.settings.scheduler_enabled, "jobs": c.scheduler.describe() if c.scheduler else []},
    }


@router.post("/sources/broker/login", dependencies=[rate_limit("default")])
async def broker_login(c: Ctx, actor: Actor) -> dict[str, Any]:
    """Current broker session state. When login is required the response carries the Kite
    authorisation link for the account holder to open. The first check after the session becomes
    authorised starts a backfill of instruments whose NSE history is short or stale."""
    return {**meta(c), "kite_mcp": await _kite_session(c, actor, c.kite.auth_state)}


@router.post("/sources/broker/login-link", dependencies=[rate_limit("default")])
async def broker_login_link(c: Ctx, actor: Actor) -> dict[str, Any]:
    """A freshly issued Kite authorisation link, requested when the user clicks to open it. Kite expires
    links quickly, so the page never opens a link it received earlier."""
    return {**meta(c), "kite_mcp": await _kite_session(c, actor, c.kite.login_link)}


@router.post("/sources/broker/logout", dependencies=[rate_limit("default")])
async def broker_logout(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Disconnect from Kite: the approved session and its saved copy are discarded."""
    state = await c.kite.disconnect()
    await audit(s, actor, "broker.logout", "connector", None, {"provider": "kite_mcp"})
    await s.commit()
    return {**meta(c), "kite_mcp": state}


@router.post("/sources/rediscover", dependencies=[rate_limit("refresh")])
async def rediscover(c: Ctx, actor: Actor) -> dict[str, Any]:
    """Re-read the MCP servers' advertised tools. A live session is kept, so an approved Kite login survives."""
    await c.nse.start()
    await c.kite.start()
    return {**meta(c), "nse_mcp": c.nse.health(), "kite_mcp": c.kite.health()}


# ------------------------------------------------------------------ instruments
@router.get("/instruments")
async def list_instruments(c: Ctx, s: Db, actor: Actor, q: Annotated[str | None, Query(max_length=60)] = None,
                           sector: Annotated[str | None, Query(max_length=80)] = None,
                           instrument_type: Annotated[str | None, Query(pattern="^(EQ|ETF|INDEX|REIT|INVIT|COMMODITY)$")] = None,
                           page: Annotated[int, Query(ge=1, le=10000)] = 1, page_size: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    stmt = select(Instrument)
    if q:
        like = f"%{q.strip().upper()}%"
        stmt = stmt.where(func.upper(Instrument.symbol).like(like) | func.upper(Instrument.name).like(like))
    if sector:
        stmt = stmt.where(Instrument.sector == sector)
    if instrument_type:
        stmt = stmt.where(Instrument.instrument_type == instrument_type)
    total = await s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order: list[Any] = [Instrument.symbol]
    if q:
        # With the whole market listed, "TCS" must rank the TCS row before every symbol that merely contains it.
        qq = q.strip().upper()
        order = [case((func.upper(Instrument.symbol) == qq, 0), (func.upper(Instrument.symbol).like(f"{qq}%"), 1),
                      (func.upper(Instrument.name).like(f"{qq}%"), 2), else_=3), Instrument.symbol]
    rows = (await s.scalars(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size))).all()
    return {**meta(c), "items": [inst_dict(i) for i in rows], "page": page, "page_size": page_size, "total": total}


QUOTE_DEADLINE_SECONDS = 8.0


@router.get("/instruments/{ref}/quote")
async def instrument_quote(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Live quote only. Answers within QUOTE_DEADLINE_SECONDS: `status: pending` means the sources are
    still being asked in the background and a repeat request will be served from the cache."""
    inst = await resolve_instrument(s, ref)
    return {**meta(c), "quote": await c.market_data.quote_within(inst.symbol, inst.instrument_type, QUOTE_DEADLINE_SECONDS)}


@router.get("/instruments/{ref}")
async def get_instrument(ref: str, c: Ctx, s: Db, actor: Actor, quote: bool = True) -> dict[str, Any]:
    """`quote=false` returns stored data at once and leaves the live quote to `/instruments/{ref}/quote`."""
    inst = await resolve_instrument(s, ref)
    snap = await s.scalar(select(MetricSnapshot).where(MetricSnapshot.instrument_id == inst.id, MetricSnapshot.is_latest.is_(True)))
    live_quote = await c.market_data.quote(inst.symbol, inst.instrument_type) if quote else None
    in_themes = (await s.execute(
        select(Theme.id, Theme.slug, Theme.name, Theme.category, ThemeSnapshotConstituent.rank, ThemeSnapshot.period, ThemeSnapshot.id)
        .join(ThemeSnapshot, ThemeSnapshot.theme_id == Theme.id).join(ThemeSnapshotConstituent, ThemeSnapshotConstituent.snapshot_id == ThemeSnapshot.id)
        .where(ThemeSnapshotConstituent.instrument_id == inst.id)
        .where(ThemeSnapshot.id.in_(select(func.max(ThemeSnapshot.id)).group_by(ThemeSnapshot.theme_id)))
        .order_by(ThemeSnapshotConstituent.rank))).all()
    peers = []
    if inst.sector and snap is not None:
        rows = (await s.execute(select(MetricSnapshot, Instrument).join(Instrument, Instrument.id == MetricSnapshot.instrument_id)
                                .where(MetricSnapshot.is_latest.is_(True), Instrument.sector == inst.sector))).all()
        cols = ("close", "overall_score", "return_3m", "return_12m", "pe", "roe", "volatility_252d")
        peers = sorted(({"instrument_id": i.id, "symbol": i.symbol, "name": i.name, **{k: m.flat.get(k) for k in cols},
                         "partial_score": m.score.get("partial_overall")} for m, i in rows), key=lambda r: r["symbol"])  # type: ignore[arg-type,return-value]
    return {
        **meta(c, as_of=snap.as_of.isoformat() if snap is not None else None), "instrument": inst_dict(inst), "quote": live_quote,
        "quote_deferred": not quote,
        "snapshot": None if snap is None else {"as_of": snap.as_of.isoformat(), "computed_at": snap.computed_at.isoformat(),
                                                "score": snap.score, "calculation_version": snap.versions, "is_demo": snap.is_demo},
        "themes": [{"theme_id": a, "slug": b, "name": n, "category": cat, "rank": r, "period": p, "snapshot_id": sid} for a, b, n, cat, r, p, sid in in_themes],
        "peers": peers,
        "data_state": "ok" if snap is not None else "missing",
        "data_state_reason": None if snap is not None else "No price history has been ingested for this instrument yet.",
    }


@router.get("/instruments/{ref}/metrics")
async def instrument_metrics(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Every computed metric with its value, unit, status, reason, formula and inputs."""
    inst = await resolve_instrument(s, ref)
    snap = await s.scalar(select(MetricSnapshot).where(MetricSnapshot.instrument_id == inst.id, MetricSnapshot.is_latest.is_(True)))
    if snap is None:
        return {**meta(c), "instrument": inst_dict(inst), "metrics": [], "data_state": "missing",
                "data_state_reason": "No metrics computed: price history has not been ingested."}
    out = []
    for name, d in sorted(snap.detail.items()):
        cat = METRIC_CATALOG.get(name, {})
        out.append({"name": name, "label": cat.get("label", name), "group": cat.get("group", "other"), "value": snap.flat.get(name),
                    "unit": d.get("unit") or cat.get("unit"), "status": d.get("status"), "reason": d.get("reason"),
                    "formula": d.get("formula"), "inputs": d.get("inputs"), "higher_is_better": cat.get("higher_is_better"),
                    "kind": "calculated"})
    return {**meta(c, as_of=snap.as_of.isoformat()), "instrument": inst_dict(inst), "metrics": out, "currency": inst.currency,
            "monetary_unit": "INR crore for statement values", "calculation_version": snap.versions, "computed_at": snap.computed_at.isoformat(),
            "coverage": {"available": sum(1 for m in out if m["value"] is not None), "total": len(out)}, "data_state": "ok"}


@router.get("/instruments/{ref}/financials")
async def instrument_financials(ref: str, c: Ctx, s: Db, actor: Actor,
                                period_type: Annotated[str, Query(pattern="^(annual|quarterly)$")] = "annual") -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    rows = (await s.scalars(select(FinancialPeriod).where(FinancialPeriod.instrument_id == inst.id, FinancialPeriod.period_type == period_type)
                            .order_by(FinancialPeriod.period_end))).all()
    if not rows:
        return {**meta(c), "instrument": inst_dict(inst), "periods": [], "period_type": period_type, "data_state": "missing",
                "data_state_reason": "No financial statements are ingested for this instrument. NSE MCP and Kite do not provide "
                                     "statements; upload a normalised statements file to enable fundamentals and valuation."}
    return {**meta(c), "instrument": inst_dict(inst), "period_type": period_type, "currency": rows[-1].currency, "unit": "crore",
            "reported_unit": rows[-1].reported_unit, "source": rows[-1].source, "source_url": rows[-1].source_url,
            "ingested_at": rows[-1].ingested_at.isoformat(), "is_demo": rows[-1].is_demo, "data_state": "ok", "kind": "reported",
            "periods": [{"period_end": r.period_end.isoformat(), "fiscal_year": r.fiscal_year,
                         "published_at": r.published_at.isoformat() if r.published_at else None, **r.facts} for r in rows]}


@router.put("/instruments/{ref}/financials", dependencies=[rate_limit("refresh")])
async def upload_financials(ref: str, c: Ctx, s: Db, actor: Actor, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Upload normalised statements for one instrument, then recompute metrics."""
    inst = await resolve_instrument(s, ref)
    try:
        doc = c.fundamentals.save(inst.symbol, payload)
    except FundamentalsValidationError as e:
        raise ApiError(422, "invalid_statements", str(e)) from e
    res = await ingestion.ingest_fundamentals(c, s)
    await audit(s, actor, "fundamentals.upload", "instrument", inst.id, {"periods": len(doc["periods"]), "source": doc["source"]})
    await s.commit()
    m = await metrics.compute_all(c, s)
    return {**meta(c), "saved_periods": len(doc["periods"]), "ingest": res, "metrics": m}


@router.get("/instruments/{ref}/history")
async def instrument_history(ref: str, c: Ctx, s: Db, actor: Actor, start: date | None = None, end: date | None = None,
                             limit: Annotated[int, Query(ge=1, le=2000)] = 800) -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    q = select(PriceBar).where(PriceBar.instrument_id == inst.id)
    if start:
        q = q.where(PriceBar.date >= start)
    if end:
        q = q.where(PriceBar.date <= end)
    bars = list((await s.scalars(q.order_by(PriceBar.date.desc()).limit(limit))).all())[::-1]
    f = lambda v: None if v is None else float(v)  # noqa: E731
    conflicts = (await s.scalars(select(SourceConflict).where(SourceConflict.instrument_id == inst.id).order_by(SourceConflict.obs_date.desc()).limit(100))).all()
    actions = (await s.scalars(select(CorporateActionRow).where(CorporateActionRow.instrument_id == inst.id).order_by(CorporateActionRow.ex_date))).all()
    lineage = (await s.scalars(select(DataSourceRecord).where(DataSourceRecord.instrument_id == inst.id)
                               .order_by(DataSourceRecord.retrieved_at.desc(), DataSourceRecord.id.desc()).limit(12))).all()
    providers: dict[str, int] = {}
    for b in bars:
        providers[b.provider] = providers.get(b.provider, 0) + 1
    last = bars[-1].date if bars else None
    age = (c.clock.now().date() - last).days if last else None
    return {
        **meta(c, as_of=last.isoformat() if last else None), "instrument": inst_dict(inst), "interval": "1d", "currency": inst.currency,
        "adjustment": "split_adjusted (splits and bonuses; cash dividends not adjusted)", "adjustment_version": "adjust-v1",
        "bars": [{"date": b.date.isoformat(), "open": f(b.open), "high": f(b.high), "low": f(b.low), "close": f(b.close),
                  "volume": f(b.volume), "provider": b.provider} for b in bars],
        "coverage": {"bars": len(bars), "by_provider": providers, "first": bars[0].date.isoformat() if bars else None,
                     "last": last.isoformat() if last else None},
        "freshness": None if age is None else ("fresh" if age <= metrics.FRESH_DAYS else "stale"), "data_age_days": age,
        "conflicts": [{"date": x.obs_date.isoformat(), "field": x.field, "values": x.values, "diff_pct": float(x.diff_pct),
                       "tolerance_pct": float(x.tolerance_pct), "chosen": x.chosen} for x in conflicts],
        "corporate_actions": [{"ex_date": a.ex_date.isoformat(), "kind": a.kind, "ratio": f(a.ratio), "description": a.description,
                               "provider": a.provider} for a in actions],
        "lineage": [{"provider": r.provider, "dataset": r.dataset, "status": r.status, "reason": r.reason, "rows": r.rows,
                     "source_id": r.source_id, "adjustment": r.adjustment, "retrieved_at": r.retrieved_at.isoformat(),
                     "detail": r.detail} for r in lineage],
        "data_state": "ok" if bars else "missing",
        "data_state_reason": None if bars else "No price history ingested. Check the Data status page for source errors.",
    }


@router.get("/instruments/{ref}/research")
async def latest_research(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    row = await s.scalar(select(ResearchReport).where(ResearchReport.instrument_id == inst.id).order_by(ResearchReport.created_at.desc()).limit(1))
    if row is None:
        return {**meta(c), "instrument": inst_dict(inst), "report": None,
                "data_state": "missing", "data_state_reason": "No research has been generated for this instrument yet."}
    return {**meta(c), "report_id": row.id, **row.report}


# ------------------------------------------------------------------ screener
def _parse_filters(raw: str | None) -> list[Filter]:
    if not raw:
        return []
    if len(raw) > 4000:
        raise ApiError(422, "invalid_filters", "The filters parameter is too long.")
    try:
        fs = _filters.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else "not valid JSON"
        raise ApiError(422, "invalid_filters", f"filters must be a JSON list of {{metric, op, value[, value2]}}: {msg}") from e
    if len(fs) > 25:
        raise ApiError(422, "invalid_filters", "At most 25 filters are allowed.")
    return fs


async def _screen(c: Any, s: Any, filters: str | None, sectors: str | None, q: str | None, sort: str, order: str, page: int,
                  page_size: int, columns: str | None) -> dict[str, Any]:
    try:
        cols = screener.validate_columns([x for x in columns.split(",") if x] if columns else None)
        return screener.run(await metrics.latest_rows(s), filters=_parse_filters(filters),
                            sectors=[x for x in sectors.split(",") if x] if sectors else None, search=q, sort=sort, order=order,
                            page=page, page_size=page_size, columns=cols)
    except screener.ScreenerError as e:
        raise ApiError(422, "invalid_screen", str(e)) from e


@router.get("/screener")
async def run_screener(c: Ctx, s: Db, actor: Actor, filters: str | None = None, sectors: Annotated[str | None, Query(max_length=600)] = None,
                       q: Annotated[str | None, Query(max_length=60)] = None, sort: Annotated[str, Query(max_length=60)] = "overall_score",
                       order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc", page: Annotated[int, Query(ge=1, le=10000)] = 1,
                       page_size: Annotated[int, Query(ge=1, le=screener.MAX_PAGE_SIZE)] = 25,
                       columns: Annotated[str | None, Query(max_length=1500)] = None) -> dict[str, Any]:
    return {**meta(c), **await _screen(c, s, filters, sectors, q, sort, order, page, page_size, columns)}


@router.get("/screener/export.csv")
async def export_screener(c: Ctx, s: Db, actor: Actor, filters: str | None = None, sectors: Annotated[str | None, Query(max_length=600)] = None,
                          q: Annotated[str | None, Query(max_length=60)] = None, sort: Annotated[str, Query(max_length=60)] = "overall_score",
                          order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
                          columns: Annotated[str | None, Query(max_length=1500)] = None) -> Response:
    res = await _screen(c, s, filters, sectors, q, sort, order, 1, 100000, columns)
    note = ("DEMO SYNTHETIC DATA - not real market data" if c.settings.is_demo else "prices: NSE MCP primary, broker fallback") \
        + f" · exported {c.clock.now().isoformat()}"
    return Response(screener.to_csv(res, note), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="screener.csv"'})


@router.get("/screener/metrics")
async def metric_catalog(c: Ctx, actor: Actor) -> dict[str, Any]:
    return {"metrics": [{"key": k, **v} for k, v in METRIC_CATALOG.items()], "operators": ["gt", "gte", "lt", "lte", "eq", "between"],
            "default_columns": screener.DEFAULT_COLUMNS,
            "scoring": {"version": scoring.VERSION, "pillar_weights": scoring.PILLAR_WEIGHTS, "pillar_labels": scoring.PILLAR_LABELS,
                        "method": (scoring.__doc__ or "").strip(), "stance_rule": scoring.STANCE_RULE,
                        "confidence_rule": scoring.CONFIDENCE_RULE, "min_peers": scoring.MIN_PEERS,
                        "min_pillar_coverage": scoring.MIN_PILLAR_COVERAGE,
                        "profile": {p: [{"metric": m.name, "label": METRIC_CATALOG[m.name]["label"], "higher_is_better": m.higher_is_better,
                                         "peer_relative": m.peer_relative} for m in specs] for p, specs in scoring.DEFAULT_PROFILE.items()}}}


# ------------------------------------------------------------------ sectors and rotation
@router.get("/sectors")
async def sectors(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    rows = await metrics.latest_rows(s)
    snap = await sector_rotation.latest(s, c.benchmark_symbol)
    rot = snap.payload["sectors"] if snap else {}
    out: dict[str, dict[str, Any]] = {}
    for m, i in rows:
        if not i.sector:
            continue
        d = out.setdefault(i.sector, {"sector": i.sector, "instruments": 0, "_scores": [], "_r3": []})
        d["instruments"] += 1
        v = m.score.get("overall") if m.score.get("complete") else m.score.get("partial_overall")
        if v is not None:
            d["_scores"].append(v)
    items = []
    for name, d in sorted(out.items()):
        r = rot.get(name) or {}
        sc = d.pop("_scores")
        d.pop("_r3")
        items.append({**d, "avg_score": round(sum(sc) / len(sc), 1) if sc else None, "quadrant": r.get("quadrant"),
                      "returns": r.get("returns") or {}, "series_kind": r.get("series_kind"), "status": r.get("status", "missing")})
    return {**meta(c, as_of=snap.data_as_of.isoformat() if snap and snap.data_as_of else None), "items": items, "benchmark": c.benchmark_symbol}


def _rot_dict(c: Any, snap: SectorRotationSnapshot, now: Any) -> dict[str, Any]:
    age_h = (now - snap.computed_at).total_seconds() / 3600
    return {"snapshot_id": snap.id, "slot": snap.slot, "computed_at": snap.computed_at.isoformat(),
            "data_as_of": snap.data_as_of.isoformat() if snap.data_as_of else None, "is_demo": snap.is_demo,
            "stale": age_h > 13, "age_hours": round(age_h, 1), "refresh_schedule": c.settings.rrg_refresh_cron, **snap.payload}


@router.get("/sector-rotation")
async def get_rotation(c: Ctx, s: Db, actor: Actor, snapshot_id: int | None = None,
                       ratio_window: Annotated[int | None, Query(ge=4, le=52)] = None,
                       momentum_window: Annotated[int | None, Query(ge=2, le=26)] = None,
                       trail: Annotated[int | None, Query(ge=2, le=26)] = None, end: date | None = None) -> dict[str, Any]:
    """Latest stored snapshot by default. Passing lookback parameters or an end date computes an
    on-demand view from stored prices (not persisted) with the same documented method."""
    custom = any(v is not None for v in (ratio_window, momentum_window, trail, end))
    if custom:
        base = rrg.RRGParams()
        params = rrg.RRGParams(ratio_window or base.ratio_window, momentum_window or base.momentum_window, trail or base.trail)
        insts = list((await s.scalars(select(Instrument))).all())
        bars = await ingestion.load_bars(s)
        try:
            payload = sector_rotation.compute(sector_rotation.build_series(insts, bars, c.benchmark_symbol), c.benchmark_symbol, params,
                                              end=end.isoformat() if end else None, today=c.clock.now().date())
        except rrg.RRGError as e:
            raise ApiError(409, "rrg_unavailable", str(e)) from e
        return {**meta(c, as_of=payload["data_as_of"]), "snapshot_id": None, "on_demand": True, "computed_at": c.clock.now().isoformat(),
                "stale": False, **payload}
    snap = await s.get(SectorRotationSnapshot, snapshot_id) if snapshot_id else await sector_rotation.latest(s, c.benchmark_symbol)
    if snap is None:
        return {**meta(c), "snapshot_id": None, "sectors": {}, "data_state": "missing", "benchmark": c.benchmark_symbol,
                "data_state_reason": "No sector-rotation snapshot exists yet. Ingest prices, then refresh."}
    last_fail = await s.scalar(select(JobRun).where(JobRun.kind == "rrg", JobRun.status == "failed", JobRun.started_at > snap.computed_at)
                               .order_by(JobRun.started_at.desc()).limit(1))
    out = {**meta(c, as_of=snap.data_as_of.isoformat() if snap.data_as_of else None), "on_demand": False, **_rot_dict(c, snap, c.clock.now())}
    if last_fail is not None:
        out["last_refresh_failed"] = {"at": last_fail.started_at.isoformat(), "error": last_fail.error,
                                      "note": "The most recent refresh failed; the last valid snapshot is shown."}
    return out


@router.get("/sector-rotation/snapshots")
async def rotation_snapshots(c: Ctx, s: Db, actor: Actor, limit: Annotated[int, Query(ge=1, le=200)] = 60) -> dict[str, Any]:
    rows = (await s.scalars(select(SectorRotationSnapshot).where(SectorRotationSnapshot.benchmark == c.benchmark_symbol,
                                                                 SectorRotationSnapshot.params_key == "default")
                            .order_by(SectorRotationSnapshot.computed_at.desc()).limit(limit))).all()
    return {**meta(c), "items": [{"snapshot_id": r.id, "slot": r.slot, "computed_at": r.computed_at.isoformat(),
                                  "data_as_of": r.data_as_of.isoformat() if r.data_as_of else None, "coverage": r.coverage,
                                  "quadrants": {k: v.get("quadrant") for k, v in r.payload["sectors"].items()}} for r in rows]}


@router.post("/sector-rotation/refresh", status_code=202, dependencies=[rate_limit("refresh")])
async def refresh_rotation(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    job_id = await jobs.start_background(c, "rrg", "rrg", pipeline.rrg_job, "manual", actor)
    await audit(s, actor, "rrg.refresh", "job", job_id)
    await s.commit()
    return {"job_id": job_id, "status": "running"}


# ------------------------------------------------------------------ overview
BREADTH_TIMEOUT_SECONDS = 6.0


_breadth_tasks: set[asyncio.Task[Any]] = set()


async def _market_breadth(c: Any, fresh: bool = False) -> dict[str, Any]:
    """Live NSE breadth that never blocks an ordinary page load. A good result is cached for 5
    minutes; a failure state (never a value) is remembered for 2 minutes. On a cache miss the check
    runs in the background and the page shows "checking"; `fresh=True` (the refresh button) waits
    for the check, bounded by BREADTH_TIMEOUT_SECONDS."""
    if not fresh:
        for key in ("overview:breadth", "overview:breadth_down"):
            if (hit := await c.cache.get(key)) is not None:
                return {**hit, "cache": "hit"}
        if not _breadth_tasks:
            task = asyncio.create_task(_check_breadth(c))
            _breadth_tasks.add(task)
            task.add_done_callback(_breadth_tasks.discard)
        return {"state": "checking", "reason": "Checking NSE market breadth in the background; refresh in a few seconds."}
    return await _check_breadth(c)


async def _check_breadth(c: Any) -> dict[str, Any]:
    try:
        market = {"state": "ok", **await asyncio.wait_for(c.nse.market_breadth(), BREADTH_TIMEOUT_SECONDS)}
        await c.cache.set("overview:breadth", market, 300)
        await c.cache.delete_prefix("overview:breadth_down")
    except ProviderUnavailable as e:
        market = {"state": e.state, "reason": str(e)[:300], "checked_at": c.clock.now().isoformat()}
        await c.cache.set("overview:breadth_down", market, 120)
    except TimeoutError:
        market = {"state": "unavailable", "reason": f"NSE market breadth did not answer within {BREADTH_TIMEOUT_SECONDS:g}s",
                  "checked_at": c.clock.now().isoformat()}
        await c.cache.set("overview:breadth_down", market, 120)
    return market


@router.get("/overview")
async def overview(c: Ctx, s: Db, actor: Actor, fresh: bool = False) -> dict[str, Any]:
    """`fresh=true` (the page's refresh button) re-checks live sources instead of using cached states."""
    rows = await metrics.latest_rows(s)
    snap = await sector_rotation.latest(s, c.benchmark_symbol)
    as_of = max((m.as_of for m, _ in rows), default=None)

    def top(key: str, n: int = 5, reverse: bool = True) -> list[dict[str, Any]]:
        have = [(m, i) for m, i in rows if m.flat.get(key) is not None]
        have.sort(key=lambda t: t[0].flat[key], reverse=reverse)
        return [{"instrument_id": i.id, "symbol": i.symbol, "name": i.name, "sector": i.sector, "value": m.flat[key],
                 "close": m.flat.get("close")} for m, i in have[:n]]

    chg = [m.flat.get("return_1w") for m, _ in rows if m.flat.get("return_1w") is not None]
    breadth = None
    if chg:
        breadth = {"advancing_1w": sum(1 for x in chg if x > 0), "declining_1w": sum(1 for x in chg if x < 0), "instruments": len(chg)}  # type: ignore[misc,operator]
    market = await _market_breadth(c, fresh)
    bench = await s.scalar(select(Instrument).where(Instrument.symbol == c.benchmark_symbol))
    bench_stats = None
    if bench is not None:
        b = (await ingestion.load_bars(s, [bench.id])).get(bench.id)
        if b is not None and len(b) > 22:
            cl = b["close"]
            bench_stats = {"symbol": bench.symbol, "close": float(cl.iloc[-1]), "last_bar": cl.index[-1].date().isoformat(),
                           "return_1w": float((cl.iloc[-1] / cl.iloc[-6] - 1) * 100), "return_1m": float((cl.iloc[-1] / cl.iloc[-22] - 1) * 100),
                           "sparkline": [round(float(x), 2) for x in cl.iloc[-60:]]}
    leaders = (await s.execute(
        select(Theme.id, Theme.name, Theme.category, ThemeSnapshot.id, ThemeSnapshot.period, ThemeSnapshot.created_at, ThemeSnapshotConstituent.symbol)
        .join(ThemeSnapshot, ThemeSnapshot.theme_id == Theme.id).join(ThemeSnapshotConstituent, ThemeSnapshotConstituent.snapshot_id == ThemeSnapshot.id)
        .where(ThemeSnapshotConstituent.rank == 1, Theme.slug.in_(["price-momentum", "low-volatility", "high-roce", "value-plus-quality",
                                                                    "revenue-growth-leaders", "dividend-yield-sustainable"]))
        .where(ThemeSnapshot.id.in_(select(func.max(ThemeSnapshot.id)).group_by(ThemeSnapshot.theme_id))))).all()
    runs = await jobs.last_runs(s, 8)
    stale = sum(1 for m, _ in rows if m.score.get("freshness") == "stale")
    complete = sum(1 for m, _ in rows if m.score.get("complete"))
    return {
        **meta(c, as_of=as_of.isoformat() if as_of else None), "benchmark": bench_stats, "benchmark_symbol": c.benchmark_symbol,
        "universe": {"instruments": len(rows), "complete_scores": complete, "stale": stale, "breadth": breadth},
        "market_breadth": market,
        "sectors": [] if snap is None else sorted(
            ({"sector": k, "quadrant": v.get("quadrant"), "return_1m": (v.get("returns") or {}).get("1M"),
              "return_3m": (v.get("returns") or {}).get("3M"), "status": v.get("status"), "series_kind": v.get("series_kind")}
             for k, v in snap.payload["sectors"].items()), key=lambda r: -(r["return_1m"] if r["return_1m"] is not None else -1e9)),
        "rotation": None if snap is None else {"computed_at": snap.computed_at.isoformat(),
                                               "data_as_of": snap.data_as_of.isoformat() if snap.data_as_of else None},
        "top_scores": top("overall_score"), "top_momentum": top("return_3m"), "weakest_momentum": top("return_3m", reverse=False),
        "theme_leaders": [{"theme_id": a, "theme": b, "category": cat, "snapshot_id": sid, "period": p, "refreshed_at": ts.isoformat(), "symbol": sym}
                          for a, b, cat, sid, p, ts, sym in leaders],
        "jobs": [jobs.to_dict(r) for r in runs],
        "data_state": "ok" if rows else "missing",
        "data_state_reason": None if rows else "No data ingested yet. Start a refresh from the Data status page.",
    }


# ------------------------------------------------------------------ ask the market
class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("/ask", dependencies=[rate_limit("research")])
async def ask(body: AskIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Interpret a question (LLM, rules fallback), fetch live MCP data with the database as fallback,
    and answer from that evidence only."""
    out = await ask_service.ask(c, s, body.question.strip())
    await audit(s, actor, "ask", None, None, {"question": body.question[:200], "intent": out["plan"]["intent"],
                                              "plan_source": out["plan_source"], "answer_source": out["answer_source"]})
    await s.commit()
    return {**meta(c), **out}


@router.post("/instruments/{ref}/refresh", status_code=202, dependencies=[rate_limit("refresh")])
async def refresh_instrument(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Fetch the latest bars for one instrument from the MCP sources and recompute metrics."""
    inst = await resolve_instrument(s, ref)
    sym = inst.symbol

    async def job(cc: Any, ss: Any) -> dict[str, Any]:
        prices = await ingestion.ingest_prices(cc, ss, [sym])
        m = await metrics.compute_all(cc, ss)
        await cc.cache.delete_prefix("api:")
        await cc.cache.delete_prefix(f"quote:{sym.upper()}")
        return {"prices": prices, "metrics": m}

    job_id = await jobs.start_background(c, "instrument_refresh", f"instrument:{inst.id}", job, "manual", actor)
    await audit(s, actor, "instrument.refresh", "instrument", inst.id, {"job_id": job_id})
    await s.commit()
    return {"job_id": job_id, "status": "running"}


# ------------------------------------------------------------------ jobs and audit
@router.get("/jobs")
async def list_jobs(c: Ctx, s: Db, actor: Actor, limit: Annotated[int, Query(ge=1, le=200)] = 50,
                    kind: Annotated[str | None, Query(max_length=40)] = None) -> dict[str, Any]:
    return {"items": [jobs.to_dict(r) for r in await jobs.last_runs(s, limit, kind)]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    run = await s.get(JobRun, job_id)
    if run is None:
        raise ApiError(404, "job_not_found", f"No job with id {job_id}.")
    return jobs.to_dict(run)


@router.post("/jobs/refresh-all", status_code=202, dependencies=[rate_limit("refresh")])
async def refresh_all(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    job_id = await jobs.start_background(c, "full_refresh", "full_refresh", pipeline.full_refresh, "manual", actor)
    await audit(s, actor, "data.refresh_all", "job", job_id)
    await s.commit()
    return {"job_id": job_id, "status": "running"}


@router.get("/audit/events")
async def audit_events(c: Ctx, s: Db, actor: Actor, limit: Annotated[int, Query(ge=1, le=200)] = 50,
                       page: Annotated[int, Query(ge=1, le=10000)] = 1) -> dict[str, Any]:
    rows = (await s.scalars(select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).offset((page - 1) * limit).limit(limit))).all()
    return {"items": [{"id": r.id, "at": r.at.isoformat(), "actor": r.actor, "action": r.action, "entity": r.entity,
                       "entity_id": r.entity_id, "detail": r.detail, "request_id": r.request_id} for r in rows], "page": page}
