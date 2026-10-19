"""Relative Rotation Graph calculation.

Method `rrg-v1` (this project's own documented method; it does not reproduce any vendor's
proprietary RRG algorithm):

1. Resample sector and benchmark closes to weekly (last close of each week ending Friday).
2. RS(t)          = sector close / benchmark close.
3. RS-Ratio(t)    = 100 * RS(t) / SMA(RS, ratio_window)(t).
4. RS-Momentum(t) = 100 * RS-Ratio(t) / SMA(RS-Ratio, momentum_window)(t).

Both axes are centred on 100. Every value at time t uses data up to t only. Price series should
be consistently adjusted; total-return indices are preferable when available.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

VERSION = "rrg-v1"


class RRGError(ValueError):
    pass


@dataclass(frozen=True)
class RRGParams:
    ratio_window: int = 12  # weeks
    momentum_window: int = 5  # weeks
    trail: int = 10  # weekly observations plotted
    frequency: str = "W-FRI"


@dataclass(frozen=True)
class RRGPoint:
    date: str
    rs_ratio: float
    rs_momentum: float


@dataclass
class SectorRRG:
    name: str
    status: str  # ok | insufficient_history | missing
    quadrant: str | None = None
    trail: list[RRGPoint] = field(default_factory=list)
    reason: str | None = None
    last_bar: str | None = None
    returns: dict[str, float | None] = field(default_factory=dict)
    direction: str | None = None


@dataclass
class RRGResult:
    sectors: dict[str, SectorRRG]
    params: RRGParams
    benchmark_last_bar: str
    version: str = VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "params": asdict(self.params), "benchmark_last_bar": self.benchmark_last_bar,
                "sectors": {k: asdict(v) for k, v in self.sectors.items()}}


def quadrant(rs_ratio: float, rs_momentum: float) -> str:
    if rs_ratio >= 100:
        return "Leading" if rs_momentum >= 100 else "Weakening"
    return "Improving" if rs_momentum >= 100 else "Lagging"


def _direction(trail: list[RRGPoint]) -> str | None:
    if len(trail) < 2:
        return None
    dx, dy = trail[-1].rs_ratio - trail[-2].rs_ratio, trail[-1].rs_momentum - trail[-2].rs_momentum
    ns = "north" if dy > 0 else "south" if dy < 0 else ""
    ew = "east" if dx > 0 else "west" if dx < 0 else ""
    return (ns + "-" + ew).strip("-") or "flat"


def _ret(s: pd.Series, bars: int) -> float | None:
    s = s.dropna()
    if len(s) < bars + 1 or s.iloc[-(bars + 1)] <= 0:
        return None
    return float((s.iloc[-1] / s.iloc[-(bars + 1)] - 1) * 100)


def compute_rrg(sectors: Mapping[str, pd.Series], benchmark: pd.Series, params: RRGParams | None = None) -> RRGResult:
    params = params or RRGParams()
    bench = benchmark.dropna()
    if bench.empty:
        raise RRGError("benchmark history is empty; RRG cannot be computed")
    bench_w = bench.resample(params.frequency).last().dropna()
    need = params.ratio_window + params.momentum_window
    out: dict[str, SectorRRG] = {}
    for name in sorted(sectors):
        s = sectors[name].dropna() if sectors[name] is not None else pd.Series(dtype=float)
        if s.empty:
            out[name] = SectorRRG(name, "missing", reason="no price history for this sector")
            continue
        sw = s.resample(params.frequency).last().dropna()
        df = pd.concat([sw.rename("s"), bench_w.rename("b")], axis=1, join="inner").dropna()
        rets = {"1M": _ret(s, 21), "3M": _ret(s, 63), "6M": _ret(s, 126), "12M": _ret(s, 252)}
        if len(df) < need:
            out[name] = SectorRRG(name, "insufficient_history", last_bar=s.index[-1].date().isoformat(), returns=rets,
                                  reason=f"{len(df)} overlapping weekly observation(s), {need} required")
            continue
        rs = df["s"] / df["b"]
        ratio = 100 * rs / rs.rolling(params.ratio_window).mean()
        mom = 100 * ratio / ratio.rolling(params.momentum_window).mean()
        pts = pd.concat([ratio.rename("r"), mom.rename("m")], axis=1).dropna().iloc[-params.trail:]
        trail = [RRGPoint(ts.date().isoformat(), float(r.r), float(r.m)) for ts, r in pts.iterrows()]
        out[name] = SectorRRG(name, "ok", quadrant(trail[-1].rs_ratio, trail[-1].rs_momentum), trail,
                              last_bar=s.index[-1].date().isoformat(), returns=rets, direction=_direction(trail))
    return RRGResult(out, params, bench.index[-1].date().isoformat())


def equal_weight_composite(members: Mapping[str, pd.Series], min_members: int = 3) -> pd.Series | None:
    """Equal-weight daily-rebalanced index from constituent closes, base 100.

    Used only when a sector index series is unavailable; the result is labelled a composite. Each
    day's return is the mean return of members with a price on that day and the day before.
    """
    if len(members) < min_members:
        return None
    px = pd.concat({k: v for k, v in members.items()}, axis=1).sort_index()
    rets = px.pct_change(fill_method=None)
    enough = rets.notna().sum(axis=1) >= min_members
    mean = rets.mean(axis=1).where(enough)
    mean = mean.loc[mean.first_valid_index():] if mean.first_valid_index() is not None else mean
    if mean.dropna().empty:
        return None
    return (100 * (1 + mean.fillna(0)).cumprod()).rename("composite")
