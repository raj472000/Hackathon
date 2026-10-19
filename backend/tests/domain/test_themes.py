import pytest
from pydantic import ValidationError

from app.domain import theme_dsl as dsl
from app.domain.themes_builtin import BUILTIN_THEMES, CATEGORIES


def rows():
    """Fixture universe (synthetic, test-only). Metric value i for instrument Si unless overridden."""
    out = {}
    for i in range(12):
        r = {name: float(i) for name in dsl.METRIC_CATALOG}
        r.update({"instrument_type": "EQ", "sector": "IT" if i % 2 else "Banks", "sector_kind": "industrial",
                  "symbol": f"S{i}", "governance_flag": False})
        out[f"S{i}"] = r
    return out


def spec(**kw):
    base = {"name": "t", "universe": {"exchange": "NSE", "instrument_types": ["EQ"]},
            "filters": [], "rank_by": [{"metric": "return_6m", "direction": "desc"}], "limit": 5}
    base.update(kw)
    return dsl.ThemeSpec.model_validate(base)


def test_unknown_metric_is_rejected():
    with pytest.raises(ValidationError) as e:
        spec(filters=[{"metric": "users; DROP TABLE x", "op": "gt", "value": 1}])
    assert "unknown metric" in str(e.value)


def test_unknown_operator_and_missing_between_bound_rejected():
    with pytest.raises(ValidationError):
        spec(filters=[{"metric": "roe", "op": "like", "value": 1}])
    with pytest.raises(ValidationError):
        spec(filters=[{"metric": "roe", "op": "between", "value": 1}])


def test_limit_is_bounded():
    with pytest.raises(ValidationError):
        spec(limit=100000)


def test_filter_and_rank():
    res = dsl.evaluate(spec(filters=[{"metric": "roe", "op": "gte", "value": 6}]), rows())
    assert [c.instrument_id for c in res.constituents] == ["S11", "S10", "S9", "S8", "S7"]
    assert [c.rank for c in res.constituents] == [1, 2, 3, 4, 5]
    assert res.eligible_count == 6


def test_missing_filter_metric_excludes_with_reason_not_zero():
    data = rows()
    data["S11"]["roe"] = None
    res = dsl.evaluate(spec(filters=[{"metric": "roe", "op": "lte", "value": 100}]), data)
    assert "S11" not in [c.instrument_id for c in res.constituents]
    ex = {e.instrument_id: e for e in res.exclusions}
    assert ex["S11"].reason == "missing metric: roe"


def test_failed_filter_recorded_as_exclusion():
    res = dsl.evaluate(spec(filters=[{"metric": "debt_equity", "op": "lt", "value": 3}]), rows())
    ex = {e.instrument_id: e.reason for e in res.exclusions}
    assert ex["S5"] == "failed: debt_equity lt 3 (value 5)"


def test_between_and_in_operators():
    res = dsl.evaluate(spec(filters=[{"metric": "pe", "op": "between", "value": 3, "value2": 5}], limit=10), rows())
    assert sorted(c.instrument_id for c in res.constituents) == ["S3", "S4", "S5"]
    s2 = spec(universe={"exchange": "NSE", "instrument_types": ["EQ"], "sectors": ["IT"]}, limit=50)
    assert all(int(c.instrument_id[1:]) % 2 == 1 for c in dsl.evaluate(s2, rows()).constituents)


def test_multi_metric_rank_is_equal_weight_of_oriented_percentiles():
    sp = spec(rank_by=[{"metric": "return_6m", "direction": "desc"}, {"metric": "volatility_252d", "direction": "asc"}], limit=12)
    res = dsl.evaluate(sp, rows())
    # the two metrics rank in exactly opposite orders here, so every instrument scores 50
    assert all(c.score == pytest.approx(50.0) for c in res.constituents)
    # deterministic tie-break by symbol
    assert [c.instrument_id for c in res.constituents][:3] == ["S0", "S1", "S10"]


def test_governance_red_flags_excluded_by_default():
    data = rows()
    data["S11"]["governance_flag"] = True
    res = dsl.evaluate(spec(), data)
    assert "S11" not in [c.instrument_id for c in res.constituents]
    assert dsl.evaluate(spec(exclude_red_flags=False), data).constituents[0].instrument_id == "S11"


def test_evaluation_is_deterministic_and_carries_version():
    a, b = dsl.evaluate(spec(), rows()), dsl.evaluate(spec(), rows())
    assert [c.instrument_id for c in a.constituents] == [c.instrument_id for c in b.constituents]
    assert a.dsl_version == dsl.VERSION


def test_every_builtin_theme_validates_and_evaluates():
    assert len(BUILTIN_THEMES) >= 50
    assert {t.category for t in BUILTIN_THEMES} == set(CATEGORIES)
    slugs = [t.slug for t in BUILTIN_THEMES]
    assert len(slugs) == len(set(slugs))
    for t in BUILTIN_THEMES:
        res = dsl.evaluate(t.spec, rows())
        assert res.dsl_version == dsl.VERSION, t.slug
        assert t.description and t.methodology, t.slug


def test_builtin_themes_requiring_unsourced_data_are_flagged_unavailable():
    by = {t.slug: t for t in BUILTIN_THEMES}
    assert by["positive-earnings-revisions"].requires == ["analyst_estimates"]
    assert by["positive-earnings-revisions"].available is False


def test_interpret_natural_language_into_reviewable_spec():
    text = "NSE companies with improving 3-year revenue and ROCE, low debt, positive 6-month relative strength, and moderate volatility"
    out = dsl.interpret(text)
    metrics = {f.metric for f in out.spec.filters}
    assert {"revenue_cagr_3y", "roce", "debt_equity", "rel_strength_6m", "volatility_252d"} <= metrics
    assert out.spec.universe.exchange == "NSE"
    # vague words must be surfaced for confirmation rather than silently fixed
    joined = " ".join(out.ambiguities).lower()
    assert "low debt" in joined and "moderate volatility" in joined
    assert out.requires_confirmation is True


def test_interpret_unrecognised_text_returns_no_filters_and_explains():
    out = dsl.interpret("stocks my uncle likes")
    assert out.spec.filters == []
    assert out.ambiguities
