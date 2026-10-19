"""Fixes reported on 19 Oct 2026: a slow stock page, a Kite login lost minutes after approving it,
and con-call analysis stopping when the previous quarter's transcript is missing."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1 import market

API = "/api/v1"


async def _wait_jobs(demo, kind: str, limit_s: float = 120.0) -> None:
    """Let background jobs of `kind` (started by a session check) finish before the next test."""
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        items = (await demo.client.get(API + "/jobs", params={"kind": kind, "limit": 5})).json()["items"]
        if not any(j["status"] == "running" for j in items):
            return
        await asyncio.sleep(0.2)


# ------------------------------------------------------------ the stock page does not wait for the live quote
async def test_instrument_data_returns_at_once_and_the_quote_is_bounded(demo, monkeypatch):
    md = demo.container.market_data

    async def slow_quote(symbol, instrument_type="EQ"):
        await asyncio.sleep(2)
        return {"symbol": symbol, "status": "ok", "fields": {}, "conflicts": [], "sources": []}

    monkeypatch.setattr(md, "quote", slow_quote)
    t0 = time.perf_counter()
    r = await demo.client.get(API + "/instruments/NSE:DEMOIT01", params={"quote": False})
    body = r.json()
    assert r.status_code == 200 and time.perf_counter() - t0 < 1.5
    assert body["quote"] is None and body["quote_deferred"] is True and body["snapshot"] is not None

    monkeypatch.setattr(market, "QUOTE_DEADLINE_SECONDS", 0.1)
    q = (await demo.client.get(API + "/instruments/NSE:DEMOIT01/quote")).json()["quote"]
    assert q["status"] == "pending" and "still checking in the background" in q["reason"] and q["fields"] == {}
    for task in list(md._pending):  # the fetch keeps running after the deadline; stop it for the next test
        task.cancel()


async def test_quote_endpoint_returns_the_merged_quote_when_sources_answer(demo):
    q = (await demo.client.get(API + "/instruments/NSE:DEMOIT01/quote")).json()["quote"]
    assert q["symbol"] == "DEMOIT01" and q["status"] in ("ok", "unavailable") and "sources" in q


# ------------------------------------------------------------ Kite: an approved session must survive
async def test_rereading_tools_keeps_the_kite_session_and_its_approval(demo, mock_state):
    """The Data status page's "Re-check sources" reconnected every MCP server with a new session. Kite binds the
    approval to the session, so a user who had just logged in was logged out again."""
    mock_state.kite_logged_in = True
    k = demo.container.kite
    assert (await k.auth_state())["state"] == "authorized"
    session = k.client._session_id
    assert (await demo.client.post(API + "/sources/rediscover")).status_code == 200
    assert k.client._session_id == session, "re-reading the tools must not open a new session"
    assert k.health()["auth"]["state"] == "authorized"


async def test_status_page_notices_an_approval_without_a_manual_check(demo, mock_state):
    """After approving in Kite, the Data status page kept showing "login required" because it only displayed the
    last stored state. It now asks Kite again (get_profile only) when that state is older than 30 seconds."""
    k = demo.container.kite
    mock_state.kite_logged_in = False
    assert (await k.auth_state())["state"] == "login_required"
    k._auth["checked_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    mock_state.kite_logged_in = True  # the user approved access in Kite's own page
    status = (await demo.client.get(API + "/sources/status")).json()
    kite = next(c for c in status["connectors"] if c["key"] == "kite_mcp")
    assert kite["auth"]["state"] == "authorized"
    await _wait_jobs(demo, "broker_backfill")


async def test_a_recent_status_check_is_not_repeated(demo, mock_state, monkeypatch):
    k = demo.container.kite
    mock_state.kite_logged_in = False
    await k.auth_state()
    calls = 0
    real = k.auth_state

    async def counted(*a, **kw):
        nonlocal calls
        calls += 1
        return await real(*a, **kw)

    monkeypatch.setattr(k, "auth_state", counted)
    await demo.client.get(API + "/sources/status")
    assert calls == 0, "a check made seconds ago is shown as is"


async def test_a_session_kite_forgot_is_reported_instead_of_a_stale_authorized(demo, mock_state):
    """Kite drops idle MCP sessions (HTTP 400 "Invalid session ID"). The client opens a new one, but the approval
    belonged to the old session, so the status must say a new approval is needed."""
    mock_state.kite_logged_in = True
    k = demo.container.kite
    assert (await k.auth_state())["state"] == "authorized"
    mock_state.kite_forget_session = True
    await k.client.call("get_profile", {})
    auth = k.health()["auth"]
    assert auth["state"] == "login_required" and "ended the previous session" in auth["reason"] and auth["session_lost_at"]


async def test_a_generic_data_error_after_approval_is_not_reported_as_login_required(demo, mock_state, monkeypatch):
    """With a login link pending, every Kite error used to be read as "not logged in", even after approval."""
    from app.connectors.base import ProviderUnavailable
    from app.connectors.mcp_client import McpToolError

    mock_state.kite_logged_in = True
    k = demo.container.kite
    await k.login_link()  # leaves no pending link once authorised; force one as if issued just before approval
    k._pending_login_url = "http://127.0.0.1/authorize?token=old"
    real = k.client.call

    async def call(tool, args=None):
        if tool == "get_quotes":
            raise McpToolError("get_quotes: Failed to get quotes")
        return await real(tool, args)

    monkeypatch.setattr(k.client, "call", call)
    k._auth = {"state": "unknown"}
    with pytest.raises(ProviderUnavailable) as e:
        await k.get_quotes(["DEMOIT01"])
    assert e.value.state == "unavailable", "Kite confirmed the session, so this is a data error, not a login problem"
    assert k.health()["auth"]["state"] == "authorized"
