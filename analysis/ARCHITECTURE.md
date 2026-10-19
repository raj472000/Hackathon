# Architecture and technical specification

How Markets Intelligence is built: components, data flow, interfaces, algorithms, storage, API
conventions, operations and the technology stack. For what the system does see
[`REPOSITORY_GUIDE.md`](REPOSITORY_GUIDE.md); for running it see [`HOW_TO_RUN.md`](HOW_TO_RUN.md).
Formulas are specified in full in `docs/methodology.md`.

---

## 1. System overview

A **modular monolith** (one Python codebase) with two process roles, a React single-page app, and
PostgreSQL. Redis is optional.

```
                         Browser
                            │  same origin: /api/v1, /health
                            ▼
              ┌──────────────────────────────┐
              │ Web app (React + Vite)       │  dev: Vite :5173 (proxy)   prod: nginx :8080
              └──────────────┬───────────────┘
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ API process (FastAPI, uvicorn :8000)                                        │
│  middleware: request id · JSON logs · metrics · size limit · security hdrs │
│  api/v1 ── auth (bearer, optional) · validation · rate limits · errors      │
│  services ── ingestion · market_data (correlation) · metrics · screener ·   │
│              themes · sector_rotation · research · concall · alerts · jobs  │
│  domain ──── pure maths: ratios, valuation, technicals, risk, adjust,       │
│              reconcile, scoring, theme DSL, RRG, entry setup, concall score │
│  connectors ─ McpClient ─┬─ NseMcpProvider  (bhavcopy + CM market)  primary  │
│                          └─ KiteMcpProvider (7 read-only tools)     fallback │
│               FileFundamentalsProvider · DdgsSearch/Brave · HuggingFaceLLM  │
│  scheduler (APScheduler) unless SCHEDULER_ENABLED=false                     │
└───────┬───────────────────────────┬────────────────────────────┬───────────┘
        ▼                           ▼                            ▼
 PostgreSQL 16 (psycopg 3)    Redis 7 (optional)         External services
 system of record             cache + job locks          NSE MCP, Kite MCP,
                                                         LLM provider (.env), ddgs

 Worker process (python -m app.workers.run): same code, runs only the schedule.
 Demo mode: mock MCP servers (:9100–9102) replace the external MCP servers.
```

**Process roles**

| Role | Entry point | Responsibility |
|---|---|---|
| API | `uvicorn app.main:app` | HTTP API; optionally the schedule |
| Worker | `python -m app.workers.run` | The schedule only (use with Redis) |
| Migrate | `python -m app.devtools.preflight migrate` / `alembic upgrade head` | Schema changes |
| Mock MCP (demo) | `python -m app.devtools.mock_mcp` | Three MCP servers with synthetic data |

---

## 2. Layering rules

| Layer | Directory | May depend on | Never does |
|---|---|---|---|
| API | `app/api` | services, db, domain | Business maths, provider calls |
| Services | `app/services` | connectors, domain, db, core | HTTP concerns |
| Domain | `app/domain` | Python, numpy, pandas only | I/O of any kind |
| Connectors | `app/connectors` | core, httpx | Database access, scoring |
| Core | `app/core` | — | Business logic |

Interfaces (Protocols) in `app/connectors/base.py`: `MarketDataProvider`, `BrokerDataProvider`,
`FundamentalsProvider`, `WebSearchProvider`, `LLMProvider`. Services take providers through the
`Container` (`app/core/container.py`), which wires settings into one instance of each per process.

---

## 3. MCP connector design

### 3.1 McpClient (`app/connectors/mcp_client.py`)

A minimal MCP client over **Streamable HTTP** implemented on `httpx` (not the MCP SDK client), so a
long-lived session can be held across requests; the Kite login is bound to the MCP session.

| Concern | Implementation |
|---|---|
| Protocol | JSON-RPC 2.0: `initialize` (protocol 2025-03-26), `notifications/initialized`, `tools/list` (paginated), `tools/call` |
| Framing | JSON responses and SSE (`text/event-stream`) responses both parsed |
| Session | `Mcp-Session-Id` kept; HTTP 404 or a session error triggers one re-handshake, not counted as a retry |
| Discovery | Tools and input schemas read at start-up and on "Reconnect"; stored as `advertised` |
| Allowlist | Optional per client; calls outside it raise `McpForbiddenTool` before any request |
| Validation | Required arguments present; unknown arguments rejected when the schema has properties |
| Timeouts | Connect 10 s, per call 30 s (configurable) |
| Concurrency | `asyncio.Semaphore` (default 4) |
| Pacing | Minimum interval between calls (NSE 0.2 s, Kite 0.35 s) |
| Retries | Exponential backoff with full jitter, only for `McpUnavailable` (transport errors, HTTP 5xx/429) |
| Circuit breaker | Opens after 5 consecutive failures, half-open after 60 s |
| Errors | `McpUnavailable`, `McpToolError` (`isError` or JSON-RPC error), `McpNotSupported`, `McpForbiddenTool`, `McpInvalidArguments`; errors are raised, never returned or cached |
| Health | State, breaker, tool counts, blocked tools, latency (EWMA), calls, errors, retries, error rate, last success/error |

### 3.2 Argument binding (`app/connectors/binding.py`)

Services express intents (`symbol`, `start_date`, `end_date`, `months`, `index_name`, `query`,
`instrument_token`, …). `bind_args` maps them to the server's own property names through synonym
tables, formats dates as the property description asks (`YYYY-MM-DD`, `DD-MM-YYYY`,
`YYYY-MM-DD HH:MM:SS`, …), and fills arguments a server marks required but documents a default for
("Pass 0 for default", "pass 'today'"). An unbindable required argument makes that capability
`not_supported` for the call.

### 3.3 NseMcpProvider (`app/connectors/nse_mcp.py`)

Capabilities map to the first advertised tool of a candidate list:

| Capability | Server | Tool (verified 2026-10-12) |
|---|---|---|
| history | bhavcopy | `get_stock_history(symbol, months≤3, endDate)`, paged with `next_end_date` |
| index_history | bhavcopy | `get_index_history(indexName, months≤12, endDate)` |
| corporate_actions | bhavcopy | `get_corporate_actions(symbol, fromDate, toDate)` |
| market_breadth, top_movers, bulk_quote, search | bhavcopy | `get_market_breadth`, `get_top_movers`, `get_bulk_quote`, `search_symbols` |
| quote, index_quote | CM market | `cm_get_stock_quote(symbol)` (nested under `stock`), `cm_get_index_quote(indexName)` |
| live_movers, data_status | CM market | `nse_get_market_movers`, `cm_get_data_status` |
| intraday_history, fundamentals | — | not advertised → `not_supported` |

Error payloads returned as normal results (`{"error": "Symbol not found…"}`) are raised as errors.
Quotes are marked `delayed`; bhavcopy bars `unadjusted`.

### 3.4 KiteMcpProvider (`app/connectors/kite_mcp.py`)

Allowlist: `login`, `get_profile`, `search_instruments`, `get_quotes`, `get_ohlc`, `get_ltp`,
`get_historical_data`. The 15 other advertised tools (orders, GTT, holdings, positions, margins,
trades, mutual funds) are refused in code. Instrument lookup is exact by `id` (`NSE:INFY`) and
returns the instrument token and ISIN. Login: `get_profile` failing → `login` returns a Kite
authorisation URL; until approved, generic failures are reported as `login_required`. History bars
are `split_adjusted`.

### 3.5 Other providers

| Provider | File | Notes |
|---|---|---|
| FileFundamentalsProvider | `fundamentals_file.py` | Pydantic-validated JSON per symbol; units normalised to INR crore; demo files rejected in live mode |
| DdgsSearch / BraveSearch / NullSearch | `web_search.py` | `ddgs` news first, web results as fallback, newest first, day-level dates in IST; URL safety (http(s), public hosts only); primary/secondary classification by domain; no page fetching (no SSRF surface) |
| HuggingFaceLLM | `llm.py` | OpenAI-compatible chat completions on the HF router or a dedicated endpoint; `response_format` JSON schema; Pydantic validation; one repair attempt; daily call and token budgets; usage tracking; model-id suggestions on errors; token never logged |

---

## 4. Data flow

### 4.1 Ingestion (per instrument)

```
CorrelatedMarketData.history(symbol, start, end, type)
  ├─ NSE history (unadjusted)  ┐
  ├─ NSE corporate actions     ├─ in parallel; each outcome → SourceStatus
  └─ Kite history (adjusted)   ┘
  NSE bars → adjust_bars(splits, bonuses) → detect_unexplained_gaps
  reconcile_bars({nse_mcp, kite_mcp}) → merged bars + conflicts + coverage
ingestion._write_history → price_bars (upsert by instrument+date), corporate_actions,
                           source_conflicts, data_source_records (one per provider call)
```

Fetches run with bounded concurrency (default 4); writes are sequential in one session. Requests
for the same instrument and range are coalesced. A newly seen split or bonus triggers a full
re-fetch because every earlier adjusted price changes.

### 4.2 Correlation policy (`app/domain/reconcile.py`, `reconcile-v1`)

- Precedence per field: `nse_mcp`, then `kite_mcp`.
- Per date the whole bar comes from the first provider with a close, so a bar never mixes sources.
- Where two providers have a value: relative difference > tolerance → conflict recorded, the
  precedence winner kept. Tolerances: open/high/low/close 0.1%, live price 1%, volume 5%, others 0.5%.
- Quotes reconcile field by field the same way; a field only the broker has (e.g. circuit limits)
  is filled from it and marked `filled_from_fallback`.

### 4.3 Metrics and scores

`metrics.compute_all` loads all bars and statements, builds benchmark and sector series, computes
the RRG, then per instrument: technicals, risk, fundamentals, valuation, sector context, P/E sector
percentile; then `score_universe` (peer groups = sectors, inapplicable metrics excluded), labels and
the entry setup. One `metric_snapshots` row per instrument per as-of date stores the flat values,
per-metric detail (status, reason, formula, inputs, unit) and the score document, with formula
versions.

### 4.4 Request path (example: instrument page)

`GET /api/v1/instruments/NSE:INFY` → resolve instrument (id or EXCHANGE:SYMBOL) → latest metric
snapshot → live reconciled quote (cached 60 s; failures never cached) → theme memberships from
latest snapshots → sector peers → response with `data_mode`, `is_demo`, `retrieved_at`, `as_of`.

---

## 5. Algorithms (versioned)

| Component | Version | Specification |
|---|---|---|
| Price adjustment | `adjust-v1` | Back-adjust prices before ex-date by the share multiplier (split, bonus); volumes inversely; dividends not adjusted |
| Fundamentals | `fundamentals-v1` | Ratios with explicit statuses: `missing`, `not_meaningful` (non-positive denominator), `not_applicable` (financial sector), `insufficient_history` |
| Valuation | `valuation-v1` | Price/share in INR × shares in crore = INR crore; EV = market cap + debt − cash + minority interest |
| Technicals | `technicals-v1` | Windows in trading bars (1W 5 … 5Y 1260); Wilder RSI/ATR/ADX; EMA-based MACD; no look-ahead (`as_of` truncation) |
| Risk | `risk-v1` | Annualisation by √252; label = worst bucket of volatility (20/35/55%), drawdown (−20/−35/−55%), beta (0.9/1.3/1.7) |
| Scoring | `score-v1` | Mid-rank percentiles (min 5 peers), oriented; pillar = mean of available metric percentiles, ≥50% coverage; overall = mean of 5 pillars at 20% each; partial score when ≥3 pillars |
| Theme DSL | `theme-dsl-v1` | Universe + filters (`gt, gte, lt, lte, eq, between` on ~100 allowlisted metrics) + equal-weight percentile ranking + limit ≤ 500; missing metric → exclusion with reason |
| RRG | `rrg-v1` | Weekly closes; RS = sector/benchmark; RS-Ratio = 100·RS/SMA₁₂(RS); RS-Momentum = 100·Ratio/SMA₅(Ratio); quadrants at 100 |
| Entry setup | `entry-v1` | Eight rule conditions; states by explicit precedence; no entry zone without a backtested method |
| Concall score | `concall-v1` | Σ(importance × credit)/Σ(importance)·100; credits delivered 1, partial 0.5, in progress 0.5, deferred 0.25, not delivered/addressed 0; weights 3/2/1; ungrounded quotes excluded; ≥3 items |
| Research grounding | `research-v1` | A model statement is kept only if it cites retrieved evidence, has no unretrieved URL and no number absent from the cited evidence; >50% dropped → rule-based board |

---

## 6. Storage

PostgreSQL 16 through **psycopg 3** (`postgresql+psycopg://…`, SQLAlchemy 2 async engine, pool
size 10 + 10 overflow, pre-ping). Schema owned by Alembic (`backend/migrations`), one revision so
far (`0e59e2fb0d7a`). Prices and money use `NUMERIC`; timestamps are `timestamptz` (a type
decorator also keeps SQLite test runs timezone-aware).

| Table | Key columns and constraints |
|---|---|
| `instruments` | exchange, symbol (unique together), isin, instrument_type, currency, sector, industry, sector_kind, sector_index_symbol, lot_size, trading_status, governance_flag, contract_meta (commodity/derivative metadata), is_demo |
| `instrument_identifiers` | instrument, provider, kind (unique together), value — e.g. Kite instrument token |
| `price_bars` | instrument + date unique; open/high/low/close NUMERIC(18,4), volume, traded_value, provider, adjustment, retrieved_at |
| `corporate_actions` | instrument, ex_date, kind, ratio, description, provider |
| `market_quotes` | latest reconciled quote fields per instrument |
| `source_conflicts` | instrument + field + date unique; values per provider, diff %, tolerance %, chosen |
| `data_source_records` | provider, source_id (tool), dataset, status, reason, rows, as_of, adjustment, retrieved_at |
| `financial_statement_periods` | instrument + period_end + period_type + source unique; facts JSON (INR crore), raw JSON, units, published_at, ingested_at |
| `metric_snapshots` | instrument + as_of unique; flat JSON, detail JSON, score JSON, overall_score, versions, is_latest |
| `themes`, `theme_versions` | slug unique, spec JSON, version, availability, requirements |
| `theme_snapshots`, `theme_snapshot_constituents` | theme + period + revision unique; constituents with rank, score, values, price at snapshot; exclusions |
| `sector_rotation_snapshots` | benchmark + slot (12-hour bucket) + params unique; payload JSON |
| `research_reports`, `research_sources` | mode (llm / deterministic), report JSON, cited sources with URL and times |
| `concall_transcripts`, `concall_analyses` | instrument + fiscal year + quarter unique; text, sha256; result JSON, score, label, confidence |
| `watchlists`, `watchlist_items`, `alerts`, `alert_events` | owner-scoped; alerts with idempotency key, events with dedupe key |
| `job_runs`, `audit_events`, `users` | job status, timings, detail, error; actor, action, entity, request id |

Start-up guarantees (`app/main.py`, `app/devtools/preflight.py`):

- The API refuses a database with no schema, or a schema at a different revision than the code,
  and names the command to run.
- `preflight migrate` adopts tables created without a migration record (stamps, then upgrades).
- `AUTO_CREATE_SCHEMA=true` (tests only) also records the revision so migrations keep working.

---

## 7. Jobs, scheduling, caching

- **Scheduler:** APScheduler `AsyncIOScheduler` with cron triggers in `Asia/Kolkata`, `coalesce`,
  `max_instances=1`, one-hour misfire grace. Runs in the API or in the worker, never both.
- **Jobs:** `instruments`, `prices` (prices → broker identifiers → fundamentals → metrics),
  `rrg`, `themes`, `alerts`, and `full_refresh` (all in dependency order). Every run creates a
  `job_runs` row; a lock `job:<key>` (in-process or Redis `SET NX EX`) makes a concurrent second run
  `skipped`; failures are recorded and leave earlier snapshots untouched; runs left `running` by a
  stopped process are marked interrupted at start-up unless their lock is still held.
- **Idempotency:** theme snapshots are immutable (new revision per refresh); rotation snapshots are
  upserted per 12-hour slot; price bars upserted by date; alert events deduplicated by key.
- **Cache:** `MemoryCache` or `RedisCache` behind one interface (`get/set/delete_prefix/lock/is_locked`).
  Quotes cached 60 s; only successful values are cached. Local runs fall back to the in-process
  cache if Redis is unreachable (reported in readiness and on Data status); elsewhere that is a
  readiness failure.

---

## 8. API conventions

- Versioned REST under `/api/v1`, 45 operations; OpenAPI at `/docs`. Health: `/health/live`,
  `/health/ready` (database and cache; upstream sources reported but not fatal); `/metrics`.
- **Envelope:** data responses include `data_mode`, `is_demo`, `retrieved_at` and, where relevant,
  `as_of`, `calculation_version`, coverage, freshness, `data_state` + `data_state_reason`.
- **Errors:** `{"error": {"code", "message", "details", "request_id"}}` for every failure,
  including validation (422 with field list) and unhandled errors (500, no internals).
- **Auth:** optional single bearer token (`API_AUTH_TOKEN`), constant-time comparison; blank = off.
- **Rate limits:** sliding windows per actor and client IP: research 6/min, refresh triggers
  3/min, default 300/min; 429 with `Retry-After`.
- **Validation:** every query parameter typed and bounded; screener filters parsed against the
  metric allowlist; sort fields allowlisted; CSV export neutralises formula injection; request
  bodies capped (5 MB; 8 MB for transcript uploads).
- Main resources: sources, instruments (+ metrics, financials, history, research, concalls),
  screener (+ export, metric catalog), themes (+ interpret, refresh, snapshots), sectors,
  sector-rotation (+ snapshots, refresh), overview, research, concalls, watchlists, alerts, jobs,
  audit.

---

## 9. Frontend

React 19 + TypeScript 6 + Vite 8, Tailwind CSS 4, TanStack Query 5 (server state, retries,
placeholder data), React Router 7, Lightweight Charts 5 (price chart), a custom SVG rotation graph
(zoom, hover, up to three highlighted sectors with colour-blind-validated colours, direct labels),
zod response contracts for the screener, rotation and sources payloads.

| Page | Route |
|---|---|
| Overview | `/` |
| Screener | `/screener` |
| Themes, theme detail | `/themes`, `/themes/:id` |
| Instrument detail | `/instrument/:ref` |
| Research board | `/research/:ref?` |
| Sector rotation | `/rotation` |
| Concall follow-through | `/concalls/:ref?` |
| Watchlists and alerts | `/watchlists` |
| Data status | `/status` |

Conventions: a persistent provenance strip (as-of date, source health); missing values as an em
dash with the reason on hover and for screen readers; gain/loss always with ▲/▼; skeletons, empty
states and errors with request ids; keyboard navigation and skip link; light and dark themes from
design tokens; responsive down to phone width. The browser talks only to `/api` on its own origin;
no secrets in the bundle.

---

## 10. Security

- Secrets only in `.env` or a secret store; `.env` is git-ignored; settings print secrets masked;
  logs redact authorization headers, tokens and secret-named fields.
- No trading surface: no order routes, broker trading tools blocked in code.
- SQL only through SQLAlchemy with bound parameters; theme and screener filters are data validated
  against an allowlist, never SQL; the model never executes code or queries.
- Retrieved web content and transcripts are passed to the model as delimited untrusted data; quotes
  must match source text verbatim; URLs validated (public http(s) only); no server-side page fetching.
- CORS restricted to configured origins; security headers on every response; nginx CSP in the
  container image.
- Demo and live data separated by an `is_demo` flag and removal on mode switch.

---

## 11. Observability

- JSON logs (structlog) with request id or job key; secrets redacted.
- Prometheus metrics: requests and latency per route, rate-limit rejections, job runs and
  durations, connector up/down, LLM calls and tokens today, cache hit ratio, stale instruments,
  share of complete scores. Alert rules in `infra/monitoring/alerts.yml`.
- Data status page: connector health and latency, capability matrix, stored coverage, schedule,
  job history, audit trail. Per-instrument lineage on the Price and provenance tab.

---

## 12. Testing

| Suite | Count | What it covers |
|---|---|---|
| Domain unit tests | 126 | Every ratio and indicator with hand-calculated fixtures and edge cases; scoring weights and normalisation; theme DSL and all built-in themes; RRG; reconciliation; entry setup; concall scoring |
| Connector contract tests | 61 | MCP client behaviour (retries, SSE, sessions, breaker, allowlist); bindings and parsing against the **recorded real tool schemas and payloads** of NSE and Kite (`tests/fixtures/live/`); web search; LLM (mocked) |
| Service and API integration | 83 | Full pipeline over real MCP transport to mock servers; correlation cases (split, gap, conflict, outages); every API area; security (auth, rate limits, injection, size limits, redaction); scheduling with a controllable clock; snapshot immutability |
| Config and start-up | 16 | psycopg URL handling, `.env` cleaning, schema checks, unrecorded-schema adoption, interrupted jobs |
| Frontend component tests | 14 | Formatting, missing states, score displays, entry setup, filters, rotation chart, response contracts |
| Browser end-to-end | 13 | Main user flows on desktop and mobile |

The backend suite runs on SQLite by default and on PostgreSQL + Redis with `TEST_DATABASE_URL` and
`TEST_REDIS_URL`; both pass (283 tests, plus 3 Redis-only tests that skip without Redis). The CI
workflow runs lint, types, tests, the PostgreSQL migration check, the PostgreSQL + Redis
integration tests, dependency audits and the browser tests.

---

## 13. Technology stack

| Area | Technology (version in use) |
|---|---|
| Language | Python 3.12+ (venv on 3.13), TypeScript 6 |
| API | FastAPI 0.143, uvicorn 0.54, Pydantic 2.14, pydantic-settings |
| Database | PostgreSQL 16, psycopg 3.3, SQLAlchemy 2.1 (async), Alembic 1.20 |
| Cache / locks | Redis 7 (redis-py 8.1) or in-process |
| Scheduling | APScheduler 3.11 |
| MCP | Own Streamable HTTP client on httpx 0.28; MCP SDK 2.3 for the demo mock servers |
| Numerics | numpy 2.5, pandas 3.0 |
| LLM | Any OpenAI-compatible chat-completions provider set in `.env` (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID`); OpenRouter with `z-ai/glm-5.3-flash` in `.env.example` |
| Web search | ddgs 9.16 (default), Brave (optional) |
| Documents | pypdf 6.20 (transcript PDFs) |
| Observability | structlog 26, prometheus-client 0.26 |
| Frontend | React 19, Vite 8, Tailwind 4, TanStack Query 5, React Router 7, Lightweight Charts 5, zod 4 |
| Testing | pytest 9 (+ asyncio, cov), Vitest 5, Testing Library, Playwright 1.64 |
| Quality | Ruff, mypy, pip-audit, npm audit |
| Packaging | uv (`pyproject.toml` + `uv.lock`, exported `requirements*.txt`), npm (`package-lock.json`) |
| Containers | Docker Compose: postgres, redis, migrate, api, worker, web (nginx), mock-mcp (demo) |

---

## 14. Known technical limits

- Single-tenant authentication; no user accounts or roles.
- Screener filtering runs in memory over the latest snapshots (suits thousands of instruments).
- Valuation uses the latest annual period; no quarterly/TTM valuation or valuation history.
- Prices are split- and bonus-adjusted, not total-return.
- Kite MCP approval is bound to the API process's MCP session and must be repeated after restarts
  and daily.
- No OpenTelemetry tracing yet.
- The CI workflow on a runner, real Kite quotes/history and real model output have not been
  verified (see `REPOSITORY_GUIDE.md` §5). The Docker Compose stack has been verified.
