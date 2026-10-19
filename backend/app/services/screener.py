"""Screener over the latest metric snapshots: validated filters, allowlisted sort, pagination, CSV."""

from __future__ import annotations

import csv
import io
from typing import Any

from app.db.models import Instrument, MetricSnapshot
from app.domain.theme_dsl import METRIC_CATALOG, Filter, _passes

DEFAULT_COLUMNS = ["close", "overall_score", "fundamentals_score", "valuation_score", "momentum_score", "risk_score",
                   "context_score", "return_3m", "return_12m", "pe", "roe", "debt_equity", "volatility_252d", "completeness_pct"]
TEXT_SORTS = {"symbol", "name", "sector"}
MAX_PAGE_SIZE = 200


class ScreenerError(ValueError):
    pass


def validate_columns(cols: list[str] | None) -> list[str]:
    cols = cols or DEFAULT_COLUMNS
    bad = [c for c in cols if c not in METRIC_CATALOG]
    if bad:
        raise ScreenerError(f"unknown column(s): {', '.join(x[:40] for x in bad[:5])}")
    return list(dict.fromkeys(cols))[:40]


def run(rows: list[tuple[MetricSnapshot, Instrument]], *, filters: list[Filter], sectors: list[str] | None, search: str | None,
        sort: str, order: str, page: int, page_size: int, columns: list[str], hide_missing: bool = False) -> dict[str, Any]:
    if sort not in METRIC_CATALOG and sort not in TEXT_SORTS:
        raise ScreenerError(f"sort field '{sort[:40]}' is not sortable")
    if order not in ("asc", "desc"):
        raise ScreenerError("order must be asc or desc")
    q = (search or "").strip().lower()
    matched: list[tuple[MetricSnapshot, Instrument]] = []
    dropped_missing = 0
    for m, i in rows:
        if sectors and i.sector not in sectors:
            continue
        if q and q not in i.symbol.lower() and q not in (i.name or "").lower():
            continue
        ok, missing = True, False
        for f in filters:
            v = m.flat.get(f.metric)
            if v is None:
                missing = True
                break
            if not _passes(f, float(v)):
                ok = False
                break
        if missing:
            dropped_missing += 1  # a missing value never satisfies a filter and is never read as zero
            continue
        if ok:
            matched.append((m, i))

    def key(t: tuple[MetricSnapshot, Instrument]) -> Any:
        m, i = t
        if sort in TEXT_SORTS:
            return (0, (getattr(i, sort) or "").lower(), i.symbol)
        v = m.flat.get(sort)
        return (v is None, 0 if v is None else (-v if order == "desc" else v), i.symbol)

    matched.sort(key=key, reverse=(order == "desc" and sort in TEXT_SORTS))
    total = len(matched)
    page_rows = matched[(page - 1) * page_size: page * page_size]
    items = []
    for m, i in page_rows:
        values, missing_info = {}, {}
        for c in columns:
            v = m.flat.get(c)
            values[c] = v
            if v is None:
                d = m.detail.get(c) or {}
                missing_info[c] = {"status": d.get("status", "missing"), "reason": d.get("reason") or "not available"}
        sc = m.score
        items.append({"instrument_id": i.id, "symbol": i.symbol, "name": i.name, "exchange": i.exchange, "sector": i.sector,
                      "currency": i.currency, "values": values, "missing": missing_info, "stance": sc.get("stance"),
                      "risk_label": (sc.get("risk") or {}).get("label"), "confidence": sc.get("confidence"),
                      "score_complete": sc.get("complete"), "partial_score": sc.get("partial_overall"),
                      "entry_state": (sc.get("entry_setup") or {}).get("state"), "freshness": sc.get("freshness"),
                      "last_bar": sc.get("last_bar"), "is_demo": m.is_demo})
    as_of = max((m.as_of for m, _ in rows), default=None)
    computed = max((m.computed_at for m, _ in rows), default=None)
    return {"items": items, "page": page, "page_size": page_size, "total": total, "universe": len(rows),
            "excluded_for_missing_filter_metric": dropped_missing, "sort": sort, "order": order,
            "columns": [{"key": c, **{k: METRIC_CATALOG[c][k] for k in ("label", "unit", "group")}} for c in columns],
            "as_of": as_of.isoformat() if as_of else None, "computed_at": computed.isoformat() if computed else None,
            "calculation_version": rows[0][0].versions if rows else None, "exchange": "NSE", "currency": "INR"}


def to_csv(result: dict[str, Any], source_note: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# Screener export · exchange {result['exchange']} · currency {result['currency']} · metrics as of {result['as_of']} · "
                f"computed {result['computed_at']} · {source_note}"])
    w.writerow(["# Empty cells mean the value is unavailable, not zero. Research output, not investment advice."])
    cols = [c["key"] for c in result["columns"]]
    w.writerow(["symbol", "name", "sector", "stance", "risk_label", "confidence", "last_bar",
                *[f"{c['label']} ({c['unit']})" for c in result["columns"]]])
    for it in result["items"]:
        w.writerow([_safe(it["symbol"]), _safe(it["name"]), _safe(it["sector"]), it["stance"], it["risk_label"], it["confidence"], it["last_bar"],
                    *["" if it["values"][c] is None else round(it["values"][c], 4) for c in cols]])
    return buf.getvalue()


def _safe(v: Any) -> str:
    """Neutralise spreadsheet formula injection in text cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s
