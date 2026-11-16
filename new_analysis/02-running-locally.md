# 02 · Running the system locally

A complete, step-by-step guide to running Markets Intelligence on your own machine.
All commands run from the repository root unless a `cd` is shown.

**Contents**

1. [Prerequisites](#1-prerequisites)
2. [One command: `./scripts/dev.sh`](#2-one-command--scriptsddevsh)
3. [Database options](#3-database-options)
4. [Demo mode vs live mode](#4-demo-mode-vs-live-mode)
5. [Running the pieces separately](#5-running-the-pieces-separately)
6. [The whole stack in Docker (on a laptop)](#6-the-whole-stack-in-docker-on-a-laptop)
7. [Optional integrations](#7-optional-integrations)
8. [Running the tests](#8-running-the-tests)
9. [Everyday operations](#9-everyday-operations)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Tool | Version | Why | Install (macOS) |
|---|---|---|---|
| [uv](https://docs.astral.sh/uv/) | any recent | Python package manager; downloads Python 3.12 itself | `brew install uv` |
| Node.js | 22+ | Frontend build/dev | `brew install node` |
| Docker Desktop | recent | PostgreSQL (and optionally Redis); must be **running, not paused** | `brew install --cask docker` |

Nothing else to install by hand: Python dependencies are pinned in `backend/uv.lock` (the start
script runs `uv sync`), JavaScript ones in `frontend/package-lock.json` (`npm install`).
`backend/requirements.txt` / `requirements-dev.txt` are generated from the lockfile for plain
`pip` users.

Create your configuration (the start script also does this if missing — never commit `.env`):

```bash
cp .env.example .env
```

Minimum footprint: without Redis the API uses an in-process cache; without Docker you can point
`DATABASE_URL` at any PostgreSQL 14+ (section 3).

## 2. One command: `./scripts/dev.sh`

```bash
./scripts/dev.sh
```

What it does, in order, stopping with a clear message at the first problem:

1. Creates `.env` from `.env.example` if missing.
2. Checks ports **8000** (API) and **5173** (web) are free — and tells you which process holds
   them if not (a paused Docker Desktop keeps ports reserved; resume it first).
3. `uv sync` for the backend.
4. Checks PostgreSQL; if it is a local URL and unreachable, starts it with
   `docker compose up -d --wait postgres` (and `redis` if `REDIS_URL` points at localhost).
5. Applies Alembic migrations (also repairs a database whose tables exist without a migration
   record).
6. **Demo mode only:** starts the mock MCP servers on 9100–9102 (synthetic NSE + Kite).
7. Starts the API on 8000 and the web app on 5173.

Then open **http://127.0.0.1:5173**.

- First start ingests and scores the demo universe in the background (~15 s). Until then the
  Overview says "No market data yet"; **Data status → Job history** shows progress.
- API reference while running: **http://127.0.0.1:8000/docs**.
- **Stop:** Ctrl+C stops the API, web app and mock servers. PostgreSQL/Redis containers keep
  running — stop them with `docker compose stop postgres redis`.
- Different API port: `API_PORT=8001 ./scripts/dev.sh` (the web app's proxy follows
  automatically). Different mock port: `MOCK_MCP_PORT=9200`.

## 3. Database options

Default `.env` values:

```bash
DATABASE_URL=postgresql+psycopg://markets:change-me-locally@localhost:5432/markets
POSTGRES_PASSWORD=change-me-locally
AUTO_CREATE_SCHEMA=false
```

`postgres://`, `postgresql://` and `postgresql+asyncpg://` URLs are rewritten to
`postgresql+psycopg://` automatically. Schema is created **only** by Alembic migrations — never
by hand, and keep `AUTO_CREATE_SCHEMA=false`.

**Option A — Docker Desktop (recommended; `dev.sh` does it automatically).** Start Docker
Desktop (resume if paused), then just run `./scripts/dev.sh`. Manual equivalent:

```bash
docker compose up -d --wait postgres redis   # data lives in the `pgdata` volume
docker compose ps                            # both should be "healthy"
docker compose stop postgres redis           # stop, keep data
docker compose down -v                       # stop and DELETE the database volume
```

**Option B — Homebrew PostgreSQL**

```bash
brew install postgresql@16 && brew services start postgresql@16
psql -d postgres -c "CREATE ROLE markets LOGIN PASSWORD 'change-me-locally';"
createdb -O markets markets
```

Optional Redis: `brew install redis && brew services start redis`.

**Option C — any other PostgreSQL 14+.** Point `DATABASE_URL` at it (add `?sslmode=require` if
it needs TLS). The user must be able to create tables.

**Check the database without starting the app:**

```bash
cd backend
uv run python -m app.devtools.preflight db       # reachability + version
uv run python -m app.devtools.preflight redis    # "ok" or "not configured (in-process cache)"
uv run python -m app.devtools.preflight migrate  # apply migrations (safe to repeat)
```

## 4. Demo mode vs live mode

Controlled by `DATA_MODE` in `.env`:

| | `demo` (default) | `live` |
|---|---|---|
| Market data | Local mock MCP servers, synthetic | Real NSE MCP + Kite MCP |
| Symbols | Fictional, start with `DEMO` | From `backend/data/reference/nse_universe.csv` |
| Financial statements | Generated synthetic figures | Only what you upload |
| Web research | Always off (symbols are fictional) | `ddgs` by default |
| Labelling | Orange "Demo mode" banner on every page | None |

Demo and live data never mix — switching modes removes the other mode's instruments on next
start. Keep them apart by using two databases if you want both.

### Quick tour of demo mode

| Page | What to check |
|---|---|
| Overview | Benchmark, sector table with rotation quadrants, top scores |
| Screener | Add a filter (e.g. ROE ≥ 15), sort, Export CSV |
| Themes | "Low realized volatility": members, methodology, snapshot history |
| Instrument `DEMOAUTO01` → Price and provenance | Recorded NSE-vs-broker disagreements |
| Sector rotation | The RRG and its table |
| Research board → `DEMOIT02` → Generate research | Rule-based summary; says model/web search were not used |
| Concall follow-through → `DEMOIT01` → Run analysis | Score with a verdict per commitment |
| Data status | Connector states, blocked broker tools, schedule, job history |

### Going live, step by step

1. In `.env` set `DATA_MODE=live`. The MCP URLs are already filled in:
   `NSE_BHAVCOPY_MCP_URL`, `NSE_CM_MARKET_MCP_URL`, `BROKER_MCP_URL`.
2. Choose instruments: edit `backend/data/reference/nse_universe.csv`. Fewer rows = faster first
   refresh.
3. Optional sanity check against the real servers:
   ```bash
   cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY && cd ..
   ```
4. `./scripts/dev.sh`
5. **Kite login** (optional gap-filling/cross-check): **Data status → Check broker session** →
   open the Kite link → approve read-only access → check again. The approval lives in the
   running API process and Kite expires it daily. Without it the app runs on NSE data alone.
6. **Data status → Refresh all data.** NSE history comes three months per call, so 3 years for
   one stock ≈ 12 calls; the first full refresh can take minutes. `HISTORY_YEARS=1` or a shorter
   universe goes faster.

The NSE MCP servers are sometimes slow or return 502/504; calls retry, then the source shows
"unavailable" with the reason while stored data keeps being served.

## 5. Running the pieces separately

Separate terminals, in this order — useful for debugging or tailoring logs:

```bash
# 1. Database (and Redis)
docker compose up -d --wait postgres redis

# 2. Migrations
cd backend && uv run python -m app.devtools.preflight migrate

# 3. Demo mode only: mock MCP servers (each on its own port)
cd backend && uv run python -m app.devtools.mock_mcp --port 9100
#    add --no-auto-login to make the mock broker require the login flow like real Kite

# 4. API (runs the schedule itself)
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/ready        # {"status":"ready", ...}

# 4b. Or: API without the schedule + a separate worker (use Redis so they share locks)
cd backend && SCHEDULER_ENABLED=false uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
cd backend && uv run python -m app.workers.run

# 5. Web app
cd frontend && npm install && npm run dev -- --host 127.0.0.1
#    different API port: VITE_API_PROXY=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1
```

The API refuses to start against a missing/outdated schema and prints the migration command;
prefer `preflight migrate` over plain `alembic upgrade head` because it also repairs
unrecorded-schema databases.

## 6. The whole stack in Docker (on a laptop)

```bash
cp .env.example .env
docker compose --profile demo up -d --build     # demo (includes mock MCP servers)
# live instead: set DATA_MODE=live in .env, then
docker compose up -d --build
```

| Service | What | Address |
|---|---|---|
| `postgres` | PostgreSQL 16, volume `pgdata` | 127.0.0.1:5432 |
| `redis` | Redis 7 (no persistence) | 127.0.0.1:6379 |
| `migrate` | `alembic upgrade head` once, then exits | — |
| `api` | FastAPI, scheduler off | 127.0.0.1:8000 |
| `worker` | Runs the schedule (`python -m app.workers.run`) | — |
| `web` | nginx serving the built app, proxying `/api` + `/health` | http://127.0.0.1:8080 |
| `mock-mcp` | Demo only (`--profile demo`) | internal |

Inside Compose, the API/worker always use
`postgresql+psycopg://markets:${POSTGRES_PASSWORD}@postgres:5432/markets` and
`redis://redis:6379/0` regardless of what `.env` says for those two.

One service at a time:

```bash
docker compose up -d --wait postgres redis
docker compose run --rm migrate
docker compose --profile demo up -d mock-mcp     # demo only
docker compose up -d api worker web
docker compose logs -f api
docker compose down                              # stop everything, keep data
```

Note: Compose publishes 8000/8080 on loopback. Stop the Compose stack (`docker compose down`)
before using `./scripts/dev.sh`, or run dev.sh with `API_PORT=8001` — the script detects Docker
holding port 8000 and tells you.

> Verification status recorded in the repo: the Compose file and Dockerfiles are written and
> statically checked, but had **not been built/run** on the authoring machine (Docker was
> paused). Expect the first `up --build` to be the shakedown run.

## 7. Optional integrations

Set in `.env`, then restart.

**Financial statements** (no MCP provides them; format in `docs/data-formats.md`):

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/instruments/NSE:INFY/financials" \
  -H "Content-Type: application/json" --data @infy_statements.json
```

Metrics recompute immediately after upload. Files land in `backend/var/fundamentals`.

**Language model (GLM through Hugging Face)**

```bash
LLM_ENABLED=true
HF_TOKEN=<your token>
HF_MODEL_ID=zai-org/GLM-5.3-Flash
```

Requires Inference Providers credits on the HF account (HTTP 402 otherwise → rule-based
fallback). `LLM_DAILY_BUDGET` / `LLM_DAILY_TOKEN_BUDGET` cap daily use.

**Web research:** `WEB_SEARCH_PROVIDER=ddgs` (default, no key) | `brave` + `WEB_SEARCH_API_KEY` |
`none`. Always off in demo mode.

**API auth:** `API_AUTH_TOKEN=<openssl rand -hex 32>` → every `/api/v1` call needs
`Authorization: Bearer <token>`; enter the same value under **Data status → Access** in the web
app.

**Concall transcripts:** Concall follow-through → choose company → Add a transcript (PDF, text
or pasted) — upload two consecutive quarters, then Run analysis.

## 8. Running the tests

```bash
cd backend
uv run pytest                    # ~280 tests, ~20 s; SQLite + mock MCP servers, no network
uv run ruff check . && uv run mypy app

cd ../frontend
npm run typecheck && npm test && npm run build
npx playwright install chromium  # first time only
npm run e2e                      # browser tests; starts the demo stack if not running
```

Integration tests on real PostgreSQL + Redis (empty database, migrated first):

```bash
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://markets:change-me-locally@localhost:5432/markets_test
createdb -h localhost -U markets markets_test    # or: docker compose exec postgres createdb -U markets markets_test
DATABASE_URL=$TEST_DATABASE_URL uv run python -m app.devtools.preflight migrate
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest
```

Security audits: `uvx pip-audit --disable-pip --no-deps -r requirements.txt` (backend) and
`npm audit --omit=dev` (frontend). CI (`.github/workflows/ci.yml`) runs all of the above.

## 9. Everyday operations

**Trigger jobs by hand**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all              # everything, in order
curl -X POST http://127.0.0.1:8000/api/v1/sector-rotation/refresh       # rotation only
curl -X POST http://127.0.0.1:8000/api/v1/themes/price-momentum/refresh # one theme
curl http://127.0.0.1:8000/api/v1/jobs/1                                # follow a job
```

Refresh triggers are rate-limited (default 3/min; research 6/min). Already-taken theme
snapshots are immutable — a re-refresh in the same month adds a new revision.

**Look at the data**

```bash
docker compose exec postgres psql -U markets -d markets
# psql: \dt tables · \d price_bars columns · \x expanded rows
```

Key tables: `instruments`, `price_bars`, `corporate_actions`, `source_conflicts`,
`data_source_records` (lineage), `financial_statement_periods`, `metric_snapshots`
(`is_latest` = current), `themes`/`theme_snapshots`, `sector_rotation_snapshots`,
`research_reports`/`research_sources`, `concall_*`, `watchlists`/`alerts`, `job_runs`,
`audit_events`, `alembic_version`.

```sql
SELECT id, kind, status, started_at, finished_at, error FROM job_runs ORDER BY id DESC LIMIT 10;
SELECT i.symbol, m.overall_score, m.as_of
FROM metric_snapshots m JOIN instruments i ON i.id = m.instrument_id
WHERE m.is_latest ORDER BY m.overall_score DESC NULLS LAST LIMIT 10;
```

**Backup / restore**

```bash
docker compose exec -T postgres pg_dump -U markets -Fc markets > markets_$(date +%F).dump
docker compose exec -T postgres pg_restore -U markets -d markets --clean --if-exists < markets_YYYY-MM-DD.dump
```

**Update the code:** `git pull && ./scripts/dev.sh` (syncs Python deps, installs npm deps,
applies new migrations). **Schema change:** edit `backend/app/db/models.py`, then
`uv run alembic revision --autogenerate -m "…"`, review the file in `backend/migrations/versions/`,
apply with `preflight migrate`, confirm with `uv run alembic check`.
**Dependency change:** `uv add <pkg>` (+ `../scripts/export-requirements.sh` to regenerate
requirements) / `npm install <pkg>`.
**Completely fresh start:** stop the app, `rm -rf backend/var`, `./scripts/dev.sh`.

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `dev.sh`: "port 8000 … is held by Docker" | A Compose stack publishes 8000/8080 → `docker compose down`, or `API_PORT=8001 ./scripts/dev.sh`. A **paused** Docker Desktop keeps ports reserved: resume first, then `docker compose down` |
| `dev.sh`: "PostgreSQL is not reachable … Docker is not available" | Start/resume Docker Desktop, or use Homebrew PostgreSQL, or set `DATABASE_URL` to your server |
| `docker compose up`: "Docker Desktop is manually paused" | Whale menu → Resume |
| API exits: "has no tables: no migration has been applied" | `cd backend && uv run python -m app.devtools.preflight migrate` |
| API exits: "schema is at migration X but this code expects Y" | Same command — code is newer than the database |
| `alembic upgrade head`: "relation … already exists" | Tables exist without a migration record → use `preflight migrate` |
| "password authentication failed for user markets" | The volume was created with a different password (PostgreSQL applies `POSTGRES_PASSWORD` only on first volume creation). Restore the old password in `.env`, or `docker compose exec postgres psql -U markets -d markets -c "ALTER USER markets PASSWORD 'new';"`. `docker compose down -v` also works but **deletes the database** |
| Data status shows a Redis warning | `REDIS_URL` set but Redis down. Start Redis or clear `REDIS_URL` (falls back to in-process cache locally) |
| Every API call returns 401 | `API_AUTH_TOKEN` is set → enter it under Data status → Access, or clear it |
| "No market data yet" for long | First refresh still running or failed → Data status → Job history |
| A job shows "interrupted" | Process stopped mid-run → run it again |
| NSE source "unavailable" (502/504/timeout) | NSE servers are intermittently down; stored data keeps being served, refresh later |
| Broker "login required" | Data status → Check broker session → approve → check again |
| Scores "Insufficient data" (live) | No financial statements uploaded, or not enough history; the page lists which pillars lack data |
| 12-month return / 1-year volatility missing | Fewer than 253 daily bars → `HISTORY_YEARS=2`+ and refresh |
| Research says the model was not used | `LLM_ENABLED` off, missing token/model id, no HF credits (402), or daily budget reached — the notice says which |
| "Too many refresh requests" | Rate limit; wait the seconds shown |
| Ports 9100–9102 busy | `lsof -ti :9100,:9101,:9102 | xargs kill`, or `MOCK_MCP_PORT=9200` |

Logs: `dev.sh` streams the API's JSON logs; every error surfaced in the web app carries a
request id you can search for there.

**Before any public/commercial use:** NSE states its MCP data is delayed and for informational
and educational use, not commercial deployment or AI model training. Confirm NSE, Kite and
search-provider terms.
