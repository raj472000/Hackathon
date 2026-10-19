# PLAN

Design: `docs/superpowers/specs/2026-10-12-capital-markets-intelligence-design.md`
Details: `README.md`, `docs/architecture.md`, `docs/methodology.md`, `docs/operations.md`

## Status (2026-10-12)

| Phase | Scope | Status |
|---|---|---|
| 1 | Analysis, MCP discovery, design | Done |
| 2 | Foundation: structure, config, database, migration, health, logging, CI file | Done. Compose/PostgreSQL/Redis written, not run (no Docker here) |
| 3 | Connectors, correlation, data quality | Done. NSE MCP verified live. Kite quotes/history not verified live (needs login) |
| 4 | Deterministic metrics, scoring, screener | Done |
| 5 | Themes, DSL, monthly snapshots | Done (70 built-in definitions, 5 flagged unavailable) |
| 6 | Sector rotation / RRG | Done |
| 7 | LLM research, web evidence, concall analyzer | Done with mocked LLM/search; rule-based fallbacks verified. No real inference call made |
| 8 | Frontend | Done (9 pages) |
| 9 | Verification, documentation | Done for what can run on this machine; see "Not verified" |

## Changes and results, 2026-10-15

Requested: PostgreSQL with psycopg; Kite through its MCP server only (no Kite Connect REST); fix
start-up errors; documentation under `analysis/`.

- Database: PostgreSQL via psycopg 3 is the default; asyncpg removed; `postgres://`, `postgresql://`
  and `+asyncpg` URLs rewritten to `postgresql+psycopg://` (`?ssl=` → `?sslmode=`).
- Kite Connect REST adapter, settings, route and tests removed; precedence is NSE MCP, then Kite MCP.
- Start-up fixes, each with a test:
  - `.env` values with trailing `# notes` (passed through by Docker Compose) or stray spaces broke settings;
  - `AUTO_CREATE_SCHEMA=true` left tables without a migration record, so `alembic upgrade head` then
    failed with "already exists" (reproduced on PostgreSQL); auto-created schemas are now recorded and
    `preflight migrate` adopts unrecorded ones;
  - the API now refuses an unmigrated or out-of-date database with the command to run;
  - a paused Docker Desktop holding port 8000 made the API die with only "address already in use";
    `scripts/dev.sh` now checks ports and names the holder and the fix;
  - jobs left "running" by a stopped process are marked interrupted at start-up.
- `scripts/dev.sh` now starts PostgreSQL/Redis with Docker Compose when local and down, applies
  migrations, then starts the app.
- Results: backend 283 passed + 3 Redis-only (284 verified earlier on PostgreSQL 16 + Redis 6.2 through psycopg); lint
  and types clean; frontend 14 passed, builds; browser tests 13 passed against a PostgreSQL-backed
  stack; live mode on PostgreSQL started with real NSE MCP (21 + 15 tools) and returned a real INFY quote.
- Docker Compose verified (Docker Desktop started): images build, `migrate` exits 0, all services healthy,
  live mode reachable through nginx :8080; `scripts/dev.sh` verified against the Compose PostgreSQL/Redis
  with the user's `.env` (real TCS quote from NSE MCP).
- Found and fixed during that run: the Compose default database password had been changed to
  `markets`, but an existing volume keeps its original password (`change-me-locally`), so `migrate`
  failed with "password authentication failed" — `change-me-locally` restored as the single default
  and a pre-flight hint added; Kite's generic pre-login errors now read "login required" with a link;
  NSE index history while NSE is unreachable now reads "unavailable", not "not supported".
- Not verified: Kite quotes/history (needs the account holder's login); real model output (HTTP 402);
  CI on a GitHub runner.
- Documentation: `analysis/HOW_TO_RUN.md` (rewritten), `analysis/REPOSITORY_GUIDE.md` (new),
  `analysis/ARCHITECTURE.md` (new), `analysis/RUN_AND_DEPLOY.md` (cloud deployment, updated).

## Test results (fresh run, 2026-10-12)

- `cd backend && uv run pytest` → 271 passed, 3 skipped (Redis-only tests), 86% line coverage
- PostgreSQL 16.2 + Redis 6.2 (throwaway servers): `alembic upgrade head` and `alembic check` clean;
  `pytest tests/api tests/services` → 82 passed on the migrated schema; standalone worker ran a cron job
- `pip-audit` and `npm audit --omit=dev` → no known vulnerabilities
- Live research through the API for INFY and RELIANCE: real NSE quotes plus `ddgs` sources, all citations grounded
- `uv run ruff check .` → clean; `uv run mypy app` → clean (49 `type: ignore` comments cover typing gaps that were not resolved)
- `alembic upgrade head` + `alembic check` on PostgreSQL 16 through psycopg → applies, no drift
- `cd frontend && npm run typecheck && npm test && npm run build` → clean, 14 passed, builds
- `npm run e2e` from a clean database → 13 passed (12 desktop, 1 mobile)
- Live: `scripts/check_connectors.py --symbol INFY` and a full pipeline run on 12 NSE stocks + Nifty 50
  against the real NSE MCP servers → succeeded (3,263 bars, real quotes, corporate actions)

## Decisions taken without a user answer

- Broker: Zerodha Kite MCP only (the Kite Connect REST path was removed on 2026-10-15 at the user's request).
- Database: PostgreSQL through psycopg 3 (switched from SQLite/asyncpg on 2026-10-15 at the user's request);
  Redis optional. SQLite remains only as the fast default for the unit test suite.
- No LLM or search credentials: both integrations built with mocked contract tests and honest
  "unavailable" states; rule-based fallbacks for research and concall analysis.
- Demo mode: same connector code against local mock MCP servers, synthetic data, fictional symbols.
- Fundamentals: file/upload provider, because no configured source supplies statements.
- Single-tenant auth with an optional bearer token.
- Partial score shown (labelled) when at least three of five pillars have data.
- Same-month theme refresh creates a new revision; earlier snapshots are never changed.
- No entry zone or target price, because there is no backtested method.

## What live discovery changed

The real NSE servers advertise more than public write-ups listed (21 + 15 tools). Adapters were
updated to the recorded schemas: `months`/`endDate` paging, required-but-defaultable arguments,
errors returned as `{"error": …}` payloads, quotes nested under `stock`/`index`, index history and
index quotes from NSE, Kite lookup by exact `id`. Recorded schemas live in
`backend/tests/fixtures/live/` and drive `tests/connectors/test_real_contracts.py`.

## Not verified

- Kite MCP quotes and history against the real server (needs the account holder's login).
- Brave search (no key). Web search through `ddgs` is verified.
- Real GLM output: the LLM client is provider-neutral (any OpenAI-compatible API, configured in `.env`).
  OpenRouter's route and model id `z-ai/glm-5.3-flash` are confirmed, but no `LLM_API_KEY` is configured,
  so no real completion has been seen.
- Docker Compose, the Dockerfiles and the nginx config (Docker not installed). PostgreSQL and Redis
  themselves are verified.
- CI workflow on a GitHub runner (each step passes locally).
- NSE split/bonus handling on a real split: parsing is tested on exchange-style purpose strings
  and the `adjustmentFactor` field the real tool returns is not used.

## Not implemented

- User accounts and roles; external alert delivery; backtesting; commodity data ingestion;
  quarterly/TTM valuation; point-in-time valuation history; UI benchmark selection for rotation;
  OpenTelemetry tracing (Prometheus metrics and structured logs only); a filings connector.

## Next steps

1. Log in to Kite from Data status and run a live refresh; confirm quote/history shapes, then
   record them as fixtures.
2. Add `LLM_API_KEY` (and adjust `LLM_BASE_URL`/`LLM_MODEL_ID` if not using OpenRouter) and verify the model id on Data status; run research and a concall
   analysis with the model.
3. Supply statement files for the tracked universe.
4. Install Docker and run `docker compose --profile demo up --build`; apply the migration to PostgreSQL.
