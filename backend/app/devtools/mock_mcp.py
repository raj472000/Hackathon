"""DEMO-ONLY mock MCP servers (real Streamable HTTP MCP, synthetic data).

Three servers mirror the *shape* of the configured upstreams so the whole connector path runs
locally and in tests:

    port      NSE bhavcopy  (tool names as advertised by mcp.nseindia.in/bhavcopy/cm/mcp)
    port + 1  NSE CM market (subset of the live-market tool names)
    port + 2  Kite          (read tools, plus order tools the application must never call)

Tool names for the NSE mocks come from public write-ups of the real servers; argument names are
this mock's own and are *not* assumed by the connector, which binds from the advertised schema.

Run:  uv run python -m app.devtools.mock_mcp --port 9100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date
from typing import Any

import pandas as pd
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.devtools import demo_data as dd

NOTE = "DEMO synthetic data. Not real market data."
PAGE_ROWS = 63  # the real history tool returns at most about three months per call


class MockState:
    def __init__(self) -> None:
        self.kite_logged_in = False
        self.forbidden_calls: list[str] = []
        self.calls: list[str] = []
        self.fail_next: dict[str, int] = {}
        self.port = 9100
        self.link_ttl_seconds = 300.0  # like the real server, an authorisation link expires
        self.kite_forget_session = False  # next request with a session id gets Kite's "Invalid session ID"


STATE = MockState()


def _j(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _err(msg: str) -> str:
    raise ToolError(msg)


def _bars(symbol: str, source: str) -> pd.DataFrame:
    if dd.by_symbol(symbol) is None:
        _err(f"Symbol not found: {symbol}")
    return dd.bars(symbol, source)


def _rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"date": ts.date().isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "prevClose": r.prev_close,
         "volume": int(r.volume), "tradedValue": r.traded_value, "deliveryQty": 0}
        for ts, r in df.iterrows()
    ]


# ------------------------------------------------------------------ NSE bhavcopy mock
def build_bhavcopy() -> MCPServer:
    s = MCPServer("nse-bhavcopy-mock", version="demo")

    @s.tool(description="Daily OHLCV history for a symbol, newest first, at most ~3 months per call. " + NOTE)
    def get_stock_history(symbol: str, endDate: str | None = None) -> str:  # noqa: N803
        STATE.calls.append("get_stock_history")
        if STATE.fail_next.get("get_stock_history", 0) > 0:
            STATE.fail_next["get_stock_history"] -= 1
            _err("temporary upstream failure")
        df = _bars(symbol, "nse")
        if endDate:
            df = df.loc[: pd.Timestamp(endDate)]
        page = df.iloc[-PAGE_ROWS:]
        nxt = None
        if len(df) > PAGE_ROWS:
            nxt = df.index[-PAGE_ROWS - 1].date().isoformat()
        return _j({"symbol": symbol.upper(), "data": _rows(page)[::-1], "next_end_date": nxt, "note": NOTE})

    @s.tool(description="Closing price of a symbol on a date (YYYY-MM-DD). " + NOTE)
    def get_ltp_by_date(symbol: str, date: str) -> str:  # noqa: A002
        df = _bars(symbol, "nse")
        ts = pd.Timestamp(date)
        if ts not in df.index:
            _err(f"Symbol not found: {symbol}")
        return _j({"symbol": symbol.upper(), "date": date, "close": float(df.at[ts, "close"])})

    @s.tool(description="52-week high and low with dates. " + NOTE)
    def get_52_week_high_low(symbol: str) -> str:
        df = _bars(symbol, "nse").iloc[-252:]
        hi, lo, last = df["high"].max(), df["low"].min(), df["close"].iloc[-1]
        return _j({"symbol": symbol.upper(), "last_close": last, "52w_high": hi, "52w_high_date": df["high"].idxmax().date(),
                   "52w_low": lo, "52w_low_date": df["low"].idxmin().date(),
                   "position_in_range_pct": round((last - lo) / (hi - lo) * 100, 2), "trading_days": len(df)})

    @s.tool(description="Simple moving average of closes over N trading days. " + NOTE)
    def moving_average(symbol: str, days: int = 20) -> str:
        df = _bars(symbol, "nse")
        return _j({"symbol": symbol.upper(), "days": days, "sma": round(float(df["close"].iloc[-days:].mean()), 2)})

    @s.tool(description="Volume trend over N trading days. " + NOTE)
    def get_volume_analysis(symbol: str, days: int = 10) -> str:
        df = _bars(symbol, "nse").iloc[-days:]
        return _j({"symbol": symbol.upper(), "days": days, "avg_volume": int(df["volume"].mean()), "max_volume": int(df["volume"].max())})

    @s.tool(description="Return and max drawdown for several symbols over N months. " + NOTE)
    def compare_stocks(symbols: list[str], months: int = 1) -> str:
        out = []
        for sym in symbols:
            c = _bars(sym, "nse")["close"].iloc[-(21 * months + 1):]
            out.append({"symbol": sym.upper(), "start": float(c.iloc[0]), "end": float(c.iloc[-1]),
                        "return_pct": round((c.iloc[-1] / c.iloc[0] - 1) * 100, 2),
                        "max_drawdown_pct": round(float((c / c.cummax() - 1).min() * 100), 2)})
        return _j({"months": months, "data": out})

    @s.tool(description="End-of-day snapshot for many symbols. " + NOTE)
    def get_bulk_quote(symbols: list[str]) -> str:
        out = []
        for sym in symbols:
            if dd.by_symbol(sym) is None:
                continue
            df = dd.bars(sym, "nse")
            out.append({"symbol": sym.upper(), **_rows(df.iloc[-1:])[0]})
        return _j({"data": out, "note": NOTE})

    def _movers(d: str | None) -> pd.DataFrame:
        rows = []
        for inst in dd.instruments():
            df = dd.bars(inst.symbol, "nse")
            if d:
                df = df.loc[: pd.Timestamp(d)]
            r = df.iloc[-1]
            rows.append({"symbol": inst.symbol, "close": r.close, "change_pct": round((r.close / r.prev_close - 1) * 100, 2),
                         "volume": int(r.volume), "date": df.index[-1].date().isoformat()})
        return pd.DataFrame(rows)

    @s.tool(description="Top gainers or losers on a date. " + NOTE)
    def get_top_movers(date: str | None = None, limit: int = 10, direction: str = "gainers") -> str:  # noqa: A002
        m = _movers(date).sort_values("change_pct", ascending=direction == "losers").head(limit)
        return _j({"direction": direction, "data": m.to_dict("records")})

    @s.tool(description="Most traded stocks on a date. " + NOTE)
    def get_top_by_volume(date: str | None = None, limit: int = 10) -> str:  # noqa: A002
        return _j({"data": _movers(date).sort_values("volume", ascending=False).head(limit).to_dict("records")})

    @s.tool(description="Advance and decline counts on a date. " + NOTE)
    def get_market_breadth(date: str | None = None) -> str:  # noqa: A002
        m = _movers(date)
        return _j({"date": m["date"].max(), "advances": int((m.change_pct > 0).sum()), "declines": int((m.change_pct < 0).sum()),
                   "unchanged": int((m.change_pct == 0).sum()), "note": NOTE})

    @s.tool(description="Dividends, splits, bonus and similar actions for a symbol. " + NOTE)
    def get_corporate_actions(symbol: str) -> str:
        if dd.by_symbol(symbol) is None:
            _err(f"Symbol not found: {symbol}")
        return _j({"symbol": symbol.upper(), "data": dd.corporate_actions(symbol)})

    def _search(q: str) -> list[dict[str, str]]:
        ql = q.lower()
        return [{"symbol": i.symbol, "name": i.name, "series": "EQ"} for i in dd.instruments() if ql in i.symbol.lower() or ql in i.name.lower()]

    @s.tool(description="Find symbols by name or partial symbol. " + NOTE)
    def search_symbols(query: str) -> str:
        return _j({"data": _search(query)[:50]})

    @s.tool(description="Ticker lookup by keyword, no prices. " + NOTE)
    def nse_lookup_symbol(keyword: str) -> str:
        return _j({"data": _search(keyword)[:50]})

    return s


# ------------------------------------------------------------------ NSE CM market mock
def build_cm_market() -> MCPServer:
    s = MCPServer("cm-market-mock", version="demo")

    def _quote(symbol: str) -> dict[str, Any]:
        df = _bars(symbol, "broker")  # the live side is current even where the EOD archive lags
        r = df.iloc[-1]
        return {"symbol": symbol.upper(), "open": r.open, "high": r.high, "low": r.low, "lastPrice": r.close,
                "previousClose": r.prev_close, "change": round(r.close - r.prev_close, 2),
                "pChange": round((r.close / r.prev_close - 1) * 100, 2), "totalTradedVolume": int(r.volume),
                "totalTradedValue": r.traded_value, "lastUpdateTime": f"{df.index[-1].date().isoformat()}T15:30:00+05:30"}

    @s.tool(description="Live quote for one stock (delayed by a few minutes). " + NOTE)
    def cm_get_stock_quote(symbol: str) -> str:
        STATE.calls.append("cm_get_stock_quote")
        return _j({**_quote(symbol), "note": NOTE})

    @s.tool(description="Freshness of the live data. " + NOTE)
    def cm_get_data_status() -> str:
        return _j({"status": "ok", "lastUpdateTime": f"{date.today().isoformat()}T15:30:00+05:30", "delayMinutes": 3, "note": NOTE})

    def _all() -> list[dict[str, Any]]:
        return [_quote(i.symbol) for i in dd.instruments()]

    @s.tool(description="Live top gainers and losers. " + NOTE)
    def nse_get_market_movers(limit: int = 5) -> str:
        q = sorted(_all(), key=lambda r: r["pChange"])
        return _j({"gainers": q[::-1][:limit], "losers": q[:limit], "note": NOTE})

    @s.tool(description="Top gainers. " + NOTE)
    def nse_get_gainers(limit: int = 10) -> str:
        return _j({"data": sorted(_all(), key=lambda r: -r["pChange"])[:limit]})

    @s.tool(description="Top losers. " + NOTE)
    def nse_get_losers(limit: int = 10) -> str:
        return _j({"data": sorted(_all(), key=lambda r: r["pChange"])[:limit]})

    @s.tool(description="Equity series stocks. " + NOTE)
    def cm_get_equity_stocks(series: str = "EQ") -> str:
        return _j({"series": series, "data": [{"symbol": i.symbol, "name": i.name} for i in dd.instruments()]})

    return s


# ------------------------------------------------------------------ Kite mock
def build_kite() -> MCPServer:
    s = MCPServer("Kite MCP Server (mock)", version="demo")

    def _auth() -> None:
        if not STATE.kite_logged_in:
            _err("Please log in first using the login tool")

    def _key(k: str) -> dd.DemoInstrument:
        sym = k.split(":", 1)[1] if ":" in k else k
        inst = dd.by_symbol(sym)
        if inst is None:
            _err(f"instrument not found: {k}")
        return inst  # type: ignore[return-value]

    @s.tool(description="Login to Kite API. Returns a link that the user should click to authorize access.")
    def login() -> str:
        if STATE.kite_logged_in:
            return "You are already logged in to the demo broker session."
        return f"Please login to Kite by opening this URL: http://127.0.0.1:{STATE.port + 2}/authorize?token=demo&t={time.time():.3f}"

    @s.tool(description="Retrieve the user's profile information.")
    def get_profile() -> str:
        _auth()
        return _j({"user_id": "DEMO01", "user_name": "Demo User", "broker": "DEMO", "exchanges": ["NSE"]})

    @s.tool(description="Search instruments. Supports pagination for large result sets.")
    def search_instruments(query: str, filter_on: str | None = None, limit: int | None = None) -> str:
        # as on the real server, instrument search works before login
        q = query.lower()
        every = [*dd.instruments(), *dd.indices()]
        if filter_on in (None, "id"):
            hits = [i for i in every if q in f"nse:{i.symbol}".lower()]
        else:
            hits = [i for i in every if q in i.symbol.lower() or q in i.name.lower()]
        data = [{"instrument_token": i.token, "exchange_token": i.token % 100000, "tradingsymbol": i.symbol, "name": i.name,
                 "isin": "", "exchange": "NSE", "segment": "INDICES" if i.instrument_type == "INDEX" else "NSE",
                 "instrument_type": "EQ", "lot_size": 1, "tick_size": 0.05, "id": f"NSE:{i.symbol}"} for i in hits]
        return _j({"data": data[: limit or 50], "pagination": {"total": len(data)}})

    def _q(keys: list[str]) -> dict[str, Any]:
        out = {}
        for k in keys:
            if dd.by_symbol(k.split(":", 1)[-1]) is None:
                continue  # as on the real server, unknown instruments are absent from the response
            inst = _key(k)
            df = dd.bars(inst.symbol, "broker")
            r = df.iloc[-1]
            out[k] = {"instrument_token": inst.token, "last_price": r.close, "volume": int(r.volume),
                      "timestamp": f"{df.index[-1].date().isoformat()} 15:30:00", "net_change": round(r.close - r.prev_close, 2),
                      "ohlc": {"open": r.open, "high": r.high, "low": r.low, "close": r.prev_close},
                      "lower_circuit_limit": round(r.prev_close * 0.9, 2), "upper_circuit_limit": round(r.prev_close * 1.1, 2)}
        return out

    @s.tool(description="Get market data quotes for a list of instruments")
    def get_quotes(instruments: list[str]) -> str:
        _auth()
        STATE.calls.append("get_quotes")
        return _j(_q(instruments))

    @s.tool(description="Get OHLC data for a list of instruments")
    def get_ohlc(instruments: list[str]) -> str:
        _auth()
        return _j({k: {"instrument_token": v["instrument_token"], "last_price": v["last_price"], "ohlc": v["ohlc"]} for k, v in _q(instruments).items()})

    @s.tool(description="Get latest trading prices for a list of instruments")
    def get_ltp(instruments: list[str]) -> str:
        _auth()
        return _j({k: {"instrument_token": v["instrument_token"], "last_price": v["last_price"]} for k, v in _q(instruments).items()})

    @s.tool(description="Get historical price data for an instrument")
    def get_historical_data(instrument_token: int, from_date: str, to_date: str, interval: str,
                            continuous: bool = False, oi: bool = False) -> str:
        _auth()
        STATE.calls.append("get_historical_data")
        if interval != "day":
            _err("only the 'day' interval is available in the mock")
        inst = dd.by_token(instrument_token)
        if inst is None:
            _err(f"instrument token not found: {instrument_token}")
        df = dd.bars(inst.symbol, "broker").loc[pd.Timestamp(from_date[:10]): pd.Timestamp(to_date[:10])]  # type: ignore[union-attr]
        return _j([{"date": f"{ts.date().isoformat()}T00:00:00+05:30", "open": r.open, "high": r.high, "low": r.low,
                    "close": r.close, "volume": int(r.volume)} for ts, r in df.iterrows()])

    def _forbidden(name: str) -> str:
        STATE.forbidden_calls.append(name)
        return _j({"status": "mock", "note": f"{name} reached the mock: the application must never call this"})

    @s.tool(description="Place an order")
    def place_order(exchange: str, tradingsymbol: str, transaction_type: str, quantity: int, product: str, order_type: str, variety: str) -> str:
        return _forbidden("place_order")

    @s.tool(description="Modify an existing order")
    def modify_order(order_id: str, variety: str, order_type: str) -> str:
        return _forbidden("modify_order")

    @s.tool(description="Cancel an existing order")
    def cancel_order(order_id: str, variety: str) -> str:
        return _forbidden("cancel_order")

    @s.tool(description="Get holdings for the current user.")
    def get_holdings() -> str:
        return _forbidden("get_holdings")

    @s.custom_route("/authorize", methods=["GET"])
    async def authorize(request: Request) -> PlainTextResponse:
        try:
            issued = float(request.query_params.get("t", "0"))
        except ValueError:
            issued = 0.0
        if issued and time.time() - issued > STATE.link_ttl_seconds:
            return PlainTextResponse("invalid authorize session", status_code=400)
        STATE.kite_logged_in = True
        return PlainTextResponse("Demo broker session authorised. You can close this tab.")

    return s


def forgets_sessions(app: Any) -> Any:
    """Like mcp.kite.trade after a session has been idle: HTTP 400, text/plain "Invalid session ID"."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and STATE.kite_forget_session and dict(scope["headers"]).get(b"mcp-session-id"):
            STATE.kite_forget_session = False
            await PlainTextResponse("Invalid session ID\n", status_code=400)(scope, receive, send)
            return
        await app(scope, receive, send)

    return wrapped


SERVERS = (build_bhavcopy, build_cm_market, build_kite)


def _app(build: Any, kwargs: dict[str, Any]) -> Any:
    app = build().streamable_http_app(**kwargs)
    return forgets_sessions(app) if build is build_kite else app


def make_uvicorn_servers(port: int, log_level: str = "warning", host: str = "127.0.0.1") -> list[uvicorn.Server]:
    STATE.port = port
    kwargs: dict[str, Any] = {"json_response": True, "host": host}
    if host != "127.0.0.1":
        # Inside a container network the servers are addressed by service name.
        from mcp.server.transport_security import TransportSecuritySettings

        kwargs["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return [
        uvicorn.Server(uvicorn.Config(_app(build, kwargs), host=host, port=port + n, log_level=log_level, lifespan="on"))
        for n, build in enumerate(SERVERS)
    ]


async def serve(port: int, auto_login: bool, host: str = "127.0.0.1") -> None:
    STATE.kite_logged_in = auto_login
    await asyncio.gather(*(s.serve() for s in make_uvicorn_servers(port, "info", host)))


def main() -> None:
    ap = argparse.ArgumentParser(description="DEMO-ONLY mock MCP servers")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--no-auto-login", action="store_true", help="require the broker login flow as the real Kite MCP does")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"[mock-mcp] DEMO synthetic data on 127.0.0.1:{a.port} (bhavcopy), :{a.port + 1} (cm market), :{a.port + 2} (kite)")
    asyncio.run(serve(a.port, not a.no_auto_login, a.host))


if __name__ == "__main__":
    main()
