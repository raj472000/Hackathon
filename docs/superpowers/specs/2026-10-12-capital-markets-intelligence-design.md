# AI Capital Markets Intelligence & Theme Screener — design

Date: 2026-10-12 · Status: approved by standing goal (user declined the question round and asked to proceed)

## 1. Intended outcome

A working, explainable research and screening prototype for NSE-listed instruments, built to the
contract in `CLAUDE.md`: screener, themes with monthly snapshots, sector rotation (RRG), stock
research board, entry-setup indicator, concall commitment analyzer, watchlists/alerts and a data
status page. It is a research product. It never places orders.

Success means: a clean checkout starts with one documented command sequence; every displayed number
carries source, timestamp and freshness; NSE MCP and Kite data are correlated field by field with
conflicts surfaced; all deterministic maths is unit-tested; missing data is shown as missing.

## 2. What discovery established (2026-10-12)

| Source | Finding |
|---|---|
| Kite MCP `https://mcp.kite.trade/mcp` | Live. Streamable HTTP, server `Kite MCP Server v0.3.2`, 22 tools. Market data tools need an interactive `login` (browser authorisation) bound to the MCP session. Includes order tools that this product must never call. |
| NSE MCP `https://mcp.nseindia.in/bhavcopy/cm/mcp`, `/cmmkt/mcp` | Host resolves (Akamai) but origin returned HTTP 504 on every attempt. Tool schemas could not be read directly. Public write-ups list 13 tools per server; only `get_52_week_high_low` has a confirmed schema. |
| Kite Connect v3 REST | Documented: `Authorization: token api_key:access_token`, daily token expiry at 06:00 IST, quote 1 req/s, historical 3 req/s, `/quote` ≤500 instruments, `/quote/ohlc` and `/quote/ltp` ≤1000. Needs a paid developer app. |
| Fundamentals | Neither NSE MCP nor Kite exposes financial statements. |
| Hugging Face / web search | No credentials configured. |
| Machine | No Docker, PostgreSQL or Redis. Python via uv, Node 26. |

NSE's stated terms: MCP data is for informational and educational use, not commercial deployment or
AI model training. Bhavcopy prices are unadjusted for splits and bonuses.

## 3. Decisions

1. **Modular monolith + in-process scheduler.** FastAPI API, APScheduler worker (same codebase,
   separate process in Compose). No microservices.
2. **Schema-driven MCP binding.** The connector lists tools at startup, stores the advertised
   schemas, and maps *capabilities* (history, quote, corporate actions, search…) to tools that are
   actually present. Arguments are bound against the advertised `inputSchema`; a capability whose
   tool is absent or whose required arguments cannot be bound is reported `not_supported`. No tool
   name is assumed to exist.
3. **Broker tool allowlist.** Only read-only market-data tools of Kite MCP are callable
   (`search_instruments`, `get_quotes`, `get_ohlc`, `get_ltp`, `get_historical_data`, `login`,
   `get_profile`). Order, GTT, holdings and positions tools are refused in code and covered by a test.
4. **Correlation policy.** Per field: NSE first, broker fills gaps. When both supply a value they
   are compared with a field-specific tolerance; disagreements are stored in `source_conflicts`,
   the precedence winner is used, and the conflict is shown in the UI. The broker never silently
   overrides NSE.
5. **Kite Connect REST adapter** as an optional second broker path (instrument dump, batch quotes,
   historical candles) behind the same `BrokerDataProvider` interface, enabled only when
   `KITE_API_KEY` and an access token are configured.
6. **Fundamentals** come through a `FundamentalsProvider` interface. Shipped implementation: a
   file/upload provider (normalised JSON). Without data the fundamentals and valuation pillars are
   `missing`, scores are marked incomplete.
7. **Demo mode is the same code path.** `DATA_MODE=demo` points the connectors at local mock MCP
   servers (real streamable-HTTP MCP, tool names mirroring the real servers) that serve
   deterministic synthetic data. Every record is stamped `is_demo` and the UI shows a permanent
   banner. Demo data never enters a live database.
8. **Local runtime.** SQLite + in-memory cache/locks natively (verifiable here); PostgreSQL + Redis
   through Docker Compose (written, not verifiable on this machine).
9. **Sector series for RRG.** Kite index history when the broker session is authorised; otherwise
   an equal-weight composite built from constituent histories, labelled as such.
10. **LLM** (`LLMProvider`): Hugging Face router, OpenAI-compatible chat completions, JSON-schema
    output validated by Pydantic, one repair retry, then a deterministic summary. The LLM never
    computes scores.
11. **Concall analyzer.** Transcripts are uploaded per instrument and fiscal quarter. Commitments
    are extracted from quarter *Q-1*, each is checked against quarter *Q* evidence, and a
    deterministic follow-through score is computed from the per-commitment verdicts. Verdict quotes
    must be verbatim substrings of the transcripts or they are rejected.
12. **Auth.** Single-tenant prototype: optional bearer token (`API_AUTH_TOKEN`), one local user,
    role column present. Multi-user auth is out of scope and listed as a limitation.

## 4. Components

```
connectors/   mcp_client (session, discovery, breaker, rate limit) · nse_mcp · kite_mcp ·
              kite_connect · fundamentals_file · web_search · hf_llm
domain/       pure, deterministic: financial_metrics · valuation · technicals · risk · adjust ·
              scoring · theme_dsl · themes_builtin · rrg · entry_setup · reconcile · concall_score
services/     instruments · market_data (correlation) · ingestion · metrics · screener · themes ·
              sector_rotation · research · concall · alerts · jobs · audit
api/v1/       REST routes of CLAUDE.md §15 plus /concalls
workers/      APScheduler jobs with unique job keys and locks
frontend/     React + TypeScript + Vite + Tailwind + TanStack Query + Lightweight Charts
```

Data flow: connector → `DataPoint` (value, provider, retrieved_at, as_of, currency, unit,
adjustment, source id) → reconcile → persist raw + normalised → metrics engine (versioned) →
scoring/themes/RRG snapshots → API → UI.

## 5. Scoring

Five pillars at 20% each. Within a pillar, the equal-weight mean of eligible metrics, each a
percentile rank inside the peer universe at the as-of date and oriented so higher is more
favourable. Missing or inapplicable metrics leave the denominator. A pillar needs ≥50% metric
coverage; otherwise the total is `incomplete` and coverage is shown. Confidence and risk labels
come from deterministic rules. Formula version ids are persisted with every snapshot.

## 6. Error handling

Connector failures produce typed degraded states (`missing`, `not_supported`, `delayed`, `stale`,
`unavailable`) with a reason. Errors are never cached as values. A failed refresh keeps the last
good snapshot and marks it stale. API errors share one schema with a request id.

## 7. Testing

pytest for domain maths (hand-calculated fixtures), scoring weight proofs, theme DSL, RRG,
reconciliation, connector contract tests against the mock MCP servers and the recorded real Kite
tool list, API integration tests, LLM contract and grounding tests with mocked responses, scheduler
tests with a controllable clock, security tests. Vitest for components and Playwright for the main
flows.

## 8. Out of scope

Order placement, portfolio changes, intraday candles from NSE, multi-user auth, backtested entry
zones beyond the documented ATR/moving-average method, commodities data ingestion (schema only).
