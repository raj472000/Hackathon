"""Entry Setup indicator: a rule checklist, never an instruction to buy.

State rules (version `entry-v1`)
- Insufficient data: no price, price data older than `MAX_AGE_DAYS`, or completeness below 60%.
- Unfavorable: risk label is Very High, or the close is below SMA200 with a negative 6-month return.
- Setup favorable: every condition passes.
- Watch: otherwise; the failed or unknown conditions are listed as things to wait for.

No entry zone is produced: this project has no backtested entry-zone method. Reference levels
(moving averages, ATR distance) are descriptive only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

VERSION = "entry-v1"
MAX_AGE_DAYS = 5
MIN_COMPLETENESS = 60.0
MIN_TRADED_VALUE = 1e7  # INR 1 crore a day

DISCLAIMER = ("General research signal based on disclosed rules. It is not personalised advice, not a recommendation "
              "to transact, and not a prediction of returns.")


@dataclass
class Condition:
    key: str
    label: str
    rule: str
    passed: bool | None  # None = cannot be evaluated
    detail: str


@dataclass
class EntrySetup:
    state: str
    conditions: list[Condition]
    wait_for: list[str]
    invalidation: dict[str, Any] | None
    reference_levels: dict[str, float | None]
    entry_zone: None
    entry_zone_reason: str
    rules: str
    disclaimer: str = DISCLAIMER
    version: str = VERSION
    reasons: list[str] = field(default_factory=list)


RULES = __doc__.split("State rules", 1)[1].strip() if __doc__ else ""


def _fmt(v: Any, nd: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"


def evaluate(m: Mapping[str, Any]) -> EntrySetup:
    g = m.get
    close, s50, s200 = g("close"), g("sma_50"), g("sma_200")
    r6, rs6, rsi, atr = g("return_6m"), g("rel_strength_6m"), g("rsi_14"), g("atr_14")
    val, fund, risk = g("valuation_score"), g("fundamentals_score"), g("risk_label")
    atv, comp, age = g("avg_traded_value"), g("completeness_pct"), g("data_age_days")

    def cond(key: str, label: str, rule: str, inputs: list[Any], test: Any, detail: str) -> Condition:
        return Condition(key, label, rule, None if any(i is None for i in inputs) else bool(test()), detail)

    conds = [
        cond("trend", "Price trend", "close > SMA50 > SMA200", [close, s50, s200], lambda: close > s50 > s200,  # type: ignore[operator]
             f"close {_fmt(close)}, SMA50 {_fmt(s50)}, SMA200 {_fmt(s200)}"),
        cond("momentum", "Momentum and relative strength", "6M return > 0 and 6M relative strength > 0", [r6, rs6],
             lambda: r6 > 0 and rs6 > 0, f"6M return {_fmt(r6, 1)}%, relative strength {_fmt(rs6, 1)}%"),  # type: ignore[operator]
        cond("not_overbought", "Not overextended", "RSI(14) < 75", [rsi], lambda: rsi < 75, f"RSI {_fmt(rsi, 1)}"),  # type: ignore[operator]
        cond("valuation", "Valuation versus peers", "valuation pillar score >= 40", [val], lambda: val >= 40,  # type: ignore[operator]
             f"valuation score {_fmt(val, 0)}"),
        cond("quality", "Fundamental quality", "fundamentals pillar score >= 50", [fund], lambda: fund >= 50,  # type: ignore[operator]
             f"fundamentals score {_fmt(fund, 0)}"),
        cond("risk", "Risk", "risk label is not Very High", [risk], lambda: risk != "Very High", f"risk label {risk or 'n/a'}"),
        cond("liquidity", "Liquidity", f"average traded value >= INR {MIN_TRADED_VALUE:,.0f} a day", [atv],
             lambda: atv >= MIN_TRADED_VALUE, f"avg traded value {_fmt(atv, 0)}"),  # type: ignore[operator]
        cond("data", "Data completeness and freshness", f"completeness >= {MIN_COMPLETENESS:.0f}% and price data <= {MAX_AGE_DAYS} days old",
             [comp, age], lambda: comp >= MIN_COMPLETENESS and age <= MAX_AGE_DAYS,  # type: ignore[operator]
             f"completeness {_fmt(comp, 0)}%, data age {_fmt(age, 0)} day(s)"),
    ]
    reasons: list[str] = []
    if close is None:
        reasons.append("no current price")
    if age is None or age > MAX_AGE_DAYS:
        reasons.append("price data is stale or its age is unknown")
    if comp is None or comp < MIN_COMPLETENESS:
        reasons.append(f"data completeness below {MIN_COMPLETENESS:.0f}%")
    if reasons:
        state = "Insufficient data"
    elif risk == "Very High" or (s200 is not None and r6 is not None and close < s200 and r6 < 0):
        state = "Unfavorable"
        reasons.append("risk label is Very High" if risk == "Very High" else "close is below SMA200 with a negative 6-month return")
    elif all(c.passed for c in conds):
        state = "Setup favorable"
    else:
        state = "Watch"
    wait = [f"{c.label}: needs {c.rule} ({c.detail})" for c in conds if not c.passed]
    invalidation = None
    if s200 is not None:
        invalidation = {
            "condition": f"A daily close below SMA200 ({_fmt(s200)}), or 6-month relative strength turning negative, would weaken the setup",
            "level": s200,
            "method": "200-day simple moving average of adjusted closes",
        }
    levels = {"sma_50": s50, "sma_200": s200, "atr_14": atr,
              "two_atr_below_close": None if close is None or atr is None else close - 2 * atr}
    return EntrySetup(state, conds, wait, invalidation, levels, None,
                      "No backtested entry-zone method is configured, so no entry zone or target price is shown.", RULES,
                      reasons=reasons)
