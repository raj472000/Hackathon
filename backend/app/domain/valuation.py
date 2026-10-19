"""Deterministic valuation ratios.

Price inputs are per share in the trading currency. Aggregate inputs (revenue, EBITDA, debt, cash,
market cap, EV) share the reporting unit (INR crore) and share counts are in crore, so
price (INR) x shares (crore) = market cap (INR crore).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.result import MetricValue, Status, bad, first_missing, ok

VERSION = "valuation-v1"
Num = float | None
MIN_HISTORY_POINTS = 5


def _pos_ratio(name: str, num: Num, den: Num, unit: str, formula: str, nl: str, dl: str, scale: float = 1.0,
               require_positive_num: bool = False) -> MetricValue:
    inputs = {nl: num, dl: den}
    miss = first_missing(inputs)
    if miss:
        return bad(name, Status.MISSING, f"{miss} is missing", unit, formula, inputs)
    assert num is not None and den is not None
    if den <= 0:
        return bad(name, Status.NOT_MEANINGFUL, f"{dl} is non-positive ({den:g}); ratio is not meaningful", unit, formula, inputs)
    if require_positive_num and num <= 0:
        return bad(name, Status.NOT_MEANINGFUL, f"{nl} is non-positive ({num:g}); ratio is not meaningful", unit, formula, inputs)
    return ok(name, num / den * scale, unit, formula, inputs)


def pe(price: Num, eps: Num, basis: str = "TTM") -> MetricValue:
    return _pos_ratio("pe", price, eps, "x", f"price / EPS ({basis})", "price", "eps")


def peg(pe_value: Num, growth_pct: Num, growth_basis: str = "EPS growth YoY") -> MetricValue:
    return _pos_ratio("peg", pe_value, growth_pct, "x", f"P/E / growth rate in percent ({growth_basis})", "pe", "growth_pct")


def market_cap(price: Num, shares_outstanding: Num) -> MetricValue:
    formula = "price * shares outstanding"
    inputs = {"price": price, "shares_outstanding": shares_outstanding}
    miss = first_missing(inputs)
    if miss:
        return bad("market_cap", Status.MISSING, f"{miss} is missing", "currency", formula, inputs)
    return ok("market_cap", price * shares_outstanding, "currency", formula, inputs)  # type: ignore[operator]


def enterprise_value(mcap: Num, total_debt: Num, cash: Num, minority_interest: Num) -> MetricValue:
    formula = "market cap + total debt - cash + minority interest"
    inputs = {"market_cap": mcap, "total_debt": total_debt, "cash": cash, "minority_interest": minority_interest}
    miss = first_missing({k: v for k, v in inputs.items() if k != "minority_interest"})
    if miss:
        return bad("enterprise_value", Status.MISSING, f"{miss} is missing", "currency", formula, inputs)
    note = None if minority_interest is not None else "minority interest not reported; excluded from EV"
    return ok("enterprise_value", mcap + total_debt - cash + (minority_interest or 0.0), "currency", formula, inputs, note)  # type: ignore[operator]


def net_debt(total_debt: Num, cash: Num) -> MetricValue:
    formula = "total debt - cash"
    inputs = {"total_debt": total_debt, "cash": cash}
    miss = first_missing(inputs)
    if miss:
        return bad("net_debt", Status.MISSING, f"{miss} is missing", "currency", formula, inputs)
    return ok("net_debt", total_debt - cash, "currency", formula, inputs)  # type: ignore[operator]


def ev_multiple(name: str, ev: Num, denominator: Num) -> MetricValue:
    label = name.split("_", 1)[1] if "_" in name else "denominator"
    return _pos_ratio(name, ev, denominator, "x", f"enterprise value / {label.upper()}", "enterprise_value", label)


def pb(price: Num, book_value_per_share: Num) -> MetricValue:
    return _pos_ratio("pb", price, book_value_per_share, "x", "price / book value per share", "price", "book_value_per_share")


def ps(mcap: Num, revenue: Num) -> MetricValue:
    return _pos_ratio("ps", mcap, revenue, "x", "market cap / revenue", "market_cap", "revenue")


def price_to_fcf(mcap: Num, fcf: Num) -> MetricValue:
    return _pos_ratio("price_to_fcf", mcap, fcf, "x", "market cap / free cash flow", "market_cap", "fcf")


def earnings_yield(eps: Num, price: Num) -> MetricValue:
    return _pos_ratio("earnings_yield", eps, price, "pct", "EPS / price * 100", "eps", "price", 100)


def fcf_yield(fcf: Num, mcap: Num) -> MetricValue:
    return _pos_ratio("fcf_yield", fcf, mcap, "pct", "free cash flow / market cap * 100", "fcf", "market_cap", 100)


def dividend_yield(dividend_per_share: Num, price: Num) -> MetricValue:
    return _pos_ratio("dividend_yield", dividend_per_share, price, "pct", "dividend per share / price * 100",
                      "dividend_per_share", "price", 100)


def percentile_in_range(value: Num, history: Sequence[float], name: str = "valuation_percentile") -> MetricValue:
    """Percent of historical observations at or below `value` using the mid-rank convention."""
    formula = "(count below + 0.5 * count equal) / n * 100"
    hist = [h for h in history if h is not None and h == h]
    if value is None:
        return bad(name, Status.MISSING, "value is missing", "pct", formula)
    if len(hist) < MIN_HISTORY_POINTS:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(hist)} observation(s), {MIN_HISTORY_POINTS} required", "pct", formula)
    below = sum(1 for h in hist if h < value)
    equal = sum(1 for h in hist if h == value)
    return ok(name, (below + 0.5 * equal) / len(hist) * 100, "pct", formula, {"value": value, "n": len(hist)})


def compute_valuation(
    *,
    price: Num,
    shares_outstanding: Num,
    eps_ttm: Num,
    book_value_per_share: Num,
    revenue: Num,
    ebitda: Num,
    ebit: Num,
    fcf: Num,
    total_debt: Num,
    cash: Num,
    minority_interest: Num,
    dividend_per_share: Num,
    eps_growth_pct: Num,
    growth_basis: str = "EPS growth YoY",
    earnings_basis: str = "TTM",
    sector_kind: str = "industrial",
) -> dict[str, MetricValue]:
    out: dict[str, MetricValue] = {}
    mc = market_cap(price, shares_outstanding)
    ev = enterprise_value(mc.value, total_debt, cash, minority_interest)
    p_e = pe(price, eps_ttm, earnings_basis)
    for m in (
        mc, ev, p_e,
        peg(p_e.value, eps_growth_pct, growth_basis),
        pb(price, book_value_per_share),
        ps(mc.value, revenue),
        net_debt(total_debt, cash),
        ev_multiple("ev_ebitda", ev.value, ebitda),
        ev_multiple("ev_ebit", ev.value, ebit),
        ev_multiple("ev_sales", ev.value, revenue),
        price_to_fcf(mc.value, fcf),
        fcf_yield(fcf, mc.value),
        earnings_yield(eps_ttm, price),
        dividend_yield(dividend_per_share, price),
    ):
        out[m.name] = m
    if sector_kind == "financial":
        for name in ("enterprise_value", "ev_ebitda", "ev_ebit", "ev_sales", "price_to_fcf", "fcf_yield", "net_debt", "ps"):
            m = out[name]
            out[name] = bad(name, Status.NOT_APPLICABLE, "not applicable to banks, NBFCs or insurers", m.unit, m.formula)
    return out
