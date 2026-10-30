"""Reported 19 Oct 2026: the Kite login was lost every time the API restarted, with no visible way to log in again.
Kite binds the approval to the MCP session, so the API keeps that session across restarts, re-confirms it while
authorised, and offers a deliberate disconnect."""

from __future__ import annotations

import asyncio
import json
import stat

from app.connectors.kite_mcp import ALLOWED_TOOLS, KiteMcpProvider
from app.connectors.mcp_client import McpClient

API = "/api/v1"


def _kite(mock_mcp, store, keepalive=0.0):
    c = McpClient("kite_mcp", mock_mcp["kite"], min_interval=0, allowed_tools=ALLOWED_TOOLS)
    c.session_store = store
    return KiteMcpProvider(c, keepalive_seconds=keepalive)


async def test_an_approved_session_survives_an_api_restart(mock_mcp, mock_state, tmp_path):
    store = tmp_path / "kite_mcp_session.json"
    mock_state.kite_logged_in = True
    first = _kite(mock_mcp, store)
    await first.start()
    assert (await first.auth_state())["state"] == "authorized"
    session = first.client._session_id
    await first.close()  # the API stops

    saved = json.loads(store.read_text())
    assert saved["session_id"] == session and saved["url"].endswith("/mcp") and set(saved) <= {"url", "session_id", "protocol", "server_info", "saved_at"}
    assert stat.S_IMODE(store.stat().st_mode) == 0o600, "the session handle is readable by its owner only"

    second = _kite(mock_mcp, store)  # the API starts again
    await second.start()
    assert second.client._session_id == session, "the saved session is reused instead of opening a new one"
    assert second.client.restored_session
    assert second.health()["auth"]["state"] == "authorized", "the approval is confirmed at start, no login asked"
    assert second.health()["session"]["restored_from_previous_run"] is True
    await second.close()


async def test_a_saved_session_that_kite_has_ended_is_replaced_and_reported(mock_mcp, mock_state, tmp_path):
    store = tmp_path / "kite_mcp_session.json"
    first = _kite(mock_mcp, store)
    await first.start()
    await first.close()
    old = json.loads(store.read_text())["session_id"]
    mock_state.kite_forget_session = True  # Kite no longer knows the saved session
    second = _kite(mock_mcp, store)
    await second.start()
    assert second.client._session_id != old and not second.client.restored_session
    assert json.loads(store.read_text())["session_id"] == second.client._session_id, "the new session is saved"
    await second.close()


async def test_keepalive_notices_when_kite_ends_an_authorised_session(mock_mcp, mock_state, tmp_path):
    mock_state.kite_logged_in = True
    k = _kite(mock_mcp, tmp_path / "s.json", keepalive=0.05)
    await k.start()
    assert (await k.auth_state())["state"] == "authorized"
    mock_state.kite_logged_in = False  # Kite's daily expiry, or a restart on its side
    for _ in range(100):
        if k.health()["auth"]["state"] == "login_required":
            break
        await asyncio.sleep(0.02)
    assert k.health()["auth"]["state"] == "login_required"
    await k.close()
    assert k._keepalive_task is not None and k._keepalive_task.done()


async def test_disconnect_discards_the_session_and_its_saved_copy(mock_mcp, mock_state, tmp_path):
    store = tmp_path / "kite_mcp_session.json"
    mock_state.kite_logged_in = True
    k = _kite(mock_mcp, store)
    await k.start()
    await k.auth_state()
    old = k.client._session_id
    st = await k.disconnect()
    assert st["state"] == "login_required" and "Disconnected" in st["reason"]
    assert k.client._session_id != old and json.loads(store.read_text())["session_id"] == k.client._session_id
    await k.close()


async def test_demo_mode_keeps_no_session_file(demo):
    assert demo.container.kite.client.session_store is None


async def test_disconnect_endpoint_and_status_after_it(demo, mock_state):
    r = await demo.client.post(API + "/sources/broker/logout")
    assert r.status_code == 200 and r.json()["kite_mcp"]["state"] == "login_required"
    events = (await demo.client.get(API + "/audit/events")).json()["items"]
    assert events[0]["action"] == "broker.logout"
    mock_state.kite_logged_in = True  # the mock's approval is global, so restore the state other tests expect
    await demo.container.kite.auth_state()


async def test_a_login_link_is_issued_on_a_fresh_session_unless_a_live_link_exists(mock_mcp, mock_state, tmp_path):
    """Reported 27 Oct 2026: Kite login failed. Kite ends every MCP session 12 hours after it was opened (then answers
    "Session terminated"), but the API kept one session for as long as it could, so the morning login was bound to a
    session opened the evening before and died minutes later. A login therefore starts on a new session, which gets
    Kite's full 12 hours, while a link still inside Kite's 5-minute signature window keeps its session."""
    from datetime import UTC, datetime, timedelta

    from app.connectors.kite_mcp import LOGIN_LINK_TTL_SECONDS

    store = tmp_path / "kite_mcp_session.json"
    mock_state.kite_logged_in = False
    k = _kite(mock_mcp, store)
    await k.start()
    old = k.client._session_id

    first = await k.login_link()
    assert first["login_url"] and k.client._session_id != old, "the link belongs to a newly opened session"
    assert json.loads(store.read_text())["session_id"] == k.client._session_id, "the new session is saved"
    fresh = k.client._session_id

    calls = k.client._stats["calls"]
    again = await k.login_link()  # a second click while the first login may still be in progress
    assert again["login_url"] and k.client._session_id == fresh, "a live link's session is not replaced"
    # Kite's login tool, called on a session not yet approved, clears that session's Kite data ("error completing Kite
    # session" for a login in progress), so a second click reuses the link instead of asking for another one.
    assert again["login_url"] == first["login_url"]
    assert k.client._stats["calls"] == calls + 1, "only get_profile ran; the login tool was not called again"

    # Kite's callback page failed ("error completing Kite session", Zerodha's hosted server, 27 and 30 Oct 2026):
    # "Start over" must not hand back the same link, but open a new session with a new link at once.
    restart = await k.login_link(fresh=True)
    assert k.client._session_id != fresh and restart["login_url"], "starting over uses a new session"
    started_over = k.client._session_id

    k._link_issued_at = datetime.now(UTC) - timedelta(seconds=LOGIN_LINK_TTL_SECONDS + 1)  # every link has expired
    await k.login_link()
    assert k.client._session_id != started_over
    await k.close()
    mock_state.kite_logged_in = True  # the mock's approval is global, so restore the state other tests expect
