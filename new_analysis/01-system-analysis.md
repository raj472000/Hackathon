# 01 · System analysis

What the system is, how it is built, and what runs where. Everything below was verified against
the current code; file paths are relative to the repository root.

## 1. What it does

Markets Intelligence is a research and screening platform for NSE-listed instruments:

- **Screener** — filter the tracked universe by financial and technical characteristics.
- **Themes** — rule-based screens (momentum, low volatility, quality, value, …) with immutable
  monthly snapshots; users can also create custom themes from natural language which are
  translated into a validated filter specification.
- **Sector rotation** — a Relative Rotation Graph (RRG) of sectors vs a benchmark, refreshed
  every 12 hours.
- **Research board** — per-instrument evidence-grounded analysis: pros/cons, catalysts, risks,
  valuation and technical context, with citations to actually-retrieved sources.
- **Entry setup** — a checklist of measurable conditions (favorable / watch / unfavorable /
  insufficient data), never an unconditional "buy" signal or a target price.
- **Concall follow-through** — scores how much of what management promised in a previous
  conference call was implemented in the current one, from uploaded transcripts.
- **Watchlists and alerts** — threshold-based, recorded in the app only.

Hard boundaries: no order placement, no portfolio changes, nothing presented as investment
advice or a prediction of returns. Only read-only broker tools are callable — order, GTT,
holdings, positions and margin tools are refused in code before any request is sent.

## 2. How the data works

- **MCP only.** Market data comes from MCP servers: NSE MCP (`https://mcp.nseindia.in/bhavcopy/cm/mcp`
  and `…/cmmkt/mcp`) is primary; Zerodha Kite MCP (`https://mcp.kite.trade/mcp`) fills gaps and
  cross-checks. There is no Kite Connect REST integration.
- **Capability discovery.** Tool names and argument names are read from each server at start-up;
  a capability with no advertised tool is reported as *not supported*, never assumed.
- **Precedence.** When NSE and Kite both report a value and disagree beyond a tolerance, the NSE
  value is kept and the disagreement is stored (`source_conflicts` table) and shown in the UI.
- **Provenance everywhere.** Every number carries provider, retrieval time, freshness. A value
  that cannot be computed is shown as missing with a reason — never as zero.
- **Two modes.** `DATA_MODE=demo` runs the real connector code against local **mock MCP servers**
  serving synthetic data (fictional `DEMO…` symbols, an orange banner on every page);
  `DATA_MODE=live` talks to the real servers. The two never mix in one database.
- **Deterministic maths, optional LLM.** Ratios, technicals, risk, scoring, themes and RRG are
  pure, versioned, unit-tested code in `backend/app/domain`. The LLM (GLM through Hugging Face,
  off by default) only writes summaries from supplied evidence; output is schema-validated and
  grounding-checked, with daily call/token budgets.
- **Fundamentals have no connector.** Financial statements are uploaded per company
  (`PUT /api/v1/instruments/{ref}/financials`, format in `docs/data-formats.md`) and stored in
  `backend/var/fundamentals` plus the database. Until then valuation shows as missing and scores
  are marked incomplete.

## 3. Architecture

```
  Browser
     │
     ▼
┌──────────────┐  /api, /health   ┌──────────────────┐     ┌────────────────────────────┐
│ Web app      │ ───────────────▶ │ API (FastAPI)    │ ──▶ │ NSE MCP / Kite MCP          │
│ React + Vite │                  │ + scheduler      │     │ (or local mock MCP in demo) │
│ :5173 (dev)  │                  │ :8000            │     └────────────────────────────┘
│ :8080 (nginx)│                  └──────┬───────────┘
└──────────────┘                         │
                          ┌──────────────┼──────────────────┐
                          ▼              ▼                  ▼
                  ┌──────────────┐ ┌─────────────┐  ┌──────────────────┐
                  │ Database     │ │ Redis       │  │ Worker (optional)│
                  │ PostgreSQL 16│ │ (optional)  │  │ runs the schedule│
                  │ via psycopg 3│ │ cache+locks │  │ instead of API   │
                  └──────────────┘ └─────────────┘  └──────────────────┘
```

| Part | Required? | Default when you do nothing |
|---|---|---|
| Web app | Yes | Vite dev server on 5173 (local) or nginx on 8080 (containers) |
| API | Yes | Port 8000; also runs the scheduled jobs unless a worker does |
| PostgreSQL 16 | Yes | Started with Docker Compose by `scripts/dev.sh`; driver psycopg 3 |
| Redis 7 | No | In-process cache and locks (fine for one API process) |
| Worker | No | The API runs the schedule itself (`SCHEDULER_ENABLED=true`) |
| Mock MCP servers | Demo only | Ports 9100–9102, synthetic data |

### Backend layout (`backend/app`)

| Package | Responsibility |
|---|---|
| `connectors` | MCP client, NSE MCP and Kite MCP adapters, fundamentals file storage, web search (`ddgs`/Brave), Hugging Face LLM client |
| `domain` | Pure deterministic maths: ratios, valuation, technicals, risk, scoring, themes, RRG |
| `services` | Ingestion, correlation, metrics, themes, rotation, research, concall, alerts, jobs |
| `api/v1` | Versioned REST API under `/api/v1` |
| `workers` | APScheduler-based scheduler; standalone entry point `python -m app.workers.run` |
| `devtools` | Demo-only mock MCP servers, preflight checks, synthetic data |
| `db`, `models`, `migrations` | SQLAlchemy 2 models and Alembic migrations (schema comes only from migrations; `AUTO_CREATE_SCHEMA=false`) |
| `observability` | structlog JSON logs, Prometheus metrics (`/metrics`), OpenTelemetry hooks |

Notable behaviours:

- On start the API refuses to serve if the schema is missing or behind the code, and prints the
  exact migration command to run.
- With `AUTO_INITIAL_REFRESH=true` (default) an empty database is ingested and scored
  automatically at first start (~15 s in demo, minutes in live).
- Health endpoints: `GET /health/live` (liveness) and `GET /health/ready` (readiness) on 8000.
- Job runs are recorded in `job_runs`, are idempotent, lock-protected (only one run of a job at
  a time; a second request is recorded as "skipped"), and a job interrupted by a restart is
  marked as such at next start.

### Frontend layout (`frontend/src`)

React 19 + TypeScript + Vite, Tailwind 4, TanStack Query, Lightweight Charts, Zod response
validation. Pages: overview, screener, themes, instrument detail, research board, sector
rotation, watchlists/alerts, concall follow-through, data status. The browser only ever talks to
the API through the Vite proxy (`/api`, `/health`) in dev, or the same origin via nginx in
production — no provider URLs or credentials are compiled into the bundle.

### Scheduled jobs (default cron, Asia/Kolkata, all configurable in `.env`)

| Job | Schedule |
|---|---|
| Prices + metrics refresh | `30 19 * * 1-5` (19:30 weekdays, after NSE close) |
| Sector rotation (RRG) | `0 */12 * * *` (every 12 hours) |
| Themes | `30 6 1 * *` (06:30 on the 1st, monthly) |
| Instrument master | `0 7 * * 1-5` (07:00 weekdays) |
| Alerts | `*/15 * * * *` (every 15 minutes) |

## 4. Technology stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2 (async) + Alembic, psycopg 3, APScheduler, httpx, `mcp` SDK, pandas/numpy, structlog, Prometheus client, OpenTelemetry API/SDK, uv as package manager (`uv.lock` + generated `requirements*.txt`) |
| Frontend | React 19, TypeScript, Vite 8, Tailwind 4, TanStack Query 5, react-router 7, Lightweight Charts, Zod, Vitest, Playwright, oxlint |
| Data stores | PostgreSQL 16 (system of record), Redis 7 (optional cache + distributed locks) |
| External | NSE MCP (primary market data), Zerodha Kite MCP (broker data, read-only allowlist), Hugging Face Inference (GLM, optional), `ddgs`/Brave web search (optional) |
| Infra | Docker (two images), Docker Compose, nginx 1.27, GitHub Actions CI |

## 5. What runs on which port

| Part | Port | Notes |
|---|---|---|
| Vite dev server | 5173 | `./scripts/dev.sh`; proxies `/api` + `/health` to 8000 |
| API | 8000 | FastAPI; `/docs` = OpenAPI reference; `/metrics` = Prometheus |
| PostgreSQL | 5432 | Compose publishes it on `127.0.0.1` only |
| Redis | 6379 | Compose publishes it on `127.0.0.1` only |
| nginx web container | 8080 | Serves built app, proxies `/api` + `/health` to the API |
| Mock MCP servers | 9100–9102 | Demo mode only; base port movable with `MOCK_MCP_PORT` |

## 6. Configuration surface

Everything is configured through environment variables (`.env`, template `.env.example`).
The important groups:

| Group | Key variables |
|---|---|
| App | `APP_ENV` (local/staging/production), `DATA_MODE` (demo/live), `LOG_LEVEL`, `DEFAULT_MARKET_TIMEZONE`, `API_AUTH_TOKEN`, `CORS_ORIGINS`, `SCHEDULER_ENABLED`, `AUTO_INITIAL_REFRESH`, `AUTO_CREATE_SCHEMA` (keep `false`) |
| Database | `DATABASE_URL` (psycopg 3 URL; `postgres://`/`postgresql://`/`+asyncpg://` are rewritten), `POSTGRES_PASSWORD` (Compose DB) |
| Cache | `REDIS_URL` (blank = in-process cache) |
| MCP | `NSE_BHAVCOPY_MCP_URL`, `NSE_CM_MARKET_MCP_URL`, `BROKER_MCP_URL`, timeouts/retries/concurrency/pacing, circuit breaker |
| LLM | `LLM_ENABLED`, `HF_TOKEN`, `HF_MODEL_ID` (e.g. `zai-org/GLM-5.3-Flash`), `HF_ENDPOINT_URL`, timeouts/retries/output caps, daily call+token budgets |
| Web search | `WEB_SEARCH_PROVIDER` (`ddgs` no-key / `brave` / `none`), `WEB_SEARCH_API_KEY` |
| Schedules | `THEME_REFRESH_CRON`, `RRG_REFRESH_CRON`, `PRICE_REFRESH_CRON`, `INSTRUMENT_REFRESH_CRON`, `ALERTS_CRON` |
| Universe & limits | `BENCHMARK_SYMBOL`, `HISTORY_YEARS`, cache TTLs, `RATE_LIMIT_*`, `MAX_REQUEST_BYTES` |
| dev.sh only | `API_PORT` (8000), `MOCK_MCP_PORT` (9100) |

The tracked universe lives in `backend/data/reference/nse_universe.csv`
(`symbol,name,sector,industry,sector_kind,instrument_type,sector_index_symbol,isin`; 60
instruments currently, sector labels manually maintained).

## 7. Repository layout

```
backend/            FastAPI app, Alembic migrations, tests, uv.lock, reference data
  app/connectors    MCP client + NSE/Kite adapters, fundamentals files, search, LLM
  app/domain        deterministic maths (ratios, valuation, technicals, scoring, RRG)
  app/services      ingestion, metrics, themes, rotation, research, concall, alerts, jobs
  app/api/v1        REST API
  app/workers       scheduler + standalone worker entry point
  app/devtools      mock MCP servers, preflight, synthetic demo data
  data/reference    nse_universe.csv (what to track)
frontend/           React + Vite app, component tests, Playwright e2e
infra/docker/       backend.Dockerfile, frontend.Dockerfile, nginx.conf
infra/monitoring/   alert rules
scripts/            dev.sh (one-command local start), check_connectors.py, export-requirements.sh
analysis/, docs/    earlier analysis and methodology docs
docker-compose.yml  postgres, redis, migrate, api, worker, web, mock-mcp (profile "demo")
.github/workflows/  CI: lint, types, tests, migrations, integration, audits, e2e
```

## 8. Verification status (as recorded in the repo)

- Backend: ~271 tests passing, 86% line coverage; frontend 14 component tests; 13 browser e2e
  tests (demo stack).
- Real NSE MCP verified 2026-10-12 (tool discovery, quotes, paged history, index history,
  corporate actions, full pipeline run). Kite MCP: discovery/allowlist verified; quotes/history
  need a login and are covered by mock contract tests only.
- Hugging Face route/token/model id confirmed; inference returned HTTP 402 (no credits at the
  time), so real-model behaviour is covered by mocked tests.
- PostgreSQL 16.2 + Redis 6.2 integration verified (migrations drift-free, worker ran a job).
- **Docker Compose, Dockerfiles and nginx were written but not yet executed on the authoring
  machine** (Docker was unavailable). Treat the first container run as a shakedown; the CI
  workflow's steps were each run locally but the workflow itself has not run on GitHub runners.
