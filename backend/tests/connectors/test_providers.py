"""Contract tests of the NSE and Kite providers against the DEMO mock MCP servers."""

from datetime import date, timedelta

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.kite_mcp import ALLOWED_TOOLS, KiteMcpProvider
from app.connectors.mcp_client import McpClient
from app.connectors.nse_mcp import NseMcpProvider
from app.devtools import demo_data as dd


def nse(mock_mcp, bhav=True, cm=True):
    b = McpClient("nse_bhavcopy", mock_mcp["bhavcopy"], min_interval=0) if bhav else None
    c = McpClient("nse_cm_market", mock_mcp["cm"], min_interval=0) if cm else None
    return NseMcpProvider(b, c, is_demo=True)


def kite(mock_mcp):
    return KiteMcpProvider(McpClient("kite_mcp", mock_mcp["kite"], min_interval=0, allowed_tools=ALLOWED_TOOLS), is_demo=True)


# ------------------------------------------------------------ NSE
async def test_nse_history_pages_backwards_and_returns_ascending_range(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    end = dd.bars("DEMOIT01").index[-1].date()
    start = end - timedelta(days=300)
    res = await p.get_history("DEMOIT01", start, end)
    df = res.frame
    expected = dd.bars("DEMOIT01").loc[str(start):str(end)]
    assert len(df) == len(expected) > 150
    assert df.index.is_monotonic_increasing and df.index[0].date() >= start
    assert df["close"].iloc[-1] == pytest.approx(expected["close"].iloc[-1])
    assert mock_state.calls.count("get_stock_history") >= 3, "more than one page was needed"
    assert res.provider == "nse_mcp" and res.source_id == "get_stock_history"
    assert res.adjustment == "unadjusted" and res.is_demo and res.retrieved_at.tzinfo is not None
    assert {"open", "high", "low", "close", "volume", "prev_close", "traded_value"} <= set(df.columns)
    await p.close()


async def test_nse_tool_error_surfaces_as_provider_unavailable(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    mock_state.fail_next["get_stock_history"] = 1
    end = dd.bars("DEMOIT01").index[-1].date()
    with pytest.raises(ProviderUnavailable):
        await p.get_history("DEMOIT01", end - timedelta(days=20), end)
    await p.close()


async def test_nse_unknown_symbol_is_a_clean_error(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    with pytest.raises(ProviderUnavailable) as e:
        await p.get_history("NOSUCH", date(2026, 1, 1), date(2026, 2, 1))
    assert "Symbol not found" in str(e.value)
    await p.close()


async def test_nse_quote_fields_carry_provenance(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    q = await p.get_quote("DEMOIT01")
    assert {"last_price", "open", "high", "low", "prev_close", "volume"} <= set(q.fields)
    lp = q.fields["last_price"]
    assert lp.provider == "nse_mcp" and lp.source_id == "cm_get_stock_quote" and lp.currency == "INR"
    assert lp.status == "delayed" and lp.as_of is not None and lp.is_demo
    assert lp.value == pytest.approx(dd.bars("DEMOIT01", "broker")["close"].iloc[-1])
    await p.close()


async def test_nse_corporate_actions_are_parsed(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    acts = await p.get_corporate_actions(dd.SPLIT_SYMBOL)
    assert len(acts) == 1 and acts[0].kind == "split" and acts[0].ratio == pytest.approx(2.0)
    assert acts[0].ex_date == dd.split_date()
    assert await p.get_corporate_actions("DEMOIT01") == []
    await p.close()


async def test_nse_capabilities_reflect_what_each_server_advertises(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    caps = p.capabilities()
    assert caps["history"]["supported"] and caps["history"]["tool"] == "get_stock_history"
    assert caps["quote"]["supported"] and caps["corporate_actions"]["supported"]
    assert caps["intraday_history"]["supported"] is False and caps["fundamentals"]["supported"] is False
    assert caps["index_history"]["supported"] is False
    await p.close()
    only_bhav = nse(mock_mcp, cm=False)
    await only_bhav.start()
    assert only_bhav.capabilities()["quote"]["supported"] is False
    assert "not configured" in only_bhav.capabilities()["quote"]["reason"]
    with pytest.raises(ProviderUnavailable):
        await only_bhav.get_quote("DEMOIT01")
    await only_bhav.close()


async def test_nse_down_reports_degraded_state_instead_of_raising_on_start():
    def down(req):
        return httpx.Response(504, text="<html>504 Gateway Time-out</html>")

    t = httpx.MockTransport(down)
    p = NseMcpProvider(McpClient("nse_bhavcopy", "http://x.test/mcp", transport=t, max_retries=0, min_interval=0),
                       McpClient("nse_cm_market", "http://y.test/mcp", transport=t, max_retries=0, min_interval=0))
    await p.start()
    caps = p.capabilities()
    assert caps["history"]["supported"] is False and "504" in caps["history"]["reason"]
    h = p.health()
    assert h["nse_bhavcopy"]["state"] == "unavailable"
    with pytest.raises(ProviderUnavailable):
        await p.get_history("INFY", date(2026, 1, 1), date(2026, 2, 1))


async def test_nse_search(mock_mcp, mock_state):
    p = nse(mock_mcp)
    await p.start()
    hits = await p.search("DEMOIT")
    assert {"DEMOIT01", "DEMOIT02"} <= {h["symbol"] for h in hits}
    await p.close()


# ------------------------------------------------------------ Kite
async def test_kite_requires_login_then_authorises(mock_mcp, mock_state):
    k = kite(mock_mcp)
    await k.start()
    st = await k.auth_state()
    assert st["state"] == "login_required" and not st["login_url"], "a status check never issues a login link"
    assert (await k.login_link())["login_url"].startswith("http"), "the link is issued when the user asks for it"
    with pytest.raises(ProviderUnavailable) as e:
        await k.get_quotes(["DEMOIT01"])
    assert "login" in str(e.value).lower()
    async with httpx.AsyncClient() as h:
        assert (await h.get(mock_mcp["kite_authorize"])).status_code == 200
    assert (await k.auth_state())["state"] == "authorized"
    await k.close()


async def test_kite_history_and_quotes(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    k = kite(mock_mcp)
    await k.start()
    end = dd.bars("DEMOIT01").index[-1].date()
    res = await k.get_history("DEMOIT01", end - timedelta(days=120), end)
    exp = dd.bars("DEMOIT01", "broker").loc[str(end - timedelta(days=120)):]
    assert len(res.frame) == len(exp)
    assert res.frame["close"].iloc[-1] == pytest.approx(exp["close"].iloc[-1])
    assert res.provider == "kite_mcp" and res.adjustment == "split_adjusted" and res.source_id == "get_historical_data"
    qs = await k.get_quotes(["DEMOIT01", "DEMOFIN01", "NOSUCH"])
    assert set(qs) == {"DEMOIT01", "DEMOFIN01"}, "instruments the broker does not know are simply absent"
    assert qs["DEMOIT01"].fields["last_price"].provider == "kite_mcp"
    assert qs["DEMOIT01"].fields["prev_close"].value == pytest.approx(exp["prev_close"].iloc[-1])
    await k.close()


async def test_kite_index_history_is_available_for_benchmark(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    k = kite(mock_mcp)
    await k.start()
    end = dd.bars(dd.BENCHMARK).index[-1].date()
    res = await k.get_history(dd.BENCHMARK, end - timedelta(days=60), end)
    assert len(res.frame) > 30
    assert k.capabilities()["index_history"]["supported"] is True
    await k.close()


async def test_kite_provider_never_calls_forbidden_tools(mock_mcp, mock_state):
    mock_state.kite_logged_in = True
    k = kite(mock_mcp)
    await k.start()
    end = dd.bars("DEMOIT01").index[-1].date()
    await k.get_history("DEMOIT01", end - timedelta(days=10), end)
    await k.get_quotes(["DEMOIT01"])
    await k.auth_state()
    assert mock_state.forbidden_calls == []
    assert k.capabilities()["order_placement"] == {"supported": False, "tool": None, "reason": "blocked by policy: this product never places orders"}
    await k.close()
