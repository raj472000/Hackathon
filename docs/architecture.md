# Architecture

A modular monolith plus a scheduler. One codebase, two process roles (`api`, `worker`).

```
                    ┌──────────── web (React, Vite) ────────────┐
                    │  talks only to /api/v1 on the same origin  │
                    └───────────────────┬────────────────────────┘
                                        │
        ┌───────────────────────── api (FastAPI) ─────────────────────────┐
        │ auth · validation · rate limits · request ids · error schema     │
        ├──────────────────────────── services ────────────────────────────┤
        │ ingestion → market_data (correlation) → metrics → themes / RRG   │
        │ research · concall · alerts · jobs (locks, run history, audit)   │
        ├──────────────── domain (pure, deterministic) ────────────────────┤
        │ ratios · valuation · technicals · risk · adjust · reconcile ·    │
        │ scoring · theme DSL · RRG · entry setup · concall score          │
        ├─────────────────────────── connectors ───────────────────────────┤
        │ McpClient ── NseMcpProvider (bhavcopy + CM market)   [primary]   │
        │           └─ KiteMcpProvider (allowlisted read tools) [fallback] │
        │ KiteConnectProvider · FileFundamentalsProvider · search · LLM    │
        └──────────────┬──────────────────────────────┬────────────────────┘
                 PostgreSQL (psycopg 3)           Redis / in-memory
                                        ▲
                    worker (APScheduler): prices daily, RRG every 12 h,
                    themes monthly, instruments daily, alerts every 15 min
```

## Rules the code enforces

- **Discovered tools only.** `McpClient` calls a tool only if the server advertised it; arguments are
  bound to the advertised schema (`connectors/binding.py`). Unknown required arguments make the
  capability `not_supported` for that call.
- **No trading surface.** `KiteMcpProvider` passes an allowlist of read-only tools; everything else
  raises before a request is sent. No API route, model or UI element concerns orders.
- **Errors are not values.** Connector failures raise typed errors; only successful results are
  cached; services turn failures into states (`missing`, `not_supported`, `stale`, `unavailable`,
  `login_required`) with reasons.
- **The model does no arithmetic.** Scores, ranks and labels come from `domain/`. The LLM provider
  is used only by `services/research.py` and `services/concall.py`, behind schema validation and
  grounding checks.
- **Demo and live never mix.** Demo mode points the same connectors at local mock MCP servers and
  stamps every record `is_demo`. Switching mode removes the other mode's instruments.
- **Jobs are idempotent.** One active run per job key; failures are recorded and leave earlier
  snapshots untouched. Theme snapshots are immutable; RRG snapshots are keyed by 12-hour slot.

## Data flow for one instrument

1. `ingestion.ingest_prices` asks `CorrelatedMarketData.history`.
2. NSE history (unadjusted) and corporate actions are fetched; NSE bars are split-adjusted.
   Broker history is fetched in parallel when authorised.
3. `reconcile_bars` picks one source per date by precedence and lists conflicts.
4. Bars, actions, conflicts and one lineage record per source call are stored.
5. `metrics.compute_all` computes every metric for the universe, scores it, derives labels and the
   entry setup, and stores one `metric_snapshots` row per instrument.

## Main tables

`instruments`, `instrument_identifiers`, `price_bars`, `corporate_actions`, `market_quotes`,
`source_conflicts`, `data_source_records` (lineage), `financial_statement_periods`,
`metric_snapshots`, `themes`, `theme_versions`, `theme_snapshots`, `theme_snapshot_constituents`,
`sector_rotation_snapshots`, `research_reports`, `research_sources`, `concall_transcripts`,
`concall_analyses`, `watchlists`, `watchlist_items`, `alerts`, `alert_events`, `job_runs`,
`audit_events`, `users`.

Differences from the suggested model in `CLAUDE.md`: statement facts and computed metrics are JSON
documents on typed rows instead of one row per fact or metric; sectors and exchanges are columns,
not tables; quotes are fetched live and cached, not persisted.

## API

Versioned under `/api/v1`; interactive reference at `/docs`. Responses carry `data_mode`,
`is_demo`, `retrieved_at` and, where relevant, `as_of`, `calculation_version`, coverage and
freshness. Errors use `{"error": {"code", "message", "details", "request_id"}}`.
