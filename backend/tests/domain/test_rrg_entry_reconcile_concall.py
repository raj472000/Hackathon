from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.domain import concall_score as cs
from app.domain import entry_setup as es
from app.domain import reconcile as rc
from app.domain import rrg
from app.domain.provenance import DataPoint


def series(values, start="2023-01-02"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.bdate_range(start, periods=len(values)))


# ---------------- RRG ----------------

def test_sector_identical_to_benchmark_sits_at_centre():
    b = series(np.linspace(100, 150, 400))
    out = rrg.compute_rrg({"X": b.copy()}, b)
    last = out.sectors["X"].trail[-1]
    assert last.rs_ratio == pytest.approx(100.0)
    assert last.rs_momentum == pytest.approx(100.0)


def test_accelerating_outperformer_is_leading_and_fading_one_is_not():
    n = 400
    b = series(np.full(n, 100.0))
    t = np.arange(n)
    leader = series(100 * np.exp(0.00002 * t**2 / 2))  # relative strength rising at an increasing rate
    laggard = series(100 * np.exp(-0.00002 * t**2 / 2))
    out = rrg.compute_rrg({"L": leader, "G": laggard}, b)
    assert out.sectors["L"].quadrant == "Leading"
    assert out.sectors["G"].quadrant == "Lagging"


def test_quadrant_mapping():
    assert rrg.quadrant(101, 101) == "Leading"
    assert rrg.quadrant(101, 99) == "Weakening"
    assert rrg.quadrant(99, 99) == "Lagging"
    assert rrg.quadrant(99, 101) == "Improving"


def test_trail_length_and_weekly_dates():
    b = series(np.linspace(100, 120, 500))
    s = series(np.linspace(100, 160, 500))
    out = rrg.compute_rrg({"S": s}, b, params=rrg.RRGParams(trail=8))
    trail = out.sectors["S"].trail
    assert len(trail) == 8
    assert all(a.date < c.date for a, c in zip(trail, trail[1:], strict=False))
    assert out.params.trail == 8 and out.version == rrg.VERSION


def test_insufficient_history_is_flagged_not_plotted():
    b = series(np.linspace(100, 120, 500))
    short = series(np.linspace(100, 110, 30), start="2024-10-01")
    out = rrg.compute_rrg({"S": short}, b)
    assert out.sectors["S"].trail == []
    assert out.sectors["S"].status == "insufficient_history"


def test_missing_benchmark_raises_clear_error():
    with pytest.raises(rrg.RRGError):
        rrg.compute_rrg({"S": series([1, 2, 3])}, series([]))


def test_rrg_is_deterministic_and_uses_only_past_data():
    b = series(np.linspace(100, 130, 500))
    s = series(100 + 10 * np.sin(np.arange(500) / 30) + np.arange(500) * 0.1)
    full = rrg.compute_rrg({"S": s}, b)
    cut = rrg.compute_rrg({"S": s.iloc[:450]}, b.iloc[:450])
    by_date = {p.date: p for p in full.sectors["S"].trail}
    p = cut.sectors["S"].trail[-2]  # a completed week present in both runs
    assert by_date[p.date].rs_ratio == pytest.approx(p.rs_ratio) if p.date in by_date else True


# ---------------- entry setup ----------------

BASE = {"close": 110.0, "sma_50": 105.0, "sma_200": 95.0, "return_6m": 18.0, "rel_strength_6m": 6.0,
        "rsi_14": 58.0, "atr_14": 2.5, "valuation_score": 62.0, "fundamentals_score": 70.0,
        "risk_label": "Moderate", "avg_traded_value": 5e8, "completeness_pct": 95.0, "data_age_days": 1}


def test_all_conditions_pass_gives_setup_favorable():
    r = es.evaluate(BASE)
    assert r.state == "Setup favorable"
    assert all(c.passed for c in r.conditions)
    assert r.invalidation and "SMA200" in r.invalidation["condition"]
    assert r.entry_zone is None and "backtested" in r.entry_zone_reason


def test_one_confirmation_missing_gives_watch_and_lists_what_to_wait_for():
    r = es.evaluate({**BASE, "valuation_score": 30.0})
    assert r.state == "Watch"
    assert any("valuation" in w.lower() for w in r.wait_for)


def test_broken_trend_or_very_high_risk_is_unfavorable():
    assert es.evaluate({**BASE, "close": 90.0, "return_6m": -12.0}).state == "Unfavorable"
    assert es.evaluate({**BASE, "risk_label": "Very High"}).state == "Unfavorable"


def test_stale_or_missing_critical_data_is_insufficient():
    assert es.evaluate({**BASE, "data_age_days": 30}).state == "Insufficient data"
    assert es.evaluate({**BASE, "close": None}).state == "Insufficient data"
    r = es.evaluate({**BASE, "fundamentals_score": None, "valuation_score": None, "completeness_pct": 40.0})
    assert r.state == "Insufficient data"
    assert any(c.passed is None for c in r.conditions)


def test_rules_are_disclosed():
    r = es.evaluate(BASE)
    assert r.rules and r.version == es.VERSION
    assert all(c.rule for c in r.conditions)
    assert "not" in r.disclaimer.lower()


# ---------------- reconcile ----------------

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def dp(provider, value, field="close"):
    return DataPoint(field=field, value=value, provider=provider, retrieved_at=NOW, as_of=NOW, currency="INR")


def test_nse_wins_and_broker_only_fills_gaps():
    r = rc.reconcile("close", [dp("kite_mcp", 100.0), dp("nse_mcp", 100.0)])
    assert r.provider == "nse_mcp" and r.conflict is None and r.status == "ok"
    r2 = rc.reconcile("close", [dp("kite_mcp", 100.0), dp("nse_mcp", None)])
    assert r2.provider == "kite_mcp" and r2.value == 100.0 and r2.filled_from_fallback is True


def test_disagreement_beyond_tolerance_is_flagged_and_precedence_kept():
    r = rc.reconcile("close", [dp("nse_mcp", 100.0), dp("kite_mcp", 103.0)])
    assert r.value == 100.0 and r.provider == "nse_mcp"
    assert r.conflict is not None
    assert r.conflict.diff_pct == pytest.approx(3.0)
    assert r.conflict.values == {"nse_mcp": 100.0, "kite_mcp": 103.0}
    assert r.status == "conflict"


def test_difference_within_tolerance_is_not_a_conflict():
    assert rc.reconcile("close", [dp("nse_mcp", 100.0), dp("kite_mcp", 100.04)]).conflict is None


def test_live_price_tolerance_is_wider_than_close():
    assert rc.tolerance_pct("last_price") > rc.tolerance_pct("close")
    assert rc.reconcile("last_price", [dp("nse_mcp", 100.0, "last_price"), dp("kite_mcp", 100.4, "last_price")]).conflict is None


def test_no_source_has_value_gives_missing_with_reason():
    r = rc.reconcile("close", [dp("nse_mcp", None), dp("kite_mcp", None)])
    assert r.value is None and r.status == "missing" and r.reason


def test_field_specific_precedence_override():
    r = rc.reconcile("lot_size", [dp("nse_mcp", 1.0, "lot_size"), dp("kite_mcp", 1.0, "lot_size")],
                     precedence={"lot_size": ["kite_mcp", "nse_mcp"]})
    assert r.provider == "kite_mcp"


def test_reconcile_bars_aligns_by_date_and_reports_conflicts():
    idx = pd.to_datetime(["2026-10-07", "2026-10-08", "2026-10-09"])
    nse = pd.DataFrame({"close": [100.0, 101.0, np.nan], "volume": [10.0, 11.0, np.nan]}, index=idx)
    kite = pd.DataFrame({"close": [100.0, 105.0, 102.0], "volume": [10.0, 11.0, 12.0]}, index=idx)
    merged, conflicts, coverage = rc.reconcile_bars({"nse_mcp": nse, "kite_mcp": kite})
    assert merged["close"].tolist() == [100.0, 101.0, 102.0]
    assert merged["provider"].tolist() == ["nse_mcp", "nse_mcp", "kite_mcp"]
    assert len(conflicts) == 1 and conflicts[0]["date"] == "2026-10-08" and conflicts[0]["field"] == "close"
    assert coverage == {"nse_mcp": 2, "kite_mcp": 1, "overlap": 2, "conflicts": 1}


# ---------------- concall follow-through ----------------

def c(verdict, importance="medium", grounded=True):
    return cs.Assessment(commitment="x", verdict=verdict, importance=importance, evidence_grounded=grounded)


def test_score_is_importance_weighted_mean_of_verdict_credit():
    # delivered high (3 * 1.0) + not_delivered medium (2 * 0) + partially low (1 * 0.5) = 3.5 / 6
    r = cs.score([c("delivered", "high"), c("not_delivered", "medium"), c("partially_delivered", "low")])
    assert r.score == pytest.approx(58.3, abs=0.05)
    assert r.counts == {"delivered": 1, "not_delivered": 1, "partially_delivered": 1}


def test_all_delivered_is_100_and_none_delivered_is_0():
    assert cs.score([c("delivered")] * 4).score == pytest.approx(100.0)
    assert cs.score([c("not_delivered")] * 4).score == pytest.approx(0.0)


def test_unaddressed_commitments_count_against_the_company():
    r = cs.score([c("delivered"), c("delivered"), c("not_addressed"), c("not_addressed")])
    assert r.score == pytest.approx(50.0)


def test_ungrounded_assessments_are_excluded_and_lower_confidence():
    r = cs.score([c("delivered"), c("delivered"), c("delivered"), c("delivered", grounded=False)])
    assert r.assessed == 3 and r.excluded_ungrounded == 1
    assert r.confidence in ("Medium", "Low")


def test_too_few_commitments_is_insufficient():
    r = cs.score([c("delivered")])
    assert r.score is None and r.status == "insufficient_commitments"


def test_label_bands():
    assert cs.label(85) == "Strong follow-through"
    assert cs.label(60) == "Mixed follow-through"
    assert cs.label(30) == "Weak follow-through"
    assert cs.label(None) == "Insufficient data"


def test_quote_grounding_normalises_whitespace_and_case_but_rejects_inventions():
    text = "We expect to commission the   new Dahej plant by\nQ2 FY26, adding 40 KTPA."
    assert cs.quote_is_grounded("commission the new Dahej plant by Q2 FY26", text)
    assert not cs.quote_is_grounded("commission the new Dahej plant by Q1 FY26", text)
    assert not cs.quote_is_grounded("", text)
    assert not cs.quote_is_grounded("plant", text)  # too short to count as evidence
