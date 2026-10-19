import numpy as np
import pandas as pd
import pytest

from app.domain import adjust, risk
from app.domain import technicals as t
from app.domain.result import Status


def frame(closes, highs=None, lows=None, volumes=None, start="2024-01-01"):
    n = len(closes)
    idx = pd.bdate_range(start, periods=n)
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": np.asarray(highs, dtype=float) if highs is not None else c,
            "low": np.asarray(lows, dtype=float) if lows is not None else c,
            "close": c,
            "volume": np.asarray(volumes, dtype=float) if volumes is not None else np.full(n, 1000.0),
        },
        index=idx,
    )


def test_period_return_uses_bar_count():
    df = frame(np.linspace(100, 200, 300))
    # 21 trading bars back from the last close
    expected = (df.close.iloc[-1] / df.close.iloc[-22] - 1) * 100
    assert t.period_return(df.close, "1M").value == pytest.approx(expected)


def test_period_return_insufficient_history():
    df = frame(np.linspace(100, 110, 30))
    m = t.period_return(df.close, "12M")
    assert m.value is None and m.status == Status.INSUFFICIENT_HISTORY


def test_sma_simple_average_of_last_n():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    assert t.sma(s, 5).value == pytest.approx(8.0)
    assert t.sma(s, 20).status == Status.INSUFFICIENT_HISTORY


def test_rsi_wilder_known_series():
    # Classic Wilder worksheet series; RSI(14) after the 15th close is 70.46
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
              45.89, 46.03, 45.61, 46.28, 46.28]
    assert t.rsi(pd.Series(closes), 14).value == pytest.approx(70.46, abs=0.05)


def test_rsi_all_gains_is_100_and_flat_is_not_meaningful():
    assert t.rsi(pd.Series(np.arange(1.0, 40.0)), 14).value == pytest.approx(100.0)
    assert t.rsi(pd.Series(np.full(40, 5.0)), 14).status == Status.NOT_MEANINGFUL


def test_macd_is_zero_on_constant_series_and_positive_in_uptrend():
    assert t.macd(pd.Series(np.full(60, 100.0)))["macd"].value == pytest.approx(0.0)
    up = t.macd(pd.Series(np.linspace(100, 200, 80)))
    assert up["macd"].value > 0
    assert up["macd_hist"].value == pytest.approx(up["macd"].value - up["macd_signal"].value)


def test_atr_constant_range():
    n = 40
    c = np.full(n, 100.0)
    df = frame(c, highs=c + 2, lows=c - 2)
    # true range is 4 every bar
    assert t.atr(df, 14).value == pytest.approx(4.0)


def test_bollinger_bands_width():
    s = pd.Series([10.0, 12.0] * 10)
    b = t.bollinger(s, 20, 2.0)
    assert b["bb_mid"].value == pytest.approx(11.0)
    # population std of alternating 10/12 is 1.0
    assert b["bb_upper"].value == pytest.approx(13.0)
    assert b["bb_lower"].value == pytest.approx(9.0)


def test_adx_strong_trend_scores_high():
    c = np.linspace(100, 200, 80)
    df = frame(c, highs=c + 1, lows=c - 1)
    assert t.adx(df, 14).value > 40


def test_distance_from_52_week_extremes():
    c = np.concatenate([np.linspace(100, 200, 200), np.linspace(200, 150, 60)])
    df = frame(c)
    out = t.range_52w(df)
    assert out["high_52w"].value == pytest.approx(200.0)
    assert out["dist_from_52w_high_pct"].value == pytest.approx(-25.0)


def test_breakout_status():
    c = np.concatenate([np.full(60, 100.0), [106.0]])
    df = frame(c, volumes=np.concatenate([np.full(60, 1000.0), [3000.0]]))
    assert t.breakout_status(df, lookback=55) == "breakout"
    d = frame(np.concatenate([np.full(60, 100.0), [94.0]]))
    assert t.breakout_status(d, lookback=55) == "breakdown"
    assert t.breakout_status(frame(np.full(61, 100.0)), lookback=55) == "none"


def test_relative_volume():
    v = np.concatenate([np.full(20, 1000.0), [2500.0]])
    df = frame(np.full(21, 100.0), volumes=v)
    assert t.relative_volume(df, 20).value == pytest.approx(2.5)


def test_ma_alignment_score_full_in_steady_uptrend():
    df = frame(np.linspace(100, 300, 260))
    assert t.ma_alignment(df.close).value == pytest.approx(100.0)
    assert t.ma_alignment(frame(np.linspace(300, 100, 260)).close).value == pytest.approx(0.0)


def test_relative_strength_vs_benchmark():
    stock = pd.Series(np.linspace(100, 150, 127))
    bench = pd.Series(np.linspace(100, 110, 127))
    m = t.relative_strength(stock, bench, "6M")
    assert m.value == pytest.approx((1.5 / 1.1 - 1) * 100, rel=1e-3)


def test_compute_technicals_never_looks_ahead():
    df = frame(np.linspace(100, 300, 400))
    full = t.compute_technicals(df.iloc[:300])
    again = t.compute_technicals(df.iloc[:300].copy())
    assert full["return_3m"].value == again["return_3m"].value
    # appending future bars must not change a score computed as of bar 300
    assert t.compute_technicals(df, as_of=df.index[299])["return_3m"].value == pytest.approx(
        full["return_3m"].value
    )


# ---- risk ----

def test_realized_volatility_annualised():
    rng = np.random.default_rng(7)
    rets = rng.normal(0, 0.01, 300)
    closes = 100 * np.exp(np.cumsum(rets))
    s = pd.Series(closes)
    lr = np.diff(np.log(closes))[-252:]
    assert risk.realized_volatility(s, 252).value == pytest.approx(lr.std(ddof=1) * np.sqrt(252) * 100)


def test_max_drawdown():
    s = pd.Series([100.0, 120.0, 90.0, 130.0, 104.0])
    # worst peak-to-trough: 120 -> 90 = -25%
    assert risk.max_drawdown(s).value == pytest.approx(-25.0)


def test_beta_of_doubled_returns_is_two():
    rng = np.random.default_rng(1)
    b = rng.normal(0, 0.01, 260)
    bench = pd.Series(100 * np.cumprod(1 + b))
    stock = pd.Series(100 * np.cumprod(1 + 2 * b))
    assert risk.beta(stock, bench, 252).value == pytest.approx(2.0, rel=1e-6)
    assert risk.correlation(stock, bench, 252).value == pytest.approx(1.0, rel=1e-6)


def test_beta_flat_benchmark_not_meaningful():
    s = pd.Series(np.linspace(100, 120, 260))
    assert risk.beta(s, pd.Series(np.full(260, 100.0)), 252).status == Status.NOT_MEANINGFUL


def test_downside_volatility_ignores_gains():
    s = pd.Series(np.linspace(100, 200, 300))
    assert risk.downside_volatility(s, 252).value == pytest.approx(0.0)


def test_average_traded_value():
    df = frame(np.full(30, 100.0), volumes=np.full(30, 5000.0))
    assert risk.average_traded_value(df, 20).value == pytest.approx(500_000.0)


def test_risk_label_thresholds_are_disclosed():
    assert risk.risk_label(vol_pct=12.0, max_dd_pct=-10.0, beta=0.7).label == "Low"
    assert risk.risk_label(vol_pct=28.0, max_dd_pct=-25.0, beta=1.1).label == "Moderate"
    assert risk.risk_label(vol_pct=45.0, max_dd_pct=-45.0, beta=1.5).label == "High"
    assert risk.risk_label(vol_pct=70.0, max_dd_pct=-65.0, beta=2.0).label == "Very High"
    r = risk.risk_label(vol_pct=None, max_dd_pct=None, beta=None)
    assert r.label == "Unknown" and r.criteria


# ---- corporate-action adjustment ----

def test_split_adjusts_prior_prices_and_volumes():
    df = frame([1000.0, 1010.0, 505.0, 510.0], volumes=[100, 100, 200, 200])
    actions = [adjust.CorporateAction(ex_date=df.index[2].date(), kind="split", ratio=2.0)]
    adj = adjust.adjust_bars(df, actions)
    assert adj.close.tolist() == pytest.approx([500.0, 505.0, 505.0, 510.0])
    assert adj.volume.tolist() == pytest.approx([200.0, 200.0, 200.0, 200.0])


def test_bonus_one_for_one_equals_factor_two():
    a = adjust.CorporateAction.bonus(ex_date=pd.Timestamp("2024-01-03").date(), bonus_shares=1, held_shares=1)
    assert a.ratio == pytest.approx(2.0)


def test_unadjusted_gap_is_flagged_when_no_action_explains_it():
    df = frame([1000.0, 1010.0, 505.0, 510.0])
    warnings = adjust.detect_unexplained_gaps(df, [], threshold_pct=40.0)
    assert len(warnings) == 1
    assert warnings[0]["date"] == df.index[2].date().isoformat()
    explained = [adjust.CorporateAction(ex_date=df.index[2].date(), kind="split", ratio=2.0)]
    assert adjust.detect_unexplained_gaps(df, explained, threshold_pct=40.0) == []


def test_parse_action_text_from_exchange_purpose_strings():
    p = adjust.parse_action("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share")
    assert p == ("split", pytest.approx(5.0))
    assert adjust.parse_action("Bonus 1:1") == ("bonus", pytest.approx(2.0))
    assert adjust.parse_action("Bonus 3:2") == ("bonus", pytest.approx(2.5))
    assert adjust.parse_action("Dividend - Rs 5 Per Share") == ("dividend", pytest.approx(5.0))
    assert adjust.parse_action("Annual General Meeting") is None
