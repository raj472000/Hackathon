"""Top movers and open-interest dashboard.

- Live top gainers and losers (cash market) from the NSE CM market MCP, delayed by a few minutes.
- End-of-day F&O analytics from NSE's F&O bhavcopy for the latest published trading day: open-interest
  gainers and losers, and the long buildup / short buildup / short covering / long unwinding buckets
  (see `domain.derivatives` for the rules). Open interest is published once a day after the close.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.connectors import nse_filings as nf
from app.connectors import parsing as ps
from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.domain import derivatives as dv
from app.services import kite_fallback

IST = ZoneInfo("Asia/Kolkata")
ARCH = "https://nsearchives.nseindia.com/content/indices"
SEGMENT_LISTS = {"nifty": f"{ARCH}/ind_nifty50list.csv", "banknifty": f"{ARCH}/ind_niftybanklist.csv",
                 "niftynext50": f"{ARCH}/ind_niftynext50list.csv"}

MAX_LOOKBACK_DAYS = 10
LIVE_TIMEOUT_SECONDS = 12.0
MOVER_SEGMENTS = {"all": None, "nifty": "NIFTY", "banknifty": "BANKNIFTY", "niftynext50": "NIFTYNEXT50"}


def _movers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        sym = ps.pick(r, ("symbol",))
        if not sym:
            continue
        out.append({"symbol": str(sym).upper(), "ltp": ps.to_float(ps.pick(r, ("ltp", "lastprice"))),
                    "prev_close": ps.to_float(ps.pick(r, ("prevprice", "prevclose", "previousclose"))),
                    "change_pct": ps.to_float(ps.pick(r, ("perchange", "pchange", "netprice"))),
                    "volume": ps.to_float(ps.pick(r, ("tradequantity", "volume"))),
                    "turnover_lakh": ps.to_float(ps.pick(r, ("turnover",)))})
    return out


async def _segment_symbols(c: Container, segment: str) -> tuple[list[str], str]:
    """Symbols the broker fallback ranks: an index's constituents (NSE's published list), or for "all" every stock
    with futures and options (from the latest F&O bhavcopy)."""
    if segment != "all" and c.filings.available:
        url = SEGMENT_LISTS[segment]
        try:
            return sorted(await nf.industry_list(c.filings, url)), f"{segment} constituents"
        except ProviderUnavailable:
            pass
    _d, rows = await latest_fo(c)
    return sorted({r["TckrSymb"] for r in rows if r.get("FinInstrmTp") == "STF"}), "stocks with F&O"


async def kite_movers(c: Container, segment: str, limit: int, nse_reason: str) -> dict[str, Any]:
    try:
        symbols, universe = await _segment_symbols(c, segment)
    except ProviderUnavailable as e:
        return {"state": "unavailable", "reason": f"{nse_reason}; broker fallback could not list stocks: {str(e)[:150]}"}
    got, state = await kite_fallback.equity_quotes(c, symbols)
    if not got:
        return {"state": state["state"], "reason": f"{nse_reason}; Zerodha Kite: {state.get('reason')}"}
    rows = [{"symbol": sym, "ltp": q["last_price"], "prev_close": q["prev_close"], "change_pct": round(q["change_pct"], 2),
             "volume": q.get("volume"), "turnover_lakh": round(q["last_price"] * q["volume"] / 1e5, 2) if q.get("volume") else None}
            for sym, q in got.items() if q.get("change_pct") is not None]
    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    stamp = max((q["as_of"] for q in got.values() if q.get("as_of")), default=None)
    return {"state": "live", "segment": segment, "universe": f"{len(rows)} {universe}", "gainers": rows[:limit],
            "losers": list(reversed(rows[-limit:])), "as_of": stamp, "source": kite_fallback.SOURCE, "fallback_reason": nse_reason,
            "retrieved_at": c.clock.now().isoformat()}


async def live_movers(c: Container, segment: str, limit: int) -> dict[str, Any]:
    """NSE CM market MCP first; when it fails, Zerodha Kite quotes ranked over the segment's stocks."""
    if c.settings.is_demo:
        return {"state": "not_configured", "reason": "Live movers are not shown in demo mode."}
    try:
        res = await asyncio.wait_for(c.nse.live_movers(limit, MOVER_SEGMENTS.get(segment)), LIVE_TIMEOUT_SECONDS)
    except ProviderUnavailable as e:
        return await kite_movers(c, segment, limit, f"NSE: {str(e)[:150]}")
    except TimeoutError:
        return await kite_movers(c, segment, limit, f"NSE CM market MCP did not answer within {LIVE_TIMEOUT_SECONDS:g}s")
    d = res["data"] if isinstance(res.get("data"), dict) else {}
    return {"state": "delayed", "segment": d.get("segment") or segment, "gainers": _movers(d.get("gainers") or []),
            "losers": _movers(d.get("losers") or []), "as_of": d.get("gainersUpdatedAt") or d.get("losersUpdatedAt"),
            "source": f"NSE CM market MCP ({res['source_id']})", "retrieved_at": res["retrieved_at"]}


async def latest_fo(c: Container, day: date | None = None) -> tuple[date, list[dict[str, str]]]:
    """The F&O bhavcopy for `day`, or the latest published trading day on or before it."""
    tpl = c.settings.nse_fo_bhavcopy_url_template
    if not tpl:
        raise ProviderUnavailable("NSE_FO_BHAVCOPY_URL_TEMPLATE is not set", "not_configured", "nse_filings")
    want = day or c.clock.now().date()
    d = want
    while (want - d).days <= MAX_LOOKBACK_DAYS:
        if d.weekday() < 5 and (rows := await nf.bhavcopy(c.filings, tpl, d)) is not None:
            return d, rows
        d -= timedelta(days=1)
    raise ProviderUnavailable(f"NSE has no F&O bhavcopy in the {MAX_LOOKBACK_DAYS} days up to {want.isoformat()}", "unavailable", "nse_filings")


def _payload(c: Container, rows: list[dv.FuturesRow], n: int) -> dict[str, Any]:
    stocks = [r for r in rows if r.kind == "stock"]
    return {"underlyings": len(stocks),
            "price_gainers": dv.rank(stocks, "price_change_pct", n), "price_losers": dv.rank(stocks, "price_change_pct", n, reverse=False),
            "oi_gainers": dv.rank(stocks, "oi_change_pct", n), "oi_losers": dv.rank(stocks, "oi_change_pct", n, reverse=False),
            "buildups": dv.buildup_lists(stocks, n), "buildup_counts": {b: sum(1 for r in stocks if r.buildup == b) for b in dv.BUILDUPS},
            "buildup_labels": dv.BUILDUP_LABEL,
            "index_futures": [r.to_dict() for r in sorted((r for r in rows if r.kind == "index"), key=lambda r: -r.open_interest)],
            "method": (dv.__doc__ or "").strip()}


async def live_from_kite(c: Container, prev_day: date, rows: list[dict[str, str]]) -> tuple[list[dv.FuturesRow], dict[str, Any]]:
    """Today's futures from Zerodha Kite quotes against the previous day's NSE F&O bhavcopy."""
    today = c.clock.now().astimezone(IST).date().isoformat()
    names = sorted({r["FinInstrmNm"] for r in rows if r.get("FinInstrmTp") in dv.FUTURES_TYPES and r.get("FinInstrmNm")
                    and (r.get("XpryDt") or "") >= today})
    got, state = await kite_fallback.quotes(c, [f"NFO:{x}" for x in names])
    if not got:
        return [], state
    live = dv.live_futures(rows, got, today)
    stamp = max((q["as_of"] for q in got.values() if q.get("as_of")), default=None)
    return live, {"state": "ok", "quoted": len(got), "contracts": len(names), "as_of": stamp}


async def oi_dashboard(c: Container, day: date | None = None, n: int = 15) -> dict[str, Any]:
    """Latest view: when NSE has not yet published today's F&O file, today's figures come live from the Zerodha Kite
    MCP (current OI and price against the last published file); otherwise, or when Kite cannot answer, the latest
    published end-of-day file. A past `day` always uses that day's file."""
    if c.settings.is_demo:
        return {"state": "not_configured", "reason": "F&O data is real NSE data and is not shown in demo mode."}
    try:
        d, rows = await latest_fo(c, day)
    except ProviderUnavailable as e:
        return {"state": e.state, "reason": str(e)[:300]}
    today = c.clock.now().astimezone(IST).date()
    requested = day or today
    live_state: dict[str, Any] | None = None
    if requested == today and d < today and today.weekday() < 5:
        live, live_state = await live_from_kite(c, d, rows)
        if live:
            return {"state": "ok", "mode": "live", "date": today.isoformat(), "requested": requested.isoformat(), "previous_file": d.isoformat(),
                    "as_of": live_state.get("as_of"), "live_state": live_state,
                    "source": f"Zerodha Kite MCP live quotes (OI and price) vs NSE F&O bhavcopy of {d.isoformat()}",
                    **_payload(c, live, n)}
    return {"state": "ok", "mode": "end_of_day", "date": d.isoformat(), "requested": requested.isoformat(),
            "source": "NSE F&O bhavcopy (end of day)", "live_state": live_state, **_payload(c, dv.aggregate_futures(rows), n)}


# ------------------------------------------------------------------ Nifty 50 ticker (Overview)
TICKER_CACHE_SECONDS = 30


async def nifty50_ticker(c: Container) -> dict[str, Any]:
    """Every Nifty 50 constituent with its latest price and change against the previous close, plus the index.
    Constituents come from NSE's published list. Prices: NSE's live movers for the Nifty segment (one call, both
    directions), then one batched Zerodha Kite quote for any constituent NSE did not return, then NSE's bulk quote
    (latest bhavcopy close) for whatever Kite could not price, e.g. when the Kite session has expired. A stock no
    source can price is left out of the ticker (and counted in `unpriced`); nothing is substituted or shown blank."""
    if c.settings.is_demo:
        return {"state": "not_configured", "reason": "Live Nifty 50 prices are not shown in demo mode.", "items": []}
    if (hit := await c.cache.get("ticker:nifty50")) is not None:
        return {**hit, "cache": "hit"}
    try:
        symbols, _ = await _segment_symbols(c, "nifty")
    except ProviderUnavailable as e:
        return {"state": "unavailable", "reason": f"Nifty 50 constituents unavailable: {str(e)[:200]}", "items": []}
    want = set(symbols)
    prices: dict[str, dict[str, Any]] = {}
    sources: dict[str, Any] = {}
    try:
        res = await asyncio.wait_for(c.nse.live_movers(50, "NIFTY"), LIVE_TIMEOUT_SECONDS)
        d = res["data"] if isinstance(res.get("data"), dict) else {}
        stamp = d.get("gainersUpdatedAt") or d.get("losersUpdatedAt")
        for r in _movers((d.get("gainers") or []) + (d.get("losers") or [])):
            if r["symbol"] in want and r["ltp"] is not None:
                prices[r["symbol"]] = {"last_price": r["ltp"], "prev_close": r["prev_close"], "change_pct": r["change_pct"], "as_of": stamp,
                                       "source": "NSE CM market MCP"}
        sources["nse"] = {"state": "delayed", "priced": len(prices)}
    except (ProviderUnavailable, TimeoutError) as e:
        sources["nse"] = {"state": getattr(e, "state", "unavailable"), "reason": str(e)[:160] or "timeout"}
    missing = [x for x in symbols if x not in prices]
    if missing:
        got, st = await kite_fallback.equity_quotes(c, missing)
        for sym, q in got.items():
            prices[sym] = {"last_price": q["last_price"], "prev_close": q["prev_close"], "change_pct": q["change_pct"], "as_of": q["as_of"],
                           "source": kite_fallback.SOURCE}
        sources["kite"] = {**st, "priced": len(got)}
    if missing := [x for x in symbols if x not in prices]:
        try:
            got = await asyncio.wait_for(c.nse.bulk_quotes(missing), LIVE_TIMEOUT_SECONDS)
            n = 0
            for sym, q in got.items():
                if sym not in want or q.get("last_price") is None:
                    continue
                prev, chg = q.get("prev_close"), q.get("change_pct")
                if chg is None and prev:
                    chg = round((q["last_price"] / prev - 1) * 100, 2)
                prices[sym] = {"last_price": q["last_price"], "prev_close": prev, "change_pct": chg, "as_of": q.get("as_of"),
                               "source": "NSE bhavcopy MCP (latest close)"}
                n += 1
            sources["nse_close"] = {"state": "delayed", "priced": n}
        except (ProviderUnavailable, TimeoutError) as e:
            sources["nse_close"] = {"state": getattr(e, "state", "unavailable"), "reason": str(e)[:160] or "timeout"}
    from app.services import macro  # local import: macro imports this module's sibling services

    index = await macro._nse_index(c, "NIFTY 50", "Nifty 50")
    items = [{"symbol": sym, **prices[sym]} for sym in sorted(symbols) if sym in prices]
    priced = len(items)
    out = {"state": "ok" if priced == len(symbols) else ("degraded" if priced else "unavailable"), "items": items, "index": index,
           "priced": priced, "total": len(symbols), "unpriced": sorted(x for x in symbols if x not in prices), "sources": sources,
           "retrieved_at": c.clock.now().isoformat()}
    if not priced:
        out["reason"] = "No source could price the Nifty 50 constituents right now."
    if priced:  # an outage is never cached
        await c.cache.set("ticker:nifty50", out, TICKER_CACHE_SECONDS)
    return out
