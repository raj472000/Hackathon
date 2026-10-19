"""Composed refresh jobs. Each is a `JobFn(container, session) -> detail`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import Instrument, MetricSnapshot, PriceBar, ThemeSnapshot, ThemeSnapshotConstituent
from app.domain.rrg import RRGError
from app.services import alerts, ingestion, market_files, metrics, sector_rotation, themes


async def instruments_job(c: Container, s: AsyncSession, refresh_labels: bool = True) -> dict[str, Any]:
    """`refresh_labels=False` (API start-up) syncs from the labels already stored, with no NSE requests."""
    labels = None
    if refresh_labels and not market_files.configured(c):
        # NSE industry labels first, so stocks outside NSE's index files can be given a sector by the sync.
        eq = (await s.scalars(select(Instrument.symbol).where(Instrument.instrument_type == "EQ"))).all()
        labels = await market_files.refresh_industry_labels(c, sorted(eq))
    out = await ingestion.sync_instruments(c, s)
    out["industry_labels"] = labels
    out["builtin_themes_added"] = await themes.seed_builtin(s)
    return out


async def prices_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Daily price refresh, then recompute metrics and invalidate caches.

    With the whole market (UNIVERSE_SCOPE=all) equities come from NSE's daily bhavcopy files; indices and
    the curated reference stocks also use the per-symbol MCP path (with Kite filling gaps)."""
    bulk = await market_files.ingest_bhavcopy(c, s)
    if bulk["status"] == "skipped":
        prices = await ingestion.ingest_prices(c, s)
    else:
        reference = {str(r["symbol"]).upper() for r in ingestion.universe_rows(c)}
        indices = (await s.scalars(select(Instrument.symbol).where(Instrument.instrument_type == "INDEX"))).all()
        prices = {"bhavcopy": bulk, "per_symbol": await ingestion.ingest_prices(c, s, sorted(reference | set(indices)))}
    await ingestion.resolve_broker_identifiers(c, s)
    fundamentals = await ingestion.ingest_fundamentals(c, s)
    m = await metrics.compute_all(c, s)
    await c.cache.delete_prefix("api:")
    return {"prices": prices, "fundamentals": fundamentals, "metrics": m}


async def rrg_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await sector_rotation.refresh(c, s)


async def themes_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await themes.refresh_all(c, s, "scheduled")


async def alerts_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await alerts.evaluate_all(c, s)


SHORT_HISTORY_BARS = 250


async def broker_backfill_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    """After the broker session is authorised: refetch full history for instruments whose stored
    history is short or stale (NSE's history window did not cover them), with Kite filling the gaps,
    then recompute metrics and the rotation graph."""
    counts = dict((await s.execute(select(PriceBar.instrument_id, func.count(PriceBar.id)).group_by(PriceBar.instrument_id))).all())
    last = dict((await s.execute(select(PriceBar.instrument_id, func.max(PriceBar.date)).group_by(PriceBar.instrument_id))).all())
    newest = max(last.values(), default=None)
    # Whole-market equities come from NSE's bhavcopy, where short history means a recent listing; the broker
    # backfill is for indices and the curated reference stocks whose per-symbol NSE history can be short.
    reference = {str(r["symbol"]).upper() for r in ingestion.universe_rows(c)}
    insts = [i for i in (await s.scalars(select(Instrument))).all() if i.instrument_type == "INDEX" or i.symbol in reference]
    short = [i.symbol for i in insts if counts.get(i.id, 0) < SHORT_HISTORY_BARS or (newest and last.get(i.id) and last[i.id] < newest)]
    out: dict[str, Any] = {"short_or_stale": short}
    if short:
        out["prices"] = await ingestion.ingest_prices(c, s, short, full=True)
        out["metrics"] = await metrics.compute_all(c, s)
        try:
            out["rrg"] = await rrg_job(c, s)
        except RRGError as e:
            out["rrg"] = {"status": "failed", "error": str(e)}
        await c.cache.delete_prefix("api:")
    return out


async def themes_need_refresh(s: AsyncSession) -> bool:
    metrics_as_of = await s.scalar(select(func.max(MetricSnapshot.as_of)).where(MetricSnapshot.is_latest.is_(True)))
    if metrics_as_of is None:
        return False
    latest = select(func.max(ThemeSnapshot.id)).group_by(ThemeSnapshot.theme_id)
    snaps = (await s.execute(select(ThemeSnapshot.metrics_as_of, func.count(ThemeSnapshotConstituent.id))
                             .outerjoin(ThemeSnapshotConstituent, ThemeSnapshotConstituent.snapshot_id == ThemeSnapshot.id)
                             .where(ThemeSnapshot.id.in_(latest)).group_by(ThemeSnapshot.id, ThemeSnapshot.metrics_as_of))).all()
    if not snaps:
        return True
    return any(n == 0 or a is None or a < metrics_as_of for a, n in snaps)


async def full_refresh(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Everything in dependency order. Used at first start and by the manual 'refresh all'."""
    out: dict[str, Any] = {"instruments": await instruments_job(c, s)}
    out.update(await prices_job(c, s))
    try:
        out["rrg"] = await rrg_job(c, s)
        out["metrics"] = await metrics.compute_all(c, s)  # pick up sector context from the new rotation data
    except RRGError as e:
        out["rrg"] = {"status": "failed", "error": str(e)}
    # Re-rank themes when their latest snapshot predates the metrics or is empty. A new snapshot is
    # stored as the next revision of the month; earlier snapshots are never modified.
    if await themes_need_refresh(s):
        first = not await s.scalar(select(func.count()).select_from(ThemeSnapshot))
        try:
            out["themes"] = await themes.refresh_all(c, s, "initial" if first else "data_refresh")
        except themes.ThemeError as e:
            out["themes"] = {"status": "failed", "error": str(e)}
    out["alerts"] = await alerts.evaluate_all(c, s)
    return out
