"""Deterministic risk metrics from adjusted daily closes. Annualisation uses 252 trading days."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.domain.result import MetricValue, Status, bad, ok

VERSION = "risk-v1"
TRADING_DAYS = 252


def _log_returns(close: pd.Series, window: int) -> np.ndarray | None:
    c = close.dropna().to_numpy(dtype=float)
    if len(c) < window + 1 or (c <= 0).any():
        return None
    return np.diff(np.log(c))[-window:]


def _need(name: str, close: pd.Series, window: int, unit: str, formula: str) -> MetricValue:
    return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(close.dropna())} bar(s) available, {window + 1} required", unit, formula)


def realized_volatility(close: pd.Series, window: int = TRADING_DAYS, name: str | None = None) -> MetricValue:
    name = name or f"volatility_{window}d"
    formula = f"sample std of the last {window} daily log returns * sqrt(252) * 100"
    r = _log_returns(close, window)
    if r is None:
        return _need(name, close, window, "pct", formula)
    return ok(name, float(r.std(ddof=1) * np.sqrt(TRADING_DAYS) * 100), "pct", formula, {"window": window})


def downside_volatility(close: pd.Series, window: int = TRADING_DAYS) -> MetricValue:
    name = f"downside_volatility_{window}d"
    formula = f"sqrt(mean(min(r, 0)^2)) over the last {window} daily log returns * sqrt(252) * 100"
    r = _log_returns(close, window)
    if r is None:
        return _need(name, close, window, "pct", formula)
    return ok(name, float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)) * np.sqrt(TRADING_DAYS) * 100), "pct", formula, {"window": window})


def max_drawdown(close: pd.Series, window: int | None = None, name: str = "max_drawdown") -> MetricValue:
    formula = "min over time of (close / running peak - 1) * 100"
    c = close.dropna()
    if window:
        c = c.iloc[-window:]
    if len(c) < 2:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(c)} bar(s) available, 2 required", "pct", formula)
    dd = c / c.cummax() - 1
    return ok(name, float(dd.min() * 100), "pct", formula, {"bars": len(c)})


def _paired(close: pd.Series, benchmark: pd.Series, window: int) -> tuple[np.ndarray, np.ndarray] | None:
    df = pd.concat([close, benchmark], axis=1, join="inner").dropna()
    if len(df) < window + 1:
        return None
    r = df.pct_change().dropna().iloc[-window:]
    return r.iloc[:, 0].to_numpy(), r.iloc[:, 1].to_numpy()


def beta(close: pd.Series, benchmark: pd.Series, window: int = TRADING_DAYS) -> MetricValue:
    name, formula = "beta", f"cov(instrument, benchmark) / var(benchmark) on the last {window} daily returns"
    pr = _paired(close, benchmark, window)
    if pr is None:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"fewer than {window + 1} overlapping bars with the benchmark", "x", formula)
    a, b = pr
    var = b.var(ddof=1)
    if var < 1e-14:
        return bad(name, Status.NOT_MEANINGFUL, "benchmark returns have no variance", "x", formula)
    return ok(name, float(np.cov(a, b, ddof=1)[0, 1] / var), "x", formula, {"window": window})


def correlation(close: pd.Series, benchmark: pd.Series, window: int = TRADING_DAYS) -> MetricValue:
    name, formula = "correlation", f"Pearson correlation of the last {window} daily returns with the benchmark"
    pr = _paired(close, benchmark, window)
    if pr is None:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"fewer than {window + 1} overlapping bars with the benchmark", "ratio", formula)
    a, b = pr
    if a.std() < 1e-12 or b.std() < 1e-12:
        return bad(name, Status.NOT_MEANINGFUL, "a return series has no variance", "ratio", formula)
    return ok(name, float(np.corrcoef(a, b)[0, 1]), "ratio", formula, {"window": window})


def average_traded_value(df: pd.DataFrame, window: int = 20) -> MetricValue:
    name, formula = "avg_traded_value", f"mean of close * volume over the last {window} bars"
    if len(df) < window:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(df)} bar(s) available, {window} required", "currency", formula)
    w = df.iloc[-window:]
    tv = w["traded_value"] if "traded_value" in w and w["traded_value"].notna().all() else w["close"] * w["volume"]
    return ok(name, float(tv.mean()), "currency", formula, {"window": window})


def gap_risk(df: pd.DataFrame, window: int = TRADING_DAYS) -> MetricValue:
    name, formula = "max_gap_pct", f"largest absolute open-vs-previous-close gap in the last {window} bars, percent"
    if len(df) < 21:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(df)} bar(s) available, 21 required", "pct", formula)
    w = df.iloc[-(window + 1):]
    gaps = (w["open"] / w["close"].shift(1) - 1).dropna().abs()
    return ok(name, float(gaps.max() * 100), "pct", formula, {"bars": len(gaps)})


@dataclass(frozen=True)
class RiskLabel:
    label: str
    criteria: str
    drivers: dict[str, str]


_BUCKETS = ("Low", "Moderate", "High", "Very High")
RISK_CRITERIA = (
    "Worst bucket across three measures. 1Y volatility: <20% Low, <35% Moderate, <55% High, else Very High. "
    "Max drawdown: shallower than -20% Low, -35% Moderate, -55% High, else Very High. "
    "Beta: <0.9 Low, <1.3 Moderate, <1.7 High, else Very High. Missing measures are skipped."
)


def _bucket(v: float, cuts: tuple[float, float, float]) -> int:
    return sum(1 for c in cuts if v >= c)


def risk_label(vol_pct: float | None, max_dd_pct: float | None, beta: float | None) -> RiskLabel:
    drivers: dict[str, int] = {}
    if vol_pct is not None:
        drivers["volatility"] = _bucket(vol_pct, (20, 35, 55))
    if max_dd_pct is not None:
        drivers["max_drawdown"] = _bucket(-max_dd_pct, (20, 35, 55))
    if beta is not None:
        drivers["beta"] = _bucket(beta, (0.9, 1.3, 1.7))
    if not drivers:
        return RiskLabel("Unknown", RISK_CRITERIA, {})
    return RiskLabel(_BUCKETS[max(drivers.values())], RISK_CRITERIA, {k: _BUCKETS[v] for k, v in drivers.items()})


def compute_risk(df: pd.DataFrame, benchmark: pd.Series | None = None) -> dict[str, MetricValue]:
    close = df["close"]
    out = {m.name: m for m in (
        realized_volatility(close, 252), realized_volatility(close, 63), downside_volatility(close, 252),
        max_drawdown(close, 252, "max_drawdown_1y"), max_drawdown(close, None, "max_drawdown_all"),
        average_traded_value(df, 20), gap_risk(df),
    )}
    if benchmark is not None and len(benchmark):
        out["beta"], out["correlation"] = beta(close, benchmark), correlation(close, benchmark)
    else:
        for n, u in (("beta", "x"), ("correlation", "ratio")):
            out[n] = bad(n, Status.MISSING, "benchmark history unavailable", u, "vs benchmark daily returns")
    return out
