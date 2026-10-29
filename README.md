<div align="center">

# 📊 Markets Intelligence

### Explainable research & theme screening for Indian markets

Discover NSE stocks by fundamentals, valuation, momentum or your own custom themes,<br/>
see how sectors rotate, and check whether management kept last quarter's promises.

<br/>

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)
<br/>
![React](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
<br/>
![MCP](https://img.shields.io/badge/Data-NSE_MCP_·_Kite_MCP_·_Tickertape_MCP-F59E0B?style=for-the-badge)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Azure](https://img.shields.io/badge/Deploy-Azure_Container_Apps-0078D4?style=for-the-badge&logo=microsoftazure&logoColor=white)

<br/>

[✨ Features](#-features) ·
[📸 Screenshots](#-screenshots) ·
[🚀 Quick start](#-run-it-locally) ·
[🏗️ Architecture](#%EF%B8%8F-architecture) ·
[⚙️ Configuration](#%EF%B8%8F-configuration) ·
[☁️ Deployment](#%EF%B8%8F-deployment)

</div>

<br/>

> [!IMPORTANT]
> **A research tool, not a trading system.** It cannot place orders, and nothing it shows is
> investment advice or a prediction of returns. Every score is a research classification.

---

## ✨ Features

### 🔎 Research & screening

| | Feature | What it does |
|:-:|---|---|
| 🏠 | **Overview** | Index levels, sector performance, theme leaders, watchlist highlights and data freshness at a glance. |
| 💬 | **Ask the market** | Ask in plain words — *"strongest stocks in the last 3 days"*, *"compare HDFCBANK and ICICIBANK"* — answered from live MCP data, with stored data as a clearly labelled fallback. |
| 🧮 | **Screener** | The whole NSE equity universe: filter builder, sortable columns, saved screens, CSV export with source and as-of metadata. |
| 🎯 | **Themes** | Momentum, low risk, quality, value, contrarian, income, growth and financial-strength themes as a validated filter DSL. Write **custom themes in natural language** and review the filters before saving. Monthly snapshots are never overwritten. |
| 🤖 | **Theme AI filter** | Narrow any theme's members on request (*"only low-volatility names"*) without touching its snapshots. |
| 🏢 | **Instrument detail** | Quote, chart, annual and quarterly statements, valuation, technicals, risk, peers, matching themes and field-level provenance. |
| 🏅 | **Five-pillar score** | Fundamentals · Valuation · Price action · Risk · Market context — **20% each**. Peer-percentile ranked; missing metrics are excluded and disclosed, never scored as zero. |
| 📝 | **Research board** | LLM-written pros, cons, catalysts and risks built from a retrieved evidence bundle. Every citation is checked against what was actually retrieved. |
| 🚦 | **Entry setup** | 🟢 Favorable · 🟡 Watch · 🔴 Unfavorable · ⚪ Insufficient data — with every rule that passed or failed and what would invalidate the thesis. |
| 🔄 | **Sector rotation** | Relative Rotation Graph with quadrants, animated trails, replay and a companion table. Refreshed every 12 hours. |
| 🎙️ | **Concall analyzer** | Reads con-call transcripts filed with NSE, extracts last quarter's commitments, checks them against this quarter's call and the news, and scores the **follow-through**. Every quote must appear verbatim in the transcript. |

### 📈 Market pulse

| | Feature | What it does |
|:-:|---|---|
| 📰 | **Market news** | NSE announcements and headlines with a rule-based, low-confidence 🐂 bullish / 🐻 bearish / ➖ neutral reading. |
| 🌍 | **Macro** | Crude, dollar index, USD/INR, US yields, global indices, India CPI (MoSPI), optional FRED series. |
| 🏦 | **FII / DII flows** | Daily institutional flows and official NSDL FPI history. |
| 📉 | **F&O and OI** | Derivatives movers, open interest and participant-wise OI from NSE F&O bhavcopy files. |

### 🧰 You & operations

| | Feature | What it does |
|:-:|---|---|
| ⭐ | **Watchlists** | Your own lists, with highlights on the overview. |
| 🔔 | **Alerts** | Price, indicator, theme inclusion, filing, valuation, rotation-quadrant and data-outage alerts (in-app). |
| 🩺 | **Data status** | Health, latency and last success for every source; Kite and Tickertape login; LLM status; job history; rate-limited manual refresh. |
| 🧑‍💻 | **Assistant** | A chat widget that answers questions about the app and takes you to the right page, stock or theme. |
| 🌗 | **Responsive & themed** | Desktop and mobile layouts, light and dark themes, keyboard accessible. |

---

## 📸 Screenshots

<table>
  <tr>
    <td width="50%" align="center"><img src="analysis/proof/17-overview-desktop.png" alt="Overview page with index cards, Ask the market and the chat assistant" width="100%"/><br/><sub>🏠 <b>Overview</b> — indices, Ask the market, assistant</sub></td>
    <td width="50%" align="center"><img src="analysis/proof/readme-rotation.png" alt="Sector rotation graph of 22 NSE sectors against NIFTY 50" width="100%"/><br/><sub>🔄 <b>Sector rotation</b> — 22 sectors vs NIFTY 50</sub></td>
  </tr>
</table>

<sub>More screenshots in <a href="analysis/proof/"><code>analysis/proof/</code></a>.</sub>

---

## 🛡️ How it treats data

| | Principle |
|:-:|---|
| 🥇 | **NSE MCP is the primary source** — end-of-day history, corporate actions, delayed quotes, index history. |
| 🥈 | **Zerodha Kite MCP fills gaps** and cross-checks. If both disagree beyond a tolerance, NSE wins and the conflict is stored and shown. |
| 📑 | **Fundamentals** come from financial results (XBRL) filed with NSE. **Tickertape MCP** fills a metric only when the NSE value is missing — never overrides it. |
| 🔍 | **No invented tools.** Tool names and arguments are read from each MCP server at start-up; anything not advertised is reported as *not supported*. |
| 🔒 | **Read-only by construction.** Order, GTT, holdings, positions, margin and account tools are refused in code before any request is sent. |
| 🕒 | **Every number is traceable** — source, timestamp and freshness. A value that cannot be computed shows as missing with a reason, never as zero. |
| 🧪 | **Deterministic maths.** All arithmetic is unit-tested and versioned. The LLM only interprets questions, writes summaries from supplied evidence and classifies concall statements — schema-validated and grounding-checked. |
| 🔁 | **Resilient connectors.** Timeouts, bounded concurrency, retries with backoff, circuit breakers, and fast retries on NSE's intermittent *"Session not found"* responses before falling back to Kite. |

---

## 🏗️ Architecture

```mermaid
flowchart LR
    subgraph Sources["🌐 Data sources"]
        NSE["NSE MCP<br/>bhavcopy · CM market"]
        KITE["Kite MCP<br/>(fallback)"]
        TT["Tickertape MCP<br/>(fundamentals fallback)"]
        FIL["NSE filings<br/>XBRL · transcripts"]
        PULSE["NSDL · MoSPI · FRED<br/>F&O bhavcopy"]
        WEB["Web search<br/>ddgs / Brave"]
    end

    subgraph Backend["⚙️ FastAPI modular monolith"]
        CONN["Connectors<br/>retry · breaker · rate limit"]
        DOM["Domain<br/>ratios · technicals · scoring<br/>themes · RRG · concall score"]
        SVC["Services<br/>ingestion · screener · research<br/>ask · concall · alerts"]
        API["REST API /api/v1"]
        SCHED["Scheduler / worker"]
    end

    LLM["🤖 LLM<br/>OpenAI-compatible"]
    DB[("🐘 PostgreSQL")]
    CACHE[("⚡ Redis")]
    UI["🖥️ React + Vite"]

    Sources --> CONN --> SVC
    SVC <--> DOM
    SVC <--> DB
    SVC <--> CACHE
    SVC <-->|evidence → summary| LLM
    SCHED --> SVC
    API --> SVC
    UI <--> API
```

<details>
<summary><b>📁 Repository layout</b></summary>

```
backend/app/connectors   🔌 MCP client; NSE, Kite, Tickertape adapters; NSE filings; NSDL; macro; search; LLM
backend/app/domain       🧮 pure deterministic maths: ratios, valuation, technicals, risk, scoring, themes, RRG
backend/app/services     🧩 ingestion, metrics, screener, themes, rotation, research, ask, concall, pulse, alerts
backend/app/api/v1       🌐 REST API
backend/app/workers      ⏰ scheduler and standalone worker
backend/app/devtools     🧪 preflight checks, demo-only mock MCP servers and synthetic data
frontend/src             🖥️ React + TypeScript + Vite (TanStack Query, Tailwind, Lightweight Charts)
infra/                   🐳 Dockerfiles, nginx, Azure deployment scripts, alert rules
docs/                    📚 architecture, methodology, data formats, operations
analysis/                🗺️ run guide, repository guide, data-flow walkthroughs, screenshots
```

</details>

---

## 🚀 Run it locally

All commands run from the project root unless a `cd` is shown.

### 🧭 What runs, and on which ports

| | Part | Port | Needed? | What it is |
|:-:|---|---|---|---|
| 🖥️ | Web app (Vite dev server) | 5173 | Yes | The React interface; talks only to the API |
| ⚙️ | API (FastAPI) | 8000 | Yes | REST API; also runs the scheduled jobs unless a worker does |
| 🐘 | PostgreSQL 16 | 5432 | Yes | The database (driver: psycopg 3) |
| ⚡ | Redis 7 | 6379 | Optional | Shared cache and job locks; without it the API uses an in-process cache |
| ⏰ | Worker | — | Optional | Runs the schedule in its own process instead of the API |
| 🧪 | Mock MCP servers | 9100–9102 | Demo mode only | Stand-ins for NSE bhavcopy, NSE CM market and Kite, serving synthetic data |

### 1️⃣ One-time setup

```bash
brew install uv node                 # uv = Python package manager, node = JavaScript runtime (22+)
uv --version && node --version
cp .env.example .env                 # never commit .env (dev.sh also creates it if missing)
```

uv downloads the right Python (3.12+) itself. Dependencies are pinned in `backend/uv.lock` and
`frontend/package-lock.json`; the start script runs `uv sync` and `npm install` for you.

### 2️⃣ PostgreSQL

The default connection in `.env`:

```bash
DATABASE_URL=postgresql+psycopg://markets:change-me-locally@localhost:5432/markets
POSTGRES_PASSWORD=change-me-locally
AUTO_CREATE_SCHEMA=false
```

Tables are created and upgraded by **Alembic migrations**, which the start script runs. Pick one
way to provide PostgreSQL:

<details open>
<summary><b>🐳 Option A — Docker Desktop (recommended, automatic)</b></summary>

1. Start Docker Desktop. If it shows **Paused**, choose **Resume** in the whale menu.
2. Run `./scripts/dev.sh`. When nothing answers on `localhost:5432` it runs
   `docker compose up -d --wait postgres` (and `redis` if `REDIS_URL` points at localhost).

```bash
docker compose up -d --wait postgres redis   # manual start; data kept in the pgdata volume
docker compose ps                            # both should be "healthy"
docker compose stop postgres redis           # stop (data kept)
docker compose down -v                       # stop and DELETE the database volume
```

</details>

<details>
<summary><b>🍺 Option B — Homebrew PostgreSQL</b></summary>

```bash
brew install postgresql@16
brew services start postgresql@16
psql -d postgres -c "CREATE ROLE markets LOGIN PASSWORD 'change-me-locally';"
createdb -O markets markets
brew install redis && brew services start redis   # optional
```

</details>

<details>
<summary><b>☁️ Option C — any other PostgreSQL 14+</b></summary>

Set `DATABASE_URL` in `.env` (add `?sslmode=require` if it needs TLS). `postgres://`,
`postgresql://` and `postgresql+asyncpg://` URLs are rewritten to `postgresql+psycopg://`
automatically. The user must be able to create tables.

</details>

Check the database without starting the app:

```bash
cd backend && uv run python -m app.devtools.preflight db
```

### 3️⃣ Start everything with one command

```bash
./scripts/dev.sh
```

What it does, stopping with a clear message at the first problem:

1. 📄 Creates `.env` from `.env.example` if missing.
2. 🔌 Checks that ports 8000 and 5173 are free, and says who holds them if not.
3. 🐍 `uv sync` for the backend.
4. 🐘 Checks PostgreSQL; starts it with Docker Compose if it is local and not running.
5. ⚡ Checks Redis (if `REDIS_URL` is set); otherwise the API uses its in-process cache.
6. 📜 Applies migrations (`preflight migrate`).
7. 🧪 Demo mode: starts the mock MCP servers on 9100–9102.
8. 🚀 Starts the API on 8000 and the web app on 5173.

| | Open |
|:-:|---|
| 🖥️ | Web app — <http://127.0.0.1:5173> |
| 📖 | API docs — <http://127.0.0.1:8000/docs> |

On the first start the API fills the database in the background (about 15 seconds in demo mode,
several minutes in live mode). Until then the Overview says *"No market data yet"*;
**Data status → Job history** shows progress.

- ⏹️ **Stop:** `Ctrl+C` stops the API, web app and mock servers. PostgreSQL and Redis keep running;
  stop them with `docker compose stop postgres redis`.
- 🔀 **Another API port:** `API_PORT=8001 ./scripts/dev.sh` (the web app follows automatically).

### 4️⃣ Demo mode vs live mode

Set by `DATA_MODE` in `.env`.

| | 🧪 `demo` (default) | 📡 `live` |
|---|---|---|
| Market data | Local mock MCP servers, synthetic | Real NSE MCP and Kite MCP |
| Symbols | Fictional, start with `DEMO` | Every NSE-listed equity (`UNIVERSE_SCOPE=all`), or the reference list |
| Financial statements | Generated synthetic figures | Financial results filed with NSE (XBRL); Tickertape fills missing metrics |
| Concall transcripts | Two synthetic transcripts for `DEMOIT01` | Transcripts filed with NSE, or uploaded |
| Web research | Always off | `ddgs` by default |
| Labelling | Orange "Demo mode" banner on every page | None |

Demo and live data never mix: switching mode removes the other mode's instruments on the next
start. To keep both, use two databases.

<details>
<summary><b>🗺️ A quick tour of demo mode</b></summary>

| Page | What to check |
|---|---|
| Overview | Benchmark, sector table with rotation quadrants, top scores, watchlist highlights |
| Screener | Add a filter (ROE at least 15), sort a column, Export CSV |
| Themes | "Low realized volatility": members, methodology, exclusions, snapshot history |
| Instrument `DEMOAUTO01` → Price and provenance | Two recorded NSE-vs-broker disagreements of 2% |
| Instrument `DEMOIT03` → Price and provenance | A split under "Corporate actions applied" |
| Sector rotation | The rotation graph and its table |
| Research board → `DEMOIT02` → Generate research | Rule-based summary noting the model and web search were not used |
| Concall follow-through → `DEMOIT01` → Run analysis | Score with a verdict per commitment |
| Data status | Connector states, blocked broker tools, schedule, job history |

</details>

### 5️⃣ Live mode step by step

1. ✏️ In `.env` set `DATA_MODE=live`. The MCP URLs are already set:
   ```bash
   NSE_BHAVCOPY_MCP_URL=https://mcp.nseindia.in/bhavcopy/cm/mcp
   NSE_CM_MARKET_MCP_URL=https://mcp.nseindia.in/cmmkt/mcp
   BROKER_MCP_URL=https://mcp.kite.trade/mcp
   TICKERTAPE_MCP_URL=https://mcp.tickertape.in/mcp
   ```
2. 🗂️ Choose the instruments:
   - `UNIVERSE_SCOPE=all` (default) tracks **every NSE-listed equity** in `UNIVERSE_SERIES`
     (EQ and BE, about 2,580 stocks), from NSE's `EQUITY_L.csv`. Sectors come from NSE's industry
     classification. Prices for the whole market come from NSE's daily bhavcopy files; splits and
     bonuses are back-adjusted.
   - `UNIVERSE_SCOPE=reference` tracks only `backend/data/reference/nse_universe.csv`
     (`symbol,name,sector,industry,sector_kind,instrument_type,sector_index_symbol,isin`).
   - Financial statements are fetched over several refreshes, `NSE_FILINGS_MAX_COMPANIES_PER_RUN`
     (400) per run, companies without statements first.
3. 🔍 Optional check of the real servers with the app's own connectors:
   ```bash
   cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY && cd ..
   ```
4. ▶️ `./scripts/dev.sh`
5. 🔑 **Kite MCP login** (gap-filling and cross-checks): **Data status → Check broker session**, open
   the Kite link, approve read-only access, then check again. Kite expires the approval daily.
   Without it the app runs on NSE data alone and shows the broker as *"login required"*.
6. 🔑 **Tickertape (optional):** **Data status → Connect Tickertape** to fill fundamental metrics
   that NSE filings do not provide (read-only `market_data` scope).
7. 🔄 **Data status → Refresh all data.** The first full-market refresh downloads one bhavcopy file
   per trading day (three years ≈ 800 files, a few minutes); files are cached in
   `backend/var/nse_filings`, so later refreshes fetch only new days.

The NSE MCP servers are sometimes slow or return 502/504. Calls retry, then the source shows
*"unavailable"* with the reason; stored data keeps being served.

| Missing | On screen |
|---|---|
| Financial statements | Fundamentals and valuation *"Not available"*; scores incomplete or *"Insufficient data"* |
| Kite login | Broker *"login required"*; no gap filling or cross-checking |
| Language model | Research and concall analysis use rule-based fallbacks and say so |
| Web search off or rate-limited | No news or catalysts, with a notice that web research was unavailable |

> [!WARNING]
> NSE states that its MCP data is delayed and for informational and educational use, not commercial
> deployment or AI model training. Confirm NSE, broker, Tickertape and search-provider terms before
> any production or public use.

### 6️⃣ Optional integrations

Set in `.env`, then restart `./scripts/dev.sh`.

<details open>
<summary><b>🤖 Language model (any OpenAI-compatible provider)</b></summary>

The app calls `POST {LLM_BASE_URL}/chat/completions`; which provider is used is decided only by
these values. OpenRouter example:

```bash
LLM_ENABLED=true
LLM_PROVIDER_NAME=OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...                        # server-side only, never commit it
LLM_MODEL_ID=z-ai/glm-5.3-flash                 # exact id from https://openrouter.ai/models
LLM_EXTRA_HEADERS={"X-Title": "Markets Intelligence"}
LLM_EXTRA_BODY={"usage": {"include": true}, "reasoning": {"effort": "low", "exclude": true}}
LLM_RESPONSE_FORMAT=json_schema
LLM_VERIFY_MODEL_LIST=true
```

| Provider | `LLM_BASE_URL` | Notes |
|---|---|---|
| Azure OpenAI | `https://<resource>.cognitiveservices.azure.com/openai/v1` | `LLM_MODEL_ID=<deployment name>`, `LLM_RESPONSE_FORMAT=json_object`, `LLM_VERIFY_MODEL_LIST=false`, `LLM_MAX_TOKENS_PARAM=max_completion_tokens`, clear `LLM_EXTRA_BODY` and `LLM_EXTRA_HEADERS` |
| OpenAI | `https://api.openai.com/v1` | clear `LLM_EXTRA_BODY` |
| Groq | `https://api.groq.com/openai/v1` | clear `LLM_EXTRA_BODY` |
| Together | `https://api.together.xyz/v1` | clear `LLM_EXTRA_BODY` |
| Ollama (local) | `http://localhost:11434/v1` | `LLM_API_KEY=ollama`, `LLM_RESPONSE_FORMAT=json_object`, `LLM_VERIFY_MODEL_LIST=false` |

**Data status → Language model** shows the provider, whether the model id is in the provider's
model list (checked without spending credits), the provider's error if a call fails, and today's
calls, tokens and cost. `LLM_DAILY_BUDGET` and `LLM_DAILY_TOKEN_BUDGET` cap daily use. Without a
working model, research, concall analysis, Ask the market and the theme AI filter use rule-based
fallbacks and say so.

</details>

<details>
<summary><b>🔎 Web research</b></summary>

`WEB_SEARCH_PROVIDER=ddgs` (default, no key), `brave` with `WEB_SEARCH_API_KEY`, or `none`. Always
off in demo mode. `ddgs` uses public endpoints that rate-limit; a failed search is reported as
*"web research unavailable"*, never as *"no news"*.

</details>

<details>
<summary><b>🌍 Macro data</b></summary>

`FRED_API_KEY` (free from <https://fred.stlouisfed.org/docs/api/api_key.html>) adds US CPI, Fed
funds, unemployment and India 10Y. Any market-pulse URL left unset shows *"not configured"*.

</details>

<details>
<summary><b>🔐 Require a token for the API</b></summary>

`API_AUTH_TOKEN=<long random value>` (`openssl rand -hex 32`), then enter the same value under
**Data status → Access** in the web app.

</details>

<details>
<summary><b>📤 Upload statements or transcripts by hand</b></summary>

For a company whose filings are not available, upload a normalised statements file (format in
`docs/data-formats.md`); metrics are recomputed straight after:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/instruments/NSE:INFY/financials" \
  -H "Content-Type: application/json" --data @infy_statements.json
```

For a con-call that was not filed: **Concall follow-through → choose a company → Add a transcript**
(PDF, text file or pasted text).

</details>

### 7️⃣ Start each part separately

Use separate terminals when you want separate logs. Start in this order:

```bash
# 1. 🐘 Database (and Redis)
docker compose up -d --wait postgres redis

# 2. 📜 Migrations
cd backend && uv run python -m app.devtools.preflight migrate

# 3. 🧪 Demo mode only: mock MCP servers on 9100, 9101, 9102
cd backend && uv run python -m app.devtools.mock_mcp --port 9100
#    add --no-auto-login to make the mock broker require the login flow like real Kite

# 4. ⚙️ API (also runs the schedule)
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/ready        # {"status":"ready", ...}

# 4b. Or: API without the schedule + a separate worker (use Redis so they share locks)
cd backend && SCHEDULER_ENABLED=false uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
cd backend && uv run python -m app.workers.run

# 5. 🖥️ Web app
cd frontend && npm install && npm run dev -- --host 127.0.0.1
#    another API port: VITE_API_PROXY=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1
```

The API refuses to start against a database whose schema is missing or behind the code, and names
the command to run. Prefer `preflight migrate` over `alembic upgrade head`: it also repairs a
database whose tables were created without a migration record.

### 8️⃣ Run the whole stack in Docker

```bash
cp .env.example .env
docker compose --profile demo up -d --build     # 🧪 demo: includes the mock MCP servers
docker compose up -d --build                    # 📡 live: set DATA_MODE=live in .env first
```

| Service | What | Address |
|---|---|---|
| `postgres` | PostgreSQL 16, volume `pgdata` | 127.0.0.1:5432 |
| `redis` | Redis 7 (no persistence) | 127.0.0.1:6379 |
| `migrate` | Runs `alembic upgrade head` once, then exits | — |
| `api` | API, scheduler off | 127.0.0.1:8000 |
| `worker` | Runs the schedule | — |
| `web` | nginx serving the built app, proxying `/api` | <http://127.0.0.1:8080> |
| `mock-mcp` | Demo only (`--profile demo`) | internal |

```bash
docker compose logs -f api      # follow the API logs
docker compose down             # stop all, keep data
```

Stop the Compose stack before using `./scripts/dev.sh`, which also needs port 8000 (or run dev.sh
with `API_PORT=8001`).

### 9️⃣ Update the system

```bash
git pull
./scripts/dev.sh        # syncs Python packages, installs new npm packages, applies new migrations
```

<details>
<summary><b>🧱 Change the database schema</b></summary>

1. Edit the models in `backend/app/db/models.py`.
2. Generate a migration against an up-to-date database:
   ```bash
   cd backend
   uv run python -m app.devtools.preflight migrate
   uv run alembic revision --autogenerate -m "describe the change"
   ```
3. Review the new file in `backend/migrations/versions/`, then apply it with `preflight migrate`.
4. Confirm models and migrations agree: `uv run alembic check`.

</details>

<details>
<summary><b>📦 Change dependencies</b></summary>

```bash
cd backend && uv add <package>          # or: uv remove <package>; dev-only: uv add --dev <package>
../scripts/export-requirements.sh        # regenerate requirements*.txt from uv.lock
cd ../frontend && npm install <package>
```

</details>

### 🔟 Trigger jobs by hand

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all              # everything, in order
curl -X POST http://127.0.0.1:8000/api/v1/sector-rotation/refresh       # rotation only
curl -X POST http://127.0.0.1:8000/api/v1/themes/price-momentum/refresh # one theme
curl http://127.0.0.1:8000/api/v1/jobs/1                                # follow a job
```

Refresh triggers are limited to 3 per minute and research to 6 per minute by default. Only one run
of a job runs at a time; a second request is recorded as *"skipped"*. Theme snapshots already taken
are never changed.

### 🩹 Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose up` fails with *"Docker Desktop is manually paused"* | Whale menu → Resume |
| API exits with *"has no tables: no migration has been applied"* | `cd backend && uv run python -m app.devtools.preflight migrate` |
| API exits with *"schema is at migration X but this code expects Y"* | Same command: the code is newer than the database |
| *"password authentication failed for user markets"* | The volume was created with another password. Put the original in `.env`, or `docker compose exec postgres psql -U markets -d markets -c "ALTER USER markets PASSWORD 'new-password';"` (`docker compose down -v` also works but **deletes the database**) |
| API exits with *"Cannot reach the database"* | PostgreSQL is down or `DATABASE_URL` is wrong; `preflight db` shows the exact error |
| Data status shows a Redis cache warning | `REDIS_URL` is set but Redis is not running; start it or clear `REDIS_URL` |
| Every API call returns 401 | `API_AUTH_TOKEN` is set; enter it under Data status → Access, or clear it |
| *"No market data yet"* for a long time | The first refresh is still running or failed: Data status → Job history |
| NSE sources *"unavailable"* (502/504/timeout) | The NSE servers are intermittently down; stored data keeps being served |
| Broker *"login required"* | Data status → Check broker session → open the link → approve → check again |
| 12-month return or 1-year volatility missing | Fewer than 253 daily bars; use `HISTORY_YEARS=2` or more and refresh |
| Research says the model was not used | `LLM_ENABLED` off, missing key or model id, no credits (402), or daily budget reached |
| Ports 9100–9102 busy | An old mock server is running: `lsof -ti :9100,:9101,:9102 \| xargs kill`, or `MOCK_MCP_PORT=9200` |

Every error shown in the web app carries a request id you can search for in the API's JSON logs.

📘 Even more detail: [`analysis/HOW_TO_RUN.md`](analysis/HOW_TO_RUN.md)

---

## 🛠️ Commands

| | Task | Command |
|:-:|---|---|
| ▶️ | Start everything | `./scripts/dev.sh` |
| 📦 | Backend with pip instead of uv | `cd backend && pip install -r requirements.txt` |
| 🧪 | Backend tests | `cd backend && uv run pytest` |
| 🧹 | Backend lint and types | `cd backend && uv run ruff check . && uv run mypy app` |
| 📜 | Create / apply migrations | `uv run alembic revision --autogenerate -m "…"` / `uv run python -m app.devtools.preflight migrate` |
| 🐘 | Check the database connection | `cd backend && uv run python -m app.devtools.preflight db` |
| 🧪 | Frontend unit tests | `cd frontend && npm test` |
| 🧹 | Frontend lint, types, build | `cd frontend && npm run lint && npm run typecheck && npm run build` |
| 🎭 | End-to-end tests (Playwright) | `cd frontend && npm run e2e` |
| 🔗 | Integration tests on PostgreSQL + Redis | `TEST_DATABASE_URL=postgresql+psycopg://… TEST_REDIS_URL=redis://… uv run pytest tests/api tests/services` |
| 🐳 | Smoke-test the built images | `./scripts/smoke-images.sh` |

---

## ⚙️ Configuration

All settings live in `.env` (template: `.env.example`, placeholders only — 🔐 never commit `.env`).

| Setting | Default | Meaning |
|---|---|---|
| `DATA_MODE` | `demo` | `demo` (mock MCP, synthetic) or `live` (real MCP servers) |
| `DATABASE_URL` | local Compose PostgreSQL | PostgreSQL through psycopg 3 |
| `REDIS_URL` | blank | Blank = in-process cache; needed when API and worker run separately |
| `API_AUTH_TOKEN` | blank | When set, every `/api/v1` call needs `Authorization: Bearer <token>` |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` | off | Any OpenAI-compatible provider |
| `LLM_DAILY_BUDGET`, `LLM_DAILY_TOKEN_BUDGET` | 200 calls, 2M tokens | 💰 Daily usage caps |
| `WEB_SEARCH_PROVIDER` | `ddgs` | `ddgs`, `brave` or `none` |
| `UNIVERSE_SCOPE` | `all` | `all` NSE equities or the `reference` list |
| `BENCHMARK_SYMBOL` | `NIFTY 50` | Rotation and relative-strength benchmark |

⏰ **Schedules** (cron, in `DEFAULT_MARKET_TIMEZONE`):

| Job | Setting | Default |
|---|---|---|
| 🎯 Theme snapshots | `THEME_REFRESH_CRON` | Monthly, 1st at 06:30 |
| 🔄 Sector rotation | `RRG_REFRESH_CRON` | Every 12 hours |
| 📈 Prices + fundamentals | `PRICE_REFRESH_CRON` | Weekdays 19:30 |
| 🗂️ Instrument master | `INSTRUMENT_REFRESH_CRON` | Weekdays 07:00 |
| 🏦 FII / DII flows | `FII_DII_CRON` | Weekdays 19:30 and 21:30 |
| 🔔 Alerts | `ALERTS_CRON` | Every 15 minutes |

📘 Full reference: [`analysis/HOW_TO_RUN.md` §15](analysis/HOW_TO_RUN.md#15-configuration-reference)

---

## ☁️ Deployment

```
git push → dev   →   🧪 tests   →   📜 migration checks   →   🔗 PG + Redis integration   →   🔍 audits
                 →   🐳 build + smoke-test images   →   ☁️ Azure Container Apps   →   🩺 health check   →   ↩️ auto-rollback on failure
```

- ⚙️ Workflow: [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
- 🔧 One-time setup: [`set-github.md`](set-github.md) · [`infra/azure/CICD.md`](infra/azure/CICD.md)
- 🌩️ AWS and Google Cloud recipes: [`analysis/RUN_AND_DEPLOY.md`](analysis/RUN_AND_DEPLOY.md)

---

## ✅ Verification status

| Area | Status |
|---|---|
| 🧪 Backend tests | ✅ 314 passing, 3 skipped, 85% line coverage on 2026-10-15. Tests added since are not counted here |
| 🖥️ Frontend | ✅ 14 component tests + 29 browser end-to-end tests (desktop and mobile) on 2026-10-15 |
| 📡 NSE MCP, real servers | ✅ Discovery, quotes, paged history, index history, corporate actions, full pipeline run (2026-10-12) |
| 🥈 Kite MCP, real server | 🟡 Discovery, allowlist, instrument lookup and login prompt verified; logged-in quotes/history covered by mock contract tests |
| 🔎 Web search (`ddgs`) | ✅ Live research for INFY and RELIANCE with retrieved sources (2026-10-12) |
| 🤖 LLM | 🟡 OpenRouter model-list check and errors verified against the real API; Azure OpenAI integrated; model output covered by mocked contract tests |
| 🐘 PostgreSQL · Redis · Docker Compose | ✅ Migrations, integration tests, worker and the full Compose stack (2026-10-15) |
| 🔐 Dependency audits | ✅ `pip-audit` and `npm audit --omit=dev`: no known vulnerabilities (2026-10-12) |

## ⚠️ Known limitations

- 👤 Single-tenant: one optional bearer token (`API_AUTH_TOKEN`), no user accounts or roles.
- 💹 Prices are adjusted for splits and bonuses only, not dividends (not total return).
- 🎯 No entry zone or target price — there is no backtested method or backtesting module.
- 🧾 Themes needing analyst estimates, governance data or commodity exposure are listed as unavailable.
- 🔔 Alerts are in-app only; no email or push delivery.
- 🌍 The macro chart source is an unofficial, delayed endpoint — check its terms before production use.
- 🧮 Screener filtering runs in memory over the latest snapshots (fine for a few thousand instruments); saved screens live in the browser.
- 🛢️ Commodities: the schema has contract metadata, but no commodity data is ingested.

---

<div align="center">

📚 **Learn more** —
[Repository guide](analysis/REPOSITORY_GUIDE.md) ·
[Architecture](analysis/ARCHITECTURE.md) ·
[Methodology](docs/methodology.md) ·
[Operations](docs/operations.md) ·
[Plan](PLAN.md)

<sub>Built for research and education. Not investment advice. 🇮🇳</sub>

</div>
