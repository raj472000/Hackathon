# Repository guide: what Markets Intelligence is, what it does, and where things are

This document explains the whole repository for someone new to it: the product, every feature,
how the parts fit, where each piece of code lives, and what is finished, verified, or not built.

- How to run and update it: [`HOW_TO_RUN.md`](HOW_TO_RUN.md)
- Architecture and technical specification: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Cloud deployment: [`RUN_AND_DEPLOY.md`](RUN_AND_DEPLOY.md)

---

## 1. The product in one paragraph

Markets Intelligence is an explainable research and screening tool for NSE-listed Indian
instruments. It pulls market data from the **NSE MCP servers** (primary) and the **Zerodha Kite
MCP server** (fills gaps and cross-checks NSE), stores it in **PostgreSQL**, computes financial,
valuation, technical and risk metrics deterministically, scores every instrument on five equally
weighted pillars, ranks instruments into rule-based themes, tracks sector rotation, and produces
evidence-grounded research boards and concall follow-through scores. Every number shows its source,
timestamp and freshness. It is a research tool: it **cannot place orders**, and nothing it shows is
investment advice or a prediction of returns.

---

## 2. Features

Each feature lists what it does, where it is in the web app, and where its code lives.

### 2.1 Market data from MCP servers, correlated

- **NSE MCP is the primary source.** Two servers: bhavcopy (daily history, corporate actions,
  index history, market breadth, symbol search) and CM market (delayed live quotes, index quotes,
  movers). Verified live: 21 and 15 advertised tools.
- **Kite MCP fills gaps and cross-checks.** Used only for read-only market data: instrument lookup,
  quotes, OHLC, last price, daily history. An allowlist of 7 tools is enforced in code; the 15
  order, GTT, holdings, positions, margins and trades tools are refused before any request is sent.
- **Tools are discovered, not assumed.** At start-up each server's advertised tools and argument
  schemas are read; arguments are bound to those schemas. A capability with no matching tool is
  reported "not supported" with the reason.
- **Correlation.** For every date, the bar comes from NSE when NSE has it; Kite fills dates NSE
  lacks. Where both report a value and differ beyond a tolerance (close 0.1%, live price 1%,
  volume 5%), the NSE value is kept and the disagreement is stored and shown.
- **Corporate actions.** NSE prices are unadjusted; they are split- and bonus-adjusted with NSE's
  corporate-action data before comparison. Unexplained price gaps are flagged.
- **Provenance.** Every price bar records its provider, adjustment and retrieval time; every
  provider call is recorded with status, row count and reason.
- **Degraded states, never invented values.** A source that is down, unsupported, not configured or
  awaiting login is shown as such; nothing is substituted.
- Web app: **Data status** (connectors, advertised and blocked tools, capability matrix, broker
  login, job history) and each instrument's **Price and provenance** tab.
- Code: `backend/app/connectors/` (`mcp_client.py`, `nse_mcp.py`, `kite_mcp.py`, `binding.py`,
  `parsing.py`), `backend/app/services/market_data.py`, `backend/app/domain/reconcile.py`,
  `backend/app/domain/adjust.py`.

### 2.2 Instrument master

Symbols, exchange, ISIN (from Kite when available), sector, industry, sector kind (industrial,
financial, index), lot size, trading status, broker instrument token. Live mode reads
`backend/data/reference/nse_universe.csv` (about 50 large NSE stocks and 8 indices); demo mode uses
48 fictional `DEMO…` companies and 11 demo indices. Code: `backend/app/services/ingestion.py`.

### 2.3 Fundamentals

No MCP server provides financial statements, so they are uploaded as one normalised JSON document
per company (validated: units, duplicate periods, numeric fields, symbol). Values are normalised to
INR crore. From them the system computes growth (YoY, 3- and 5-year CAGR), margins, ROE, ROA, ROCE,
ROIC, leverage, coverage, liquidity, working-capital days, free cash flow, CFO/PAT, accruals,
payout, dilution, and multi-year stability measures. Ratios that do not describe banks, NBFCs or
insurers are marked "not applicable" for them instead of being scored. Web app: instrument
**Financial statements** and **All metrics** tabs. Code: `backend/app/connectors/fundamentals_file.py`,
`backend/app/domain/financial_metrics.py`.

### 2.4 Valuation

P/E, PEG (only for positive growth, with its growth basis shown), P/B, P/S, EV, EV/EBITDA, EV/EBIT,
EV/Sales, price/FCF, FCF yield, earnings yield, dividend yield, market cap, net debt, and P/E
percentile within the sector. Negative or non-meaningful ratios are shown with a reason, never as
"cheap". Code: `backend/app/domain/valuation.py`.

### 2.5 Price action, technicals and risk

Returns (1W to 5Y), relative strength against the benchmark and the sector, moving averages
(20/50/100/200), RSI, MACD, ATR, Bollinger Bands, ADX, relative volume, volume trend, 52-week range
and breakout status, moving-average alignment, momentum acceleration, trend persistence; realised
and downside volatility, maximum drawdown, beta, correlation, average traded value, gap risk, and a
Low/Moderate/High/Very High risk label with disclosed thresholds. No look-ahead: every value uses
only data up to its date. Code: `backend/app/domain/technicals.py`, `backend/app/domain/risk.py`.

### 2.6 Five-pillar research score

Fundamentals, valuation, momentum, risk and sector context, **20% each**. Each metric becomes a
percentile rank within its peer set (valuation within the sector when it has five or more members),
oriented so higher is better. Missing and inapplicable metrics leave the average; a pillar needs
half its metrics. If any pillar lacks data the overall score is withheld and a labelled partial
score is shown. Also: data completeness, confidence (High/Medium/Low from coverage and freshness),
research stance (Favorable/Mixed/Unfavorable/Insufficient data). Web app: instrument **Summary**
tab with a "How this is calculated" panel; the five-bar pillar glyph appears across the app. Code:
`backend/app/domain/scoring.py`, `backend/app/services/metrics.py`.

### 2.7 Screener

Filter the universe on any of ~100 metrics, sort any column, paginate, choose columns, save screens
(in the browser), export CSV with source and as-of metadata. Missing values are shown as missing
with the reason and never satisfy a filter. Web app: **Screener**. Code:
`backend/app/services/screener.py`, `frontend/src/pages/Screener.tsx`.

### 2.8 Themes

70 built-in themes in nine categories (momentum, low risk, quality, value, contrarian, income,
growth, financial strength, event context), each a validated specification rather than code. Five
that need data no source provides (analyst estimates, governance data, a corporate-action calendar,
commodity exposure) are listed as unavailable. Custom themes are written in plain English, turned
into reviewable filters with the assumptions listed, and saved only after confirmation. Each
refresh stores an immutable snapshot; a later refresh in the same month adds a revision and never
changes earlier ones. Web app: **Themes**. Code: `backend/app/domain/theme_dsl.py`,
`backend/app/domain/themes_builtin.py`, `backend/app/services/themes.py`.

### 2.9 Sector rotation (relative rotation graph)

Weekly relative strength (RS-Ratio) and its momentum (RS-Momentum) of each sector against the
benchmark, plotted with 10-week trails in four quadrants (Leading, Weakening, Lagging, Improving).
Refreshed every 12 hours; each refresh is stored per 12-hour slot, and a failed refresh keeps the
last good snapshot. On-demand views with other lookbacks, trail lengths and dates; snapshot
comparison; a help panel; a table of the same values. Sectors without an index use a labelled
equal-weight composite of their constituents. Web app: **Sector rotation**. Code:
`backend/app/domain/rrg.py`, `backend/app/services/sector_rotation.py`.

### 2.10 Research board

For one instrument: current reconciled quote, metrics and scores, and recent news and filings from
web search (`ddgs` by default), assembled into an evidence bundle. With a language model configured
(GLM through the OpenAI-compatible provider configured in `.env`), the model writes pros, cons, catalysts, risks, valuation, technical and
sector context, news and invalidation conditions, citing evidence ids. Every statement is checked:
it must cite retrieved evidence, contain no unretrieved URLs, and state no numbers absent from what
it cites, or it is removed. Without a model, a rule-based board is produced and labelled as such.
Web app: **Research board**. Code: `backend/app/services/research.py`, `backend/app/connectors/llm.py`,
`backend/app/connectors/web_search.py`.

### 2.11 Entry setup

A checklist, not a buy signal: trend, momentum, not overextended, valuation, quality, risk,
liquidity and data freshness, each with its rule and current value. States: Setup favorable,
Watch, Unfavorable, Insufficient data. Shows what to wait for and what would weaken the setup. No
entry zone or target price is produced, because there is no backtested method. Code:
`backend/app/domain/entry_setup.py`.

### 2.12 Concall follow-through analyzer

Upload two consecutive quarters of earnings-call transcripts (PDF, text or pasted). Commitments made
in the previous call are checked against the current call, each getting a verdict (delivered,
partly delivered, in progress, deferred, not delivered, not addressed), and a 0–100 follow-through
score is computed from importance-weighted credits. Every quote must appear verbatim in its
transcript or the item is excluded. With a model the classification is done by GLM; without one a
keyword heuristic is used and confidence is Low. Web app: **Concall follow-through**. Code:
`backend/app/services/concall.py`, `backend/app/domain/concall_score.py`.

### 2.13 Watchlists and alerts

Lists of instruments with price, returns, score, stance, risk, entry state and freshness. Alerts on
price moves, metric crossings, theme membership changes, sector quadrant changes, stale data and new
filings; idempotent (no duplicate notifications), pausable, with an in-app history. No email or push
delivery is configured, and none is claimed. Web app: **Watchlists and alerts**. Code:
`backend/app/services/alerts.py`.

### 2.14 Overview dashboard

Benchmark level and trend, universe breadth, exchange breadth from NSE, sector performance with
rotation quadrants, top and bottom movers, theme leaders, watchlist highlights, recent jobs.
Web app: **Overview**.

### 2.15 Operations

Background jobs with one-run-at-a-time locks, run history and an audit trail; jobs left running by a
stopped process are marked interrupted at the next start. Schedules: prices daily, rotation every 12
hours, themes monthly, instruments daily, alerts every 15 minutes. JSON logs with request ids and
secret redaction; Prometheus metrics at `/metrics`; liveness and readiness endpoints; alert rules in
`infra/monitoring/alerts.yml`. Code: `backend/app/services/jobs.py`, `backend/app/workers/`,
`backend/app/observability/`.

---

## 3. Repository map

```
Hackathon/
├── CLAUDE.md                 product requirements this repository implements
├── PLAN.md                   delivery status, decisions, verification log
├── README.md                 short overview and quick start
├── analysis/                 run guide, repository guide, architecture spec, deployment guide
├── docs/                     methodology (formulas), data formats, operations runbook, design spec
├── .env.example              every setting with a safe placeholder
├── docker-compose.yml        postgres, redis, migrate, api, worker, web, mock-mcp (demo profile)
├── scripts/
│   ├── dev.sh                one-command local start (database, migrations, API, web)
│   ├── check_connectors.py   live check of the real MCP servers with the app's connectors
│   └── export-requirements.sh  regenerate requirements*.txt from uv.lock
├── infra/
│   ├── docker/               backend and frontend Dockerfiles, nginx config
│   └── monitoring/alerts.yml Prometheus alert rules
├── .github/workflows/ci.yml  lint, types, tests, PostgreSQL + Redis integration, audits, e2e
├── backend/
│   ├── pyproject.toml, uv.lock, requirements*.txt
│   ├── alembic.ini, migrations/    database migrations (PostgreSQL schema)
│   ├── data/reference/nse_universe.csv   live-mode instrument list
│   ├── app/
│   │   ├── main.py           FastAPI app: start-up checks, middleware, errors, health, metrics
│   │   ├── api/              REST routes (deps.py: auth, rate limits, instrument resolution)
│   │   │   └── v1/market.py, v1/user.py
│   │   ├── core/             settings, container (wiring), cache/locks, retry/breaker/limiters
│   │   ├── connectors/       MCP client, NSE MCP, Kite MCP, fundamentals files, web search, LLM
│   │   ├── domain/           pure deterministic maths (no I/O)
│   │   ├── services/         ingestion, correlation, metrics, themes, rotation, research,
│   │   │                     concall, alerts, screener, jobs, pipeline
│   │   ├── db/               SQLAlchemy models and engine
│   │   ├── workers/          scheduler and standalone worker
│   │   ├── observability/    JSON logging, Prometheus metrics
│   │   └── devtools/         demo-only mock MCP servers, synthetic data, start-up pre-flight
│   └── tests/                domain, connectors (incl. recorded real MCP contracts), services,
│                             API, security, scheduling, config
└── frontend/
    ├── src/pages/            Overview, Screener, Themes, Instrument, Research, Rotation,
    │                         Concalls, Watchlists, Status
    ├── src/components/       layout, charts (price chart, rotation graph), score displays, UI kit
    ├── src/api/              API client and response contracts (zod)
    ├── src/test/             component tests
    └── e2e/                  browser tests (Playwright)
```

---

## 4. What has been done

| Area | State |
|---|---|
| Backend (FastAPI, 45 API operations, 25 tables) | Built |
| Frontend (9 pages, desktop and mobile) | Built |
| NSE MCP adapter | Built and verified against the real servers: discovery, quotes, paged history, index history and quotes, corporate actions, a full pipeline run |
| Kite MCP adapter | Built; discovery, allowlist and instrument lookup verified against the real server |
| NSE + Kite correlation | Built; verified with mock servers that reproduce gaps, disagreements and a split |
| PostgreSQL via psycopg 3 | Default database; migration, full test suite and start-up verified on PostgreSQL 16 |
| Redis | Optional cache and lock store; verified on Redis 6.2 |
| Start-up checks | Port conflicts, database reachability, schema version, unrecorded schemas, `.env` notes — all reported with the fix |
| Metrics, scoring, themes, rotation, entry setup | Built, unit-tested with hand-calculated fixtures |
| Research board and web search (`ddgs`) | Built; verified with real searches and real NSE quotes |
| Concall analyzer | Built; heuristic path verified on demo transcripts; model path covered by mocked tests |
| Watchlists, alerts, jobs, audit, observability | Built and tested |
| Docker Compose | Verified: images build, migration runs, all services healthy, app reachable through nginx in live mode |
| CI workflow | Written; every CI step passes locally |

Recent changes (2026-10-15):

- PostgreSQL with **psycopg 3** is the database; asyncpg removed; other URL forms rewritten.
- The **Kite Connect REST** integration was removed; Kite is used only through its MCP server.
- Start-up fixes: migrations run before the API; the API refuses an unmigrated or out-of-date
  database with the command to run; tables created without a migration record are adopted instead
  of failing; `.env` values with trailing `# notes` or stray spaces work (Docker Compose passes
  notes through); port conflicts (for example a Compose stack holding port 8000) are named with the
  fix; jobs left running by a stopped process are marked interrupted.

## 5. Verified and not verified

| Verified | Not verified |
|---|---|
| NSE MCP against the real servers | Kite quotes and history against the real server (needs the account holder's login) |
| Kite MCP discovery, allowlist, instrument lookup | Real output from the language model (no `LLM_API_KEY` configured yet) |
| Web search through `ddgs` | — |
| PostgreSQL 16 + Redis: migration, ~280 tests, start-up script, live mode | CI on a GitHub runner (no remote configured) |
| Docker Compose stack end to end, and `scripts/dev.sh` against it | — |
| Browser tests (13) against a PostgreSQL-backed stack | Cloud deployments in `RUN_AND_DEPLOY.md` |

## 6. Not built

User accounts and roles (one optional API token instead); email or push alert delivery;
backtesting (so no entry zones or target prices); commodity data ingestion (the schema allows it);
quarterly and trailing-twelve-month valuation; valuation history percentiles; analyst estimates;
governance data; a filings connector for financial statements; OpenTelemetry tracing (metrics and
structured logs are in place); choosing the rotation benchmark in the interface.

## 7. Rules the system keeps

- Never fabricates prices, statements, ratios, news, citations or tool results; missing data is
  shown as missing with the reason.
- The language model only summarises supplied evidence and classifies concall statements; it
  computes no numbers, scores or rankings.
- Never places orders; broker trading tools are blocked in code.
- Secrets stay on the server and out of logs; demo data is always labelled and never mixed with
  live data.
- NSE states its MCP data is delayed and for informational and educational use, not commercial
  deployment or AI model training.
