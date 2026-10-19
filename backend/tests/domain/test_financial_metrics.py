"""Hand-calculated fixtures for fundamental ratios. All figures in INR crore."""

import pytest

from app.domain import financial_metrics as fm
from app.domain.result import Status


def test_growth_is_percent_change_on_positive_base():
    m = fm.growth("revenue_growth_yoy", 1200.0, 1000.0)
    assert m.value == pytest.approx(20.0)
    assert m.unit == "pct"
    assert m.inputs == {"current": 1200.0, "previous": 1000.0}


def test_growth_from_negative_base_is_not_meaningful():
    m = fm.growth("pat_growth_yoy", 50.0, -20.0)
    assert m.value is None
    assert m.status == Status.NOT_MEANINGFUL
    assert "non-positive" in m.reason


def test_growth_with_missing_input_is_missing_not_zero():
    m = fm.growth("pat_growth_yoy", None, 100.0)
    assert m.value is None
    assert m.status == Status.MISSING


def test_cagr_three_years():
    # 1000 -> 1728 over 3 years is exactly 20% a year
    m = fm.cagr("revenue_cagr_3y", 1000.0, 1728.0, 3)
    assert m.value == pytest.approx(20.0)


def test_cagr_rejects_non_positive_endpoints():
    assert fm.cagr("x", -10.0, 100.0, 3).status == Status.NOT_MEANINGFUL
    assert fm.cagr("x", 100.0, 0.0, 3).status == Status.NOT_MEANINGFUL


def test_margin_and_zero_revenue():
    assert fm.margin("ebitda_margin", 250.0, 1000.0).value == pytest.approx(25.0)
    assert fm.margin("ebitda_margin", 250.0, 0.0).status == Status.NOT_MEANINGFUL


def test_roe_uses_average_equity():
    # PAT 150, equity 900 -> 1100, average 1000 => 15%
    m = fm.roe(150.0, 1100.0, 900.0)
    assert m.value == pytest.approx(15.0)


def test_roe_negative_equity_not_meaningful():
    m = fm.roe(150.0, -50.0, -100.0)
    assert m.status == Status.NOT_MEANINGFUL


def test_roce_is_ebit_over_capital_employed():
    # EBIT 200, total assets 2000, current liabilities 500 => 200 / 1500
    m = fm.roce(200.0, 2000.0, 500.0)
    assert m.value == pytest.approx(13.3333, rel=1e-4)


def test_roic_uses_tax_adjusted_ebit_and_invested_capital():
    # NOPAT = 200 * (1 - 0.25) = 150; invested = equity 1000 + debt 400 - cash 200 = 1200
    m = fm.roic(200.0, 25.0, 1000.0, 400.0, 200.0)
    assert m.value == pytest.approx(12.5)


def test_debt_equity_and_net_debt_ebitda():
    assert fm.debt_equity(400.0, 1000.0).value == pytest.approx(0.4)
    # net debt = 400 - 150 = 250; EBITDA 250 => 1.0x
    assert fm.net_debt_ebitda(400.0, 150.0, 250.0).value == pytest.approx(1.0)
    assert fm.net_debt_ebitda(400.0, 150.0, -10.0).status == Status.NOT_MEANINGFUL


def test_interest_coverage_with_no_interest_is_not_meaningful():
    assert fm.interest_coverage(200.0, 40.0).value == pytest.approx(5.0)
    m = fm.interest_coverage(200.0, 0.0)
    assert m.value is None and m.status == Status.NOT_MEANINGFUL


def test_liquidity_ratios():
    assert fm.current_ratio(600.0, 400.0).value == pytest.approx(1.5)
    # quick = (600 - 200 inventory) / 400
    assert fm.quick_ratio(600.0, 200.0, 400.0).value == pytest.approx(1.0)


def test_working_capital_days_and_cash_conversion_cycle():
    inv = fm.inventory_days(200.0, 730.0)  # 200 / 730 * 365 = 100
    rec = fm.receivable_days(150.0, 1095.0)  # 150 / 1095 * 365 = 50
    pay = fm.payable_days(120.0, 730.0)  # 60
    assert inv.value == pytest.approx(100.0)
    assert rec.value == pytest.approx(50.0)
    assert pay.value == pytest.approx(60.0)
    assert fm.cash_conversion_cycle(inv, rec, pay).value == pytest.approx(90.0)


def test_cash_conversion_cycle_missing_leg_is_missing():
    inv = fm.inventory_days(None, 730.0)
    rec = fm.receivable_days(150.0, 1095.0)
    pay = fm.payable_days(120.0, 730.0)
    assert fm.cash_conversion_cycle(inv, rec, pay).status == Status.MISSING


def test_free_cash_flow_sign_convention_capex_is_positive_outflow():
    # CFO 300, capex spend 120 => FCF 180. A negative capex input (cash-flow sign) is normalised.
    assert fm.free_cash_flow(300.0, 120.0).value == pytest.approx(180.0)
    assert fm.free_cash_flow(300.0, -120.0).value == pytest.approx(180.0)


def test_cfo_to_pat_requires_positive_pat():
    assert fm.cfo_to_pat(180.0, 150.0).value == pytest.approx(1.2)
    assert fm.cfo_to_pat(180.0, -10.0).status == Status.NOT_MEANINGFUL


def test_effective_tax_rate():
    assert fm.effective_tax_rate(50.0, 200.0).value == pytest.approx(25.0)
    assert fm.effective_tax_rate(50.0, -5.0).status == Status.NOT_MEANINGFUL


def test_accrual_ratio():
    # (PAT 150 - CFO 180) / avg total assets 2000 = -1.5%
    assert fm.accrual_ratio(150.0, 180.0, 2100.0, 1900.0).value == pytest.approx(-1.5)


def test_payout_ratio():
    assert fm.payout_ratio(45.0, 150.0).value == pytest.approx(30.0)
    assert fm.payout_ratio(45.0, -1.0).status == Status.NOT_MEANINGFUL


def test_ttm_sums_four_consecutive_quarters():
    q = [("2025-12-31", 100.0), ("2026-03-31", 110.0), ("2026-06-30", 120.0), ("2026-09-30", 130.0)]
    m = fm.ttm("revenue_ttm", q)
    assert m.value == pytest.approx(460.0)


def test_ttm_detects_missing_quarter():
    q = [("2025-09-30", 90.0), ("2026-03-31", 110.0), ("2026-06-30", 120.0), ("2026-09-30", 130.0)]
    m = fm.ttm("revenue_ttm", q)
    assert m.value is None
    assert m.status == Status.INSUFFICIENT_HISTORY
    assert "consecutive" in m.reason


def test_ttm_needs_four_quarters():
    assert fm.ttm("x", [("2026-06-30", 1.0)]).status == Status.INSUFFICIENT_HISTORY


ANNUAL = [
    {
        "period_end": "2024-03-31", "revenue": 1000.0, "ebitda": 220.0, "ebit": 180.0, "pat": 120.0,
        "interest": 30.0, "tax": 40.0, "pbt": 160.0, "equity": 900.0, "total_debt": 400.0,
        "cash": 100.0, "total_assets": 1900.0, "current_assets": 550.0, "current_liabilities": 450.0,
        "inventory": 180.0, "receivables": 140.0, "payables": 110.0, "cogs": 650.0,
        "cfo": 150.0, "capex": 90.0, "dividends_paid": 30.0, "shares_outstanding": 10.0,
    },
    {
        "period_end": "2025-03-31", "revenue": 1200.0, "ebitda": 276.0, "ebit": 230.0, "pat": 150.0,
        "interest": 40.0, "tax": 50.0, "pbt": 200.0, "equity": 1100.0, "total_debt": 400.0,
        "cash": 150.0, "total_assets": 2100.0, "current_assets": 600.0, "current_liabilities": 400.0,
        "inventory": 200.0, "receivables": 150.0, "payables": 120.0, "cogs": 730.0,
        "cfo": 180.0, "capex": 100.0, "dividends_paid": 45.0, "shares_outstanding": 10.0,
    },
]


def test_compute_fundamentals_bundle_for_industrial():
    out = fm.compute_fundamentals(ANNUAL, sector_kind="industrial")
    assert out["revenue_growth_yoy"].value == pytest.approx(20.0)
    assert out["ebitda_margin"].value == pytest.approx(23.0)
    assert out["pat_margin"].value == pytest.approx(12.5)
    assert out["roe"].value == pytest.approx(15.0)
    assert out["debt_equity"].value == pytest.approx(400.0 / 1100.0)
    assert out["interest_coverage"].value == pytest.approx(5.75)
    assert out["fcf"].value == pytest.approx(80.0)
    assert out["cfo_to_pat"].value == pytest.approx(1.2)
    assert out["share_count_change"].value == pytest.approx(0.0)
    # only two years supplied, so a 3-year CAGR cannot be computed
    assert out["revenue_cagr_3y"].status == Status.INSUFFICIENT_HISTORY


def test_financial_sector_marks_industrial_ratios_not_applicable():
    out = fm.compute_fundamentals(ANNUAL, sector_kind="financial")
    for name in ("debt_equity", "current_ratio", "inventory_days", "net_debt_ebitda", "ebitda_margin", "fcf"):
        assert out[name].status == Status.NOT_APPLICABLE, name
        assert out[name].value is None
    assert out["roe"].status == Status.OK


def test_every_metric_exposes_formula_and_inputs():
    out = fm.compute_fundamentals(ANNUAL, sector_kind="industrial")
    for name, m in out.items():
        assert m.formula, name
        assert m.name == name


def _years(pats, revs=None, margins=None, divs=None):
    out = []
    for i, pat in enumerate(pats):
        rev = revs[i] if revs else 1000.0 + 100 * i
        out.append({"period_end": f"{2020 + i}-03-31", "pat": pat, "revenue": rev,
                    "ebitda": (margins[i] / 100 * rev) if margins else 0.2 * rev,
                    "dividends_paid": divs[i] if divs else None})
    return out


def test_consistency_growth_variability_and_positive_years():
    # PAT growth: +10%, +10%, +10% => stdev 0, growth in 100% of years
    out = fm.compute_consistency(_years([100.0, 110.0, 121.0, 133.1]))
    assert out["pat_growth_stdev"].value == pytest.approx(0.0, abs=1e-9)
    assert out["positive_pat_growth_years"].value == pytest.approx(100.0)
    # one down year out of three
    out2 = fm.compute_consistency(_years([100.0, 120.0, 90.0, 99.0]))
    assert out2["positive_pat_growth_years"].value == pytest.approx(200 / 3)


def test_consistency_margin_variability():
    out = fm.compute_consistency(_years([1, 1, 1, 1], margins=[18.0, 20.0, 22.0, 20.0]))
    # population stdev of 18, 20, 22, 20 = sqrt(2)
    assert out["ebitda_margin_stdev"].value == pytest.approx(2 ** 0.5)


def test_consistency_dividend_streak_counts_back_from_latest_year():
    out = fm.compute_consistency(_years([1, 1, 1, 1, 1], divs=[5.0, 0.0, 4.0, 6.0, 7.0]))
    assert out["dividend_years"].value == 3
    assert fm.compute_consistency(_years([1, 1, 1], divs=[None, None, None]))["dividend_years"].status == Status.MISSING


def test_consistency_needs_three_years():
    out = fm.compute_consistency(_years([100.0, 110.0]))
    assert out["pat_growth_stdev"].status == Status.INSUFFICIENT_HISTORY
