# Proof of fix — 15 Oct 2026

What was asked, what was wrong, what changed, and the evidence. Screenshots are in
[`proof/`](proof/). Files starting `live-` were taken against the real NSE MCP servers and the
PostgreSQL database. The others were taken against a demo stack (mock MCP servers,
synthetic `DEMO…` symbols, labelled as such on every page).

## Test results

| Suite | Command | Result |
|---|---|---|
| Backend (unit, contract, API, services, security) | `cd backend && uv run pytest --cov=app` | **364 passed**, 3 skipped |
| Backend lint and types | `uv run ruff check . && uv run mypy app` | clean |
| Frontend types and unit tests | `cd frontend && npx tsc -b && npm test` | clean, **14 passed** |
| Browser end-to-end, demo stack, desktop and mobile | `E2E_BASE_URL=http://127.0.0.1:5174 npx playwright test` | **34 passed** |
| Browser checks against live data (NSE, filings, OpenRouter, Kite) | `E2E_LIVE=1 E2E_BASE_URL=http://127.0.0.1:5175 npx playwright test e2e/live-proof.spec.ts --project=desktop` | **8 passed** |

New tests:
- `backend/tests/services/test_ask_theme_ai.py`: 16 tests covering asking the market, the AI theme filter, the refresh jobs and the Overview speed fix.
- `backend/tests/connectors/test_llm_provider.py`: 23 tests for the provider-neutral LLM client.
- `backend/tests/connectors/test_real_contracts.py`: 2 new tests for NSE's limited history window.
- `frontend/e2e/features.spec.ts`: 16 browser tests.
- `frontend/e2e/live-proof.spec.ts`: an opt-in check against live data.

---

## 1. The Overview page did not load

**Cause.** Every page load called NSE's market-breadth tool on the bhavcopy server. That server
was timing out, so the page waited the full 30-second tool timeout before rendering.

**Fix.**
- An ordinary page load never waits: it uses the cached result, or starts the check in the
  background and shows "Checking…".
- The page's **Refresh** button re-checks NSE live, with a 6-second limit.
- A failure is remembered for 2 minutes as a state. It is never stored as a value.

**Evidence.**
- Live `GET /api/v1/overview` took **30.2 s** before the fix and **0.01–0.04 s** after.
- Tests: `test_overview_page_load_never_waits_for_breadth` and
  `test_overview_never_waits_long_on_a_slow_breadth_source`.

### Ask the market (on Overview)

How a question is answered:
1. The language model interprets the question into a validated plan: symbols from the instrument
   master, metrics from the catalog, and sectors.
2. Quotes and market movers are fetched **live from the MCP sources**. If a source fails, the
   **last values stored in the database** are used and labelled with their date.
3. Screens use the stored, deterministically computed metrics.
4. The model writes the answer and may cite only the evidence it was given. Uncited points are
   removed.

With no model configured, rule-based interpretation and answers are used, and the page says so.

**Evidence.**
- [`live-01-ask-icicibank.png`](proof/live-01-ask-icicibank.png): a real NSE quote for ICICIBANK,
  ₹1,389.40 (+3.80%), tagged "Live · MCP".
- [`02-ask-screen-question.png`](proof/02-ask-screen-question.png): a ranked screen answer.
- [`03-ask-single-stock.png`](proof/03-ask-single-stock.png): live quote and stored metrics side
  by side.
- [`04-ask-market.png`](proof/04-ask-market.png): a market question.
- Tests:
  - `test_ask_uses_live_mcp_quote_and_stored_metrics_without_llm`
  - `test_ask_falls_back_to_database_when_mcp_is_down`: when MCP is down, the stored close is
    used and flagged.
  - `test_ask_with_llm_plans_validates_and_drops_uncited_points`: the model path, using a mocked
    OpenRouter response.
  - `test_ask_falls_back_to_rules_when_llm_has_no_credits`

## 2. Themes showed no data

**Causes.**
1. The live price ingestion failed for 58 of 59 instruments. NSE keeps only a limited history
   window, and pages older than it answer "Symbol not found" or "No data for index … between".
   The connector treated that as a total failure and threw away the newer pages it had already
   received.
2. A full refresh only re-ranked themes when *no* snapshot existed, so empty October snapshots
   were never replaced.

**Fixes.**
- The connector keeps the newer pages and records a warning.
- Failed symbols are retried once after the circuit breaker resets.
- A full refresh re-ranks every theme whose snapshot is empty or older than the metrics. This
  creates a new revision; earlier snapshots are unchanged.

**Evidence (live NSE data).**
- The refresh after the fix: **58/59 instruments, 37,721 daily bars**. Sector rotation was
  computed and **65 themes** re-ranked. LTIM is the only failure: neither NSE nor Kite recognises
  that symbol.
- Price momentum: 12 members (DIVISLAB, TECHM, KOTAKBANK, …).
- Low realized volatility: 25 members (NTPC, SUNPHARMA, BRITANNIA, …).
- Screenshot: [`live-02-themes.png`](proof/live-02-themes.png).

### AI filter on each theme

On a theme's page, "Narrow this theme with AI" turns a request such as "only banks", "low debt"
or "the 10 least volatile" into validated filters. These are applied deterministically to the
latest data. The saved snapshots are not changed.

**Evidence.**
- [`live-03-low-volatility-ai-filter.png`](proof/live-03-low-volatility-ai-filter.png): live
  "only banks" gives ICICIBANK, KOTAKBANK, SBILIFE, SBIN, AXISBANK, HDFCLIFE and BAJAJFINSV.
- [`07-theme-ai-filter.png`](proof/07-theme-ai-filter.png): the same on the demo stack.
- Tests: `test_theme_ai_filter_*`, including the model path, where an invented metric is ignored.

### Refresh on each theme

Every theme card and theme page has **Refresh**. It fetches the latest prices from MCP,
recomputes the metrics, and re-ranks the theme into a new snapshot revision.

**Evidence.**
- [`06-theme-card-refreshed.png`](proof/06-theme-card-refreshed.png): "25 members, prices updated
  for 59 of 59".
- Test: `test_theme_refresh_fetches_latest_prices_then_reranks_and_keeps_history`.

## 3. A refresh button on every page

| Page | What Refresh does | Screenshot |
|---|---|---|
| Overview | Re-checks the live NSE sources, then reloads | [08](proof/08-refresh-overview.png) |
| Screener | Reloads the latest computed metrics | [09](proof/09-refresh-screener.png) |
| Themes | Reloads the list; each card has its own fetch-and-re-rank Refresh | [10](proof/10-refresh-themes.png) |
| Sector rotation | Reload, plus Recalculate from the latest prices | [11](proof/11-refresh-rotation.png) |
| Research board | Reloads the reports and source status | [12](proof/12-refresh-research.png) |
| Concall follow-through | Reloads the transcripts and analyses | [13](proof/13-refresh-concalls.png) |
| Watchlists and alerts | Reloads the prices and alert states | [14](proof/14-refresh-watchlists.png) |
| Data status | Reconnects to every source and re-reads its tools | [15](proof/15-refresh-status.png) |
| Instrument | Fetch latest: new bars for that instrument from MCP, then recomputed metrics | [16](proof/16-refresh-instrument.png) |

Each button is tested in `features.spec.ts` ("refresh button on … reloads its data"). Refreshes
that fetch from a source are rate-limited to 3 per minute, and the page shows the wait time.

## 4. UI redesign

The new design has:
- a deep indigo navigation rail, grouped into Markets, Research and You;
- a saffron accent used only for focus, selection and the main action;
- jade for gains and brick for losses;
- Bricolage Grotesque headings and IBM Plex Sans tabular figures;
- the "Ask the market" band as the single focal element on Overview;
- a light and dark theme switch, and a responsive layout down to phone width.

**Evidence.**
- [`17-overview-desktop.png`](proof/17-overview-desktop.png)
- [`17-overview-mobile.png`](proof/17-overview-mobile.png): checked to have no horizontal scroll.
- [`18-overview-dark.png`](proof/18-overview-dark.png)

## 5. LLM calls: OpenRouter, or any provider, configured only in `.env`

All Hugging Face code is removed, and no provider URL is built into the code. One client calls
`POST {LLM_BASE_URL}/chat/completions` for any OpenAI-compatible provider. These settings control
it:

| Setting | What it sets |
|---|---|
| `LLM_BASE_URL` | the provider's API endpoint |
| `LLM_API_KEY` | the key |
| `LLM_MODEL_ID` | the model |
| `LLM_PROVIDER_NAME` | the display name |
| `LLM_EXTRA_HEADERS`, `LLM_EXTRA_BODY` | provider-specific extras (JSON) |
| `LLM_RESPONSE_FORMAT` | `json_schema`, `json_object` or `none` |
| `LLM_VERIFY_MODEL_LIST` | whether to check the model id against the provider's list first |

`.env` and `.env.example` are set up for OpenRouter with `z-ai/glm-5.3-flash`. The other
endpoints that used to be in code are now in `.env` as well: `DATABASE_URL`, `CORS_ORIGINS` and
`WEB_SEARCH_BASE_URL` (Brave).

The client:
- tracks each call's cost when the provider reports it;
- verifies the model id against `GET {LLM_BASE_URL}/models` before spending credits;
- retries 429, 5xx and upstream errors that arrive inside a 200 response;
- removes keys from error messages and logs.

**Evidence.**
- `backend/tests/connectors/test_llm_provider.py`, 23 tests on mocked HTTP:
  - two different providers configured purely from settings: an OpenRouter-style gateway, and a
    self-hosted Ollama-style server without schema support;
  - the base URL is used exactly as written (including Gemini's `/v1beta/openai` path);
  - each missing setting is named in the error;
  - a missing `DATABASE_URL` stops start-up with a clear message.
- The real OpenRouter API was checked earlier with the same request path: the model id is in the
  live list, and an invalid key returns HTTP 401.

## 6. Review of 15 Oct 2026 (evening): seven issues

Live checks ran against the real sources, using `.env` with the OpenRouter model `z-ai/glm-5.3-flash`:
NSE MCP, NSE filings, the Kite MCP and PostgreSQL. The command is
`E2E_LIVE=1 E2E_BASE_URL=http://127.0.0.1:5175 npx playwright test e2e/live-proof.spec.ts`, and
**7 of 7 passed**. Demo-stack browser tests are in `e2e/fixes.spec.ts` (5 tests), and backend tests are in
`tests/services/test_live_data_fixes.py` (23) and `tests/connectors/test_nse_filings.py` (13).

### 6.1 Overview answer refused to answer and used stale database data

**Cause.**
- The plan had no notion of "the last N days", so "strong bullish stocks in last 3 days" became an
  unfiltered screen sorted by a score that was null for every stock.
- The model then wrote that the evidence did not show it.
- The model could also add stocks the question never named.

**Fix.**
- A `movers` intent with `lookback_days`, understood by both the model and the rules ("last 3 days",
  "this week", "past 2 weeks").
- The window ends at **the price of the day**: NSE MCP `get_bulk_quote` first, then Kite `get_quotes`,
  then the last stored close. Each price is labelled live or stored, with its date.
- When stored daily bars are behind the last session, the question starts a background price refresh
  (at most once every 30 minutes) and the page offers "Ask again" when it finishes.
- The model is told to lead with the answer. Stocks the question did not name are dropped.
- Every model-written answer carries: *"This answer was generated by a language model from the data
  shown below. It can be wrong or incomplete, so do not rely on it alone."*

**Evidence.**
- [`live-05-ask-bullish-3-days.png`](proof/live-05-ask-bullish-3-days.png): TECHM +12.2%, HCLTECH +11.2%,
  TCS +10.6% over 3 sessions, with 50 live NSE MCP prices and the reliability note.
- [`20-ask-movers-3-days.png`](proof/20-ask-movers-3-days.png): the same on the demo stack.

### 6.2 Fundamentals and valuation pillars were empty

**Cause.** No MCP server provides financial statements. The recorded tool lists show that the Kite MCP
exposes only quotes, history and account tools, so Kite cannot fill this.

**Fix.** A new adapter, `connectors/nse_filings.py`, reads the **financial results each company files with
NSE**: SEBI integrated-filing XBRL, consolidated where filed. It covers:
- income statement, balance sheet and cash flow, for industrial, bank and insurer formats;
- EPS restated across bonus issues and splits (HDFCBANK's 2025 1:1 bonus);
- P/E on trailing-twelve-month EPS;
- documents fetched only from `nsearchives.nseindia.com`, size-capped, with DTDs refused.

The endpoints are set in `.env` (`NSE_FILINGS_BASE_URL`, `NSE_FILINGS_DOCUMENT_HOSTS`).

**Evidence.**
- Live ingestion: **50 of 51 equities, 347 periods**; complete five-pillar scores went from **0 to 49 of 50**.
- ABB: fundamentals 6/8 and valuation 5/5, where both were 0 before; P/E 48.3 = ₹6,816 / TTM EPS ₹141.01.
- Screenshot: [`live-06-abb-pillars.png`](proof/live-06-abb-pillars.png).
- HDFCBANK is still incomplete: NSE MCP has price history only from Jul 2026. It is backfilled from Kite
  after you log in (6.6).

### 6.3 Sector rotation: long tails and missing sectors

**Fix.**
- A **Tail** slider (1 to 10 weeks). The axes rescale to what is drawn. Since 19 Oct 2026 the default is
  2 weeks, with − and + buttons and ◀ Week / Week ▶ to step back through earlier weeks.
- Consumer Durables and Telecom were missing because a composite needed 3 stocks. Every sector with any
  priced constituent is now plotted, and thin composites are labelled ("BHARTIARTL only: a thin proxy").

**Evidence.**
- [`live-07-rotation-all-sectors-tail-3.png`](proof/live-07-rotation-all-sectors-tail-3.png): **12 of 12 sectors**.
- [`21`](proof/21-rotation-tail-2-weeks.png) and [`22`](proof/22-rotation-tail-4-weeks.png): the slider,
  with the polyline points counted in the test.

### 6.4 Research board showed rule-based values ("failed schema validation")

**Cause.** GLM is a reasoning model. Its thinking used the whole 2,000-token budget, so the answer was
empty: "Expecting value: line 1 column 1".

**Fix.**
- Low reasoning effort via `LLM_EXTRA_BODY`, and `LLM_MAX_OUTPUT_TOKENS=6000`.
- A cut-off reply is retried once with double the budget, and the error names the setting to change.
- The schema is always sent in the prompt.
- Grounding no longer reads labels such as FY27, SMA200 or "Oct 8" as facts.
- Ids written inline in the text count as citations.
- A failed summary gets one small repair call.

**Evidence.**
- [`live-08-research-tcs-llm.png`](proof/live-08-research-tcs-llm.png): written by the model.
- A fresh report took 39 s, with 32 of 35 points grounded, at about $0.0035.

### 6.5 Concalls: fetch from NSE, check promises on the web, score, with no upload

**Fix.**
- Opening a company fetches its last four con-call transcripts from NSE announcements: the results call
  of each quarter, as PDF.
- The model extracts the commitments from the previous call.
- Each one is checked with a web search, and verdicts may cite the transcript (verbatim quote) or a
  retrieved web source (by id).
- PDF ligatures and curly quotes no longer break verbatim matching.
- Uploading remains for calls NSE does not have.

**Evidence.** [`live-09-concall-tcs-nse.png`](proof/live-09-concall-tcs-nse.png): TCS Q1 FY27 → Q2 FY27.
- 4 transcripts came from NSE, and 6 web searches were made.
- All 12 commitments are grounded.
- Score 34.6, "Weak follow-through".

### 6.6 Kite: login at start, and the login link opened an invalid page

**Cause.** Kite rejects an authorisation link after about 5 minutes; this was measured live, with the
link valid at 271 s and HTTP 400 "invalid authorize session" at 302 s. The app showed a link issued
when the API started.

**Fix.**
- The app asks for the Kite login when it opens.
- **Open the Kite login page** requests a new link at the moment of the click
  (`POST /sources/broker/login-link`). The tab opens synchronously, so pop-up blockers allow it.
- A cached link older than 60 s is never shown.
- When the session becomes authorised, a backfill job refetches short or stale history (Kite fills what
  NSE lacks), then recomputes metrics and the rotation graph.
- The status strip shows Kite as needing login instead of green.

**Evidence.**
- [`live-10-kite-prompt.png`](proof/live-10-kite-prompt.png) shows the prompt.
- [`live-11-kite-authorize-page.png`](proof/live-11-kite-authorize-page.png): the clicked link opens Kite's
  real "Continue to Kite login" page.
- [`23`](proof/23-kite-login-prompt.png) and [`24`](proof/24-kite-connected.png): each click gets a new
  link, and approval is confirmed.
- Test `test_login_link_is_issued_on_click_and_an_expired_link_is_never_served`: the mock Kite server
  expires links like the real one.

## 7. Only the top 50 stocks were tracked (16 Oct 2026)

**Cause.** The live universe was the hand-written file `backend/data/reference/nse_universe.csv`:
Nifty 50 plus 8 indices.

**Fix.** `UNIVERSE_SCOPE=all` (set in `.env`) tracks every NSE-listed equity in `UNIVERSE_SERIES` (EQ, BE).
- **Stock list:** NSE's `EQUITY_L.csv`, 2,605 listed, of which 2,576 are in the EQ and BE series.
- **Sectors:** NSE's industry classification from the Nifty Total Market constituents file, covering 749
  stocks. The rest are shown as unclassified rather than guessed.
- **Prices:** NSE's daily bhavcopy, one file per trading day for every stock. That is 688 files instead
  of thousands of per-stock MCP calls.
- **Splits and bonuses:** back-adjusted from NSE's market-wide corporate-actions list (one call). Gaps
  that no recorded action explains are counted and reported.
- **Later refreshes** append new days only. Files are cached in `backend/var/nse_filings`.
- **Financial statements:** fetched for 400 companies per refresh, companies without statements first.
- **Ask the market:** sends the model only the stocks a question names, and matches short tickers only when
  typed in capitals. Movers questions rank the whole market on stored closes, then fetch live prices
  for the leading 60.
- **Search:** ranks the exact symbol first.
- **URLs** are in `.env`: `NSE_EQUITY_LIST_URL`, `NSE_INDUSTRY_LIST_URL`, `NSE_BHAVCOPY_URL_TEMPLATE`.

**Evidence (live run, 16 Oct 2026).**
- Universe: 2,576 equities; 1,409,374 daily bars from Sep 2023 to Oct 2026; 111 holidays skipped. The run
  took 9 minutes, and computing metrics for 2,576 instruments took 20 s.
- Sector rotation: **22 of 22 sectors**, up from 12. All 65 themes were re-ranked over the whole market.
- "Strong bullish stocks in last 3 days" ranks **2,573 stocks**; the leaders are now small caps such as
  GUJRAFFIA +48.5%.
- Screenshots:
  - [`live-12-screener-whole-market.png`](proof/live-12-screener-whole-market.png): the Screener shows
    "2576 of 2576 instruments".
  - [`live-13-small-cap-instrument.png`](proof/live-13-small-cap-instrument.png): a small cap's page.
  - [`live-14-rotation-all-sectors.png`](proof/live-14-rotation-all-sectors.png): sector rotation.
- Tests:
  - `tests/services/test_market_files.py` checks the universe from NSE's files, bhavcopy loading with split
    adjustment, holidays, incremental re-runs and the reference scope.
  - Search ordering and live-candidate tests are in `test_live_data_fixes.py`.

**Statements and sectors for the whole market.**
- The statements backfill covered **2,309 of 2,576** companies; the rest file no integrated results
  with NSE. NSE throttling is handled by pausing and resuming. Companies with no filings are not asked
  again for 7 days.
- Most stocks were still incomplete only on the market/sector context pillar, because they had no
  sector. They now take the sector shared by NSE-classified stocks with the same NSE industry label,
  when at least 60% agree. Tested in `test_unclassified_stocks_take_the_sector_...`.
- Live result: labels for 888 companies; **1,209 stocks** now have a sector (749 from NSE's index file
  and 460 by label). **Complete five-pillar scores: 1,104 of 2,576**, up from 50.
- The other ~1,370 stocks have no NSE industry label in a year of announcements, or a label without a
  clear majority sector. They stay unclassified and show a partial score that says so.

The backfill has finished. The daily refresh keeps statements, prices and labels current.

## 8. Kite connect stopped working after the API had been running (19 Oct 2026)

**What you saw.** "Connect Kite" opened nothing useful: the app had no login link to give.

**Reproduced on the running API (port 8002, up since 15 Oct):**

```
POST /api/v1/sources/broker/login-link
→ state login_required, login_url null, issued_at 2026-10-15T18:55:10Z   (a link from four days earlier)
```

A fresh process on the same machine got a link at once, so the code path worked and the long-running
server was stuck.

**Root cause.**
- Kite's MCP server forgets an idle session. It then answers every call with **HTTP 400, plain text
  `Invalid session ID`**, verified live against `mcp.kite.trade`.
- The MCP client reconnected only on HTTP 404 or a JSON-RPC message mentioning "session", so it never
  reconnected.
- `get_profile` then failed and was read as "needs login".
- The `login` tool failed the same way, and that error was swallowed. The server returned
  `login_url: null` until it was restarted.

**Fix.**
- `backend/app/connectors/mcp_client.py`: a 400 whose plain-text body mentions the session counts as an
  expired session. The client re-handshakes once and repeats the call.
- `backend/app/connectors/kite_mcp.py`: a failed `login` call clears the old link and returns a
  `reason`. The Connect prompt shows that reason instead of a generic message.
- The demo Kite server (`app/devtools/mock_mcp.py`) can now forget a session exactly as Kite does, so
  the test suite covers this case.

**Tests.** All fail before the fix and pass after it:
- `test_kite_plain_text_invalid_session_is_reinitialised_once` (client level, Kite's recorded reply).
- `test_login_link_still_works_after_kite_forgets_the_mcp_session`: through the API, the server forgets
  the session and the next click still gets a new link. The link authorises the new session, and the
  state becomes `authorized`.
- `test_a_failed_login_tool_explains_why_there_is_no_link`.

**Live proof against Kite**
(`analysis/proof/kite-forgotten-session.txt`, session IDs truncated):

```
1. first click: login_required https://mcp.kite.trade/authorize?session_id=kitemc…
2. Kite's answer to the forgotten session: 400 'Invalid session ID'
3. second click: login_required https://mcp.kite.trade/authorize?session_id=kitemc… reason: None | reconnected: True
4. opening the new link: 200 | title: Continue to Kite Login – Kite MCP Server | 'Continue to Kite login' on page: True | 'invalid authorize session': False
```

After restarting the API on the fixed code, `POST /sources/broker/login-link` returns a new link. The
browser check `live: Kite login is asked at start and the link opens a valid Kite page` passes, and
[`live-11-kite-authorize-page.png`](proof/live-11-kite-authorize-page.png) shows Kite's real
"Continue to Kite login" page.

**Found while fixing this: the API took more than 6 minutes to start.**
- Start-up ran the instrument sync, which first asked NSE for industry labels. That was up to 3,000
  rate-limited requests, because the ~1,700 companies with no label were never remembered and were asked
  again on every run.
- Fixes:
  - Start-up syncs from the stored labels with no NSE requests; the scheduled instruments job refreshes
    labels.
  - Companies NSE has no label for are recorded and asked again only after 30 days
    (`test_label_refresh_remembers_companies_nse_has_no_label_for_and_startup_skips_it`).
- Start-up now takes 26 s.

**Also found: the complete-score count had dropped to 683.**
- The 8002 server had been started before the label classification was added.
- Its scheduled sync on 17 Oct reset sectors to the 749 from NSE's index file, and the price job then
  recomputed the scores.
- After the restart on current code, the sectors (1,209) and **1,104 of 2,576 complete scores** are back.

## What is not verified, and what you need to do

- **Signing in to Zerodha itself** needs your account. The link and approval flow is verified up to Kite's
  login page, including after Kite has dropped the server's session. After you approve, the HDFCBANK backfill runs automatically.
- **Your API on port 8000 runs the code from before these fixes and has no auto-reload.** Its scheduler
  recomputed the rotation graph with the old rule at 17:39. Restart it with `./scripts/dev.sh`.
- **NSE filings history:** machine-readable full-year figures start with the year ended March 2025.
  Earlier XBRL files hold only the quarter, so 3-year CAGR shows "insufficient history".
- **NSE MCP servers are intermittent**, with 504s and timeouts during this session. Answers fall back to Kite
  and then to stored closes, and say so.
- **Web search (ddgs)** is keyless and rate-limited. If it fails, concall verdicts use the transcripts and
  the page says so.
