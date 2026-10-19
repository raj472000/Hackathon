# Methodology

Every formula below is implemented in `backend/app/domain/` and unit-tested with hand-calculated
fixtures. Each stored result records the formula version that produced it. The API returns the
formula text and inputs for any metric (`GET /api/v1/instruments/{ref}/metrics`).

## Conventions

- Statement values are normalised to INR crore. Per-share values are in INR.
- Percent metrics are percent numbers (15 means 15%).
- A ratio whose denominator is zero or negative is `not_meaningful`. A ratio with a missing input
  is `missing`. Ratios that do not describe banks, NBFCs or insurers are `not_applicable` for
  those sectors. None of these is ever treated as zero.
- CapEx is a positive outflow; FCF = CFO − |CapEx|.
- Windows are counted in trading bars: 1W 5, 1M 21, 3M 63, 6M 126, 12M 252.

## Price adjustment (`adjust-v1`)

Back-adjustment for splits and bonus issues from the exchange corporate-actions tool: prices before
the ex-date are divided by the share multiplier and volumes multiplied by it. Dividends are not
adjusted. A close-to-close move of 40% or more with no matching action is flagged as an unexplained
gap. NSE bhavcopy prices are unadjusted and are adjusted before being compared with broker prices.

## Source reconciliation (`reconcile-v1`)

Order of use: NSE MCP, then Kite MCP. The first source with a value supplies it; a later
source only fills a gap. Where two sources both have a value, a relative difference above the
tolerance (close, open, high, low 0.1%; live price 1%; volume 5%) is recorded as a conflict and the
earlier source's value is kept.

## Research score (`score-v1`)

1. Each metric becomes a mid-rank percentile, 0–100, within the tracked universe at the as-of date
   (valuation metrics within the sector when it has at least five members), oriented so higher is
   more favourable. At least five peers are needed.
2. Pillar score = plain mean of the available metric percentiles. Missing and inapplicable metrics
   leave the denominator. A pillar needs at least half of its applicable metrics.
3. Overall = mean of the five pillars, 20% each. If any pillar is insufficient, the overall score
   is withheld; a labelled partial score is shown when at least three pillars exist.

| Pillar | Metrics (direction) |
|---|---|
| Fundamentals | ROE ↑, ROCE ↑, revenue growth ↑, PAT growth ↑, EBITDA margin ↑, debt/equity ↓, interest coverage ↑, CFO/PAT ↑ |
| Valuation | P/E ↓, P/B ↓, EV/EBITDA ↓, FCF yield ↑, dividend yield ↑ |
| Momentum | 3M, 6M, 12M return ↑, 6M relative strength ↑, moving-average alignment ↑, distance from 52-week high ↑ |
| Risk | 1Y volatility ↓, 1Y downside volatility ↓, 1Y max drawdown ↑ (shallower), beta ↓ |
| Context | sector RS-Ratio ↑, sector RS-Momentum ↑, 3M strength vs sector ↑, average traded value ↑ |

Labels, all rule-based: stance (Favorable ≥ 65, Mixed 45–65, Unfavorable < 45, else Insufficient
data); confidence (High: completeness ≥ 85% and prices ≤ 3 days old; Medium: ≥ 60% and ≤ 14 days;
else Low); risk (worst bucket of volatility 20/35/55%, drawdown −20/−35/−55%, beta 0.9/1.3/1.7).

## Themes (`theme-dsl-v1`)

A theme is a validated specification: universe, filters (`metric op value` over an allowlisted
catalog), ranking metrics and a limit. Instruments missing a filter metric are excluded with the
reason. Theme score = mean of the percentile ranks of the ranking metrics among instruments that
pass. Ties break on instrument id. Each refresh stores an immutable snapshot keyed by theme, month
and revision. Natural-language requests are turned into a specification by deterministic rules and
must be reviewed and confirmed before saving.

## Sector rotation (`rrg-v1`)

Weekly closes (week ending Friday). RS = sector / benchmark. RS-Ratio = 100 × RS / SMA(RS, 12).
RS-Momentum = 100 × RS-Ratio / SMA(RS-Ratio, 5). Quadrants split at 100 on both axes. A sector uses
its index when history exists, otherwise an equal-weight composite of its tracked constituents,
labelled as such. This is the project's own documented method, not a vendor's algorithm.

## Entry setup (`entry-v1`)

Eight conditions: trend (close > SMA50 > SMA200), momentum (6M return and 6M relative strength
positive), not overextended (RSI < 75), valuation score ≥ 40, fundamentals score ≥ 50, risk not
Very High, liquidity ≥ INR 1 crore a day, data completeness ≥ 60% with prices ≤ 5 days old.
Insufficient data when price is missing, stale or completeness is low; Unfavorable when risk is
Very High or the close is below SMA200 with a negative 6M return; Setup favorable when all pass;
otherwise Watch. No entry zone or target is produced.

## Concall follow-through (`concall-v1`)

Commitments are taken from the previous quarter's call and each is given a verdict from the
current quarter's call. Score = Σ(importance × credit) / Σ(importance) × 100, with credit delivered
1.0, partly delivered 0.5, in progress 0.5, deferred 0.25, not delivered 0, not addressed 0, and
importance high 3, medium 2, low 1. Any item whose quote is not found verbatim in the transcript is
excluded. Fewer than three scored items gives no score. Without a language model a keyword
heuristic is used and confidence is Low.

## Research grounding (`research-v1`)

The model receives only the evidence bundle and must cite evidence ids. A statement is dropped if
it cites nothing that was retrieved, contains a URL that was not retrieved, or states a number not
present in the evidence it cites. If more than half are dropped, the rule-based summary is shown.
