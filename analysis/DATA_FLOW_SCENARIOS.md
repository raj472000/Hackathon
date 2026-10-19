# Data flow scenarios: worked examples

This document walks through every way data enters the platform and leaves it. Each scenario covers:

1. **how it starts:** a schedule, a button, or opening a page;
2. **what is fetched:** which source, which tool or file, the exact arguments, and the real values returned;
3. **what the backend does with it:** parsing, merging sources, calculations, and checks;
4. **where it is stored;**
5. **what the frontend receives:** the real JSON from the API;
6. **what the page shows.**

All values are real. They come from the public NSE MCP servers, the Kite MCP server, NSE's own files, and
the API running locally (`DATA_MODE=live`, Compose PostgreSQL), captured on **2026-10-16** and
**2026-10-17**. Long JSON is cut with `…`; nothing else has been edited. Most examples follow
**NSE:TCS**, so the same numbers can be traced from the source to the screen.

For the full field-by-field schemas of each MCP tool and API response, see
[DATA_FLOW_EXAMPLES.md](DATA_FLOW_EXAMPLES.md). This document focuses on flow.

---

## Contents

- [0. The big picture](#0-the-big-picture)
- **Part 1: data coming in (background jobs)**
  - [S1. Instrument master: which stocks exist](#s1-instrument-master-which-stocks-exist)
  - [S2. Whole-market daily prices from the NSE bhavcopy file](#s2-whole-market-daily-prices-from-the-nse-bhavcopy-file)
  - [S3. Per-symbol history: NSE MCP first, Kite fills gaps](#s3-per-symbol-history-nse-mcp-first-kite-fills-gaps)
  - [S4. Financial statements from NSE filings (XBRL)](#s4-financial-statements-from-nse-filings-xbrl)
  - [S5. Metrics, scores and labels](#s5-metrics-scores-and-labels)
  - [S6. Sector rotation (RRG), every 12 hours](#s6-sector-rotation-rrg-every-12-hours)
  - [S7. Monthly theme snapshots](#s7-monthly-theme-snapshots)
  - [S8. Alerts, every 15 minutes](#s8-alerts-every-15-minutes)
  - [S9. Kite login, then broker backfill](#s9-kite-login-then-broker-backfill)
- **Part 2: data going out (a user opens a page or clicks)**
  - [S10. Instrument page: the live quote](#s10-instrument-page-the-live-quote)
  - [S11. When NSE and Kite disagree, or one is missing](#s11-when-nse-and-kite-disagree-or-one-is-missing)
  - [S12. Instrument page: chart, metrics and financials tabs](#s12-instrument-page-chart-metrics-and-financials-tabs)
  - [S13. Screener](#s13-screener)
  - [S14. Research board](#s14-research-board)
  - [S15. Concall analyzer](#s15-concall-analyzer)
  - [S16. Ask the market](#s16-ask-the-market)
  - [S17. Creating a custom theme](#s17-creating-a-custom-theme)
  - [S18. AI filter on a theme](#s18-ai-filter-on-a-theme)
  - [S19. Overview dashboard](#s19-overview-dashboard)
  - [S20. Data status page](#s20-data-status-page)
  - [S21. Watchlists and the search box](#s21-watchlists-and-the-search-box)
- **Part 3: when things go wrong**
  - [S22. Failure catalogue](#s22-failure-catalogue)
- [Appendix: which source fills which value on screen](#appendix-which-source-fills-which-value-on-screen)

---

## 0. The big picture

```
 EXTERNAL SOURCES                    BACKEND (FastAPI + APScheduler worker)                     FRONTEND (React)
 ────────────────                    ──────────────────────────────────────                     ────────────────
 NSE bhavcopy MCP  ─┐                                                                           
 (history, actions, │  connectors/      parsing.py        services/market_data.py               pages/*.tsx
  breadth, search)  │  nse_mcp.py  ──►  vendor names ──►  CorrelatedMarketData  ──┐             calls only
 NSE CM market MCP ─┤  (JSON-RPC over   → app names,      NSE + Kite in parallel, │             /api/v1/* on
 (live quotes)      │   Streamable      DataPoint with    reconcile-v1 picks a    │             its own origin
 Kite MCP          ─┘   HTTP)           provider, as_of,  value, flags conflicts  │                  ▲
 (fallback quotes,     kite_mcp.py      status            ─────────────────────┐  │                  │
  history, tokens)                                                             ▼  ▼                  │
 NSE bhavcopy file ──► services/market_files.py ───────────────────────►  PostgreSQL ──► api/v1/*.py ┘
 NSE filings (XBRL,──► connectors/nse_filings.py ──────────────────────►  price_bars        builds plain JSON:
  transcripts PDF)                                                        financial_…       envelope + values +
 Web search (ddgs) ──► connectors/web_search.py ─┐                        metric_snapshots  as_of, provider,
 LLM (OpenRouter)  ──► connectors/llm.py ────────┴─► research / concall / theme_snapshots   status, reason
                                                     ask / theme_ai      sector_rotation_…
                                                                         research_reports …
```

Two rules shape every scenario:

- **The browser never talks to a source.** It calls `/api/v1/*` only. Keys, broker sessions and MCP URLs stay on the server.
- **A failure is never turned into a value.** A missing number is `null` with a `status` and a `reason`, never `0`.
  A source that fails leaves a `SourceStatus` row saying why. An error is never cached as data.

There are two timelines:

| Timeline | What runs | Sources touched | Scenarios |
|---|---|---|---|
| **Background (schedule, Asia/Kolkata)** | `instruments` 07:00 Mon–Fri · `prices` 19:30 Mon–Fri · `rrg` every 12 h · `themes` 06:30 on the 1st · `alerts` every 15 min | NSE files, NSE MCP, Kite MCP, NSE filings | S1–S9 |
| **On request** | A page load or button click | Mostly the database. Live calls only for quotes, research, concalls and Ask | S10–S21 |

Every response the frontend receives starts with the same three fields:

```json
{"data_mode": "live", "is_demo": false, "retrieved_at": "2026-10-16T12:00:38.548633+00:00", …}
```

Most responses also carry `as_of` (the date of the newest data used), `data_state` (`ok`, `missing`,
`stale` or `unavailable`) and `data_state_reason` (a sentence the page shows when the state is not `ok`).

---

# Part 1: data coming in (background jobs)

## S1. Instrument master: which stocks exist

| | |
|---|---|
| **Trigger** | `instruments` job, 07:00 Mon–Fri; also the first step of "Refresh all" |
| **Sources** | NSE's equity list CSV (`NSE_EQUITY_LIST_URL`), NSE's industry file (`NSE_INDUSTRY_LIST_URL`), the curated reference file |
| **Writes** | `instruments`, then built-in `themes` are seeded |
| **Frontend** | Search box, every page that names a stock |

**Flow**

```
NSE equity list CSV ──┐
NSE industry file  ───┼─► market_files.load_universe() ─► ingestion.sync_instruments() ─► instruments
reference file     ───┘   (series filter, sector from     (insert new, update existing,
                           NSE industry, reference wins)   mark delisted "inactive")
```

1. **Fetch.** NSE's list of listed equities is read and kept to the series in `UNIVERSE_SERIES` (EQ by default).
2. **Classify.** A sector comes from NSE's industry file. Stocks outside that file get the sector shared by
   most NSE-classified stocks with the same NSE industry label. If there is no clear majority, the stock is
   left unclassified rather than guessed. Rows in the curated reference file (indices, sector-index mapping,
   hand-checked sectors) take precedence.
3. **Store.** Each row is inserted or updated by symbol. A symbol that is no longer on NSE's list keeps its
   history but is marked `inactive`.

**What the frontend receives.** `GET /api/v1/instruments?q=TCS&page_size=8&instrument_type=EQ`, captured
live and cut to the first item:

```json
{"items": [{"id": 79, "exchange": "NSE", "symbol": "TCS", "name": "Tata Consultancy Services",
            "isin": "INE467B01029", "instrument_type": "EQ", "currency": "INR",
            "sector": "Information Technology", "industry": "Computers - Software", "sector_kind": "industrial",
            "lot_size": 1, "trading_status": "active", "classification_source": "reference_file", "is_demo": false,
            "identifiers": [{"provider": "kite_mcp", "kind": "instrument_token", "value": "2953217"}],
            "updated_at": "2026-10-16T06:37:24.460937+00:00"}, …],
 "page": 1, "page_size": 8, "total": …}
```

`sector_kind` matters later: `industrial`, `bank`, `nbfc` or `insurance` decides which ratios apply (S13).
The Kite `instrument_token` is added in S3.

---

## S2. Whole-market daily prices from the NSE bhavcopy file

| | |
|---|---|
| **Trigger** | `prices` job, 19:30 Mon–Fri (after NSE publishes the day's file) |
| **Source** | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip` |
| **Writes** | `price_bars` (`provider = nse_bhavcopy_file`), `corporate_actions` |
| **Frontend** | Instrument chart (S12) and, through metrics (S5), every score and screen |

**Why a file and not MCP?** The universe has 2,576 equities. One file per day carries every security, so
one request replaces 2,576 per-symbol MCP calls. The MCP path (S3) is kept for indices and the reference
stocks.

**Flow**

```
for each trading day missing from price_bars:
  GET BhavCopy_…_{yyyymmdd}_F_0000.csv.zip ─► cached at backend/var/nse_filings/<sha256(url)[:32]>.zip
     │   (404 = no file that day: remembered as a holiday, not retried)
     ▼
  unzip ─► CSV, one row per security ─► keep series EQ ─► one bar per symbol
     ▼
  NSE market-wide corporate actions ─► splits and bonuses ─► back-adjust older bars (adjust-v1)
     ▼
  upsert price_bars (UNIQUE instrument_id + date), numeric columns
```

**1. What comes back.** The 2026-10-15 file had 3,673 rows. The TCS row, column by column (the columns the app reads are marked):

```
TradDt=2026-10-15 ◄date   TckrSymb=TCS ◄symbol   SctySrs=EQ ◄series   ISIN=INE467B01029
OpnPric=2347.20 ◄open   HghPric=2386.70 ◄high   LwPric=2335.00 ◄low   ClsPric=2350.00 ◄close
PrvsClsgPric=2304.90 ◄gap check   TtlTradgVol=9150523 ◄volume   TtlTrfVal=21607212833.80 ◄traded value
LastPric=2350.00   SttlmPric=2350.45   TtlNbOfTxsExctd=265014   FinInstrmNm=TATA CONSULTANCY SERV LT   …
```

**2. Adjustment.** Bhavcopy prices are unadjusted. Older bars are divided by the split or bonus ratio
from the corporate-actions list (`adjust-v1`). For example, `"Face Value Split From Rs 10 To Rs 2"`
gives ratio 5.0 and `"Bonus 1:1"` gives 2.0. Cash dividends are not adjusted. A stock with a new split
not yet applied has its whole history rebuilt so it stays consistent.

**3. Stored row.** One bar per instrument per date:

```
 date       | open    | high    | low     | close   | volume     | provider          | adjustment
 2026-10-15 | 2347.20 | 2386.70 | 2335.00 | 2350.00 | 9150523.00 | nse_bhavcopy_file | split_adjusted
```

The same close (2350.0) and volume (9150523) also came from NSE MCP history and from Kite for that date
(S3). Three independent sources agree.

---

## S3. Per-symbol history: NSE MCP first, Kite fills gaps

| | |
|---|---|
| **Trigger** | `prices` job (indices and reference stocks); "Fetch latest" on an instrument (`POST /instruments/{ref}/refresh`); theme refresh with `fetch_latest`; broker backfill (S9) |
| **Sources** | NSE bhavcopy MCP `get_stock_history` (or `get_index_history`) and `get_corporate_actions`; Kite MCP `search_instruments` and `get_historical_data` |
| **Writes** | `price_bars`, `corporate_actions`, `source_conflicts`, `data_source_records`, `instrument_identifiers` |
| **Frontend** | Instrument chart `coverage.by_provider`, `lineage[]`, `conflicts[]` |

**Flow**

```
CorrelatedMarketData.history("TCS", start, end)
 ├─ in parallel ───────────────────────────────────────────────────────────────────────┐
 │  NSE: get_stock_history (paged backwards)   Kite: search_instruments → token          │  NSE: get_corporate_actions
 │                                                   get_historical_data                  │
 ▼                                                                                        ▼
 each source's frame (unadjusted NSE bars are split-adjusted with the actions) ──► reconcile_bars()
                                                                                        │
     one whole bar per date from the first source in precedence [nse_mcp, kite_mcp] ◄───┘
     differences above tolerance → conflicts
 ▼
 ingestion._write_history(): upsert price_bars, write source_conflicts and data_source_records
```

**Step 1: MCP session.** Every MCP call follows the same JSON-RPC sequence over Streamable HTTP:
`initialize` → `notifications/initialized` (HTTP 202) → `tools/list` (once, cached, to learn the real tool
names and argument schemas) → `tools/call`. Headers: `Mcp-Session-Id`, `MCP-Protocol-Version: 2025-03-26`.

**Step 2: NSE history, fetched page by page.** `bind_args` maps the app's request onto the tool's own
argument names, from its advertised schema. Captured live:

```json
// tools/call get_stock_history, arguments:
{"symbol": "TCS", "months": 3, "endDate": "today"}

// result.content[0].text (parsed):
{"symbol": "TCS", "from_date": "2026-07-16", "to_date": "2026-10-16", "trading_days": 65,
 "next_end_date": "2026-07-15",
 "summary": {"first_close": 2201.0, "last_close": 2465.0, "return_pct": 11.99, …},
 "data": [
   {"symbol": "TCS", "series": "EQ", "date": "2026-07-16", "open": 2206.0, "high": 2231.4, "low": 2187.8, "close": 2201.0,
    "prevClose": 2189.2, "volume": 5197595, "totalTradedValue": 11501044792.2, …},
   …,
   {"date": "2026-10-15", "open": 2347.2, "high": 2386.7, "low": 2335.0, "close": 2350.0, "volume": 9150523, …},
   {"date": "2026-10-16", "open": 2382.0, "high": 2484.7, "low": 2375.7, "close": 2465.0, "volume": 12166634, …}]}
```

The connector sends `next_end_date` back as `endDate` until it reaches the start date. If NSE answers
older pages with an error payload, it keeps the pages it has and adds a warning.

**Step 3: corporate actions, fetched alongside.** Captured live:

```json
// arguments: {"symbol": "TCS", "fromDate": "2021-10-12", "toDate": "2026-10-16"}
{"symbol": "TCS", "count": 23, "actions": [
  {"exDate": "2021-10-14", "actionType": "DIVIDEND", "purpose": "Interim Dividend - Rs 7 Per Share", "adjustmentFactor": 1.0},
  {"exDate": "2022-02-22", "actionType": "OTHER", "purpose": "Buyback", "adjustmentFactor": 1.0}, …]}
```

`parse_action()` reads `purpose`: the dividend becomes `(dividend, 7.0)` and the buyback becomes `other`.
TCS had no split or bonus in this window, so its prices are unchanged. A close-to-close move of 40% or more
that no action explains is reported as a warning.

**Step 4: Kite.** `search_instruments` resolves the instrument token once. The token is stored as an identifier
(`{"provider": "kite_mcp", "kind": "instrument_token", "value": "2953217"}`). `get_historical_data` is
then called with dates as `"YYYY-MM-DD HH:MM:SS"`.

**Step 5: merge.** `reconcile_bars()` takes each date's whole bar from the first source that has a close
for it, so one bar never mixes sources. Where both sources have the date, a difference above tolerance
(close 0.1%, volume 5%) becomes a conflict row.

**A real degraded run (2026-10-16, 06:53 UTC).** The NSE bhavcopy circuit breaker was open after repeated
timeouts, so Kite supplied the recent bars. The rows written to `data_source_records`:

```
 provider | dataset           | source_id           | status      | rows | retrieved_at (UTC)
 kite_mcp | history           | get_historical_data | ok          |   10 | 2026-10-16 06:53:33
 nse_mcp  | history           |                     | unavailable |      | 2026-10-16 06:53:33
 nse_mcp  | corporate_actions |                     | unavailable |      | 2026-10-16 06:53:33
```

The bars written to `price_bars`:

```
 date       | close     | volume      | provider | retrieved_at (UTC)
 2026-10-16 | 2433.1000 |  4779833.00 | kite_mcp | 2026-10-16 06:53:33   ← fetched at 12:23 IST, mid-session
 2026-10-15 | 2350.0000 |  9150523.00 | kite_mcp | 2026-10-16 06:53:33   ← matches NSE MCP and the bhavcopy file
```

> The 2026-10-16 bar was taken during trading hours, so its "close" of 2433.1 is the price at 12:23 IST.
> NSE's end-of-day close was 2465.0. The next price refresh overwrites that date's row in place. Every
> metric computed in between (S5) used 2433.1, and the doc's later examples show that value.

**What the frontend receives.** `GET /api/v1/instruments/NSE:TCS/history`, captured live and cut:

```json
{"as_of": "2026-10-16", "interval": "1d", "currency": "INR",
 "adjustment": "split_adjusted (splits and bonuses; cash dividends not adjusted)", "adjustment_version": "adjust-v1",
 "bars": [{"date": "2024-01-01", "open": 3790.0, "high": 3832.0, "low": 3773.0, "close": 3811.1, "volume": 825907.0, "provider": "nse_bhavcopy_file"},
          …,
          {"date": "2026-10-16", "open": 2382.0, "high": 2446.0, "low": 2375.7, "close": 2433.1, "volume": 4779833.0, "provider": "kite_mcp"}],
 "coverage": {"bars": 689, "by_provider": {"nse_bhavcopy_file": 679, "kite_mcp": 10}, "first": "2024-01-01", "last": "2026-10-16"},
 "freshness": "fresh", "data_age_days": 0, "conflicts": [],
 "corporate_actions": [{"ex_date": "2021-10-14", "kind": "dividend", "ratio": 7.0,
                        "description": "Interim Dividend - Rs 7 Per Share", "provider": "nse_mcp"}, …],
 "lineage": [{"provider": "kite_mcp", "dataset": "history", "status": "ok", "rows": 10, "source_id": "get_historical_data",
              "adjustment": "split_adjusted", "retrieved_at": "2026-10-16T06:53:33.023711+00:00",
              "detail": {"coverage": {"kite_mcp": 10, "overlap": 0, "conflicts": 0},
                         "warnings": ["Corporate actions unavailable (nse_bhavcopy: circuit open after repeated failures; retrying later); NSE prices could not be split-adjusted."]}},
             {"provider": "nse_mcp", "dataset": "history", "status": "unavailable",
              "reason": "nse_bhavcopy: circuit open after repeated failures; retrying later", …}, …],
 "data_state": "ok", "data_state_reason": null}
```

**What the page shows.** The chart draws all 689 bars. The sources panel shows "679 from the NSE bhavcopy
file, 10 from Kite". The lineage list shows NSE as unavailable with the circuit-breaker reason, so the user
can see why the last 10 bars came from the broker.

---

## S4. Financial statements from NSE filings (XBRL)

| | |
|---|---|
| **Trigger** | `prices` job (step 4); uploading a statements file (`PUT /instruments/{ref}/financials`) |
| **Source** | `{NSE_FILINGS_BASE_URL}/integrated-filing-results?index=equities&symbol=TCS`, then each filing's XBRL document on `nsearchives.nseindia.com` |
| **Writes** | `financial_statement_periods` (values in INR crore, plus `derived` formulas and `xbrl_url`) |
| **Frontend** | Instrument → Financials tab; and through S5, every fundamental and valuation metric |

**Why not MCP?** Neither NSE MCP server nor the Kite MCP advertises a statements tool (checked against
their recorded `tools/list`). So statements come from the documents NSE itself publishes.

**Flow**

```
integrated-filing-results (JSON list, 14 filings for TCS)
   │  pick basis: consolidated if available, else standalone
   │  newest filing per period wins (type_Sub: Original / Revised)
   ▼
for each filing: GET xbrl URL (host allowlisted, size-capped, cached on disk forever)
   ▼
parse_xbrl() ─► extract_period(): 3-month contexts → quarterly, 12-month → annual
   │   income and cash-flow items: durations; balance-sheet items: instants
   │   money ÷ 10^7 → INR crore; per-share values kept as filed
   ▼
derived fields with their formula: ebit, ebitda, total_debt, capex, shares_outstanding
   ▼
financial_statement_periods (one row per instrument, source, period type and period end)
```

**1. The filings list.** One entry, cut (from the recorded fixture):

```json
{"symbol": "TCS", "type": "Integrated Filing- Financials", "type_Sub": "Original", "consolidated": "Consolidated",
 "audited": "Audited", "qe_Date": "30-SEP-2026", "broadcast_Date": "08-Oct-2026 22:07:33",
 "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1734607_08102026100810_WEB.xml", …}
```

`qe_Date` gives the period end, `consolidated` the basis, `xbrl` the document, and `broadcast_Date` the published date.

**2. XBRL concept → field.** Concepts are tried in order and the first non-zero one wins. For example,
`revenue` comes from `RevenueFromOperations`, `pat` from `ProfitOrLossAttributableToOwnersOfParent`, and
`cfo` from `CashFlowsFromUsedInOperatingActivities`. The full table is in DATA_FLOW_EXAMPLES.md §B.3.

**3. Derived values, with their formulas recorded:**

| Field | Formula | TCS FY2026 |
|---|---|---|
| `ebit` | profit before exceptional items and tax + finance costs − other income | 66,838 |
| `ebitda` | EBIT + depreciation | 66,838 + 5,560 = 72,398 |
| `total_debt` | BorrowingsCurrent + BorrowingsNoncurrent (leases excluded) | 0 |
| `capex` | purchase of PP&E + intangibles | 4,078 |
| `shares_outstanding` | paid-up capital ÷ face value (crore shares) | 362 |

**What the frontend receives.** `GET /api/v1/instruments/NSE:TCS/financials?period_type=annual`, captured live and cut:

```json
{"period_type": "annual", "currency": "INR", "unit": "crore", "reported_unit": "INR (XBRL)",
 "source": "NSE filings (consolidated)", "kind": "reported",
 "source_url": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1648408_09042026114218_WEB.xml",
 "ingested_at": "2026-10-15T17:30:44.122147+00:00", "data_state": "ok",
 "periods": [
   {"period_end": "2025-03-31", "fiscal_year": 2025, "revenue": 255324.0, "pat": 48553.0, "equity": 94756.0, …},
   {"period_end": "2026-03-31", "fiscal_year": 2026, "published_at": "2026-04-09",
    "revenue": 267021.0, "depreciation": 5560.0, "interest": 1227.0, "pbt": 65487.0, "tax": 16033.0, "pat": 49210.0,
    "eps": 136.01, "exceptional_items": -4526.0, "equity": 107240.0, "cash": 6417.0, "total_debt": 0.0,
    "cfo": 52094.0, "capex": 4078.0, "dividends_paid": 39571.0, "ebit": 66838.0, "ebitda": 72398.0,
    "shares_outstanding": 362.0, "minority_interest": 1238.0, …}]}
```

With `period_type=quarterly` the same endpoint returns 5 quarters, ending with Q2 FY2027
(period end 2026-09-30, revenue 73,188, PAT 13,884, EPS 38.37, published 2026-10-08).

**When there are no statements** (for example, a company with no integrated filing yet):

```json
{"periods": [], "period_type": "annual", "data_state": "missing",
 "data_state_reason": "No financial statements are ingested for this instrument. NSE MCP and Kite do not provide statements; upload a normalised statements file to enable fundamentals and valuation."}
```

Every fundamental and valuation metric for that stock is then `missing` with the reason
`"no financial statements ingested for this instrument"` (S5). None is shown as zero.

---

## S5. Metrics, scores and labels

| | |
|---|---|
| **Trigger** | End of the `prices` job; after a statements upload; after "Fetch latest"; inside "Refresh all" |
| **Inputs** | `price_bars` (S2, S3), `financial_statement_periods` (S4), sector series (S6) |
| **External calls** | **None.** No source and no LLM is called |
| **Writes** | `metric_snapshots` (one row per instrument per as-of date; `is_latest` marks the current one) |
| **Frontend** | Metrics tab, score card, screener, themes, overview, watchlists, research evidence |

**Flow**

```
load_bars() + statements ─► per instrument:
   technicals (returns, SMA, RSI, MACD, ATR…)   risk (volatility, drawdown, beta)   fundamentals   valuation
   │   each metric = value + unit + status + reason + formula + inputs
   ▼
across the universe: P/E percentile inside each sector (needs ≥ 5 peers with a valid P/E)
   ▼
scoring.score_universe(): percentile of each metric in its peer set → pillar means → overall (20% each)
   ▼
labels: stance, confidence, risk label, entry setup        ─► metric_snapshots.flat / detail / score / versions
```

**Worked example: NSE:TCS, as of 2026-10-16 (computed 06:53:55 UTC).** Every number below is the stored
value, and the inputs come from S3 and S4.

| Metric | Formula (as stored) | Inputs | Result |
|---|---|---|---|
| `close` | last adjusted close | 2026-10-16 bar | 2433.1 |
| `return_3m` | (close[t] / close[t−63] − 1) × 100 | start 2269.0, end 2433.1, 63 bars | 7.23% |
| `rsi_14` | 100 − 100 / (1 + Wilder avg gain / avg loss) | gain 29.36, loss 11.40 | 72.0 |
| `volatility_252d` | sample std of 252 daily log returns × √252 × 100 | window 252 | 29.54% |
| `roe` | PAT / average equity × 100 | PAT 49,210; avg equity (94,756 + 107,240) / 2 = 100,998 | 48.72% |
| `fcf` | CFO − abs(CapEx) | 52,094 − 4,078 | 48,016 cr |
| `debt_equity` | total debt / equity | 0 / 107,240 | 0.0 |
| `pe` | price / EPS (TTM) | 2433.1 / (29.45 + 37.92 + 36.90 + 38.37 = 142.64) | 17.06 |
| `ev_ebitda` | EV / EBITDA | EV = 2433.1 × 362 + 0 debt + 1,238 minority − 6,417 cash = 875,603.2; EBITDA 72,398 | 12.09 |
| `peg` | P/E / EPS growth % | 17.06 / 1.36 | 12.58 |
| `pe_sector_percentile` | mid-rank percentile of P/E among sector peers | 63 IT peers | 30.2 |
| `sector_rs_ratio` | sector RS-Ratio (rrg-v1) | NIFTY IT vs NIFTY 50 (S6) | 107.7 |

P/E uses trailing-twelve-month EPS because the latest quarter (2026-09-30) is newer than the last annual
report (2026-03-31). The basis is stored with the value: `"TTM to 2026-09-30 (sum of the latest four quarterly EPS as filed)"`.

**From metrics to the score (`score-v1`).** Each metric becomes a 0–100 percentile in its peer set,
oriented so higher is better (for example, a lower P/E scores higher). A pillar is the plain mean of its
metric percentiles. The overall score is the mean of five pillars at 20% each:

| Pillar | Metric percentiles (value → percentile) | Pillar | × 20% |
|---|---|---|---|
| Fundamentals | roe 48.7→97.9, roce 55.0→98.9, revenue growth 4.6→35.7, PAT growth 1.4→41.6, EBITDA margin 27.1→87.9, D/E 0.0→92.7, interest cover 54.5→89.9, CFO/PAT 1.06→50.2 | 74.3 | 14.87 |
| Valuation (vs sector) | pe 17.1→69.8, pb 8.2→12.3, ev/ebitda 12.1→69.8, fcf yield 5.5→74.6, dividend yield 4.5→92.6 | 63.8 | 12.77 |
| Momentum | 3M 7.2→73.1, 6M −5.6→43.1, 12M −19.6→40.8, RS 6M −1.1→43.1, MA alignment 50→60.8, from 52w high −27.4→48.3 | 51.5 | 10.30 |
| Risk | volatility 29.5→88.2, downside vol 21.8→81.4, max drawdown −40.4→35.2, beta 0.83→69.5 | 68.6 | 13.71 |
| Context | sector RS-Ratio 107.7→97.2, RS-Momentum 106.2→97.2, RS vs sector 3M −0.9→57.8, traded value→99.7 | 88.0 | 17.60 |
| **Overall** | (74.3 + 63.8 + 51.5 + 68.6 + 88.0) / 5 | | **69.2** |

Then four deterministic labels:

- **Stance:** Favorable (score ≥ 65; Mixed 45 to below 65; Unfavorable below 45).
- **Confidence:** High (completeness 100% ≥ 85% and newest bar 0 days old ≤ 3).
- **Risk label:** High. It takes the worst of three buckets: volatility is Moderate, max drawdown of −40.4% is High, and beta is Low.
- **Entry setup:** Unfavorable. 6 of 8 conditions pass. The two that fail are listed with their numbers.

**What the frontend receives (1): the score card.** The `snapshot` block of `GET /api/v1/instruments/NSE:TCS`, captured live and cut:

```json
"snapshot": {"as_of": "2026-10-16", "computed_at": "2026-10-16T06:53:55.854799+00:00",
  "score": {"overall": 69.2, "complete": true, "band": "Upper-middle", "stance": "Favorable",
            "completeness_pct": 100.0, "missing_metrics": [], "confidence": "High",
            "risk": {"label": "High", "drivers": {"volatility": "Moderate", "max_drawdown": "High", "beta": "Low"}, "criteria": "Worst bucket across three measures. …"},
            "pillars": {"fundamentals": {"label": "Fundamentals and financial quality", "score": 74.3, "sufficient": true,
                                         "weight": 0.2, "contribution": 14.87, "metrics_used": 8, "metrics_total": 8,
                                         "metrics": [{"name": "roe", "label": "ROE", "value": 48.72373710370502, "score": 97.9,
                                                      "higher_is_better": true, "peer_scope": "universe"}, …]}, …},
            "top_positive": [{"metric": "avg_traded_value", "score": 99.7, …}, {"metric": "roce", "score": 98.9, …}, …],
            "top_negative": [{"metric": "pb", "value": 8.213187243565832, "score": 12.3, "peer_scope": "sector"}, …],
            "entry_setup": {"state": "Unfavorable",
                            "conditions": [{"key": "trend", "rule": "close > SMA50 > SMA200", "passed": false,
                                            "detail": "close 2,433.10, SMA50 2,240.99, SMA200 2,465.91"},
                                           {"key": "not_overbought", "rule": "RSI(14) < 75", "passed": true, "detail": "RSI 72.0"}, …],
                            "wait_for": ["Price trend: needs close > SMA50 > SMA200 (close 2,433.10, SMA50 2,240.99, SMA200 2,465.91)", …], …},
            "freshness": "fresh", "last_bar": "2026-10-16", "bars": 689,
            "bar_providers": {"nse_bhavcopy_file": 679, "kite_mcp": 10},
            "fundamentals": {"source": "NSE filings (consolidated)", "earnings_basis": "TTM to 2026-09-30 (…)", …},
            "peer_universe": {"size": 2576, "sector": "Information Technology", "exchange": "NSE"}},
  "calculation_version": {"fundamentals": "fundamentals-v1", "valuation": "valuation-v1", "technicals": "technicals-v1",
                          "risk": "risk-v1", "score": "score-v1", "entry": "entry-v1", "rrg": "rrg-v1",
                          "adjust": "adjust-v1", "reconcile": "reconcile-v1"}, "is_demo": false}
```

**What the frontend receives (2): every metric with its formula.** `GET /api/v1/instruments/NSE:TCS/metrics`, captured live and cut:

```json
{"as_of": "2026-10-16", "currency": "INR", "monetary_unit": "INR crore for statement values",
 "computed_at": "2026-10-16T06:53:55.854799+00:00", "coverage": {"available": 90, "total": 105},
 "metrics": [
   {"name": "pe", "label": "P/E (TTM)", "group": "valuation", "value": 17.057627593942794, "unit": "x", "status": "ok",
    "reason": null, "formula": "price / EPS (TTM to 2026-09-30 (sum of the latest four quarterly EPS as filed))",
    "inputs": {"price": 2433.1, "eps": 142.64}, "higher_is_better": false, "kind": "calculated"},
   {"name": "roe", "label": "ROE", "group": "fundamentals", "value": 48.72373710370502, "unit": "pct", "status": "ok",
    "formula": "PAT / average shareholders' equity * 100", "inputs": {"pat": 49210.0, "avg_equity": 100998.0}, …}, …],
 "data_state": "ok"}
```

**What the page shows.** The score card shows 69.2 ("Upper-middle"), five pillar bars with their
contributions, the stance, confidence and risk badges, and the best and worst metrics. The Metrics tab
lists all 105 metrics. Each row has a "how calculated" view built from `formula` and `inputs`. The 15
metrics without a value show their status and reason, for example `pe_history_percentile`:
`insufficient_history`, "needs point-in-time earnings history, which is not ingested".

> The score is recomputed after every price refresh. A later run on 2026-10-17 at 14:18 UTC gave TCS 70.2.
> The examples in this document use the 2026-10-16 capture, so the numbers agree with each other.

---

## S6. Sector rotation (RRG), every 12 hours

| | |
|---|---|
| **Trigger** | `rrg` job, `0 */12 * * *`; the Rotation page's refresh action (`POST /sector-rotation/refresh`) |
| **Inputs** | Stored index bars (NIFTY 50 and sector indices such as NIFTY IT, from S3) and equity bars (S2) |
| **External calls** | None |
| **Writes** | `sector_rotation_snapshots`, one per 12-hour slot. Running it again in the same slot updates that slot's row |
| **Frontend** | Rotation page (RRG chart and table), Overview sector list, Sectors in the screener |

**Flow**

```
build_series(): benchmark = NIFTY 50 closes
                each sector = its mapped index (e.g. NIFTY IT) if it has history,
                              else an equal-weight composite of its stocks (labelled "composite")
   ▼
rrg-v1:  weekly closes (Friday) → RS = sector / benchmark
         RS-Ratio    = 100 × RS / SMA(RS, 12 weeks)
         RS-Momentum = 100 × RS-Ratio / SMA(RS-Ratio, 5 weeks)
         quadrant: Leading (≥100, ≥100) · Weakening (≥100, <100) · Lagging (<100, <100) · Improving (<100, ≥100)
   ▼
flags (composites, stale or missing series) ─► snapshot for slot "2026-10-16T00"
```

If the calculation fails, nothing is written and the previous snapshot keeps being served. The API then
adds `last_refresh_failed`.

**What the frontend receives.** `GET /api/v1/sector-rotation`, captured live and cut to one sector:

```json
{"snapshot_id": 3, "slot": "2026-10-16T00", "computed_at": "2026-10-16T06:37:49.434001+00:00",
 "data_as_of": "2026-10-16", "stale": false, "age_hours": 5.4, "refresh_schedule": "0 */12 * * *",
 "version": "rrg-v1", "params": {"ratio_window": 12, "momentum_window": 5, "trail": 10, "frequency": "W-FRI"},
 "benchmark": "NIFTY 50", "benchmark_kind": "index", "price_basis": "split-adjusted closes (not total return)",
 "coverage": {"sectors_total": 22, "sectors_plotted": 22, "composites": 15},
 "sectors": {
   "Information Technology": {"status": "ok", "quadrant": "Leading", "direction": "north-east",
     "trail": [… 7 earlier weeks …,
               {"date": "2026-10-02", "rs_ratio": 100.7850399692859, "rs_momentum": 99.89392282607972},
               {"date": "2026-10-09", "rs_ratio": 100.84723707753305, "rs_momentum": 100.83471272077946},
               {"date": "2026-10-16", "rs_ratio": 107.62551564030802, "rs_momentum": 106.12883194182614}],
     "returns": {"1M": 8.7384164757208, "3M": 8.221448954035026, "6M": -0.5910269505775112, "12M": -10.225731007430694},
     "series_kind": "index", "series_note": "sector index NIFTY IT", "members": 69, "index_symbol": "NIFTY IT", "last_bar": "2026-10-16"},
   …},
 "flags": ["Capital Goods: equal-weight composite of 168 constituent(s) in the tracked universe; no sector index mapped", …],
 "interpretation": "Quadrants describe relative strength and its momentum against the benchmark over the lookback. They are descriptive and do not predict future performance."}
```

**Reading the IT trail.** Two weeks earlier IT sat just below 100 on momentum (Weakening side). It then moved to
107.6 / 106.1, which is up and to the right, into **Leading**. This matches the 8.7% one-month sector return.

**What the page shows.** Each sector's trail, 2 weeks long by default. − and + change its length, ◀ Week and
Week ▶ step the whole picture back and forward one week at a time ("2 weeks back · week ending …"), and at the
earliest computed week a link loads more weeks on demand. Also shown: quadrant shading, and a table of RS-Ratio,
RS-Momentum and returns. Composite sectors carry a "composite" badge with the note from `flags`, so the user
knows 15 of 22 sectors are built from member stocks rather than an index. Passing `ratio_window`,
`momentum_window`, `trail` or `end` computes an on-demand view (`"on_demand": true`) that is not stored.

---

## S7. Monthly theme snapshots

| | |
|---|---|
| **Trigger** | `themes` job, 06:30 on the 1st of each month; "Refresh all" when the metrics are newer than a theme's last snapshot; "Refresh" on a theme (`POST /themes/{id}/refresh`) |
| **Inputs** | Latest `metric_snapshots` (S5); the theme's stored spec |
| **External calls** | None, unless the manual refresh fetches the latest prices first (S3) |
| **Writes** | A new **immutable** `theme_snapshots` row with constituents and exclusions. Earlier snapshots are never changed |
| **Frontend** | Themes page, the "In themes" list on an instrument, Overview theme leaders |

**Flow (Price momentum, theme 1)**

```
spec: universe NSE EQ, avg traded value 20D ≥ 5e7
      filters  return_6m > 0, return_12m > 0
      rank by  equal-weight percentile of return_1m, 3m, 6m, 12m (high first), top 25
      exclude  governance / insolvency / data-integrity red flags
   ▼
theme_dsl.evaluate() over 2,576 metric rows
   ├─ fails a filter or has a missing metric → exclusion with the reason and value
   └─ passes (455) → ranked → top 25 stored with each value and the price at the snapshot
   ▼
snapshot (period "2026-10", revision = previous revision of that month + 1)
```

**What the frontend receives.** `GET /api/v1/themes/1`, captured live and cut:

```json
{"theme": {"id": 1, "slug": "price-momentum", "name": "Price momentum", "category": "Momentum and trend",
           "interpreted_filters": ["Universe: NSE EQ", "Avg traded value 20D ≥ 5e+07", "Return 6M > 0", "Return 12M > 0",
                                   "Rank by equal-weight percentile of: Return 1M (high first), …", "Exclude instruments with governance, insolvency or data-integrity red flags"],
           "version": 1, "refresh_schedule": "30 6 1 * *", "next_refresh": "2026-11-01T06:30:00+05:30",
           "last_refreshed": "2026-10-16T06:38:06.988273+00:00",
           "latest_snapshot": {"id": 265, "period": "2026-10", "revision": 7, "constituents": 25, "eligible_count": 455}, …},
 "snapshot": {"id": 265, "period": "2026-10", "revision": 7, "trigger": "data_refresh", "metrics_as_of": "2026-10-16",
              "universe_count": 2576, "eligible_count": 455, "prices_moved_since_snapshot": true,
              "constituents": [
                {"symbol": "TBZ", "name": "Tribhovandas Bhimji Zaveri Limited", "sector": "Consumer Durables", "rank": 1, "score": 99.011,
                 "values": {"return_1m": 37.858709123844015, "return_3m": 182.38878256454325, "return_6m": 385.3977844914401, "return_12m": 292.31645775679635},
                 "price_at_snapshot": 723.0, "latest_close": 759.15, "change_since_snapshot_pct": 5.0,
                 "stance": "Mixed", "risk_label": "Very High"}, …],
              "exclusions": [{"symbol": "TATASTEEL", "reason": "failed: return_6m gt 0 (value -16.1042)"},
                             {"symbol": "HARDWYN", "reason": "failed: liquidity below 5e+07 (value 7.53678e+06)"}, …],
              "versions": {…, "theme_dsl": "theme-dsl-v1"}},
 "data_state": "ok"}
```

**What the page shows.** The ranked list, each stock's values, and "price at snapshot → latest close" (TBZ: 723.0 → 759.15, +5.0%). Because
`prices_moved_since_snapshot` is true, the page says prices have moved since the snapshot. It also shows the exclusion reasons, the next
refresh date, and a history list from `GET /themes/1/snapshots` (revisions 7, 6, … of October, each with its top five).

---

## S8. Alerts, every 15 minutes

| | |
|---|---|
| **Trigger** | `alerts` job, `*/15 * * * *`; also the last step of "Refresh all" |
| **Inputs** | Latest metric snapshots, the last two RRG snapshots, theme snapshots, filings |
| **External calls** | None. Delivery is the in-app history only |
| **Writes** | `alert_events` (a dedupe key per trigger, so the same event is never recorded twice) |

**Flow.** For each active alert the job reads the stored value. A `price_move` alert on `1W` reads
`return_1w`. It fires when abs(value) ≥ threshold. The dedupe key is `"{as_of}:{window}"`, so one
data date fires once at most.

**What the frontend receives.** `GET /api/v1/alerts`, captured live:

```json
{"items": [{"id": 2, "kind": "price_move", "instrument_id": 69, "symbol": "HDFCBANK", "params": {"window": "1W", "pct": 0.1},
            "status": "active", "channel": "in_app", "last_triggered_at": "2026-10-16T06:45:01.118615+00:00", …}],
 "history": [{"id": 2, "alert_id": 2, "kind": "price_move", "message": "HDFCBANK moved +3.4% over 1W (threshold 0.1%), as of 2026-10-16",
              "triggered_at": "2026-10-16T06:45:01.115473+00:00", "delivery_status": "recorded"},
             {"id": 1, "alert_id": 2, "kind": "price_move", "message": "HDFCBANK moved +5.0% over 1W (threshold 0.1%), as of 2026-10-15",
              "triggered_at": "2026-10-15T18:30:00.467018+00:00", "delivery_status": "recorded"}],
 "kinds": ["price_move", "indicator_cross", "theme_membership", "sector_quadrant", "data_quality", "filing"],
 "delivery_note": "Notifications are recorded in this in-app history. No email, SMS or push channel is configured, so none is claimed. Alerts never place orders."}
```

The two events come from two data dates (2026-10-15 and 2026-10-16). Every run in between found the same
key already recorded, so no duplicate was written. Each run is a `job_runs` row, for example
`{"kind": "alerts", "status": "succeeded", "detail": {"evaluated": 1, "triggered": 0}}`.

Creating an alert (`POST /alerts`) requires an `idempotency_key`. Sending the same key again returns the existing alert with `"created": false`.

---

## S9. Kite login, then broker backfill

| | |
|---|---|
| **Trigger** | The user opens the Kite login link from the Data status page, then the page checks the session |
| **Sources** | Kite MCP `login` (issues the link), then `get_profile` (confirms the session) |
| **Writes** | On the first check after authorisation: a `broker_backfill` job → S3 for short or stale instruments → S5 → S6 |

**Flow**

```
Status page login action → POST /sources/broker/login-link → Kite MCP login → {"login_url": …} (fresh each click; Kite links expire)
user approves in Kite's own page (the app never sees the password)
Status page → POST /sources/broker/login → get_profile ok → state "authorized"
   └─ state changed → start broker_backfill: instruments with < 250 bars or an old last bar
                      → full history with Kite filling gaps → metrics → RRG → clear the API cache
```

Kite's approval is bound to the MCP session. The API keeps that session id in `backend/var/kite_mcp_session.json`
(owner-only, never logged or sent to the browser; `KITE_PERSIST_SESSION=false` turns this off) and reuses it after a
restart, re-checking at start whether the approval still applies. While logged in it re-confirms the session every
5 minutes (`KITE_KEEPALIVE_SECONDS`). Re-reading the advertised tools ("Re-check sources") keeps the
session, so an approval survives it. The Data status page has a Kite session panel at the top with "Log in to Kite",
"Log in again" after Kite ends a session (it does so daily), "I have approved it" and "Disconnect Kite"
(`POST /sources/broker/logout`, which discards the saved session). If Kite itself forgets the session (HTTP 400 "Invalid session ID"), the client
opens a new one and the status says plainly that the approval must be given again. `GET /sources/status` re-asks
Kite (`get_profile` only) when its last check is older than 30 s (10 minutes while authorised), so the page never
shows a stale "login required" after you approve. Login links are issued only when you click to open one.

Before login, every Kite call ends as `status: "login_required"` with the reason
`"broker login required: authorise the Kite session from the Data status page"`. That status appears
in every `sources[]` list (S10, S16). It is never shown as a price.

> Only 7 Kite tools are allowlisted (`search_instruments`, `get_quotes`, `get_ltp`, `get_ohlc`,
> `get_historical_data`, `get_profile`, `login`). The 15 order, holdings and position tools are blocked in the connector.

---

# Part 2: data going out (a user opens a page or clicks)

## S10. Instrument page: the live quote

| | |
|---|---|
| **Trigger** | Opening `/instrument/NSE:TCS` → `GET /api/v1/instruments/NSE:TCS?quote=false` (stored data, answers at once) and `GET /api/v1/instruments/NSE:TCS/quote` (live quote, separately) |
| **Live sources** | NSE CM market MCP `cm_get_stock_quote` **and** Kite MCP `get_quotes`, in parallel |
| **Deadline** | The quote endpoint answers within 8 s (`QUOTE_DEADLINE_SECONDS`). If no source has answered by then it returns `"status": "pending"` and keeps fetching in the background; the page asks again every 4 s and the next answer comes from the cache |
| **From the database** | Instrument row, latest metric snapshot (S5), theme memberships (S7), sector peers |
| **Cache** | Merged quote 60 s (`CACHE_TTL_QUOTE`). An `unavailable` result is never cached |

**Flow**

```
GET /instruments/NSE:TCS
 ├─ resolve "NSE:TCS" → instruments.id 79 (404 instrument_not_found if absent)
 ├─ metric_snapshots (is_latest) ─────────────────────────────────────────────► "snapshot"
 ├─ market_data.quote("TCS"):  cache "quote:TCS" hit? → return it, "cache": "hit"
 │     miss → in parallel:  NSE cm_get_stock_quote {"symbol":"TCS"}   Kite get_quotes ["NSE:TCS"]
 │            parse each → DataPoint per field (value, provider, as_of, status)
 │            reconcile each field (precedence nse_mcp, kite_mcp; last_price tolerance 1%)
 │            → "quote" {fields, conflicts, sources}
 ├─ theme snapshots containing id 79 ─────────────────────────────────────────► "themes"
 └─ latest snapshots of stocks in the same sector ────────────────────────────► "peers"
```

### Case A: NSE answers, Kite needs a login (2026-10-16, 12:00 UTC)

**1. What NSE returned** (`result.content[0].text`, parsed):

```json
{"updatedAt": "2026-10-16T11:59:27.105411880Z",
 "stock": {"symbol": "TCS", "series": "EQ", "openPrice": 2382.0, "highPrice": 2484.7, "lowPrice": 2375.7,
           "preClosePrice": 2350.0, "lastTradedPrice": 2465.0, "change": 115.0, "perChange": 4.89,
           "volume": 12166634, "fiftyTwoWeekHigh": 3350.0, "fiftyTwoWeekLow": 1976.8,
           "latestTimestamp": "2026-10-16 16:00:28", …}}
```

**2. Parsing.** `unwrap_quote` finds `stock`, and `QUOTE_KEYS` renames the fields: `lastTradedPrice` → `last_price`,
`preClosePrice` → `prev_close`, `perChange` → `change_pct`. `latestTimestamp` is read as IST and becomes
`as_of`. Values from NSE get the status `delayed`, because NSE states its MCP data is delayed.

**3. Kite** answered `login_required`, so each field has one value, and that value wins.

**4. What the frontend receives** (cut to one of seven fields):

```json
"quote": {"symbol": "TCS", "status": "ok", "currency": "INR", "is_demo": false, "cache": "miss",
  "fields": {
    "last_price": {"value": 2465.0, "provider": "nse_mcp", "status": "delayed",
                   "reason": "NSE MCP live data is delayed by a few minutes",
                   "as_of": "2026-10-16T16:00:28+05:30", "retrieved_at": "2026-10-16T12:00:38.548633+00:00",
                   "currency": "INR", "source_id": "cm_get_stock_quote", "filled_from_fallback": false, "conflict": null},
    "open": {…}, "high": {…}, "low": {…}, "prev_close": {…}, "volume": {…}, "change_pct": {…}},
  "conflicts": [],
  "sources": [{"provider": "nse_mcp", "dataset": "quote", "status": "ok", "source_id": "cm_get_stock_quote", …},
              {"provider": "kite_mcp", "dataset": "quote", "status": "login_required",
               "reason": "broker login required: authorise the Kite session from the Data status page", …}]}
```

**5. What the page shows.** The page renders from stored data first (score, themes, peers, tabs) and shows
"Fetching the live quote from NSE and Kite…" in the price area. Hovering a row in the screener loads that stored
data in advance, so the click opens the page at once. When the quote arrives: "₹2,465.00", "+4.89 vs previous close", a yellow **Delayed quote** badge, and
"NSE MCP, as of 16 Oct 2026 16:00". The same page also shows the score card from `snapshot` (S5), the theme
list (for example "Net-cash businesses #4", "High ROCE / ROIC #14"), and a table of 69 IT peers.

### Case B: every quote source fails (2026-10-17, 15:06 UTC, a Saturday)

NSE's CM server answered HTTP 504, and Kite still needed a login. Captured live:

```json
"quote": {"symbol": "TCS", "status": "unavailable", "currency": "INR", "is_demo": false, "cache": "miss",
  "conflicts": [],
  "sources": [{"provider": "nse_mcp", "dataset": "quote", "status": "unavailable", "reason": "nse_cm_market returned HTTP 504", …},
              {"provider": "kite_mcp", "dataset": "quote", "status": "login_required",
               "reason": "broker login required: authorise the Kite session from the Data status page", …}]}
```

There is no `fields.last_price`, and no price was made up. The result was not cached, so the next page load
tries again. The rest of the response was unaffected: `data_state` was `ok`, because the snapshot as of
2026-10-16 was still there. **The page shows** "No quote", with a list: "NSE MCP: unavailable",
"Kite: login required". The score card, chart and peers still render from stored data.

---

## S11. When NSE and Kite disagree, or one is missing

`reconcile()` (version `reconcile-v1`) decides each field separately:

| Situation | Rule | Result |
|---|---|---|
| Only one source has a value | Use it. If it is not the first in precedence, set `filled_from_fallback: true` | `status` from that source |
| Both have values within tolerance | Use the first in precedence | `ok` / `delayed`, `conflict: null` |
| Both have values beyond tolerance | Keep the first in precedence and record the disagreement | `status: "conflict"` and a `conflict` object |
| Neither has a value | No value | The field is absent and the quote `status` is `unavailable` |

Tolerances: `close` 0.1%, `last_price` 1%, `volume` 5%. These are shown on the Data status page from
`GET /sources/status` → `precedence.tolerances_pct`.

**Real example.** The real `reconcile()` was run on two real TCS prices from 2026-10-16: NSE's 2465.0 (16:00 IST)
and Kite's 2433.1 (12:23 IST).

```json
// both present, 1.29% apart > 1% tolerance
{"status": "conflict", "provider": "nse_mcp", "value": 2465.0,
 "conflict": {"field": "last_price", "values": {"nse_mcp": 2465.0, "kite_mcp": 2433.1},
              "diff_pct": 1.2941, "chosen": "nse_mcp", "tolerance_pct": 1.0}}

// NSE timed out, so Kite fills the gap
{"status": "ok", "provider": "kite_mcp", "value": 2433.1, "filled_from_fallback": true}
```

**Where it surfaces.** For a quote, the field's `conflict` and the response's `conflicts[]` make the
page show a red **"Sources disagree by 1.29%"** badge next to the price. For daily bars, the conflicts
are stored in `source_conflicts` (one row per date and field) and returned in `history.conflicts[]`. The
chart tab lists them as date, field, both values, chosen source, and difference.

---

## S12. Instrument page: chart, metrics and financials tabs

Opening the page triggers four reads. Only the first one makes live calls:

| Tab | Request | Live call? | Built from | Scenario |
|---|---|---|---|---|
| Header, score, themes, peers | `GET /instruments/{ref}` | Quote only (S10) | metric snapshot, theme snapshots | S5, S7, S10 |
| Price chart | `GET /instruments/{ref}/history?limit=800` | No | `price_bars`, `source_conflicts`, `corporate_actions`, `data_source_records` | S2, S3 |
| Metrics | `GET /instruments/{ref}/metrics` | No | `metric_snapshots.detail` and `.flat` | S5 |
| Financials | `GET /instruments/{ref}/financials?period_type=annual\|quarterly` | No | `financial_statement_periods` | S4 |

**"Fetch latest" button** (`POST /instruments/NSE:TCS/refresh` → `{"job_id": …, "status": "running"}`):
runs S3 for this one symbol, then S5, then clears the API cache and `quote:TCS`. The page polls
`GET /jobs/{id}` (`api/client.ts`) until the job finishes, then refetches the requests above.

**Empty states, each with the server's reason:**

| Tab | `data_state` | `data_state_reason` shown on the page |
|---|---|---|
| Chart | `missing` | "No price history ingested. Check the Data status page for source errors." |
| Metrics | `missing` | "No metrics computed: price history has not been ingested." |
| Financials | `missing` | "No financial statements are ingested for this instrument. NSE MCP and Kite do not provide statements; …" |
| Index (e.g. NIFTY 50) | — | "Indices are not scored": scores, themes and entry setup apply to tradable instruments |

---

## S13. Screener

| | |
|---|---|
| **Trigger** | Opening the Screener page, changing a filter, sort or page |
| **Requests** | `GET /screener/metrics` (catalog, cached 1 h in the browser), `GET /sectors`, `GET /screener?…` |
| **External calls** | None. It reads the latest `metric_snapshots` only |
| **Validated in the browser** | Yes: `ScreenerSchema` (zod) in `frontend/src/api/schemas.ts` |

**Flow**

```
GET /screener?filters=[{"metric":"roce","op":"gt","value":25}]&sectors=Information Technology&sort=overall_score&order=desc&page=1
  ├─ filters: JSON list, ≤ 25, each metric must be in METRIC_CATALOG, op in gt/gte/lt/lte/eq/between → else 422 invalid_filters
  ├─ sort: must be a catalog metric or symbol/name/sector → else 422 invalid_screen
  ├─ for each latest snapshot: sector match → search match → every filter:
  │      value missing → excluded and counted (never treated as 0)
  │      fails → excluded
  ├─ sort (missing values last) → page
  └─ per row: requested columns; a null value gets {status, reason} from the metric detail
```

**What the frontend receives.** `GET /api/v1/screener` with the defaults, captured live and cut:

```json
{"page": 1, "page_size": 25, "total": 2576, "universe": 2576, "excluded_for_missing_filter_metric": 0,
 "sort": "overall_score", "order": "desc", "as_of": "2026-10-16", "computed_at": "2026-10-16T06:53:55.854799+00:00",
 "exchange": "NSE", "currency": "INR", "calculation_version": {…},
 "columns": [{"key": "close", "label": "Close", "unit": "price", "group": "price"}, {"key": "overall_score", …}, …],
 "items": [
   {"instrument_id": 83, "symbol": "TECHM", "name": "Tech Mahindra", "sector": "Information Technology", "currency": "INR",
    "values": {"close": 1770.0, "overall_score": 76.3, "fundamentals_score": 72.3, "valuation_score": 51.8, "momentum_score": 81.0,
               "risk_score": 85.4, "context_score": 90.9, "return_3m": 12.53, "return_12m": 21.38, "pe": 28.39, "roe": 16.89,
               "debt_equity": 0.0738, "volatility_252d": 29.16, "completeness_pct": 100.0},
    "missing": {}, "stance": "Favorable", "risk_label": "Moderate", "confidence": "High", "score_complete": true,
    "entry_state": "Setup favorable", "freshness": "fresh", "last_bar": "2026-10-16", "is_demo": false},
   …,
   {"instrument_id": 1360, "symbol": "KTKBANK", "name": "The Karnataka Bank Limited", "sector": "Financial Services",
    "values": {"close": 328.95, "overall_score": 72.1, "pe": 8.65, "roe": 10.36, "debt_equity": null, …},
    "missing": {"debt_equity": {"status": "not_applicable", "reason": "not applicable to banks, NBFCs or insurers"}}, …}, …]}
```

**The bank row shows the missing-data rule.** Debt/equity is meaningless for a bank, so it is
`null` with the status `not_applicable`. The page shows a dash with that reason as a tooltip, not 0. The
metric is also left out of the bank's fundamentals pillar instead of being scored as zero.

**CSV export** (`GET /screener/export.csv`) returns the same rows. Its first line records the exchange,
currency, as-of date, computed time and source note, and the second line says "Empty cells mean the value
is unavailable, not zero." Text cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so a
spreadsheet cannot run them as formulas.

---

## S14. Research board

| | |
|---|---|
| **Trigger** | The Research page's generate action → `POST /api/v1/research/stock {"instrument": "NSE:TCS"}` (rate-limited) |
| **Live sources** | Quote (S10), web search (ddgs), LLM (OpenRouter `z-ai/glm-5.3-flash`) |
| **From the database** | Latest metric snapshot (S5) |
| **Writes** | `research_reports` (the whole response), `research_sources` (one row per web result) |
| **Read back** | `GET /instruments/{ref}/research` returns the latest stored report |

**Flow**

```
1. snapshot (S5) + live quote (S10)
2. web search: "{name} {symbol} NSE results announcement" → up to WEB_SEARCH_MAX_RESULTS hits
3. evidence bundle: every item gets an id the model must cite
      M:<metric>  from the snapshot     Q:<field>  from the quote     S1…Sn  web results
4. LLM call with a Pydantic schema (LLMReport); retried once with a repair prompt if the JSON is invalid
5. grounding check on every point:
      cited ids must exist; every number in the text must appear in the cited evidence
      failing points are dropped and counted; if more than half fail → deterministic report instead
6. any empty section is filled from the deterministic (rule-based) report
7. store and return
```

**Real run: report 12, TCS, 2026-10-15 18:16 UTC.**

**Step 2: the search** returned 8 results:

```json
"web": {"provider": "ddgs", "available": true, "results": 8,
        "query": "Tata Consultancy Services TCS NSE results announcement", "retrieved_at": "2026-10-15T18:16:21.039812+00:00"}
```

**Step 3: the evidence bundle** had 56 items. Three of them:

```json
"M:close":      {"kind": "calculation", "label": "Last close", "value": 2350.0, "unit": "price", "formula": "last adjusted close", "as_of": "2026-10-15"},
"Q:last_price": {"kind": "quote", "label": "last price", "value": 2350.0, "provider": "nse_mcp", "as_of": "2026-10-15T16:00:28+05:30", "status": "delayed", "currency": "INR"},
"S2":           {"kind": "source", "source_type": "secondary", "published_at": "2026-10-08",
                 "title": "TCS Q2 Results 2026 Live Updates: TCS Q2 profit rises 4% QoQ; company declares interim dividend of Rs 12/share",
                 "url": "https://economictimes.indiatimes.com/markets/stocks/live-blog/…/liveblog/134779808.cms", "snippet": "…"}
```

The search results go to the model inside a delimited block, labelled as untrusted data. The system prompt
tells the model to ignore any instructions inside it.

**Step 4: the model's output** (validated against `LLMReport`). Each point cites evidence, and `kinds` says what it rests on:

```json
"pros": [{"text": "Exceptional profitability: ROE 48.72%, ROCE 55.03%, EBITDA margin 27.11%, PAT margin 18.43%.",
          "evidence": ["M:roe", "M:roce", "M:ebitda_margin", "M:pat_margin"], "kinds": ["calculation"]}, …],
"cons": [{"text": "Weak longer-term price performance: -8.02% over 6M, -20.97% over 12M, and -29.85% below the 52-week high; news reports cite a ~34-35% fall in 2026 YTD.",
          "evidence": ["M:return_6m", "M:return_12m", "M:dist_from_52w_high_pct", "S4", "S5"], "kinds": ["calculation", "source"]}, …],
"news": [{"text": "Q2 FY27 profit rose 4% QoQ; interim dividend of Rs 12/share declared with record date the following week.",
          "evidence": ["S2", "S7"], "kinds": ["source"]}],
"invalidation": [{"text": "If the price falls back below the 50-day SMA (2240.58), the recent 1M/3M recovery would lose technical support.",
                  "evidence": ["M:sma_50", "M:return_1m", "M:return_3m"], "kinds": ["calculation"]}]
```

**Step 5: grounding.** 3 of 32 points were dropped. Here is one, with the reason:

```json
"grounding": {"points_total": 32, "points_dropped": 3, "dropped": [
  {"section": "valuation_context", "text": "P/E (TTM) 17.28x sits at the 50th percentile among sector peers; …",
   "reason": "states number(s) not present in the cited evidence: 12.74, 46"}, …]}
```

The model's summary line also failed the check, so the rule-based line was shown instead, and the response says so:

```json
"llm": {"available": true, "model_id": "z-ai/glm-5.3-flash",
        "reason": "The model's summary stated a figure not found in its cited evidence, so the rule-based summary line is shown."},
"report": {"summary": {"text": "Research stance is Mixed with High confidence, from deterministic rules over the available metrics. No language-model interpretation was applied.",
                       "evidence": ["M:overall_score"], "kinds": ["calculation"]}, …}
```

**What the page shows.** A badge "Written by z-ai/glm-5.3-flash from the evidence below". Each point has
chips for its kinds (sourced, calculated, interpretation) and links to each evidence item it cites, so a
claim can be traced to the metric, quote or article behind it. A note says 3 points were removed by the
grounding check. The score, entry setup, quote and disclaimer come from the same response.

**Without an LLM or web search.** `mode` becomes `"deterministic"`, and `llm.reason` or `web.reason` says why
(for example `"No web search provider is configured."`). The report is built by rules from the same
evidence, so the page still renders every section and labels it "Rule-based summary, no language model".

---

## S15. Concall analyzer

| | |
|---|---|
| **Trigger** | The Concalls page's fetch-from-NSE action → `POST /instruments/NSE:TCS/concalls/auto` (background job). Or upload a transcript (`POST …/concalls`), then `POST …/concalls/analyze` |
| **Sources** | NSE corporate announcements (transcript PDFs), web search (ddgs), LLM (two schema-constrained calls) |
| **Writes** | `concall_transcripts`, `concall_analyses` |
| **Read back** | `GET /instruments/{ref}/concalls`, `GET /concalls` (all companies) |

**Flow**

```
1. NSE announcements (category "Analysts/Institutional Investor Meet/Con. Call Updates")
     → newest 4 transcript PDFs → text → concall_transcripts (FY, quarter, sha256, chars)
2. current = latest quarter (Q2 FY2027); previous = the quarter before (Q1 FY2027). If that quarter is missing,
   the latest earlier transcript is used, the result carries `comparison.warning` naming both quarters, and
   confidence is capped at Medium (e.g. HAL files only Q4 calls: Q4 FY2026 is compared with Q4 FY2025)
3. LLM #1 (CommitmentList): commitments from the PREVIOUS call, each with a verbatim quote
      → keep only those whose quote is found verbatim in the previous transcript
4. web search per important commitment → W1…Wn
5. LLM #2 (AssessedList): a verdict per commitment from the CURRENT call and the web sources
6. grounding: the evidence quote must be verbatim in the current transcript, or the cited Wn must
   exist and support it; "not_addressed" needs no quote. Ungrounded items are excluded from the score
7. score (concall-v1) = Σ(weight × credit) / Σ(weight) × 100
      credit: delivered 1 · partially 0.5 · in progress 0.5 · deferred 0.25 · not delivered 0 · not addressed 0
      weight: high 3 · medium 2 · low 1
```

**Real run: analysis 5, TCS, 2026-10-15 18:15 UTC.**

The two transcripts that NSE had on file:

```json
"transcripts": [
  {"id": 3, "label": "Q2 FY2027", "call_date": "2026-10-14", "chars": 52193,
   "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_14102026155941_SEInt14102026_signed.pdf", …},
  {"id": 4, "label": "Q1 FY2027", "call_date": "2026-07-15", "chars": 46187,
   "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_15072026193646_SEInt15072026_Signed.pdf", …}]
```

The model extracted 12 commitments from the Q1 call. The web search ran 6 queries and returned 7 results.
After grounding, 6 commitments could be scored:

| Id | Importance (weight) | Verdict (credit) | Grounded? | Counted |
|---|---|---|---|---|
| c1 | high (3) | in_progress (0.5) | yes, web | 1.5 / 3 |
| c2 | high (3) | partially_delivered (0.5) | yes, web | 1.5 / 3 |
| c3 | high (3) | partially_delivered (0.5) | yes, web | 1.5 / 3 |
| c6 | medium (2) | not_addressed (0) | yes | 0 / 2 |
| c7 | high (3) | in_progress (0.5) | yes, web | 1.5 / 3 |
| c12 | low (1) | not_addressed (0) | yes | 0 / 1 |
| c4, c5, c8–c11 | — | various | **no:** "the evidence quote was not found verbatim in the current transcript" | excluded |
| **Score** | | | | **6 / 15 × 100 = 40.0** |

One assessment, as returned (cut):

```json
{"id": "c1", "commitment": "Exit FY27 quarters at operating margin of 25% or above, striving to achieve it sooner",
 "category": "margin", "importance": "high", "target": "25%+ operating margin exit rate",
 "quote": "We want to exit at 25% plus and strive to achieve it sooner rather than later.",
 "verdict": "in_progress", "evidence_source": "web",
 "web_sources": [{"id": "W1", "title": "TCS Expects To Recoup Margin As AI Investments Taper, Bench Strength Gets Deployed",
                  "url": "https://www.msn.com/en-in/news/other/…", "published_at": "2026-10-09", "source_type": "secondary"}],
 "rationale": "The FY27 exit target of 25%+ is not yet due (currently Q2 FY27). …", "grounded": true, "credit": 0.5, "weight": 3.0}
```

**What the frontend receives** (`GET /instruments/NSE:TCS/concalls` → `latest.score`):

```json
{"value": 40.0, "label": "Weak follow-through", "status": "ok", "assessed": 6, "excluded_ungrounded": 6,
 "counts": {"in_progress": 2, "partially_delivered": 2, "not_addressed": 2}, "confidence": "Low",
 "formula": "sum(importance weight x verdict credit) / sum(importance weight) x 100",
 "credit_table": {"delivered": 1.0, "partially_delivered": 0.5, "in_progress": 0.5, "deferred": 0.25, "not_delivered": 0.0, "not_addressed": 0.0},
 "importance_weights": {"high": 3.0, "medium": 2.0, "low": 1.0}, "min_commitments": 3, "version": "concall-v1"}
```

**What the page shows.** "40 · Weak follow-through · Low confidence". Below that is a table of all 12
commitments: the quote from the previous call, the verdict, the evidence (a transcript quote or a web
link), and a "not counted" tag with the reason for the 6 that failed grounding. The overview list
(`GET /concalls`) ranks companies by score, for example HDFCAMC 50.0 and TCS 40.0, and lists companies
that have transcripts but no analysis yet as `pending`.

**Without an LLM**, `mode` is `"heuristic"`. Commitments are found from forward-looking phrases and
matched by shared key terms. Confidence is always Low, and `limitations` explains that this mode cannot
judge whether a stated number was met.

---

## S16. Ask the market

| | |
|---|---|
| **Trigger** | The Ask panel on the Overview page → `POST /api/v1/ask {"question": "…"}` (rate-limited) |
| **Live sources** | Depends on the question: quotes (S10), `get_bulk_quote`, Kite `get_quotes`, `nse_get_market_movers` |
| **From the database** | Metric snapshots, screener, RRG snapshot, stored bars |
| **LLM** | Two calls: one turns the question into a plan (`AskPlan`), one writes the answer (`AskAnswer`). Rules take over for either if the model is unavailable |

**Flow**

```
1. PLAN    question → LLM (AskPlan: intent, symbols, filters, sectors, sort, lookback)
                       the model sees only instruments named in the question, plus the sector list and metric catalog
           → sanitize: unknown symbols, metrics and sectors are dropped and noted in plan_notes
2. FRESH   stored bars older than the last session? → start the prices job in the background (at most every 30 min)
3. EVIDENCE by intent (each item has an id, a source, live true/false and as_of):
     instrument/compare → Q1 live quote (else last stored bar) + M1 metrics
     movers             → P1: rank all stocks on stored closes, re-rank the top 60 with today's price (NSE bulk quote, then Kite)
     screen             → S1: deterministic screener
     market             → B1 benchmark quote + L1 live movers (else best and worst stored 1-week returns)
     sector             → R1: latest RRG snapshot
4. ANSWER  LLM writes from the evidence only; any point citing an unknown id is removed
           without an LLM: a rule-based answer from the same evidence
```

**Six real questions, asked on 2026-10-17 (Saturday) between 15:04 and 15:06 UTC.** The NSE MCP servers
were timing out and Kite needed a login, so every answer fell back to stored data. Each item says so.

| Question | Plan (LLM) | Evidence | Answer (start) |
|---|---|---|---|
| "Compare TCS and INFY" | `compare`, symbols TCS, INFY | Q1, Q2 = last stored bars of 2026-10-16 (live quotes unavailable); M1, M2 = metrics | "Both are IT-sector stocks with near-identical overall scores (TCS 70.2 vs INFY 70.1). TCS leads on most metrics…" |
| "Which stocks rose most in the last 3 days?" | `movers`, lookback 3, desc | P1: 2,573 ranked, 0 live prices | "…PRISMX +50.0% (to 0.69), SHREERAMA +38.87% (to 56.98), AVROIND +32.63%…" |
| "IT stocks with ROCE above 25 and low debt" | `screen`, ROCE > 25, D/E < 1, sector IT, sort ROCE | S1: 8 of 2,576 match | "Eight IT stocks passed the screen…, led by TCS at ROCE 55.03, followed by OFSS (40.06)…" |
| "How is the market today?" | `market` | B1: NIFTY 50 last stored close; L1: stored 1-week returns ("Live movers unavailable (timeout)") | "…NIFTY 50 finished at 23,105.70, up 0.47% on the day…" |
| "Which sectors are leading?" | `sector` | R1: RRG snapshot as of 2026-10-16 | "Information Technology and Media Entertainment & Publication are the leading sectors…" |
| "How is TCS doing?" | `screen`, overall score ≥ 0 (**wrong:** see below) | S1: top 10 by score | "The evidence does not cover TCS…" |

**One answer in full: the movers question** (cut):

```json
{"question": "Which stocks rose most in the last 3 days?", "version": "ask-v2",
 "plan": {"intent": "movers", "symbols": [], "sort_direction": "desc", "lookback_days": 3, "limit": 10, …},
 "plan_source": "llm", "plan_notes": [],
 "data_refresh": {"stored_until": "2026-10-16", "expected_session": "2026-10-16", "started_job": null, "state": "current"},
 "evidence": [{"id": "P1", "kind": "movers_window", "title": "Largest rises over the last 3 sessions",
               "source": "0 live MCP prices, 2573 stored closes; history from the system database",
               "live": false, "as_of": "2026-10-16",
               "note": "NSE MCP bulk quote unavailable: timed out; Kite MCP quotes unavailable: broker login required: authorise the Kite session from the Data status page; 3 instrument(s) without enough stored history were skipped",
               "data": {"sessions": 3, "ranked": 2573, "direction": "gainers",
                        "top": [{"symbol": "PRISMX", "price": 0.69, "price_date": "2026-10-16", "live": false, "change_pct": 50.0,
                                 "from_date": "2026-10-13", "day_change_pct": 16.95, "up_sessions": 3, "above_sma20": true}, …],
                        "method": "change % = price of the day (live when NSE/Kite have a newer session, else last stored close) / stored close 3 sessions earlier - 1; …"}}],
 "answer": {"answer": "Over the last 3 sessions (2026-10-13 to 2026-10-16 closes), the biggest gainers were PRISMX +50.0% (to 0.69), …",
            "points": [{"text": "PRISMX led with +50.0% over 3 sessions, closing at 0.69 on 2026-10-16, up in all 3 sessions.", "evidence_ids": ["P1"]}, …],
            "caveats": […]},
 "answer_source": "llm",
 "llm": {"used": true, "model_id": "z-ai/glm-5.3-flash", "reason_not_used": null, "uncited_points_removed": []},
 "movers": [{"symbol": "PRISMX", "name": "Prismx Global Ventures Limited", "price": 0.69, "price_source": "system database",
             "change_pct": 50.0, "from_close": 0.46, "from_date": "2026-10-13", …}, …],
 "data_sources": {"live_mcp": 0, "system_db": 1},
 "llm_disclaimer": "This answer was generated by a language model from the data shown below. It can be wrong or incomplete, …",
 "disclaimer": "Research output, not investment advice. Live MCP data is delayed per NSE terms."}
```

**Citation check in action.** For the ROCE screen, the model wrote two points without valid evidence ids.
Both were removed and listed in `llm.uncited_points_removed`, and the answer text was kept.

**What the panel shows.** The answer with its points (each linked to its evidence), a table (`movers` or
`table`), an evidence list showing source, as-of date and whether each item was live, a caveats list
built from the evidence notes, and the LLM disclaimer.

> **Observed defect: "How is TCS doing?"** The model returned `intent: "screen"` with no symbols, even though
> TCS was in the instrument list it was given. `keep_named_symbols()` only removes symbols the question did
> not name; it does not restore a named symbol the model left out. So the answer said, correctly, that its
> evidence did not cover TCS. "Compare TCS and INFY" worked because the model kept both symbols. A fix
> would add symbols found by `_rule_symbols()` to the plan, or fall back to `rule_plan()` when the model
> drops them. It has not been fixed here.

---

## S17. Creating a custom theme

| | |
|---|---|
| **Trigger** | Themes page → describe a custom theme in a sentence → review the interpreted filters → confirm |
| **Requests** | `POST /themes/interpret` (nothing saved) → `POST /themes` with `confirmed: true` → `POST /themes/{id}/refresh` |
| **External calls** | None. The interpreter is rule-based (`"source": "rules"`) |
| **Writes** | `themes`, `theme_versions`; then a snapshot (S7) |

**Step 1: interpret.** Captured live:

```json
// request
{"text": "NSE companies with improving 3-year revenue and ROCE, low debt, positive 6-month relative strength, and moderate volatility",
 "name": "Quality compounders with momentum"}

// response
{"spec": {"name": "Quality compounders with momentum",
          "universe": {"exchange": "NSE", "instrument_types": ["EQ"], "sectors": null, "min_avg_traded_value": null},
          "filters": [{"metric": "revenue_cagr_3y", "op": "gt", "value": 0.0},
                      {"metric": "roce", "op": "gte", "value": 15.0},
                      {"metric": "debt_equity", "op": "lt", "value": 0.5},
                      {"metric": "rel_strength_6m", "op": "gt", "value": 0.0},
                      {"metric": "volatility_252d", "op": "between", "value": 20.0, "value2": 35.0}],
          "rank_by": [{"metric": "overall_score", "direction": "desc"}, {"metric": "rel_strength_6m", "direction": "desc"}],
          "limit": 25, "exclude_red_flags": true},
 "interpreted_filters": ["Universe: NSE EQ", "Revenue CAGR 3Y > 0", "ROCE ≥ 15", "Debt / equity < 0.5",
                         "Relative strength 6M vs benchmark > 0", "Volatility 1Y between 20 and 35", "Rank by …", "Exclude …"],
 "ambiguities": ["'improving revenue' read as 3-year revenue CAGR > 0%; set the growth rate you want",
                 "ROCE threshold assumed at 15%; an improving-ROCE trend metric is not available",
                 "'low debt' read as debt/equity < 0.5x; confirm the limit",
                 "'moderate volatility' read as 1-year volatility between 20% and 35%; confirm the range"],
 "matched_phrases": ["improving 3-year revenue", "roce", "low debt", "positive 6-month relative strength", "moderate volatility"],
 "requires_confirmation": true, "source": "rules",
 "note": "Review and adjust the filters and thresholds, then confirm to save. Nothing has been saved yet."}
```

**What the page shows.** Each filter as an editable row, and each ambiguity as a warning the user must
look at. The page is honest that "improving ROCE" cannot be expressed: there is no ROCE-trend metric, so it
became a fixed threshold.

**Step 2: save.** `POST /themes {"spec": …, "confirmed": true}` → `201` with the theme. Without
`confirmed: true` the API returns `409 confirmation_required` and repeats the interpreted filters.
The spec is a validated filter DSL: only catalog metrics and fixed operators. Nothing the user types is run as SQL or code.

**Step 3: refresh** creates the first snapshot, exactly as in S7.

---

## S18. AI filter on a theme

| | |
|---|---|
| **Trigger** | Themes page → a theme → the AI filter box (e.g. "only IT companies with low debt") → `POST /themes/{id}/ai-filter {"query": "…"}` |
| **Sources** | LLM (`RefinePlan`), with the rules interpreter as fallback |
| **Writes** | Nothing except an audit event. The theme's snapshots are untouched |

**Flow.** The model sees the theme's filters, the sector list and the metric catalog, and proposes extra
filters, sectors and a ranking. Each proposal is validated: an unknown metric or sector is dropped with a
note. The merged spec is evaluated on the latest metrics, using the same code as S7.

**Real run, theme 1 (Price momentum), 2026-10-17:**

```json
// request
{"query": "only IT and healthcare stocks with ROE above 15"}

// response (cut)
{"theme_id": 1, "version": "theme-ai-v1", "source": "llm", "model_id": "z-ai/glm-5.3-flash",
 "explanation": "Restrict the momentum theme to Information Technology and Healthcare stocks with ROE above 15%.",
 "added_filters": ["ROE > 15"], "sectors": ["Information Technology", "Healthcare"],
 "interpreted_filters": ["Universe: NSE EQ, sectors Information Technology, Healthcare", "Avg traded value 20D ≥ 5e+07",
                         "Return 6M > 0", "Return 12M > 0", "ROE > 15", "Rank by …", "Exclude …"],
 "notes": [], "eligible_count": 28, "universe_count": 108,
 "top_exclusion_reasons": [["failed: return_6m gt 0", 35], ["failed: return_12m gt 0", 21], ["failed: roe gt 15", 18],
                           ["missing metric: return_12m", 5], ["missing metric: roe", 1]],
 "members": [{"symbol": "LAURUSLABS", "sector": "Healthcare", "rank": 1, "score": 95.5357,
              "values": {"return_1m": 8.63, "return_3m": 35.31, "return_6m": 83.45, "return_12m": 142.43, "roe": 18.19},
              "close": 2070.0, "stance": "Favorable", "risk_label": "Moderate"}, …],
 "metrics_as_of": "2026-10-16", "saved": false,
 "note": "A filtered view of the latest data. The theme's monthly snapshots are not changed."}
```

**What the page shows.** The model's one-line explanation, the added filters, 28 matching stocks out of
108 in those two sectors, and why the other 80 dropped out. Six were dropped because a value was missing,
not because they failed.

---

## S19. Overview dashboard

| | |
|---|---|
| **Trigger** | Opening the home page → `GET /overview`, `GET /watchlists`; the header calls `GET /sources/status` every 60 s |
| **Live source** | NSE bhavcopy MCP `get_market_breadth`, in the background and never blocking the page |
| **From the database** | Metric snapshots, NIFTY 50 bars, RRG snapshot, theme snapshots, job runs |

**Market breadth never blocks the page:**

```
cache "overview:breadth" (5 min)? ──yes──► return it ("cache": "hit")
cache "overview:breadth_down" (2 min)? ──yes──► return the failure state
neither → start the check in the background, return {"state": "checking"} now
"Refresh" button (fresh=true) → wait for the check, at most 6 s
```

**Real states, as seen:**

```json
// NSE answered (2026-10-16, get_market_breadth {"date": "2026-10-16"}):
{"date": "2026-10-16", "total_stocks": 3675, "advances": 2058, "declines": 1502, "unchanged": 115, "ad_ratio": 1.37, "total_volume": 6182337837}

// first load on 2026-10-17, nothing cached yet:
{"state": "checking", "reason": "Checking NSE market breadth in the background; refresh in a few seconds."}

// the next load, after the background check timed out:
{"state": "unavailable", "reason": "NSE market breadth did not answer within 6s", "checked_at": "2026-10-17T15:07:56.489260+00:00", "cache": "hit"}
```

**The rest of the response is computed from stored data** (2026-10-17 capture, cut):

```json
{"benchmark": {"symbol": "NIFTY 50", "close": 23105.7, "last_bar": "2026-10-16", "return_1w": 2.5987, "return_1m": -0.4820,
               "sparkline": [23869.6, 23767.45, …]},
 "universe": {"instruments": 2576, "complete_scores": 683, "stale": 2,
              "breadth": {"advancing_1w": 1484, "declining_1w": 1069, "instruments": 2570}},
 "sectors": [{"sector": "Information Technology", "quadrant": "Leading", "return_1m": 8.77, "return_3m": 8.25, "series_kind": "index", …}, …],
 "top_scores": [{"symbol": "PVRINOX", "value": 77.2, "close": 1408.7, …}, {"symbol": "TECHM", "value": 76.7, …}, …],
 "theme_leaders": [{"theme": "Price momentum", "symbol": "TBZ", "period": "2026-10", "refreshed_at": "2026-10-16T06:38:06.988273+00:00", …},
                   {"theme": "Low realized volatility", "symbol": "CASTROLIND", …}, …],
 "jobs": [{"job_id": 274, "kind": "alerts", "status": "succeeded", "detail": {"evaluated": 1, "triggered": 0}, …}, …],
 "data_state": "ok"}
```

**What the page shows.** The NIFTY 50 card with its sparkline. The breadth tile shows either the NSE
numbers, "Checking…", or "NSE breadth unavailable: did not answer within 6s". Next to it is the
universe's own one-week breadth (1,484 up, 1,069 down), computed from stored data. Below are sector
quadrants, top scores, momentum leaders and laggards, theme leaders, recent jobs, the user's watchlists,
and the Ask panel (S16).

---

## S20. Data status page

| | |
|---|---|
| **Requests** | `GET /sources/status`, `GET /jobs`, `GET /audit/events`; buttons: `POST /sources/broker/login-link`, `POST /jobs/refresh-all`, `POST /sources/rediscover` |
| **Validated in the browser** | `SourcesSchema` (zod) |

`GET /sources/status` puts together the health that each connector tracks in memory (calls, errors,
latency, circuit breaker), the capabilities discovered from `tools/list`, and coverage counts from the
database. One connector, captured live and cut:

```json
{"key": "nse_bhavcopy", "label": "NSE bhavcopy MCP", "role": "primary: end-of-day history, corporate actions",
 "state": "degraded", "breaker": "half_open",
 "server": {"name": "nse-bhavcopy-redis-mcp", "version": "1.0.0"}, "tool_count": 21,
 "last_success_at": "2026-10-15T19:24:12.903083+00:00",
 "last_error": "timeout calling nse_bhavcopy: ReadTimeout", "last_error_at": "2026-10-16T06:53:04.656082+00:00",
 "latency_ms": 616.5, "calls": 2351, "errors": 1562, "retries": 718, "error_rate": 0.6644}
```

The rest of the response:

- `capabilities` per dataset: `{"supported": true, "tool": "get_stock_history"}`, or `{"supported": false, "reason": "…"}`.
  For example, `top_movers` on the bhavcopy server is not supported because the app cannot supply its required `direction` argument.
- `precedence` and `tolerances_pct` (S11).
- `coverage`: instruments, bars by provider, last bar, instruments with statements, stale instruments, conflict count, and calls by status over the last two days.
- `terms`: NSE's statement that MCP data is delayed and for informational use only.
- `scheduler.jobs` with their cron expressions.

**What the page shows.** One card per connector (NSE bhavcopy, NSE CM market, Kite, fundamentals files,
web search, LLM), each with its state, its error rate, and its last error in plain words. Kite's card
has a login action (S9). Below that are the job history and the latest audit events. Each Ask question
is recorded in the audit log (`action: "ask"`, the first 200 characters, the intent and the sources used).

---

## S21. Watchlists and the search box

**Search box** (`GET /instruments?q=TCS&page_size=8`). An exact symbol ranks first, then symbols that
start with the text, then names that start with it, then anything that contains it. So "TCS" lists
TCS before WSTCSTPAPR.

**Watchlists** (`GET /watchlists`). Each item joins the instrument with its latest metric snapshot.
Nothing is fetched live. Captured live:

```json
{"items": [{"id": 1, "name": "klklk", "items": [
   {"item_id": 1, "symbol": "AEPL", "name": "Artemis Electricals and Projects Limited", "sector": null,
    "close": 15.17, "return_1w": 2.8474576271186436, "return_1m": -4.4108380592312475,
    "overall_score": null, "partial_score": 42.3, "stance": "Unfavorable", "risk_label": "Moderate",
    "entry_state": "Insufficient data", "freshness": "fresh", "last_bar": "2026-10-16", …}]}],
 "currency": "INR", "note": "Watchlists are for monitoring only. No orders can be placed from this application."}
```

AEPL has no sector and no statements, so some pillars lack coverage. `overall_score` is therefore `null`
and a **partial** score of 42.3 is offered. The page labels it "partial" and does not present it as a full score.

---

# Part 3: when things go wrong

## S22. Failure catalogue

Every row below was observed in this project, either in the captures used above or in recorded tests.

| Situation | What actually comes back | What the backend does | What the frontend receives | Seen in |
|---|---|---|---|---|
| NSE MCP times out | `httpx.ReadTimeout` | Retries with backoff. Repeated failures open the circuit breaker: `"circuit open after repeated failures; retrying later"` | `sources[].status: "unavailable"` with that reason; breaker state on the status page | S3, S20 |
| NSE CM server error | HTTP 504 | `ProviderUnavailable`; reported, never cached | Quote `status: "unavailable"`, `"nse_cm_market returned HTTP 504"` | S10 case B |
| Unknown symbol at NSE | `{"error": "Symbol not found: NOSUCHSYM", "hint": "Use search_symbols tool…"}` with `isError: false` | `payload_error()` spots it and raises `ServerRefused`; no rows written | `unavailable` with the server's message | §E.4 of the schema doc |
| Kite session not approved | `"kite_mcp rejected the request (HTTP 400): Invalid session ID"` / "Please log in first" | `auth.state = "login_required"` | `status: "login_required"` + "authorise the Kite session from the Data status page" | S9, S10, S16 |
| Tool needs an argument the app cannot supply | `bind_args` → `BindError: cannot supply required argument(s) date, direction` | Capability marked unsupported; another server is used | `{"supported": false, "reason": …}`; status `not_supported` | S20 |
| Every price source fails for a refresh | — | `DataUnavailable("no price history for X: nse_mcp: unavailable (…); kite_mcp: login_required (…)")`; earlier bars kept | Old bars still served; `freshness` becomes `stale` after 4 days | S3 |
| Live sources down when answering a question | Timeouts | Falls back to stored bars and returns | `live: false`, `"note": "Live quote was unavailable…; showing the latest stored daily bar."` | S16 |
| Breadth slow | No answer in 6 s | Failure state cached 2 min (never a value) | `{"state": "unavailable", "reason": "…did not answer within 6s"}` | S19 |
| No statements for a company | — | Fundamentals and valuation metrics `missing` | `data_state: "missing"` on Financials; null metrics with reasons | S4 |
| Ratio not meaningful (bank D/E) | — | `not_applicable`; left out of the pillar | `null` + `{"status": "not_applicable", "reason": "not applicable to banks, NBFCs or insurers"}` | S13 |
| Too few sector peers | — | No percentile | `pe_sector_percentile` null, "fewer than 5 sector peers with a valid P/E" | S5 |
| Not enough history | — | `insufficient_history` | null with the reason, e.g. "fewer than 56 bars" | S5 |
| Pillar coverage below 50% | — | Overall withheld; partial score offered | `overall: null`, `partial_overall: 42.3`, `complete: false` | S21 |
| LLM output fails grounding | Numbers not in cited evidence | Point dropped; if > half dropped, deterministic report | `grounding.dropped[]` with reasons; `llm.reason` | S14 |
| LLM cites an unknown evidence id | — | Point removed | `llm.uncited_points_removed[]` | S16 |
| Concall quote not in transcript | — | Excluded from the score | `grounded: false`, `grounding_note` | S15 |
| LLM not configured / errors | `LLMError` | Rules take over | `mode: "deterministic"` / `"heuristic"`, `plan_source: "rules"`, reason given | S14–S18 |
| Web search not configured | — | No `S*` / `W*` items | `web.available: false`, `web.reason` | S14, S15 |
| RRG refresh fails | `RRGError` | Nothing written; previous snapshot kept | `last_refresh_failed: {at, error, note}` | S6 |
| Unknown instrument in the URL | — | — | HTTP 404 `{"error": {"code": "instrument_not_found", "message": "NSE:NOSUCH is not in the instrument master.", "request_id": "36151e65e6bb4642"}}` | all |
| Bad screener filter | — | Rejected | HTTP 422 `invalid_filters` with the validation message | S13 |
| Theme save without confirmation | — | Rejected | HTTP 409 `confirmation_required` with the interpreted filters | S17 |

---

## Appendix: which source fills which value on screen

| On screen | API field | Comes from | Path |
|---|---|---|---|
| Price in the instrument header | `quote.fields.last_price.value` | NSE CM `cm_get_stock_quote` → `stock.lastTradedPrice`; Kite `get_quotes` if NSE has none | S10, S11 |
| "Delayed quote" badge | `quote.fields.last_price.status` | NSE marks its MCP data as delayed | S10 |
| "Sources disagree by x%" | `quote.fields.*.conflict.diff_pct` | reconcile-v1 | S11 |
| Chart candles | `history.bars[]` | Bhavcopy file `OpnPric/HghPric/LwPric/ClsPric/TtlTradgVol`; NSE MCP history; Kite candles | S2, S3 |
| "679 from NSE file, 10 from Kite" | `history.coverage.by_provider` | `price_bars.provider` | S3 |
| Corporate actions list | `history.corporate_actions[]` | NSE MCP `get_corporate_actions` → `actions[].purpose` | S3 |
| Financials table | `financials.periods[]` | NSE XBRL concepts ÷ 10^7 | S4 |
| Every metric and its "how calculated" | `metrics[].{value, formula, inputs}` | Stored bars and statements | S5 |
| Score, pillars, stance, risk, confidence | `snapshot.score.*` | score-v1 over metric percentiles | S5 |
| Entry setup | `snapshot.score.entry_setup` | entry-v1 rules over metrics | S5 |
| RRG chart | `sectors.{name}.trail[]` | NIFTY 50 and sector index bars (S3) or composites (S2) | S6 |
| Theme members | `snapshot.constituents[]` | theme-dsl-v1 over metric snapshots | S7 |
| Alert history | `history[].message` | Metric snapshots, RRG and theme snapshots | S8 |
| Research points and links | `report.*[].{text, evidence, kinds}`, `evidence.*` | Metrics, quote, ddgs results, LLM, grounding check | S14 |
| Concall score | `latest.score.value` | NSE transcript PDFs, LLM verdicts, ddgs, concall-v1 | S15 |
| Ask answer | `answer`, `evidence[]`, `movers`/`table` | Live MCP where it answers, otherwise the database; LLM or rules | S16 |
| Market breadth tile | `market_breadth` | NSE `get_market_breadth` (cached 5 min) | S19 |
| Connector cards | `connectors[]` | Each connector's in-memory health | S20 |
