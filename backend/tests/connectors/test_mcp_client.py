import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.connectors import mcp_client as mc
from app.connectors.binding import BindError, bind_args
from app.connectors.kite_mcp import ALLOWED_TOOLS
from app.core.resilience import FakeClock

FIXTURE = Path(__file__).parents[1] / "fixtures" / "live" / "kite_mcp_tools_2026-10-12.json"


def client(url, **kw):
    kw.setdefault("min_interval", 0)
    return mc.McpClient("test", url, **kw)


async def test_discovers_advertised_tools_and_schemas(mock_mcp):
    c = client(mock_mcp["bhavcopy"])
    tools = await c.connect()
    assert "get_stock_history" in tools and "get_corporate_actions" in tools
    assert tools["get_stock_history"].input_schema["required"] == ["symbol"]
    assert c.health()["state"] == "ok" and c.health()["tool_count"] == len(tools)
    await c.close()


async def test_call_returns_parsed_payload(mock_mcp):
    c = client(mock_mcp["bhavcopy"])
    out = await c.call("get_52_week_high_low", {"symbol": "DEMOIT01"})
    assert out["symbol"] == "DEMOIT01" and out["52w_high"] >= out["52w_low"]
    assert c.health()["last_success_at"] is not None
    await c.close()


async def test_tool_not_advertised_is_not_supported_and_never_sent(mock_mcp, mock_state):
    c = client(mock_mcp["bhavcopy"])
    with pytest.raises(mc.McpNotSupported):
        await c.call("get_intraday_candles", {"symbol": "DEMOIT01"})
    await c.close()


async def test_tool_error_is_raised_with_message_and_not_retried(mock_mcp, mock_state):
    c = client(mock_mcp["bhavcopy"], max_retries=3)
    with pytest.raises(mc.McpToolError) as e:
        await c.call("get_stock_history", {"symbol": "NOSUCH"})
    assert "Symbol not found" in str(e.value)
    assert mock_state.calls.count("get_stock_history") == 1
    await c.close()


async def test_missing_required_argument_rejected_before_sending(mock_mcp, mock_state):
    c = client(mock_mcp["bhavcopy"])
    with pytest.raises(mc.McpInvalidArguments):
        await c.call("get_stock_history", {})
    with pytest.raises(mc.McpInvalidArguments):
        await c.call("get_stock_history", {"symbol": "DEMOIT01", "made_up": 1})
    assert mock_state.calls == []
    await c.close()


async def test_allowlist_blocks_order_tools_without_reaching_server(mock_mcp, mock_state):
    c = client(mock_mcp["kite"], allowed_tools=ALLOWED_TOOLS)
    await c.connect()
    assert "place_order" in c.advertised and "place_order" not in c.tools
    for tool in ("place_order", "modify_order", "cancel_order", "get_holdings"):
        with pytest.raises(mc.McpForbiddenTool):
            await c.call(tool, {})
    assert mock_state.forbidden_calls == []
    await c.close()


def test_allowlist_matches_recorded_real_kite_tool_list():
    tools = {t["name"]: t for t in json.loads(FIXTURE.read_text())["tools"]}
    assert len(tools) == 22
    assert ALLOWED_TOOLS <= set(tools), "every allowed tool is advertised by the real server"
    blocked = set(tools) - ALLOWED_TOOLS
    for name in ("place_order", "modify_order", "cancel_order", "place_gtt_order", "modify_gtt_order", "delete_gtt_order",
                 "get_holdings", "get_positions", "get_orders", "get_trades", "get_margins", "get_mf_holdings"):
        assert name in blocked
    assert not [n for n in ALLOWED_TOOLS if any(w in n for w in ("order", "gtt", "holding", "position", "margin", "trade"))]


def test_arguments_bind_against_recorded_real_kite_schema():
    tools = {t["name"]: t for t in json.loads(FIXTURE.read_text())["tools"]}
    args = bind_args(tools["get_historical_data"]["inputSchema"],
                     {"instrument_token": 408065, "start_date": date(2026, 1, 1), "end_date": date(2026, 3, 31), "interval": "day"})
    assert args == {"instrument_token": 408065, "from_date": "2026-01-01 00:00:00", "to_date": "2026-03-31 23:59:59", "interval": "day"}


def test_binding_adapts_to_different_argument_names_and_date_formats():
    schema = {"type": "object", "required": ["ticker", "toDate"], "additionalProperties": False, "properties": {
        "ticker": {"type": "string"}, "toDate": {"type": "string", "description": "End date in DD-MM-YYYY format"},
        "fromDate": {"type": "string", "description": "DD-MM-YYYY"}}}
    args = bind_args(schema, {"symbol": "INFY", "start_date": date(2026, 1, 5), "end_date": date(2026, 2, 6)})
    assert args == {"ticker": "INFY", "toDate": "06-02-2026", "fromDate": "05-01-2026"}


def test_binding_fails_clearly_when_a_required_argument_is_unknown():
    schema = {"type": "object", "required": ["symbol", "segmentCode"], "properties": {"symbol": {"type": "string"}, "segmentCode": {"type": "string"}}}
    with pytest.raises(BindError) as e:
        bind_args(schema, {"symbol": "INFY"})
    assert "segmentCode" in str(e.value)


def test_binding_omits_optional_arguments_it_was_not_given():
    schema = {"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}, "endDate": {"type": "string"}}}
    assert bind_args(schema, {"symbol": "INFY", "end_date": None}) == {"symbol": "INFY"}


# ---- transport behaviour with a scripted HTTP transport ----

def scripted(handler):
    return httpx.MockTransport(handler)


def _ok(result, rid, headers=None):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid, "result": result}, headers=headers or {})


TOOLS = {"tools": [{"name": "echo", "description": "", "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}}}]}


def make_handler(call_responses, seen):
    it = iter(call_responses)

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((body.get("method"), dict(req.headers)))
        m = body.get("method")
        if m == "initialize":
            return _ok({"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "s", "version": "1"}},
                       body["id"], {"mcp-session-id": "sess-1"})
        if m == "notifications/initialized":
            return httpx.Response(202)
        if m == "tools/list":
            return _ok(TOOLS, body["id"])
        return next(it)(body)

    return handler


async def test_retries_gateway_timeout_then_succeeds_and_sends_session_id():
    seen = []
    responses = [lambda b: httpx.Response(504, text="<html>504 Gateway Time-out</html>"),
                 lambda b: _ok({"content": [{"type": "text", "text": '{"v": 1}'}]}, b["id"])]
    clock = FakeClock()
    c = client("http://mcp.test/mcp", transport=scripted(make_handler(responses, seen)), max_retries=2, clock=clock)
    assert await c.call("echo", {"x": 1}) == {"v": 1}
    calls = [h for m, h in seen if m == "tools/call"]
    assert len(calls) == 2 and all(h.get("mcp-session-id") == "sess-1" for h in calls)
    assert clock.slept, "backed off between attempts"
    assert c.health()["retries"] == 1


async def test_parses_sse_framed_response():
    seen = []
    sse = lambda b: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(  # noqa: E731
        'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
        f'event: message\ndata: {{"jsonrpc":"2.0","id":{b["id"]},"result":{{"content":[{{"type":"text","text":"[1,2]"}}]}}}}\n\n'))
    c = client("http://mcp.test/mcp", transport=scripted(make_handler([sse], seen)))
    assert await c.call("echo", {}) == [1, 2]


async def test_expired_session_is_reinitialised_once():
    seen = []
    responses = [lambda b: httpx.Response(404, json={"jsonrpc": "2.0", "error": {"code": -32001, "message": "Session not found"}}),
                 lambda b: _ok({"content": [{"type": "text", "text": "7"}]}, b["id"])]
    c = client("http://mcp.test/mcp", transport=scripted(make_handler(responses, seen)), max_retries=0)
    assert await c.call("echo", {}) == 7
    assert [m for m, _ in seen].count("initialize") == 2


async def test_kite_plain_text_invalid_session_is_reinitialised_once():
    # Recorded from mcp.kite.trade on 19 Oct 2026: once the server forgets an idle session it answers
    # HTTP 400, text/plain "Invalid session ID" (not 404, not JSON-RPC). The client must reconnect.
    seen = []
    responses = [lambda b: httpx.Response(400, headers={"content-type": "text/plain; charset=utf-8"}, text="Invalid session ID\n"),
                 lambda b: _ok({"content": [{"type": "text", "text": "7"}]}, b["id"])]
    c = client("http://mcp.test/mcp", transport=scripted(make_handler(responses, seen)), max_retries=0)
    assert await c.call("echo", {}) == 7
    assert [m for m, _ in seen].count("initialize") == 2


async def test_unreachable_server_raises_unavailable_and_opens_breaker():
    def down(req):
        raise httpx.ConnectError("refused")

    clock = FakeClock()
    c = client("http://mcp.test/mcp", transport=scripted(down), max_retries=0, breaker_threshold=2, breaker_reset=60, clock=clock)
    for _ in range(2):
        with pytest.raises(mc.McpUnavailable):
            await c.connect()
    with pytest.raises(mc.McpUnavailable) as e:
        await c.connect()
    assert "circuit open" in str(e.value)
    h = c.health()
    assert h["state"] == "unavailable" and h["breaker"] == "open" and h["last_error"]
    clock.advance(61)
    assert c.health()["breaker"] == "half_open"


async def test_error_payloads_are_never_returned_as_values():
    seen = []
    responses = [lambda b: _ok({"isError": True, "content": [{"type": "text", "text": "rate limited"}]}, b["id"])]
    c = client("http://mcp.test/mcp", transport=scripted(make_handler(responses, seen)))
    with pytest.raises(mc.McpToolError):
        await c.call("echo", {})


async def test_authorization_header_never_appears_in_health_or_repr():
    c = client("http://mcp.test/mcp", headers={"Authorization": "Bearer SECRET123"}, transport=scripted(lambda r: httpx.Response(500)))
    assert "SECRET123" not in repr(c) and "SECRET123" not in json.dumps(c.health())
