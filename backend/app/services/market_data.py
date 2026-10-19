"""Correlated market data: NSE MCP first, broker sources fill gaps, disagreements are flagged.

Every provider is asked for what it supports. Each provider's outcome is kept as a `SourceStatus`
so the caller can persist lineage and the UI can show exactly which source supplied, lacked or
failed each dataset. A provider failure never becomes a value.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from app.connectors.base import BarsResult, ProviderUnavailable, QuoteResult
from app.core.cache import Coalescer
from app.domain import adjust, reconcile
from app.domain.adjust import CorporateAction
from app.domain.provenance import DataPoint


@dataclass
class SourceStatus:
    provider: str
    dataset: str
    status: str  # ok | unavailable | not_supported | login_required | not_configured
    reason: str | None = None
    rows: int | None = None
    source_id: str | None = None
    adjustment: str | None = None
    last_bar: str | None = None


@dataclass
class HistoryBundle:
    symbol: str
    frame: pd.DataFrame
    conflicts: list[dict[str, Any]]
    coverage: dict[str, int]
    sources: list[SourceStatus]
    actions: list[CorporateAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_demo: bool = False


class DataUnavailable(Exception):
    def __init__(self, message: str, sources: list[SourceStatus]) -> None:
        super().__init__(message)
        self.sources = sources


class CorrelatedMarketData:
    def __init__(self, nse: Any, kite: Any, cache: Any, quote_ttl: int = 60,
                 precedence: dict[str, list[str]] | None = None) -> None:
        self.nse, self.kite, self.cache = nse, kite, cache
        self.quote_ttl = quote_ttl
        self.precedence = precedence
        self._coalesce = Coalescer()
        self._pending: set[asyncio.Future[Any]] = set()

    def _brokers(self) -> list[Any]:
        return [self.kite] if self.kite is not None else []

    @staticmethod
    def _status(provider: str, dataset: str, exc: BaseException) -> SourceStatus:
        if isinstance(exc, ProviderUnavailable):
            return SourceStatus(provider, dataset, exc.state, str(exc)[:400])
        return SourceStatus(provider, dataset, "unavailable", f"{type(exc).__name__}: {str(exc)[:300]}")

    # ------------------------------------------------------------------ history
    async def history(self, symbol: str, start: date, end: date, instrument_type: str = "EQ") -> HistoryBundle:
        return await self._coalesce.run(f"hist:{symbol}:{start}:{end}", lambda: self._history(symbol, start, end, instrument_type))

    async def _history(self, symbol: str, start: date, end: date, instrument_type: str) -> HistoryBundle:
        sources: list[SourceStatus] = []
        frames: dict[str, pd.DataFrame | None] = {}
        warnings: list[str] = []
        actions: list[CorporateAction] = []
        is_demo = False
        providers: list[Any] = []
        cap = "index_history" if instrument_type == "INDEX" else "history"
        if self.nse is not None:
            c = self.nse.capabilities().get(cap, {})
            # Skip NSE for an index only when its servers are up and advertise no index-history tool.
            # When they are unreachable, try anyway so the outcome reads "unavailable", not "not supported".
            if instrument_type == "INDEX" and not c.get("supported") and "no tool advertised" in (c.get("reason") or ""):
                sources.append(SourceStatus("nse_mcp", "history", "not_supported", c.get("reason") or "index history is not provided"))
            else:
                providers.append(self.nse)
        providers += self._brokers()

        async def one(p: Any) -> BarsResult | BaseException:
            try:
                if p is self.nse:
                    return await p.get_history(symbol, start, end, instrument_type)
                return await p.get_history(symbol, start, end)
            except Exception as e:
                return e

        async def acts() -> list[CorporateAction] | BaseException:
            if self.nse is None or self.nse not in providers or instrument_type == "INDEX":
                return []
            try:
                return await self.nse.get_corporate_actions(symbol)
            except Exception as e:
                return e

        results, act_res = await asyncio.gather(asyncio.gather(*(one(p) for p in providers)), acts())
        actions_known = not isinstance(act_res, BaseException)
        if isinstance(act_res, BaseException):
            st = self._status("nse_mcp", "corporate_actions", act_res)
            sources.append(st)
            warnings.append(f"Corporate actions unavailable ({st.reason}); NSE prices could not be split-adjusted.")
        else:
            actions = act_res
            if self.nse is not None and self.nse in providers and instrument_type != "INDEX":
                sources.append(SourceStatus("nse_mcp", "corporate_actions", "ok", rows=len(actions), source_id="get_corporate_actions"))

        for p, res in zip(providers, results, strict=True):
            if isinstance(res, BaseException):
                sources.append(self._status(p.name, "history", res))
                continue
            df, adj = res.frame, res.adjustment
            is_demo = is_demo or res.is_demo
            warnings += res.warnings
            if res.adjustment == "unadjusted":
                gaps = adjust.detect_unexplained_gaps(df, actions) if len(df) > 1 else []
                df = adjust.adjust_bars(df, actions)
                adj = "split_adjusted" if actions_known else "unadjusted"
                for g in gaps:
                    warnings.append(f"{p.name}: {g['change_pct']}% move on {g['date']} is not explained by a known split or bonus; "
                                    "returns across this date may be unreliable.")
            frames[p.name] = df
            sources.append(SourceStatus(p.name, "history", "ok", rows=len(df), source_id=res.source_id, adjustment=adj,
                                        last_bar=df.index[-1].date().isoformat() if len(df) else None))
        if not any(f is not None and not f.empty for f in frames.values()):
            why = "; ".join(f"{s.provider}: {s.status} ({s.reason})" for s in sources if s.dataset == "history") or "no provider configured"
            raise DataUnavailable(f"no price history for {symbol}: {why}", sources)
        order = (self.precedence or {}).get("history")
        merged, conflicts, coverage = reconcile.reconcile_bars(frames, order)
        return HistoryBundle(symbol.upper(), merged, conflicts, coverage, sources, actions, warnings, is_demo)

    # ------------------------------------------------------------------ quote
    async def quote(self, symbol: str, instrument_type: str = "EQ") -> dict[str, Any]:
        key = f"quote:{symbol.upper()}"
        if (hit := await self.cache.get(key)) is not None:
            return {**hit, "cache": "hit"}
        out = await self._coalesce.run(key, lambda: self._quote(symbol, instrument_type))
        if out["status"] != "unavailable":
            await self.cache.set(key, out, self.quote_ttl)  # errors are never cached
        return {**out, "cache": "miss"}

    async def quote_within(self, symbol: str, instrument_type: str = "EQ", deadline: float = 8.0) -> dict[str, Any]:
        """The quote, or a `pending` state when no source has answered within `deadline` seconds. The
        fetch keeps running in the background, so a later call is served from the cache."""
        task = asyncio.ensure_future(self.quote(symbol, instrument_type))
        try:
            return await asyncio.wait_for(asyncio.shield(task), deadline)
        except TimeoutError:
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
            return {"symbol": symbol.upper(), "status": "pending", "fields": {}, "conflicts": [], "sources": [], "is_demo": False,
                    "currency": "INR", "cache": "miss",
                    "reason": f"No quote source answered within {deadline:g}s; still checking in the background."}

    async def _quote(self, symbol: str, instrument_type: str) -> dict[str, Any]:
        sym = symbol.upper()
        sources: list[SourceStatus] = []
        points: dict[str, list[DataPoint]] = {}

        async def from_nse() -> QuoteResult | BaseException | None:
            if self.nse is None:
                return None
            if instrument_type == "INDEX" and not self.nse.capabilities().get("index_quote", {}).get("supported"):
                return None
            try:
                return await self.nse.get_quote(symbol if instrument_type == "INDEX" else sym, instrument_type)
            except Exception as e:
                return e

        async def from_broker(b: Any) -> QuoteResult | BaseException | None:
            try:
                return (await b.get_quotes([sym])).get(sym) or ProviderUnavailable(f"{sym} absent from the broker response", "unavailable")
            except Exception as e:
                return e

        brokers = self._brokers()
        res = await asyncio.gather(from_nse(), *(from_broker(b) for b in brokers))
        names = ["nse_mcp", *(b.name for b in brokers)]
        is_demo = False
        for name, r in zip(names, res, strict=True):
            if r is None:
                if name == "nse_mcp" and instrument_type == "INDEX":
                    sources.append(SourceStatus(name, "quote", "not_supported", "index quotes are taken from the broker source"))
                continue
            if isinstance(r, BaseException):
                sources.append(self._status(name, "quote", r))
                continue
            sources.append(SourceStatus(name, "quote", "ok", source_id=next(iter(r.fields.values())).source_id if r.fields else None))
            for f, dp in r.fields.items():
                points.setdefault(f, []).append(dp)
                is_demo = is_demo or dp.is_demo
        fields: dict[str, Any] = {}
        conflicts = []
        for f, pts in points.items():
            rec = reconcile.reconcile(f, pts, self.precedence)
            dp = rec.point  # type: ignore[assignment]
            fields[f] = {
                "value": rec.value, "provider": rec.provider, "status": (dp.status if dp and rec.status == "ok" else rec.status),
                "reason": (dp.reason if dp else rec.reason), "as_of": dp.as_of.isoformat() if dp and dp.as_of else None,
                "retrieved_at": dp.retrieved_at.isoformat() if dp else None, "currency": dp.currency if dp else None,
                "source_id": dp.source_id if dp else None, "filled_from_fallback": rec.filled_from_fallback,
                "conflict": asdict(rec.conflict) if rec.conflict else None,
            }
            if rec.conflict:
                conflicts.append(asdict(rec.conflict))
        ok = any(v["value"] is not None for v in fields.values())
        return {"symbol": sym, "status": "ok" if ok else "unavailable", "fields": fields, "conflicts": conflicts,
                "sources": [asdict(s) for s in sources], "is_demo": is_demo, "currency": "INR"}
