# How to run and update Markets Intelligence

The complete, step-by-step guide to installing, starting, stopping, updating and testing the
system on your own machine. Cloud deployment is in [`RUN_AND_DEPLOY.md`](RUN_AND_DEPLOY.md);
what the system does is in [`REPOSITORY_GUIDE.md`](REPOSITORY_GUIDE.md); how it is built is in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

All commands run from the project root, `/Users/rajeshsanapala/Documents/Hackathon`, unless a
`cd` is shown.

**Contents**

1. [What runs, and on which ports](#1-what-runs-and-on-which-ports)
2. [One-time setup](#2-one-time-setup)
3. [PostgreSQL (the database)](#3-postgresql-the-database)
4. [Start everything with one command](#4-start-everything-with-one-command)
5. [Demo mode and live mode](#5-demo-mode-and-live-mode)
6. [Live mode step by step](#6-live-mode-step-by-step)
7. [Optional integrations](#7-optional-integrations)
8. [Start each part separately](#8-start-each-part-separately)
9. [Run the whole stack in Docker](#9-run-the-whole-stack-in-docker)
10. [Look at the data](#10-look-at-the-data)
11. [Update the system](#11-update-the-system)
12. [Trigger jobs by hand, and the schedule](#12-trigger-jobs-by-hand-and-the-schedule)
13. [Run the tests](#13-run-the-tests)
14. [Troubleshooting](#14-troubleshooting)
15. [Configuration reference](#15-configuration-reference)

---

## 1. What runs, and on which ports

| Part | Port | Needed? | What it is |
|---|---|---|---|
| Web app (Vite dev server) | 5173 | Yes | The React interface. Talks only to the API |
| API (FastAPI) | 8000 | Yes | REST API; also runs the scheduled jobs unless a worker does |
| PostgreSQL 16 | 5432 | Yes | The database. Driver: **psycopg 3** |
| Redis 7 | 6379 | Optional | Shared cache and job locks. Without it the API uses an in-process cache |
| Worker | — | Optional | Runs the schedule in its own process instead of the API |
| Mock MCP servers | 9100–9102 | Demo mode only | Stand-ins for NSE bhavcopy, NSE CM market and Kite, serving synthetic data |

Market data comes from **MCP servers only**: NSE MCP (primary) and Zerodha Kite MCP (fills gaps
and cross-checks). There is no Kite Connect REST integration.

---

## 2. One-time setup

```bash
brew install uv node                 # uv = Python package manager, node = JavaScript runtime (22+)
uv --version && node --version
```

uv downloads the right Python (3.12+) itself. For the database you need **one** of: Docker
Desktop (recommended; used automatically) or PostgreSQL 16 from Homebrew. See section 3.

Create your configuration file (the start script also does this if it is missing):

```bash
cp .env.example .env                 # never commit .env
```

Dependencies are pinned in lockfiles: `backend/uv.lock` (Python) and `frontend/package-lock.json`
(JavaScript). `backend/requirements.txt` and `backend/requirements-dev.txt` are generated from the
lockfile for plain `pip`. Nothing needs installing by hand: the start script runs `uv sync` and
`npm install`.

---

## 3. PostgreSQL (the database)

The default connection, in `.env`:

```bash
DATABASE_URL=postgresql+psycopg://markets:change-me-locally@localhost:5432/markets
POSTGRES_PASSWORD=change-me-locally
AUTO_CREATE_SCHEMA=false
```

`postgres://…`, `postgresql://…` and `postgresql+asyncpg://…` URLs are accepted and rewritten to
`postgresql+psycopg://…` automatically. For a managed database add `?sslmode=require`.

The tables are created and upgraded by **Alembic migrations**, never by hand. The start script
runs them for you. Pick one way to provide PostgreSQL:

### Option A — Docker Desktop (recommended, automatic)

1. Start Docker Desktop. If it shows **Paused**, choose **Resume** in the Whale menu: a paused
   Docker cannot start containers, and its old port mappings stay reserved (see section 14).
2. Run `./scripts/dev.sh`. When nothing answers on `localhost:5432` it runs
   `docker compose up -d --wait postgres` (and `redis` if `REDIS_URL` points at localhost).

Manual equivalent:

```bash
docker compose up -d --wait postgres redis   # data is kept in the `pgdata` Docker volume
docker compose ps                            # both should be "healthy"
docker compose stop postgres redis           # stop them (data kept)
docker compose down -v                       # stop and DELETE the database volume
```

### Option B — Homebrew PostgreSQL

```bash
brew install postgresql@16
brew services start postgresql@16
psql -d postgres -c "CREATE ROLE markets LOGIN PASSWORD 'change-me-locally';"
createdb -O markets markets
```

Optional Redis: `brew install redis && brew services start redis`.

### Option C — any other PostgreSQL 14+

Set `DATABASE_URL` in `.env` to it (with `?sslmode=require` if it needs TLS). The user must be
able to create tables in that database.

### Check the database without starting the app

```bash
cd backend
uv run python -m app.devtools.preflight db      # e.g. "database: PostgreSQL 16.x at postgresql+psycopg://markets:***@localhost:5432/markets"
uv run python -m app.devtools.preflight redis   # "redis: ok …" or "not configured (in-process cache)"
uv run python -m app.devtools.preflight migrate # apply migrations (safe to repeat)
```

---

## 4. Start everything with one command

```bash
./scripts/dev.sh
```

What it does, in order, stopping with a clear message at the first problem:

1. Creates `.env` from `.env.example` if missing.
2. Checks that ports 8000 (API) and 5173 (web) are free, and says who holds them if not.
3. `uv sync` for the backend.
4. Checks PostgreSQL; starts it with Docker Compose if it is local and not running.
5. Checks Redis (if `REDIS_URL` is set); starts it with Docker Compose if local, otherwise the API
   uses its in-process cache.
6. Applies migrations (`preflight migrate`). If an older start-up created tables without recording
   a migration, it records them first instead of failing with "table already exists".
7. Demo mode: starts the mock MCP servers on 9100–9102.
8. Starts the API on 8000 and the web app on 5173.

Open **http://127.0.0.1:5173**. On the first start the API fills the database in the background
(about 15 seconds in demo mode; several minutes in live mode). Until then the Overview says
"No market data yet"; **Data status → Job history** shows progress.

**Stop:** Ctrl+C stops the API, web app and mock servers. PostgreSQL and Redis containers keep
running; stop them with `docker compose stop postgres redis`.

**Another API port:** `API_PORT=8001 ./scripts/dev.sh` (the web app follows automatically).

---

## 5. Demo mode and live mode

Set by `DATA_MODE` in `.env`.

| | `demo` (default in `.env.example`) | `live` |
|---|---|---|
| Market data | Local mock MCP servers, synthetic | Real NSE MCP and Kite MCP |
| Symbols | Fictional, start with `DEMO` | Every NSE-listed equity (`UNIVERSE_SCOPE=all`), or the rows of `backend/data/reference/nse_universe.csv` |
| Financial statements | Generated synthetic figures | Only what you upload (section 7) |
| Concall transcripts | Two synthetic transcripts for `DEMOIT01` | Only what you upload |
| Web research | Always off | `ddgs` by default |
| Labelling | Orange "Demo mode" banner on every page | None |

Demo and live data never mix: switching mode removes the other mode's instruments on the next
start. To keep both, use two databases (two `DATABASE_URL` values).

### A quick tour (demo mode)

| Page | What to check |
|---|---|
| Overview | Benchmark, sector table with rotation quadrants, top scores, watchlist highlights |
| Screener | Add a filter (ROE at least 15), sort a column, Export CSV |
| Themes | "Low realized volatility": members, methodology, exclusions, snapshot history |
| Instrument `DEMOAUTO01` → Price and provenance | Two recorded NSE-vs-broker disagreements of 2% |
| Instrument `DEMOIT03` → Price and provenance | A split under "Corporate actions applied" |
| Sector rotation | The rotation graph and its table |
| Research board → `DEMOIT02` → Generate research | Rule-based summary that says the model and web search were not used |
| Concall follow-through → `DEMOIT01` → Run analysis | Score with a verdict per commitment |
| Data status | Connector states, blocked broker tools, schedule, job history |

---

## 6. Live mode step by step

1. In `.env`: `DATA_MODE=live`. The MCP URLs are already set:
   ```bash
   NSE_BHAVCOPY_MCP_URL=https://mcp.nseindia.in/bhavcopy/cm/mcp
   NSE_CM_MARKET_MCP_URL=https://mcp.nseindia.in/cmmkt/mcp
   BROKER_MCP_URL=https://mcp.kite.trade/mcp
   ```
2. Choose the instruments.
   - `UNIVERSE_SCOPE=all` (the default in `.env.example`) tracks **every NSE-listed equity** in
     `UNIVERSE_SERIES` (EQ and BE, about 2,580 stocks). The list comes from NSE's `EQUITY_L.csv`
     (`NSE_EQUITY_LIST_URL`).
   - Sectors are NSE's industry classification from the Nifty Total Market constituents file
     (`NSE_INDUSTRY_LIST_URL`), which covers about 750 stocks.
   - Other stocks take the sector shared by NSE-classified stocks with the same NSE industry label
     (`smIndustry` on the company's NSE announcements, e.g. "Computers - Software"). A label is used
     only when at least 60% of those stocks agree; otherwise the stock stays unclassified. The rule
     and the counts are shown as the instrument's classification source.
   - Labels are kept in `backend/var/nse_filings/industry_labels.json` and fetched by the daily
     instruments job.
   - Prices for the whole market come from NSE's daily bhavcopy (`NSE_BHAVCOPY_URL_TEMPLATE`): one
     file per trading day for all stocks. Splits and bonus issues are back-adjusted using NSE's
     market-wide corporate-actions list.
   - `UNIVERSE_SCOPE=reference` tracks only the rows in `backend/data/reference/nse_universe.csv`
     (`symbol,name,sector,industry,sector_kind,instrument_type,sector_index_symbol,isin`). That file is
     also used with `all`: for indices, for the sector-index mapping, and for hand-checked sectors,
     which override NSE's.
   - Financial statements for ~2,580 companies are fetched over several refreshes:
     `NSE_FILINGS_MAX_COMPANIES_PER_RUN` (400) per run, companies without statements first.
3. Optional check of the real servers with the app's own connectors:
   ```bash
   cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY && cd ..
   ```
4. `./scripts/dev.sh`
5. **Kite MCP login** (for broker gap-filling and cross-checks): open **Data status → Check broker
   session**, open the Kite link, approve read-only access in Kite, then check again. The approval
   belongs to the running API process and Kite expires it daily; repeat after a restart. Without
   it the system runs on NSE data alone and shows the broker as "login required".
6. **Data status → Refresh all data**. With `UNIVERSE_SCOPE=all`, the first refresh downloads one
   bhavcopy file per trading day: three years is about 800 files (~200 KB each), roughly 7 minutes
   at `NSE_FILINGS_MIN_INTERVAL_SECONDS=0.5`. Files are cached in `backend/var/nse_filings`, so later
   refreshes download only new days. Indices and the reference stocks also use the per-symbol NSE
   MCP history tool, which returns three months per call.

The NSE MCP servers are sometimes slow or return 502/504. Calls retry, then the source shows
"unavailable" with the reason; stored data keeps being served.

What live mode shows without the optional pieces:

| Missing | On screen |
|---|---|
| Financial statements | Fundamentals and valuation "Not available"; scores incomplete or "Insufficient data" |
| Kite login | Broker "login required"; no gap filling or cross-checking |
| Language model | Research and concall analysis use rule-based fallbacks and say so |
| Web search off or rate-limited | No news or catalysts, with a notice that web research was unavailable |

---

## 7. Optional integrations

Set in `.env`, then restart `./scripts/dev.sh`.

### Financial statements

No MCP server provides them. Upload one JSON document per company (format in
`docs/data-formats.md`):

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/instruments/NSE:INFY/financials" \
  -H "Content-Type: application/json" --data @infy_statements.json
```

Metrics are recomputed straight after an upload.

### Language model (any OpenAI-compatible provider)

The app calls `POST {LLM_BASE_URL}/chat/completions`. No provider is built into the code: which
one is used is decided only by these `.env` values. For OpenRouter (create a key at
https://openrouter.ai/settings/keys and add credits):

```bash
LLM_ENABLED=true
LLM_PROVIDER_NAME=OpenRouter                    # display name only
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...                        # server-side only, never commit it
LLM_MODEL_ID=z-ai/glm-5.3-flash                 # exact id from https://openrouter.ai/models
LLM_EXTRA_HEADERS={"X-Title": "Markets Intelligence"}
LLM_EXTRA_BODY={"usage": {"include": true}, "reasoning": {"effort": "low", "exclude": true}}
#   usage: OpenRouter reports each call's cost. reasoning: GLM is a reasoning model; its thinking counts
#   against LLM_MAX_OUTPUT_TOKENS (6000), and without a low effort a long prompt can leave no answer.
LLM_RESPONSE_FORMAT=json_schema
LLM_VERIFY_MODEL_LIST=true
```

To use another provider, change the same keys, for example:

| Provider | `LLM_BASE_URL` | Notes |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | clear `LLM_EXTRA_BODY` |
| Groq | `https://api.groq.com/openai/v1` | clear `LLM_EXTRA_BODY` |
| Together | `https://api.together.xyz/v1` | clear `LLM_EXTRA_BODY` |
| Ollama (local) | `http://localhost:11434/v1` | `LLM_API_KEY=ollama`, `LLM_RESPONSE_FORMAT=json_object`, `LLM_VERIFY_MODEL_LIST=false` |

`LLM_RESPONSE_FORMAT` is `json_schema` (strict structured output), `json_object`, or `none`; with
the last two the JSON schema is sent in the prompt instead. `LLM_BASE_URL` is used as written,
so a provider whose compatible API is not under `/v1` works too.

Restart the API. Data status → Language model shows the provider name, the base URL, whether the
model id is in the provider's model list (checked without spending credits), the provider's error
if a call fails (for example HTTP 402 when the account has no credits), similar ids if it looks
mistyped, and today's calls, tokens and cost when the provider reports it. Without a working
model, research, concall analysis, "Ask the market" and the theme AI filter use rule-based
fallbacks and say so. `LLM_DAILY_BUDGET` and `LLM_DAILY_TOKEN_BUDGET` cap daily use.

### Web research

`WEB_SEARCH_PROVIDER=ddgs` (default, no key) or `brave` with `WEB_SEARCH_API_KEY`, or `none`.
Always off in demo mode. `ddgs` uses public search endpoints that rate-limit; a failed search is
reported as "web research unavailable", never as "no news".

### Require a token for the API

`API_AUTH_TOKEN=<long random value>` (`openssl rand -hex 32`), then enter the same value under
Data status → Access in the web app.

### Concall transcripts

Concall follow-through → choose a company → Add a transcript (PDF, text file or pasted text).
Upload two consecutive quarters, then Run analysis.

---

## 8. Start each part separately

Use separate terminals when you want separate logs or are debugging one part. Start in this order.

```bash
# 1. Database (and Redis) — section 3
docker compose up -d --wait postgres redis

# 2. Migrations
cd backend && uv run python -m app.devtools.preflight migrate

# 3. Demo mode only: mock MCP servers on 9100, 9101, 9102
cd backend && uv run python -m app.devtools.mock_mcp --port 9100
#    add --no-auto-login to make the mock broker require the login flow like real Kite

# 4. API (also runs the schedule)
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/ready        # {"status":"ready", ...}

# 4b. Or: API without the schedule + a separate worker (use Redis so they share locks)
cd backend && SCHEDULER_ENABLED=false uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
cd backend && uv run python -m app.workers.run

# 5. Web app
cd frontend && npm install && npm run dev -- --host 127.0.0.1
#    another API port: VITE_API_PROXY=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1
```

API reference while running: **http://127.0.0.1:8000/docs**.

The API refuses to start against a database whose schema is missing or behind the code, with a
message naming the command to run. Prefer `preflight migrate` over `alembic upgrade head`: it
also repairs a database whose tables were created without a migration record.

---

## 9. Run the whole stack in Docker

```bash
cp .env.example .env
docker compose --profile demo up -d --build     # demo: includes the mock MCP servers
# live data instead: set DATA_MODE=live in .env, then
docker compose up -d --build
```

| Service | What | Address |
|---|---|---|
| `postgres` | PostgreSQL 16, volume `pgdata` | 127.0.0.1:5432 |
| `redis` | Redis 7 (no persistence) | 127.0.0.1:6379 |
| `migrate` | Runs `alembic upgrade head` once, then exits | — |
| `api` | API, scheduler off | 127.0.0.1:8000 |
| `worker` | Runs the schedule | — |
| `web` | nginx serving the built app, proxying `/api` | http://127.0.0.1:8080 |
| `mock-mcp` | Demo only (`--profile demo`) | internal |

Inside Compose the API and worker always use `postgresql+psycopg://markets:${POSTGRES_PASSWORD}@postgres:5432/markets`
and `redis://redis:6379/0`, whatever `.env` says for those two settings.

One service at a time:

```bash
docker compose up -d --wait postgres redis
docker compose run --rm migrate
docker compose --profile demo up -d mock-mcp     # demo only
docker compose up -d api worker web
docker compose logs -f api
docker compose down                              # stop all, keep data
```

The Compose stack publishes ports 8000 and 8080. Stop it (`docker compose down`) before using
`./scripts/dev.sh`, which needs port 8000; or run dev.sh with `API_PORT=8001`.

> **Verified on 2026-10-15** on this machine (Docker Desktop 29.8, Apple Silicon): all images build;
> `migrate` applies the schema and exits 0; `postgres`, `redis`, `api`, `worker` and `web` become
> healthy; the web app and API answer through nginx on port 8080 in live mode, with psycopg, Redis and
> the worker running the schedule. `./scripts/dev.sh` was also verified against the Compose
> PostgreSQL and Redis with the same `.env`.

---

## 10. Look at the data

```bash
docker compose exec postgres psql -U markets -d markets        # database in Docker
psql postgresql://markets:change-me-locally@localhost:5432/markets        # from the Mac (Homebrew psql)
```

GUI tools (DBeaver, TablePlus, pgAdmin, Postico): host `127.0.0.1`, port `5432`, user `markets`,
database `markets`, password from `POSTGRES_PASSWORD`.

`psql` basics: `\dt` tables, `\d price_bars` columns, `\x` expanded rows, `\q` quit.

| Table | Holds |
|---|---|
| `instruments`, `instrument_identifiers` | Symbol, exchange, ISIN, sector, broker instrument token |
| `price_bars` | Daily OHLCV (split-adjusted) with `provider`, `adjustment`, `retrieved_at` |
| `corporate_actions` | Splits, bonuses, dividends from NSE |
| `source_conflicts` | Where NSE and Kite disagreed beyond tolerance, and which value was used |
| `data_source_records` | Lineage: every provider call, status, rows, reason |
| `financial_statement_periods` | Uploaded statements per period (INR crore) |
| `metric_snapshots` | Every computed metric and score per instrument (`is_latest` = current) |
| `themes`, `theme_versions`, `theme_snapshots`, `theme_snapshot_constituents` | Theme definitions and immutable monthly results |
| `sector_rotation_snapshots` | Rotation graph results, one per 12-hour slot |
| `research_reports`, `research_sources` | Research boards and the sources they cited |
| `concall_transcripts`, `concall_analyses` | Uploaded transcripts and follow-through results |
| `watchlists`, `watchlist_items`, `alerts`, `alert_events` | Lists, alerts and notification history |
| `job_runs`, `audit_events` | Job history; user and admin actions |
| `alembic_version` | Which migration the database is at |

```sql
SELECT id, kind, status, started_at, finished_at, error FROM job_runs ORDER BY id DESC LIMIT 10;

SELECT i.symbol, p.date, p.close, p.volume, p.provider
FROM price_bars p JOIN instruments i ON i.id = p.instrument_id
WHERE i.symbol = 'INFY' ORDER BY p.date DESC LIMIT 5;          -- 'DEMOIT01' in demo mode

SELECT i.symbol, i.sector, m.overall_score, m.as_of
FROM metric_snapshots m JOIN instruments i ON i.id = m.instrument_id
WHERE m.is_latest ORDER BY m.overall_score DESC NULLS LAST LIMIT 10;

SELECT * FROM source_conflicts ORDER BY id DESC LIMIT 10;
```

Backup and restore:

```bash
docker compose exec -T postgres pg_dump -U markets -Fc markets > markets_$(date +%F).dump
docker compose exec -T postgres pg_restore -U markets -d markets --clean --if-exists < markets_2026-10-15.dump
```

---

## 11. Update the system

### Get new code

```bash
git pull
./scripts/dev.sh        # syncs Python packages, installs new npm packages, applies new migrations
```

If `frontend/package-lock.json` changed and the web app fails to start, run
`cd frontend && npm install` once.

### Change the database schema

1. Edit the models in `backend/app/db/models.py`.
2. Generate a migration against an up-to-date database:
   ```bash
   cd backend
   uv run python -m app.devtools.preflight migrate
   uv run alembic revision --autogenerate -m "describe the change"
   ```
3. Review the new file in `backend/migrations/versions/`, then apply it:
   `uv run python -m app.devtools.preflight migrate`.
4. Confirm models and migrations agree: `uv run alembic check` → "No new upgrade operations detected."

### Change Python or JavaScript dependencies

```bash
cd backend && uv add <package>          # or: uv remove <package>; dev-only: uv add --dev <package>
../scripts/export-requirements.sh        # regenerate requirements*.txt from uv.lock
cd ../frontend && npm install <package>
```

### Change what is tracked or how often

- Instruments: `UNIVERSE_SCOPE` (all NSE-listed equities, or `backend/data/reference/nse_universe.csv`), then **Refresh all data**.
- Schedules: the `*_CRON` settings in `.env` (cron syntax, India time), then restart.
- Rate limits, history length, cache lifetimes: see section 15.

### Re-fetch everything

**Data status → Refresh all data**, or `curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all`.
Theme snapshots already taken are never changed; a refresh in the same month adds a new revision.

---

## 12. Trigger jobs by hand, and the schedule

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all              # everything, in order
curl -X POST http://127.0.0.1:8000/api/v1/sector-rotation/refresh       # rotation only
curl -X POST http://127.0.0.1:8000/api/v1/themes/price-momentum/refresh # one theme
curl http://127.0.0.1:8000/api/v1/jobs/1                                # follow a job
```

Refresh triggers are limited to 3 per minute and research to 6 per minute by default. Only one run
of a given job runs at a time; a second request while one is running is recorded as "skipped". A
job that was running when the process stopped is marked "interrupted" at the next start.

| Job | Default schedule (India time) |
|---|---|
| Prices, fundamentals reload, metrics | 19:30 on weekdays |
| Sector rotation | Every 12 hours |
| Themes | 06:30 on the 1st of each month |
| Instrument master | 07:00 on weekdays |
| Alerts | Every 15 minutes |

---

## 13. Run the tests

```bash
cd backend
uv run pytest                    # ~280 tests, about 20 s; SQLite + mock MCP servers, no network
uv run ruff check . && uv run mypy app

cd ../frontend
npm run typecheck && npm test && npm run build
npx playwright install chromium  # first time only
npm run e2e                      # browser tests; starts the demo stack if it is not running
```

Run the integration tests on **PostgreSQL and Redis** (empty, migrated database):

```bash
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://markets:change-me-locally@localhost:5432/markets_test
createdb -h localhost -U markets markets_test        # or: docker compose exec postgres createdb -U markets markets_test
DATABASE_URL=$TEST_DATABASE_URL uv run python -m app.devtools.preflight migrate
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest
```

Security audits: `uvx pip-audit --disable-pip --no-deps -r requirements.txt` (backend) and
`npm audit --omit=dev` (frontend).

---

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `dev.sh`: "port 8000 … is held by Docker" | A Compose stack publishes 8000 (and 8080). `docker compose down`, or `API_PORT=8001 ./scripts/dev.sh`. A **paused** Docker Desktop keeps these ports reserved: resume it first, then `docker compose down` |
| `dev.sh`: "PostgreSQL is not reachable … Docker is not available" | Start or resume Docker Desktop, or use Homebrew PostgreSQL (section 3, option B), or set `DATABASE_URL` to your server |
| `docker compose up` fails with "Docker Desktop is manually paused" | Whale menu → Resume |
| API exits with "has no tables: no migration has been applied" | `cd backend && uv run python -m app.devtools.preflight migrate` |
| API exits with "schema is at migration X but this code expects Y" | Same command: the code is newer than the database |
| `alembic upgrade head` fails with "relation … already exists" | Tables were created without a migration record (an old start with `AUTO_CREATE_SCHEMA=true`). Use `preflight migrate`, which records them and continues |
| "password authentication failed for user markets" (in `dev.sh`, the API or the Compose `migrate` service) | The database volume was created with a different password: PostgreSQL applies `POSTGRES_PASSWORD` only when it first creates the volume. Either put the original password in `.env` (`POSTGRES_PASSWORD` and `DATABASE_URL`; the default has always been `change-me-locally`), or change it inside the container: `docker compose exec postgres psql -U markets -d markets -c "ALTER USER markets PASSWORD 'new-password';"`. `docker compose down -v` also works but **deletes the database** |
| API exits with "Cannot reach the database" | PostgreSQL is down or `DATABASE_URL` is wrong; `uv run python -m app.devtools.preflight db` shows the exact error |
| Settings look wrong in Docker | A note after a value (`KEY=value   # note`) is passed through by Compose. The app strips such notes, but keep comments on their own lines (as in `.env.example`) |
| Data status shows a Redis cache warning | `REDIS_URL` is set but Redis is not running. Local runs fall back to the in-process cache; start Redis or clear `REDIS_URL` |
| Every API call returns 401 | `API_AUTH_TOKEN` is set; enter it under Data status → Access, or clear it |
| "No market data yet" for a long time | The first refresh is still running, or failed: Data status → Job history shows which |
| A job shows "interrupted" | The process stopped while it ran. Run it again |
| NSE sources "unavailable" (502/504/timeout) | The NSE servers are intermittently down. Stored data keeps being served; refresh later |
| Broker "login required" | Data status → Check broker session → open the link → approve → check again |
| Scores "Insufficient data" in live mode | No financial statements uploaded, or not enough history; the page lists which pillars lack data |
| 12-month return or 1-year volatility missing | Fewer than 253 daily bars; use `HISTORY_YEARS=2` or more and refresh |
| Research says the model was not used | `LLM_ENABLED` off, missing token or model id, no OpenRouter credits (402), or daily budget reached; the notice says which |
| "Too many refresh requests" | Rate limit; wait the seconds shown |
| Ports 9100–9102 busy | An old mock server is running: `lsof -ti :9100,:9101,:9102 \| xargs kill`, or `MOCK_MCP_PORT=9200` |

Logs: `dev.sh` prints the API's JSON logs. Every error shown in the web app carries a request id
that you can search for in them.

---

## 15. Configuration reference

All settings live in `.env` (template: `.env.example`). Blank means "not set". Keep comments on
their own lines.

| Setting | Default | Meaning |
|---|---|---|
| `APP_ENV` | `local` | `local`, `staging`, `production`. Outside local, an unreachable Redis is an error instead of a fallback |
| `DATA_MODE` | `demo` | `demo` (mock MCP, synthetic) or `live` (real MCP servers) |
| `DATABASE_URL` | `postgresql+psycopg://markets:change-me-locally@localhost:5432/markets` | PostgreSQL through psycopg 3 |
| `POSTGRES_PASSWORD` | `change-me-locally` | Password of the Compose database (must match `DATABASE_URL`) |
| `AUTO_CREATE_SCHEMA` | `false` | Leave false: migrations create the schema |
| `REDIS_URL` | blank | Blank = in-process cache; needed when API and worker run separately |
| `SCHEDULER_ENABLED` | `true` | Set false on the API when a worker runs the schedule |
| `AUTO_INITIAL_REFRESH` | `true` | Fill an empty database automatically at first start |
| `API_AUTH_TOKEN` | blank | When set, every `/api/v1` call needs `Authorization: Bearer <token>` |
| `CORS_ORIGINS` | local Vite URLs | Allowed browser origins |
| `NSE_BHAVCOPY_MCP_URL`, `NSE_CM_MARKET_MCP_URL`, `BROKER_MCP_URL` | the real MCP URLs | Used in live mode |
| `MCP_*`, `BREAKER_*`, `*_MIN_INTERVAL_SECONDS` | see `.env.example` | Timeouts, retries, concurrency, pacing, circuit breaker |
| `NSE_FILINGS_BASE_URL`, `NSE_FILINGS_DOCUMENT_HOSTS`, `NSE_FILINGS_MIN_INTERVAL_SECONDS`, `NSE_FILINGS_TIMEOUT_SECONDS` | `https://www.nseindia.com/api`, `nsearchives.nseindia.com`, 1 s, 30 s | Financial results (XBRL) and con-call transcripts as filed with NSE; empty URL disables. Fundamentals refresh with the daily price job |
| `LLM_ENABLED`, `LLM_PROVIDER_NAME`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID`, `LLM_EXTRA_HEADERS`, `LLM_EXTRA_BODY`, `LLM_*` | off, none built in | The model provider (any OpenAI-compatible API) and its budgets |
| `WEB_SEARCH_PROVIDER`, `WEB_SEARCH_API_KEY` | `ddgs` | Web research provider |
| `*_CRON` | see section 12 | Job schedules (India time) |
| `BENCHMARK_SYMBOL` | `NIFTY 50` | Benchmark for relative strength and sector rotation |
| `HISTORY_YEARS` | `3` | Years of daily history fetched on first ingest |
| `CACHE_TTL_*`, `RATE_LIMIT_*`, `MAX_REQUEST_BYTES` | see `.env.example` | Cache lifetimes, API rate limits, upload size |
| `API_PORT`, `MOCK_MCP_PORT` | 8000, 9100 | Environment variables for `scripts/dev.sh` |

Before any public or commercial use: NSE states its MCP data is delayed and for informational and
educational use, not commercial deployment or AI model training. Check NSE, Kite and
search-provider terms.
