"""NSE + broker correlation, exercised end to end over MCP against the DEMO mock servers."""

from datetime import timedelta

import httpx
import pytest

from app.connectors.kite_mcp import ALLOWED_TOOLS, KiteMcpProvider
from app.connectors.mcp_client import McpClient
from app.connectors.nse_mcp import NseMcpProvider
from app.core.cache import MemoryCache
from app.devtools import demo_data as dd
from app.services.market_data import CorrelatedMarketData, DataUnavailable


async def build(mock_mcp, nse_up=True, kite_up=True):
    dead = httpx.MockTransport(lambda r: httpx.Response(504))
    kw = {"min_interval": 0, "max_retries": 0}
    nse = NseMcpProvider(McpClient("nse_bhavcopy", mock_mcp["bhavcopy"], transport=None if nse_up else dead, **kw),
                         McpClient("nse_cm_market", mock_mcp["cm"], transport=None if nse_up else dead, **kw), is_demo=True)
    kite = KiteMcpProvider(McpClient("kite_mcp", mock_mcp["kite"], allowed_tools=ALLOWED_TOOLS, transport=None if kite_up else dead, **kw), is_demo=True)
    await nse.start()
    await kite.start()
    return CorrelatedMarketData(nse, kite, MemoryCache())


def rng(symbol, days=320):
    end = dd.bars(symbol, "broker").index[-1].date()
    return end - timedelta(days=days), end


def by(sources, provider, dataset="history"):
    return next(s for s in sources if s.provider == provider and s.dataset == dataset)


async def test_agreeing_sources_use_nse_and_report_no_conflict(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    b = await md.history("DEMOIT01", *rng("DEMOIT01"))
    assert set(b.frame["provider"]) == {"nse_mcp"}
    assert b.conflicts == [] and b.coverage["overlap"] == len(b.frame) and b.coverage["kite_mcp"] == 0
    assert by(b.sources, "nse_mcp").status == "ok" and by(b.sources, "kite_mcp").status == "ok"
    assert b.is_demo


async def test_unadjusted_nse_series_is_split_adjusted_before_comparison(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    b = await md.history(dd.SPLIT_SYMBOL, *rng(dd.SPLIT_SYMBOL, 420))
    assert len(b.actions) == 1 and b.actions[0].kind == "split"
    assert b.conflicts == [], "after adjustment the NSE series agrees with the broker's adjusted series"
    assert by(b.sources, "nse_mcp").adjustment == "split_adjusted"
    # no artificial -50% day survives in the stored series
    assert b.frame["close"].pct_change().min() > -0.25
    assert b.warnings == []


async def test_broker_fills_only_the_bars_nse_lacks(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    b = await md.history(dd.GAP_SYMBOL, *rng(dd.GAP_SYMBOL))
    assert b.frame["provider"].iloc[-dd.GAP_BARS:].tolist() == ["kite_mcp"] * dd.GAP_BARS
    assert set(b.frame["provider"].iloc[:-dd.GAP_BARS]) == {"nse_mcp"}
    assert b.coverage["kite_mcp"] == dd.GAP_BARS


async def test_disagreement_is_flagged_and_nse_value_kept(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    b = await md.history(dd.CONFLICT_SYMBOL, *rng(dd.CONFLICT_SYMBOL))
    closes = [c for c in b.conflicts if c["field"] == "close"]
    assert len(closes) == 2
    for c in closes:
        assert c["chosen"] == "nse_mcp" and c["diff_pct"] == pytest.approx(2.0, abs=0.01)
        assert float(b.frame.loc[c["date"], "close"]) == pytest.approx(c["values"]["nse_mcp"])
    assert set(b.frame["provider"]) == {"nse_mcp"}


async def test_broker_login_missing_degrades_to_nse_only_with_reason(mock_mcp, mock_state):
    md = await build(mock_mcp)
    b = await md.history("DEMOIT01", *rng("DEMOIT01"))
    k = by(b.sources, "kite_mcp")
    assert k.status == "login_required" and "login" in k.reason.lower()
    assert len(b.frame) > 100 and set(b.frame["provider"]) == {"nse_mcp"}


async def test_nse_down_broker_supplies_history_and_says_so(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp, nse_up=False)
    b = await md.history("DEMOIT01", *rng("DEMOIT01"))
    assert set(b.frame["provider"]) == {"kite_mcp"}
    assert by(b.sources, "nse_mcp").status == "unavailable" and "504" in by(b.sources, "nse_mcp").reason
    assert any("Corporate actions unavailable" in w for w in b.warnings)


async def test_all_sources_down_raises_with_per_source_reasons(mock_mcp, mock_state):
    md = await build(mock_mcp, nse_up=False, kite_up=False)
    with pytest.raises(DataUnavailable) as e:
        await md.history("DEMOIT01", *rng("DEMOIT01"))
    assert {s.provider for s in e.value.sources if s.dataset == "history"} == {"nse_mcp", "kite_mcp"}
    assert "nse_mcp" in str(e.value) and "kite_mcp" in str(e.value)


async def test_index_history_comes_from_broker_because_nse_does_not_provide_it(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    b = await md.history(dd.BENCHMARK, *rng(dd.BENCHMARK), instrument_type="INDEX")
    assert by(b.sources, "nse_mcp").status == "not_supported"
    assert set(b.frame["provider"]) == {"kite_mcp"}


async def test_quote_is_reconciled_per_field_with_provenance_and_cached(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    md = await build(mock_mcp)
    q = await md.quote("DEMOIT01")
    lp = q["fields"]["last_price"]
    assert q["status"] == "ok" and lp["provider"] == "nse_mcp" and lp["status"] == "delayed" and lp["conflict"] is None
    assert lp["source_id"] == "cm_get_stock_quote" and lp["as_of"] and lp["currency"] == "INR"
    # a field only the broker has is filled from the broker
    assert q["fields"]["upper_circuit"]["provider"] == "kite_mcp"
    n = mock_state.calls.count("cm_get_stock_quote")
    assert (await md.quote("DEMOIT01"))["cache"] == "hit"
    assert mock_state.calls.count("cm_get_stock_quote") == n


async def test_failed_quote_is_not_cached(mock_mcp, mock_state):
    md = await build(mock_mcp, nse_up=False, kite_up=False)
    q = await md.quote("DEMOIT01")
    assert q["status"] == "unavailable" and q["fields"] == {}
    assert {s["status"] for s in q["sources"]} == {"unavailable"}
    assert (await md.quote("DEMOIT01"))["cache"] == "miss"


async def test_index_history_with_nse_unreachable_is_unavailable_not_unsupported(mock_mcp, mock_state):
    """NSE down must read as 'unavailable' (try again later), not 'not supported' (never possible)."""
    from app.connectors.nse_mcp import NseMcpProvider

    dead = httpx.MockTransport(lambda r: httpx.Response(504))
    nse = NseMcpProvider(McpClient("nse_bhavcopy", "http://x.test/mcp", transport=dead, min_interval=0, max_retries=0),
                         McpClient("nse_cm_market", "http://y.test/mcp", transport=dead, min_interval=0, max_retries=0))
    await nse.start()
    mock_state.kite_logged_in = True
    kite = KiteMcpProvider(McpClient("kite_mcp", mock_mcp["kite"], allowed_tools=ALLOWED_TOOLS, min_interval=0), is_demo=True)
    await kite.start()
    b = await CorrelatedMarketData(nse, kite, MemoryCache()).history(dd.BENCHMARK, *rng(dd.BENCHMARK), instrument_type="INDEX")
    st = by(b.sources, "nse_mcp")
    assert st.status == "unavailable" and "504" in st.reason
    assert set(b.frame["provider"]) == {"kite_mcp"}
