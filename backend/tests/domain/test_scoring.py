import pytest

from app.domain import scoring as s


def test_pillar_weights_are_equal_and_sum_to_one():
    assert set(s.PILLAR_WEIGHTS) == {"fundamentals", "valuation", "momentum", "risk", "context"}
    assert all(w == pytest.approx(0.2) for w in s.PILLAR_WEIGHTS.values())
    assert sum(s.PILLAR_WEIGHTS.values()) == pytest.approx(1.0)


def test_percentile_rank_mid_rank_and_orientation():
    vals = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0}
    up = s.percentile_ranks(vals, higher_is_better=True)
    assert up == pytest.approx({"a": 10.0, "b": 30.0, "c": 50.0, "d": 70.0, "e": 90.0})
    down = s.percentile_ranks(vals, higher_is_better=False)
    assert down["a"] == pytest.approx(90.0) and down["e"] == pytest.approx(10.0)


def test_percentile_rank_ties_share_rank_and_missing_stays_missing():
    vals = {"a": 1.0, "b": 1.0, "c": 3.0, "d": 4.0, "e": 5.0, "f": None}
    r = s.percentile_ranks(vals)
    assert r["a"] == r["b"] == pytest.approx(20.0)
    assert r["f"] is None


def test_percentile_rank_needs_minimum_sample():
    r = s.percentile_ranks({"a": 1.0, "b": 2.0})
    assert r == {"a": None, "b": None}


def _universe(n=10):
    """Ten instruments with every default metric equal to its index, so ranks are predictable."""
    rows = {}
    for i in range(n):
        rows[f"I{i}"] = {m.name: float(i) for specs in s.DEFAULT_PROFILE.values() for m in specs}
    return rows


def test_overall_is_equal_weight_mean_of_five_pillars():
    res = s.score_universe(_universe())
    r = res["I9"]
    assert r.complete
    assert set(r.pillars) == set(s.PILLAR_WEIGHTS)
    expected = sum(r.pillars[p].score * 0.2 for p in s.PILLAR_WEIGHTS)
    assert r.overall == pytest.approx(expected)
    for p in r.pillars.values():
        assert p.contribution == pytest.approx(p.score * 0.2)


def test_many_metrics_in_one_pillar_cannot_overpower_another():
    # Give one pillar 20 metrics and the others one each; every pillar still contributes 20%.
    profile = {p: [s.MetricSpec(f"{p}_m", True)] for p in s.PILLAR_WEIGHTS}
    profile["momentum"] = [s.MetricSpec(f"mom_{k}", True) for k in range(20)]
    rows = {}
    for i in range(10):
        row = {f"{p}_m": 0.0 for p in s.PILLAR_WEIGHTS}  # everyone tied on the single-metric pillars
        row.update({f"mom_{k}": float(i) for k in range(20)})
        rows[f"I{i}"] = row
    res = s.score_universe(rows, profile=profile)
    top = res["I9"]
    assert top.pillars["momentum"].score == pytest.approx(95.0)
    # four tied pillars at 50 and momentum at 95 => 0.2 * (4 * 50 + 95)
    assert top.overall == pytest.approx(59.0)


def test_lower_is_better_metric_is_inverted():
    rows = _universe()
    res = s.score_universe(rows)
    # In the default risk pillar, volatility is lower-is-better, so the lowest index wins on it.
    detail = {d.name: d for d in res["I0"].pillars["risk"].metrics}
    assert detail["volatility_252d"].score == pytest.approx(95.0)


def test_missing_metric_is_excluded_from_denominator_not_scored_zero():
    rows = _universe()
    names = [m.name for m in s.DEFAULT_PROFILE["fundamentals"]]
    rows["I9"][names[0]] = None
    r = s.score_universe(rows)["I9"]
    p = r.pillars["fundamentals"]
    assert p.metrics_used == len(names) - 1
    assert p.metrics_total == len(names)
    used = [d.score for d in p.metrics if d.score is not None]
    assert len(used) == len(names) - 1
    assert p.score == pytest.approx(sum(used) / len(used))  # mean of the remaining metrics only
    assert next(d for d in p.metrics if d.name == names[0]).score is None
    assert names[0] in r.missing_metrics


def test_pillar_below_coverage_threshold_marks_total_incomplete():
    rows = _universe()
    for m in s.DEFAULT_PROFILE["valuation"]:
        rows["I5"][m.name] = None
    r = s.score_universe(rows)["I5"]
    assert r.pillars["valuation"].score is None
    assert r.pillars["valuation"].sufficient is False
    assert r.complete is False
    assert r.overall is None
    assert r.partial_overall is not None
    assert r.pillars_available == 4
    assert "valuation" in r.disclosure


def test_too_few_pillars_gives_no_partial_score():
    rows = _universe()
    for p in ("valuation", "fundamentals", "context"):
        for m in s.DEFAULT_PROFILE[p]:
            rows["I5"][m.name] = None
    r = s.score_universe(rows)["I5"]
    assert r.partial_overall is None
    assert s.stance(r) == "Insufficient data"


def test_scoring_is_deterministic():
    a = s.score_universe(_universe())
    b = s.score_universe(dict(reversed(list(_universe().items()))))
    assert {k: v.overall for k, v in a.items()} == {k: v.overall for k, v in b.items()}


def test_peer_group_ranking_for_valuation():
    rows = _universe(12)
    groups = {f"I{i}": ("A" if i < 6 else "B") for i in range(12)}
    res = s.score_universe(rows, peer_groups=groups)
    # pe is lower-is-better and ranked inside the group: I0 is cheapest in A, I6 cheapest in B.
    pe = lambda k: {d.name: d for d in res[k].pillars["valuation"].metrics}["pe"]  # noqa: E731
    assert pe("I0").score == pytest.approx(pe("I6").score)
    assert pe("I0").peer_scope == "sector"


def test_small_peer_group_falls_back_to_universe_and_says_so():
    rows = _universe(10)
    groups = {f"I{i}": ("A" if i < 8 else "B") for i in range(10)}
    res = s.score_universe(rows, peer_groups=groups)
    pe = {d.name: d for d in res["I9"].pillars["valuation"].metrics}["pe"]
    assert pe.peer_scope == "universe"


def test_data_completeness_and_confidence_rules():
    rows = _universe()
    r = s.score_universe(rows)["I3"]
    assert r.completeness_pct == pytest.approx(100.0)
    assert s.confidence(100.0, 1) == "High"
    assert s.confidence(100.0, 10) == "Medium"
    assert s.confidence(70.0, 1) == "Medium"
    assert s.confidence(40.0, 1) == "Low"
    assert s.confidence(100.0, None) == "Low"


def test_stance_bands():
    res = s.score_universe(_universe())
    assert s.stance(res["I9"]) == "Favorable"
    assert s.stance(res["I0"]) == "Unfavorable"
    assert s.stance(res["I5"]) == "Mixed"


def test_top_positive_and_negative_metrics_reported():
    r = s.score_universe(_universe())["I9"]
    assert len(r.top_positive) == 3 and len(r.top_negative) == 3
    assert r.top_positive[0]["score"] >= r.top_negative[0]["score"]
    assert r.version == s.VERSION


def test_inapplicable_metrics_leave_the_denominator_and_do_not_reduce_coverage():
    rows = _universe()
    names = [m.name for m in s.DEFAULT_PROFILE["fundamentals"]]
    not_for_banks = set(names[:5])  # five of eight fundamentals metrics do not apply to this instrument
    for n in not_for_banks:
        rows["I4"][n] = None
    without = s.score_universe(rows)["I4"]
    assert without.pillars["fundamentals"].sufficient is False, "treated as missing, coverage is 3 of 8"
    res = s.score_universe(rows, inapplicable={"I4": not_for_banks})["I4"]
    p = res.pillars["fundamentals"]
    assert p.metrics_total == 3 and p.metrics_used == 3 and p.sufficient and res.complete
    assert not (not_for_banks & set(res.missing_metrics))
    assert res.completeness_pct == pytest.approx(100.0)
    assert {d.name for d in p.metrics if d.peer_scope == "not_applicable"} == not_for_banks
