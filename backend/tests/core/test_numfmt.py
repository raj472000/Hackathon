from decimal import Decimal

from app.core.numfmt import round_floats, tidy_decimals


def test_tidy_decimals_rounds_long_decimals_in_llm_text() -> None:
    text = ("better 12m return (-4.058192955589579 vs -31.739065383272646); "
            "P/B 1.8115585812876573 vs 2.600357620491767; price 683.3")
    assert tidy_decimals(text) == "better 12m return (-4.06 vs -31.74); P/B 1.81 vs 2.60; price 683.3"


def test_tidy_decimals_rounds_half_up() -> None:
    assert tidy_decimals("ratio 2.125 and 2.135") == "ratio 2.13 and 2.14"


def test_tidy_decimals_leaves_timestamps_versions_and_short_numbers() -> None:
    text = "as of 2026-10-29T16:00:27.123456+05:30, model v1.2.345, ID AB1.2345, 13.15, 1,386.6, 7"
    assert tidy_decimals(text) == text


def test_round_floats_walks_nested_evidence() -> None:
    ev = [{"id": "M1", "data": {"pe": 13.152988660388237, "volume": 120, "ok": True, "d": Decimal("1.23456"),
                                "top": [{"chg": -8.030725593114752}], "sym": "HDFCBANK"}}]
    out = round_floats(ev)
    assert out == [{"id": "M1", "data": {"pe": 13.15, "volume": 120, "ok": True, "d": Decimal("1.23"),
                                         "top": [{"chg": -8.03}], "sym": "HDFCBANK"}}]
    assert ev[0]["data"]["pe"] == 13.152988660388237  # input not mutated


def test_filter_description_caps_decimals() -> None:
    from app.domain.theme_dsl import Filter

    assert Filter(metric="roce", op="gt", value=15.333333).describe().endswith("> 15.33")
