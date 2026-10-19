"""Built-in theme definitions. Each is a validated ThemeSpec (data, not prose or code).

Thresholds are starting defaults, documented in `methodology`, and editable by cloning a theme.
Themes that need data no configured source provides are declared with `requires` and are shown as
unavailable instead of being approximated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.theme_dsl import Filter, RankKey, ThemeSpec, Universe

CATEGORIES = [
    "Momentum and trend", "Low risk and stability", "Quality and compounders", "Value and valuation",
    "Contrarian and mean reversion", "Income", "Growth", "Financial strength", "Event and trend context",
]

# Capabilities the running system can supply. Anything else makes a theme unavailable.
SUPPORTED_REQUIREMENTS = {"prices", "fundamentals", "sector_rotation", "corporate_actions"}
LIQUID = 5e7  # INR 5 crore average daily traded value


@dataclass(frozen=True)
class BuiltinTheme:
    slug: str
    category: str
    title: str
    description: str
    methodology: str
    spec: ThemeSpec
    requires: list[str] = field(default_factory=lambda: ["prices"])

    @property
    def available(self) -> bool:
        return set(self.requires) <= SUPPORTED_REQUIREMENTS


def _t(slug: str, category: str, title: str, description: str, filters: list[tuple], rank: list[tuple[str, str]],
       requires: list[str] | None = None, types: list[str] | None = None, liquid: float | None = LIQUID,
       red_flags: bool = True, limit: int = 25) -> BuiltinTheme:
    fs = [Filter(metric=f[0], op=f[1], value=f[2], value2=f[3] if len(f) > 3 else None) for f in filters]
    spec = ThemeSpec(
        name=title,
        universe=Universe(instrument_types=types or ["EQ"], min_avg_traded_value=liquid),  # type: ignore[arg-type]
        filters=fs,
        rank_by=[RankKey(metric=m, direction=d) for m, d in rank],  # type: ignore[arg-type]
        limit=limit,
        exclude_red_flags=red_flags,
    )
    return BuiltinTheme(slug, category, title, description, " · ".join(spec.describe()), spec, requires or ["prices"])


P, F, PF = ["prices"], ["fundamentals"], ["prices", "fundamentals"]
M, R, Q, V, C, I, G, S, E = CATEGORIES  # noqa: E741

BUILTIN_THEMES: list[BuiltinTheme] = [
    # ---- Momentum and trend
    _t("price-momentum", M, "Price momentum", "Leaders on blended 1M, 3M, 6M and 12M returns.",
       [("return_6m", "gt", 0), ("return_12m", "gt", 0)],
       [("return_1m", "desc"), ("return_3m", "desc"), ("return_6m", "desc"), ("return_12m", "desc")]),
    _t("relative-strength-leaders", M, "Relative strength leaders", "Outperforming the benchmark over 3, 6 and 12 months.",
       [("rel_strength_6m", "gt", 0), ("rel_strength_12m", "gt", 0)],
       [("rel_strength_3m", "desc"), ("rel_strength_6m", "desc"), ("rel_strength_12m", "desc")]),
    _t("strong-uptrend", M, "Strong uptrend", "Close above the 20/50/100/200-day averages in order.",
       [("ma_alignment", "gte", 100), ("adx_14", "gte", 20)], [("adx_14", "desc"), ("return_3m", "desc")]),
    _t("breakout-candidates", M, "Breakout candidates", "Closing above the prior 55-day range, within 5% of the 52-week high.",
       [("breakout_signal", "eq", 1), ("dist_from_52w_high_pct", "gte", -5)], [("relative_volume", "desc"), ("return_1m", "desc")]),
    _t("volume-confirmed-momentum", M, "Volume-confirmed momentum", "Positive 3M return with rising volume.",
       [("return_3m", "gt", 0), ("volume_trend", "gte", 1.2)], [("volume_trend", "desc"), ("return_3m", "desc")]),
    _t("momentum-acceleration", M, "Momentum acceleration", "Latest 3M return exceeds the prior 3M return.",
       [("momentum_acceleration", "gt", 0), ("return_3m", "gt", 0)], [("momentum_acceleration", "desc")]),
    _t("sector-leaders", M, "Sector leaders", "Beating their own sector over 3 months, in a sector with RS-Ratio above 100.",
       [("rel_strength_vs_sector_3m", "gt", 0), ("sector_rs_ratio", "gte", 100)],
       [("rel_strength_vs_sector_3m", "desc"), ("sector_rs_ratio", "desc")], ["prices", "sector_rotation"]),
    _t("trend-following", M, "Trend-following candidates", "Persistent trend: aligned averages, ADX above 25, RSI not extreme.",
       [("ma_alignment", "gte", 75), ("adx_14", "gte", 25), ("rsi_14", "between", 50, 75)],
       [("trend_persistence", "desc"), ("adx_14", "desc")]),
    # ---- Low risk and stability
    _t("low-volatility", R, "Low realized volatility", "Lowest 1-year realized volatility.",
       [("volatility_252d", "lt", 25)], [("volatility_252d", "asc")]),
    _t("low-beta", R, "Low beta", "Beta below 0.8 against the benchmark.", [("beta", "lt", 0.8), ("beta", "gt", 0)], [("beta", "asc")]),
    _t("low-drawdown", R, "Low maximum drawdown", "Shallowest 1-year peak-to-trough decline.",
       [("max_drawdown_1y", "gte", -15)], [("max_drawdown_1y", "desc")]),
    _t("defensive-equities", R, "Defensive equities", "Low beta and low volatility together.",
       [("beta", "lt", 0.9), ("volatility_252d", "lt", 25)], [("beta", "asc"), ("volatility_252d", "asc")]),
    _t("stable-earnings", R, "Stable earnings", "Low variability of annual PAT growth with profits rising in most years.",
       [("pat_growth_stdev", "lt", 15), ("positive_pat_growth_years", "gte", 75)], [("pat_growth_stdev", "asc")], F),
    _t("stable-margins", R, "Stable margins", "Low variability of EBITDA margin.",
       [("ebitda_margin_stdev", "lt", 3), ("ebitda_margin", "gte", 12)], [("ebitda_margin_stdev", "asc")], F),
    _t("consistent-dividend-payers", R, "Consistent dividend payers", "Dividends paid in at least five consecutive years.",
       [("dividend_years", "gte", 5)], [("dividend_years", "desc"), ("dividend_yield", "desc")], F),
    _t("lower-downside-volatility", R, "Lower downside volatility", "Lowest volatility of negative daily returns.",
       [("downside_volatility_252d", "lt", 15)], [("downside_volatility_252d", "asc")]),
    _t("quality-low-volatility", R, "Quality with low volatility", "ROE of at least 15% with 1-year volatility under 25%.",
       [("roe", "gte", 15), ("volatility_252d", "lt", 25), ("debt_equity", "lt", 1)],
       [("roe", "desc"), ("volatility_252d", "asc")], PF),
    # ---- Quality and compounders
    _t("high-roce", Q, "High ROCE / ROIC", "ROCE of at least 20%.", [("roce", "gte", 20)], [("roce", "desc"), ("roic", "desc")], F),
    _t("high-roe-acceptable-leverage", Q, "High ROE with acceptable leverage", "ROE of at least 18% with debt/equity below 1x.",
       [("roe", "gte", 18), ("debt_equity", "lt", 1)], [("roe", "desc")], F),
    _t("consistent-revenue-growth", Q, "Consistent revenue growth", "Revenue up in every available year, 3Y CAGR at least 10%.",
       [("positive_revenue_growth_years", "gte", 100), ("revenue_cagr_3y", "gte", 10)], [("revenue_cagr_3y", "desc")], F),
    _t("consistent-earnings-growth", Q, "Consistent earnings growth", "PAT up in every available year, 3Y CAGR at least 10%.",
       [("positive_pat_growth_years", "gte", 100), ("pat_cagr_3y", "gte", 10)], [("pat_cagr_3y", "desc")], F),
    _t("high-stable-margins", Q, "High and stable margins", "EBITDA margin of at least 20% with low variability.",
       [("ebitda_margin", "gte", 20), ("ebitda_margin_stdev", "lt", 4)], [("ebitda_margin", "desc")], F),
    _t("strong-free-cash-flow", Q, "Strong free cash flow", "Positive and growing free cash flow.",
       [("fcf", "gt", 0), ("fcf_growth_yoy", "gt", 0)], [("fcf_yield", "desc")], PF),
    _t("high-cash-conversion", Q, "High cash conversion", "CFO at least equal to PAT with low accruals.",
       [("cfo_to_pat", "gte", 1), ("accrual_ratio", "lt", 5)], [("cfo_to_pat", "desc")], F),
    _t("low-leverage-strong-coverage", Q, "Low leverage / strong interest coverage", "Debt/equity under 0.5x and coverage above 8x.",
       [("debt_equity", "lt", 0.5), ("interest_coverage", "gte", 8)], [("interest_coverage", "desc")], F),
    _t("durable-compounders", Q, "Durable compounders", "ROCE at least 18%, 5Y revenue and PAT CAGR at least 10%, low debt.",
       [("roce", "gte", 18), ("revenue_cagr_5y", "gte", 10), ("pat_cagr_5y", "gte", 10), ("debt_equity", "lt", 0.7)],
       [("roce", "desc"), ("pat_cagr_5y", "desc")], F),
    _t("efficient-capital-allocation", Q, "Efficient capital allocation", "ROIC of at least 15% with asset turnover above 0.8x.",
       [("roic", "gte", 15), ("asset_turnover", "gte", 0.8)], [("roic", "desc")], F),
    _t("low-dilution", Q, "Low dilution", "Share count grew by no more than 1% over the year.",
       [("share_count_change", "lte", 1)], [("share_count_change", "asc"), ("roe", "desc")], F),
    _t("governance-screened-quality", Q, "Governance-screened quality", "Quality screen restricted to companies clear of sourced governance flags.",
       [("roe", "gte", 15)], [("roe", "desc")], ["fundamentals", "governance_data"]),
    # ---- Value and valuation
    _t("low-valuation-vs-peers", V, "Low valuation relative to industry peers", "P/E in the lowest 30% of sector peers.",
       [("pe_sector_percentile", "lte", 30), ("pe", "gt", 0)], [("pe_sector_percentile", "asc")], PF),
    _t("low-historical-valuation", V, "Low historical valuation percentile", "P/E in the lowest 30% of its own history.",
       [("pe_history_percentile", "lte", 30)], [("pe_history_percentile", "asc")], PF),
    _t("earnings-yield-leaders", V, "Earnings yield leaders", "Highest earnings yield among profitable companies.",
       [("earnings_yield", "gt", 0)], [("earnings_yield", "desc")], PF),
    _t("fcf-yield-leaders", V, "FCF yield leaders", "Highest free-cash-flow yield.", [("fcf_yield", "gt", 0)], [("fcf_yield", "desc")], PF),
    _t("reasonable-peg", V, "Reasonable PEG", "PEG between 0 and 1.5, computed only for positive earnings growth.",
       [("peg", "between", 0.01, 1.5)], [("peg", "asc")], PF),
    _t("value-plus-quality", V, "Value plus quality", "Cheaper than half of sector peers with ROCE at least 15%.",
       [("pe_sector_percentile", "lte", 50), ("roce", "gte", 15)], [("pe_sector_percentile", "asc"), ("roce", "desc")], PF),
    _t("deep-value", V, "Deep value", "P/B below 1.5x and P/E in the lowest 20% of peers. Value-trap risk: check debt, cash flow and trend.",
       [("pb", "lt", 1.5), ("pe_sector_percentile", "lte", 20), ("pe", "gt", 0)], [("pb", "asc")], PF),
    _t("dividend-value", V, "Dividend value", "Yield of at least 3% with P/E in the lower half of peers.",
       [("dividend_yield", "gte", 3), ("pe_sector_percentile", "lte", 50)], [("dividend_yield", "desc")], PF),
    _t("net-cash-businesses", V, "Net-cash businesses", "Cash exceeds total debt.", [("net_debt", "lt", 0)], [("net_debt", "asc")], PF),
    # ---- Contrarian and mean reversion
    _t("oversold-financially-sound", C, "Oversold but financially sound", "RSI below 35 with low debt and positive ROE.",
       [("rsi_14", "lt", 35), ("debt_equity", "lt", 1), ("roe", "gt", 10)], [("rsi_14", "asc")], PF),
    _t("recovery-improving-fundamentals", C, "Recovery candidates with improving fundamentals",
       "Down over 12 months while PAT and margins improve.",
       [("return_12m", "lt", 0), ("pat_growth_yoy", "gt", 0), ("ebitda_margin_change", "gt", 0)], [("pat_growth_yoy", "desc")], PF),
    _t("positive-earnings-revisions", C, "Positive earnings revisions", "Needs a trusted analyst-estimate source; none is configured.",
       [], [("overall_score", "desc")], ["analyst_estimates"]),
    _t("below-trend-improving-cash-flow", C, "Below trend with improving cash flow", "Below the 200-day average while FCF grows.",
       [("ma_alignment", "lte", 25), ("fcf_growth_yoy", "gt", 0), ("fcf", "gt", 0)], [("fcf_growth_yoy", "desc")], PF),
    _t("out-of-favor-sector-improving", C, "Out-of-favor sector with improving relative strength",
       "Sector RS-Ratio below 100 with RS-Momentum above 100 (Improving quadrant).",
       [("sector_rs_ratio", "lt", 100), ("sector_rs_momentum", "gt", 100)],
       [("sector_rs_momentum", "desc"), ("rel_strength_vs_sector_3m", "desc")], ["prices", "sector_rotation"]),
    _t("drawdown-recovery", C, "Drawdown recovery candidates", "At least 25% off the 52-week high with a positive 1-month return.",
       [("dist_from_52w_high_pct", "lte", -25), ("return_1m", "gt", 0)], [("return_1m", "desc")]),
    _t("value-improving-momentum", C, "Value with improving momentum", "Cheaper than 40% of peers with accelerating momentum.",
       [("pe_sector_percentile", "lte", 40), ("momentum_acceleration", "gt", 0)], [("momentum_acceleration", "desc")], PF),
    # ---- Income
    _t("dividend-yield-sustainable", I, "Dividend yield with payout sustainability", "Yield at least 2.5% with payout under 70%.",
       [("dividend_yield", "gte", 2.5), ("payout_ratio", "between", 1, 70)], [("dividend_yield", "desc")], PF),
    _t("consistent-dividend-history", I, "Consistent dividend history", "Seven or more consecutive dividend years.",
       [("dividend_years", "gte", 7)], [("dividend_years", "desc")], F),
    _t("fcf-backed-dividends", I, "FCF-backed dividends", "Dividend payers with positive FCF and CFO at least 0.9x PAT.",
       [("dividend_yield", "gte", 1.5), ("fcf", "gt", 0), ("cfo_to_pat", "gte", 0.9)], [("fcf_yield", "desc")], PF),
    _t("low-leverage-dividend-payers", I, "Lower leverage dividend payers", "Yield at least 2% with debt/equity under 0.5x.",
       [("dividend_yield", "gte", 2), ("debt_equity", "lt", 0.5)], [("dividend_yield", "desc")], PF),
    _t("reit-invit-yield", I, "REIT / InvIT yield", "Distribution yield for listed REITs and InvITs.",
       [("dividend_yield", "gt", 0)], [("dividend_yield", "desc")], PF, types=["REIT", "INVIT"], liquid=None),
    # ---- Growth
    _t("revenue-growth-leaders", G, "Revenue growth leaders", "Highest revenue growth, at least 15% YoY.",
       [("revenue_growth_yoy", "gte", 15)], [("revenue_growth_yoy", "desc"), ("revenue_cagr_3y", "desc")], F),
    _t("earnings-growth-leaders", G, "Earnings growth leaders", "Highest PAT growth, at least 15% YoY.",
       [("pat_growth_yoy", "gte", 15)], [("pat_growth_yoy", "desc"), ("pat_cagr_3y", "desc")], F),
    _t("profitable-growth", G, "Profitable growth", "Revenue growth of at least 12% with PAT margin of at least 10%.",
       [("revenue_growth_yoy", "gte", 12), ("pat_margin", "gte", 10)], [("revenue_growth_yoy", "desc")], F),
    _t("fcf-growth", G, "FCF growth", "Positive free cash flow growing at least 10% YoY.",
       [("fcf", "gt", 0), ("fcf_growth_yoy", "gte", 10)], [("fcf_growth_yoy", "desc")], F),
    _t("growth-improving-margins", G, "Growth with improving margins", "Revenue growth of at least 10% with EBITDA margin expanding.",
       [("revenue_growth_yoy", "gte", 10), ("ebitda_margin_change", "gt", 0)], [("ebitda_margin_change", "desc")], F),
    _t("growth-reasonable-valuation", G, "Growth at reasonable valuation", "PAT growth of at least 15% with PEG at or below 1.5.",
       [("pat_growth_yoy", "gte", 15), ("peg", "between", 0.01, 1.5)], [("peg", "asc")], PF),
    _t("small-mid-cap-growth", G, "Small / mid-cap growth", "Market cap under INR 50,000 crore, growing, liquid and not very volatile.",
       [("market_cap", "lt", 50000), ("revenue_growth_yoy", "gte", 15), ("volatility_252d", "lt", 50)],
       [("revenue_growth_yoy", "desc")], PF, liquid=1e8),
    # ---- Financial strength
    _t("net-cash-low-debt", S, "Net cash / low debt", "Debt/equity under 0.3x.", [("debt_equity", "lt", 0.3)], [("debt_equity", "asc")], F),
    _t("strong-interest-coverage", S, "Strong interest coverage", "Interest coverage of at least 6x. A coverage-trend metric is not available.",
       [("interest_coverage", "gte", 6)], [("interest_coverage", "desc")], F),
    _t("strong-liquidity-ratios", S, "Strong current / quick ratio", "Current ratio at least 1.5x and quick ratio at least 1x (non-financials).",
       [("current_ratio", "gte", 1.5), ("quick_ratio", "gte", 1)], [("quick_ratio", "desc")], F),
    _t("high-cash-flow-quality", S, "High cash-flow quality", "CFO at least 1x PAT, low accruals and positive FCF.",
       [("cfo_to_pat", "gte", 1), ("accrual_ratio", "lt", 3), ("fcf", "gt", 0)], [("cfo_to_pat", "desc")], F),
    _t("low-working-capital-stress", S, "Lower working-capital stress", "Cash conversion cycle under 60 days.",
       [("cash_conversion_cycle", "lt", 60)], [("cash_conversion_cycle", "asc")], F),
    _t("balance-sheet-resilience", S, "Balance-sheet resilience", "Low debt, strong coverage and adequate liquidity together.",
       [("debt_equity", "lt", 0.5), ("interest_coverage", "gte", 6), ("current_ratio", "gte", 1.2)],
       [("interest_coverage", "desc"), ("debt_equity", "asc")], F),
    # ---- Event and trend context
    _t("post-results-positive-trend", E, "Post-results positive fundamental trend", "Revenue, PAT and margin all improved in the latest filed period.",
       [("revenue_growth_yoy", "gt", 0), ("pat_growth_yoy", "gt", 0), ("ebitda_margin_change", "gt", 0)],
       [("pat_growth_yoy", "desc")], F),
    _t("corporate-action-watchlist", E, "Corporate-action watchlist", "Instruments with an upcoming sourced corporate action.",
       [], [("avg_traded_value", "desc")], ["corporate_actions_calendar"]),
    _t("sector-rotation-leaders", E, "Sector rotation leaders", "Stocks in sectors in the Leading quadrant.",
       [("sector_rs_ratio", "gte", 100), ("sector_rs_momentum", "gte", 100)],
       [("sector_rs_ratio", "desc"), ("rel_strength_3m", "desc")], ["prices", "sector_rotation"]),
    _t("sector-rotation-laggards", E, "Sector rotation laggards", "Stocks in sectors in the Lagging quadrant.",
       [("sector_rs_ratio", "lt", 100), ("sector_rs_momentum", "lt", 100)],
       [("sector_rs_ratio", "asc")], ["prices", "sector_rotation"]),
    _t("commodity-sensitivity", E, "Commodity sensitivity", "Needs sourced commodity-exposure data; none is configured.",
       [], [("overall_score", "desc")], ["commodity_exposure"]),
    _t("earnings-momentum", E, "Earnings momentum", "Needs sourced analyst estimates; none is configured.",
       [], [("overall_score", "desc")], ["analyst_estimates"]),
    _t("newly-improving-relative-strength", E, "Newly improving relative strength", "3M relative strength positive while 12M is still negative.",
       [("rel_strength_3m", "gt", 0), ("rel_strength_12m", "lt", 0)], [("rel_strength_3m", "desc")]),
]

BY_SLUG = {t.slug: t for t in BUILTIN_THEMES}
