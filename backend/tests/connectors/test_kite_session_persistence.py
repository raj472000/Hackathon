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
