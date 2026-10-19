# Data and LLM flow — a beginner guide

This guide tells you three things:

1. Where the data comes from.
2. When the data moves, and which code moves it.
3. When the application calls the LLM (the language model), and when it does not.

The text follows most of the ASD-STE100 (Simplified Technical English) rules: short sentences,
active voice, one idea in each sentence, and the same word for the same thing. Code names stay
exactly as they are in the code, so you can search for them.

All facts in this guide come from the code. Each section gives the file names. When you change
the code, update this guide.

---

## 1. Words to know

| Word | Meaning in this project |
|---|---|
| **Instrument** | One thing you can look at: a stock (`EQ`) or an index (`INDEX`). It is a row in the `instruments` table. |
| **MCP** | Model Context Protocol. A server that gives "tools" (functions) over HTTP. The application calls these tools to get market data. |
| **NSE MCP** | Two MCP servers from NSE: `nse-bhavcopy` (daily prices, history, corporate actions) and `cm-market` (live market data). This is the **primary** source. |
| **Kite MCP** | The Zerodha broker MCP server. This is the **fallback** source. It fills gaps only. The code calls only 7 read-only tools. |
| **Bhavcopy** | A daily file from NSE. It has the close price of every listed security for one day. |
| **Fundamentals** | Financial statements: revenue, profit, debt, cash flow. |
| **Metric snapshot** | One row per instrument with all computed numbers (returns, RSI, ROCE, P/E, scores). Table: `metric_snapshots`. |
| **Job** | A unit of background work, for example "refresh prices". Each run is a row in `job_runs`. |
| **Scheduler** | The clock that starts jobs at set times (APScheduler). File: `backend/app/workers/scheduler.py`. |
| **Container** | One object that holds every connector (NSE, Kite, LLM, search, cache, database). File: `backend/app/core/container.py`. |
| **Deterministic** | Same input gives the same output, every time. All maths in `backend/app/domain/` is deterministic. |
| **LLM** | The language model (GLM 5.3 Flash through an OpenAI-compatible API). It writes text and reads questions. It **does not** calculate numbers. |
| **Evidence** | The list of facts (with IDs such as `M:roce` or `S1`) that the application gives to the LLM. The LLM can use only these facts. |
| **Grounding check** | Code that makes sure each LLM sentence uses only facts from the evidence. |
| **Fallback** | What the application does when a source or the LLM is not available. |

---

## 2. The big picture

```
        ┌────────────────────────────── EXTERNAL SOURCES ──────────────────────────────┐
        │                                                                              │
        │  NSE MCP (primary)   Kite MCP (fallback)   NSE files       NSE filings       │
        │  prices, history,    prices, history,      bhavcopy,       XBRL results,     │
        │  corporate actions,  quotes               equity list     concall PDFs      │
        │  live movers                                                                 │
        │                                                                              │
        │  Web search (ddgs / Brave)                 LLM (GLM 5.3 Flash)               │
        │  news headlines + URLs                     text only, never numbers          │
        └───────┬──────────────────────────────────────────────────┬───────────────────┘
                │ (A) scheduled jobs pull data                     │ (B) user actions only
                ▼                                                  ▼
   ┌──────────────────────────────┐                ┌────────────────────────────────────┐
   │ INGESTION                    │                │ AI FEATURES (5 call sites)         │
   │ services/ingestion.py        │                │ research.py   ask.py               │
   │ services/market_files.py     │                │ concall.py    theme_ai.py          │
   │ services/market_data.py      │                │ + one start-up check (llm.py)      │
   └──────────────┬───────────────┘                └─────────────────▲──────────────────┘
                  ▼                                                  │ reads evidence
   ┌──────────────────────────────┐                                  │
   │ PostgreSQL (system of record)│──────────────────────────────────┘
   │ instruments, price_bars,     │
   │ financial_periods, ...       │
   └──────────────┬───────────────┘
                  ▼
   ┌──────────────────────────────┐   NO LLM IN THIS BOX
   │ DETERMINISTIC ENGINE         │
   │ services/metrics.py          │──► metric_snapshots (scores, ratios, technicals)
   │ services/sector_rotation.py  │──► sector_rotation_snapshots (RRG)
   │ services/themes.py           │──► theme_snapshots (monthly rankings)
   │ services/alerts.py           │──► alert events
   └──────────────┬───────────────┘
                  ▼
   ┌──────────────────────────────┐
   │ API  /api/v1/...  (FastAPI)  │──► React web app (frontend/src/pages)
   └──────────────────────────────┘
```

Remember these two rules:

- **Rule A.** Data comes in through **jobs**. Jobs run on a schedule, at start-up, or when you
  click a "Refresh" button. Jobs never call the LLM.
- **Rule B.** The LLM runs **only when a user asks for an AI feature**. It reads data that is
  already in the database (plus web search results). It never writes prices or scores.

---

## 3. Data sources

| # | Source | What it gives | Code that calls it | When |
|---|---|---|---|---|
| 1 | NSE MCP `nse-bhavcopy` | Stock history (`get_stock_history`), corporate actions, bulk quotes, market breadth, top movers | `connectors/nse_mcp.py` | Price job, quote requests, Ask "market" questions |
| 2 | NSE MCP `cm-market` | Live market data (when the server advertises the tool) | `connectors/nse_mcp.py` | Quote requests |
| 3 | Kite MCP | `get_historical_data`, `get_quotes`, `get_ohlc`, `get_ltp`, `search_instruments`, `login`, `get_profile` | `connectors/kite_mcp.py` | Only to fill gaps that NSE does not supply. Needs a Kite login. |
| 4 | NSE published files | Equity list, industry list, daily bhavcopy for every stock | `services/market_files.py` | Instrument job and price job, when `UNIVERSE_SCOPE=all` and the URLs are set |
| 5 | NSE filings (`NSE_FILINGS_BASE_URL`) | XBRL financial results, concall transcript PDFs | `connectors/nse_filings.py` | Price job (results) and concall "auto" job (transcripts) |
| 6 | Fundamentals files | Statements from JSON files in `FUNDAMENTALS_DIR` (or uploaded through `PUT /instruments/{ref}/financials`) | `connectors/fundamentals_file.py` | Price job |
| 7 | Web search (ddgs or Brave) | Headlines, URLs, snippets, dates | `connectors/web_search.py` | Research board and concall analyzer only |
| 8 | LLM | Structured JSON text | `connectors/llm.py` | The 5 AI features (Section 7) |

### 3.1 How NSE and Kite work together

File: `services/market_data.py`, class `CorrelatedMarketData`.

1. The code asks NSE MCP and Kite MCP **at the same time**.
2. Each answer becomes a `SourceStatus`: `ok`, `unavailable`, `not_supported`, `login_required`
   or `not_configured`.
3. `domain/reconcile.py` selects the value for each field. NSE has priority.
4. Kite fills a field only when NSE has no value for it (`filled_from_fallback = true`).
5. If NSE and Kite give different values, the code keeps the NSE value and records a **conflict**.
6. A failure never becomes a number. A missing value stays missing.

### 3.2 Demo mode and live mode

The setting `DATA_MODE` in `.env` controls this.

| | `DATA_MODE=demo` (default) | `DATA_MODE=live` |
|---|---|---|
| MCP servers | Local mock servers on ports 9100–9102 (`devtools/mock_mcp.py`) | The real NSE and Kite URLs |
| Instruments | A synthetic universe with fictional symbols | The reference CSV, or all NSE equities |
| Fundamentals | Synthetic statements (`devtools/demo_data.py`) | Real files and NSE filings |
| Web search | Off. Demo symbols are fictional, so real results would be wrong. | On, if configured |
| Labels | Every value has `is_demo = true`. The UI shows a demo label. | Real data |

---

## 4. What happens at start-up

File: `backend/app/main.py`, function `lifespan`.

1. The application reads `.env` into `Settings` (`core/config.py`).
2. `build_container` creates one client for each source: NSE, Kite, LLM, search, filings, cache,
   database.
3. `init_database` makes sure the tables exist.
4. `c.start()` checks the cache (Redis or in-memory) and connects to the MCP servers. The MCP
   client reads the list of tools that each server advertises.
5. `jobs.mark_interrupted` marks old jobs that stopped during a crash.
6. `seed` runs the **instrument job** once. In demo mode it also stores two demo concall
   transcripts.
7. If `SCHEDULER_ENABLED=true`, the scheduler starts.
8. If the `price_bars` table is empty, the **full refresh** job starts in the background.
9. **LLM call (check only):** if the LLM is configured, `llm.check_availability()` sends one tiny
   request ("Reply with the single word OK.", 5 tokens). This confirms the key and the model ID.
10. In live mode, the code asks Kite for the login state, so the login link is ready.

> Start-up makes **at most one** LLM call, and only to test the connection.

---

## 5. Scheduled jobs (automatic data flow)

File: `workers/scheduler.py` (list `JOBS`) and `services/pipeline.py` (job functions).
Times use `DEFAULT_MARKET_TIMEZONE` (Asia/Kolkata by default). You can change each time in `.env`.

| Job | Default time (cron) | Plain words | What it does | LLM? |
|---|---|---|---|---|
| `instruments` | `0 7 * * 1-5` | 07:00, Monday to Friday | Syncs the instrument list. Adds sector labels. Adds built-in themes. | No |
| `prices` | `30 19 * * 1-5` | 19:30, Monday to Friday (after market close) | Loads prices, loads fundamentals, recomputes all metrics, clears the API cache. | No |
| `rrg` | `0 */12 * * *` | Every 12 hours | Computes the sector rotation graph snapshot. | No |
| `themes` | `30 6 1 * *` | 06:30 on day 1 of each month | Ranks every theme. Stores a new monthly snapshot. | No |
| `alerts` | `*/15 * * * *` | Every 15 minutes | Checks alert rules. Records events. | No |

Each job goes through `services/jobs.py`. This code:

- lets only one run of the same job key happen at a time,
- writes a `job_runs` row with the start time, end time, status and details,
- keeps the last good snapshot when a job fails.

### 5.1 The price job, step by step

Function: `pipeline.prices_job`.

```
prices_job
 │
 ├─1─ market_files.ingest_bhavcopy ──► NSE bhavcopy files (whole market, one file per day)
 │       └─ if skipped (demo, or UNIVERSE_SCOPE is not "all"):
 │          ingestion.ingest_prices ──► CorrelatedMarketData.history
 │                                        ├─ NSE MCP get_stock_history   (primary)
 │                                        └─ Kite MCP get_historical_data (fills gaps)
 │       └─ else: ingest_prices also runs for indices and reference stocks
 │
 ├─2─ ingestion.resolve_broker_identifiers  (stores Kite instrument tokens, lineage only)
 │
 ├─3─ ingestion.ingest_fundamentals
 │       ├─ fundamentals files (FUNDAMENTALS_DIR)
 │       └─ ingest_filings ──► NSE filings XBRL results
 │
 ├─4─ metrics.compute_all  ──► one MetricSnapshot per instrument
 │       uses domain/financial_metrics.py, valuation.py, technicals.py, risk.py,
 │       scoring.py, entry_setup.py   (pure maths, no I/O, no LLM)
 │
 └─5─ cache.delete_prefix("api:")  ──► the next page load reads fresh numbers
```

### 5.2 The full refresh job

Function: `pipeline.full_refresh`. It runs at first start and when you click "Refresh all" on the
Status page (`POST /api/v1/jobs/refresh-all`).

Order: instruments → prices (all of 5.1) → RRG → metrics again (to add sector context) → themes
(only if the theme snapshots are older than the metrics) → alerts.

---

## 6. Where the LLM is NOT used

Many people think that "AI" is in every screen. It is not. These parts use **no LLM**:

| Feature | Why no LLM | Code |
|---|---|---|
| All ratios (P/E, ROCE, D/E, FCF ...) | Arithmetic must be exact | `domain/financial_metrics.py`, `domain/valuation.py` |
| Technicals (RSI, MACD, ATR, moving averages) | Arithmetic | `domain/technicals.py` |
| Risk (volatility, drawdown, beta) | Arithmetic | `domain/risk.py` |
| Scores and the 5 equal pillars | Must be reproducible | `domain/scoring.py` |
| Entry Setup state | Rule-based | `domain/entry_setup.py` |
| Screener | SQL-like filters on stored metrics | `services/screener.py` |
| Theme rankings and monthly snapshots | DSL evaluation | `domain/theme_dsl.py`, `services/themes.py` |
| **Custom theme "interpret"** (`POST /themes/interpret`) | Rules interpreter (`theme_dsl.interpret`) | `api/v1/user.py` |
| Sector rotation (RRG) | Arithmetic | `domain/rrg.py` |
| Alerts | Threshold rules | `services/alerts.py` |
| Concall **score** number | Formula in code | `domain/concall_score.py` |
| Overview, Instrument detail, Watchlists, Status pages | They read stored data | `api/v1/market.py`, `api/v1/user.py` |

---

## 7. Where the LLM IS used — the complete list

There are **5 places** in the code. Each place calls `c.llm.complete_validated(...)`. Search for
that text and you find all of them.

| # | Feature | User action | API endpoint | LLM calls (normal) | File |
|---|---|---|---|---|---|
| 0 | Connection check | Application starts | — | 1 tiny call | `connectors/llm.py` `check_availability` |
| 1 | Research board | Click "Generate research" | `POST /api/v1/research/stock` | 1 (sometimes 2) | `services/research.py` `generate` |
| 2 | Ask the market | Type a question in the Ask panel | `POST /api/v1/ask` | 2 (plan + answer) | `services/ask.py` `ask` |
| 3 | Concall analyzer | Click "Analyze" or "Fetch from NSE" | `POST /instruments/{ref}/concalls/analyze` or `/concalls/auto` | 2 (extract + assess) | `services/concall.py` `analyze` |
| 4 | Theme AI filter | Type a refine request on a theme | `POST /api/v1/themes/{id}/ai-filter` | 1 | `services/theme_ai.py` `refine` |

**Reading a stored report makes no LLM call.** `GET /instruments/{ref}/research` only reads the
last saved report from the `research_reports` table.

### 7.1 What one `complete_validated` call does

File: `connectors/llm.py`.

1. It adds the JSON schema of the expected answer to the system prompt.
2. It sends `POST {LLM_BASE_URL}/chat/completions` with `response_format = json_schema`.
3. It checks the daily budget first (`LLM_DAILY_BUDGET` calls, `LLM_DAILY_TOKEN_BUDGET` tokens).
   If the budget is used up, it stops and the feature uses its fallback.
4. On a network error, HTTP 429 or HTTP 5xx, it tries again (`LLM_MAX_RETRIES`, default 1).
5. If the answer is cut off (`finish_reason = length`), it tries once more with double the token
   limit.
6. It validates the answer with Pydantic.
7. If the answer does not match the schema, it sends **one repair request**.
8. If the repair also fails, it raises `LLMInvalidOutput`. The feature then uses its fallback.
9. It records tokens, latency, errors and cost. The Status page shows these values.

So one `complete_validated` is **1 HTTP call in the normal case** and **at most about 3–6** in
the worst case (truncation retry, repair, network retries).

### 7.2 When is the LLM "available"?

`c.llm.available` is true only when all of these are true:

- `LLM_ENABLED=true`
- `LLM_BASE_URL` has a value
- `LLM_API_KEY` has a value
- `LLM_MODEL_ID` has a value

If one is missing, every AI feature uses its **fallback** (Section 7.3 to 7.6). The feature still
works. The response shows `mode = "deterministic"`, `"heuristic"` or `"rules"`, and a reason.

### 7.3 Research board (`services/research.py`)

```
User clicks "Generate research" for a stock
 │
 ├─1─ Read the latest MetricSnapshot from the database           (no LLM)
 ├─2─ Get a live quote: NSE MCP + Kite MCP (Section 3.1)         (no LLM)
 ├─3─ Web search: "<name> <symbol> NSE results announcement"     (no LLM)
 │       up to WEB_SEARCH_MAX_RESULTS sources (default 8)
 ├─4─ build_evidence: give each fact an ID
 │       M:<metric>  = calculated metric     Q:<field> = quote     S1..S8 = web source
 ├─5─ LLM CALL #1: SYSTEM_PROMPT + evidence  ──► LLMReport (pros, cons, risks, ...)
 ├─6─ validate_grounding: remove each point that
 │       - cites an ID that is not in the evidence,
 │       - contains a URL,
 │       - states a number not found in the cited evidence.
 │     If more than half of the points are removed ──► use the deterministic report.
 ├─7─ If only the summary was removed:
 │       LLM CALL #2 (repair_summary) ──► one new summary point, checked again
 ├─8─ deterministic_report fills each empty section
 └─9─ Save to research_reports + research_sources. Return to the UI.
```

Fallback: if the LLM is not available or fails, `deterministic_report` writes the summary from
rules. The UI shows `mode = deterministic` and the reason.

### 7.4 Ask the market (`services/ask.py`)

```
User types a question
 │
 ├─1─ LLM CALL #1 (make_plan): question ──► AskPlan
 │       intent: instrument | compare | screen | sector | market | movers | unknown
 │       The code sends only the symbols the question names, the sector list and the metric catalog.
 │       Fallback: rule_plan (keyword rules)
 ├─2─ keep_named_symbols + sanitize_plan: remove symbols, filters and sectors that are
 │       not real or not in the question. Nothing the model writes runs without a check.
 ├─3─ ensure_fresh_prices: if stored prices are older than the last session,
 │       start the price job in the background (live mode only, with a cooldown)
 ├─4─ gather evidence (no LLM):
 │       instrument/compare ─► live quote (MCP), else stored quote + stored metrics
 │       screen             ─► the deterministic screener
 │       market             ─► benchmark quote + NSE MCP live movers (else stored 1W returns)
 │       sector             ─► the latest RRG snapshot
 │       movers             ─► price changes over the lookback window
 ├─5─ LLM CALL #2 (answer): question + evidence ──► AskAnswer
 │       validate_citations removes each point with an unknown evidence ID.
 │       Fallback: deterministic_answer
 └─6─ Return the plan, the answer, the evidence, and which parts used the LLM.
```

If the intent is `unknown`, the code does not make LLM call #2.

### 7.5 Concall analyzer (`services/concall.py`)

Goal: "Did management do this quarter what it said last quarter?"

```
User clicks "Fetch from NSE" (auto) or "Analyze"
 │
 ├─0─ (auto only) fetch_from_nse: download transcript PDFs from NSE filings,
 │       extract text, store in concall_transcripts                         (no LLM)
 ├─1─ Select the current quarter and the previous quarter. Both are required.
 ├─2─ LLM CALL #1 (extract): previous transcript ──► CommitmentList
 │       Keep a commitment only if its quote appears word for word in the transcript.
 ├─3─ Web search for the 6 most important commitments, 3 results each      (no LLM)
 ├─4─ LLM CALL #2 (assess): commitments + current transcript + web sources ──► AssessedList
 │       verdict: delivered | partially_delivered | in_progress | deferred |
 │                not_delivered | not_addressed
 ├─5─ assemble: remove each verdict whose evidence quote is not in the current transcript
 │       or whose web source was not retrieved
 ├─6─ domain/concall_score.py computes the score (no LLM):
 │       score = sum(weight × credit) / sum(weight) × 100
 │       weight: high 3, medium 2, low 1
 │       credit: delivered 1.0, partial 0.5, in_progress 0.5, deferred 0.25, others 0
 │       Fewer than 3 grounded items ──► no score ("Insufficient data")
 └─7─ Save to concall_analyses. Return to the UI.
```

Fallback: `heuristic_commitments` and `heuristic_assess` use forward-looking phrases and shared
key words. The result has `mode = heuristic` and **Low** confidence.

> The LLM only **classifies** (it lists promises and gives verdicts). The **number** comes from
> code.

### 7.6 Theme AI filter (`services/theme_ai.py`)

```
User types "only large banks with low debt" on a theme page
 │
 ├─1─ LLM CALL #1: request + theme filters + sector list + metric catalog ──► RefinePlan
 │       Fallback: rules_plan
 ├─2─ Validate each filter against METRIC_CATALOG. Ignore unknown metrics and sectors.
 ├─3─ theme_dsl.evaluate on the latest metric snapshots (no LLM)
 └─4─ Return the members. Nothing is saved. Monthly snapshots do not change.
```

---

## 8. Examples

Each example tells you what you do, what the code does, and how many LLM calls occur.

### Example 1 — You open the Overview page

1. The browser calls `GET /api/v1/overview`.
2. The API reads stored metric snapshots, theme snapshots and the RRG snapshot.
3. The API may use the cache (`api:` keys).

**LLM calls: 0.** **External calls: 0**, unless you click the page refresh button. That sends
`fresh=true`, and the API checks the live sources again.

### Example 2 — You filter the Screener for "ROCE > 15 % and Debt/Equity < 0.5"

1. The browser calls `GET /api/v1/screener?filters=[{"metric":"roce","op":"gt","value":15},{"metric":"debt_equity","op":"lt","value":0.5}]`.
2. `services/screener.py` filters the latest `metric_snapshots` rows.
3. A stock without a ROCE value is shown as missing. It is not shown as zero.

**LLM calls: 0.** The numbers come from the last price job.

### Example 3 — It is 19:30 on a Tuesday

1. The scheduler starts the `prices` job.
2. In live mode with `UNIVERSE_SCOPE=all`, the code downloads the NSE bhavcopy files.
3. For indices and reference stocks, the code asks NSE MCP `get_stock_history`.
4. NSE does not return a bar for one index on one day. Kite `get_historical_data` fills it, if
   you are logged in to Kite. The bar has the provider `kite_mcp`.
5. The code loads fundamentals and NSE XBRL results.
6. `metrics.compute_all` recomputes every snapshot.
7. The job writes a `job_runs` row with status `succeeded`.

**LLM calls: 0.**

### Example 4 — You click "Generate research" for TCS

1. `POST /api/v1/research/stock` with `{"instrument": "NSE:TCS"}`.
2. The code reads the TCS metric snapshot and gets a live quote.
3. The code searches the web: "Tata Consultancy Services TCS NSE results announcement". It gets 8
   sources: `S1` … `S8`.
4. Evidence example:
   ```json
   {"M:roce": {"kind": "calculation", "value": 51.2, "unit": "%", "formula": "EBIT / capital employed"},
    "Q:last_price": {"kind": "quote", "value": 3920.5, "provider": "nse_mcp"},
    "S1": {"kind": "source", "title": "TCS Q2 results ...", "url": "https://...", "published_at": "2026-10-09"}}
   ```
   (The numbers above are only an illustration of the format, not real data.)
5. **LLM call #1** returns pros, cons, risks and a summary with IDs such as `[M:roce]`.
6. The model writes "ROCE is 60 %" and cites `M:roce` (51.2). The grounding check removes that
   point, because 60 is not in the cited evidence.
7. If the summary was removed, **LLM call #2** asks for a new summary.
8. The report is saved with `mode = llm`.

**LLM calls: 1 or 2. Web searches: 1.**

### Example 5 — The same as Example 4, but `LLM_ENABLED=false`

1. Steps 1–4 are the same.
2. The code does not call the LLM.
3. `deterministic_report` writes pros and cons from the score pillars and metric rules.
4. The UI shows `mode = deterministic` and the reason "LLM_ENABLED is false".

**LLM calls: 0.** The page still works.

### Example 6 — You ask "Compare TCS and INFY"

1. **LLM call #1** returns `{"intent": "compare", "symbols": ["TCS", "INFY"]}`.
2. `sanitize_plan` confirms that both symbols exist.
3. `gather` gets a live quote for each (`Q1`, `Q2`) and the stored metrics (`M1`, `M2`).
4. **LLM call #2** writes the answer. It cites `Q1`, `M1`, `Q2`, `M2`.
5. `validate_citations` removes any point with an unknown ID.

**LLM calls: 2.**

### Example 7 — You ask "Which banks have ROE above 15 %?"

1. **LLM call #1** returns `intent = screen`, filter `roe > 15`, sector `Financial Services`.
2. The deterministic screener runs. It returns a table (evidence `S1`).
3. **LLM call #2** writes a short answer about the table.

**LLM calls: 2.** The list of banks comes from the screener, not from the model.

### Example 8 — You ask "What is the weather?"

1. **LLM call #1** returns `intent = unknown`.
2. The code does not gather evidence and does not make LLM call #2.
3. `deterministic_answer` says that the question is not supported.

**LLM calls: 1.**

### Example 9 — You run the concall analyzer for a company

1. You click "Fetch from NSE". `POST /instruments/NSE:XYZ/concalls/auto` starts a background job.
2. The job downloads 4 transcript PDFs from NSE filings and stores the text.
3. Previous quarter = Q1 FY2027. Current quarter = Q2 FY2027.
4. **LLM call #1** finds 9 commitments in the Q1 text. One quote is not in the transcript, so the
   code removes it. 8 remain.
5. The code makes 6 web searches (3 results each).
6. **LLM call #2** gives a verdict for each of the 8 commitments.
7. One verdict cites `W7`, but `W7` was not retrieved. The code excludes it. 7 remain.
8. Example score: 3 high delivered (3×1.0 each), 2 medium in_progress (2×0.5 each), 2 low
   not_addressed (1×0 each).
   Score = (9 + 2 + 0) / (9 + 4 + 2) × 100 = 11 / 15 × 100 = **73.3** → "Mixed follow-through"
   (75 or more is "Strong").

**LLM calls: 2. Web searches: 6.**

### Example 10 — You create a custom theme "low debt IT companies with momentum"

1. `POST /api/v1/themes/interpret` runs `theme_dsl.interpret`. This is a **rules** interpreter.
2. You see the filters. You confirm. `POST /api/v1/themes` with `confirmed=true` saves the theme.

**LLM calls: 0.** (The LLM is used only by the separate "AI filter" on a theme, Example 11.)

### Example 11 — You type "only companies above ₹50,000 crore" in a theme AI filter

1. **LLM call #1** returns a filter such as `market_cap gte 50000`.
2. The code checks that `market_cap` is in `METRIC_CATALOG`.
3. `theme_dsl.evaluate` returns the filtered members.
4. Nothing is saved.

**LLM calls: 1.**

### Example 12 — NSE MCP is down

1. The price job asks NSE MCP. The request fails. The circuit breaker opens.
2. The `SourceStatus` for NSE is `unavailable` with a reason.
3. If Kite is logged in, Kite history fills the bars. Each bar has the provider `kite_mcp`.
4. If Kite is also not available, the code stores no new bars. The old bars stay. The UI shows
   the data as **stale**.
5. After a delay, the code tries the failed symbols once more, one at a time.

**LLM calls: 0.** The code never invents a price.

---

## 9. Summary table — LLM calls for each action

| Your action | LLM calls (normal) | LLM calls (maximum*) | Web searches | Fallback when no LLM |
|---|---|---|---|---|
| Open any page | 0 | 0 | 0 | — |
| Screener, export CSV | 0 | 0 | 0 | — |
| Scheduled / manual refresh jobs | 0 | 0 | 0 | — |
| Create custom theme (interpret) | 0 | 0 | 0 | — |
| Application start | 1 (5 tokens) | 1 | 0 | Status shows the reason |
| Generate research | 1 | 2 `complete_validated` | 1 | `deterministic_report` |
| Ask a question | 2 | 2 `complete_validated` | 0 | `rule_plan` + `deterministic_answer` |
| Concall analyze | 2 | 2 `complete_validated` | up to 6 | heuristic mode (Low confidence) |
| Theme AI filter | 1 | 1 `complete_validated` | 0 | `rules_plan` |

\* Each `complete_validated` can make extra HTTP requests for a retry, a truncation retry or one
schema repair (Section 7.1). All count against `LLM_DAILY_BUDGET`.

---

## 10. How to see the flow yourself

| What you want to know | Where to look |
|---|---|
| Is each source up? | Status page, or `GET /api/v1/sources/status` |
| Is the LLM configured? Tokens used? Last error? | Status page, "LLM" row (from `c.llm.status()`) |
| Which jobs ran, and their result? | Status page job list, or `GET /api/v1/jobs` |
| Which provider gave a value? | Instrument page "Sources and data quality", or field `provider` in the quote |
| Did a research report use the LLM? | Field `mode` (`llm` or `deterministic`) and `llm.reason` in the report |
| Did an Ask answer use the LLM? | Fields `plan_source`, `answer_source`, `llm.used` |
| Did a concall analysis use the LLM? | Field `mode` (`llm` or `heuristic`) and `limitations` |
| Who did what? | `GET /api/v1/audit/events` |
| Find every LLM call in code | `grep -rn "complete_validated" backend/app/services` |

---

## 11. File map

| Area | Files |
|---|---|
| Settings | `backend/app/core/config.py`, `.env.example` |
| Wiring | `backend/app/core/container.py` |
| Start-up | `backend/app/main.py` |
| Schedule | `backend/app/workers/scheduler.py`, `backend/app/workers/run.py` |
| Job order | `backend/app/services/pipeline.py`, `backend/app/services/jobs.py` |
| Data in | `services/ingestion.py`, `services/market_files.py`, `services/market_data.py` |
| Connectors | `connectors/nse_mcp.py`, `connectors/kite_mcp.py`, `connectors/mcp_client.py`, `connectors/nse_filings.py`, `connectors/fundamentals_file.py`, `connectors/web_search.py`, `connectors/llm.py` |
| Pure maths | `backend/app/domain/*.py` |
| AI features | `services/research.py`, `services/ask.py`, `services/concall.py`, `services/theme_ai.py` |
| API routes | `backend/app/api/v1/market.py`, `backend/app/api/v1/user.py` |
| Web pages | `frontend/src/pages/*.tsx`, `frontend/src/components/AskPanel.tsx` |

For deeper detail, read [`ARCHITECTURE.md`](ARCHITECTURE.md) and `docs/methodology.md`.
