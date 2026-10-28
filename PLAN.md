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

## Changes and results, 2026-10-26: market pulse

Requested: Accellor title suffix; stocks-in-the-news module with move since the news (news left, the day's
stocks right, headline click shows the short description); macro section; FII/DII summary with past data;
Nifty 50 / Sensex / Nifty Bank at the top of Overview; OI and top-movers section.

- Title: tab title "Markets Intelligence · An Accellor product"; sidebar subtitle "An Accellor product".
- New pages (Markets group): Market news, Macro, FII / DII flows, F&O and open interest. Overview has an index strip.
- New routes: `/api/v1/markets/indices`, `/markets/news`, `/macro`, `/flows/fii-dii`, `/flows/participant-oi`,
  `/derivatives/movers`, `/derivatives/oi`. Code: `api/v1/pulse.py`, `services/{news,macro,flows,derivatives}.py`,
  `connectors/macro.py`, `domain/derivatives.py`.
- Sources (all verified live 2026-10-26): NSE corporate announcements (market-wide), NSE `fiidiiTradeReact`,
  NSE F&O bhavcopy and participant-OI archive files, NSE CM MCP (`cm_get_index_quote`, `nse_get_market_movers`,
  `cm_get_stock_quote`, `cm_get_equity_stocks`), bhavcopy MCP `get_ltp_by_date`, NSE `allIndices` (index fallback),
  MOSPI CPI API, a v8 chart endpoint for crude/DXY/USD-INR/US yields/global indices/Sensex (unofficial; terms to
  confirm), FRED (optional `FRED_API_KEY`).
- Decisions: Sensex comes from the chart feed (no NSE source publishes the BSE index). NSE serves FII/DII cash
  figures for the latest day only, so they are stored daily (`fii_dii_flows`, migration `b7c1d2e3f4a5`, job
  `fii_dii` at 19:30 and 21:30 IST on weekdays); history starts with the first recording and is not backfilled. Past derivatives
  positioning is available for any date from NSE's participant-OI archive. OI buildup per underlying sums OI
  across expiries (a roll is not counted as unwinding); price change is the near month's settlement vs previous close.
  MOSPI's TLS needs legacy renegotiation, allowed for that client only (certificates still verified).
- Fixed on the way: scheduler crons used APScheduler's Monday-0 weekday numbering, so `1-5` ran Tuesday to
  Saturday (price refresh missed Monday and ran Saturday). Crons are now read with standard cron numbering.
- Not available / limits: MOSPI's API latest CPI month is Dec 2025 (shown as stale); FRED rows need a key; during
  NSE CM MCP outages (seen several times on 2026-10-26, HTTP 504) news moves show "NSE quotes are unavailable",
  gainers/losers fall back to end-of-day F&O, indices fall back to NSE's index feed.
- Results: backend 427 passed, 3 skipped (Redis-only); ruff and mypy clean; `alembic upgrade head` + `alembic check`
  clean on PostgreSQL; frontend typecheck clean, 18 tests passed, builds; e2e against an isolated demo stack 35 passed,
  2 failed in the full run and passed when rerun alone (refresh rate-limit / timing). The spec `fixes.spec.ts`
  now reads ▼ as a negative value.

### Follow-up, 2026-10-26: "none of the new features are working"

- Cause: the running `scripts/dev.sh` API had been started before the changes and ran uvicorn without reload, so
  every new route answered 404 through the web app. The code was fine; the running process was stale.
- Fix: `scripts/dev.sh` now starts the API with `--reload` (watching `backend/app`; `DEV_RELOAD=false` turns it off)
  and takes `WEB_PORT` (default 5173). Verified: the updated script on API 8001 / web 5174 served all seven new
  routes with 200 while the stale stack on 5173 still answered 404, and an edit to `services/news.py` was picked up
  with "WatchFiles detected changes ... Reloading". Screenshots of each page in `.playwright-mcp/proof-*.png` (local).
- Also: news rows priced only at end of day now say the live price is unavailable instead of "no session since".

### Follow-up, 2026-10-26: Kite fallback, macro reading, FII/DII history

- Zerodha Kite MCP (read-only `get_quotes`) is now the fallback whenever NSE's MCP fails: news prices (NSE live →
  Kite live → NSE end of day), Nifty / Nifty Bank / India VIX (NSE → Kite → NSE index feed), Sensex (Kite → chart feed),
  top gainers/losers (ranked from Kite quotes over the segment's constituents or the F&O stocks).
- F&O: until NSE publishes today's F&O file, the OI page is live from Kite (current OI and price of every futures
  contract) against the last published file; past dates and Kite failures use the end-of-day file, labelled.
- Macro: a rule-based reading at the top (Risky / Neutral / Safe / Insufficient data) from 11 signals with fixed,
  displayed thresholds (`macro-regime-v1`); stale indicators are skipped.
- FII/DII history: official NSDL FPI daily figures from NSDL's public archive (table `fpi_daily`, migration
  `c3d4e5f6a7b8`), merged with NSE's recorded provisional FII/DII. Page: presets, custom from/to dates and a
  "Refresh historical data" button (`POST /api/v1/flows/history/refresh`). No public source has past DII figures;
  DII history grows from the daily recording. The evening job also refreshes the current NSDL month.
- Results: backend 439 passed, 3 skipped; ruff, mypy, `alembic check` clean on PostgreSQL; frontend 18 passed, builds.
  Live: Kite priced the news stocks with NSE forced down; history refresh stored 138 NSDL days (Apr–Oct 2026).

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

## Chat assistant (2026-10-26) — done
- Floating icon bottom-right on every page (`frontend/src/components/ChatAssistant.tsx`, mounted in `Layout`); history kept per browser session.
- `POST /api/v1/assistant/chat` (`backend/app/services/assistant.py`): uses the existing server-side `LLM_*` provider (OpenRouter, `z-ai/glm-5.3-flash`) with a validated `AssistantReply`; the model may only pick a page id from the catalog, and stocks/themes are resolved against the database before a route is returned. Keyword fallback when the model is unavailable. Own rate limit `RATE_LIMIT_CHAT_PER_MINUTE` (default 20); every chat is audited.
- The assistant does not answer market facts from model memory; it points to the page with sourced data.
- Tests: `backend/tests/services/test_assistant.py` (15, mocked LLM), `frontend/e2e/assistant.spec.ts` (desktop + mobile).

## Market news fixes (2026-10-26) — done
- Cause of the empty "Today" page: NSE announcements failed with intermittent TCP ConnectTimeout (NAT64/IPv6 network); the service returned an empty list and cached it as a valid result.
- Fixes: last good NSE/web items are kept in the cache (Redis, 7 days) and served marked `stale` with their retrieval time during an outage; outage/stale results are never cached; NSE filings connect timeout 8s with one retry of a failed connect.
- `GET /api/v1/markets/news?refresh=true` skips the response cache and the NSE disk cache; the page's "Re-fetch all news" button uses it.
- The page polls every 2 minutes at all hours, including in background tabs (response cache and announcement TTL cut to 60s so each poll is fresh).
- Tests: `test_news_outage_keeps_last_good_items_marked_stale_and_is_not_cached`, `test_a_stuck_connect_is_retried_once_before_counting_as_an_outage`, `frontend/e2e/news-refetch.spec.ts`.

## Sector rotation readability (2026-10-26) — done
- Cause of the long, jumpy tails: `rrg-v1` used raw weekly RS with no smoothing, so one noisy week moved a sector 5–10 points.
- `rrg-v2` (`backend/app/domain/rrg.py`): trailing EMA (span `smoothing=4` weeks) on RS before RS-Ratio and on RS-Momentum. On stored NSE data the median weekly move fell from 1.93 to 0.72 points and the largest from 10.4 to 4.5. `smoothing=0` reproduces v1 exactly; `GET /sector-rotation?smoothing=0` shows it. Older v1 snapshots are kept.
- Chart (`frontend/src/components/Charts.tsx`): curved, tapering tails coloured by current quadrant; collision-free labels with leader lines; axes fit the data; quadrant roster beside the chart with linked highlighting; week-by-week replay; 2-week default tail. Data notes and sector chips are collapsed on the page.
- Tests: `test_rrg_v2_smoothing_*` (domain), rotation e2e in `app.spec.ts`/`fixes.spec.ts`/`live-proof.spec.ts`.

## Watchlists and alerts split (2026-10-26) — done
- `/watchlists` (`/watchlists/:id`): list switcher, weekly breadth line, sortable table with per-column diverging move bars, score meters, rename (`PATCH /api/v1/watchlists/{id}`), per-row "Set alert".
- `/alerts`: sentence-style builder (prefilled from a watchlist row via `?instrument=&id=`), alert list with pause switches and filter, day-grouped notification history.
- Assistant routes "alerts" and "watchlists" to separate pages.

## Tickertape fallback for missing fundamentals (2026-10-26) — done
- `backend/app/services/provider_ratios.py`: resolves SIDs (`in_resolve_sid_read`, batches halved on 404 to isolate unlisted tickers), fetches ratios with `in_screen_stocks_read` (10 SIDs per call), stores raw + mapped values in `provider_ratios` (migration `d4e5f6a7b8c9`).
- Precedence: NSE-based values always win. Tickertape fills only `missing`; never `not_meaningful` or `not_applicable`; bank/NBFC rules apply; non-positive Tickertape multiples become `not_meaningful`. Each filled metric records provider, field, SID and retrieval time; the screener marks such cells "TT".
- Runs after each price refresh, after Tickertape login, and on `POST /api/v1/sources/tickertape/backfill`.
- Result on live data (2,591 stocks): stocks missing P/E, ROE or D/E 888 → 581; truly missing P/E 336 → 65, P/B 342 → 4, ROE 342 → 13, D/E 430 → 9, market cap 341 → 11. Remaining blanks are mostly negative-earnings P/E (`not_meaningful`) and bank D/E (`not_applicable`). 3 tickers are not listed on Tickertape.
- Known data issue (not changed): AARON shows P/B ≈ 532,278x from NSE-based figures; needs a units check on its filings.
- Tests: `backend/tests/services/test_provider_ratios.py` (7).
- Follow-up: screener exclusions now give a precise reason per instrument, with example symbols (`missing_reason` in `backend/app/services/screener.py`): listed recently (one financial year on NSE and Tickertape), prior-year figure zero/negative, no statements anywhere, rights entitlements (`-RE`), stopped trading (no price for 30+ days, e.g. LTIM). The note is a collapsed, neutral disclosure. For "Revenue growth YoY ≥ 2" the remaining 21 exclusions all fall in these groups; none can be filled from Tickertape (checked against its income statements).
- E2E refresh tests now wait for the first load before clicking Refresh (on the live universe the click raced the in-flight load).

## Frontend enhancement via Figma (2026-10-26) — done
- Figma file "Markets Intelligence — UI Enhancement" (https://www.figma.com/design/yJjsnDxzrRI3FEY44x0m6V): `MI / color` variable collection (Light only; the Starter plan allows one mode, dark values stay in `frontend/src/index.css`), 16 `icon/*` components, `NavItem`, `StatTile`, `MoverRow`, and an enhanced Overview screen (illustrative DEMO values only).
- Code: `frontend/src/components/Icons.tsx` (same SVG paths as Figma); sidebar icons and uppercase group labels; search icon and `/` shortcut; `StatTile`, `QuadrantChip`, `DivergingBar`, `MagnitudeBar` in `ui.tsx`; Overview Universe tiles (breadth split meter), sector table with quadrant chips and 1-month diverging bars, ranked leaderboards with magnitude bars.
- Fixes: Nifty 50 ticker's hidden labels widened the page (track was not positioned); leaderboard grid overflowed on phones.

## Design pass 2: "market desk" (2026-10-27) — done
- Direction (also in the Figma file as "v2 / Market desk hero"): the NSE trading session is the signature element. Overview opens with an IST date, session state, a 09:00–16:00 session band with a marigold "now" marker, the three headline indices with source/as-of, and the Nifty 50 tape.
- `frontend/src/lib/session.ts` (+ `src/test/session.test.ts`, 6 tests): session phase from the clock in IST. Exchange holidays are not known; the UI says so under the band and in the top-bar tooltip.
- Every page: top-bar session indicator (`SessionChip`), sentence-case sidebar groups, larger page titles, 12 px panel radius.
- Overview: `MarketDesk` replaces the separate ticker, page header and index cards (`IndexStrip` kept for its tests); "Ask the market" is a light panel; sector table shows 8 rows with a "Show all" control.
- Instrument page: two-row header (classification + refresh, then symbol vs price with provenance).
- Not verified: e2e suite in demo mode (the running dev server is live mode, which the demo-mode specs do not expect).

## CI/CD to Azure on push to main (2026-10-28) — built, not yet live
- `.github/workflows/ci.yml`: new `images` job (build api + web for linux/amd64, smoke-test together with PostgreSQL via `scripts/smoke-images.sh`, push to ACR tagged with the commit SHA on `main`) and `deploy` job (needs backend, frontend, e2e, images; GitHub environment `production`; OIDC sign-in, no stored secrets).
- `infra/azure/deploy.sh`: changes only the container images of the existing app as one revision, waits for Healthy, smoke-tests the public URL, rolls back to the previous images on failure. `DRY_RUN=1` previews.
- `infra/azure/setup-github-deploy.sh`: one-time identity `id-markets-github`, federated credentials (main, environment production), AcrPush/Reader on ACR, Contributor on the app and its environment, GitHub environment + variables. Docs: `infra/azure/CICD.md`; manual setup guide `set-github.md` (the user runs the setup).
- CI was red on `main` because of the dependency audit: certifi 2026.7.22 → 2026.10.23, pypdf 6.20.0 → 6.21.3 (floor raised in `pyproject.toml`). `.dockerignore` now excludes `.env.*` (kept `.env.azure` out of build contexts).
- Verified: ruff, mypy, 499 backend tests; shellcheck and actionlint clean; images build and pass the smoke test locally; `deploy.sh` dry run against the live app (only images change) and its input validation.
- Verified live on a throwaway app `markets-cicd-test` (same environment, deleted afterwards): good rollout of both containers as one revision (command/env preserved); nonexistent image tag detected in 60 s and rolled back (this test led to the fail-fast check on the app's provisioning state); crash-on-start rolled back after the wait limit.
- Not verified: the workflow on GitHub runners, the OIDC sign-in, a rollout of the real `markets` app. These wait on the one-time setup (`set-github.md`, done by the user) and on the live app being healthy (its API fails start-up with `DATABASE_URL` not set).
