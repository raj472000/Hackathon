"""Themes, research, concall analyzer, watchlists and alerts."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import Actor, ApiError, Ctx, Db, meta, rate_limit, resolve_instrument
from app.db.models import (
    Alert,
    AlertEvent,
    ConcallAnalysis,
    ConcallTranscript,
    Instrument,
    MetricSnapshot,
    Theme,
    ThemeSnapshot,
    ThemeVersion,
    Watchlist,
    WatchlistItem,
)
from app.domain import theme_dsl
from app.domain.theme_dsl import ThemeSpec
from app.domain.themes_builtin import CATEGORIES
from app.services import alerts, concall, ingestion, jobs, metrics, research, theme_ai, themes
from app.services.jobs import audit

router = APIRouter()
MAX_UPLOAD_BYTES = 8_000_000


# ------------------------------------------------------------------ themes
async def _theme(s: Any, theme_id: str) -> Theme:
    t = await s.get(Theme, int(theme_id)) if theme_id.isdigit() else await s.scalar(select(Theme).where(Theme.slug == theme_id[:80]))
    if t is None:
        raise ApiError(404, "theme_not_found", "Theme not found.")
    return t


@router.get("/themes")
async def list_themes(c: Ctx, s: Db, actor: Actor, category: Annotated[str | None, Query(max_length=60)] = None,
                      q: Annotated[str | None, Query(max_length=60)] = None, include_archived: bool = False) -> dict[str, Any]:
    stmt = select(Theme)
    if not include_archived:
        stmt = stmt.where(Theme.archived.is_(False))
    if category:
        stmt = stmt.where(Theme.category == category)
    rows = list((await s.scalars(stmt.order_by(Theme.is_builtin.desc(), Theme.id))).all())
    if q:
        ql = q.lower()
        rows = [t for t in rows if ql in t.name.lower() or ql in t.description.lower()]
    latest_ids = select(func.max(ThemeSnapshot.id)).group_by(ThemeSnapshot.theme_id)
    snaps = {x.theme_id: x for x in (await s.scalars(select(ThemeSnapshot).options(selectinload(ThemeSnapshot.constituents))
                                                     .where(ThemeSnapshot.id.in_(latest_ids)))).all()}
    items = []
    for t in rows:
        d = themes.theme_dict(t, snaps.get(t.id), c)
        d.pop("spec")
        sn = snaps.get(t.id)
        d["leaders"] = [k.symbol for k in sn.constituents[:3]] if sn else []
        items.append(d)
    return {**meta(c), "items": items, "categories": [*CATEGORIES, "Custom"], "refresh_schedule": c.settings.theme_refresh_cron,
            "next_refresh": themes.next_refresh(c), "timezone": c.settings.default_market_timezone}


class InterpretIn(BaseModel):
    text: str = Field(min_length=3, max_length=600)
    name: str | None = Field(default=None, max_length=120)


@router.post("/themes/interpret")
async def interpret_theme(body: InterpretIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Translate a natural-language request into a reviewable structured spec. Nothing is saved."""
    it = theme_dsl.interpret(body.text, body.name)
    return {"spec": it.spec.model_dump(mode="json"), "interpreted_filters": it.spec.describe(), "ambiguities": it.ambiguities,
            "matched_phrases": it.matched_phrases, "requires_confirmation": it.requires_confirmation, "source": it.source,
            "note": "Review and adjust the filters and thresholds, then confirm to save. Nothing has been saved yet."}


class ThemeCreate(BaseModel):
    spec: ThemeSpec
    description: str = Field(default="", max_length=2000)
    confirmed: bool = False


@router.post("/themes", status_code=201)
async def create_theme(body: ThemeCreate, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    if not body.confirmed:
        raise ApiError(409, "confirmation_required", "Review the interpreted filters and resend with confirmed=true to save the theme.",
                       {"interpreted_filters": body.spec.describe()})
    if (await s.scalar(select(func.count()).select_from(Theme).where(Theme.owner == actor, Theme.is_builtin.is_(False))) or 0) >= 200:
        raise ApiError(409, "limit_reached", "Custom theme limit reached.")
    t = await themes.create_custom(s, actor, body.spec, body.description)
    return {**meta(c), "theme": themes.theme_dict(t, None, c)}


@router.get("/themes/{theme_id}")
async def get_theme(theme_id: str, c: Ctx, s: Db, actor: Actor, snapshot_id: int | None = None) -> dict[str, Any]:
    t = await _theme(s, theme_id)
    snap = await themes.current_snapshot(s, t.id, snapshot_id)
    live: dict[int, dict[str, Any]] = {}
    names: dict[int, dict[str, Any]] = {}
    if snap is not None:
        ids = [k.instrument_id for k in snap.constituents]
        for m, i in (await s.execute(select(MetricSnapshot, Instrument).join(Instrument, Instrument.id == MetricSnapshot.instrument_id)
                                     .where(MetricSnapshot.is_latest.is_(True), Instrument.id.in_(ids)))).all():
            live[i.id] = {"close": m.flat.get("close")}
            names[i.id] = {"name": i.name, "sector": i.sector, "stance": m.score.get("stance"), "risk_label": (m.score.get("risk") or {}).get("label")}
    versions = (await s.scalars(select(ThemeVersion).where(ThemeVersion.theme_id == t.id).order_by(ThemeVersion.version.desc()))).all()
    return {**meta(c, as_of=snap.metrics_as_of.isoformat() if snap and snap.metrics_as_of else None),
            "theme": themes.theme_dict(t, snap, c), "snapshot": themes.snapshot_dict(snap, live, names) if snap else None,
            "versions": [{"version": v.version, "created_at": v.created_at.isoformat(), "changed_by": v.changed_by} for v in versions],
            "metric_labels": {k: {"label": v["label"], "unit": v["unit"]} for k, v in theme_dsl.METRIC_CATALOG.items()},
            "data_state": "ok" if snap else ("not_supported" if not t.available else "missing"),
            "data_state_reason": None if snap else (f"This theme needs data that is not configured: {', '.join(t.requires)}." if not t.available
                                                    else "No snapshot yet. Run a refresh.")}


class ThemePatch(BaseModel):
    spec: ThemeSpec | None = None
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None
    confirmed: bool = False


@router.patch("/themes/{theme_id}")
async def patch_theme(theme_id: str, body: ThemePatch, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    t = await _theme(s, theme_id)
    if t.owner != actor and not t.is_builtin:
        raise ApiError(403, "forbidden", "Only the owner can change this theme.")
    if (body.spec is not None or body.archived) and not body.confirmed:
        raise ApiError(409, "confirmation_required", "Theme definition changes and archiving need confirmed=true.")
    try:
        t = await themes.update_theme(s, t, actor, body.spec, body.description, body.archived)
    except themes.ThemeError as e:
        raise ApiError(409, "theme_immutable", str(e)) from e
    return {**meta(c), "theme": themes.theme_dict(t, await themes.current_snapshot(s, t.id), c)}


@router.post("/themes/{theme_id}/refresh", status_code=202, dependencies=[rate_limit("refresh")])
async def refresh_theme(theme_id: str, c: Ctx, s: Db, actor: Actor, fetch_latest: bool = True) -> dict[str, Any]:
    """Re-rank the theme into a new snapshot revision. With `fetch_latest` (the default) the latest
    bars are fetched from the MCP sources and metrics recomputed first; earlier snapshots are kept."""
    t = await _theme(s, theme_id)
    if not t.available:
        raise ApiError(409, "theme_unavailable", f"This theme needs data that is not configured: {', '.join(t.requires)}.")
    tid = t.id

    async def job(cc: Any, ss: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if fetch_latest:
            prices = await ingestion.ingest_prices(cc, ss)
            out["prices"] = {k: prices[k] for k in ("instruments", "ok", "failed", "bars")}
            out["prices"]["failures"] = prices["failures"][:10]
            out["metrics"] = await metrics.compute_all(cc, ss)
            await cc.cache.delete_prefix("api:")
        theme = await ss.get(Theme, tid)
        snap = await themes.refresh_theme(cc, ss, theme, "manual")
        return {**out, "snapshot_id": snap.id, "period": snap.period, "revision": snap.revision, "constituents": len(snap.constituents)}

    job_id = await jobs.start_background(c, "theme_refresh", f"theme:{tid}", job, "manual", actor)
    await audit(s, actor, "theme.refresh", "theme", tid, {"job_id": job_id})
    await s.commit()
    return {"job_id": job_id, "status": "running"}


@router.get("/themes/{theme_id}/snapshots")
async def theme_snapshots(theme_id: str, c: Ctx, s: Db, actor: Actor, limit: Annotated[int, Query(ge=1, le=120)] = 36) -> dict[str, Any]:
    t = await _theme(s, theme_id)
    rows = (await s.scalars(select(ThemeSnapshot).options(selectinload(ThemeSnapshot.constituents)).where(ThemeSnapshot.theme_id == t.id)
                            .order_by(ThemeSnapshot.created_at.desc(), ThemeSnapshot.id.desc()).limit(limit))).all()
    return {**meta(c), "theme_id": t.id, "items": [
        {"id": r.id, "period": r.period, "revision": r.revision, "created_at": r.created_at.isoformat(), "trigger": r.trigger,
         "theme_version": r.theme_version, "metrics_as_of": r.metrics_as_of.isoformat() if r.metrics_as_of else None,
         "constituents": len(r.constituents), "eligible_count": r.eligible_count, "top": [k.symbol for k in r.constituents[:5]],
         "versions": r.versions} for r in rows]}


class RefineIn(BaseModel):
    query: str = Field(min_length=3, max_length=500)


@router.post("/themes/{theme_id}/ai-filter", dependencies=[rate_limit("research")])
async def ai_filter_theme(theme_id: str, body: RefineIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Narrow a theme with a plain-language request. The model proposes DSL filters; they are
    validated and evaluated deterministically. Nothing is saved."""
    t = await _theme(s, theme_id)
    try:
        out = await theme_ai.refine(c, s, t, body.query.strip())
    except theme_ai.RefineError as e:
        raise ApiError(409, "no_data", str(e)) from e
    await audit(s, actor, "theme.ai_filter", "theme", t.id, {"query": body.query[:200], "source": out["source"]})
    await s.commit()
    return {**meta(c), "theme_id": t.id, **out}


# ------------------------------------------------------------------ research
class ResearchIn(BaseModel):
    instrument: str = Field(min_length=1, max_length=60, description="Instrument id or EXCHANGE:SYMBOL")


@router.post("/research/stock", dependencies=[rate_limit("research")])
async def research_stock(body: ResearchIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    inst = await resolve_instrument(s, body.instrument)
    if inst.instrument_type == "INDEX":
        raise ApiError(422, "unsupported_instrument", "Research boards are produced for tradable instruments, not indices.")
    doc = await research.generate(c, s, inst)
    await audit(s, actor, "research.generate", "instrument", inst.id, {"mode": doc["mode"], "report_id": doc["report_id"]})
    await s.commit()
    return {**meta(c), **doc}


# ------------------------------------------------------------------ concalls
@router.get("/instruments/{ref}/concalls")
async def list_concalls(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    ts = (await s.scalars(select(ConcallTranscript).where(ConcallTranscript.instrument_id == inst.id)
                          .order_by(ConcallTranscript.fiscal_year.desc(), ConcallTranscript.quarter.desc()))).all()
    an = (await s.scalars(select(ConcallAnalysis).where(ConcallAnalysis.instrument_id == inst.id)
                          .order_by(ConcallAnalysis.created_at.desc(), ConcallAnalysis.id.desc()).limit(12))).all()
    latest = an[0] if an else None
    return {**meta(c), "instrument": {"id": inst.id, "symbol": inst.symbol, "name": inst.name},
            "transcripts": [concall.transcript_meta(t) for t in ts],
            "analyses": [{"analysis_id": a.id, "created_at": a.created_at.isoformat(), "mode": a.mode, "label": a.label,
                          "score": float(a.score) if a.score is not None else None, "confidence": a.confidence,
                          "current": a.result["current"]["label"], "previous": a.result["previous"]["label"]} for a in an],
            "latest": None if latest is None else {"analysis_id": latest.id, "created_at": latest.created_at.isoformat(), **latest.result},
            "llm": {"available": c.llm.available, "reason": c.llm.status()["reason"]},
            "nse_filings": {"available": c.filings.available, "reason": c.filings.status()["reason"]},
            "web_search": {"available": bool(c.search.available), "provider": c.search.name},
            "how_it_works": (concall.__doc__ or "").strip(),
            "data_state": "ok" if ts else "missing",
            "data_state_reason": None if ts else "No transcripts stored yet. Fetch them from NSE to run the analysis."}


@router.post("/instruments/{ref}/concalls", status_code=201, dependencies=[rate_limit("refresh")])
async def upload_concall(ref: str, c: Ctx, s: Db, actor: Actor, fiscal_year: Annotated[int, Form(ge=2000, le=2100)],
                         quarter: Annotated[int, Form(ge=1, le=4)], source: Annotated[str, Form(min_length=2, max_length=200)],
                         text: Annotated[str | None, Form(max_length=concall.MAX_CHARS)] = None,
                         source_url: Annotated[str | None, Form(max_length=1000)] = None, call_date: Annotated[date | None, Form()] = None,
                         file: Annotated[UploadFile | None, File()] = None) -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    if file is not None:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "file_too_large", "Transcript files are limited to 8 MB.")
        name = (file.filename or "").lower()
        try:
            text = concall.extract_pdf_text(data) if name.endswith(".pdf") or data[:5] == b"%PDF-" else data.decode("utf-8", "replace")
        except concall.ConcallError as e:
            raise ApiError(422, "invalid_transcript", str(e)) from e
    if not text:
        raise ApiError(422, "invalid_transcript", "Provide the transcript as text or as a .txt / .pdf file.")
    if source_url:
        from app.connectors.web_search import safe_url

        if safe_url(source_url) is None:
            raise ApiError(422, "invalid_source_url", "source_url must be a public http(s) URL.")
    try:
        t = await concall.store_transcript(s, inst, fiscal_year, quarter, text, source, source_url, call_date, is_demo=c.settings.is_demo)
    except concall.ConcallError as e:
        raise ApiError(422, "invalid_transcript", str(e)) from e
    await audit(s, actor, "concall.upload", "instrument", inst.id, {"transcript_id": t.id, "fy": fiscal_year, "quarter": quarter, "chars": t.chars})
    await s.commit()
    return {**meta(c), "transcript": concall.transcript_meta(t)}


class AnalyzeIn(BaseModel):
    current_transcript_id: int | None = None


@router.post("/instruments/{ref}/concalls/auto", status_code=202, dependencies=[rate_limit("research")])
async def auto_concall(ref: str, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Fetch the company's con-call transcripts from NSE, check commitments on the web, and score follow-through."""
    inst = await resolve_instrument(s, ref)
    if inst.instrument_type == "INDEX":
        raise ApiError(422, "unsupported_instrument", "Con-call analysis applies to companies, not indices.")
    if not c.filings.available:
        raise ApiError(409, "filings_unavailable", f"NSE filings are not available: {c.filings.status()['reason']}. "
                                                   "Set NSE_FILINGS_BASE_URL, or upload transcripts instead.")
    job_id = await jobs.start_background(c, "concall_auto", f"concall:{inst.id}", concall.auto_job(inst.id), "manual", actor)
    await audit(s, actor, "concall.auto", "instrument", inst.id, {"job_id": job_id})
    await s.commit()
    return {**meta(c), "job_id": job_id, "status": "running"}


@router.post("/instruments/{ref}/concalls/analyze", dependencies=[rate_limit("research")])
async def analyze_concall(ref: str, body: AnalyzeIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    inst = await resolve_instrument(s, ref)
    try:
        out = await concall.analyze(c, s, inst, body.current_transcript_id)
    except concall.ConcallError as e:
        raise ApiError(409, "concall_unavailable", str(e)) from e
    await audit(s, actor, "concall.analyze", "instrument", inst.id, {"analysis_id": out["analysis_id"], "mode": out["mode"]})
    await s.commit()
    return {**meta(c), **out}


@router.get("/concalls")
async def concall_overview(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    """Latest follow-through analysis per instrument, plus instruments that have transcripts."""
    latest_ids = select(func.max(ConcallAnalysis.id)).group_by(ConcallAnalysis.instrument_id)
    rows = (await s.execute(select(ConcallAnalysis, Instrument).join(Instrument, Instrument.id == ConcallAnalysis.instrument_id)
                            .where(ConcallAnalysis.id.in_(latest_ids)))).all()
    counts = dict((await s.execute(select(ConcallTranscript.instrument_id, func.count()).group_by(ConcallTranscript.instrument_id))).all())
    insts = {i.id: i for i in (await s.scalars(select(Instrument).where(Instrument.id.in_(list(counts) or [0])))).all()}
    done = {i.id for _a, i in rows}
    return {**meta(c), "llm": {"available": c.llm.available, "reason": c.llm.status()["reason"], "model_id": c.llm.model_id},
            "items": sorted(({"instrument_id": i.id, "symbol": i.symbol, "name": i.name, "sector": i.sector, "mode": a.mode,
                              "score": float(a.score) if a.score is not None else None, "label": a.label, "confidence": a.confidence,
                              "current": a.result["current"]["label"], "previous": a.result["previous"]["label"],
                              "created_at": a.created_at.isoformat(), "counts": a.result["score"]["counts"], "is_demo": a.result.get("is_demo")}
                             for a, i in rows), key=lambda r: -(r["score"] if r["score"] is not None else -1)),
            "pending": [{"instrument_id": i.id, "symbol": i.symbol, "name": i.name, "transcripts": counts[i.id]}
                        for i in insts.values() if i.id not in done]}


# ------------------------------------------------------------------ watchlists
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WatchItemIn(BaseModel):
    instrument: str = Field(min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=300)


async def _watchlist(s: Any, wid: int, actor: str) -> Watchlist:
    w = await s.get(Watchlist, wid)
    if w is None or w.owner != actor:
        raise ApiError(404, "watchlist_not_found", "Watchlist not found.")
    return w


async def _wl_dict(s: Any, w: Watchlist) -> dict[str, Any]:
    await s.refresh(w, ["items"])
    ids = [it.instrument_id for it in w.items]
    rows = {i.id: (i, m) for i, m in (await s.execute(
        select(Instrument, MetricSnapshot).outerjoin(MetricSnapshot, (MetricSnapshot.instrument_id == Instrument.id) & MetricSnapshot.is_latest.is_(True))
        .where(Instrument.id.in_(ids or [0])))).all()}
    items = []
    for it in w.items:
        i, m = rows.get(it.instrument_id, (None, None))
        if i is None:
            continue
        flat, sc = (m.flat, m.score) if m is not None else ({}, {})
        items.append({"item_id": it.id, "instrument_id": i.id, "symbol": i.symbol, "name": i.name, "sector": i.sector, "note": it.note,
                      "close": flat.get("close"), "return_1w": flat.get("return_1w"), "return_1m": flat.get("return_1m"),
                      "overall_score": sc.get("overall"), "partial_score": sc.get("partial_overall"), "stance": sc.get("stance"),
                      "risk_label": (sc.get("risk") or {}).get("label"), "entry_state": (sc.get("entry_setup") or {}).get("state"),
                      "freshness": sc.get("freshness"), "last_bar": sc.get("last_bar"), "added_at": it.added_at.isoformat()})
    return {"id": w.id, "name": w.name, "created_at": w.created_at.isoformat(), "items": items}


@router.get("/watchlists")
async def list_watchlists(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    ws = (await s.scalars(select(Watchlist).where(Watchlist.owner == actor).order_by(Watchlist.id))).all()
    return {**meta(c), "items": [await _wl_dict(s, w) for w in ws], "currency": "INR",
            "note": "Watchlists are for monitoring only. No orders can be placed from this application."}


@router.post("/watchlists", status_code=201)
async def create_watchlist(body: WatchlistIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    if await s.scalar(select(Watchlist.id).where(Watchlist.owner == actor, Watchlist.name == body.name)):
        raise ApiError(409, "duplicate", "A watchlist with this name already exists.")
    if (await s.scalar(select(func.count()).select_from(Watchlist).where(Watchlist.owner == actor)) or 0) >= 50:
        raise ApiError(409, "limit_reached", "Watchlist limit reached.")
    w = Watchlist(owner=actor, name=body.name.strip())
    s.add(w)
    await audit(s, actor, "watchlist.create", "watchlist", None, {"name": body.name})
    await s.commit()
    return await _wl_dict(s, w)


@router.post("/watchlists/{wid}/items", status_code=201)
async def add_watch_item(wid: int, body: WatchItemIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    w = await _watchlist(s, wid, actor)
    inst = await resolve_instrument(s, body.instrument)
    if any(it.instrument_id == inst.id for it in w.items):
        raise ApiError(409, "duplicate", f"{inst.symbol} is already in this watchlist.")
    if len(w.items) >= 200:
        raise ApiError(409, "limit_reached", "A watchlist can hold at most 200 instruments.")
    w.items.append(WatchlistItem(instrument_id=inst.id, note=body.note))
    await s.commit()
    return await _wl_dict(s, w)


@router.delete("/watchlists/{wid}/items/{item_id}")
async def remove_watch_item(wid: int, item_id: int, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    w = await _watchlist(s, wid, actor)
    item = next((it for it in w.items if it.id == item_id), None)
    if item is None:
        raise ApiError(404, "item_not_found", "Item not found in this watchlist.")
    w.items.remove(item)
    await s.commit()
    return await _wl_dict(s, w)


@router.delete("/watchlists/{wid}")
async def delete_watchlist(wid: int, c: Ctx, s: Db, actor: Actor, confirm: bool = False) -> dict[str, Any]:
    w = await _watchlist(s, wid, actor)
    if not confirm:
        raise ApiError(409, "confirmation_required", "Deleting a watchlist needs confirm=true.")
    await s.delete(w)
    await audit(s, actor, "watchlist.delete", "watchlist", wid, {"name": w.name})
    await s.commit()
    return {"deleted": wid}


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
async def list_alerts(c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    rows = (await s.execute(select(Alert, Instrument).outerjoin(Instrument, Instrument.id == Alert.instrument_id)
                            .where(Alert.owner == actor).order_by(Alert.id.desc()))).all()
    events = (await s.execute(select(AlertEvent, Alert).join(Alert, Alert.id == AlertEvent.alert_id).where(Alert.owner == actor)
                              .order_by(AlertEvent.triggered_at.desc(), AlertEvent.id.desc()).limit(100))).all()
    return {**meta(c), "items": [alerts.to_dict(a, i) for a, i in rows], "kinds": alerts.KINDS,
            "history": [{"id": e.id, "alert_id": a.id, "kind": a.kind, "message": e.message, "triggered_at": e.triggered_at.isoformat(),
                         "delivery_status": e.delivery_status} for e, a in events],
            "delivery_note": "Notifications are recorded in this in-app history. No email, SMS or push channel is configured, "
                             "so none is claimed. Alerts never place orders."}


@router.post("/alerts", status_code=201)
async def create_alert(body: alerts.AlertIn, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    try:
        a, created = await alerts.create(s, actor, body)
    except (ValueError, KeyError, TypeError) as e:
        raise ApiError(422, "invalid_alert", str(e) or "invalid alert parameters") from e
    inst = await s.get(Instrument, a.instrument_id) if a.instrument_id else None
    if created:
        await audit(s, actor, "alert.create", "alert", a.id, {"kind": a.kind})
        await s.commit()
    return {**alerts.to_dict(a, inst), "created": created}


class AlertPatch(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


@router.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: int, body: AlertPatch, c: Ctx, s: Db, actor: Actor) -> dict[str, Any]:
    a = await s.get(Alert, alert_id)
    if a is None or a.owner != actor:
        raise ApiError(404, "alert_not_found", "Alert not found.")
    a.status = body.status
    await s.commit()
    return alerts.to_dict(a, await s.get(Instrument, a.instrument_id) if a.instrument_id else None)


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int, c: Ctx, s: Db, actor: Actor, confirm: bool = False) -> dict[str, Any]:
    a = await s.get(Alert, alert_id)
    if a is None or a.owner != actor:
        raise ApiError(404, "alert_not_found", "Alert not found.")
    if not confirm:
        raise ApiError(409, "confirmation_required", "Deleting an alert needs confirm=true.")
    await s.delete(a)
    await audit(s, actor, "alert.delete", "alert", alert_id)
    await s.commit()
    return {"deleted": alert_id}
