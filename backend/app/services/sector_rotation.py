"""Sector rotation: builds sector series, computes the RRG and stores 12-hourly snapshots."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import Instrument, SectorRotationSnapshot
from app.domain import rrg
from app.services.ingestion import load_bars

MIN_COMPOSITE_MEMBERS = 3  # for the benchmark fallback
MIN_SECTOR_MEMBERS = 1  # a sector is always plotted when any constituent has history; thin composites are flagged
STALE_DAYS = 5


def slot_for(now: datetime) -> str:
    """12-hour bucket used as the idempotency key of a refresh."""
    n = now.astimezone(UTC)
    return f"{n.date().isoformat()}T{0 if n.hour < 12 else 12:02d}"


def build_series(instruments: list[Instrument], bars: dict[int, pd.DataFrame], benchmark_symbol: str) -> dict[str, Any]:
    """Benchmark and per-sector close series with a note on how each was obtained.

    A sector uses its mapped index when that index has history; otherwise an equal-weight composite
    of its constituents (labelled as such). The benchmark falls back to a composite of the whole
    equity universe when the configured index has no history.
    """
    by_symbol = {i.symbol: i for i in instruments}
    close = lambda i: bars[i.id]["close"] if i.id in bars and not bars[i.id].empty else None  # noqa: E731
    equities = [i for i in instruments if i.instrument_type == "EQ" and close(i) is not None]

    bench_inst = by_symbol.get(benchmark_symbol)
    bench = close(bench_inst) if bench_inst else None
    bench_kind = "index"
    if bench is None:
        bench = rrg.equal_weight_composite({i.symbol: close(i) for i in equities}, MIN_COMPOSITE_MEMBERS)
        bench_kind = "universe_composite"

    sectors: dict[str, dict[str, Any]] = {}
    for name in sorted({i.sector for i in equities if i.sector}):
        members = [i for i in equities if i.sector == name]
        idx_symbol = next((i.sector_index_symbol for i in members if i.sector_index_symbol), None)
        idx_inst = by_symbol.get(idx_symbol) if idx_symbol else None
        series = close(idx_inst) if idx_inst else None
        kind, note = "index", f"sector index {idx_symbol}"
        if series is None:
            with_history = [i for i in members if close(i) is not None]
            series = rrg.equal_weight_composite({i.symbol: close(i) for i in with_history}, MIN_SECTOR_MEMBERS)
            kind = "composite"
            thin = "" if len(with_history) >= MIN_COMPOSITE_MEMBERS else (
                f" ({', '.join(i.symbol for i in with_history)} only: a thin proxy for the sector, read with caution)")
            note = (f"equal-weight composite of {len(with_history)} constituent(s) in the tracked universe{thin}"
                    + (f"; index {idx_symbol} has no history from the configured sources" if idx_symbol else "; no sector index mapped"))
            if series is None:
                kind, note = "missing", "no constituent with price history and no sector index"
        sectors[name] = {"series": series, "kind": kind, "note": note, "members": len(members), "index_symbol": idx_symbol}
    return {"benchmark": bench, "benchmark_kind": bench_kind, "sectors": sectors}


def compute(series: dict[str, Any], benchmark_symbol: str, params: rrg.RRGParams | None = None,
            end: Any = None, today: Any = None) -> dict[str, Any]:
    bench = series["benchmark"]
    if bench is None or bench.empty:
        raise rrg.RRGError("benchmark history is unavailable; ingest prices for the benchmark or enough constituents first")
    sec = {k: v["series"] for k, v in series["sectors"].items() if v["series"] is not None}
    if end is not None:
        bench = bench.loc[:end]
        sec = {k: v.loc[:end] for k, v in sec.items()}
    res = rrg.compute_rrg(sec, bench, params)
    payload = res.to_dict()
    flags: list[str] = []
    last = bench.index[-1].date()
    if today is not None and (today - last).days > STALE_DAYS:
        flags.append(f"benchmark prices are stale: last bar {last.isoformat()}")
    if series["benchmark_kind"] != "index":
        flags.append(f"benchmark {benchmark_symbol} has no history; an equal-weight composite of the tracked universe is used instead")
    for name, meta in series["sectors"].items():
        s = payload["sectors"].get(name) or asdict(rrg.SectorRRG(name, "missing", reason=meta["note"]))
        s.update(series_kind=meta["kind"], series_note=meta["note"], members=meta["members"], index_symbol=meta["index_symbol"])
        payload["sectors"][name] = s
        if meta["kind"] == "composite":
            flags.append(f"{name}: {meta['note']}")
        if s["status"] != "ok":
            flags.append(f"{name}: {s['status']} ({s.get('reason')})")
        elif s.get("last_bar") and s["last_bar"] < last.isoformat():
            flags.append(f"{name}: last bar {s['last_bar']} is older than the benchmark's")
    payload.update(
        benchmark=benchmark_symbol, benchmark_kind=series["benchmark_kind"], data_as_of=last.isoformat(), flags=flags,
        method=(rrg.__doc__ or "").strip(),
        price_basis="split-adjusted closes (not total return)",
        interpretation="Quadrants describe relative strength and its momentum against the benchmark over the lookback. "
                       "They are descriptive and do not predict future performance.",
    )
    ok = sum(1 for s in payload["sectors"].values() if s["status"] == "ok")
    payload["coverage"] = {"sectors_total": len(payload["sectors"]), "sectors_plotted": ok,
                           "composites": sum(1 for m in series["sectors"].values() if m["kind"] == "composite")}
    return payload


async def refresh(c: Container, s: AsyncSession, params: rrg.RRGParams | None = None) -> dict[str, Any]:
    """Compute and store the snapshot for the current 12-hour slot. Idempotent per slot. If the
    computation fails nothing is written, so the previous valid snapshot keeps being served."""
    insts = list((await s.scalars(select(Instrument))).all())
    bars = await load_bars(s)
    payload = compute(build_series(insts, bars, c.benchmark_symbol), c.benchmark_symbol, params, today=c.clock.now().date())
    if payload["coverage"]["sectors_plotted"] == 0:
        raise rrg.RRGError("no sector has enough history to plot")
    slot = slot_for(c.clock.now())
    key = "default" if params is None else f"{params.ratio_window}-{params.momentum_window}-{params.trail}"
    row = await s.scalar(select(SectorRotationSnapshot).where(
        SectorRotationSnapshot.benchmark == c.benchmark_symbol, SectorRotationSnapshot.slot == slot, SectorRotationSnapshot.params_key == key))
    created = row is None
    if row is None:
        row = SectorRotationSnapshot(benchmark=c.benchmark_symbol, slot=slot, params_key=key, payload={}, version=rrg.VERSION)
        s.add(row)
    row.payload, row.coverage, row.version = payload, payload["coverage"], rrg.VERSION
    row.data_as_of = datetime.fromisoformat(payload["data_as_of"]).date()
    row.is_demo, row.computed_at = c.settings.is_demo, c.clock.now()
    await s.commit()
    return {"snapshot_id": row.id, "slot": slot, "created": created, **payload["coverage"], "data_as_of": payload["data_as_of"]}


async def latest(s: AsyncSession, benchmark: str) -> SectorRotationSnapshot | None:
    return await s.scalar(select(SectorRotationSnapshot).where(SectorRotationSnapshot.benchmark == benchmark,
                                                               SectorRotationSnapshot.params_key == "default")
                          .order_by(SectorRotationSnapshot.computed_at.desc()).limit(1))
