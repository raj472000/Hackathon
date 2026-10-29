"""Kite MCP fallbacks (news prices, indices, movers, live open interest), the macro interpretation and the
FII/DII history (NSDL FPI archive). The NSDL page was recorded from the live archive on 2026-10-26."""

import csv
import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.connectors.base import ProviderUnavailable
from app.connectors.nsdl import parse_archive
from app.core.config import Settings
from app.db.models import FpiDaily
from app.db.session import create_all, make_engine, make_sessionmaker
from app.domain import derivatives as dv
from app.services import derivatives, flows, macro, news

FX = Path(__file__).parents[1] / "fixtures" / "live" / "market_pulse"


class Clock:
    def now(self):
        return datetime(2026, 10, 26, 7, 0, tzinfo=UTC)  # Monday 12:30 IST


class FakeKite:
    """Kite MCP stand-in answering get_quotes from a dict of raw Kite quotes."""

    def __init__(self, quotes=None, fail=None):
        self.client = object()
        self.q, self.fail, self.asked = quotes or {}, fail, []

    async def quotes_raw(self, keys):
        self.asked.append(list(keys))
        if self.fail:
            raise ProviderUnavailable(self.fail, "login_required", "kite_mcp")
        return {k: v for k, v in self.q.items() if k in keys}


def kq(last, prev, oi=None, volume=1000):
    return {"last_price": last, "ohlc": {"open": prev, "high": last, "low": prev, "close": prev}, "oi": oi, "volume": volume,
            "timestamp": "2026-10-26T12:29:00+05:30"}


class DownNse:
    async def get_quote(self, sym, instrument_type="EQ"):
        raise ProviderUnavailable("nse_cm_market: session could not be re-established", "unavailable", "nse_mcp")

    async def bulk_quotes(self, syms):
        raise ProviderUnavailable("nse_bhavcopy: HTTP 504", "unavailable", "nse_mcp")

    async def index_quote(self, name):
        raise ProviderUnavailable("nse_cm_market: session could not be re-established", "unavailable", "nse_mcp")

    async def live_movers(self, limit, index_name=None):
        raise ProviderUnavailable("nse_cm_market: session could not be re-established", "unavailable", "nse_mcp")

    def health(self):
        return {}


def ctx(**kw):
    base = dict(settings=SimpleNamespace(is_demo=False), clock=Clock(), nse=DownNse(), kite=FakeKite(), filings=SimpleNamespace(available=False))
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ news prices
async def test_news_prices_come_from_kite_when_nse_fails():
    c = ctx(kite=FakeKite({"NSE:ABREL": kq(1079.1, 1042.4), "NSE:AAVAS": kq(1127.3, 1114.1)}))
    out, state = await news.live_quotes(c, ["ABREL", "AAVAS", "NOPE"])
    assert out["ABREL"]["basis"] == "live (Zerodha Kite)" and out["ABREL"]["prev_close"] == 1042.4
    assert out["ABREL"]["change_pct"] == pytest.approx((1079.1 / 1042.4 - 1) * 100, abs=1e-3)
    assert state["live"] == 0 and state["broker"] == 2 and state["missing"] == 1 and state["state"] == "degraded"


async def test_news_prices_report_kite_failure_without_inventing_prices():
    c = ctx(kite=FakeKite(fail="broker login required"))
    out, state = await news.live_quotes(c, ["ABREL"])
    assert out == {} and state["state"] == "unavailable" and state["broker_state"]["state"] == "login_required"


# ------------------------------------------------------------------ indices
async def test_indices_fall_back_to_kite_and_sensex_prefers_kite():
    c = ctx(kite=FakeKite({"NSE:NIFTY 50": kq(22650.35, 22600.0), "NSE:NIFTY BANK": kq(54117.85, 54287.35),
                           "BSE:SENSEX": kq(73308.96, 73124.0)}), chart=SimpleNamespace())
    rows = {r["key"]: r for r in await macro.headline_indices(c)}
    assert rows["NIFTY 50"]["value"] == 22650.35 and rows["NIFTY 50"]["source"].startswith("Zerodha Kite")
    assert "session could not be re-established" in rows["NIFTY 50"]["note"]
    assert rows["SENSEX"]["value"] == 73308.96 and rows["SENSEX"]["change"] == pytest.approx(184.96)
    assert rows["NIFTY BANK"]["change_pct"] == pytest.approx(-0.312, abs=1e-3)


# ------------------------------------------------------------------ movers and live open interest
def _fo_rows():
    with zipfile.ZipFile(FX / "fo_bhavcopy_20261023_subset.csv.zip") as z:
        return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode())))


async def test_movers_ranked_from_kite_over_fo_stocks_when_nse_fails(monkeypatch):
    async def latest(c, day=None):
        return date(2026, 10, 23), _fo_rows()

    monkeypatch.setattr(derivatives, "latest_fo", latest)
    c = ctx(kite=FakeKite({"NSE:INFY": kq(1140, 1153.8), "NSE:SWIGGY": kq(330, 300), "NSE:NYKAA": kq(320, 322), "NSE:RELIANCE": kq(1400, 1390)}))
    out = await derivatives.live_movers(c, "all", 2)
    assert out["state"] == "live" and out["source"].startswith("Zerodha Kite") and "session could not" in out["fallback_reason"]
    assert [r["symbol"] for r in out["gainers"]] == ["SWIGGY", "RELIANCE"] and [r["symbol"] for r in out["losers"]] == ["INFY", "NYKAA"]


def test_live_futures_sum_oi_over_quoted_live_contracts():
    prev = [r for r in _fo_rows() if r["TckrSymb"] == "ABCAPITAL"]
    near = min(prev, key=lambda r: r["XpryDt"])
    quotes = {f"NFO:{r['FinInstrmNm']}": {"last_price": 350.0, "prev_close": 346.5, "oi": float(r["OpnIntrst"]) * 1.1, "volume": 3100.0}
              for r in prev}
    (row,) = dv.live_futures(prev, quotes, "2026-10-26")
    prev_oi = sum(float(r["OpnIntrst"]) for r in prev)
    assert row.open_interest == pytest.approx(prev_oi * 1.1) and row.oi_change_pct == pytest.approx(10.0)
    assert row.price_change_pct == pytest.approx((350 / 346.5 - 1) * 100, abs=1e-3) and row.buildup == "long_buildup"
    assert row.near_expiry == near["XpryDt"]
    # an expired contract is left out on both sides, so the change still compares like with like
    (later,) = dv.live_futures(prev, quotes, "2026-10-28")
    assert later.near_expiry > "2026-10-28" and later.oi_change_pct == pytest.approx(10.0)


async def test_oi_dashboard_goes_live_from_kite_until_todays_file_is_published(monkeypatch):
    rows = _fo_rows()

    async def latest(c, day=None):
        return date(2026, 10, 23), rows

    monkeypatch.setattr(derivatives, "latest_fo", latest)
    quotes = {f"NFO:{r['FinInstrmNm']}": kq(float(r["SttlmPric"]) * 1.01, float(r["SttlmPric"]), oi=float(r["OpnIntrst"]) * 1.05)
              for r in rows if r["FinInstrmTp"] in ("STF", "IDF")}
    c = ctx(kite=FakeKite(quotes))
    live = await derivatives.oi_dashboard(c)
    assert live["mode"] == "live" and live["date"] == "2026-10-26" and live["previous_file"] == "2026-10-23"
    assert live["source"].startswith("Zerodha Kite") and live["buildup_counts"]["long_buildup"] == live["underlyings"]
    c.kite = FakeKite(fail="broker login required")
    eod = await derivatives.oi_dashboard(c)
    assert eod["mode"] == "end_of_day" and eod["date"] == "2026-10-23" and eod["live_state"]["state"] == "login_required"
    past = await derivatives.oi_dashboard(c, date(2026, 10, 23))
    assert past["mode"] == "end_of_day" and past["live_state"] is None


# ------------------------------------------------------------------ macro interpretation
def _row(key, **kw):
    return {"key": key, "unit": "", "freshness": "fresh", **kw}


def test_interpretation_risky_safe_neutral_and_insufficient():
    risky = [_row("indiavix", value=24, change_pct=12), _row("vix", value=30), _row("brent", value=110, change_pct=4),
             _row("usdinr", change_pct=0.5), _row("dxy", change_pct=0.8), _row("us10y", value=5.0, change_bps=10)]
    r = macro.interpret(risky)
    assert r["stance"] == "Risky" and r["net_score"] >= 3 and r["risk_on"] == 0 and r["version"] == macro.INTERPRETATION_VERSION
    safe = [_row("indiavix", value=11, change_pct=-9), _row("vix", value=13), _row("brent", value=70, change_pct=-4),
            _row("usdinr", change_pct=-0.4), _row("sp500", change_pct=1.5), _row("nasdaq", change_pct=1.8)]
    assert macro.interpret(safe)["stance"] == "Safe"
    mixed = [_row("indiavix", value=16, change_pct=1), _row("vix", value=18), _row("brent", value=85, change_pct=0.5), _row("usdinr", change_pct=0.1),
             _row("dxy", change_pct=0.1)]
    assert macro.interpret(mixed)["stance"] == "Neutral"
    few = [_row("vix", value=30), _row("brent", value=110)]
    assert macro.interpret(few)["stance"] == "Insufficient data"


def test_interpretation_skips_stale_and_missing_indicators():
    r = macro.interpret([_row("india_cpi", value=8, freshness="stale"), _row("vix", value=None)])
    assert any("India CPI inflation (stale)" in x for x in r["skipped"]) and any("CBOE VIX level (not available)" in x for x in r["skipped"])
    assert r["signals_read"] == 0


# ------------------------------------------------------------------ FII/DII history
def test_nsdl_archive_parses_every_day_and_ignores_summary_blocks():
    rows = parse_archive((FX / "nsdl_fpi_archive_2026-10-20.html").read_text())
    days = [r["reporting_date"] for r in rows]
    assert days[0] == date(2026, 10, 1) and days == sorted(set(days)) and all(d.month == 10 for d in days)
    first = rows[0]  # 01-Oct-2026 as published: equity via stock exchange 17667.90 / 27237.47 / (9569.57)
    assert (first["equity_exchange_buy"], first["equity_exchange_sell"], first["equity_exchange_net"]) == (17667.90, 27237.47, -9569.57)
    assert first["equity_net"] == -9231.88 and first["usd_inr"] == pytest.approx(95.9832)
    # month-to-date and year-to-date totals are not mistaken for a day
    assert max(abs(r["equity_exchange_buy"]) for r in rows) < 100_000


@pytest.fixture
async def db(tmp_path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/h.db")
    engine = make_engine(settings.database_url)  # type: ignore[arg-type]
    await create_all(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


class FakeNsdl:
    def __init__(self, rows, fail_month=None):
        self.rows, self.fail_month, self.months = rows, fail_month, []

    async def month(self, d):
        self.months.append(d)
        if self.fail_month and d.strftime("%Y-%m") == self.fail_month:
            raise ProviderUnavailable("NSDL archive returned HTTP 503", "unavailable", "nsdl_fpi")
        return [r for r in self.rows if (r["reporting_date"].year, r["reporting_date"].month) == (d.year, d.month)]

    def status(self):
        return {"state": "ok"}


async def test_history_refresh_stores_each_month_and_reports_failures(db):
    rows = parse_archive((FX / "nsdl_fpi_archive_2026-10-20.html").read_text())
    nsdl = FakeNsdl(rows, fail_month="2026-09")
    c = SimpleNamespace(settings=SimpleNamespace(is_demo=False), clock=Clock(), nsdl=nsdl, filings=SimpleNamespace(available=False))
    async with db() as s:
        out = await flows.refresh_history(c, s, date(2026, 9, 15), date(2026, 10, 26))
        again = await flows.refresh_history(c, s, date(2026, 10, 1), date(2026, 10, 26))  # idempotent
        stored = (await s.scalars(select(FpiDaily))).all()
        h = await flows.history(c, s, date(2026, 10, 1), date(2026, 10, 31))
    assert [m.strftime("%Y-%m") for m in nsdl.months[:2]] == ["2026-09", "2026-10"]
    assert out["state"] == "ok" and out["months"] == ["2026-10"] and out["failed"][0]["month"] == "2026-09"
    assert again["fpi_days"] == len(rows) and len(stored) == len(rows)
    assert len(h["rows"]) == len(rows) and h["rows"][-1]["fpi"]["net"] == -9569.57
    assert h["totals"]["fpi_equity_exchange_net"] == pytest.approx(round(sum(r["equity_exchange_net"] for r in rows), 2))


# ------------------------------------------------------------------ Nifty 50 ticker
async def test_nifty50_ticker_uses_nse_then_kite_and_leaves_unpriced_stocks_empty(monkeypatch):
    from app.core.cache import MemoryCache

    async def symbols(c, segment):
        return ["INFY", "RELIANCE", "TCS"], "nifty constituents"

    async def index(c, name, label):
        return {"key": name, "value": 22753.2, "change_pct": 0.68, "source": "NSE CM market MCP"}

    class HalfNse(DownNse):
        async def live_movers(self, limit, index_name=None):
            assert (limit, index_name) == (50, "NIFTY")
            return {"data": {"gainers": [{"symbol": "INFY", "ltp": 1150.0, "prev_price": 1140.0, "perChange": 0.88}], "losers": [],
                             "gainersUpdatedAt": "2026-10-26T07:00:00Z"}, "source_id": "nse_get_market_movers", "retrieved_at": "x"}

    monkeypatch.setattr(derivatives, "_segment_symbols", symbols)
    monkeypatch.setattr(macro, "_nse_index", index)
    kite = FakeKite({"NSE:RELIANCE": kq(1201.8, 1190.0)})
    c = ctx(nse=HalfNse(), kite=kite, cache=MemoryCache())
    out = await derivatives.nifty50_ticker(c)
    by = {i["symbol"]: i for i in out["items"]}
    assert [i["symbol"] for i in out["items"]] == ["INFY", "RELIANCE", "TCS"]  # alphabetical, every constituent listed
    assert by["INFY"]["source"] == "NSE CM market MCP" and by["INFY"]["change_pct"] == 0.88
    assert by["RELIANCE"]["source"].startswith("Zerodha Kite") and by["RELIANCE"]["change_pct"] == pytest.approx((1201.8 / 1190 - 1) * 100, abs=1e-3)
    assert by["TCS"]["last_price"] is None  # no source priced it: shown empty, never invented
    assert kite.asked == [["NSE:RELIANCE", "NSE:TCS"]]  # only the gaps go to Kite, in one batch
    assert out["state"] == "degraded" and out["priced"] == 2 and out["index"]["value"] == 22753.2
    assert (await derivatives.nifty50_ticker(c))["cache"] == "hit"


# ------------------------------------------------------------------ market breadth
async def test_breadth_counts_advances_and_declines_from_kite_quotes():
    from app.services import kite_fallback

    c = ctx(kite=FakeKite({"NSE:A": kq(110, 100), "NSE:B": kq(90, 100), "NSE:C": kq(100, 100), "NSE:D": kq(105, 100)}))
    data, state = await kite_fallback.breadth(c, ["A", "B", "C", "D", "UNPRICED"])
    assert state["state"] == "ok"
    assert (data["advances"], data["declines"], data["unchanged"], data["priced"], data["universe"]) == (2, 1, 1, 4, 5)


async def test_breadth_reports_kite_failure_without_inventing_counts():
    from app.services import kite_fallback

    data, state = await kite_fallback.breadth(ctx(kite=FakeKite(fail="broker login required")), ["A"])
    assert data is None and state["state"] == "login_required"


async def test_overview_breadth_falls_back_to_kite_when_nse_fails(tmp_path):
    from app.api.v1 import market as market_routes
    from app.core.cache import MemoryCache
    from app.db.models import Instrument

    class NoBreadthNse(DownNse):
        async def market_breadth(self):
            raise ProviderUnavailable("nse_bhavcopy: session could not be re-established", "unavailable", "nse_mcp")

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/b.db")
    await create_all(engine)
    sessions = make_sessionmaker(engine)
    async with sessions() as s:
        s.add_all([Instrument(symbol=x, name=x, exchange="NSE", instrument_type="EQ", currency="INR", trading_status="active")
                   for x in ("A", "B", "C")])
        await s.commit()
    c = ctx(nse=NoBreadthNse(), cache=MemoryCache(), sessions=sessions,
            kite=FakeKite({"NSE:A": kq(110, 100), "NSE:B": kq(90, 100), "NSE:C": kq(120, 100)}))
    m = await market_routes._check_breadth(c)
    assert m["state"] == "ok" and m["provider"] == "kite_mcp" and m["source"].startswith("Zerodha Kite")
    assert (m["data"]["advances"], m["data"]["declines"]) == (2, 1) and "session could not" in m["fallback_reason"]
    # Kite down too: the NSE reason is shown, no counts are invented
    c.kite = FakeKite(fail="broker login required")
    await c.cache.delete_prefix("overview:")
    down = await market_routes._check_breadth(c)
    assert down["state"] == "unavailable" and "session could not" in down["reason"] and "data" not in down
    await engine.dispose()
