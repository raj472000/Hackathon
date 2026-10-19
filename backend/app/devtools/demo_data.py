"""DEMO-ONLY synthetic market data.

Everything produced here is fictional and deterministic (seeded by symbol). Symbols are prefixed
`DEMO` so they cannot be mistaken for listed companies. This module is imported only by the mock
MCP servers and by tests; production services never read from it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

START = "2022-01-03"
BENCHMARK = "DEMO 50"

# sector -> (code, count, sector_kind)
SECTORS: dict[str, tuple[str, int, str]] = {
    "Information Technology": ("IT", 6, "industrial"),
    "Financial Services": ("FIN", 7, "financial"),
    "Healthcare": ("HC", 5, "industrial"),
    "Automobile": ("AUTO", 5, "industrial"),
    "FMCG": ("FMCG", 5, "industrial"),
    "Metals & Mining": ("METAL", 4, "industrial"),
    "Oil & Gas": ("ENERGY", 4, "industrial"),
    "Capital Goods": ("CAPG", 5, "industrial"),
    "Chemicals": ("CHEM", 4, "industrial"),
    "Power": ("POWER", 3, "industrial"),
}
SPLIT_SYMBOL, SPLIT_RATIO, SPLIT_BARS_AGO = "DEMOIT03", 2.0, 180
GAP_SYMBOL, GAP_BARS = "DEMOHC02", 4  # NSE mock lacks the last bars; the broker mock has them
CONFLICT_SYMBOL, CONFLICT_BARS_AGO = "DEMOAUTO01", (10, 30)  # broker close differs by 2% on these bars


@dataclass(frozen=True)
class DemoInstrument:
    symbol: str
    name: str
    sector: str
    sector_kind: str
    token: int
    instrument_type: str = "EQ"


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


@lru_cache
def instruments() -> list[DemoInstrument]:
    out, token = [], 100001
    for sector, (code, n, kind) in SECTORS.items():
        for i in range(1, n + 1):
            out.append(DemoInstrument(f"DEMO{code}{i:02d}", f"Demo {sector} {i:02d} Ltd (synthetic)", sector, kind, token))
            token += 1
    return out


@lru_cache
def indices() -> list[DemoInstrument]:
    out = [DemoInstrument(BENCHMARK, "Demo 50 index (synthetic)", "Index", "index", 900001, "INDEX")]
    for n, (sector, (code, _, _)) in enumerate(SECTORS.items(), start=2):
        out.append(DemoInstrument(f"DEMO {code}", f"Demo {sector} index (synthetic)", sector, "index", 900000 + n, "INDEX"))
    return out


def by_symbol(symbol: str) -> DemoInstrument | None:
    return next((i for i in [*instruments(), *indices()] if i.symbol == symbol.upper()), None)


def by_token(token: int) -> DemoInstrument | None:
    return next((i for i in [*instruments(), *indices()] if i.token == int(token)), None)


def _dates(end: date | None = None) -> pd.DatetimeIndex:
    return pd.bdate_range(START, end or (pd.Timestamp.today().normalize() - pd.offsets.BDay(1)))


@lru_cache(maxsize=4)
def _returns(end_iso: str) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    idx = _dates(date.fromisoformat(end_iso))
    n = len(idx)
    t = np.arange(n)
    market = np.random.default_rng(_seed("market")).normal(0.0004, 0.008, n)
    out: dict[str, np.ndarray] = {}
    for k, (sector, (code, _cnt, _kind)) in enumerate(SECTORS.items()):
        rng = np.random.default_rng(_seed(sector))
        # slow sector cycles of different phase so sectors rotate through the RRG quadrants
        cycle = 0.0009 * np.sin(2 * np.pi * (t / (170 + 25 * k)) + k * 0.9)
        sec = cycle + rng.normal(0, 0.005, n)
        out[f"__sector__{code}"] = sec
        for inst in (i for i in instruments() if i.sector == sector):
            r = np.random.default_rng(_seed(inst.symbol))
            beta = 0.6 + 0.9 * r.random()
            drift = r.normal(0.0001, 0.0004)
            out[inst.symbol] = beta * market + sec + drift + r.normal(0, 0.011, n)
    return idx, out


@lru_cache(maxsize=512)
def _adjusted(symbol: str, end_iso: str) -> pd.DataFrame:
    idx, rets = _returns(end_iso)
    inst = by_symbol(symbol)
    if inst is None:
        raise KeyError(symbol)
    if inst.instrument_type == "INDEX":
        members = instruments() if symbol == BENCHMARK else [i for i in instruments() if i.sector == inst.sector]
        r = np.mean([rets[m.symbol] for m in members], axis=0)
        base = 10000.0
    else:
        r = rets[symbol]
        base = 150 + (_seed(symbol + "px") % 2400)
    rng = np.random.default_rng(_seed(symbol + "ohlc"))
    close = base * np.cumprod(1 + r)
    prev = np.concatenate([[close[0] / (1 + r[0])], close[:-1]])
    open_ = prev * (1 + rng.normal(0, 0.003, len(idx)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, len(idx))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, len(idx))))
    vol = np.round(np.exp(rng.normal(13.2, 0.45, len(idx))) * (1 + 6 * np.abs(r)))
    if inst.instrument_type == "INDEX":
        vol = np.zeros(len(idx))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "prev_close": prev, "volume": vol}, index=idx).round(2)


def split_date(end: date | None = None) -> date:
    return _dates(end)[-SPLIT_BARS_AGO].date()


def bars(symbol: str, source: str = "nse", end: date | None = None) -> pd.DataFrame:
    """Daily bars as the given mock source would publish them.

    `nse`: unadjusted (the split symbol shows the raw pre-split prices) and missing the latest bars
    for GAP_SYMBOL. `broker`: split-adjusted, complete, with two deliberately different closes for
    CONFLICT_SYMBOL so the reconciliation path has something to flag.
    """
    idx = _dates(end)
    df = _adjusted(symbol.upper(), idx[-1].date().isoformat()).copy()
    if source == "nse":
        if symbol.upper() == SPLIT_SYMBOL:
            before = df.index < pd.Timestamp(split_date(end))
            cols = ["open", "high", "low", "close", "prev_close"]
            df.loc[before, cols] = (df.loc[before, cols] * SPLIT_RATIO).round(2)
            df.loc[before, "volume"] = np.round(df.loc[before, "volume"] / SPLIT_RATIO)
            df.loc[df.index == pd.Timestamp(split_date(end)), "prev_close"] = df.loc[before, "close"].iloc[-1]
        if symbol.upper() == GAP_SYMBOL:
            df = df.iloc[:-GAP_BARS]
    elif symbol.upper() == CONFLICT_SYMBOL:
        for ago in CONFLICT_BARS_AGO:
            df.iloc[-ago, df.columns.get_loc("close")] = round(float(df.iloc[-ago]["close"]) * 1.02, 2)
    df["traded_value"] = (df["close"] * df["volume"]).round(2)
    return df


def corporate_actions(symbol: str, end: date | None = None) -> list[dict[str, str]]:
    if symbol.upper() != SPLIT_SYMBOL:
        return []
    return [{"symbol": SPLIT_SYMBOL, "exDate": split_date(end).isoformat(),
             "purpose": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"}]


def fundamentals(symbol: str) -> dict:
    """Five synthetic annual periods (INR crore) in the normalised fundamentals file format."""
    inst = by_symbol(symbol)
    if inst is None or inst.instrument_type != "EQ":
        raise KeyError(symbol)
    rng = np.random.default_rng(_seed(symbol + "fund"))
    rev = float(rng.uniform(1500, 40000))
    growth, margin = rng.normal(0.11, 0.07), float(rng.uniform(0.10, 0.32))
    # Share count chosen so the synthetic price implies a plausible earnings multiple.
    base_price = 150 + (_seed(symbol + "px") % 2400)
    shares = float(rev * (1 + growth) ** 5 * margin * 0.55 * rng.uniform(14, 42) / (base_price * 1.3))
    equity, debt = rev * rng.uniform(0.35, 0.9), rev * rng.uniform(0.0, 0.45)
    periods = []
    for year in range(2022, 2027):
        g = growth + rng.normal(0, 0.05)
        rev *= 1 + g
        m = float(np.clip(margin + rng.normal(0, 0.015), 0.03, 0.5))
        ebitda = rev * m
        dep = rev * 0.035
        ebit = ebitda - dep
        interest = debt * 0.085
        pbt = ebit - interest
        tax = max(pbt, 0) * 0.25
        pat = pbt - tax
        equity += pat * 0.7
        debt *= 1 + rng.normal(-0.03, 0.08)
        cfo = pat * rng.uniform(0.7, 1.3) + dep
        capex = rev * rng.uniform(0.03, 0.09)
        total_assets = equity + debt + rev * 0.25
        div = max(pat, 0) * rng.uniform(0.15, 0.4)
        periods.append({
            "period_end": f"{year}-03-31", "fiscal_year": year, "period_type": "annual", "revenue": rev, "cogs": rev * (1 - m - 0.18),
            "ebitda": ebitda, "depreciation": dep, "ebit": ebit, "interest": interest, "pbt": pbt, "tax": tax, "pat": pat,
            "eps": pat / shares, "equity": equity, "total_debt": debt, "cash": rev * rng.uniform(0.03, 0.15),
            "total_assets": total_assets, "current_assets": rev * 0.32, "current_liabilities": rev * 0.2,
            "inventory": rev * 0.09, "receivables": rev * 0.12, "payables": rev * 0.08, "cfo": cfo, "capex": capex,
            "dividends_paid": div, "shares_outstanding": shares, "minority_interest": 0.0,
        })
    return {
        "symbol": symbol, "exchange": "NSE", "currency": "INR", "unit": "crore", "is_demo": True,
        "source": "demo_generator", "source_note": "Synthetic figures for demonstration only. Not real financial statements.",
        "periods": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()} for p in periods],
    }
