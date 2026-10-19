"""Contract tests against what the REAL servers advertised and returned on 2026-10-12.

Tool schemas are the recorded `tools/list` results in tests/fixtures/live/. Response bodies below
are trimmed copies of real responses observed that day (public market data, shapes unchanged).
The transport is scripted, so these tests are offline and repeatable; they prove our binding and
parsing match the real contracts, not that the servers are up today.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.kite_mcp import ALLOWED_TOOLS, KiteMcpProvider
from app.connectors.mcp_client import McpClient
from app.connectors.nse_mcp import NseMcpProvider

LIVE = Path(__file__).parents[1] / "fixtures" / "live"


def tools(name):
    return json.loads((LIVE / f"{name}_tools_2026-10-12.json").read_text())["tools"]


def server(name, responder, calls):
    """Scripted MCP server that advertises the recorded real tool list."""
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        m = body.get("method")
        if m == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": name}}})
        if m == "notifications/initialized":
            return httpx.Response(202)
        if m == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools(name)}})
        p = body["params"]
        calls.append((p["name"], p["arguments"]))
        out = responder(p["name"], p["arguments"])
        res = {"isError": True, "content": [{"type": "text", "text": out[1]}]} if isinstance(out, tuple) else {"content": [{"type": "text", "text": json.dumps(out)}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": res})

    return McpClient(name, f"http://{name}.test/mcp", transport=httpx.MockTransport(handler), min_interval=0, max_retries=0,
                     allowed_tools=ALLOWED_TOOLS if name == "kite_mcp" else None)


def bar(d, close, prev):
    return {"symbol": "INFY", "series": "EQ", "ltp": close, "open": close - 2, "high": close + 5, "low": close - 6, "close": close, "prevClose": prev,
            "volume": 14094234, "totalTradedValue": 14404647936.8, "ch52WeekHigh": 0.0, "ch52WeekLow": 0.0, "mktType": "N", "deliveryQty": 0, "date": d}


def nse_responder(name, a):
    if name == "get_stock_history":
        if a["symbol"] == "NOSUCHSYM":
            return {"hint": "Use search_symbols tool to find valid symbols", "error": "Symbol not found: NOSUCHSYM"}
        if a["endDate"] == "today":
            return {"symbol": "INFY", "from_date": "2026-10-08", "to_date": "2026-10-09", "trading_days": 2, "source": "in-memory-cache",
                    "data": [bar("2026-10-08", 997.0, 990.1), bar("2026-10-09", 1023.4, 997.0)], "chunk_months": 3, "next_end_date": "2026-10-07"}
        if a["endDate"] == "2026-10-07":
            return {"symbol": "INFY", "data": [bar("2026-10-06", 985.0, 980.0), bar("2026-10-07", 990.1, 985.0)], "chunk_months": 3, "next_end_date": "2026-10-05"}
        return {"symbol": "INFY", "data": [], "next_end_date": None}
    if name == "get_index_history":
        return {"index": "Nifty 50", "data": [{"indexName": "Nifty 50", "open": 22314.95, "high": 22580.75, "low": 22294.75, "close": 22520.45, "pointsChange": 288.65,
                                               "pctChange": 1.3, "volume": 296205852, "turnoverCr": 26129.26, "pe": 19.27, "pb": 2.77, "divYield": 1.52, "date": "2026-10-09"}],
                "chunk_months": 12, "next_end_date": "2026-10-08"}
    if name == "get_corporate_actions":
        return {"symbol": "INFY", "from": a["fromDate"], "to": a["toDate"], "count": 3, "actions": [
            {"exDate": "2024-05-31", "actionType": "DIVIDEND", "purpose": "Annual General Meeting/Special Dividend - Rs 8 Per Share /Dividend - Rs 20 Per Share", "adjustmentFactor": 1.0},
            {"exDate": "2025-11-14", "actionType": "OTHER", "purpose": "Buy Back", "adjustmentFactor": 1.0},
            {"exDate": "2026-06-10", "actionType": "DIVIDEND", "purpose": "Dividend - Rs 25 Per Share", "adjustmentFactor": 1.0}]}
    if name == "cm_get_stock_quote":
        return {"updatedAt": "2026-10-12T09:14:27.343319113Z", "stock": {"type": "CM", "symbol": "INFY", "series": "EQ", "openPrice": 1029.3, "highPrice": 1033.8,
                "lowPrice": 1010.65, "preClosePrice": 1023.4, "lastTradedPrice": 1012.05, "indicativeClosePrice": 0.0, "change": -11.1, "perChange": -1.08,
                "volume": 7095723, "value": 723.26704539, "fiftyTwoWeekHigh": 1728.0, "fiftyTwoWeekLow": 980.4, "latestTimestamp": "2026-10-12 14:44:27"}}
    if name == "cm_get_index_quote":
        return {"nseTimestamp": "12-Oct-2026 14:44:33", "group": "derivatives", "index": {"indexName": "NIFTY 50", "last": 22316.45, "previousClose": 22520.45,
                "change": -204.0, "percChange": -0.91, "open": 22593.2, "high": 22651.1, "low": 22312.45, "timeVal": "12-Oct-2026 14:44"}}
    if name == "get_market_breadth":
        return {"date": "2026-10-09", "total_stocks": 3687, "advances": 2145, "declines": 1436, "unchanged": 106, "ad_ratio": 1.49}
    if name == "search_symbols":
        return {"results": [{"symbol": "INFY", "last_close": 1023.4, "pct_change": 2.65, "last_date": "2026-10-09"}], "count": 1, "query": a["query"]}
    raise AssertionError(name)


async def nse(calls):
    p = NseMcpProvider(server("nse_bhavcopy", nse_responder, calls), server("nse_cm_market", nse_responder, calls))
    await p.start()
    return p


async def test_real_nse_tool_lists_map_to_capabilities():
    p = await nse([])
    caps = p.capabilities()
    assert len(tools("nse_bhavcopy")) == 21 and len(tools("nse_cm_market")) == 15
    for cap, tool in {"history": "get_stock_history", "corporate_actions": "get_corporate_actions", "index_history": "get_index_history",
                      "quote": "cm_get_stock_quote", "index_quote": "cm_get_index_quote", "market_breadth": "get_market_breadth"}.items():
        assert caps[cap] == {"supported": True, "tool": tool, "reason": None}
    assert caps["fundamentals"]["supported"] is False and caps["intraday_history"]["supported"] is False


async def test_real_history_contract_pages_with_months_and_next_end_date():
    calls = []
    p = await nse(calls)
    res = await p.get_history("infy", date(2026, 10, 6), date(2099, 1, 1))
    assert [c[1] for c in calls] == [{"symbol": "INFY", "months": 3, "endDate": "today"}, {"symbol": "INFY", "months": 3, "endDate": "2026-10-07"}]
    assert [d.date().isoformat() for d in res.frame.index] == ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    last = res.frame.iloc[-1]
    assert (last.close, last.prev_close, last.volume, last.traded_value) == (1023.4, 997.0, 14094234.0, 14404647936.8)
    assert res.adjustment == "unadjusted" and res.source_id == "get_stock_history"


async def test_real_error_payload_is_an_error_not_data():
    p = await nse([])
    with pytest.raises(ProviderUnavailable) as e:
        await p.get_history("NOSUCHSYM", date(2026, 1, 1), date(2026, 10, 9))
    assert "Symbol not found: NOSUCHSYM" in str(e.value)


async def test_real_quote_contract_is_nested_under_stock_and_timestamped_in_ist():
    p = await nse([])
    q = (await p.get_quote("INFY")).fields
    assert {k: q[k].value for k in ("last_price", "open", "high", "low", "prev_close", "volume", "change_pct")} == {
        "last_price": 1012.05, "open": 1029.3, "high": 1033.8, "low": 1010.65, "prev_close": 1023.4, "volume": 7095723.0, "change_pct": -1.08}
    assert q["last_price"].as_of.isoformat() == "2026-10-12T14:44:27+05:30" and q["last_price"].status == "delayed"
    assert "traded_value" not in q, "the live 'value' field is in crore, so it is not mixed with rupee traded values"


async def test_real_index_quote_and_history_contracts():
    calls = []
    p = await nse(calls)
    q = (await p.get_quote("NIFTY 50", "INDEX")).fields
    assert q["last_price"].value == 22316.45 and q["prev_close"].value == 22520.45 and q["last_price"].source_id == "cm_get_index_quote"
    h = await p.get_history("NIFTY 50", date(2026, 10, 9), date(2026, 10, 9), "INDEX")
    assert h.frame["close"].tolist() == [22520.45] and h.source_id == "get_index_history"
    assert ("get_index_history", {"indexName": "NIFTY 50", "months": 12, "endDate": "2026-10-09"}) in calls
    assert ("cm_get_index_quote", {"indexName": "NIFTY 50"}) in calls


async def test_real_corporate_actions_contract_sends_date_range_and_parses_purposes():
    calls = []
    p = await nse(calls)
    acts = await p.get_corporate_actions("INFY")
    args = calls[-1][1]
    assert set(args) == {"symbol", "fromDate", "toDate"} and args["fromDate"] < args["toDate"] and len(args["toDate"]) == 10
    assert [(a.ex_date.isoformat(), a.kind, a.ratio) for a in acts] == [("2024-05-31", "dividend", 8.0), ("2025-11-14", "other", None), ("2026-06-10", "dividend", 25.0)]


async def test_real_breadth_and_search_contracts():
    calls = []
    p = await nse(calls)
    b = await p.market_breadth()
    assert b["data"]["advances"] == 2145 and len(calls[-1][1]["date"]) == 10
    assert (await p.search("INFY"))[0]["symbol"] == "INFY"


def kite_responder(logged_in):
    def r(name, a):
        if name == "search_instruments":
            assert a == {"query": "NSE:NIFTY 50", "filter_on": "id", "limit": 20} or a["query"] == "NSE:INFY"
            rows = [{"id": "NSE:NIFTY 500", "instrument_token": 268041, "tradingsymbol": "NIFTY 500", "exchange": "NSE", "isin": "", "segment": "INDICES"},
                    {"id": "NSE:NIFTY 50", "instrument_token": 256265, "tradingsymbol": "NIFTY 50", "exchange": "NSE", "isin": "", "segment": "INDICES"},
                    {"id": "NSE:INFY", "instrument_token": 408065, "tradingsymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021", "segment": "NSE", "lot_size": 1}]
            return {"data": [x for x in rows if a["query"] in x["id"]], "pagination": {"from": 0, "limit": 20, "total": 1, "has_more": False}}
        if not logged_in:
            if name == "get_historical_data":
                return ("error", "Failed to get historical data")
            if name == "login":
                return "Please login: https://mcp.kite.trade/authorize?session_id=abc"  # shape of the real reply
            return ("error", "Please log in first using the login tool")
        if name == "get_profile":
            return {"user_id": "AB1234"}
        raise AssertionError(name)
    return r


async def test_real_kite_lookup_is_exact_by_id_and_returns_isin():
    calls = []
    k = KiteMcpProvider(server("kite_mcp", kite_responder(False), calls))
    await k.start()
    assert (await k.resolve("NIFTY 50"))["instrument_token"] == 256265, "the exact id wins over the NIFTY 500 substring match"
    inst = await k.resolve("infy")
    assert inst["instrument_token"] == 408065 and inst["isin"] == "INE009A01021"
    assert len(k.client.tools) == 7 and len(k.client.advertised) == 22


async def test_real_kite_generic_history_failure_before_login_is_reported_as_login_required():
    calls = []
    k = KiteMcpProvider(server("kite_mcp", kite_responder(False), calls))
    await k.start()
    with pytest.raises(ProviderUnavailable) as e:
        await k.get_history("INFY", date(2026, 10, 1), date(2026, 10, 9))
    assert e.value.state == "login_required"
    sent = next(a for n, a in calls if n == "get_historical_data")
    assert sent == {"instrument_token": 408065, "from_date": "2026-10-01 00:00:00", "to_date": "2026-10-09 23:59:59", "interval": "day"}
    assert (await k.login_link())["login_url"] == "https://mcp.kite.trade/authorize?session_id=abc"
    assert not [n for n, _ in calls if n not in ALLOWED_TOOLS]


async def test_login_stays_required_while_a_login_link_is_pending_even_if_errors_turn_generic():
    """Observed live: once `login` has been called, get_profile and data tools fail with generic
    'Failed to get ...' messages until the user approves. The state must stay login_required."""
    state = {"login_called": False}

    def r(name, a):
        if name == "search_instruments":
            return {"data": [{"id": "NSE:INFY", "instrument_token": 408065, "tradingsymbol": "INFY", "exchange": "NSE"}]}
        if name == "login":
            state["login_called"] = True
            return "Please login: https://mcp.kite.trade/authorize?session_id=abc"
        if state["login_called"]:
            return ("error", f"Failed to get {'profile' if name == 'get_profile' else 'historical data'}")
        return ("error", "Please log in first using the login tool")

    k = KiteMcpProvider(server("kite_mcp", r, []))
    await k.start()
    assert (await k.login_link())["state"] == "login_required"  # the user asks for the login link
    assert state["login_called"]
    for _ in range(3):
        with pytest.raises(ProviderUnavailable) as e:
            await k.get_history("INFY", date(2026, 10, 1), date(2026, 10, 9))
        assert e.value.state == "login_required"
    st = await k.auth_state()
    assert st["state"] == "login_required" and st["login_url"].endswith("session_id=abc")


async def test_generic_profile_failure_before_login_still_offers_the_login_link():
    """Observed live on 2026-10-15: before login, get_profile fails with a generic
    'Failed to execute get_profile' (no mention of login). That is still a login problem."""
    def r(name, a):
        if name == "login":
            return "Please login: https://mcp.kite.trade/authorize?session_id=xyz"
        if name == "search_instruments":
            return {"data": [{"id": "NSE:INFY", "instrument_token": 408065, "tradingsymbol": "INFY", "exchange": "NSE"}]}
        return ("error", f"Failed to execute {name}")

    k = KiteMcpProvider(server("kite_mcp", r, []))
    await k.start()
    st = await k.login_link()
    assert st["state"] == "login_required" and st["login_url"].endswith("session_id=xyz")
    with pytest.raises(ProviderUnavailable) as e:
        await k.get_history("INFY", date(2026, 10, 1), date(2026, 10, 9))
    assert e.value.state == "login_required"


async def test_history_keeps_newer_pages_when_older_pages_are_beyond_the_server_window():
    """Real NSE behaviour: pages older than its retention answer 'Symbol not found' / 'No data for index'."""
    def responder(name, a):
        if name == "get_stock_history" and a["endDate"] == "2026-10-05":
            return {"hint": "Use search_symbols tool to find valid symbols", "error": "Symbol not found: INFY"}
        if name == "get_index_history" and a.get("endDate") not in (None, "today"):
            return {"error": "No data for index 'NIFTY 50' between 2025-10-08 and 2026-10-08"}
        return nse_responder(name, a)

    p = NseMcpProvider(server("nse_bhavcopy", responder, []), server("nse_cm_market", responder, []))
    await p.start()
    res = await p.get_history("INFY", date(2023, 1, 1), date(2099, 1, 1))
    assert [d.date().isoformat() for d in res.frame.index] == ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert res.warnings and "history starts at 2026-10-06" in res.warnings[0] and "Symbol not found" in res.warnings[0]
    idx = await p.get_history("NIFTY 50", date(2023, 1, 1), date(2099, 1, 1), "INDEX")
    assert len(idx.frame) == 1 and "No data for index" in idx.warnings[0]


async def test_history_first_page_error_is_still_a_failure():
    p = await nse([])
    with pytest.raises(ProviderUnavailable, match="Symbol not found: NOSUCHSYM"):
        await p.get_history("NOSUCHSYM", date(2023, 1, 1), date(2026, 10, 9))
