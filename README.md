# Markets Intelligence

Explainable research and screening for NSE-listed instruments: a screener, rule-based themes with
monthly snapshots, a sector rotation graph, an evidence-grounded research board, an entry-setup
checklist, a concall follow-through analyzer, watchlists and alerts.

It is a research tool. It cannot place orders, and nothing it shows is investment advice or a
prediction of returns.

## What it does with data

- **NSE MCP is the primary source** (end-of-day history, corporate actions, delayed quotes, index
  history). **Zerodha Kite MCP fills gaps** and is used to cross-check. When both report a value and
  differ beyond a tolerance, the NSE value is kept and the disagreement is stored and shown.
- Tool names and argument names are **read from each server at start-up**. A capability with no
  advertised tool is reported as not supported; nothing is assumed.
- Only read-only market-data tools of the broker are callable. Order, GTT, holdings, positions and
  margin tools are refused in code before any request is sent.
- Every number carries its source, timestamp and freshness. A value that cannot be computed is
  shown as missing with the reason, never as zero.
- All arithmetic is deterministic and versioned. The language model only writes summaries from
  supplied evidence and classifies concall statements; its output is schema-validated and
  grounding-checked.

## Quick start (demo mode, no credentials)

Requirements: [uv](https://docs.astral.sh/uv/), Node 22+, and PostgreSQL — easiest through Docker
Desktop (running, not paused), which the start script uses automatically.

```bash
cp .env.example .env          # DATA_MODE=demo is the default
./scripts/dev.sh              # PostgreSQL (Docker) + migrations + mock MCP servers + API + web app
```

Full instructions, including Homebrew PostgreSQL and troubleshooting: `analysis/HOW_TO_RUN.md`.
What every feature does: `analysis/REPOSITORY_GUIDE.md`. Architecture: `analysis/ARCHITECTURE.md`.

Open <http://127.0.0.1:5173>. The first start ingests and scores the demo universe (about 15
seconds). Demo mode runs the real connector code against local mock MCP servers that serve
**synthetic** data; every screen carries a demo banner and the symbols are fictional (`DEMO…`).

## Live mode

1. In `.env` set `DATA_MODE=live`. The NSE and Kite MCP URLs are already filled in.
2. `./scripts/dev.sh`
3. Open **Data status**, choose **Check broker session**, open the Kite link and approve read-only
   access, then **Refresh all data**. Without the broker login the app still runs on NSE data alone.
4. Optional:
   - **Fundamentals**: neither NSE MCP nor Kite provides financial statements. Upload a normalised
     statements file per company (`PUT /api/v1/instruments/{ref}/financials`, format in
     `docs/data-formats.md`). Until then fundamentals and valuation are shown as missing and scores
     are marked incomplete.
   - **Language model**: set `LLM_ENABLED=true`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` for any OpenAI-compatible provider (OpenRouter shown in `.env.example`). Switching provider means changing only these values.
   - **Web research**: on by default in live mode through `ddgs` (DuckDuckGo-backed, no API key).
     Set `WEB_SEARCH_PROVIDER=brave` with `WEB_SEARCH_API_KEY` to use Brave instead, or `none` to turn
     it off. It is always off in demo mode, because demo symbols are fictional.
5. `UNIVERSE_SCOPE=all` tracks every NSE-listed equity (prices from NSE's daily bhavcopy files); set it to
   `reference` to track only the rows of `backend/data/reference/nse_universe.csv`.

Check what the real servers advertise and whether the adapters can use them:

```bash
cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY
```

NSE states that its MCP data is delayed and for informational and educational use, not commercial
deployment or AI model training. Confirm NSE, broker and search-provider terms before any
production or public use.

## Commands

| Task | Command |
|---|---|
| Start everything | `./scripts/dev.sh` |
| Install backend with pip instead of uv | `cd backend && pip install -r requirements.txt` |
| Backend tests | `cd backend && uv run pytest` |
| Backend lint and types | `cd backend && uv run ruff check . && uv run mypy app` |
| Create / apply migrations | `cd backend && uv run alembic revision --autogenerate -m "…"` / `uv run alembic upgrade head` |
| Frontend unit tests | `cd frontend && npm test` |
| Frontend type check and build | `cd frontend && npm run typecheck && npm run build` |
| End-to-end tests (starts the demo stack if needed) | `cd frontend && npm run e2e` |
| Integration tests on PostgreSQL and Redis | `TEST_DATABASE_URL=postgresql+psycopg://… TEST_REDIS_URL=redis://… uv run pytest tests/api tests/services` (empty database, migrated with Alembic first) |
| Full stack in containers | `docker compose --profile demo up --build` |

API reference: <http://127.0.0.1:8000/docs> while the API is running.

## Layout

```
backend/app/connectors   MCP client, NSE MCP and Kite MCP adapters, fundamentals files, search, LLM
backend/app/domain       pure deterministic maths: ratios, valuation, technicals, risk, scoring, themes, RRG
backend/app/services     ingestion, correlation, metrics, themes, rotation, research, concall, alerts, jobs
backend/app/api/v1       REST API
backend/app/workers      scheduler
backend/app/devtools     demo-only mock MCP servers and synthetic data
frontend/src             React app
infra/                   Dockerfiles, nginx, alert rules
docs/                    architecture, methodology, data formats, operations
```

## Verification status

| Area | Status |
|---|---|
| Backend unit, contract, integration, security and scheduler tests | 314 passing, 3 skipped, 85% line coverage (`uv run pytest --cov=app`) on 2026-10-15 |
| Frontend component tests | 14 passing |
| Browser end-to-end tests (demo stack, desktop and mobile) | 29 passing on 2026-10-15; screenshots in `analysis/proof/`. One opt-in live check (`E2E_LIVE=1`) passes against real NSE data |
| NSE MCP, real servers | Verified on 2026-10-12 with the application's connectors: tool discovery, quotes, paged history, index history, corporate actions, and a full pipeline run on 12 stocks |
| Kite MCP, real server | Tool discovery, allowlist and instrument lookup verified. Quotes and history need an account login and were **not** verified against the real server; they are covered by mock contract tests only |
| Web search (`ddgs`) | Verified on 2026-10-12: research generated through the API for INFY and RELIANCE in live mode with real NSE quotes and retrieved sources; unit tests use a stand-in client |
| LLM through OpenRouter (configured in `.env`) | Verified on 2026-10-15 against the real OpenRouter API: the model id `z-ai/glm-5.3-flash` is in the live model list, a misspelt id is rejected before any paid call, and an invalid key is reported as HTTP 401. No key was configured, so no completion was produced; behaviour with real model output is covered by mocked tests only |
| PostgreSQL and Redis | Verified on 2026-10-12 against PostgreSQL 16.2 and Redis 6.2: the Alembic migration applies with no drift, 82 integration tests pass on the migrated schema with Redis as cache and lock store, and the standalone worker ran a scheduled job |
| Dependency audits | `pip-audit` and `npm audit --omit=dev`: no known vulnerabilities on 2026-10-12 |
| Docker Compose, Dockerfiles, nginx | Written but **not run**: Docker was not available on the authoring machine |
| CI workflow | Never run on a GitHub runner. Each of its steps was run locally and passes |

## Known limitations

- Single-tenant: one optional bearer token (`API_AUTH_TOKEN`), no user accounts or roles.
- Fundamentals need uploaded files; there is no filings connector. Valuation uses the latest
  annual period; quarterly and trailing-twelve-month valuation are not computed.
- Sector labels come from a manually maintained reference file, not from the exchange.
- Prices are adjusted for splits and bonuses only, not for dividends (not total return).
- No entry zone or target price is produced: there is no backtested method, and no backtesting
  module exists.
- Themes needing analyst estimates, governance data, a corporate-actions calendar or commodity
  exposure are listed as unavailable.
- Alerts are recorded in the app only; no email or push delivery.
- The rotation benchmark is the configured index; it is not selectable in the UI.
- Screener filtering runs in memory over the latest snapshots, which suits a universe of a few
  thousand instruments. Saved screens live in the browser.
- Commodities: the instrument schema has contract metadata, but no commodity data is ingested.
- CSV export uses a plain link and does not send the bearer token.

More detail: `docs/architecture.md`, `docs/methodology.md`, `docs/operations.md`, `PLAN.md`.
