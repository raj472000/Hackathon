"""Correlating values from several providers.

Policy `reconcile-v1`
- Precedence is per field. Default order: NSE MCP, then Kite MCP.
- The first provider in precedence with a value supplies it. A lower-precedence provider only
  fills a gap; it never overrides a higher-precedence value.
- When two providers both report a value and differ by more than the field tolerance, a conflict
  is recorded and returned so it can be stored and shown. The precedence winner is still used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.domain.provenance import DataPoint

VERSION = "reconcile-v1"
DEFAULT_PRECEDENCE = ["nse_mcp", "kite_mcp"]

# Relative tolerance in percent. End-of-day closes should agree almost exactly; live prices are
# sampled at different moments (NSE MCP is delayed by minutes), so they get a wider band.
_TOLERANCE = {"close": 0.1, "open": 0.1, "high": 0.1, "low": 0.1, "prev_close": 0.1, "last_price": 1.0,
              "volume": 5.0, "traded_value": 5.0}
_DEFAULT_TOLERANCE = 0.5
BAR_FIELDS = ("open", "high", "low", "close", "volume")


def tolerance_pct(field: str) -> float:
    return _TOLERANCE.get(field, _DEFAULT_TOLERANCE)


@dataclass(frozen=True)
class Conflict:
    field: str
    values: dict[str, float]
    diff_pct: float
    chosen: str
    tolerance_pct: float


@dataclass(frozen=True)
class Reconciled:
    field: str
    value: float | None
    provider: str | None
    status: str  # ok | conflict | missing
    point: DataPoint | None = None
    conflict: Conflict | None = None
    filled_from_fallback: bool = False
    reason: str | None = None


def _order(field: str, precedence: Mapping[str, Sequence[str]] | None) -> list[str]:
    return list((precedence or {}).get(field) or DEFAULT_PRECEDENCE)


def _diff_pct(a: float, b: float) -> float:
    base = max(abs(a), abs(b))
    return 0.0 if base == 0 else abs(a - b) / abs(a) * 100 if a != 0 else 100.0


def reconcile(field: str, points: Sequence[DataPoint], precedence: Mapping[str, Sequence[str]] | None = None) -> Reconciled:
    order = _order(field, precedence)
    rank = {p: i for i, p in enumerate(order)}
    ordered = sorted(points, key=lambda p: rank.get(p.provider, len(order)))
    have = [p for p in ordered if p.value is not None and p.value == p.value]
    if not have:
        why = "; ".join(f"{p.provider}: {p.reason or p.status}" for p in ordered) or "no provider returned this field"
        return Reconciled(field, None, None, "missing", reason=why)
    chosen = have[0]
    fallback = bool(ordered) and ordered[0].provider != chosen.provider
    worst = max((_diff_pct(chosen.value, p.value) for p in have[1:]), default=0.0)  # type: ignore[arg-type]
    tol = tolerance_pct(field)
    if worst > tol:
        c = Conflict(field, {p.provider: float(p.value) for p in have}, round(worst, 4), chosen.provider, tol)  # type: ignore[arg-type]
        return Reconciled(field, chosen.value, chosen.provider, "conflict", chosen, c, fallback)
    return Reconciled(field, chosen.value, chosen.provider, "ok", chosen, None, fallback)


def reconcile_bars(
    frames: Mapping[str, pd.DataFrame | None], precedence: Sequence[str] | None = None
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, int]]:
    """Merge daily bars from several providers by date.

    Returns (merged frame with a `provider` column, conflicts, coverage). Each date takes the whole
    bar from the first provider in precedence that has a close for it, so one bar never mixes
    sources. Conflicts are reported per field for dates where two providers both have a value.
    """
    order = [p for p in (precedence or DEFAULT_PRECEDENCE) if frames.get(p) is not None and not frames[p].empty]  # type: ignore[union-attr]
    order += [p for p in frames if p not in order and frames[p] is not None and not frames[p].empty]  # type: ignore[union-attr]
    coverage: dict[str, int] = {p: 0 for p in frames}
    if not order:
        return pd.DataFrame(columns=[*BAR_FIELDS, "provider"]), [], {**coverage, "overlap": 0, "conflicts": 0}
    idx = sorted(set().union(*[frames[p].index for p in order]))  # type: ignore[union-attr]
    aligned = {p: frames[p].reindex(idx) for p in order}  # type: ignore[union-attr]
    rows, conflicts, overlap = [], [], 0
    for ts in idx:
        have = [p for p in order if pd.notna(aligned[p].at[ts, "close"])]
        if not have:
            continue
        chosen = have[0]
        row = aligned[chosen].loc[ts].to_dict()
        row["provider"] = chosen
        row["_ts"] = ts
        rows.append(row)
        coverage[chosen] += 1
        if len(have) > 1:
            overlap += 1
            for f in BAR_FIELDS:
                vals = {p: float(aligned[p].at[ts, f]) for p in have if f in aligned[p].columns and pd.notna(aligned[p].at[ts, f])}
                if len(vals) < 2 or chosen not in vals:
                    continue
                worst = max(_diff_pct(vals[chosen], v) for p, v in vals.items() if p != chosen)
                if worst > tolerance_pct(f):
                    conflicts.append({"date": pd.Timestamp(ts).date().isoformat(), "field": f, "values": vals,
                                      "diff_pct": round(worst, 4), "chosen": chosen, "tolerance_pct": tolerance_pct(f)})
    merged = pd.DataFrame(rows).set_index("_ts") if rows else pd.DataFrame(columns=[*BAR_FIELDS, "provider"])
    merged.index.name = None
    return merged, conflicts, {**coverage, "overlap": overlap, "conflicts": len(conflicts)}
