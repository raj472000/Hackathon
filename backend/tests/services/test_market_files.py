"""Whole-market universe and bhavcopy prices. NSE's files are served by a MockTransport in the formats
recorded on 15-16 Oct 2026 (EQUITY_L.csv, Nifty Total Market constituents, UDiFF bhavcopy, corporate
actions JSON); the database is a throwaway SQLite file."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.nse_filings import NseFilingsProvider
from app.core.cache import MemoryCache
from app.core.config import Settings
from app.db.models import CorporateActionRow, Instrument, PriceBar
from app.db.session import create_all, make_engine, make_sessionmaker
from app.services import ingestion, market_files

ARCH = "https://nsearchives.nseindia.com"
EQUITY_L = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "ABB,ABB India Limited,EQ,01-JAN-1995,2,1,INE117A01022,2\n"
            "SMALLCO,Small Company Limited,EQ,01-JAN-2010,10,1,INE000S01010,10\n"
            "SPLITCO,Split Company Limited,BE,01-JAN-2012,1,1,INE000T01010,1\n"
            "BONDX,Some Bond,N1,01-JAN-2020,100,1,INE000B01010,100\n")
INDUSTRY = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
            "ABB India Ltd.,Capital Goods,ABB,EQ,INE117A01022\n"
            "Split Company Ltd.,Fast Moving Consumer Goods,SPLITCO,BE,INE000T01010\n")
REFERENCE = [{"symbol": "NIFTY 50", "name": "Nifty 50", "sector": None, "industry": None, "sector_kind": "index", "instrument_type": "INDEX",
              "sector_index_symbol": None},
             {"symbol": "HUL", "name": "Hindustan Unilever", "sector": "FMCG", "industry": None, "sector_kind": "industrial",
              "instrument_type": "EQ", "sector_index_symbol": "NIFTY FMCG"},
             {"symbol": "ABB", "name": "ABB India", "sector": "Capital Goods", "industry": None, "sector_kind": "industrial",
              "instrument_type": "EQ", "sector_index_symbol": None}]
TODAY = date(2026, 10, 16)
SPLIT_DAY = date(2026, 10, 12)  # SPLITCO 1:5 split: price drops from ~500 to ~100
HOLIDAY = date(2026, 10, 2)


def _bhav(day: date) -> bytes:
    head = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
    n = (day - date(2026, 9, 1)).days
    split_px = 100 + n if day >= SPLIT_DAY else (100 + n) * 5
    rows = [f"{day},{day},CM,NSE,STK,1,INE117A01022,ABB,EQ,{6000+n},{6010+n},{5990+n},{6000+n},{6000+n},{5999+n},1000,6000000",
            f"{day},{day},CM,NSE,STK,2,INE000S01010,SMALLCO,EQ,{50+n},{51+n},{49+n},{50+n},{50+n},{49+n},500,25000",
            f"{day},{day},CM,NSE,STK,3,INE000T01010,SPLITCO,BE,{split_px},{split_px},{split_px},{split_px},{split_px},{split_px},10,1000",
            f"{day},{day},CM,NSE,STK,4,INE000B01010,BONDX,N1,100,100,100,100,100,100,1,100"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv", head + "\n".join(rows) + "\n")
    return buf.getvalue()


def _handler(calls: list[str]):
    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        p = req.url.path
        if p.endswith("EQUITY_L.csv"):
            return httpx.Response(200, text=EQUITY_L)
        if p.endswith("ind_niftytotalmarket_list.csv"):
            return httpx.Response(200, text=INDUSTRY)
        if p.endswith("corporates-corporateActions"):
            return httpx.Response(200, json=[{"symbol": "SPLITCO", "exDate": SPLIT_DAY.strftime("%d-%b-%Y"),
                                              "subject": "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"},
                                             {"symbol": "ABB", "exDate": "05-Oct-2026", "subject": "Dividend - Rs 10 Per Share"}])
        if "BhavCopy_NSE_CM" in p:
            day = datetime.strptime(p.split("_0_0_0_")[1][:8], "%Y%m%d").date()
            if day == HOLIDAY or day > TODAY:
                return httpx.Response(404)
            return httpx.Response(200, content=_bhav(day))
        return httpx.Response(404)
    return h


@pytest.fixture
async def env(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_mode="live", database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
                        universe_scope="all", history_years=1, nse_filings_base_url="https://www.nseindia.com/api",
                        nse_equity_list_url=f"{ARCH}/content/equities/EQUITY_L.csv",
                        nse_industry_list_url=f"{ARCH}/content/indices/ind_niftytotalmarket_list.csv",
                        nse_bhavcopy_url_template=f"{ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{{yyyymmdd}}_F_0000.csv.zip")
    calls: list[str] = []
    filings = NseFilingsProvider(settings.nse_filings_base_url, ["nsearchives.nseindia.com"], tmp_path / "cache", user_agent="t",
                                 min_interval=0, transport=httpx.MockTransport(_handler(calls)))
    engine = make_engine(settings.database_url)  # type: ignore[arg-type]
    await create_all(engine)

    class Clock:
        def now(self):
            return datetime(2026, 10, 16, 12, tzinfo=UTC)

    c = SimpleNamespace(settings=settings, filings=filings, engine=engine, sessions=make_sessionmaker(engine), clock=Clock(), cache=MemoryCache())
    yield c, calls
    await filings.close()
    await engine.dispose()


async def test_universe_is_every_listed_equity_with_nse_industries_and_reference_overrides(env):
    c, _ = env
    rows, info = await market_files.universe_all(c, REFERENCE)
    by = {r["symbol"]: r for r in rows}
    assert {"ABB", "SMALLCO", "SPLITCO", "NIFTY 50", "HUL"} <= set(by) and "BONDX" not in by  # bonds are not in UNIVERSE_SERIES
    assert by["SPLITCO"]["sector"] == "FMCG" and by["SPLITCO"]["sector_index_symbol"] == "NIFTY FMCG"  # NSE name mapped to app sector
    assert by["SMALLCO"]["sector"] is None and "not classified" in by["SMALLCO"]["classification_source"]
    assert by["ABB"]["sector"] == "Capital Goods" and by["ABB"]["isin"] == "INE117A01022"  # reference row kept, ISIN filled in
    assert info["listed"] == 4 and info["classified"] == 3


async def test_bhavcopy_loads_every_stock_adjusts_splits_and_skips_holidays(env):
    c, calls = env
    async with c.sessions() as s:
        await ingestion.sync_instruments(c, s)  # reference file + listed equities (scope all)
        out = await market_files.ingest_bhavcopy(c, s)
        assert out["status"] == "ok" and out["holidays"] >= 1 and out["instruments_updated"] >= 3, out
        ids = {i.symbol: i.id for i in (await s.scalars(select(Instrument))).all()}
        n_abb = await s.scalar(select(func.count()).select_from(PriceBar).where(PriceBar.instrument_id == ids["ABB"]))
        lo = date.fromisoformat(out["from"])
        days = [lo + timedelta(days=x) for x in range((TODAY - lo).days + 1)]
        assert n_abb == len([d for d in days if d.weekday() < 5 and d != HOLIDAY and d >= date(2026, 9, 1) - timedelta(days=400)])
        assert await s.scalar(select(func.count()).select_from(PriceBar).where(PriceBar.date == HOLIDAY)) == 0
        sp = {b.date: float(b.close) for b in (await s.scalars(select(PriceBar).where(PriceBar.instrument_id == ids["SPLITCO"]))).all()}
        before, after = sp[SPLIT_DAY - timedelta(days=3)], sp[SPLIT_DAY]
        assert abs(after / before - 1) < 0.1  # back-adjusted: no 80% "crash" on the ex-date
        acts = (await s.scalars(select(CorporateActionRow))).all()
        assert [(a.kind, float(a.ratio)) for a in acts] == [("split", 5.0)]
        # A second run reads only new days: files are cached and nothing is rebuilt.
        n_calls = len(calls)
        again = await market_files.ingest_bhavcopy(c, s)
        assert again["instruments_rebuilt"] == 0
        assert not [p for p in calls[n_calls:] if "BhavCopy" in p and p.split("_0_0_0_")[1][:8] < "20261016"]


async def test_reference_scope_and_demo_mode_do_not_touch_the_whole_market(env):
    c, calls = env
    c.settings = c.settings.model_copy(update={"universe_scope": "reference"})
    async with c.sessions() as s:
        assert (await market_files.ingest_bhavcopy(c, s))["status"] == "skipped"
    assert market_files.configured(c) == "UNIVERSE_SCOPE is not 'all'" and calls == []


def test_unclassified_stocks_take_the_sector_nse_classified_peers_with_the_same_industry_label_share():
    eq = [{"symbol": "A", "sector": "Information Technology"}, {"symbol": "B", "sector": "Information Technology"},
          {"symbol": "C", "sector": "Information Technology"}, {"symbol": "D", "sector": None},
          {"symbol": "E", "sector": "Chemicals"}, {"symbol": "F", "sector": "Capital Goods"}, {"symbol": "G", "sector": None},
          {"symbol": "H", "sector": None}]
    labels = {"A": "Computers - Software", "B": "Computers - Software", "C": "Computers - Software", "D": "Computers - Software",
              "E": "Miscellaneous", "F": "Miscellaneous", "G": "Miscellaneous"}
    n = market_files.classify_by_label(eq, labels, {"Information Technology": "NIFTY IT"})
    by = {r["symbol"]: r for r in eq}
    assert n == 1 and by["D"]["sector"] == "Information Technology" and by["D"]["sector_index_symbol"] == "NIFTY IT"
    assert by["D"]["classification_source"] == "NSE industry Computers - Software: 3 of 3 classified peers" and by["D"]["industry"] == "Computers - Software"
    assert by["G"]["sector"] is None  # 'Miscellaneous' splits 50/50 between two sectors: below the 60% agreement rule
    assert by["H"]["sector"] is None and "industry" not in by["H"]  # no NSE label: stays unclassified


async def test_label_refresh_remembers_companies_nse_has_no_label_for_and_startup_skips_it(env, monkeypatch, tmp_path):
    """API start-up hung for minutes (19 Oct 2026): the start-up sync asked NSE for ~1,700 industry labels,
    and companies without one were asked again on every run."""
    from app.connectors import nse_filings as nf
    from app.services import pipeline

    c, _ = env
    c.settings = c.settings.model_copy(update={"nse_filings_cache_dir": tmp_path / "nse"})
    asked: list[str] = []

    async def company_industry(p, symbol, today):
        asked.append(symbol)
        return "Capital Goods" if symbol == "ABB" else None

    monkeypatch.setattr(nf, "company_industry", company_industry)
    first = await market_files.refresh_industry_labels(c, ["ABB", "SMALLCO", "SPLITCO"])
    assert first["fetched"] == 1 and first["no_label"] == 2 and sorted(asked) == ["ABB", "SMALLCO", "SPLITCO"]
    asked.clear()
    again = await market_files.refresh_industry_labels(c, ["ABB", "SMALLCO", "SPLITCO"])
    assert asked == [] and again["skipped_recently_checked"] == 2  # not asked again within LABEL_RECHECK_DAYS
    c.clock.now = lambda: datetime(2026, 10, 16, tzinfo=UTC) + timedelta(days=market_files.LABEL_RECHECK_DAYS)
    await market_files.refresh_industry_labels(c, ["ABB", "SMALLCO", "SPLITCO"])
    assert sorted(asked) == ["SMALLCO", "SPLITCO"]  # rechecked once the window has passed
    asked.clear()
    async with c.sessions() as s:
        out = await pipeline.instruments_job(c, s, refresh_labels=False)  # what API start-up runs
    assert asked == [] and out["industry_labels"] is None and out["builtin_themes_added"] >= 0
