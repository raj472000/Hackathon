"""Zerodha Kite MCP as the fallback price source when the NSE MCP servers cannot answer.

Kite quotes are live for the account holder's session. `ohlc.close` in a Kite quote is the previous session's
close, so the day's change is computed against it here. Nothing is substituted when Kite cannot answer either.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.connectors import parsing as ps
from app.connectors.base import ProviderUnavailable
from app.core.container import Container

KITE_TIMEOUT_SECONDS = 20.0
SOURCE = "Zerodha Kite MCP (get_quotes)"


def quote_row(q: dict[str, Any]) -> dict[str, Any] | None:
    last = ps.to_float(q.get("last_price"))
    if last is None:
        return None
    prev = ps.to_float((q.get("ohlc") or {}).get("close"))
    t = ps.parse_datetime(q.get("timestamp") or q.get("last_trade_time"))
    return {"last_price": last, "prev_close": prev, "change_pct": round((last / prev - 1) * 100, 4) if prev else None,
            "as_of": t.isoformat() if t else None, "volume": ps.to_float(q.get("volume")), "oi": ps.to_float(q.get("oi")),
            "high": ps.to_float((q.get("ohlc") or {}).get("high")), "low": ps.to_float((q.get("ohlc") or {}).get("low")),
            "open": ps.to_float((q.get("ohlc") or {}).get("open")), "source_id": "get_quotes"}


async def quotes(c: Container, keys: list[str], wait_seconds: float = KITE_TIMEOUT_SECONDS) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Quotes for `EXCHANGE:SYMBOL` keys, and the state of the attempt."""
    if c.settings.is_demo or getattr(c, "kite", None) is None or c.kite.client is None:
        return {}, {"state": "not_configured", "reason": "Kite MCP is not configured"}
    if not keys:
        return {}, {"state": "ok"}
    try:
        raw = await asyncio.wait_for(c.kite.quotes_raw(keys), wait_seconds)
    except ProviderUnavailable as e:
        return {}, {"state": e.state, "reason": str(e)[:200]}
    except TimeoutError:
        return {}, {"state": "unavailable", "reason": f"Kite MCP did not answer within {wait_seconds:g}s"}
    out = {k: row for k, q in raw.items() if (row := quote_row(q)) is not None}
    return out, {"state": "ok" if out else "unavailable", "reason": None if out else "Kite returned no prices for these instruments"}


async def equity_quotes(c: Container, symbols: list[str], exchange: str = "NSE",
                        wait_seconds: float = KITE_TIMEOUT_SECONDS) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Quotes keyed by bare symbol."""
    got, state = await quotes(c, [f"{exchange}:{s.upper()}" for s in symbols], wait_seconds)
    return {k.split(":", 1)[1]: v for k, v in got.items()}, state


BREADTH_TIMEOUT_SECONDS = 60.0  # the whole equity universe is about a dozen get_quotes batches


async def breadth(c: Container, symbols: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Advances, declines and unchanged counted from Kite quotes over `symbols` (the stored NSE equity universe),
    each against its previous close. None when Kite cannot answer; nothing is estimated for unpriced stocks."""
    got, state = await equity_quotes(c, symbols, wait_seconds=BREADTH_TIMEOUT_SECONDS)
    moves = [q["change_pct"] for q in got.values() if q.get("change_pct") is not None]
    if not moves:
        return None, state if state.get("state") != "ok" else {"state": "unavailable", "reason": "Kite returned no price changes"}
    stamp = max((q["as_of"] for q in got.values() if q.get("as_of")), default=None)
    return {"advances": sum(m > 0 for m in moves), "declines": sum(m < 0 for m in moves), "unchanged": sum(m == 0 for m in moves),
            "priced": len(moves), "universe": len(symbols), "as_of": stamp}, state
