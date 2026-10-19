"""Validated filter DSL for screens and themes.

A theme is data, not code: a universe, a list of `metric op value` filters over an allowlisted
metric catalog, and a ranking over allowlisted metrics. Natural-language requests are translated
into this structure for review; nothing a user or a model writes is ever executed as SQL or code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.scoring import percentile_ranks

VERSION = "theme-dsl-v1"
MAX_LIMIT = 500


def _m(label: str, unit: str, group: str, hib: bool | None = True) -> dict[str, Any]:
    return {"label": label, "unit": unit, "group": group, "higher_is_better": hib}


METRIC_CATALOG: dict[str, dict[str, Any]] = {
    # fundamentals
    "revenue_growth_yoy": _m("Revenue growth YoY", "pct", "fundamentals"),
    "ebitda_growth_yoy": _m("EBITDA growth YoY", "pct", "fundamentals"),
    "pat_growth_yoy": _m("PAT growth YoY", "pct", "fundamentals"),
    "eps_growth_yoy": _m("EPS growth YoY", "pct", "fundamentals"),
    "revenue_cagr_3y": _m("Revenue CAGR 3Y", "pct", "fundamentals"),
    "pat_cagr_3y": _m("PAT CAGR 3Y", "pct", "fundamentals"),
    "revenue_cagr_5y": _m("Revenue CAGR 5Y", "pct", "fundamentals"),
    "pat_cagr_5y": _m("PAT CAGR 5Y", "pct", "fundamentals"),
    "gross_margin": _m("Gross margin", "pct", "fundamentals"),
    "ebitda_margin": _m("EBITDA margin", "pct", "fundamentals"),
    "ebit_margin": _m("EBIT margin", "pct", "fundamentals"),
    "pat_margin": _m("PAT margin", "pct", "fundamentals"),
    "ebitda_margin_change": _m("EBITDA margin change YoY", "pct", "fundamentals"),
    "roe": _m("ROE", "pct", "fundamentals"),
    "roa": _m("ROA", "pct", "fundamentals"),
    "roce": _m("ROCE", "pct", "fundamentals"),
    "roic": _m("ROIC", "pct", "fundamentals"),
    "debt_equity": _m("Debt / equity", "x", "fundamentals", False),
    "net_debt_ebitda": _m("Net debt / EBITDA", "x", "fundamentals", False),
    "interest_coverage": _m("Interest coverage", "x", "fundamentals"),
    "current_ratio": _m("Current ratio", "x", "fundamentals"),
    "quick_ratio": _m("Quick ratio", "x", "fundamentals"),
    "asset_turnover": _m("Asset turnover", "x", "fundamentals"),
    "inventory_days": _m("Inventory days", "days", "fundamentals", False),
    "receivable_days": _m("Receivable days", "days", "fundamentals", False),
    "payable_days": _m("Payable days", "days", "fundamentals", None),
    "cash_conversion_cycle": _m("Cash conversion cycle", "days", "fundamentals", False),
    "fcf": _m("Free cash flow", "currency", "fundamentals"),
    "fcf_growth_yoy": _m("FCF growth YoY", "pct", "fundamentals"),
    "cfo_to_pat": _m("CFO / PAT", "x", "fundamentals"),
    "accrual_ratio": _m("Accrual ratio", "pct", "fundamentals", False),
    "payout_ratio": _m("Dividend payout ratio", "pct", "fundamentals", None),
    "share_count_change": _m("Share count change YoY", "pct", "fundamentals", False),
    "effective_tax_rate": _m("Effective tax rate", "pct", "fundamentals", None),
    "pat_growth_stdev": _m("PAT growth variability", "pct", "fundamentals", False),
    "ebitda_margin_stdev": _m("EBITDA margin variability", "pct", "fundamentals", False),
    "positive_revenue_growth_years": _m("Years of revenue growth (of available)", "pct", "fundamentals"),
    "positive_pat_growth_years": _m("Years of PAT growth (of available)", "pct", "fundamentals"),
    "dividend_years": _m("Consecutive dividend years", "count", "fundamentals"),
    # valuation
    "pe": _m("P/E (TTM)", "x", "valuation", False),
    "peg": _m("PEG", "x", "valuation", False),
    "pb": _m("P/B", "x", "valuation", False),
    "ps": _m("P/S", "x", "valuation", False),
    "ev_ebitda": _m("EV/EBITDA", "x", "valuation", False),
    "ev_ebit": _m("EV/EBIT", "x", "valuation", False),
    "ev_sales": _m("EV/Sales", "x", "valuation", False),
    "price_to_fcf": _m("Price / FCF", "x", "valuation", False),
    "fcf_yield": _m("FCF yield", "pct", "valuation"),
    "earnings_yield": _m("Earnings yield", "pct", "valuation"),
    "dividend_yield": _m("Dividend yield", "pct", "valuation"),
    "market_cap": _m("Market cap", "currency", "valuation", None),
    "net_debt": _m("Net debt", "currency", "valuation", False),
    "pe_sector_percentile": _m("P/E percentile in sector", "pct", "valuation", False),
    "pe_history_percentile": _m("P/E percentile vs own history", "pct", "valuation", False),
    # price action
    "close": _m("Last close", "price", "price", None),
    "return_1w": _m("Return 1W", "pct", "price"),
    "return_1m": _m("Return 1M", "pct", "price"),
    "return_3m": _m("Return 3M", "pct", "price"),
    "return_6m": _m("Return 6M", "pct", "price"),
    "return_12m": _m("Return 12M", "pct", "price"),
    "return_3y": _m("Return 3Y", "pct", "price"),
    "return_5y": _m("Return 5Y", "pct", "price"),
    "rel_strength_3m": _m("Relative strength 3M vs benchmark", "pct", "price"),
    "rel_strength_6m": _m("Relative strength 6M vs benchmark", "pct", "price"),
    "rel_strength_12m": _m("Relative strength 12M vs benchmark", "pct", "price"),
    "rsi_14": _m("RSI (14)", "score", "price", None),
    "macd_hist": _m("MACD histogram", "price", "price"),
    "adx_14": _m("ADX (14)", "score", "price"),
    "atr_14": _m("ATR (14)", "price", "price", None),
    "bb_pct_b": _m("Bollinger %B", "pct", "price", None),
    "ma_alignment": _m("Moving-average alignment", "score", "price"),
    "momentum_acceleration": _m("Momentum acceleration", "pct", "price"),
    "trend_persistence": _m("Trend persistence", "pct", "price"),
    "relative_volume": _m("Relative volume", "x", "price"),
    "volume_trend": _m("Volume trend 20/60", "x", "price"),
    "dist_from_52w_high_pct": _m("Distance from 52W high", "pct", "price"),
    "dist_from_52w_low_pct": _m("Distance from 52W low", "pct", "price", None),
    "breakout_signal": _m("Breakout (1) / breakdown (-1)", "count", "price"),
    # risk
    "volatility_252d": _m("Volatility 1Y", "pct", "risk", False),
    "volatility_63d": _m("Volatility 3M", "pct", "risk", False),
    "downside_volatility_252d": _m("Downside volatility 1Y", "pct", "risk", False),
    "max_drawdown_1y": _m("Max drawdown 1Y", "pct", "risk"),
    "max_drawdown_all": _m("Max drawdown (available history)", "pct", "risk"),
    "beta": _m("Beta", "x", "risk", False),
    "correlation": _m("Correlation with benchmark", "ratio", "risk", None),
    "avg_traded_value": _m("Avg traded value 20D", "currency", "risk"),
    "max_gap_pct": _m("Largest gap 1Y", "pct", "risk", False),
    # context
    "sector_rs_ratio": _m("Sector RS-Ratio", "score", "context"),
    "sector_rs_momentum": _m("Sector RS-Momentum", "score", "context"),
    "sector_return_3m": _m("Sector return 3M", "pct", "context"),
    "rel_strength_vs_sector_3m": _m("Relative strength 3M vs sector", "pct", "context"),
    # scores
    "overall_score": _m("Overall research score", "score", "score"),
    "fundamentals_score": _m("Fundamentals score", "score", "score"),
    "valuation_score": _m("Valuation score", "score", "score"),
    "momentum_score": _m("Momentum score", "score", "score"),
    "risk_score": _m("Risk score", "score", "score"),
    "context_score": _m("Context score", "score", "score"),
    "completeness_pct": _m("Data completeness", "pct", "score"),
}

Op = Literal["gt", "gte", "lt", "lte", "eq", "between"]
OP_SYMBOL = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "between": "between"}


def _check_metric(v: str) -> str:
    if v not in METRIC_CATALOG:
        raise ValueError(f"unknown metric '{v[:40]}'")
    return v


class Filter(BaseModel):
    metric: str
    op: Op
    value: float
    value2: float | None = None

    _v = field_validator("metric")(_check_metric)

    @model_validator(mode="after")
    def _between(self) -> Filter:
        if self.op == "between":
            if self.value2 is None:
                raise ValueError("'between' needs value2")
            if self.value2 < self.value:
                raise ValueError("'between' needs value <= value2")
        return self

    def describe(self) -> str:
        label = METRIC_CATALOG[self.metric]["label"]
        if self.op == "between":
            return f"{label} between {self.value:g} and {self.value2:g}"
        return f"{label} {OP_SYMBOL[self.op]} {self.value:g}"


class RankKey(BaseModel):
    metric: str
    direction: Literal["asc", "desc"] = "desc"

    _v = field_validator("metric")(_check_metric)


class Universe(BaseModel):
    exchange: Literal["NSE", "BSE"] = "NSE"
    instrument_types: list[Literal["EQ", "ETF", "INDEX", "REIT", "INVIT", "COMMODITY"]] = Field(default_factory=lambda: ["EQ"])  # type: ignore[arg-type]
    sectors: list[str] | None = Field(default=None, max_length=50)
    min_avg_traded_value: float | None = Field(default=None, ge=0)


class ThemeSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    universe: Universe = Field(default_factory=Universe)
    filters: list[Filter] = Field(default_factory=list, max_length=25)
    rank_by: list[RankKey] = Field(default_factory=lambda: [RankKey(metric="overall_score")], min_length=1, max_length=8)
    limit: int = Field(default=25, ge=1, le=MAX_LIMIT)
    exclude_red_flags: bool = True

    def describe(self) -> list[str]:
        out = [f"Universe: {self.universe.exchange} {', '.join(self.universe.instrument_types)}"
               + (f", sectors {', '.join(self.universe.sectors)}" if self.universe.sectors else "")]
        if self.universe.min_avg_traded_value:
            out.append(f"Avg traded value 20D ≥ {self.universe.min_avg_traded_value:g}")
        out += [f.describe() for f in self.filters]
        out.append("Rank by equal-weight percentile of: " + ", ".join(
            f"{METRIC_CATALOG[r.metric]['label']} ({'high' if r.direction == 'desc' else 'low'} first)" for r in self.rank_by))
        if self.exclude_red_flags:
            out.append("Exclude instruments with governance, insolvency or data-integrity red flags")
        return out


@dataclass
class Constituent:
    instrument_id: str
    rank: int
    score: float | None
    values: dict[str, float | None]


@dataclass
class Exclusion:
    instrument_id: str
    reason: str


@dataclass
class Evaluation:
    constituents: list[Constituent]
    exclusions: list[Exclusion]
    eligible_count: int
    universe_count: int
    dsl_version: str = VERSION


def _passes(f: Filter, v: float) -> bool:
    return {"gt": v > f.value, "gte": v >= f.value, "lt": v < f.value, "lte": v <= f.value, "eq": v == f.value,
            "between": f.value <= v <= (f.value2 if f.value2 is not None else f.value)}[f.op]


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def evaluate(spec: ThemeSpec, rows: Mapping[str, Mapping[str, Any]]) -> Evaluation:
    """Apply filters and ranking. A missing metric excludes the instrument with a reason; it is
    never read as zero. Ties break on instrument id so results are reproducible."""
    exclusions: list[Exclusion] = []
    eligible: list[str] = []
    universe = 0
    for iid in sorted(rows):
        r = rows[iid]
        if r.get("instrument_type", "EQ") not in spec.universe.instrument_types:
            continue
        if spec.universe.sectors and r.get("sector") not in spec.universe.sectors:
            continue
        universe += 1
        reason = None
        if spec.exclude_red_flags and r.get("governance_flag"):
            reason = "red flag: governance, insolvency or data integrity"
        if reason is None and spec.universe.min_avg_traded_value:
            atv = _num(r.get("avg_traded_value"))
            if atv is None:
                reason = "missing metric: avg_traded_value"
            elif atv < spec.universe.min_avg_traded_value:
                reason = f"failed: liquidity below {spec.universe.min_avg_traded_value:g} (value {atv:g})"
        if reason is None:
            for f in spec.filters:
                v = _num(r.get(f.metric))
                if v is None:
                    reason = f"missing metric: {f.metric}"
                    break
                if not _passes(f, v):
                    bound = f"{f.value:g}" + (f"..{f.value2:g}" if f.op == "between" else "")
                    reason = f"failed: {f.metric} {f.op} {bound} (value {v:g})"
                    break
        if reason:
            exclusions.append(Exclusion(iid, reason))
        else:
            eligible.append(iid)

    per_metric = [
        percentile_ranks({i: _num(rows[i].get(k.metric)) for i in eligible}, higher_is_better=k.direction == "desc", min_sample=1)
        for k in spec.rank_by
    ]
    scored: list[tuple[str, float | None]] = []
    for i in eligible:
        parts = [pm[i] for pm in per_metric if pm[i] is not None]
        scored.append((i, sum(parts) / len(parts) if parts else None))  # type: ignore[arg-type]
    unranked = [i for i, s in scored if s is None]
    ranked = sorted(((i, s) for i, s in scored if s is not None), key=lambda t: (-round(t[1], 9), t[0]))
    for i in unranked:
        exclusions.append(Exclusion(i, "missing metric: " + ", ".join(k.metric for k in spec.rank_by) + " (ranking)"))
    keys = {f.metric for f in spec.filters} | {k.metric for k in spec.rank_by}
    cons = [
        Constituent(i, n, round(s, 4), {k: _num(rows[i].get(k)) for k in sorted(keys)})
        for n, (i, s) in enumerate(ranked[: spec.limit], start=1)
    ]
    return Evaluation(cons, exclusions, len(eligible) - len(unranked), universe)


# ---------------------------------------------------------------------------------------------
# Natural language -> reviewable spec (deterministic rules; an LLM may propose a spec as well, and
# its proposal passes through the same ThemeSpec validation).
# ---------------------------------------------------------------------------------------------

@dataclass
class Interpretation:
    spec: ThemeSpec
    ambiguities: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    requires_confirmation: bool = True
    source: str = "rules"


def _f(metric: str, op: str, value: float, value2: float | None = None) -> Filter:
    return Filter(metric=metric, op=op, value=value, value2=value2)  # type: ignore[arg-type]


# (pattern, filters, ambiguity note or None)
_RULES: list[tuple[str, list[Filter], str | None]] = [
    (r"(improving|growing|rising|consistent)\s+(3[- ]year\s+)?revenue|revenue growth", [_f("revenue_cagr_3y", "gt", 0)],
     "'improving revenue' read as 3-year revenue CAGR > 0%; set the growth rate you want"),
    (r"(earnings|profit|pat)\s+growth|growing\s+(earnings|profit)", [_f("pat_growth_yoy", "gt", 0)],
     "'earnings growth' read as PAT growth YoY > 0%"),
    (r"\broce\b", [_f("roce", "gte", 15)], "ROCE threshold assumed at 15%; an improving-ROCE trend metric is not available"),
    (r"\broe\b", [_f("roe", "gte", 15)], "ROE threshold assumed at 15%"),
    (r"\broic\b", [_f("roic", "gte", 12)], "ROIC threshold assumed at 12%"),
    (r"low(er)?\s+(debt|leverage)|debt[- ]free|net cash", [_f("debt_equity", "lt", 0.5)],
     "'low debt' read as debt/equity < 0.5x; confirm the limit"),
    (r"(positive|improving|strong)\s+(6[- ]month\s+)?relative strength", [_f("rel_strength_6m", "gt", 0)], None),
    (r"moderate\s+volatility", [_f("volatility_252d", "between", 20, 35)],
     "'moderate volatility' read as 1-year volatility between 20% and 35%; confirm the range"),
    (r"low\s+volatility|stable\s+price", [_f("volatility_252d", "lt", 20)], "'low volatility' read as 1-year volatility < 20%"),
    (r"low\s+beta|defensive", [_f("beta", "lt", 0.9)], "'low beta / defensive' read as beta < 0.9"),
    (r"momentum|uptrend|trending", [_f("return_6m", "gt", 0), _f("ma_alignment", "gte", 75)],
     "'momentum' read as positive 6-month return with moving averages aligned"),
    (r"cheap|undervalued|low\s+(pe|p/e|valuation)|value\b", [_f("pe_sector_percentile", "lte", 40)],
     "'cheap' read as P/E in the lowest 40% of sector peers"),
    (r"(high|good)\s+dividend|dividend yield|income", [_f("dividend_yield", "gte", 2), _f("payout_ratio", "lte", 80)],
     "'dividend' read as yield >= 2% with payout <= 80%"),
    (r"(strong|positive|high)\s+(free\s+)?cash\s?flow|fcf", [_f("fcf", "gt", 0), _f("cfo_to_pat", "gte", 0.8)],
     "'strong cash flow' read as positive FCF and CFO/PAT >= 0.8"),
    (r"(high|stable|strong)\s+margin", [_f("ebitda_margin", "gte", 18)], "'high margin' read as EBITDA margin >= 18%"),
    (r"oversold", [_f("rsi_14", "lt", 35)], "'oversold' read as RSI(14) < 35"),
    (r"breakout", [_f("breakout_signal", "eq", 1)], None),
    (r"near\s+(52[- ]week\s+)?high", [_f("dist_from_52w_high_pct", "gte", -10)], "'near high' read as within 10% of the 52-week high"),
    (r"liquid", [_f("avg_traded_value", "gte", 1e8)], "'liquid' read as average traded value >= INR 10 crore a day"),
]

_SECTOR_WORDS = {
    r"banks?|banking|financials?|nbfc": "Financial Services",
    r"it (sector|stocks|companies|services)|software|technology": "Information Technology",
    r"pharma\w*|healthcare": "Healthcare",
    r"auto\w*": "Automobile",
    r"fmcg|consumer staples": "FMCG",
    r"metals?|mining": "Metals & Mining",
    r"oil|gas|energy": "Oil & Gas",
    r"power|utilities": "Power",
    r"realty|real estate": "Realty",
    r"cement": "Construction Materials",
    r"telecom": "Telecom",
    r"chemicals?": "Chemicals",
    r"capital goods|industrials?": "Capital Goods",
}


def interpret(text: str, name: str | None = None) -> Interpretation:
    t = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "
    filters: dict[str, Filter] = {}
    notes: list[str] = []
    matched: list[str] = []
    for pattern, fs, note in _RULES:
        m = re.search(pattern, t)
        if not m:
            continue
        matched.append(m.group(0).strip())
        for f in fs:
            filters.setdefault(f.metric, f)
        if note:
            notes.append(note)
    sectors = sorted({v for k, v in _SECTOR_WORDS.items() if re.search(rf"\b(?:{k})\b", t)})
    if not filters:
        notes.append("No recognised screening condition was found. Add filters manually or rephrase using metrics "
                     "such as ROCE, debt, revenue growth, relative strength, volatility, valuation or dividend.")
    if re.search(r"\bbse\b", t):
        notes.append("BSE was mentioned; only NSE data is configured, so the universe is NSE")
    rank = [RankKey(metric="overall_score")]
    if "rel_strength_6m" in filters or "return_6m" in filters:
        rank.append(RankKey(metric="rel_strength_6m"))
    spec = ThemeSpec(name=(name or text.strip()[:80] or "Custom theme"), universe=Universe(sectors=sectors or None),
                     filters=list(filters.values()), rank_by=rank, limit=25)
    return Interpretation(spec, notes, matched, requires_confirmation=True)
