import pytest

from app.domain import valuation as v
from app.domain.result import Status


def test_pe_ttm():
    m = v.pe(300.0, 15.0)
    assert m.value == pytest.approx(20.0)
    assert m.unit == "x"


def test_pe_negative_earnings_is_not_meaningful_not_cheap():
    m = v.pe(300.0, -5.0)
    assert m.value is None
    assert m.status == Status.NOT_MEANINGFUL
    assert "negative" in m.reason or "non-positive" in m.reason


def test_peg_requires_positive_growth():
    assert v.peg(20.0, 16.0).value == pytest.approx(1.25)
    assert v.peg(20.0, 0.0).status == Status.NOT_MEANINGFUL
    assert v.peg(20.0, -8.0).status == Status.NOT_MEANINGFUL
    assert v.peg(None, 16.0).status == Status.MISSING


def test_peg_records_growth_convention():
    m = v.peg(20.0, 16.0, growth_basis="EPS CAGR 3Y")
    assert "EPS CAGR 3Y" in m.formula


def test_market_cap_and_enterprise_value():
    # price 300 INR, 10 crore shares => 3000 crore
    mc = v.market_cap(300.0, 10.0)
    assert mc.value == pytest.approx(3000.0)
    # EV = 3000 + debt 400 - cash 150 + minority 20
    ev = v.enterprise_value(mc.value, 400.0, 150.0, 20.0)
    assert ev.value == pytest.approx(3270.0)


def test_enterprise_value_treats_missing_minority_interest_as_absent_and_says_so():
    ev = v.enterprise_value(3000.0, 400.0, 150.0, None)
    assert ev.value == pytest.approx(3250.0)
    assert "minority" in (ev.reason or "")


def test_enterprise_value_missing_debt_is_missing():
    assert v.enterprise_value(3000.0, None, 150.0, None).status == Status.MISSING


def test_ev_multiples():
    assert v.ev_multiple("ev_ebitda", 3250.0, 276.0).value == pytest.approx(11.7754, rel=1e-4)
    assert v.ev_multiple("ev_ebitda", 3250.0, -10.0).status == Status.NOT_MEANINGFUL


def test_price_to_book_and_sales():
    assert v.pb(300.0, 110.0).value == pytest.approx(2.7273, rel=1e-4)
    assert v.pb(300.0, -5.0).status == Status.NOT_MEANINGFUL
    assert v.ps(3000.0, 1200.0).value == pytest.approx(2.5)


def test_yields():
    assert v.earnings_yield(15.0, 300.0).value == pytest.approx(5.0)
    assert v.fcf_yield(80.0, 3000.0).value == pytest.approx(2.6667, rel=1e-4)
    assert v.dividend_yield(4.5, 300.0).value == pytest.approx(1.5)


def test_negative_fcf_yield_is_reported_as_negative_with_flag():
    m = v.fcf_yield(-40.0, 3000.0)
    assert m.value == pytest.approx(-1.3333, rel=1e-4)
    assert m.status == Status.OK
    assert v.price_to_fcf(3000.0, -40.0).status == Status.NOT_MEANINGFUL


def test_compute_valuation_bundle():
    out = v.compute_valuation(
        price=300.0,
        shares_outstanding=10.0,
        eps_ttm=15.0,
        book_value_per_share=110.0,
        revenue=1200.0,
        ebitda=276.0,
        ebit=230.0,
        fcf=80.0,
        total_debt=400.0,
        cash=150.0,
        minority_interest=0.0,
        dividend_per_share=4.5,
        eps_growth_pct=16.0,
        growth_basis="EPS growth YoY",
        earnings_basis="TTM",
    )
    assert out["pe"].value == pytest.approx(20.0)
    assert out["peg"].value == pytest.approx(1.25)
    assert out["market_cap"].value == pytest.approx(3000.0)
    assert out["ev_ebitda"].value == pytest.approx(3250.0 / 276.0)
    assert "TTM" in out["pe"].formula


def test_percentile_of_value_within_history():
    hist = [10.0, 12.0, 14.0, 16.0, 18.0]
    assert v.percentile_in_range(14.0, hist).value == pytest.approx(50.0)
    assert v.percentile_in_range(14.0, [10.0, 12.0]).status == Status.INSUFFICIENT_HISTORY
