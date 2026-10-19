"""NSE MCP adapter (primary source for the Indian-market fields it supports).

Two servers are used: bhavcopy (end-of-day history, corporate actions, lookups) and CM market
(delayed live quotes). Capabilities are derived from the tools each server advertises at start-up;
a capability with no advertised tool is reported `not_supported` with a reason.

NSE states that MCP data is for informational and educational use, is delayed, and is not licensed
for commercial deployment or model training. Bhavcopy prices are unadjusted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from app.connectors import parsing as ps
from app.connectors.base import BarsResult, ProviderUnavailable, QuoteResult, capability
from app.connectors.binding import BindError, bind_args, find_property
from app.connectors.mcp_client import McpClient, McpError, McpNotSupported, McpToolError
from app.domain.adjust import CorporateAction, parse_action
from app.domain.provenance import DataPoint

MAX_PAGES = 60
BULK_MAX = 50  # get_bulk_quote documents at most 50 symbols per call

# capability -> (server, candidate tool names in preference order)
CAPABILITY_TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    "history": ("bhav", ("get_stock_history",)),
    "corporate_actions": ("bhav", ("get_corporate_actions",)),
    "search": ("bhav", ("search_symbols", "nse_lookup_symbol")),
    "bulk_quote": ("bhav", ("get_bulk_quote",)),
    "market_breadth": ("bhav", ("get_market_breadth",)),
    "top_movers": ("bhav", ("get_top_movers",)),
    "quote": ("cm", ("cm_get_stock_quote",)),
    "live_movers": ("cm", ("nse_get_market_movers",)),
    "data_status": ("cm", ("cm_get_data_status",)),
    # Nothing in the published tool lists serves these; they resolve to not_supported unless a
    # server starts advertising a matching tool name.
    "index_history": ("bhav", ("get_index_history",)),
    "index_quote": ("cm", ("cm_get_index_quote",)),
    "intraday_history": ("cm", ("get_intraday_history", "cm_get_intraday_data")),
    "fundamentals": ("bhav", ("get_financial_results", "get_fundamentals")),
}


class ServerRefused(ProviderUnavailable):
    """The tool ran and answered with an error payload (as opposed to a transport failure)."""


class NseMcpProvider:
    name = "nse_mcp"

    def __init__(self, bhav: McpClient | None, cm: McpClient | None, is_demo: bool = False) -> None:
        self._clients: dict[str, McpClient | None] = {"bhav": bhav, "cm": cm}
        self.is_demo = is_demo
        self._start_errors: dict[str, str] = {}

    async def start(self) -> None:
        """Connect and discover tools. A server that is down leaves a degraded state; it never
        prevents the application from starting."""
        for key, c in self._clients.items():
            if c is None:
                continue
            try:
                await c.connect(refresh=True)
                self._start_errors.pop(key, None)
            except McpError as e:
                self._start_errors[key] = str(e)

    async def close(self) -> None:
        for c in self._clients.values():
            if c is not None:
                await c.close()

    # ------------------------------------------------------------------ capabilities
    def _resolve(self, cap: str) -> tuple[McpClient | None, str | None, str | None]:
        server, names = CAPABILITY_TOOLS[cap]
        c = self._clients[server]
        label = "NSE bhavcopy MCP" if server == "bhav" else "NSE CM market MCP"
        if c is None:
            return None, None, f"{label} URL is not configured"
        if not c.connected:
            return c, None, f"{label} unavailable: {c.health()['last_error'] or self._start_errors.get(server) or 'not connected'}"
        for n in names:
            if c.has_tool(n):
                return c, n, None
        return c, None, f"no tool advertised by {label} provides this"

    def capabilities(self) -> dict[str, dict[str, Any]]:
        out = {}
        for cap in CAPABILITY_TOOLS:
            _c, tool, reason = self._resolve(cap)
            out[cap] = capability(tool is not None, tool, reason)
        return out

    def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, name in (("bhav", "nse_bhavcopy"), ("cm", "nse_cm_market")):
            c = self._clients[key]
            out[name] = c.health() if c else {"name": name, "state": "not_configured", "url": None}
        return out

    async def _call(self, cap: str, intents: dict[str, Any]) -> tuple[Any, str]:
        c, tool, reason = self._resolve(cap)
        if c is not None and tool is None and not c.connected:
            # one reconnect attempt per call so a recovered upstream is picked up without a restart
            try:
                await c.connect(refresh=True)
                c, tool, reason = self._resolve(cap)
            except McpError as e:
                reason = f"{c.name} unavailable: {e}"  # type: ignore[union-attr]
        if c is None or tool is None:
            state = "not_configured" if c is None else ("unavailable" if not c.connected else "not_supported")
            raise ProviderUnavailable(reason or "unsupported", state, self.name)
        try:
            args = bind_args(c.tools[tool].input_schema, intents)
            payload = await c.call(tool, args)
            if (err := ps.payload_error(payload)) is not None:
                raise ServerRefused(f"{tool}: {err}", "unavailable", self.name)
            return payload, tool
        except BindError as e:
            raise ProviderUnavailable(f"{tool}: {e}", "not_supported", self.name) from e
        except McpNotSupported as e:
            raise ProviderUnavailable(str(e), "not_supported", self.name) from e
        except McpToolError as e:
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e
        except McpError as e:
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e

    # ------------------------------------------------------------------ data
    async def get_history(self, symbol: str, start: date, end: date, instrument_type: str = "EQ") -> BarsResult:
        """Daily bars in [start, end]. The history tools return a limited window per call, so this
        pages backwards using the server's `next_end_date` cursor when present, otherwise the day
        before the oldest row returned. Indices use the index-history tool when one is advertised."""
        is_index = instrument_type == "INDEX"
        cap = "index_history" if is_index else "history"
        c, tool, _ = self._resolve(cap)
        has_cursor = bool(c and tool and find_property(c.tools[tool].input_schema, "end_date"))
        frames: list[pd.DataFrame] = []
        cursor: date | None = None if end >= datetime.now(UTC).date() else end  # None lets the server use "today"
        used_tool = tool or "get_stock_history"
        warnings: list[str] = []
        ident = {"index_name": symbol} if is_index else {"symbol": symbol.upper()}
        chunk_months = 12 if is_index else 3  # the documented per-call maxima
        for page in range(MAX_PAGES):
            try:
                payload, used_tool = await self._call(cap, {**ident, "end_date": cursor, "start_date": start, "months": chunk_months})
            except ServerRefused as e:
                if not frames:
                    raise
                # NSE keeps a limited window: pages older than it answer with an error payload
                # ("Symbol not found", "No data for index ... between"). Keep the newer pages.
                warnings.append(f"history starts at {frames[-1].index[0].date()}: the server has no older data ({str(e)[:160]})")
                break
            df = ps.bars_frame(ps.find_rows(payload))
            if df.empty:
                break
            frames.append(df)
            oldest = df.index[0].date()
            if oldest <= start or not has_cursor:
                break
            nxt = ps.parse_date(ps.get_ci(payload, "next_end_date", "nextEndDate"))
            nxt = nxt or oldest - timedelta(days=1)
            if cursor is not None and nxt >= cursor:
                warnings.append("history paging stopped: the server did not advance its cursor")
                break
            cursor = nxt
            if page == MAX_PAGES - 1:
                warnings.append(f"history truncated after {MAX_PAGES} pages")
        if not frames:
            raise ProviderUnavailable(f"{used_tool}: no rows returned for {symbol}", "unavailable", self.name)
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")].sort_index().loc[pd.Timestamp(start): pd.Timestamp(end)]
        if df.empty:
            raise ProviderUnavailable(f"{used_tool}: no rows in the requested range for {symbol}", "unavailable", self.name)
        return BarsResult(symbol.upper(), df, self.name, used_tool, datetime.now(UTC), "unadjusted", "INR", self.is_demo, warnings)

    async def get_quote(self, symbol: str, instrument_type: str = "EQ") -> QuoteResult:
        if instrument_type == "INDEX":
            payload, tool = await self._call("index_quote", {"index_name": symbol})
        else:
            payload, tool = await self._call("quote", {"symbol": symbol.upper()})
        row = ps.unwrap_quote(payload)
        now = datetime.now(UTC)
        as_of = ps.parse_datetime(ps.pick(row, ps.QUOTE_TIME_KEYS))
        fields: dict[str, DataPoint] = {}
        for f, keys in ps.QUOTE_KEYS.items():
            v = ps.to_float(ps.pick(row, keys))
            fields[f] = DataPoint(
                field=f, value=v, provider=self.name, retrieved_at=now, as_of=as_of,
                currency="INR" if f not in ("volume", "change_pct") else None,
                unit="shares" if f == "volume" else "pct" if f == "change_pct" else "price",
                source_id=tool, status="delayed" if v is not None else "missing",
                reason="NSE MCP live data is delayed by a few minutes" if v is not None else f"{tool} did not return {f}",
                is_demo=self.is_demo,
            )
        return QuoteResult(symbol.upper(), fields, self.name, now, row if isinstance(row, dict) else {})

    async def get_corporate_actions(self, symbol: str) -> list[CorporateAction]:
        today = datetime.now(UTC).date()
        payload, _tool = await self._call("corporate_actions", {"symbol": symbol.upper(), "start_date": today - timedelta(days=5 * 366),
                                                                "end_date": today})
        out: list[CorporateAction] = []
        for r in ps.find_rows(payload):
            d = ps.parse_date(ps.pick(r, ("exdate", "exdt", "exdividenddate", "date", "recorddate")))
            text = str(ps.pick(r, ("purpose", "subject", "action", "description", "details")) or "")
            if d is None or not text:
                continue
            parsed = parse_action(text)
            declared = str(ps.pick(r, ("actiontype", "type")) or "").lower()
            if parsed:
                out.append(CorporateAction(d, parsed[0], parsed[1], text))
            else:
                # A split or bonus the text parser cannot read keeps its kind with no ratio, so the
                # unexplained-gap check can still warn about it instead of silently adjusting.
                kind = "split" if "split" in declared else "bonus" if "bonus" in declared else "dividend" if "dividend" in declared else "other"
                out.append(CorporateAction(d, kind, None, text))
        return sorted(out, key=lambda a: a.ex_date)

    async def search(self, query: str) -> list[dict[str, Any]]:
        payload, _ = await self._call("search", {"query": query})
        out = []
        for r in ps.find_rows(payload):
            sym = ps.pick(r, ("symbol", "ticker", "tradingsymbol"))
            if sym:
                out.append({"symbol": str(sym).upper(), "name": ps.pick(r, ("name", "companyname", "symbolinfo")),
                            "series": ps.pick(r, ("series",)), "provider": self.name})
        return out

    async def bulk_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Latest price snapshot for many symbols (get_bulk_quote, BULK_MAX per call)."""
        out: dict[str, dict[str, Any]] = {}
        syms = [x.upper() for x in symbols]
        for i in range(0, len(syms), BULK_MAX):
            payload, tool = await self._call("bulk_quote", {"symbols": syms[i: i + BULK_MAX]})
            now = datetime.now(UTC).isoformat()
            for r in ps.find_rows(payload):
                sym = ps.pick(r, ("symbol", "tradingsymbol", "ticker"))
                price = ps.to_float(ps.pick(r, ps.QUOTE_KEYS["last_price"] + ps.BAR_KEYS["close"]))
                if not sym or price is None:
                    continue
                as_of = ps.parse_date(ps.pick(r, ps.BAR_KEYS["date"] + ps.QUOTE_TIME_KEYS))
                out[str(sym).upper()] = {
                    "last_price": price, "prev_close": ps.to_float(ps.pick(r, ps.QUOTE_KEYS["prev_close"])),
                    "change_pct": ps.to_float(ps.pick(r, ps.QUOTE_KEYS["change_pct"])),
                    "volume": ps.to_float(ps.pick(r, ps.QUOTE_KEYS["volume"])), "as_of": as_of.isoformat() if as_of else None,
                    "provider": self.name, "source_id": tool, "retrieved_at": now, "is_demo": self.is_demo}
        return out

    async def market_breadth(self) -> dict[str, Any]:
        payload, tool = await self._call("market_breadth", {"date": datetime.now(UTC).date()})
        return {"data": payload, "source_id": tool, "provider": self.name, "retrieved_at": datetime.now(UTC).isoformat(), "is_demo": self.is_demo}

    async def live_movers(self) -> dict[str, Any]:
        payload, tool = await self._call("live_movers", {"limit": 10})
        return {"data": payload, "source_id": tool, "provider": self.name, "retrieved_at": datetime.now(UTC).isoformat(), "is_demo": self.is_demo}

    async def data_status(self) -> dict[str, Any]:
        payload, tool = await self._call("data_status", {})
        return {"data": payload, "source_id": tool}
