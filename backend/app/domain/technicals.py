"""Deterministic price-action indicators computed from adjusted daily bars.

Input frames are indexed by date ascending with columns open, high, low, close, volume.
Windows are counted in trading bars. No function reads beyond the last row it is given, and
`compute_technicals(as_of=...)` truncates first, so historical scores cannot look ahead.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.domain.result import MetricValue, Status, bad, ok

VERSION = "technicals-v1"

PERIOD_BARS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252, "3Y": 756, "5Y": 1260}


def _insufficient(name: str, have: int, need: int, unit: str, formula: str) -> MetricValue:
    return bad(name, Status.INSUFFICIENT_HISTORY, f"{have} bar(s) available, {need} required", unit, formula)


def period_return(close: pd.Series, period: str, name: str | None = None) -> MetricValue:
    bars = PERIOD_BARS[period]
    name = name or f"return_{period.lower()}"
    formula = f"(close[t] / close[t-{bars}] - 1) * 100"
    c = close.dropna()
    if len(c) < bars + 1:
        return _insufficient(name, len(c), bars + 1, "pct", formula)
    start, end = float(c.iloc[-(bars + 1)]), float(c.iloc[-1])
    if start <= 0:
        return bad(name, Status.NOT_MEANINGFUL, "starting price is non-positive", "pct", formula)
    return ok(name, (end / start - 1) * 100, "pct", formula, {"start": start, "end": end, "bars": bars})


def sma(close: pd.Series, window: int, name: str | None = None) -> MetricValue:
    name = name or f"sma_{window}"
    formula = f"mean of the last {window} closes"
    c = close.dropna()
    if len(c) < window:
        return _insufficient(name, len(c), window, "price", formula)
    return ok(name, float(c.iloc[-window:].mean()), "price", formula, {"window": window})


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing seeded with the simple mean of the first `period` values."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: pd.Series, period: int = 14) -> MetricValue:
    name, formula = f"rsi_{period}", f"100 - 100 / (1 + Wilder avg gain / Wilder avg loss), period {period}"
    c = close.dropna().to_numpy(dtype=float)
    if len(c) < period + 1:
        return _insufficient(name, len(c), period + 1, "score", formula)
    delta = np.diff(c)
    gain = _wilder(np.where(delta > 0, delta, 0.0), period)[-1]
    loss = _wilder(np.where(delta < 0, -delta, 0.0), period)[-1]
    if gain == 0 and loss == 0:
        return bad(name, Status.NOT_MEANINGFUL, "price did not change over the window", "score", formula)
    value = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return ok(name, value, "score", formula, {"avg_gain": float(gain), "avg_loss": float(loss)})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, MetricValue]:
    formula = f"EMA{fast} - EMA{slow}; signal = EMA{signal} of MACD"
    c = close.dropna()
    need = slow + signal
    if len(c) < need:
        return {n: _insufficient(n, len(c), need, "price", formula) for n in ("macd", "macd_signal", "macd_hist")}
    line = c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    m, s = float(line.iloc[-1]), float(sig.iloc[-1])
    return {
        "macd": ok("macd", m, "price", formula, {"fast": fast, "slow": slow}),
        "macd_signal": ok("macd_signal", s, "price", formula, {"signal": signal}),
        "macd_hist": ok("macd_hist", m - s, "price", "MACD - signal", {"macd": m, "signal": s}),
    }


def _true_range(df: pd.DataFrame) -> np.ndarray:
    h, low, c = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    prev = np.concatenate([[c[0]], c[:-1]])
    return np.maximum.reduce([h - low, np.abs(h - prev), np.abs(low - prev)])


def atr(df: pd.DataFrame, period: int = 14) -> MetricValue:
    name, formula = f"atr_{period}", f"Wilder average of true range, period {period}"
    if len(df) < period + 1:
        return _insufficient(name, len(df), period + 1, "price", formula)
    return ok(name, float(_wilder(_true_range(df)[1:], period)[-1]), "price", formula, {"period": period})


def bollinger(close: pd.Series, window: int = 20, k: float = 2.0) -> dict[str, MetricValue]:
    formula = f"SMA{window} +/- {k:g} * population std of the last {window} closes"
    c = close.dropna()
    if len(c) < window:
        return {n: _insufficient(n, len(c), window, "price", formula) for n in ("bb_mid", "bb_upper", "bb_lower", "bb_pct_b")}
    tail = c.iloc[-window:]
    mid, sd, last = float(tail.mean()), float(tail.std(ddof=0)), float(c.iloc[-1])
    up, lo = mid + k * sd, mid - k * sd
    out = {
        "bb_mid": ok("bb_mid", mid, "price", formula, {"window": window}),
        "bb_upper": ok("bb_upper", up, "price", formula, {"std": sd}),
        "bb_lower": ok("bb_lower", lo, "price", formula, {"std": sd}),
    }
    out["bb_pct_b"] = (
        ok("bb_pct_b", (last - lo) / (up - lo) * 100, "pct", "(close - lower) / (upper - lower) * 100", {"close": last})
        if up > lo
        else bad("bb_pct_b", Status.NOT_MEANINGFUL, "bands have zero width", "pct", "(close - lower) / (upper - lower) * 100")
    )
    return out


def adx(df: pd.DataFrame, period: int = 14) -> MetricValue:
    name, formula = f"adx_{period}", f"Wilder ADX, period {period}"
    need = 2 * period + 1
    if len(df) < need:
        return _insufficient(name, len(df), need, "score", formula)
    h, low = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    up, dn = np.diff(h), -np.diff(low)
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = _wilder(_true_range(df)[1:], period)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * _wilder(plus_dm, period) / tr
        mdi = 100 * _wilder(minus_dm, period) / tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = dx[~np.isnan(dx)]
    if len(dx) < period:
        return bad(name, Status.NOT_MEANINGFUL, "no directional movement in the window", "score", formula)
    return ok(name, float(_wilder(dx, period)[-1]), "score", formula, {"period": period})


def range_52w(df: pd.DataFrame) -> dict[str, MetricValue]:
    names = ("high_52w", "low_52w", "dist_from_52w_high_pct", "dist_from_52w_low_pct")
    if len(df) < 200:
        return {n: _insufficient(n, len(df), 200, "price", "extremes of the last 252 bars") for n in names}
    w = df.iloc[-252:]
    hi, lo, last = float(w["high"].max()), float(w["low"].min()), float(df["close"].iloc[-1])
    return {
        "high_52w": ok("high_52w", hi, "price", "max high of the last 252 bars", {"bars": len(w)}),
        "low_52w": ok("low_52w", lo, "price", "min low of the last 252 bars", {"bars": len(w)}),
        "dist_from_52w_high_pct": ok("dist_from_52w_high_pct", (last / hi - 1) * 100, "pct", "(close / 52w high - 1) * 100", {"close": last, "high": hi}),
        "dist_from_52w_low_pct": ok("dist_from_52w_low_pct", (last / lo - 1) * 100, "pct", "(close / 52w low - 1) * 100", {"close": last, "low": lo}),
    }


def breakout_status(df: pd.DataFrame, lookback: int = 55) -> str:
    """'breakout' / 'breakdown' when the last close is beyond the prior `lookback`-bar range, else 'none'.
    Returns 'insufficient_history' when there are too few bars."""
    if len(df) < lookback + 1:
        return "insufficient_history"
    prior, last = df.iloc[-(lookback + 1):-1], float(df["close"].iloc[-1])
    if last > float(prior["high"].max()):
        return "breakout"
    if last < float(prior["low"].min()):
        return "breakdown"
    return "none"


def relative_volume(df: pd.DataFrame, window: int = 20) -> MetricValue:
    name, formula = "relative_volume", f"last volume / mean volume of the prior {window} bars"
    if len(df) < window + 1:
        return _insufficient(name, len(df), window + 1, "x", formula)
    base, last = float(df["volume"].iloc[-(window + 1):-1].mean()), float(df["volume"].iloc[-1])
    if base <= 0:
        return bad(name, Status.NOT_MEANINGFUL, "average volume is zero", "x", formula)
    return ok(name, last / base, "x", formula, {"last_volume": last, "avg_volume": base})


def volume_trend(df: pd.DataFrame, short: int = 20, long: int = 60) -> MetricValue:
    name, formula = "volume_trend", f"mean volume({short}) / mean volume({long})"
    if len(df) < long:
        return _insufficient(name, len(df), long, "x", formula)
    s, lg = float(df["volume"].iloc[-short:].mean()), float(df["volume"].iloc[-long:].mean())
    if lg <= 0:
        return bad(name, Status.NOT_MEANINGFUL, "average volume is zero", "x", formula)
    return ok(name, s / lg, "x", formula, {"short_avg": s, "long_avg": lg})


def ma_alignment(close: pd.Series) -> MetricValue:
    """Share of the four trend conditions that hold: close>SMA20, SMA20>SMA50, SMA50>SMA100, SMA100>SMA200."""
    name, formula = "ma_alignment", "percent of [close>SMA20, SMA20>SMA50, SMA50>SMA100, SMA100>SMA200] that hold"
    c = close.dropna()
    if len(c) < 200:
        return _insufficient(name, len(c), 200, "score", formula)
    v = [float(c.iloc[-1])] + [float(c.iloc[-w:].mean()) for w in (20, 50, 100, 200)]
    hits = sum(1 for a, b in zip(v, v[1:], strict=False) if a > b)
    return ok(name, hits / 4 * 100, "score", formula, dict(zip(("close", "sma20", "sma50", "sma100", "sma200"), v, strict=True)))


def relative_strength(close: pd.Series, benchmark: pd.Series, period: str, name: str | None = None) -> MetricValue:
    name = name or f"rel_strength_{period.lower()}"
    formula = f"((1 + instrument return) / (1 + benchmark return) - 1) * 100 over {period}"
    a, b = period_return(close, period), period_return(benchmark, period)
    if a.value is None or b.value is None:
        src = a if a.value is None else b
        return bad(name, src.status, f"{'instrument' if a.value is None else 'benchmark'}: {src.reason}", "pct", formula)
    return ok(name, ((1 + a.value / 100) / (1 + b.value / 100) - 1) * 100, "pct", formula,
              {"instrument_return": a.value, "benchmark_return": b.value})


def momentum_acceleration(close: pd.Series) -> MetricValue:
    """Latest 3M return minus the 3M return one quarter earlier (percentage points)."""
    name, formula = "momentum_acceleration", "return(3M) now - return(3M) as of 63 bars ago"
    c = close.dropna()
    if len(c) < 127:
        return _insufficient(name, len(c), 127, "pct", formula)
    now = (float(c.iloc[-1]) / float(c.iloc[-64]) - 1) * 100
    then = (float(c.iloc[-64]) / float(c.iloc[-127]) - 1) * 100
    return ok(name, now - then, "pct", formula, {"recent_3m": now, "prior_3m": then})


def trend_persistence(close: pd.Series, window: int = 63) -> MetricValue:
    name, formula = "trend_persistence", f"percent of the last {window} bars that closed up"
    c = close.dropna()
    if len(c) < window + 1:
        return _insufficient(name, len(c), window + 1, "pct", formula)
    d = np.diff(c.iloc[-(window + 1):].to_numpy(dtype=float))
    return ok(name, float((d > 0).mean() * 100), "pct", formula, {"window": window})


def compute_technicals(df: pd.DataFrame, benchmark: pd.Series | None = None, as_of: Any = None) -> dict[str, MetricValue]:
    if as_of is not None:
        df = df.loc[:as_of]
        if benchmark is not None:
            benchmark = benchmark.loc[:as_of]
    out: dict[str, MetricValue] = {}
    close = df["close"]
    for p in PERIOD_BARS:
        m = period_return(close, p)
        out[m.name] = m
    for w in (20, 50, 100, 200):
        m = sma(close, w)
        out[m.name] = m
    for m in (rsi(close), atr(df), adx(df), relative_volume(df), volume_trend(df), ma_alignment(close),
              momentum_acceleration(close), trend_persistence(close)):
        out[m.name] = m
    out.update(macd(close))
    out.update(bollinger(close))
    out.update(range_52w(df))
    for p in ("3M", "6M", "12M"):
        name = f"rel_strength_{p.lower()}"
        out[name] = (
            relative_strength(close, benchmark, p)
            if benchmark is not None and len(benchmark)
            else bad(name, Status.MISSING, "benchmark history unavailable", "pct", "relative strength vs benchmark")
        )
    return out
