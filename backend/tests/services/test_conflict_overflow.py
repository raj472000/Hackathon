"""Regression: a near-zero source value made `diff_pct` overflow NUMERIC(12,4) and failed the whole
prices / theme_refresh job (Azure, 2026-10-29). Run with TEST_DATABASE_URL to check on PostgreSQL,
where the column limit is enforced; SQLite does not enforce numeric precision."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.db.models import Instrument, PriceBar, SourceConflict
from app.domain import reconcile as rc
from app.services import ingestion
from app.services.market_data import HistoryBundle, SourceStatus

COLUMN_LIMIT = 10**8  # NUMERIC(12,4): absolute value must round below 10^8
DAYS = pd.to_datetime(["2019-01-02", "2019-01-03"])  # before the demo history, so no stored bar is touched


def test_diff_pct_is_unchanged_for_ordinary_disagreements():
    assert rc._diff_pct(100.0, 103.0) == pytest.approx(3.0)
    assert rc._diff_pct(287.0, 287.3) == pytest.approx(0.1045, abs=1e-4)
    assert rc._diff_pct(3036196.0, 2638928.0) == pytest.approx(13.0844, abs=1e-4)
    assert rc._diff_pct(0.0, 5.0) == 100.0 and rc._diff_pct(0.0, 0.0) == 0.0


@pytest.mark.parametrize("a,b", [(0.0001, 287.3), (1.0, 3_000_000.0), (1e-9, 1e9), (-1e-6, 50.0)])
def test_diff_pct_from_a_near_zero_chosen_value_is_capped_below_the_column_limit(a, b):
    d = rc._diff_pct(a, b)
    assert d == rc.MAX_DIFF_PCT and round(d, 4) < COLUMN_LIMIT


def test_reconcile_bars_reports_a_tiny_value_as_a_capped_conflict():
    nse = pd.DataFrame({"open": [0.0001, 286.0], "high": [290.0, 290.0], "low": [280.0, 280.0], "close": [287.0, 287.0],
                        "volume": [1.0, 1000.0]}, index=DAYS)
    kite = nse.assign(open=[286.5, 286.0], volume=[3_000_000.0, 1000.0])
    _merged, conflicts, _cov = rc.reconcile_bars({"nse_mcp": nse, "kite_mcp": kite})
    by_field = {c["field"]: c for c in conflicts}
    assert set(by_field) == {"open", "volume"}
    assert all(c["diff_pct"] == rc.MAX_DIFF_PCT and c["chosen"] == "nse_mcp" for c in by_field.values())


def _bundle(symbol: str, frame: pd.DataFrame, conflicts: list[dict]) -> HistoryBundle:
    return HistoryBundle(symbol=symbol, frame=frame.assign(provider="nse_mcp"), conflicts=conflicts, coverage={"nse_mcp": len(frame)},
                         sources=[SourceStatus("nse_mcp", "history", "ok", rows=len(frame))], is_demo=True)


def _frame(**over) -> pd.DataFrame:
    return pd.DataFrame({"open": [100.0, 101.0], "high": [102.0, 103.0], "low": [99.0, 100.0], "close": [101.0, 102.0],
                         "volume": [1000.0, 1100.0], **over}, index=DAYS)


class FakeMarketData:
    def __init__(self, bundles: dict[str, HistoryBundle]):
        self.bundles = bundles

    async def history(self, symbol, start, end, instrument_type=None):
        return self.bundles[symbol]


async def _two_stocks(demo) -> list[Instrument]:
    async with demo.container.sessions() as s:
        return list((await s.scalars(select(Instrument).where(Instrument.instrument_type == "EQ", Instrument.trading_status == "active")
                                     .order_by(Instrument.symbol).limit(2))).all())


async def _run(demo, bundles: dict[str, HistoryBundle]) -> dict:
    c = SimpleNamespace(**{**demo.container.__dict__, "market_data": FakeMarketData(bundles)})
    async with demo.container.sessions() as s:
        return await ingestion.ingest_prices(c, s, list(bundles))


async def _cleanup(demo, ids: list[int]) -> None:
    async with demo.container.sessions() as s:
        await s.execute(delete(PriceBar).where(PriceBar.instrument_id.in_(ids), PriceBar.date < date(2020, 1, 1)))
        await s.execute(delete(SourceConflict).where(SourceConflict.instrument_id.in_(ids), SourceConflict.obs_date < date(2020, 1, 1)))
        await s.commit()


async def test_prices_job_stores_a_near_zero_conflict_instead_of_failing(demo):
    a, b = await _two_stocks(demo)
    nse = _frame(open=[0.0001, 101.0], volume=[1.0, 1100.0])
    kite = nse.assign(open=[100.5, 101.0], volume=[3_000_000.0, 1100.0])
    _m, conflicts, _cov = rc.reconcile_bars({"nse_mcp": nse, "kite_mcp": kite})
    # A raw value far above the column limit must be capped on write too (second guard).
    conflicts.append({"date": "2019-01-03", "field": "close", "values": {"nse_mcp": 1e-7, "kite_mcp": 102.0},
                      "diff_pct": 1.02e11, "chosen": "nse_mcp", "tolerance_pct": 0.1})
    try:
        out = await _run(demo, {a.symbol: _bundle(a.symbol, nse, conflicts), b.symbol: _bundle(b.symbol, _frame(), [])})
        assert out["ok"] == 2 and out["failed"] == 0 and out["bars"] == 4, out
        async with demo.container.sessions() as s:
            rows = list((await s.scalars(select(SourceConflict).where(SourceConflict.instrument_id == a.id,
                                                                      SourceConflict.obs_date < date(2020, 1, 1)))).all())
        assert {(r.field, r.obs_date.isoformat()) for r in rows} == {("open", "2019-01-02"), ("volume", "2019-01-02"), ("close", "2019-01-03")}
        assert all(float(r.diff_pct) == rc.MAX_DIFF_PCT for r in rows)
    finally:
        await _cleanup(demo, [a.id, b.id])


async def test_a_write_the_database_rejects_fails_only_that_instrument(demo, monkeypatch):
    a, b = await _two_stocks(demo)
    real = ingestion._write_history

    async def write(c, s, inst, bundle, replace_all):
        n = await real(c, s, inst, bundle, replace_all)
        if inst.id == a.id:
            s.add(PriceBar(instrument_id=inst.id, date=date(2019, 1, 4), close=None))  # NOT NULL violation
        return n

    monkeypatch.setattr(ingestion, "_write_history", write)
    try:
        out = await _run(demo, {a.symbol: _bundle(a.symbol, _frame(), []), b.symbol: _bundle(b.symbol, _frame(), [])})
        assert out["ok"] == 1 and out["failed"] == 1 and out["bars"] == 2, out
        assert out["failures"][0]["symbol"] == a.symbol and "database rejected write" in out["failures"][0]["reason"]
        async with demo.container.sessions() as s:
            rows = (await s.scalars(select(PriceBar.instrument_id).where(PriceBar.instrument_id.in_([a.id, b.id]),
                                                                        PriceBar.date < date(2020, 1, 1)))).all()
        assert list(rows) == [b.id, b.id], "the failing instrument rolled back alone; the other one was saved"
    finally:
        await _cleanup(demo, [a.id, b.id])
