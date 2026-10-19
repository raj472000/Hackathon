"""Zerodha Kite MCP adapter (broker fallback for data coverage).

Policy: this product never places, modifies or cancels orders and never reads holdings, positions,
margins or trades. Only the read-only market-data tools in `ALLOWED_TOOLS` can be called; the MCP
client refuses everything else before any request is sent.

Market-data tools need an authorised broker session. Authorisation is interactive: the `login`
tool returns a Kite URL, the account holder approves it in a browser, and the approval is bound to
this client's MCP session. No broker password or token is handled or stored by this application.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, date, datetime
from typing import Any

from app.connectors import parsing as ps
from app.connectors.base import BarsResult, ProviderUnavailable, QuoteResult, capability
from app.connectors.binding import BindError, bind_args
from app.connectors.mcp_client import McpClient, McpError, McpToolError
from app.domain.provenance import DataPoint

ALLOWED_TOOLS = frozenset({"login", "get_profile", "search_instruments", "get_quotes", "get_ohlc", "get_ltp", "get_historical_data"})
QUOTE_BATCH = 250  # the tool documents a 500-instrument ceiling; stay well under it
_LOGIN_HINT = re.compile(r"log\s?in|logged in|session|unauthori[sz]ed|authenticat", re.I)
_URL = re.compile(r"https?://[^\s)\]>\"']+")
ORDER_POLICY = "blocked by policy: this product never places orders"
LOGIN_LINK_MAX_AGE_SECONDS = 60  # Kite rejects older authorisation links as "invalid authorize session"


class KiteMcpProvider:
    name = "kite_mcp"

    def __init__(self, client: McpClient | None, is_demo: bool = False, exchange: str = "NSE", keepalive_seconds: float = 0.0) -> None:
        self.client = client
        self.keepalive_seconds = keepalive_seconds
        self._keepalive_task: asyncio.Task[None] | None = None
        self.is_demo = is_demo
        self.exchange = exchange
        self._instruments: dict[str, dict[str, Any]] = {}
        self._start_error: str | None = None
        self._auth: dict[str, Any] = {"state": "unknown"}
        self._pending_login_url: str | None = None  # a link has been issued and not yet approved
        self._link_issued_at: datetime | None = None
        self._login_error: str | None = None
        self._resets_at_auth = 0  # the client's session-reset count when the session was last confirmed authorised

    async def start(self) -> None:
        if self.client is None:
            return
        try:
            await self.client.connect(refresh=True)
            self._start_error = None
        except McpError as e:
            self._start_error = str(e)
            return
        if self.client.restored_session and self._auth.get("state") == "unknown":
            # The session saved by the previous run is still open at Kite: find out at once whether the account
            # holder's approval still applies, so the status page does not ask for a login that is not needed.
            with contextlib.suppress(Exception):
                await self.auth_state()
        if self.keepalive_seconds > 0 and (self._keepalive_task is None or self._keepalive_task.done()):
            self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _keepalive(self) -> None:
        """While authorised, confirm the session every `keepalive_seconds`: an idle session stays in use, and a session
        Kite has ended (its daily token expiry, a restart on its side) shows as "log in again" within minutes."""
        while True:
            await asyncio.sleep(self.keepalive_seconds)
            if self._auth.get("state") == "authorized":
                with contextlib.suppress(Exception):
                    await self.auth_state()

    async def disconnect(self) -> dict[str, Any]:
        """Discard the approved session (and its saved copy). This app then holds no access to the account."""
        if self.client is None:
            return {"state": "not_configured"}
        await self.client.forget_session()
        self._pending_login_url = self._link_issued_at = None
        self._resets_at_auth = self.client.session_resets
        self._auth = {"state": "login_required", "checked_at": datetime.now(UTC).isoformat(),
                      "reason": "Disconnected from Kite on request. Log in again to use broker data.", "login_url": None, "issued_at": None}
        return self._auth

    async def close(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._keepalive_task
        if self.client is not None:
            await self.client.close()

    # ------------------------------------------------------------------ capabilities / health
    def capabilities(self) -> dict[str, dict[str, Any]]:
        c = self.client
        if c is None:
            reason = "broker MCP URL is not configured"
        elif not c.connected:
            reason = f"broker MCP unavailable: {c.health()['last_error'] or self._start_error or 'not connected'}"
        else:
            reason = None

        def cap(tool: str) -> dict[str, Any]:
            if reason:
                return capability(False, None, reason)
            return capability(True, tool) if c.has_tool(tool) else capability(False, None, "tool not advertised by the broker MCP")  # type: ignore[union-attr]

        return {
            "history": cap("get_historical_data"), "index_history": cap("get_historical_data"),
            "intraday_history": cap("get_historical_data"), "quote": cap("get_quotes"), "search": cap("search_instruments"),
            "corporate_actions": capability(False, None, "the broker MCP advertises no corporate-actions tool"),
            "fundamentals": capability(False, None, "the broker MCP advertises no fundamentals tool"),
            "order_placement": capability(False, None, ORDER_POLICY),
        }

    def _note_session_loss(self) -> None:
        """Kite binds the approval to the MCP session. If Kite forgot the session (it answers "Invalid session
        ID", e.g. after its own restart or a long idle period) the client had to open a new one, so the
        approval is gone: say so instead of reporting a stale "authorized"."""
        c = self.client
        if c is not None and self._auth.get("state") == "authorized" and c.session_resets > self._resets_at_auth:
            self._auth = {"state": "login_required", "checked_at": datetime.now(UTC).isoformat(),
                          "reason": "Kite ended the previous session, so its approval no longer applies. Approve access again.",
                          "session_lost_at": c.session_reset_at.isoformat() if c.session_reset_at else None}

    def health(self) -> dict[str, Any]:
        if self.client is None:
            return {"name": "kite_mcp", "state": "not_configured", "url": None}
        self._note_session_loss()
        session = {"kept_across_restarts": self.client.session_store is not None, "restored_from_previous_run": self.client.restored_session,
                   "keepalive_seconds": self.keepalive_seconds or None}
        return {**self.client.health(), "auth": self._auth, "session": session}

    async def _call(self, tool: str, intents: dict[str, Any]) -> Any:
        c = self.client
        if c is None:
            raise ProviderUnavailable("broker MCP URL is not configured", "not_configured", self.name)
        try:
            if not c.connected:
                await c.connect(refresh=True)
            if not c.has_tool(tool):
                raise ProviderUnavailable(f"broker MCP does not advertise {tool}", "not_supported", self.name)
            return await c.call(tool, bind_args(c.tools[tool].input_schema, intents))
        except BindError as e:
            raise ProviderUnavailable(f"{tool}: {e}", "not_supported", self.name) from e
        except McpToolError as e:
            if _LOGIN_HINT.search(str(e)):
                self._auth = {**self._auth, "state": "login_required"}
                raise ProviderUnavailable("broker login required: authorise the Kite session from the Data status page",
                                          "login_required", self.name) from e
            # Before login some data tools fail with a generic message ("Failed to get quotes"). Confirm the session
            # with get_profile rather than assuming, and never issue a new link here: rotating the link while the user
            # is approving the previous one would make that approval fail.
            if tool in ("get_historical_data", "get_quotes", "get_ohlc", "get_ltp") and self._auth.get("state") != "authorized":
                if (await self.auth_state()).get("state") == "login_required":
                    raise ProviderUnavailable("broker login required: authorise the Kite session from the Data status page",
                                              "login_required", self.name) from e
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e
        except McpError as e:
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e

    # ------------------------------------------------------------------ auth
    async def _issue_login_link(self) -> str | None:
        """A new authorisation link from Kite's `login` tool. Kite rejects links after a short time
        ("invalid authorize session"), so a link is issued when the user is about to open it, never reused."""
        self._pending_login_url = self._link_issued_at = None
        try:
            text = await self.client.call("login", {})  # type: ignore[union-attr]
        except McpError as e:
            self._login_error = f"Kite's login tool failed: {str(e)[:200]}"
            return None
        m = _URL.search(text if isinstance(text, str) else str(text))
        self._pending_login_url = m.group(0) if m else None
        self._link_issued_at = datetime.now(UTC) if m else None
        self._login_error = None if m else "Kite's login tool returned no authorisation link"
        return self._pending_login_url

    def _link_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {"login_url": self._pending_login_url,
                               "issued_at": self._link_issued_at.isoformat() if self._link_issued_at else None}
        if self._pending_login_url is None and self._login_error:
            out["reason"] = self._login_error
        return out

    async def login_link(self) -> dict[str, Any]:
        """A fresh login link for the account holder to open now (or `authorized` when no login is needed)."""
        st = await self.auth_state()
        if st.get("state") != "login_required":
            return st
        await self._issue_login_link()  # only here, when the user is about to open it
        self._auth = {**{k: v for k, v in st.items() if k != "reason"}, **self._link_fields()}
        return self._auth

    async def auth_state(self) -> dict[str, Any]:
        """authorized | login_required (with login_url) | unavailable | not_configured."""
        if self.client is None:
            self._auth = {"state": "not_configured"}
            return self._auth
        now = datetime.now(UTC).isoformat()
        note = "Open the link, approve access in Kite, then re-check. Access is read-only market data."
        try:
            if not self.client.connected:
                await self.client.connect(refresh=True)
            profile = await self.client.call("get_profile", {})
        except McpToolError:
            # The server answered but would not return the profile: the session is not authorised.
            # Kite's wording varies ("Please log in first", "Failed to execute get_profile"), so any
            # tool error here means "log in", while transport failures below mean "unavailable".
            self._note_session_loss()
            # No link is issued here: links are issued only when the user asks to open one (login_link), so a
            # background check can never replace the link the user is approving.
            age = (datetime.now(UTC) - self._link_issued_at).total_seconds() if self._link_issued_at else None
            if age is not None and age > LOGIN_LINK_MAX_AGE_SECONDS * 10:
                self._pending_login_url = self._link_issued_at = None  # long expired; the page requests a fresh one
            lost = {k: v for k, v in self._auth.items() if k in ("reason", "session_lost_at")} if self._auth.get("session_lost_at") else {}
            self._auth = {"state": "login_required", "checked_at": now, "note": note, **lost, **self._link_fields()}
            return self._auth
        except McpError as e:
            self._auth = {"state": "unavailable", "reason": str(e)[:300], "checked_at": now}
            return self._auth
        who = profile.get("user_id") if isinstance(profile, dict) else None
        self._auth = {"state": "authorized", "user_id": who, "checked_at": now}
        self._resets_at_auth = self.client.session_resets
        self._pending_login_url = self._link_issued_at = None
        return self._auth

    # ------------------------------------------------------------------ data
    async def resolve(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        if sym in self._instruments:
            return self._instruments[sym]
        key = f"{self.exchange}:{sym}"
        # `id` is the exchange:tradingsymbol key, so this is an exact lookup rather than a text search.
        payload = await self._call("search_instruments", {"query": key, "filter_on": "id", "limit": 20})
        rows = ps.find_rows(payload)
        exact = [r for r in rows if str(r.get("id", "")).upper() == key
                 or (str(r.get("tradingsymbol", "")).upper() == sym and str(r.get("exchange", "")).upper() == self.exchange)]
        if not exact:
            raise ProviderUnavailable(f"{key} not found at the broker", "unavailable", self.name)
        self._instruments[sym] = exact[0]
        return exact[0]

    async def search(self, query: str) -> list[dict[str, Any]]:
        rows = ps.find_rows(await self._call("search_instruments", {"query": query, "limit": 50}))
        return [{"symbol": str(r.get("tradingsymbol", "")).upper(), "name": r.get("name"), "exchange": r.get("exchange"),
                 "segment": r.get("segment"), "instrument_type": r.get("instrument_type"), "lot_size": r.get("lot_size"),
                 "tick_size": r.get("tick_size"), "isin": r.get("isin"), "provider": self.name}
                for r in rows if str(r.get("exchange", "")).upper() == self.exchange]

    async def get_history(self, symbol: str, start: date, end: date) -> BarsResult:
        inst = await self.resolve(symbol)
        payload = await self._call("get_historical_data", {"instrument_token": int(inst["instrument_token"]), "start_date": start,
                                                           "end_date": end, "interval": "day"})
        rows = ps.find_rows(payload)
        if rows and not isinstance(rows[0], dict):
            rows = []
        if not rows and isinstance(payload, dict):  # candle-array style: {"candles": [[ts, o, h, l, c, v], ...]}
            candles = payload.get("candles") or (payload.get("data") or {}).get("candles") or []
            rows = [dict(zip(("date", "open", "high", "low", "close", "volume"), c, strict=False)) for c in candles]
        df = ps.bars_frame(rows)
        if df.empty:
            raise ProviderUnavailable(f"get_historical_data: no rows returned for {symbol}", "unavailable", self.name)
        return BarsResult(symbol.upper(), df, self.name, "get_historical_data", datetime.now(UTC), "split_adjusted", "INR", self.is_demo)

    async def get_quotes(self, symbols: list[str]) -> dict[str, QuoteResult]:
        out: dict[str, QuoteResult] = {}
        syms = [s.upper() for s in symbols]
        for i in range(0, len(syms), QUOTE_BATCH):
            keys = [f"{self.exchange}:{s}" for s in syms[i: i + QUOTE_BATCH]]
            payload = await self._call("get_quotes", {"symbols": keys})
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            now = datetime.now(UTC)
            for key, q in (data or {}).items():
                if not isinstance(q, dict) or ":" not in key:
                    continue
                sym = key.split(":", 1)[1].upper()
                ohlc = q.get("ohlc") or {}
                as_of = ps.parse_datetime(q.get("timestamp") or q.get("last_trade_time"))
                raw = {"last_price": q.get("last_price"), "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                       "prev_close": ohlc.get("close"), "volume": q.get("volume"),
                       "upper_circuit": q.get("upper_circuit_limit"), "lower_circuit": q.get("lower_circuit_limit")}
                fields = {
                    f: DataPoint(field=f, value=ps.to_float(v), provider=self.name, retrieved_at=now, as_of=as_of,
                                 currency=None if f == "volume" else "INR", unit="shares" if f == "volume" else "price",
                                 source_id="get_quotes", status="ok" if ps.to_float(v) is not None else "missing",
                                 reason=None if ps.to_float(v) is not None else f"get_quotes did not return {f}", is_demo=self.is_demo)
                    for f, v in raw.items()
                }
                out[sym] = QuoteResult(sym, fields, self.name, now, q)
        return out
