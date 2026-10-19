"""Deterministic fundamental ratios.

Conventions
- Monetary inputs share one reporting currency and unit (INR crore in the Indian pipeline).
- Percent metrics are returned as percent numbers (15.0 means 15%).
- CapEx is a positive outflow magnitude; FCF = CFO - |CapEx|.
- A ratio with a non-positive denominator is `not_meaningful`, never zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from app.domain.result import MetricValue, Status, bad, first_missing, ok

VERSION = "fundamentals-v1"

Num = float | None


def _ratio(
    name: str,
    num: Num,
    den: Num,
    *,
    unit: str,
    formula: str,
    scale: float = 1.0,
    num_label: str = "numerator",
    den_label: str = "denominator",
    allow_negative_num: bool = True,
) -> MetricValue:
    inputs = {num_label: num, den_label: den}
    miss = first_missing(inputs)
    if miss:
        return bad(name, Status.MISSING, f"{miss} is missing", unit, formula, inputs)
    assert num is not None and den is not None
    if den <= 0:
        return bad(name, Status.NOT_MEANINGFUL, f"{den_label} is non-positive ({den:g})", unit, formula, inputs)
    if not allow_negative_num and num < 0:
        return bad(name, Status.NOT_MEANINGFUL, f"{num_label} is negative ({num:g})", unit, formula, inputs)
    return ok(name, num / den * scale, unit, formula, inputs)


def growth(name: str, current: Num, previous: Num) -> MetricValue:
    return _growth(name, current, previous)


def _growth(name: str, current: Num, previous: Num) -> MetricValue:
    formula = "(current / previous - 1) * 100"
    inputs = {"current": current, "previous": previous}
    miss = first_missing(inputs)
    if miss:
        return bad(name, Status.MISSING, f"{miss} is missing", "pct", formula, inputs)
    assert current is not None and previous is not None
    if previous <= 0:
        return bad(name, Status.NOT_MEANINGFUL, f"previous value is non-positive ({previous:g})", "pct", formula, inputs)
    return ok(name, (current / previous - 1) * 100, "pct", formula, inputs)


def cagr(name: str, start: Num, end: Num, years: float) -> MetricValue:
    formula = f"((end / start) ^ (1 / {years:g}) - 1) * 100"
    inputs = {"start": start, "end": end, "years": years}
    miss = first_missing(inputs)
    if miss:
        return bad(name, Status.MISSING, f"{miss} is missing", "pct", formula, inputs)
    assert start is not None and end is not None
    if start <= 0 or end <= 0 or years <= 0:
        return bad(name, Status.NOT_MEANINGFUL, "CAGR needs positive start and end values", "pct", formula, inputs)
    return ok(name, ((end / start) ** (1 / years) - 1) * 100, "pct", formula, inputs)


def margin(name: str, numerator: Num, revenue: Num) -> MetricValue:
    return _ratio(name, numerator, revenue, unit="pct", formula="numerator / revenue * 100", scale=100, den_label="revenue")


def _avg(a: Num, b: Num) -> Num:
    if a is None:
        return None
    return a if b is None else (a + b) / 2


def roe(pat: Num, equity: Num, prior_equity: Num = None) -> MetricValue:
    return _ratio("roe", pat, _avg(equity, prior_equity), unit="pct", formula="PAT / average shareholders' equity * 100",
                  scale=100, num_label="pat", den_label="avg_equity")


def roa(pat: Num, total_assets: Num, prior_total_assets: Num = None) -> MetricValue:
    return _ratio("roa", pat, _avg(total_assets, prior_total_assets), unit="pct", formula="PAT / average total assets * 100",
                  scale=100, num_label="pat", den_label="avg_total_assets")


def roce(ebit: Num, total_assets: Num, current_liabilities: Num) -> MetricValue:
    formula = "EBIT / (total assets - current liabilities) * 100"
    ce = None if total_assets is None or current_liabilities is None else total_assets - current_liabilities
    m = _ratio("roce", ebit, ce, unit="pct", formula=formula, scale=100, num_label="ebit", den_label="capital_employed")
    return _with_inputs(m, {"ebit": ebit, "total_assets": total_assets, "current_liabilities": current_liabilities})


def roic(ebit: Num, tax_rate_pct: Num, equity: Num, total_debt: Num, cash: Num) -> MetricValue:
    formula = "EBIT * (1 - tax rate) / (equity + total debt - cash) * 100"
    raw = {"ebit": ebit, "tax_rate_pct": tax_rate_pct, "equity": equity, "total_debt": total_debt, "cash": cash}
    miss = first_missing(raw)
    if miss:
        return bad("roic", Status.MISSING, f"{miss} is missing", "pct", formula, raw)
    assert ebit is not None and tax_rate_pct is not None and equity is not None and total_debt is not None and cash is not None
    if not 0 <= tax_rate_pct < 100:
        return bad("roic", Status.NOT_MEANINGFUL, f"effective tax rate {tax_rate_pct:g}% is outside 0-100", "pct", formula, raw)
    m = _ratio("roic", ebit * (1 - tax_rate_pct / 100), equity + total_debt - cash, unit="pct", formula=formula, scale=100,
               num_label="nopat", den_label="invested_capital")
    return _with_inputs(m, raw)


def _with_inputs(m: MetricValue, inputs: dict[str, Any]) -> MetricValue:
    return MetricValue(m.name, m.value, m.status, m.reason, m.unit, m.formula, inputs)


def debt_equity(total_debt: Num, equity: Num) -> MetricValue:
    return _ratio("debt_equity", total_debt, equity, unit="x", formula="total debt / shareholders' equity",
                  num_label="total_debt", den_label="equity")


def net_debt_ebitda(total_debt: Num, cash: Num, ebitda: Num) -> MetricValue:
    nd = None if total_debt is None or cash is None else total_debt - cash
    m = _ratio("net_debt_ebitda", nd, ebitda, unit="x", formula="(total debt - cash) / EBITDA", num_label="net_debt", den_label="ebitda")
    return _with_inputs(m, {"total_debt": total_debt, "cash": cash, "ebitda": ebitda})


def interest_coverage(ebit: Num, interest: Num) -> MetricValue:
    return _ratio("interest_coverage", ebit, interest, unit="x", formula="EBIT / finance costs", num_label="ebit", den_label="interest")


def current_ratio(current_assets: Num, current_liabilities: Num) -> MetricValue:
    return _ratio("current_ratio", current_assets, current_liabilities, unit="x", formula="current assets / current liabilities",
                  num_label="current_assets", den_label="current_liabilities")


def quick_ratio(current_assets: Num, inventory: Num, current_liabilities: Num) -> MetricValue:
    q = None if current_assets is None or inventory is None else current_assets - inventory
    m = _ratio("quick_ratio", q, current_liabilities, unit="x", formula="(current assets - inventory) / current liabilities",
               num_label="quick_assets", den_label="current_liabilities")
    return _with_inputs(m, {"current_assets": current_assets, "inventory": inventory, "current_liabilities": current_liabilities})


def asset_turnover(revenue: Num, total_assets: Num, prior_total_assets: Num = None) -> MetricValue:
    return _ratio("asset_turnover", revenue, _avg(total_assets, prior_total_assets), unit="x", formula="revenue / average total assets",
                  num_label="revenue", den_label="avg_total_assets")


def inventory_days(inventory: Num, cogs: Num) -> MetricValue:
    return _ratio("inventory_days", inventory, cogs, unit="days", formula="inventory / COGS * 365", scale=365,
                  num_label="inventory", den_label="cogs")


def receivable_days(receivables: Num, revenue: Num) -> MetricValue:
    return _ratio("receivable_days", receivables, revenue, unit="days", formula="receivables / revenue * 365", scale=365,
                  num_label="receivables", den_label="revenue")


def payable_days(payables: Num, cogs: Num) -> MetricValue:
    return _ratio("payable_days", payables, cogs, unit="days", formula="payables / COGS * 365", scale=365,
                  num_label="payables", den_label="cogs")


def cash_conversion_cycle(inv: MetricValue, rec: MetricValue, pay: MetricValue) -> MetricValue:
    formula = "inventory days + receivable days - payable days"
    inputs = {"inventory_days": inv.value, "receivable_days": rec.value, "payable_days": pay.value}
    miss = first_missing(inputs)
    if miss:
        worst = next(m for m in (inv, rec, pay) if m.value is None)
        return bad("cash_conversion_cycle", worst.status, f"{miss} unavailable: {worst.reason}", "days", formula, inputs)
    return ok("cash_conversion_cycle", inv.value + rec.value - pay.value, "days", formula, inputs)  # type: ignore[operator]


def free_cash_flow(cfo: Num, capex: Num) -> MetricValue:
    formula = "CFO - |CapEx| (CapEx treated as a positive outflow)"
    inputs = {"cfo": cfo, "capex": capex}
    miss = first_missing(inputs)
    if miss:
        return bad("fcf", Status.MISSING, f"{miss} is missing", "currency", formula, inputs)
    return ok("fcf", cfo - abs(capex), "currency", formula, inputs)  # type: ignore[operator,arg-type]


def cfo_to_pat(cfo: Num, pat: Num) -> MetricValue:
    return _ratio("cfo_to_pat", cfo, pat, unit="x", formula="CFO / PAT", num_label="cfo", den_label="pat")


def effective_tax_rate(tax: Num, pbt: Num) -> MetricValue:
    return _ratio("effective_tax_rate", tax, pbt, unit="pct", formula="tax expense / profit before tax * 100", scale=100,
                  num_label="tax", den_label="pbt")


def accrual_ratio(pat: Num, cfo: Num, total_assets: Num, prior_total_assets: Num = None) -> MetricValue:
    num = None if pat is None or cfo is None else pat - cfo
    m = _ratio("accrual_ratio", num, _avg(total_assets, prior_total_assets), unit="pct",
               formula="(PAT - CFO) / average total assets * 100", scale=100, num_label="pat_minus_cfo", den_label="avg_total_assets")
    return _with_inputs(m, {"pat": pat, "cfo": cfo, "total_assets": total_assets, "prior_total_assets": prior_total_assets})


def payout_ratio(dividends_paid: Num, pat: Num) -> MetricValue:
    d = None if dividends_paid is None else abs(dividends_paid)
    return _ratio("payout_ratio", d, pat, unit="pct", formula="dividends paid / PAT * 100", scale=100,
                  num_label="dividends_paid", den_label="pat")


def _parse(d: str | date) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def ttm(name: str, quarters: Sequence[tuple[str | date, Num]]) -> MetricValue:
    """Sum of the latest four consecutive fiscal quarters (period ends ~3 months apart)."""
    formula = "sum of the latest four consecutive quarters"
    rows = sorted(((_parse(d), v) for d, v in quarters), key=lambda r: r[0])[-4:]
    inputs = {d.isoformat(): v for d, v in rows}
    if len(rows) < 4:
        return bad(name, Status.INSUFFICIENT_HISTORY, f"{len(rows)} quarter(s) available, 4 required", "currency", formula, inputs)
    for (d0, _), (d1, _) in zip(rows, rows[1:], strict=False):
        gap = (d1 - d0).days
        if not 80 <= gap <= 100:
            return bad(name, Status.INSUFFICIENT_HISTORY,
                       f"quarters are not consecutive: {d0.isoformat()} to {d1.isoformat()} is {gap} days", "currency", formula, inputs)
    if any(v is None for _, v in rows):
        return bad(name, Status.MISSING, "a quarter has no reported value", "currency", formula, inputs)
    return ok(name, sum(v for _, v in rows), "currency", formula, inputs)  # type: ignore[misc]


# Ratios that do not describe banks, NBFCs or insurers.
_NOT_FOR_FINANCIALS = {
    "debt_equity", "net_debt_ebitda", "current_ratio", "quick_ratio", "inventory_days", "receivable_days",
    "payable_days", "cash_conversion_cycle", "ebitda_margin", "ebit_margin", "gross_margin", "interest_coverage",
    "fcf", "fcf_growth_yoy", "cfo_to_pat", "roce", "roic", "asset_turnover", "ebitda_growth_yoy", "accrual_ratio",
}


def compute_fundamentals(periods: Sequence[dict[str, Any]], sector_kind: str = "industrial") -> dict[str, MetricValue]:
    """Compute the fundamental bundle from annual periods ordered or unordered by `period_end`.

    `sector_kind` is "industrial" or "financial". For financials, ratios in `_NOT_FOR_FINANCIALS`
    are returned as `not_applicable` so that scoring leaves them out of the denominator.
    """
    rows = sorted(periods, key=lambda p: str(p["period_end"]))
    cur: dict[str, Any] = rows[-1] if rows else {}
    prev: dict[str, Any] = rows[-2] if len(rows) >= 2 else {}
    g = cur.get
    p = prev.get
    out: dict[str, MetricValue] = {}

    def put(m: MetricValue, name: str | None = None) -> None:
        if name and name != m.name:
            m = MetricValue(name, m.value, m.status, m.reason, m.unit, m.formula, m.inputs)
        out[m.name] = m

    for key in ("revenue", "ebitda", "ebit", "pat", "eps"):
        put(_growth(f"{key}_growth_yoy", g(key), p(key) if prev else None))
    for key, yrs in (("revenue", 3), ("pat", 3), ("revenue", 5), ("pat", 5)):
        name = f"{key}_cagr_{yrs}y"
        if len(rows) < yrs + 1:
            put(bad(name, Status.INSUFFICIENT_HISTORY, f"{len(rows)} annual period(s) available, {yrs + 1} required", "pct",
                    f"((end / start) ^ (1 / {yrs}) - 1) * 100"))
        else:
            put(cagr(name, rows[-(yrs + 1)].get(key), g(key), yrs))

    gross = None if g("revenue") is None or g("cogs") is None else g("revenue") - g("cogs")  # type: ignore[operator]
    put(margin("gross_margin", gross, g("revenue")))
    put(margin("ebitda_margin", g("ebitda"), g("revenue")))
    put(margin("ebit_margin", g("ebit"), g("revenue")))
    put(margin("pat_margin", g("pat"), g("revenue")))
    if prev:
        cm, pm = margin("x", g("ebitda"), g("revenue")), margin("x", p("ebitda"), p("revenue"))
        if cm.value is not None and pm.value is not None:
            put(ok("ebitda_margin_change", cm.value - pm.value, "pct", "EBITDA margin - prior-year EBITDA margin (percentage points)",
                   {"current": cm.value, "previous": pm.value}))
        else:
            put(bad("ebitda_margin_change", Status.MISSING, "EBITDA margin unavailable for one of the two years", "pct",
                    "EBITDA margin - prior-year EBITDA margin (percentage points)"))

    put(roe(g("pat"), g("equity"), p("equity")))
    put(roa(g("pat"), g("total_assets"), p("total_assets")))
    put(roce(g("ebit"), g("total_assets"), g("current_liabilities")))
    etr = effective_tax_rate(g("tax"), g("pbt"))
    put(etr)
    put(roic(g("ebit"), etr.value, g("equity"), g("total_debt"), g("cash")))
    put(debt_equity(g("total_debt"), g("equity")))
    put(net_debt_ebitda(g("total_debt"), g("cash"), g("ebitda")))
    put(interest_coverage(g("ebit"), g("interest")))
    put(current_ratio(g("current_assets"), g("current_liabilities")))
    put(quick_ratio(g("current_assets"), g("inventory"), g("current_liabilities")))
    put(asset_turnover(g("revenue"), g("total_assets"), p("total_assets")))
    inv, rec, pay = inventory_days(g("inventory"), g("cogs")), receivable_days(g("receivables"), g("revenue")), payable_days(g("payables"), g("cogs"))
    put(inv)
    put(rec)
    put(pay)
    put(cash_conversion_cycle(inv, rec, pay))
    fcf = free_cash_flow(g("cfo"), g("capex"))
    put(fcf)
    if prev:
        put(_growth("fcf_growth_yoy", fcf.value, free_cash_flow(p("cfo"), p("capex")).value))
    put(cfo_to_pat(g("cfo"), g("pat")))
    put(accrual_ratio(g("pat"), g("cfo"), g("total_assets"), p("total_assets")))
    put(payout_ratio(g("dividends_paid"), g("pat")))

    sc_formula = "(shares outstanding / prior shares outstanding - 1) * 100"
    sc_in = {"current": g("shares_outstanding"), "previous": p("shares_outstanding") if prev else None}
    if first_missing(sc_in) or not sc_in["previous"]:
        put(bad("share_count_change", Status.MISSING, "share count unavailable for two consecutive years", "pct", sc_formula, sc_in))
    else:
        put(ok("share_count_change", (sc_in["current"] / sc_in["previous"] - 1) * 100, "pct", sc_formula, sc_in))

    bvps_formula = "shareholders' equity / shares outstanding"
    if g("equity") is not None and g("shares_outstanding"):
        put(ok("book_value_per_share", g("equity") / g("shares_outstanding"), "price", bvps_formula,  # type: ignore[operator]
               {"equity": g("equity"), "shares_outstanding": g("shares_outstanding")}))
    else:
        put(bad("book_value_per_share", Status.MISSING, "equity or share count missing", "price", bvps_formula))

    if sector_kind == "financial":
        for name in _NOT_FOR_FINANCIALS & out.keys():
            m = out[name]
            out[name] = bad(name, Status.NOT_APPLICABLE, "not applicable to banks, NBFCs or insurers", m.unit, m.formula)
    return out


def compute_consistency(periods: Sequence[dict[str, Any]]) -> dict[str, MetricValue]:
    """Multi-year stability measures from annual periods. Needs at least three annual periods
    (two growth observations) for the variability measures."""
    rows = sorted(periods, key=lambda p: str(p["period_end"]))
    out: dict[str, MetricValue] = {}

    def growths(key: str) -> list[float]:
        vals = []
        for a, b in zip(rows, rows[1:], strict=False):
            g = _growth("g", b.get(key), a.get(key))
            if g.value is not None:
                vals.append(g.value)
        return vals

    def pstdev(xs: list[float]) -> float:
        mu = sum(xs) / len(xs)
        return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5

    for key in ("pat", "revenue"):
        gs = growths(key)
        pos_name = f"positive_{key}_growth_years"
        pos_formula = f"percent of year-on-year {key.upper()} changes that were positive"
        if len(rows) < 3 or len(gs) < 2:
            reason = f"{len(rows)} annual period(s) with {len(gs)} valid growth observation(s); 3 periods required"
            out[pos_name] = bad(pos_name, Status.INSUFFICIENT_HISTORY, reason, "pct", pos_formula)
            if key == "pat":
                out["pat_growth_stdev"] = bad("pat_growth_stdev", Status.INSUFFICIENT_HISTORY, reason, "pct",
                                              "population std of annual PAT growth rates")
            continue
        out[pos_name] = ok(pos_name, sum(1 for g in gs if g > 0) / len(gs) * 100, "pct", pos_formula, {"observations": len(gs)})
        if key == "pat":
            out["pat_growth_stdev"] = ok("pat_growth_stdev", pstdev(gs), "pct", "population std of annual PAT growth rates",
                                         {"observations": len(gs)})

    margins = [m.value for m in (margin("m", r.get("ebitda"), r.get("revenue")) for r in rows) if m.value is not None]
    ms_formula = "population std of annual EBITDA margins (percentage points)"
    out["ebitda_margin_stdev"] = (
        ok("ebitda_margin_stdev", pstdev(margins), "pct", ms_formula, {"observations": len(margins)})
        if len(margins) >= 3
        else bad("ebitda_margin_stdev", Status.INSUFFICIENT_HISTORY, f"{len(margins)} annual margin(s), 3 required", "pct", ms_formula)
    )

    dy_formula = "consecutive latest annual periods with dividends paid > 0"
    if all(r.get("dividends_paid") is None for r in rows):
        out["dividend_years"] = bad("dividend_years", Status.MISSING, "dividends paid not reported", "count", dy_formula)
    else:
        streak = 0
        for r in reversed(rows):
            d = r.get("dividends_paid")
            if d is None or abs(d) <= 0:
                break
            streak += 1
        out["dividend_years"] = ok("dividend_years", streak, "count", dy_formula, {"periods": len(rows)})
    return out
