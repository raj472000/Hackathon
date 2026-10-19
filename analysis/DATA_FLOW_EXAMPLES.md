# Data schemas and data flow

This document describes every external data schema the backend reads and every JSON contract it sends
to the frontend, with real examples. Part E then follows NSE:TCS through each path end to end.

- **Part A:** schemas of the three MCP servers: tool inputs, real outputs, and how each field is mapped
- **Part B:** non-MCP sources: NSE bhavcopy file, NSE filings and XBRL, web search, and LLM structured output
- **Part C:** the app's internal normalised objects, which every source is converted into
- **Part D:** the JSON each frontend page receives, endpoint by endpoint, with the fields the UI validates
- **Part E:** end-to-end scenarios: quote, history with a degraded source, conflicts and failures

**How the schemas were produced.** Tool *input* schemas are the servers' own `tools/list` answers,
recorded in `backend/tests/fixtures/live/`. *Output* and *API response* schemas are **inferred from real
responses**: each table lists every field that appeared, its JSON type or types, whether it was
missing in some objects (`optional`), and one real value. The backend returns plain JSON objects
with no response models, and NSE's MCP tools publish no output schema, so the payloads are the
ground truth. The responses were captured on 2026-10-16 from the public NSE MCP servers, the Kite
MCP server, and the API running locally in `DATA_MODE=live` against the Compose PostgreSQL. Long
lists are cut to two items and marked `"… N more"`. Nothing else has been edited.

**Path notation:** `a.b` means a nested field, `a[]` an array item, and `a.{key}` a map with dynamic keys,
such as one entry per metric or sector.

---

## Lineage at a glance: source field → storage → API field → page

| Source schema (Part A/B) | Normalised as (Part C) | Stored in | Sent to the frontend as (Part D) | Shown on |
|---|---|---|---|---|
| `cm_get_stock_quote` → `stock.lastTradedPrice`, `openPrice`, … (A.2.1) | `DataPoint` per field, status `delayed` | not stored; merged quote cached 60 s | `GET /instruments/{ref}` → `quote.fields.last_price.{value, provider, as_of, status, source_id}` (D.1) | Instrument header, Research |
| Kite `get_quotes` → `data.{NSE:SYM}.last_price`, `ohlc.*` (A.3.2) | `DataPoint`, status `ok` | not stored | same field. `filled_from_fallback: true` when NSE had no value; `conflict` when they disagree (E.3) | same |
| `get_stock_history` → `data[].{date, open, high, low, close, volume}` (A.1.1) | `BarsResult.frame` → adjusted → merged | `price_bars` (`provider = nse_mcp`) | `GET /instruments/{ref}/history` → `bars[].{date, open, high, low, close, volume, provider}` (D.2) | Instrument chart |
| Bhavcopy CSV → `ClsPric`, `OpnPric`, `TtlTradgVol`, … (B.1) | dataframe per symbol → adjusted | `price_bars` (`provider = nse_bhavcopy_file`) | same `bars[]`, plus `coverage.by_provider` | Instrument chart |
| Kite `get_historical_data` → candles (A.3.3) | `BarsResult.frame` (split-adjusted) | `price_bars` (`provider = kite_mcp`) | same `bars[]`. The fetch outcome appears in `lineage[]` | Instrument chart, Sources panel |
| `get_corporate_actions` → `actions[].{exDate, purpose}` (A.1.2) | `CorporateAction` | `corporate_actions` | `history.corporate_actions[]` (D.2) | Instrument |
| `get_index_history` → `data[]` (A.1.5) | index bars | `price_bars` (INDEX instruments) | `GET /sector-rotation` → `sectors.{name}.trail[].{rs_ratio, rs_momentum}` (D.11); `overview.benchmark` (D.13) | Rotation, Overview |
| Kite `search_instruments` → `data[].instrument_token` (A.3.1) | identifier | `instrument_identifiers` | `instrument.identifiers[]` (D.1) | Instrument (sources) |
| Filings list → `xbrl` URL (B.2); XBRL facts (B.3) | statement period (INR crore) | `financial_statement_periods` | `GET /instruments/{ref}/financials` → `periods[]` (D.4) | Financials tab |
| `price_bars` + statements | metric results with `formula` and `inputs` | `metric_snapshots` | `GET /instruments/{ref}/metrics` → `metrics[]` (D.3); `GET /screener` → `items[].values` (D.8); `snapshot.score` (D.1) | Metrics tab, Screener, Score |
| `get_market_breadth` (A.1.6) | passed through | cached 5 min, never stored | `GET /overview` → `market_breadth.{state, data, source_id, retrieved_at}` (D.13) | Overview |
| `get_bulk_quote`, `nse_get_market_movers` (A.1.3, A.2.3) | dict per symbol | not stored | `POST /ask` answers | Ask panel |
| ddgs results (B.4) | evidence items `S1…Sn` | `research_reports`, `research_sources` | `GET /instruments/{ref}/research` → `evidence.{S*}` (D.5) | Research board |
| LLM `LLMReport` (B.5) | validated, then grounding-checked | `research_reports` | `report.{pros, cons, news, …}[].{text, evidence, kinds}` and `grounding` (D.5) | Research board |
| NSE con-call PDFs; LLM `CommitmentList` and `AssessedList` (B.5) | transcripts, assessments | `concall_transcripts`, `concall_analyses` | `GET /instruments/{ref}/concalls` → `transcripts[]`, `latest.assessments[]`, `latest.score` (D.6) | Concalls |
| Connector health, from every MCP client | health and capabilities | in memory, plus `data_source_records` | `GET /sources/status` → `connectors[]`, `capabilities`, `coverage` (D.17) | Data status, header badge |

---

## Part A: MCP server schemas

### A.0 The envelope every MCP server uses (JSON-RPC 2.0 over Streamable HTTP)

Every request is an HTTP `POST` to the server URL with these headers:
`Content-Type: application/json`, `Accept: application/json, text/event-stream`, and, after
`initialize`, `Mcp-Session-Id: <from the initialize response>` and `MCP-Protocol-Version: 2025-03-26`.
The response body is either JSON or a server-sent event stream whose `data:` line holds the same JSON.

| Message | Schema |
|---|---|
| `initialize` request | `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"capital-markets-intelligence","version":"0.1.0"}}}` |
| `initialize` result | `result.protocolVersion`, `result.capabilities.tools.listChanged`, `result.serverInfo.{name,version}`, `result.instructions` |
| `notifications/initialized` | `{"jsonrpc":"2.0","method":"notifications/initialized"}`, answered by HTTP 202 with no body |
| `tools/list` result | `result.tools[] = {name, description, inputSchema (JSON Schema)}`. None of the three servers sends `outputSchema` |
| `tools/call` request | `{"jsonrpc":"2.0","id":n,"method":"tools/call","params":{"name":"<tool>","arguments":{…}}}` |
| `tools/call` result | `result.content[] = {type:"text", text:"<JSON or plain text>"}`, `result.isError` (boolean), optional `result.structuredContent` |
| Protocol error | `{"jsonrpc":"2.0","id":n,"error":{"code":int,"message":string}}` |

`McpClient._parse_result` joins the `text` parts and parses them as JSON when possible, falling back to
`structuredContent` and then to plain text. `isError: true`, or a JSON-RPC `error`, raises `McpToolError`.
Some NSE tools report a failure inside a normal result, as `{"error": "…"}` with `isError: false`.
`parsing.payload_error()` catches that case and treats it as a failure too, so it can never become a value.

**Real `initialize` results:**

- `cm-market-mcp`: protocol `2025-03-26`, serverInfo `{"name": "cm-market-mcp", "version": "1.0.0"}`, capabilities `{"completions": {}, "logging": {}, "prompts": {"listChanged": true}, "resources": {"subscribe": false, "listChanged": false}, "tools": {"listChanged": true}}`
- `nse-bhavcopy-redis-mcp`: protocol `2025-03-26`, serverInfo `{"name": "nse-bhavcopy-redis-mcp", "version": "1.0.0"}`, capabilities `{"completions": {}, "logging": {}, "prompts": {"listChanged": true}, "resources": {"subscribe": false, "listChanged": false}, "tools": {"listChanged": true}}`

### A.1 NSE bhavcopy MCP: `https://mcp.nseindia.in/bhavcopy/cm/mcp`

Primary source for end-of-day history, corporate actions, symbol lookup, bulk end-of-day quotes and
market breadth. It advertises 21 tools; the app uses the ones below, and the rest are listed on the Data
status page. Prices are **unadjusted**. The server says its data is for educational and research use.

#### A.1.1 `get_stock_history`

**Purpose:** daily OHLCV for one equity. At most 3 months per call; older data is fetched by paging backwards with `next_end_date`.  
**Called by:** `NseMcpProvider.get_history` ← `CorrelatedMarketData.history` ← the prices job and on-demand refresh

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `symbol` | string | yes | NSE stock symbol in uppercase. Examples: RELIANCE, TCS, INFY, HDFCBANK, WIPRO |
| `months` | integer | yes | Months to fetch in this chunk. Max 3. Default: 3. Pass 0 for default. |
| `endDate` | string | yes | End date for this chunk in yyyy-MM-dd format. Omit or pass 'today' for latest data. Pass next_end_date from a previous response to fetch older chunks. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"symbol": "TCS", "months": 3, "endDate": "today"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `symbol` | string | `"TCS"` |
| `from_date` | string (date) | `"2026-07-16"` |
| `to_date` | string (date) | `"2026-10-16"` |
| `trading_days` | integer | `65` |
| `source` | string | `"in-memory-cache"` |
| `query_ms` | integer | `0` |
| `summary` | object |  |
| `summary.first_close` | number | `2201.0` |
| `summary.return_pct` | number | `11.99` |
| `summary.period_low` | number | `2032.4` |
| `summary.period_high` | number | `2495.0` |
| `summary.avg_daily_vol` | integer | `3852620` |
| `summary.last_close` | number | `2465.0` |
| `data` | array |  |
| `data[]` | object |  |
| `data[].symbol` | string | `"TCS"` |
| `data[].series` | string | `"EQ"` |
| `data[].ltp` | number | `2199.1` |
| `data[].open` | number | `2206.0` |
| `data[].high` | number | `2231.4` |
| `data[].low` | number | `2187.8` |
| `data[].close` | number | `2201.0` |
| `data[].prevClose` | number | `2189.2` |
| `data[].volume` | integer | `5197595` |
| `data[].totalTradedValue` | number | `11501044792.2` |
| `data[].ch52WeekHigh` | number | `0.0` |
| `data[].ch52WeekLow` | number | `0.0` |
| `data[].mktType` | string | `"N"` |
| `data[].deliveryQty` | integer | `0` |
| `data[].date` | string (date) | `"2026-07-16"` |
| `chunk_months` | integer | `3` |
| `next_end_date` | string (date) | `"2026-07-15"` |

**Real response** (lists cut to 2 items):

```json
{
  "symbol": "TCS",
  "from_date": "2026-07-16",
  "to_date": "2026-10-16",
  "trading_days": 65,
  "source": "in-memory-cache",
  "query_ms": 0,
  "summary": {
    "first_close": 2201.0,
    "return_pct": 11.99,
    "period_low": 2032.4,
    "period_high": 2495.0,
    "avg_daily_vol": 3852620,
    "last_close": 2465.0
  },
  "data": [
    {
      "symbol": "TCS",
      "series": "EQ",
      "ltp": 2199.1,
      "open": 2206.0,
      "high": 2231.4,
      "low": 2187.8,
      "close": 2201.0,
      "prevClose": 2189.2,
      "volume": 5197595,
      "totalTradedValue": 11501044792.2,
      "ch52WeekHigh": 0.0,
      "ch52WeekLow": 0.0,
      "mktType": "N",
      "deliveryQty": 0,
      "date": "2026-07-16"
    },
    {
      "symbol": "TCS",
      "series": "EQ",
      "ltp": 2266.0,
      "open": 2224.8,
      "high": 2275.0,
      "low": 2215.1,
      "close": 2269.0,
      "prevClose": 2201.0,
      "volume": 5615138,
      "totalTradedValue": 12666031000.1,
      "ch52WeekHigh": 0.0,
      "ch52WeekLow": 0.0,
      "mktType": "N",
      "deliveryQty": 0,
      "date": "2026-07-17"
    },
    "… 63 more"
  ],
  "chunk_months": 3,
  "next_end_date": "2026-07-15"
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `data[].date` | `date (index)` | parsed as a date |
| `data[].open/high/low/close` | `open/high/low/close` | float |
| `data[].prevClose` | `prev_close` |  |
| `data[].volume` | `volume` | shares |
| `data[].totalTradedValue` | `traded_value` | INR |
| `next_end_date` | `endDate` of the next call | paging cursor |
| `ltp, series, mktType, deliveryQty, ch52Week*` | — | not used |
| `summary.*` | — | not used; the app computes its own returns |

#### A.1.2 `get_corporate_actions`

**Purpose:** dividends, splits, bonuses and buybacks with ex-dates, used to back-adjust NSE prices.  
**Called by:** `NseMcpProvider.get_corporate_actions` (run in parallel with history)

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `symbol` | string | yes | NSE stock symbol in uppercase. Example: INFY, BRIGADE, RELIANCE |
| `fromDate` | string | yes | Start date yyyy-MM-dd. Default: 5 years ago. |
| `toDate` | string | yes | End date yyyy-MM-dd. Default: today. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"symbol": "TCS", "fromDate": "2021-10-12", "toDate": "2026-10-16"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `symbol` | string | `"TCS"` |
| `from` | string (date) | `"2021-10-12"` |
| `to` | string (date) | `"2026-10-16"` |
| `count` | integer | `23` |
| `actions` | array |  |
| `actions[]` | object |  |
| `actions[].exDate` | string (date) | `"2021-10-14"` |
| `actions[].actionType` | string | `"DIVIDEND"` |
| `actions[].purpose` | string | `"Interim Dividend - Rs 7 Per Share"` |
| `actions[].adjustmentFactor` | number | `1.0` |

**Real response** (lists cut to 2 items):

```json
{
  "symbol": "TCS",
  "from": "2021-10-12",
  "to": "2026-10-16",
  "count": 23,
  "actions": [
    {
      "exDate": "2021-10-14",
      "actionType": "DIVIDEND",
      "purpose": "Interim Dividend - Rs 7 Per Share",
      "adjustmentFactor": 1.0
    },
    {
      "exDate": "2022-01-19",
      "actionType": "DIVIDEND",
      "purpose": "Interim Dividend - Rs 7 Per Share",
      "adjustmentFactor": 1.0
    },
    "… 21 more"
  ]
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `actions[].exDate` | `ex_date` |  |
| `actions[].purpose` | `description, kind, ratio` | `parse_action()`: "Face Value Split From Rs 10 To Rs 2" gives (split, 5.0), "Bonus 1:1" gives (bonus, 2.0), "Interim Dividend - Rs 7 Per Share" gives (dividend, 7.0), and "Buyback" is not parsed, so it is kept as `other`. Only splits and bonuses adjust prices |
| `actions[].actionType` | `kind (fallback)` | used only when `purpose` cannot be parsed |
| `actions[].adjustmentFactor` | — | not used; the app derives the ratio itself |

#### A.1.3 `get_bulk_quote`

**Purpose:** latest end-of-day snapshot for up to 50 symbols in one call.  
**Called by:** `NseMcpProvider.bulk_quotes` ← `/ask` live-price answers

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `symbols` | array<string> | yes | List of NSE symbols. Example: ["RELIANCE", "TCS", "INFY", "HDFCBANK"]. Max 50. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"symbols": ["TCS", "INFY", "RELIANCE"]}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `count` | integer | `3` |
| `quotes` | array |  |
| `quotes[]` | object |  |
| `quotes[].symbol` | string | `"TCS"` |
| `quotes[].close` | number | `2465.0` |
| `quotes[].open` | number | `2382.0` |
| `quotes[].high` | number | `2484.7` |
| `quotes[].low` | number | `2375.7` |
| `quotes[].prev_close` | number | `2350.0` |
| `quotes[].pct_change` | number | `4.89` |
| `quotes[].volume` | integer | `12166634` |
| `quotes[].date` | string (date) | `"2026-10-16"` |

**Real response** (lists cut to 2 items):

```json
{
  "count": 3,
  "quotes": [
    {
      "symbol": "TCS",
      "close": 2465.0,
      "open": 2382.0,
      "high": 2484.7,
      "low": 2375.7,
      "prev_close": 2350.0,
      "pct_change": 4.89,
      "volume": 12166634,
      "date": "2026-10-16"
    },
    {
      "symbol": "INFY",
      "close": 1161.2,
      "open": 1127.5,
      "high": 1167.85,
      "low": 1122.0,
      "prev_close": 1110.0,
      "pct_change": 4.61,
      "volume": 25566304,
      "date": "2026-10-16"
    },
    "… 1 more"
  ]
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `quotes[].symbol` | `key` |  |
| `quotes[].close` | `last_price` |  |
| `quotes[].prev_close` | `prev_close` |  |
| `quotes[].pct_change` | `change_pct` |  |
| `quotes[].volume` | `volume` |  |
| `quotes[].date` | `as_of` |  |

#### A.1.4 `search_symbols`

**Purpose:** symbol search by name or ticker.  
**Called by:** `NseMcpProvider.search`

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `query` | string | yes | Search query — company name, partial symbol, or keyword. Examples: 'TATA', 'HDFC', 'BANK' |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"query": "TCS"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `query` | string | `"TCS"` |
| `results` | array |  |
| `results[]` | object |  |
| `results[].symbol` | string | `"TCS"` |
| `results[].last_close` | number | `2465.0` |
| `results[].pct_change` | number | `4.89` |
| `results[].last_date` | string (date) | `"2026-10-16"` |
| `count` | integer | `2` |

**Real response** (lists cut to 2 items):

```json
{
  "query": "TCS",
  "results": [
    {"symbol": "TCS", "last_close": 2465.0, "pct_change": 4.89, "last_date": "2026-10-16"},
    {"symbol": "WSTCSTPAPR", "last_close": 840.0, "pct_change": 2.14, "last_date": "2026-10-16"}
  ],
  "count": 2
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `results[].symbol` | `symbol` |  |
| `results[].name / companyname` | `name` |  |

#### A.1.5 `get_index_history`

**Purpose:** daily index values (12 months per call).  
**Called by:** `NseMcpProvider.get_history(…, instrument_type="INDEX")`: benchmark and sector indices for the rotation graph

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `indexName` | string | yes | NSE index name, case-insensitive. Examples: Nifty 50, Nifty Bank, Nifty IT, India VIX |
| `months` | integer | yes | Months to fetch in this chunk. Max 12. Default: 3. Pass 0 for default. |
| `endDate` | string | yes | End date for this chunk in yyyy-MM-dd format. Omit or pass 'today' for latest data. Pass next_end_date from a previous response to fetch older chunks. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"indexName": "NIFTY IT", "months": 1, "endDate": "today"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `index` | string | `"Nifty IT"` |
| `from_date` | string (date) | `"2026-09-16"` |
| `to_date` | string (date) | `"2026-10-16"` |
| `trading_days` | integer | `22` |
| `summary` | object |  |
| `summary.first_close` | number | `29087.65` |
| `summary.last_close` | number | `31924.1` |
| `summary.return_pct` | number | `9.75` |
| `summary.period_high` | number | `32015.65` |
| `summary.period_low` | number | `27612.85` |
| `data` | array |  |
| `data[]` | object |  |
| `data[].indexName` | string | `"Nifty IT"` |
| `data[].open` | number | `29696.45` |
| `data[].high` | number | `29763.4` |
| `data[].low` | number | `28974.9` |
| `data[].close` | number | `29087.65` |
| `data[].pointsChange` | number | `-467.65` |
| `data[].pctChange` | number | `-1.58` |
| `data[].volume` | integer | `29187036` |
| `data[].turnoverCr` | number | `3350.89` |
| `data[].pe` | number | `18.56` |
| `data[].pb` | number | `5.12` |
| `data[].divYield` | number | `2.76` |
| `data[].date` | string (date) | `"2026-09-16"` |
| `chunk_months` | integer | `1` |
| `next_end_date` | string (date) | `"2026-09-15"` |

**Real response** (lists cut to 2 items):

```json
{
  "index": "Nifty IT",
  "from_date": "2026-09-16",
  "to_date": "2026-10-16",
  "trading_days": 22,
  "summary": {
    "first_close": 29087.65,
    "last_close": 31924.1,
    "return_pct": 9.75,
    "period_high": 32015.65,
    "period_low": 27612.85
  },
  "data": [
    {
      "indexName": "Nifty IT",
      "open": 29696.45,
      "high": 29763.4,
      "low": 28974.9,
      "close": 29087.65,
      "pointsChange": -467.65,
      "pctChange": -1.58,
      "volume": 29187036,
      "turnoverCr": 3350.89,
      "pe": 18.56,
      "pb": 5.12,
      "divYield": 2.76,
      "date": "2026-09-16"
    },
    {
      "indexName": "Nifty IT",
      "open": 28918.75,
      "high": 29406.65,
      "low": 28805.4,
      "close": 29155.6,
      "pointsChange": 67.95,
      "pctChange": 0.23,
      "volume": 24170006,
      "turnoverCr": 2719.91,
      "pe": 18.6,
      "pb": 5.13,
      "divYield": 2.75,
      "date": "2026-09-17"
    },
    "… 20 more"
  ],
  "chunk_months": 1,
  "next_end_date": "2026-09-15"
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `data[].date / open / high / low / close` | `bar fields` | same parser as stock history |

#### A.1.6 `get_market_breadth`

**Purpose:** advances, declines and unchanged for a trading day.  
**Called by:** `GET /api/v1/overview` → `market_breadth` (passed through as `data`)

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `date` | string | yes | Date in yyyy-MM-dd format. Returns nearest trading day if holiday. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"date": "2026-10-16"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `date` | string (date) | `"2026-10-16"` |
| `total_stocks` | integer | `3675` |
| `advances` | integer | `2058` |
| `declines` | integer | `1502` |
| `unchanged` | integer | `115` |
| `ad_ratio` | number | `1.37` |
| `total_volume` | integer | `6182337837` |

**Real response** (lists cut to 2 items):

```json
{
  "date": "2026-10-16",
  "total_stocks": 3675,
  "advances": 2058,
  "declines": 1502,
  "unchanged": 115,
  "ad_ratio": 1.37,
  "total_volume": 6182337837
}
```

> `get_top_movers` is advertised but needs `date` and `direction`. The app has no intent for
> `direction`, so `bind_args` refuses it and the capability reports `not_supported` when called
> (captured: `cannot supply required argument(s) date, direction`). Live movers come from the CM
> server instead (A.2.3).

### A.2 NSE CM market MCP: `https://mcp.nseindia.in/cmmkt/mcp`

Primary source for **delayed** live quotes and movers. It advertises 15 tools.

#### A.2.1 `cm_get_stock_quote`

**Purpose:** delayed live quote for one equity.  
**Called by:** `NseMcpProvider.get_quote` ← `CorrelatedMarketData.quote` ← `GET /instruments/{ref}`, research, alerts

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `symbol` | string | yes | Exact NSE symbol (e.g. 'RELIANCE', 'TCS', 'INFY', '1048IIFL27') |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"symbol": "TCS"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `updatedAt` | string (date-time) | `"2026-10-16T11:59:27.105411880Z"` |
| `stock` | object |  |
| `stock.type` | string | `"CM"` |
| `stock.symbol` | string | `"TCS"` |
| `stock.series` | string | `"EQ"` |
| `stock.openPrice` | number | `2382.0` |
| `stock.highPrice` | number | `2484.7` |
| `stock.lowPrice` | number | `2375.7` |
| `stock.preClosePrice` | number | `2350.0` |
| `stock.lastTradedPrice` | number | `2465.0` |
| `stock.indicativeClosePrice` | number | `0.0` |
| `stock.change` | number | `115.0` |
| `stock.perChange` | number | `4.89` |
| `stock.volume` | integer | `12166634` |
| `stock.value` | number | `2968.318030248` |
| `stock.fiftyTwoWeekHigh` | number | `3350.0` |
| `stock.fiftyTwoWeekLow` | number | `1976.8` |
| `stock.perChange30d` | number | `12.62` |
| `stock.latestTimestamp` | string (date-time) | `"2026-10-16 16:00:28"` |

**Real response** (lists cut to 2 items):

```json
{
  "updatedAt": "2026-10-16T11:59:27.105411880Z",
  "stock": {
    "type": "CM",
    "symbol": "TCS",
    "series": "EQ",
    "openPrice": 2382.0,
    "highPrice": 2484.7,
    "lowPrice": 2375.7,
    "preClosePrice": 2350.0,
    "lastTradedPrice": 2465.0,
    "indicativeClosePrice": 0.0,
    "change": 115.0,
    "perChange": 4.89,
    "volume": 12166634,
    "value": 2968.318030248,
    "fiftyTwoWeekHigh": 3350.0,
    "fiftyTwoWeekLow": 1976.8,
    "perChange30d": 12.62,
    "latestTimestamp": "2026-10-16 16:00:28"
  }
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `stock.lastTradedPrice` | `last_price` | status `delayed` |
| `stock.openPrice` | `open` |  |
| `stock.highPrice` | `high` |  |
| `stock.lowPrice` | `low` |  |
| `stock.preClosePrice` | `prev_close` |  |
| `stock.volume` | `volume` | shares |
| `stock.perChange` | `change_pct` | percent |
| `stock.latestTimestamp` | `as_of` | read as IST (+05:30) |
| `stock.fiftyTwoWeekHigh/Low, value, change, perChange30d` | — | not used; computed from stored bars instead |

#### A.2.2 `cm_get_index_quote`

**Purpose:** live index value.  
**Called by:** `NseMcpProvider.get_quote(…, "INDEX")`

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `indexName` | string | yes | Exact NSE live index name, case-insensitive (e.g. 'NIFTY 50', 'NIFTY BANK', 'INDIA VIX') |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"indexName": "NIFTY 50"}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `nseTimestamp` | string | `"16-Oct-2026 15:30:00"` |
| `updatedAt` | string (date-time) | `"2026-10-16T17:33:36.558923323Z"` |
| `group` | string | `"derivatives"` |
| `index` | object |  |
| `index.indexName` | string | `"NIFTY 50"` |
| `index.last` | number | `23146.15` |
| `index.previousClose` | number | `22997.35` |
| `index.change` | number | `148.8` |
| `index.percChange` | number | `0.65` |
| `index.open` | number | `23065.1` |
| `index.high` | number | `23149.85` |
| `index.low` | number | `22913.8` |
| `index.yearHigh` | number | `26373.2` |
| `index.yearLow` | number | `22179.9` |
| `index.timeVal` | string | `"16-Oct-2026 15:40"` |
| `index.comparisons` | object |  |
| `index.comparisons.oneWeekAgo` | object |  |
| `index.comparisons.oneWeekAgo.baseDate` | string | `"09-Oct-2026"` |
| `index.comparisons.oneWeekAgo.baseValue` | number | `22520.45` |
| `index.comparisons.oneWeekAgo.changePct` | number | `2.78` |
| `index.comparisons.oneMonthAgo` | object |  |
| `index.comparisons.oneMonthAgo.baseDate` | string | `"15-Sep-2026"` |
| `index.comparisons.oneMonthAgo.baseValue` | number | `23118.6` |
| `index.comparisons.oneMonthAgo.changePct` | number | `0.12` |
| `index.comparisons.oneYearAgo` | object |  |
| `index.comparisons.oneYearAgo.baseDate` | string | `"15-Oct-2025"` |
| `index.comparisons.oneYearAgo.baseValue` | number | `25323.55` |
| `index.comparisons.oneYearAgo.changePct` | number | `-8.6` |

**Real response** (lists cut to 2 items):

```json
{
  "nseTimestamp": "16-Oct-2026 15:30:00",
  "updatedAt": "2026-10-16T17:33:36.558923323Z",
  "group": "derivatives",
  "index": {
    "indexName": "NIFTY 50",
    "last": 23146.15,
    "previousClose": 22997.35,
    "change": 148.8,
    "percChange": 0.65,
    "open": 23065.1,
    "high": 23149.85,
    "low": 22913.8,
    "yearHigh": 26373.2,
    "yearLow": 22179.9,
    "timeVal": "16-Oct-2026 15:40",
    "comparisons": {
      "oneWeekAgo": {"baseDate": "09-Oct-2026", "baseValue": 22520.45, "changePct": 2.78},
      "oneMonthAgo": {"baseDate": "15-Sep-2026", "baseValue": 23118.6, "changePct": 0.12},
      "oneYearAgo": {"baseDate": "15-Oct-2025", "baseValue": 25323.55, "changePct": -8.6}
    }
  }
}
```

#### A.2.3 `nse_get_market_movers`

**Purpose:** live top gainers and losers.  
**Called by:** `NseMcpProvider.live_movers` ← `/ask` market questions

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `indexName` | string | yes | Index filter: null or empty for ALL securities (default), or NIFTY, BANKNIFTY, NIFTYNEXT50 |
| `limit` | integer | yes | Max stocks per direction (default 10, max 50). Pass 0 for default. |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"limit": 5, "indexName": ""}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `segment` | string | `"allSec"` |
| `gainersUpdatedAt` | string (date-time) | `"2026-10-16T17:33:16.441124502Z"` |
| `losersUpdatedAt` | string (date-time) | `"2026-10-16T17:33:16.970375816Z"` |
| `gainers` | array |  |
| `gainers[]` | object |  |
| `gainers[].symbol` | string | `"EUROPRATIK"` |
| `gainers[].series` | string | `"EQ"` |
| `gainers[].ltp` | number \| integer | `193.36` |
| `gainers[].prev_price` | number | `161.14` |
| `gainers[].net_price` | integer \| number | `20` |
| `gainers[].perChange` | integer \| number | `20` |
| `gainers[].trade_quantity` | integer | `15067182` |
| `gainers[].turnover` | number | `27315.2942478` |
| `losers` | array |  |
| `losers[]` | object |  |
| `losers[].symbol` | string | `"AMAL"` |
| `losers[].series` | string | `"EQ"` |
| `losers[].ltp` | number \| integer | `746.3` |
| `losers[].prev_price` | number | `932.85` |
| `losers[].net_price` | integer \| number | `-20` |
| `losers[].perChange` | integer \| number | `-20` |
| `losers[].trade_quantity` | integer | `931295` |
| `losers[].turnover` | number | `7775.2888255` |

**Real response** (lists cut to 2 items):

```json
{
  "segment": "allSec",
  "gainersUpdatedAt": "2026-10-16T17:33:16.441124502Z",
  "losersUpdatedAt": "2026-10-16T17:33:16.970375816Z",
  "gainers": [
    {
      "symbol": "EUROPRATIK",
      "series": "EQ",
      "ltp": 193.36,
      "prev_price": 161.14,
      "net_price": 20,
      "perChange": 20,
      "trade_quantity": 15067182,
      "turnover": 27315.2942478
    },
    {
      "symbol": "TAMBOLIIN",
      "series": "EQ",
      "ltp": 365,
      "prev_price": 304.2,
      "net_price": 19.99,
      "perChange": 19.99,
      "trade_quantity": 143030,
      "turnover": 507.856621
    },
    "… 3 more"
  ],
  "losers": [
    {
      "symbol": "AMAL",
      "series": "EQ",
      "ltp": 746.3,
      "prev_price": 932.85,
      "net_price": -20,
      "perChange": -20,
      "trade_quantity": 931295,
      "turnover": 7775.2888255
    },
    {
      "symbol": "OCCLLTD",
      "series": "EQ",
      "ltp": 196.5,
      "prev_price": 226.15,
      "net_price": -13.11,
      "perChange": -13.11,
      "trade_quantity": 1470087,
      "turnover": 2877.8423112
    },
    "… 3 more"
  ]
}
```

#### A.2.4 `cm_get_data_status`

**Purpose:** freshness of the server's own crawls.  
**Called by:** `NseMcpProvider.data_status` (Data status diagnostics)

**Input schema** (from the server's `tools/list`):

_No arguments._

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `indices` | object |  |
| `indices.gainers` | object |  |
| `indices.gainers.available` | boolean | `true` |
| `indices.gainers.lastCrawled` | string (date-time) | `"2026-10-16T11:59:16.623397142Z"` |
| `indices.loosers` | object |  |
| `indices.loosers.available` | boolean | `true` |
| `indices.loosers.lastCrawled` | string (date-time) | `"2026-10-16T11:59:17.251740392Z"` |
| `crawlIntervalMinutes` | integer | `5` |
| `redisTtlMinutes` | integer | `15` |

**Real response** (lists cut to 2 items):

```json
{
  "indices": {
    "gainers": {"available": true, "lastCrawled": "2026-10-16T11:59:16.623397142Z"},
    "loosers": {"available": true, "lastCrawled": "2026-10-16T11:59:17.251740392Z"}
  },
  "crawlIntervalMinutes": 5,
  "redisTtlMinutes": 15
}
```

### A.3 Zerodha Kite MCP: `https://mcp.kite.trade/mcp`

Fallback source: it fills gaps and cross-checks NSE. The client applies a **read-only allowlist of 7 tools**:
`login, get_profile, search_instruments, get_quotes, get_ohlc, get_ltp, get_historical_data`. The other 15
advertised tools are blocked before any request is sent: `place_order, modify_order, cancel_order, get_orders,
get_order_history, get_order_trades, get_trades, get_positions, get_holdings, get_mf_holdings, get_margins,
place_gtt_order, modify_gtt_order, delete_gtt_order, get_gtts`. Market-data tools need the account holder
to approve a session through the `login` link.

#### A.3.1 `search_instruments`

**Purpose:** resolve `NSE:SYMBOL` to Kite's `instrument_token`. This works without a login.  
**Called by:** `KiteMcpProvider.resolve` (cached; the token is stored as an instrument identifier)

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `filter_on` | string enum id, name, isin, tradingsymbol, underlying | no | Filter on a specific field. (Optional). [id(default)=exch:tradingsymbol, name=nice name of the instrument, tradingsymbol=used to trade in a specific … |
| `from` | number | no | Starting index for pagination (0-based). Default: 0 |
| `limit` | number | no | Maximum number of instruments to return. If not specified, returns all matching instruments. When specified, response includes pagination metadata. |
| `query` | string | yes | Search query |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"query": "NSE:TCS", "filter_on": "id", "limit": 20}
```

**Output schema** (inferred from the real response; the JSON arrives as text in `result.content[0].text`):

| Field | Type | Real example |
|---|---|---|
| `data` | array |  |
| `data[]` | object |  |
| `data[].id` | string | `"NSE:TCS"` |
| `data[].instrument_token` | integer | `2953217` |
| `data[].exchange_token` | integer | `11536` |
| `data[].tradingsymbol` | string | `"TCS"` |
| `data[].exchange` | string | `"NSE"` |
| `data[].isin` | string | `"INE467B01029"` |
| `data[].name` | string | `"TATA CONSULTANCY SERV LT"` |
| `data[].series` | string | `"EQ"` |
| `data[].last_price` | integer | `0` |
| `data[].strike` | integer | `0` |
| `data[].tick_size` | number | `0.1` |
| `data[].lot_size` | integer | `1` |
| `data[].multiplier` | integer | `-1` |
| `data[].instrument_type` | string | `"EQ"` |
| `data[].segment` | string | `"NSE"` |
| `data[].delivery_units` | string | `""` |
| `data[].price_units` | string | `""` |
| `data[].freeze_quantity` | integer | `39798` |
| `data[].max_order_quantity` | integer | `39798` |
| `data[].expiry_type` | string | `""` |
| `data[].expiry_date` | string | `""` |
| `data[].exercise_start_date` | string | `""` |
| `data[].exercise_end_date` | string | `""` |
| `data[].issue_date` | string (date) | `"2004-08-25"` |
| `data[].listing_date` | string (date) | `"2004-08-25"` |
| `data[].maturity_date` | string | `""` |
| `data[].lower_circuit_limit` | integer | `2115` |
| `data[].upper_circuit_limit` | integer | `2585` |
| `data[].active` | boolean | `true` |
| `pagination` | object |  |
| `pagination.from` | integer | `0` |
| `pagination.limit` | integer | `20` |
| `pagination.total` | integer | `1` |
| `pagination.has_more` | boolean | `false` |
| `pagination.returned` | integer | `1` |

**Real response** (lists cut to 2 items):

```json
{
  "data": [
    {
      "id": "NSE:TCS",
      "instrument_token": 2953217,
      "exchange_token": 11536,
      "tradingsymbol": "TCS",
      "exchange": "NSE",
      "isin": "INE467B01029",
      "name": "TATA CONSULTANCY SERV LT",
      "series": "EQ",
      "last_price": 0,
      "strike": 0,
      "tick_size": 0.1,
      "lot_size": 1,
      "multiplier": -1,
      "instrument_type": "EQ",
      "segment": "NSE",
      "delivery_units": "",
      "price_units": "",
      "freeze_quantity": 39798,
      "max_order_quantity": 39798,
      "expiry_type": "",
      "expiry_date": "",
      "exercise_start_date": "",
      "exercise_end_date": "",
      "issue_date": "2004-08-25",
      "listing_date": "2004-08-25",
      "maturity_date": "",
      "lower_circuit_limit": 2115,
      "upper_circuit_limit": 2585,
      "active": true
    }
  ],
  "pagination": {"from": 0, "limit": 20, "total": 1, "has_more": false, "returned": 1}
}
```

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `data[].id` | `match key` | must equal `NSE:TCS` |
| `data[].instrument_token` | identifier `instrument_token` | stored in `instrument_identifiers` |
| `data[].tradingsymbol / exchange` | `match check` |  |
| `data[].lot_size, tick_size, isin, segment` | `search results` |  |

#### A.3.2 `get_quotes`

**Purpose:** full quote for up to 500 `exchange:symbol` keys.  
**Called by:** `KiteMcpProvider.get_quotes` ← `CorrelatedMarketData.quote` (in parallel with NSE)

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `instruments` | array<string> | yes | Eg. ['NSE:INFY', 'NSE:SBIN']. This API returns the complete market data snapshot of up to 500 instruments in one go. It includes the quantity, OHLC, … |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"instruments": ["NSE:TCS"]}
```

**Real response:** `isError: true`, text: `Please log in first using the login tool`

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `data.{EXCH:SYM}.last_price` | `last_price` | status `ok` |
| `…ohlc.open/high/low` | `open/high/low` |  |
| `…ohlc.close` | `prev_close` | Kite's `ohlc.close` is the previous close |
| `…volume` | `volume` |  |
| `…timestamp / last_trade_time` | `as_of` |  |
| `…upper/lower_circuit_limit` | `raw only` | kept in `QuoteResult.raw` |

**Output schema: what the parser reads (not captured live).** This tool needs an approved session; at capture time the server answered `Please log in first using the login tool`. The fields below are the ones `KiteMcpProvider.get_quotes` reads, and the demo mock server (`devtools/mock_mcp.py`) returns the same shape:

| Field | Type | Used as |
|---|---|---|
| `data` | object · optional wrapper | the map below may also arrive unwrapped |
| `data.{EXCH:SYMBOL}` | object | one entry per requested key, e.g. `NSE:TCS`; unknown keys are absent |
| `….instrument_token` | integer | — |
| `….last_price` | number | `last_price` |
| `….volume` | integer | `volume` |
| `….timestamp` / `….last_trade_time` | string (date-time) | `as_of` |
| `….net_change` | number | — |
| `….ohlc.open` / `.high` / `.low` | number | `open` / `high` / `low` |
| `….ohlc.close` | number | `prev_close` |
| `….upper_circuit_limit` / `….lower_circuit_limit` | number | kept in `QuoteResult.raw` |

#### A.3.3 `get_historical_data`

**Purpose:** daily candles for an instrument token.  
**Called by:** `KiteMcpProvider.get_history` ← `CorrelatedMarketData.history`

**Input schema** (from the server's `tools/list`):

| Argument | Type | Required | Description (from the server) |
|---|---|---|---|
| `continuous` | boolean | no | Get continuous data (for futures and options) (default `False`) |
| `from_date` | string | yes | From date in YYYY-MM-DD HH:MM:SS format |
| `instrument_token` | number | yes | Instrument token (can be obtained from search_instruments tool) |
| `interval` | string enum minute, day, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute | yes | Candle interval |
| `oi` | boolean | no | Include open interest data (default `False`) |
| `to_date` | string | yes | To date in YYYY-MM-DD HH:MM:SS format |

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{"instrument_token": 2953217, "from_date": "2026-10-12 00:00:00", "to_date": "2026-10-16 23:59:59", "interval": "day"}
```

**Real response:** `isError: true`, text: `Failed to get historical data`

**Mapping into the app:**

| Server field | App field | Notes |
|---|---|---|
| `[].date or candles[][0]` | `date` |  |
| `open/high/low/close or candles[][1..4]` | `bar fields` |  |
| `volume or candles[][5]` | `volume` |  |
| `—` | `adjustment` | `split_adjusted` (Kite candles are adjusted) |

**Output schema: what the parser reads (not captured live; needs a session).** It accepts either of two forms:

| Form | Field | Type |
|---|---|---|
| rows | `[]` (or `data[]`) | object |
| rows | `[].date` | string (date-time) |
| rows | `[].open` / `high` / `low` / `close` | number |
| rows | `[].volume` | integer |
| candles | `data.candles[]` (or `candles[]`) | array `[timestamp, open, high, low, close, volume]` |

Real Kite bars *are* stored in the database: section E.2 shows 10 stored TCS bars from `get_historical_data`.

#### A.3.4 `get_profile`

**Purpose:** check whether the session is authorised (the user id only).  
**Called by:** `KiteMcpProvider.auth_state` → Data status "Broker session"

**Input schema** (from the server's `tools/list`):

_No arguments._

**Arguments the app sent** (built by `bind_args` from the schema above):

```json
{}
```

**Real response:** `isError: true`, text: `Failed to execute get_profile`

A tool error here means `login_required`; a transport error means `unavailable`. Only `user_id` is read from the profile. Email and name are not stored or returned.

#### A.3.5 `login`

Takes no arguments. It returns text containing a Kite authorisation URL bound to this MCP session. The app
extracts the URL, shows it on the Data status page, and issues a fresh link whenever the old one is
more than 60 seconds old. The link was not captured here, because it is a live, session-bound URL.

---

## Part B: other sources (not MCP)

### B.1 NSE daily bhavcopy file (whole-market prices)

`GET https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip`: a zip
holding one CSV in the UDiFF format, with one row per security. It is cached on disk, and a day that
is not published is remembered as a holiday. Columns, from the real 2026-10-15 file (3,673 rows), with
the TCS row:

| Column | TCS value | Stored as |
|---|---|---|
| `TradDt` | `2026-10-15` | price_bars.date |
| `BizDt` | `2026-10-15` | — |
| `Sgmt` | `CM` | — |
| `Src` | `NSE` | — |
| `FinInstrmTp` | `STK` | — |
| `FinInstrmId` | `11536` | — |
| `ISIN` | `INE467B01029` | — |
| `TckrSymb` | `TCS` | symbol filter |
| `SctySrs` | `EQ` | series filter (`UNIVERSE_SERIES`) |
| `XpryDt` | `` | — |
| `FininstrmActlXpryDt` | `` | — |
| `StrkPric` | `` | — |
| `OptnTp` | `` | — |
| `FinInstrmNm` | `TATA CONSULTANCY SERV LT` | — |
| `OpnPric` | `2347.20` | price_bars.open |
| `HghPric` | `2386.70` | price_bars.high |
| `LwPric` | `2335.00` | price_bars.low |
| `ClsPric` | `2350.00` | price_bars.close |
| `LastPric` | `2350.00` | — |
| `PrvsClsgPric` | `2304.90` | prev_close (gap check) |
| `UndrlygPric` | `` | — |
| `SttlmPric` | `2350.45` | — |
| `OpnIntrst` | `` | — |
| `ChngInOpnIntrst` | `` | — |
| `TtlTradgVol` | `9150523` | price_bars.volume |
| `TtlTrfVal` | `21607212833.80` | price_bars.traded_value |
| `TtlNbOfTxsExctd` | `265014` | — |
| `SsnId` | `F1` | — |
| `NewBrdLotQty` | `1` | — |
| `Rmks` | `` | — |
| `Rsvd1` | `` | — |
| `Rsvd2` | `` | — |
| `Rsvd3` | `` | — |
| `Rsvd4` | `` | — |

Stored rows get `provider = "nse_bhavcopy_file"` and `adjustment = "split_adjusted"`, after splits and bonuses from NSE's market-wide corporate-actions list are applied.

### B.2 NSE filings: list of financial results

`GET {NSE_FILINGS_BASE_URL}/integrated-filing-results?index=equities&symbol=TCS&type=Integrated Filing- Financials`.
Schema inferred from the recorded fixture `tcs_integrated_filings_2026-10-15.json`:

| Field | Type | Real example |
|---|---|---|
| `data` | array |  |
| `data[]` | object |  |
| `data[].attFileSize` | string \| null | `"0 Bytes"` |
| `data[].audited` | string | `"Audited"` |
| `data[].broadcast_Date` | string | `"08-Oct-2026 22:07:33"` |
| `data[].cmName` | string | `"Tata Consultancy Services Limited"` |
| `data[].consolidated` | string | `"Consolidated"` |
| `data[].creation_Date` | string | `"08-Oct-2026 22:08:10"` |
| `data[].diff` | string | `"00:00:37"` |
| `data[].ixbrl` | string (url) | `"https://nsearchives.nseindia.com/corporate/i…` |
| `data[].ixbrlFileSize` | string \| null | `"86.96 KB"` |
| `data[].pdf_attach` | string (url) \| null | `"https://nsearchives.nseindia.com/corporate/n…` |
| `data[].qe_Date` | string | `"30-SEP-2026"` |
| `data[].revised_Date` | null (no value in this capture) |  |
| `data[].revision_Remark` | null (no value in this capture) |  |
| `data[].seq_Id` | string | `"200121"` |
| `data[].smName` | string | `"Tata Consultancy Services Limited"` |
| `data[].symbol` | string | `"TCS"` |
| `data[].type` | string | `"Integrated Filing- Financials"` |
| `data[].type_Sub` | string | `"Original"` |
| `data[].xbrl` | string (url) | `"https://nsearchives.nseindia.com/corporate/x…` |
| `data[].xbrlFileSize` | string \| null | `"104.84 KB"` |
| `size` | integer | `20` |
| `page` | integer | `-1` |
| `totalCount` | integer | `14` |

Used fields: `qe_Date` → period end; `consolidated` → basis; `xbrl` → document to read; `broadcast_Date` → `published_at`; `type_Sub` → revision. The newest filing wins for each period and basis.

### B.3 XBRL facts → normalised statement fields

Each `xbrl` document is an Ind-AS XBRL instance. `extract_period()` takes durations for income and
cash-flow items and instants for balance-sheet items. Money is divided by 10^7 to give **INR crore**;
per-share values are kept as filed. Concepts are tried in order, and the first non-zero one wins:

| Statement field | XBRL concepts (in order) |
|---|---|
| `revenue` | `RevenueFromOperations`, `NetPremiumIncome`, `Income` |
| `depreciation` | `DepreciationDepletionAndAmortisationExpense` |
| `interest` | `FinanceCosts`, `InterestExpended` |
| `pbt` | `ProfitBeforeTax`, `ProfitLossFromOrdinaryActivitiesBeforeTax`, `ProfitLossBeforeTax` |
| `tax` | `TaxExpense` |
| `pat` | `ProfitOrLossAttributableToOwnersOfParent`, `ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates`, `ProfitLossForPeriod`, `ProfitLossForThePeriod`, `ProfitLossAfterTaxAndExtraordinaryItems` |
| `eps` | `BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations`, `BasicEarningsLossPerShareFromContinuingOperations`, `BasicEarningsPerShareAfterExtraordinaryItems`, `BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized` |
| `diluted_eps` | `DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations`, `DilutedEarningsLossPerShareFromContinuingOperations`, `DilutedEarningsPerShareAfterExtraordinaryItems`, `BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized` |
| `exceptional_items` | `ExceptionalItemsBeforeTax` |
| `equity` | `EquityAttributableToOwnersOfParent`, `Equity` |
| `cash` | `CashAndCashEquivalents` |
| `short_term_investments` | `CurrentInvestments` |
| `total_assets` | `Assets` |
| `current_assets` | `CurrentAssets` |
| `current_liabilities` | `CurrentLiabilities` |
| `total_liabilities` | `Liabilities` |
| `inventory` | `Inventories` |
| `receivables` | `TradeReceivablesCurrent` |
| `payables` | `TradePayablesCurrent` |
| `goodwill` | `Goodwill` |
| `minority_interest` | `NonControllingInterest` |
| `cfo` | `CashFlowsFromUsedInOperatingActivities` |
| `cfi` | `CashFlowsFromUsedInInvestingActivities` |
| `cff` | `CashFlowsFromUsedInFinancingActivities` |
| `dividends_paid` | `DividendsPaidClassifiedAsFinancingActivities` |
| `buybacks` | `PaymentsToAcquireOrRedeemEntitysShares` |

Derived fields have their formula recorded per period under `derived`:
- `total_debt = BorrowingsCurrent + BorrowingsNoncurrent`
- `capex = purchase of PP&E + intangibles`
- `ebit = profit before exceptional items and tax + finance costs − other income`
- `ebitda = EBIT + D&A`
- `cogs = materials + purchases of stock-in-trade + inventory change`
- `shares_outstanding = paid-up capital / face value`

### B.4 Web search result (ddgs)

After normalisation, each search hit becomes an evidence item. Schema inferred from the 8 real `S*` items in research report 12:

| Field | Type | Real example |
|---|---|---|
| `[]` | object |  |
| `[].kind` | string | `"source"` |
| `[].title` | string | `"TCS Q2 Results 2026 highlights: We onboarded…` |
| `[].url` | string (url) | `"https://www.moneycontrol.com/news/business/e…` |
| `[].published_at` | string (date) | `"2026-10-09"` |
| `[].retrieved_at` | string (date-time) | `"2026-10-15T18:16:21.039621+00:00"` |
| `[].source_type` | string | `"secondary"` |
| `[].snippet` | string | `"During the quarter, TCS won a $1.45 billion …` |

`source_type` is `primary` for exchange, regulator or company domains, and `secondary` otherwise. Publication dates are day-level.

### B.5 LLM structured output (OpenRouter / Hugging Face, OpenAI-compatible)

The model is asked for JSON matching a Pydantic model, and the response is validated against it. Invalid output is retried once with a repair prompt, then the app falls back to a rule-based result. The schemas below come from `model_json_schema()`.

**Research report: `research.LLMReport`**

| Field | Type | Required |
|---|---|---|
| `summary` | Point | yes |
| `pros` | array<Point> | yes |
| `cons` | array<Point> | yes |
| `catalysts` | array<Point> | no |
| `risks` | array<Point> | yes |
| `valuation_context` | Point | yes |
| `technical_picture` | Point | yes |
| `sector_context` | Point | yes |
| `news` | array<Point> | no |
| `invalidation` | array<Point> | yes |
| `missing_data` | array<string> | no |
| `uncertainty` | string | yes |
| **Point** | | |
| `Point.text` | string | yes |
| `Point.evidence` | array<string> | no |

**Concall commitments: `concall.CommitmentList`**

| Field | Type | Required |
|---|---|---|
| `commitments` | array<Commitment> | yes |
| **Commitment** | | |
| `Commitment.id` | string | yes |
| `Commitment.commitment` | string | yes |
| `Commitment.category` | enum guidance, capex, capacity, margin, revenue, debt, product, orders, costs, strategy, other | no |
| `Commitment.importance` | enum high, medium, low | no |
| `Commitment.target` | string \| null | no |
| `Commitment.quote` | string | yes |

**Concall verdicts: `concall.AssessedList`**

| Field | Type | Required |
|---|---|---|
| `assessments` | array<Assessed> | yes |
| **Assessed** | | |
| `Assessed.id` | string | yes |
| `Assessed.verdict` | enum delivered, partially_delivered, in_progress, deferred, not_delivered, not_addressed | yes |
| `Assessed.evidence_quote` | string \| null | no |
| `Assessed.web_source_ids` | array<string> | no |
| `Assessed.rationale` | string | yes |

**Ask plan: `ask.AskPlan`**

| Field | Type | Required |
|---|---|---|
| `intent` | enum instrument, compare, screen, movers, market, sector, unknown | yes |
| `symbols` | array<string> | no |
| `filters` | array<PlanFilter> | no |
| `sectors` | array<string> | no |
| `sort_metric` | string \| null | no |
| `sort_direction` | enum asc, desc | no |
| `lookback_days` | integer \| null | no |
| `limit` | integer | no |
| **PlanFilter** | | |
| `PlanFilter.metric` | string | yes |
| `PlanFilter.op` | enum gt, gte, lt, lte, between | yes |
| `PlanFilter.value` | number | yes |
| `PlanFilter.value2` | number \| null | no |

**Ask answer: `ask.AskAnswer`**

| Field | Type | Required |
|---|---|---|
| `answer` | string | yes |
| `points` | array<AnswerPoint> | no |
| `caveats` | array<string> | no |
| **AnswerPoint** | | |
| `AnswerPoint.text` | string | yes |
| `AnswerPoint.evidence_ids` | array<string> | no |

**Theme refinement: `theme_ai.RefinePlan`**

| Field | Type | Required |
|---|---|---|
| `filters` | array<RefineFilter> | no |
| `sectors` | array<string> | no |
| `rank_by` | string \| null | no |
| `rank_direction` | enum asc, desc | no |
| `limit` | integer | no |
| `explanation` | string | no |
| **RefineFilter** | | |
| `RefineFilter.metric` | string | yes |
| `RefineFilter.op` | enum gt, gte, lt, lte, between | yes |
| `RefineFilter.value` | number | yes |
| `RefineFilter.value2` | number \| null | no |

Every `evidence` id must be an id from the evidence bundle the model was given. Statements citing nothing that was retrieved, or quoting numbers absent from the cited evidence, are dropped (section E.5).

---

## Part C: the app's internal normalised objects

Every source is converted into these objects before reconciliation and storage. They are defined in `connectors/base.py`, `domain/provenance.py`, `services/market_data.py` and `domain/reconcile.py`.

**`DataPoint`**: one value from one provider, with full provenance

| Field | Type |
|---|---|
| `field` | `str` |
| `value` | `float | None` |
| `provider` | `str` |
| `retrieved_at` | `datetime` |
| `as_of` | `datetime | None` |
| `currency` | `str | None` |
| `unit` | `str | None` |
| `adjustment` | `str` |
| `source_id` | `str | None` |
| `status` | `str` |
| `reason` | `str | None` |
| `is_demo` | `bool` |

**`QuoteResult`**: a provider's quote: a map of field to DataPoint

| Field | Type |
|---|---|
| `symbol` | `str` |
| `fields` | `dict[str, DataPoint]` |
| `provider` | `str` |
| `retrieved_at` | `datetime` |
| `raw` | `dict[str, Any]` |

**`BarsResult`**: a provider's daily bars; `frame` columns are open, high, low, close, volume, plus prev_close and traded_value when supplied

| Field | Type |
|---|---|
| `symbol` | `str` |
| `frame` | `pd.DataFrame` |
| `provider` | `str` |
| `source_id` | `str` |
| `retrieved_at` | `datetime` |
| `adjustment` | `str` |
| `currency` | `str` |
| `is_demo` | `bool` |
| `warnings` | `list[str]` |

**`SourceStatus`**: the outcome of asking one provider for one dataset; persisted to `data_source_records`

| Field | Type |
|---|---|
| `provider` | `str` |
| `dataset` | `str` |
| `status` | `str` |
| `reason` | `str | None` |
| `rows` | `int | None` |
| `source_id` | `str | None` |
| `adjustment` | `str | None` |
| `last_bar` | `str | None` |

**`HistoryBundle`**: merged history returned by `CorrelatedMarketData.history`

| Field | Type |
|---|---|
| `symbol` | `str` |
| `frame` | `pd.DataFrame` |
| `conflicts` | `list[dict[str, Any]]` |
| `coverage` | `dict[str, int]` |
| `sources` | `list[SourceStatus]` |
| `actions` | `list[CorporateAction]` |
| `warnings` | `list[str]` |
| `is_demo` | `bool` |

**`Reconciled`**: result of reconciling one field across providers

| Field | Type |
|---|---|
| `field` | `str` |
| `value` | `float | None` |
| `provider` | `str | None` |
| `status` | `str` |
| `point` | `DataPoint | None` |
| `conflict` | `Conflict | None` |
| `filled_from_fallback` | `bool` |
| `reason` | `str | None` |

**`Conflict`**: a recorded disagreement

| Field | Type |
|---|---|
| `field` | `str` |
| `values` | `dict[str, float]` |
| `diff_pct` | `float` |
| `chosen` | `str` |
| `tolerance_pct` | `float` |

**`status` values used everywhere:** `ok`, `delayed` (NSE live data), `missing`, `stale`, `unavailable`
(upstream failure), `not_supported` (no advertised tool provides it), `login_required` (Kite),
`not_configured`, `insufficient_history`, `not_meaningful` (for example, a negative denominator) and
`not_applicable` (for example, an industrial ratio for a bank).

**Database tables written:**
- `price_bars`: `(instrument_id, date)` is unique; prices are `numeric(18,4)`; each row stores `provider`, `adjustment` and `retrieved_at`.
- `corporate_actions`
- `source_conflicts`
- `data_source_records`: one per provider and dataset fetch, with `status` and `reason`.
- `financial_statement_*`
- `metric_snapshots`: all metrics plus the score, versioned.
- `research_reports`, `concall_*`, `job_runs` and `audit_events`.

---

## Part D: what the frontend receives

### D.0 Common rules

- The browser calls only `/api/v1/*` on its own origin (`frontend/src/api/client.ts`). No provider URL or key reaches it.
- **Envelope:** every data response starts with `data_mode` (`"demo"` or `"live"`), `is_demo` (boolean) and
  `retrieved_at` (date-time). Most also carry `as_of`, `data_state` (`ok`, `missing`, `stale` or `unavailable`)
  and `data_state_reason`.
- **Errors:** any non-2xx status returns `{"error": {"code": string, "message": string, "details": any, "request_id": string}}`
  plus an `x-request-id` header. `client.ts` turns this into an `ApiError` and shows the request id.
- **Validated at runtime by zod** (`frontend/src/api/schemas.ts`). Only three responses are validated, and
  extra fields are allowed:
  - `ScreenerSchema`: `items[].{instrument_id, symbol, name, sector, values, missing, stance?, risk_label?}`, `total`, `universe`, `page`, `page_size`, `columns[]`, `as_of`, `computed_at`, `excluded_for_missing_filter_metric`
  - `RotationSchema`: `snapshot_id`, `sectors.{name}.{status, quadrant?, trail[]{date, rs_ratio, rs_momentum}?, reason?}`
  - `SourcesSchema`: `connectors[].{key, label, state}`, `capabilities`, `coverage.{instruments, last_bar}`

  Every other response is typed `any` in the pages, so the tables below are the effective contract.
- A missing value is always `null` with a status and reason; it is never `0`.

| Page (`frontend/src/pages`) | Endpoints it calls |
|---|---|
| Layout (header) | `GET /sources/status` |
| Overview | `GET /overview`, `GET /watchlists` |
| Screener | `GET /screener/metrics`, `GET /sectors`, `GET /screener` |
| Instrument | `GET /instruments/{ref}`, `GET /instruments/{ref}/metrics`, `GET /instruments/{ref}/financials`, `GET /instruments/{ref}/history` |
| Research | `GET /instruments/{ref}`, `GET /instruments/{ref}/research`, `POST /research/stock` |
| Concalls | `GET /concalls`, `GET /instruments/{ref}/concalls`, `POST …/concalls`, `POST …/concalls/auto`, `POST …/concalls/analyze` |
| Rotation | `GET /sector-rotation`, `GET /sector-rotation/snapshots` |
| Themes | `GET /themes`, `GET /themes/{id}`, `GET /themes/{id}/snapshots`, `GET /screener/metrics`, `POST /themes/interpret`, `POST /themes` |
| Watchlists | `GET /watchlists`, `GET /alerts`, `GET /themes`, `GET /sectors`, `GET /screener/metrics` |
| Status | `GET /sources/status`, `GET /jobs`, `GET /audit/events`, `POST /sources/broker/login-link`, `POST /jobs/refresh-all` |
| Search box | `GET /instruments?q=…` |

### D.1 `GET /api/v1/instruments/NSE:TCS`

Instrument header, quote, score, entry setup, themes, peers. The quote is merged live from NSE and Kite (section E.1); the score is read from the latest metric snapshot.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:38.673014+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `instrument` | object |  |
| `instrument.id` | integer | `79` |
| `instrument.exchange` | string | `"NSE"` |
| `instrument.symbol` | string | `"TCS"` |
| `instrument.name` | string | `"Tata Consultancy Services"` |
| `instrument.isin` | string | `"INE467B01029"` |
| `instrument.instrument_type` | string | `"EQ"` |
| `instrument.currency` | string | `"INR"` |
| `instrument.sector` | string | `"Information Technology"` |
| `instrument.industry` | string | `"Computers - Software"` |
| `instrument.sector_kind` | string | `"industrial"` |
| `instrument.lot_size` | integer | `1` |
| `instrument.trading_status` | string | `"active"` |
| `instrument.classification_source` | string | `"reference_file"` |
| `instrument.is_demo` | boolean | `false` |
| `instrument.identifiers` | array |  |
| `instrument.identifiers[]` | object |  |
| `instrument.identifiers[].provider` | string | `"kite_mcp"` |
| `instrument.identifiers[].kind` | string | `"instrument_token"` |
| `instrument.identifiers[].value` | string | `"2953217"` |
| `instrument.updated_at` | string (date-time) | `"2026-10-16T06:37:24.460937+00:00"` |
| `quote` | object |  |
| `quote.symbol` | string | `"TCS"` |
| `quote.status` | string | `"ok"` |
| `quote.fields` | object | → keys: `last_price`, `open`, `high`, `low`, `prev_close`, `volume`, `change_pct` |
| `quote.fields.{field}` | object |  |
| `quote.fields.{field}.value` | number | `2465.0` |
| `quote.fields.{field}.provider` | string | `"nse_mcp"` |
| `quote.fields.{field}.status` | string | `"delayed"` |
| `quote.fields.{field}.reason` | string | `"NSE MCP live data is delayed by a few minute…` |
| `quote.fields.{field}.as_of` | string (date-time) | `"2026-10-16T16:00:28+05:30"` |
| `quote.fields.{field}.retrieved_at` | string (date-time) | `"2026-10-16T12:00:38.548633+00:00"` |
| `quote.fields.{field}.currency` | string \| null | `"INR"` |
| `quote.fields.{field}.source_id` | string | `"cm_get_stock_quote"` |
| `quote.fields.{field}.filled_from_fallback` | boolean | `false` |
| `quote.fields.{field}.conflict` | null (no value in this capture) |  |
| `quote.conflicts` | array |  |
| `quote.sources` | array |  |
| `quote.sources[]` | object |  |
| `quote.sources[].provider` | string | `"nse_mcp"` |
| `quote.sources[].dataset` | string | `"quote"` |
| `quote.sources[].status` | string | `"ok"` |
| `quote.sources[].reason` | string \| null | `"broker login required: authorise the Kite se…` |
| `quote.sources[].rows` | null (no value in this capture) |  |
| `quote.sources[].source_id` | string \| null | `"cm_get_stock_quote"` |
| `quote.sources[].adjustment` | null (no value in this capture) |  |
| `quote.sources[].last_bar` | null (no value in this capture) |  |
| `quote.is_demo` | boolean | `false` |
| `quote.currency` | string | `"INR"` |
| `quote.cache` | string | `"miss"` |
| `snapshot` | object |  |
| `snapshot.as_of` | string (date) | `"2026-10-16"` |
| `snapshot.computed_at` | string (date-time) | `"2026-10-16T06:53:55.854799+00:00"` |
| `snapshot.score` | object |  |
| `snapshot.score.overall` | number | `69.2` |
| `snapshot.score.partial_overall` | null (no value in this capture) |  |
| `snapshot.score.complete` | boolean | `true` |
| `snapshot.score.disclosure` | string | `"All five pillars have sufficient coverage; e…` |
| `snapshot.score.pillars_available` | integer | `5` |
| `snapshot.score.band` | string | `"Upper-middle"` |
| `snapshot.score.stance` | string | `"Favorable"` |
| `snapshot.score.stance_rule` | string | `"Favorable: score >= 65. Mixed: 45 to below 6…` |
| `snapshot.score.completeness_pct` | number | `100.0` |
| `snapshot.score.missing_metrics` | array |  |
| `snapshot.score.confidence` | string | `"High"` |
| `snapshot.score.confidence_rule` | string | `"High: data completeness >= 85% and newest pr…` |
| `snapshot.score.risk` | object |  |
| `snapshot.score.risk.label` | string | `"High"` |
| `snapshot.score.risk.criteria` | string | `"Worst bucket across three measures. 1Y volat…` |
| `snapshot.score.risk.drivers` | object |  |
| `snapshot.score.risk.drivers.volatility` | string | `"Moderate"` |
| `snapshot.score.risk.drivers.max_drawdown` | string | `"High"` |
| `snapshot.score.risk.drivers.beta` | string | `"Low"` |
| `snapshot.score.pillars` | object | → keys: `fundamentals`, `valuation`, `momentum`, `risk`, `context` |
| `snapshot.score.pillars.{pillar}` | object |  |
| `snapshot.score.pillars.{pillar}.label` | string | `"Fundamentals and financial quality"` |
| `snapshot.score.pillars.{pillar}.score` | number | `74.3` |
| `snapshot.score.pillars.{pillar}.sufficient` | boolean | `true` |
| `snapshot.score.pillars.{pillar}.weight` | number | `0.2` |
| `snapshot.score.pillars.{pillar}.contribution` | number | `14.87` |
| `snapshot.score.pillars.{pillar}.metrics_used` | integer | `8` |
| `snapshot.score.pillars.{pillar}.metrics_total` | integer | `8` |
| `snapshot.score.pillars.{pillar}.metrics` | array |  |
| `snapshot.score.pillars.{pillar}.metrics[]` | object |  |
| `snapshot.score.pillars.{pillar}.metrics[].name` | string | `"roe"` |
| `snapshot.score.pillars.{pillar}.metrics[].label` | string | `"ROE"` |
| `snapshot.score.pillars.{pillar}.metrics[].value` | number | `48.72373710370502` |
| `snapshot.score.pillars.{pillar}.metrics[].score` | number | `97.9` |
| `snapshot.score.pillars.{pillar}.metrics[].higher_is_better` | boolean | `true` |
| `snapshot.score.pillars.{pillar}.metrics[].peer_scope` | string | `"universe"` |
| `snapshot.score.top_positive` | array |  |
| `snapshot.score.top_positive[]` | object |  |
| `snapshot.score.top_positive[].metric` | string | `"avg_traded_value"` |
| `snapshot.score.top_positive[].value` | number | `10894921847.224998` |
| `snapshot.score.top_positive[].score` | number | `99.7` |
| `snapshot.score.top_positive[].peer_scope` | string | `"universe"` |
| `snapshot.score.top_negative` | array |  |
| `snapshot.score.top_negative[]` | object |  |
| `snapshot.score.top_negative[].metric` | string | `"pb"` |
| `snapshot.score.top_negative[].value` | number | `8.213187243565832` |
| `snapshot.score.top_negative[].score` | number | `12.3` |
| `snapshot.score.top_negative[].peer_scope` | string | `"sector"` |
| `snapshot.score.entry_setup` | object |  |
| `snapshot.score.entry_setup.state` | string | `"Unfavorable"` |
| `snapshot.score.entry_setup.conditions` | array |  |
| `snapshot.score.entry_setup.conditions[]` | object |  |
| `snapshot.score.entry_setup.conditions[].key` | string | `"trend"` |
| `snapshot.score.entry_setup.conditions[].label` | string | `"Price trend"` |
| `snapshot.score.entry_setup.conditions[].rule` | string | `"close > SMA50 > SMA200"` |
| `snapshot.score.entry_setup.conditions[].passed` | boolean | `false` |
| `snapshot.score.entry_setup.conditions[].detail` | string | `"close 2,433.10, SMA50 2,240.99, SMA200 2,465…` |
| `snapshot.score.entry_setup.wait_for` | array<string> |  |
| `snapshot.score.entry_setup.invalidation` | object |  |
| `snapshot.score.entry_setup.invalidation.condition` | string | `"A daily close below SMA200 (2,465.91), or 6-…` |
| `snapshot.score.entry_setup.invalidation.level` | number | `2465.905` |
| `snapshot.score.entry_setup.invalidation.method` | string | `"200-day simple moving average of adjusted cl…` |
| `snapshot.score.entry_setup.reference_levels` | object |  |
| `snapshot.score.entry_setup.reference_levels.sma_50` | number | `2240.986` |
| `snapshot.score.entry_setup.reference_levels.sma_200` | number | `2465.905` |
| `snapshot.score.entry_setup.reference_levels.atr_14` | number | `72.20254898449663` |
| `snapshot.score.entry_setup.reference_levels.two_atr_below_close` | number | `2288.6949020310067` |
| `snapshot.score.entry_setup.entry_zone` | null (no value in this capture) |  |
| `snapshot.score.entry_setup.entry_zone_reason` | string | `"No backtested entry-zone method is configure…` |
| `snapshot.score.entry_setup.rules` | string | `"(version 'entry-v1')\n- Insufficient data: n…` |
| `snapshot.score.entry_setup.disclaimer` | string | `"General research signal based on disclosed r…` |
| `snapshot.score.entry_setup.version` | string | `"entry-v1"` |
| `snapshot.score.entry_setup.reasons` | array<string> |  |
| `snapshot.score.freshness` | string | `"fresh"` |
| `snapshot.score.peer_universe` | object |  |
| `snapshot.score.peer_universe.size` | integer | `2576` |
| `snapshot.score.peer_universe.sector` | string | `"Information Technology"` |
| `snapshot.score.peer_universe.exchange` | string | `"NSE"` |
| `snapshot.score.last_bar` | string (date) | `"2026-10-16"` |
| `snapshot.score.bars` | integer | `689` |
| `snapshot.score.data_age_days` | integer | `0` |
| `snapshot.score.bar_providers` | object |  |
| `snapshot.score.bar_providers.nse_bhavcopy_file` | integer | `679` |
| `snapshot.score.bar_providers.kite_mcp` | integer | `10` |
| `snapshot.score.fundamentals` | object |  |
| `snapshot.score.fundamentals.periods` | integer | `2` |
| `snapshot.score.fundamentals.latest_period_end` | string (date) | `"2026-03-31"` |
| `snapshot.score.fundamentals.earnings_basis` | string | `"TTM to 2026-09-30 (sum of the latest four qu…` |
| `snapshot.score.fundamentals.source` | string | `"NSE filings (consolidated)"` |
| `snapshot.score.fundamentals.currency` | string | `"INR"` |
| `snapshot.score.fundamentals.unit` | string | `"crore"` |
| `snapshot.score.fundamentals.ingested_at` | string (date-time) | `"2026-10-15T17:30:44.122147+00:00"` |
| `snapshot.score.fundamentals.is_demo` | boolean | `false` |
| `snapshot.score.fundamentals.source_url` | string (url) | `"https://nsearchives.nseindia.com/corporate/x…` |
| `snapshot.score.fundamentals.other_sources` | array |  |
| `snapshot.calculation_version` | object | → keys: `fundamentals`, `valuation`, `technicals`, `risk`, `score`, `entry`, `rrg`, `adjust`, … (9 total) |
| `snapshot.calculation_version.{key}` | string | `"fundamentals-v1"` |
| `snapshot.is_demo` | boolean | `false` |
| `themes` | array |  |
| `themes[]` | object |  |
| `themes[].theme_id` | integer | `39` |
| `themes[].slug` | string | `"net-cash-businesses"` |
| `themes[].name` | string | `"Net-cash businesses"` |
| `themes[].category` | string | `"Value and valuation"` |
| `themes[].rank` | integer | `4` |
| `themes[].period` | string | `"2026-10"` |
| `themes[].snapshot_id` | integer | `301` |
| `peers` | array |  |
| `peers[]` | object |  |
| `peers[].instrument_id` | integer | `158` |
| `peers[].symbol` | string | `"ACCELYA"` |
| `peers[].name` | string | `"Accelya Solutions India Limited"` |
| `peers[].close` | number | `1095.0` |
| `peers[].overall_score` | number \| null | `65.1` |
| `peers[].return_3m` | number | `-4.848800834202294` |
| `peers[].return_12m` | number \| null | `-26.81948807057408` |
| `peers[].pe` | number \| null | `17.136150234741784` |
| `peers[].roe` | number \| null | `36.14060016331233` |
| `peers[].volatility_252d` | number \| null | `25.356056836604026` |
| `peers[].partial_score` | number \| null | `51.8` |
| `data_state` | string | `"ok"` |
| `data_state_reason` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:38.673014+00:00",
  "as_of": "2026-10-16",
  "instrument": {
    "id": 79,
    "exchange": "NSE",
    "symbol": "TCS",
    "name": "Tata Consultancy Services",
    "isin": "INE467B01029",
    "instrument_type": "EQ",
    "currency": "INR",
    "sector": "Information Technology",
    "industry": "Computers - Software",
    "sector_kind": "industrial",
    "lot_size": 1,
    "trading_status": "active",
    "classification_source": "reference_file",
    "is_demo": false,
    "identifiers": [{"provider": "kite_mcp", "kind": "instrument_token", "value": "2953217"}],
    "updated_at": "2026-10-16T06:37:24.460937+00:00"
  },
  "quote": {
    "symbol": "TCS",
    "status": "ok",
    "fields": {
      "last_price": {
        "value": 2465.0,
        "provider": "nse_mcp",
        "status": "delayed",
        "reason": "NSE MCP live data is delayed by a few minutes",
        "as_of": "2026-10-16T16:00:28+05:30",
        "retrieved_at": "2026-10-16T12:00:38.548633+00:00",
        "currency": "INR",
        "source_id": "cm_get_stock_quote",
        "filled_from_fallback": false,
        "conflict": null
      },
      "… 6 more": "…"
    },
    "conflicts": [],
    "sources": [
      {
        "provider": "nse_mcp",
        "dataset": "quote",
        "status": "ok",
        "reason": null,
        "rows": null,
        "source_id": "cm_get_stock_quote",
        "adjustment": null,
        "last_bar": null
      },
      "… 1 more"
    ],
    "is_demo": false,
    "currency": "INR",
    "cache": "miss"
  },
  "snapshot": {
    "as_of": "2026-10-16",
    "computed_at": "2026-10-16T06:53:55.854799+00:00",
    "score": {
      "overall": 69.2,
      "partial_overall": null,
      "complete": true,
      "disclosure": "All five pillars have sufficient coverage; each contributes 20%.",
      "pillars_available": 5,
      "band": "Upper-middle",
      "stance": "Favorable",
      "stance_rule": "Favorable: score >= 65. Mixed: 45 to below 65. Unfavorable: below 45. Insufficient data: …",
      "completeness_pct": 100.0,
      "missing_metrics": [],
      "confidence": "High",
      "confidence_rule": "High: data completeness >= 85% and newest price bar <= 3 days old. Medium: completeness >…",
      "risk": {
        "label": "High",
        "criteria": "Worst bucket across three measures. 1Y volatility: <20% Low, <35% Moderate, <55% High, el…",
        "drivers": {"volatility": "Moderate", "max_drawdown": "High", "beta": "Low"}
      },
      "pillars": {
        "fundamentals": {
          "label": "Fundamentals and financial quality",
          "score": 74.3,
          "sufficient": true,
          "weight": 0.2,
          "contribution": 14.87,
          "metrics_used": 8,
          "metrics_total": 8,
          "metrics": ["{…}", "… 7 more"]
        },
        "… 4 more": "…"
      },
      "top_positive": [
        {"metric": "avg_traded_value", "value": 10894921847.224998, "score": 99.7, "peer_scope": "universe"},
        "… 2 more"
      ],
      "top_negative": [{"metric": "pb", "value": 8.213187243565832, "score": 12.3, "peer_scope": "sector"}, "… 2 more"],
      "entry_setup": {
        "state": "Unfavorable",
        "conditions": ["{…}", "… 7 more"],
        "wait_for": ["Price trend: needs close > SMA50 > SMA200 (close 2,433.10, SMA50 2,240.99, SMA200 2,465.9…", "… 1 more"],
        "invalidation": {
          "condition": "A daily close below SMA200 (2,465.91), or 6-month relative strength turning negative, wou…",
          "level": 2465.905,
          "method": "200-day simple moving average of adjusted closes"
        },
        "reference_levels": {
          "sma_50": 2240.986,
          "sma_200": 2465.905,
          "atr_14": 72.20254898449663,
          "two_atr_below_close": 2288.6949020310067
        },
        "entry_zone": null,
        "entry_zone_reason": "No backtested entry-zone method is configured, so no entry zone or target price is shown.",
        "rules": "(version `entry-v1`)\n- Insufficient data: no price, price data older than `MAX_AGE_DAYS`,…",
        "disclaimer": "General research signal based on disclosed rules. It is not personalised advice, not a re…",
        "version": "entry-v1",
        "reasons": ["close is below SMA200 with a negative 6-month return"]
      },
      "freshness": "fresh",
      "peer_universe": {"size": 2576, "sector": "Information Technology", "exchange": "NSE"},
      "last_bar": "2026-10-16",
      "bars": 689,
      "data_age_days": 0,
      "bar_providers": {"nse_bhavcopy_file": 679, "kite_mcp": 10},
      "fundamentals": {
        "periods": 2,
        "latest_period_end": "2026-03-31",
        "earnings_basis": "TTM to 2026-09-30 (sum of the latest four quarterly EPS as filed)",
        "source": "NSE filings (consolidated)",
        "currency": "INR",
        "unit": "crore",
        "ingested_at": "2026-10-15T17:30:44.122147+00:00",
        "is_demo": false,
        "source_url": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1734607_081020261…",
        "other_sources": []
      }
    },
    "calculation_version": {"fundamentals": "fundamentals-v1", "… 8 more": "…"},
    "is_demo": false
  },
  "themes": [
    {
      "theme_id": 39,
      "slug": "net-cash-businesses",
      "name": "Net-cash businesses",
      "category": "Value and valuation",
      "rank": 4,
      "period": "2026-10",
      "snapshot_id": 301
    },
    "… 6 more"
  ],
  "peers": [
    {
      "instrument_id": 158,
      "symbol": "ACCELYA",
      "name": "Accelya Solutions India Limited",
      "close": 1095.0,
      "overall_score": 65.1,
      "return_3m": -4.848800834202294,
      "return_12m": -26.81948807057408,
      "pe": 17.136150234741784,
      "roe": 36.14060016331233,
      "volatility_252d": 25.356056836604026,
      "partial_score": null
    },
    "… 68 more"
  ],
  "data_state": "ok",
  "data_state_reason": null
}
```

</details>

### D.2 `GET /api/v1/instruments/NSE:TCS/history?limit=800`

Price chart and the source lineage panel. Read from `price_bars` and `data_source_records`; no provider call.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.680029+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `instrument` | object | → same as D.1 `instrument` |
| `interval` | string | `"1d"` |
| `currency` | string | `"INR"` |
| `adjustment` | string | `"split_adjusted (splits and bonuses; cash div…` |
| `adjustment_version` | string | `"adjust-v1"` |
| `bars` | array |  |
| `bars[]` | object |  |
| `bars[].date` | string (date) | `"2024-01-01"` |
| `bars[].open` | number | `3790.0` |
| `bars[].high` | number | `3832.0` |
| `bars[].low` | number | `3773.0` |
| `bars[].close` | number | `3811.1` |
| `bars[].volume` | number | `825907.0` |
| `bars[].provider` | string | `"nse_bhavcopy_file"` |
| `coverage` | object |  |
| `coverage.bars` | integer | `689` |
| `coverage.by_provider` | object | → keys: `nse_bhavcopy_file`, `kite_mcp` |
| `coverage.by_provider.{provider}` | integer | `679` |
| `coverage.first` | string (date) | `"2024-01-01"` |
| `coverage.last` | string (date) | `"2026-10-16"` |
| `freshness` | string | `"fresh"` |
| `data_age_days` | integer | `0` |
| `conflicts` | array |  |
| `corporate_actions` | array |  |
| `corporate_actions[]` | object |  |
| `corporate_actions[].ex_date` | string (date) | `"2021-10-14"` |
| `corporate_actions[].kind` | string | `"dividend"` |
| `corporate_actions[].ratio` | number \| null | `7.0` |
| `corporate_actions[].description` | string | `"Interim Dividend - Rs 7 Per Share"` |
| `corporate_actions[].provider` | string | `"nse_mcp"` |
| `lineage` | array |  |
| `lineage[]` | object |  |
| `lineage[].provider` | string | `"kite_mcp"` |
| `lineage[].dataset` | string | `"history"` |
| `lineage[].status` | string | `"login_required"` |
| `lineage[].reason` | string \| null | `"broker login required: authorise the Kite se…` |
| `lineage[].rows` | integer \| null | `10` |
| `lineage[].source_id` | string \| null | `"get_historical_data"` |
| `lineage[].adjustment` | string \| null | `"split_adjusted"` |
| `lineage[].retrieved_at` | string (date-time) | `"2026-10-16T14:05:46.537275+00:00"` |
| `lineage[].detail` | object \| null |  |
| `lineage[].detail.coverage` | object |  |
| `lineage[].detail.coverage.kite_mcp` | integer | `10` |
| `lineage[].detail.coverage.overlap` | integer | `0` |
| `lineage[].detail.coverage.conflicts` | integer | `0` |
| `lineage[].detail.warnings` | array<string> |  |
| `data_state` | string | `"ok"` |
| `data_state_reason` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.680029+00:00",
  "as_of": "2026-10-16",
  "instrument": "see same as D.1 `instrument`",
  "interval": "1d",
  "currency": "INR",
  "adjustment": "split_adjusted (splits and bonuses; cash dividends not adjusted)",
  "adjustment_version": "adjust-v1",
  "bars": [
    {
      "date": "2024-01-01",
      "open": 3790.0,
      "high": 3832.0,
      "low": 3773.0,
      "close": 3811.1,
      "volume": 825907.0,
      "provider": "nse_bhavcopy_file"
    },
    {
      "date": "2024-01-02",
      "open": 3811.1,
      "high": 3811.1,
      "low": 3767.25,
      "close": 3783.2,
      "volume": 1344068.0,
      "provider": "nse_bhavcopy_file"
    },
    "… 687 more"
  ],
  "coverage": {
    "bars": 689,
    "by_provider": {"nse_bhavcopy_file": 679, "kite_mcp": 10},
    "first": "2024-01-01",
    "last": "2026-10-16"
  },
  "freshness": "fresh",
  "data_age_days": 0,
  "conflicts": [],
  "corporate_actions": [
    {
      "ex_date": "2021-10-14",
      "kind": "dividend",
      "ratio": 7.0,
      "description": "Interim Dividend - Rs 7 Per Share",
      "provider": "nse_mcp"
    },
    {
      "ex_date": "2022-01-19",
      "kind": "dividend",
      "ratio": 7.0,
      "description": "Interim Dividend - Rs 7 Per Share",
      "provider": "nse_mcp"
    },
    "… 21 more"
  ],
  "lineage": [
    {
      "provider": "kite_mcp",
      "dataset": "history",
      "status": "login_required",
      "reason": "broker login required: authorise the Kite session from the Data status page",
      "rows": null,
      "source_id": null,
      "adjustment": null,
      "retrieved_at": "2026-10-16T14:05:46.537275+00:00",
      "detail": null
    },
    {
      "provider": "nse_mcp",
      "dataset": "history",
      "status": "unavailable",
      "reason": "nse_bhavcopy: circuit open after repeated failures; retrying later",
      "rows": null,
      "source_id": null,
      "adjustment": null,
      "retrieved_at": "2026-10-16T14:05:46.537275+00:00",
      "detail": null
    },
    "… 10 more"
  ],
  "data_state": "ok",
  "data_state_reason": null
}
```

</details>

### D.3 `GET /api/v1/instruments/NSE:TCS/metrics`

Metrics tab: every metric with formula, inputs and status.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:38.889747+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `instrument` | object | → same as D.1 `instrument` |
| `metrics` | array |  |
| `metrics[]` | object |  |
| `metrics[].name` | string | `"accrual_ratio"` |
| `metrics[].label` | string | `"Accrual ratio"` |
| `metrics[].group` | string | `"fundamentals"` |
| `metrics[].value` | number \| null | `-1.6865447761848649` |
| `metrics[].unit` | string | `"pct"` |
| `metrics[].status` | string | `"ok"` |
| `metrics[].reason` | string \| null | `"inventory_days unavailable: cogs is missing"` |
| `metrics[].formula` | string | `"(PAT - CFO) / average total assets * 100"` |
| `metrics[].inputs` | object | → keys: `pat`, `cfo`, `total_assets`, `prior_total_assets`, `period`, `revenue`, `avg_total_assets`, `window`, … (74 total) |
| `metrics[].inputs.{input}` | number \| integer \| string \| string (date) \| null | `49210.0` |
| `metrics[].higher_is_better` | boolean \| null | `false` |
| `metrics[].kind` | string | `"calculated"` |
| `currency` | string | `"INR"` |
| `monetary_unit` | string | `"INR crore for statement values"` |
| `calculation_version` | object | → keys: `fundamentals`, `valuation`, `technicals`, `risk`, `score`, `entry`, `rrg`, `adjust`, … (9 total) |
| `calculation_version.{key}` | string | `"fundamentals-v1"` |
| `computed_at` | string (date-time) | `"2026-10-16T06:53:55.854799+00:00"` |
| `coverage` | object |  |
| `coverage.available` | integer | `90` |
| `coverage.total` | integer | `105` |
| `data_state` | string | `"ok"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:38.889747+00:00",
  "as_of": "2026-10-16",
  "instrument": "see same as D.1 `instrument`",
  "metrics": [
    {
      "name": "accrual_ratio",
      "label": "Accrual ratio",
      "group": "fundamentals",
      "value": -1.6865447761848649,
      "unit": "pct",
      "status": "ok",
      "reason": null,
      "formula": "(PAT - CFO) / average total assets * 100",
      "inputs": {"pat": 49210.0, "cfo": 52094.0, "… 2 more": "…"},
      "higher_is_better": false,
      "kind": "calculated"
    },
    {
      "name": "adx_14",
      "label": "ADX (14)",
      "group": "price",
      "value": 26.977044967131285,
      "unit": "score",
      "status": "ok",
      "reason": null,
      "formula": "Wilder ADX, period 14",
      "inputs": {"period": 14},
      "higher_is_better": true,
      "kind": "calculated"
    },
    "… 103 more"
  ],
  "currency": "INR",
  "monetary_unit": "INR crore for statement values",
  "calculation_version": {"fundamentals": "fundamentals-v1", "valuation": "valuation-v1", "… 7 more": "…"},
  "computed_at": "2026-10-16T06:53:55.854799+00:00",
  "coverage": {"available": 90, "total": 105},
  "data_state": "ok"
}
```

</details>

### D.4 `GET /api/v1/instruments/NSE:TCS/financials?period_type=annual`

Financials tab (annual). `period_type=quarterly` returns the same shape.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:38.947900+00:00"` |
| `instrument` | object | → same as D.1 `instrument` |
| `period_type` | string | `"annual"` |
| `currency` | string | `"INR"` |
| `unit` | string | `"crore"` |
| `reported_unit` | string | `"INR (XBRL)"` |
| `source` | string | `"NSE filings (consolidated)"` |
| `source_url` | string (url) | `"https://nsearchives.nseindia.com/corporate/x…` |
| `ingested_at` | string (date-time) | `"2026-10-15T17:30:44.122147+00:00"` |
| `data_state` | string | `"ok"` |
| `kind` | string | `"reported"` |
| `periods` | array |  |
| `periods[]` | object |  |
| `periods[].period_end` | string (date) | `"2025-03-31"` |
| `periods[].fiscal_year` | integer | `2025` |
| `periods[].published_at` | string (date) | `"2025-04-11"` |
| `periods[].revenue` | number | `255324.0` |
| `periods[].depreciation` | number | `5242.0` |
| `periods[].interest` | number | `796.0` |
| `periods[].pbt` | number | `65331.0` |
| `periods[].tax` | number | `16534.0` |
| `periods[].pat` | number | `48553.0` |
| `periods[].eps` | number | `134.19` |
| `periods[].diluted_eps` | number | `134.19` |
| `periods[].exceptional_items` | number | `0.0` |
| `periods[].equity` | number | `94756.0` |
| `periods[].cash` | number | `8342.0` |
| `periods[].short_term_investments` | number | `30689.0` |
| `periods[].total_assets` | number | `159629.0` |
| `periods[].current_assets` | number | `123011.0` |
| `periods[].current_liabilities` | number | `53001.0` |
| `periods[].total_liabilities` | number | `63858.0` |
| `periods[].inventory` | number | `21.0` |
| `periods[].receivables` | number | `59046.0` |
| `periods[].payables` | number | `13909.0` |
| `periods[].goodwill` | number | `1860.0` |
| `periods[].minority_interest` | number | `1015.0` |
| `periods[].cfo` | number | `48908.0` |
| `periods[].cfi` | number | `-2318.0` |
| `periods[].cff` | number | `-47438.0` |
| `periods[].dividends_paid` | number | `44962.0` |
| `periods[].buybacks` | number | `0.0` |
| `periods[].short_term_debt` | number | `0.0` |
| `periods[].long_term_debt` | number | `0.0` |
| `periods[].total_debt` | number | `0.0` |
| `periods[].capex` | number | `3937.0` |
| `periods[].ebit` | number | `62165.0` |
| `periods[].ebitda` | number | `67407.0` |
| `periods[].shares_outstanding` | number | `362.0` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:38.947900+00:00",
  "instrument": "see same as D.1 `instrument`",
  "period_type": "annual",
  "currency": "INR",
  "unit": "crore",
  "reported_unit": "INR (XBRL)",
  "source": "NSE filings (consolidated)",
  "source_url": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1648408_09042026114218_WEB.xml",
  "ingested_at": "2026-10-15T17:30:44.122147+00:00",
  "data_state": "ok",
  "kind": "reported",
  "periods": [
    {
      "period_end": "2025-03-31",
      "fiscal_year": 2025,
      "published_at": "2025-04-11",
      "revenue": 255324.0,
      "depreciation": 5242.0,
      "interest": 796.0,
      "pbt": 65331.0,
      "tax": 16534.0,
      "pat": 48553.0,
      "eps": 134.19,
      "diluted_eps": 134.19,
      "exceptional_items": 0.0,
      "equity": 94756.0,
      "cash": 8342.0,
      "short_term_investments": 30689.0,
      "total_assets": 159629.0,
      "current_assets": 123011.0,
      "current_liabilities": 53001.0,
      "total_liabilities": 63858.0,
      "inventory": 21.0,
      "receivables": 59046.0,
      "payables": 13909.0,
      "goodwill": 1860.0,
      "minority_interest": 1015.0,
      "cfo": 48908.0,
      "cfi": -2318.0,
      "cff": -47438.0,
      "dividends_paid": 44962.0,
      "buybacks": 0.0,
      "short_term_debt": 0.0,
      "long_term_debt": 0.0,
      "total_debt": 0.0,
      "capex": 3937.0,
      "ebit": 62165.0,
      "ebitda": 67407.0,
      "shares_outstanding": 362.0
    },
    {
      "period_end": "2026-03-31",
      "fiscal_year": 2026,
      "published_at": "2026-04-09",
      "revenue": 267021.0,
      "depreciation": 5560.0,
      "interest": 1227.0,
      "pbt": 65487.0,
      "tax": 16033.0,
      "pat": 49210.0,
      "eps": 136.01,
      "diluted_eps": 136.01,
      "exceptional_items": -4526.0,
      "equity": 107240.0,
      "cash": 6417.0,
      "short_term_investments": 33770.0,
      "total_assets": 182372.0,
      "current_assets": 135705.0,
      "current_liabilities": 60914.0,
      "total_liabilities": 73894.0,
      "inventory": 29.0,
      "receivables": 67714.0,
      "payables": 14808.0,
      "goodwill": 9108.0,
      "minority_interest": 1238.0,
      "cfo": 52094.0,
      "cfi": -12845.0,
      "cff": -42133.0,
      "dividends_paid": 39571.0,
      "buybacks": 0.0,
      "short_term_debt": 0.0,
      "long_term_debt": 0.0,
      "total_debt": 0.0,
      "capex": 4078.0,
      "ebit": 66838.0,
      "ebitda": 72398.0,
      "shares_outstanding": 362.0
    }
  ]
}
```

</details>

### D.5 `GET /api/v1/instruments/NSE:TCS/research`

Research board. `POST /api/v1/research/stock` with `{"instrument": "NSE:TCS"}` generates a report and returns this same shape (envelope plus the stored report).

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:02:18.692038+00:00"` |
| `report_id` | integer | `12` |
| `version` | string | `"research-v1"` |
| `generated_at` | string (date-time) | `"2026-10-15T18:16:40.995905+00:00"` |
| `mode` | string | `"llm"` |
| `llm` | object |  |
| `llm.available` | boolean | `true` |
| `llm.model_id` | string | `"z-ai/glm-5.3-flash"` |
| `llm.reason` | string | `"The model's summary stated a figure not foun…` |
| `web` | object |  |
| `web.provider` | string | `"ddgs"` |
| `web.available` | boolean | `true` |
| `web.results` | integer | `8` |
| `web.reason` | null (no value in this capture) |  |
| `web.query` | string | `"Tata Consultancy Services TCS NSE results an…` |
| `web.retrieved_at` | string (date-time) | `"2026-10-15T18:16:21.039812+00:00"` |
| `instrument` | object | → id, symbol, exchange, name, sector, currency |
| `quote` | object | → same shape as D.1 `quote` |
| `labels` | object |  |
| `labels.stance` | string | `"Mixed"` |
| `labels.risk` | string | `"High"` |
| `labels.confidence` | string | `"High"` |
| `labels.score_complete` | boolean | `true` |
| `labels.entry_state` | string | `"Unfavorable"` |
| `score` | object | → subset of D.1 `snapshot.score` |
| `entry_setup` | object | → same shape as D.1 `snapshot.score.entry_setup` |
| `report` | object |  |
| `report.pros` | array |  |
| `report.pros[]` | object |  |
| `report.pros[].text` | string | `"Exceptional profitability: ROE 48.72%, ROCE …` |
| `report.pros[].evidence` | array<string> |  |
| `report.pros[].kinds` | array<string> |  |
| `report.cons` | array |  |
| `report.cons[]` | object |  |
| `report.cons[].text` | string | `"Weak longer-term price performance: -8.02% o…` |
| `report.cons[].evidence` | array<string> |  |
| `report.cons[].kinds` | array<string> |  |
| `report.catalysts` | array |  |
| `report.catalysts[]` | object |  |
| `report.catalysts[].text` | string | `"Q2 FY27 results announced October 8, 2026: p…` |
| `report.catalysts[].evidence` | array<string> |  |
| `report.catalysts[].kinds` | array<string> |  |
| `report.risks` | array |  |
| `report.risks[]` | object |  |
| `report.risks[].text` | string | `"High realized volatility (29.38% 1Y) and a s…` |
| `report.risks[].evidence` | array<string> |  |
| `report.risks[].kinds` | array<string> |  |
| `report.news` | array |  |
| `report.news[]` | object |  |
| `report.news[].text` | string | `"Q2 FY27 profit rose 4% QoQ; interim dividend…` |
| `report.news[].evidence` | array<string> |  |
| `report.news[].kinds` | array<string> |  |
| `report.invalidation` | array |  |
| `report.invalidation[]` | object |  |
| `report.invalidation[].text` | string | `"If the price falls back below the 50-day SMA…` |
| `report.invalidation[].evidence` | array<string> |  |
| `report.invalidation[].kinds` | array<string> |  |
| `report.summary` | object |  |
| `report.summary.text` | string | `"Research stance is Mixed with High confidenc…` |
| `report.summary.evidence` | array<string> |  |
| `report.summary.kinds` | array<string> |  |
| `report.valuation_context` | object |  |
| `report.valuation_context.text` | string | `"Valuation on the latest annual figures: P/E …` |
| `report.valuation_context.evidence` | array<string> |  |
| `report.valuation_context.kinds` | array<string> |  |
| `report.technical_picture` | object |  |
| `report.technical_picture.text` | string | `"Price action: Return 3M 6.77%; Return 12M -2…` |
| `report.technical_picture.evidence` | array<string> |  |
| `report.technical_picture.kinds` | array<string> |  |
| `report.sector_context` | object |  |
| `report.sector_context.text` | string | `"Sector is Information Technology. Sector RS-…` |
| `report.sector_context.evidence` | array<string> |  |
| `report.sector_context.kinds` | array<string> |  |
| `report.missing_data` | array<string> |  |
| `report.uncertainty` | null (no value in this capture) |  |
| `evidence` | object | → keys: `M:close`, `M:return_1m`, `M:return_3m`, `M:return_6m`, `M:return_12m`, `M:rel_strength_6m`, `M:rsi_14`, `M:ma_alignment`, … (56 total) |
| `evidence.{evidence_id}` | object |  |
| `evidence.{evidence_id}.kind` | string | `"calculation"` |
| `evidence.{evidence_id}.label` | string · optional | `"Last close"` |
| `evidence.{evidence_id}.value` | number · optional | `2350.0` |
| `evidence.{evidence_id}.unit` | string \| null · optional | `"price"` |
| `evidence.{evidence_id}.formula` | string \| null · optional | `"last adjusted close"` |
| `evidence.{evidence_id}.as_of` | string (date) \| string (date-time) · optional | `"2026-10-15"` |
| `evidence.{evidence_id}.provider` | string · optional | `"nse_mcp"` |
| `evidence.{evidence_id}.status` | string · optional | `"delayed"` |
| `evidence.{evidence_id}.currency` | string \| null · optional | `"INR"` |
| `evidence.{evidence_id}.title` | string · optional | `"TCS Q2 Results 2026 highlights: We onboarded…` |
| `evidence.{evidence_id}.url` | string (url) · optional | `"https://www.moneycontrol.com/news/business/e…` |
| `evidence.{evidence_id}.published_at` | string (date) · optional | `"2026-10-09"` |
| `evidence.{evidence_id}.retrieved_at` | string (date-time) · optional | `"2026-10-15T18:16:21.039621+00:00"` |
| `evidence.{evidence_id}.source_type` | string · optional | `"secondary"` |
| `evidence.{evidence_id}.snippet` | string · optional | `"During the quarter, TCS won a $1.45 billion …` |
| `grounding` | object |  |
| `grounding.points_total` | integer | `32` |
| `grounding.points_dropped` | integer | `3` |
| `grounding.dropped` | array |  |
| `grounding.dropped[]` | object |  |
| `grounding.dropped[].section` | string | `"summary"` |
| `grounding.dropped[].text` | string | `"Tata Consultancy Services (NSE: TCS) shows a…` |
| `grounding.dropped[].reason` | string | `"states number(s) not present in the cited ev…` |
| `metrics_as_of` | string (date) | `"2026-10-15"` |
| `disclaimer` | string | `"Research output for information only. Scores…` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:02:18.692038+00:00",
  "report_id": 12,
  "version": "research-v1",
  "generated_at": "2026-10-15T18:16:40.995905+00:00",
  "mode": "llm",
  "llm": {
    "available": true,
    "model_id": "z-ai/glm-5.3-flash",
    "reason": "The model's summary stated a figure not found in its cited evidence, so the rule-based su…"
  },
  "web": {
    "provider": "ddgs",
    "available": true,
    "results": 8,
    "reason": null,
    "query": "Tata Consultancy Services TCS NSE results announcement",
    "retrieved_at": "2026-10-15T18:16:21.039812+00:00"
  },
  "instrument": "see id, symbol, exchange, name, sector, currency",
  "quote": "see same shape as D.1 `quote`",
  "labels": {"stance": "Mixed", "risk": "High", "confidence": "High", "score_complete": true, "entry_state": "Unfavorable"},
  "score": "see subset of D.1 `snapshot.score`",
  "entry_setup": "see same shape as D.1 `snapshot.score.entry_setup`",
  "report": {
    "pros": [
      {
        "text": "Exceptional profitability: ROE 48.72%, ROCE 55.03%, EBITDA margin 27.11%, PAT margin 18.4…",
        "evidence": ["M:roe", "… 3 more"],
        "kinds": ["calculation"]
      },
      "… 5 more"
    ],
    "cons": [
      {
        "text": "Weak longer-term price performance: -8.02% over 6M, -20.97% over 12M, and -29.85% below t…",
        "evidence": ["M:return_6m", "… 4 more"],
        "kinds": ["calculation", "… 1 more"]
      },
      "… 4 more"
    ],
    "catalysts": [
      {
        "text": "Q2 FY27 results announced October 8, 2026: profit up 4% QoQ and an interim dividend of Rs…",
        "evidence": ["S2", "… 1 more"],
        "kinds": ["source"]
      },
      "… 3 more"
    ],
    "risks": [
      {
        "text": "High realized volatility (29.38% 1Y) and a severe max drawdown of -40.37% over the past y…",
        "evidence": ["M:volatility_252d", "… 1 more"],
        "kinds": ["calculation"]
      },
      "… 4 more"
    ],
    "news": [
      {
        "text": "Q2 FY27 profit rose 4% QoQ; interim dividend of Rs 12/share declared with record date the…",
        "evidence": ["S2", "… 1 more"],
        "kinds": ["source"]
      },
      "… 3 more"
    ],
    "invalidation": [
      {
        "text": "If the price falls back below the 50-day SMA (2240.58), the recent 1M/3M recovery would l…",
        "evidence": ["M:sma_50", "… 2 more"],
        "kinds": ["calculation"]
      },
      "… 3 more"
    ],
    "summary": {
      "text": "Research stance is Mixed with High confidence, from deterministic rules over the availabl…",
      "evidence": ["M:overall_score"],
      "kinds": ["calculation"]
    },
    "valuation_context": {
      "text": "Valuation on the latest annual figures: P/E (TTM) 17.28x; P/B 7.93x; EV/EBITDA 11.68x; FC…",
      "evidence": ["M:pe", "… 4 more"],
      "kinds": ["calculation"]
    },
    "technical_picture": {
      "text": "Price action: Return 3M 6.77%; Return 12M -20.97%; Relative strength 6M vs benchmark -3.0…",
      "evidence": ["M:return_3m", "… 5 more"],
      "kinds": ["calculation"]
    },
    "sector_context": {
      "text": "Sector is Information Technology. Sector RS-Ratio is 105.29 and RS-Momentum is 104.31, su…",
      "evidence": ["M:sector_rs_ratio", "… 3 more"],
      "kinds": ["calculation"]
    },
    "missing_data": ["revenue_cagr_3y (flagged missing in evidence)", "… 5 more"],
    "uncertainty": null
  },
  "evidence": {
    "M:close": {
      "kind": "calculation",
      "label": "Last close",
      "value": 2350.0,
      "unit": "price",
      "formula": "last adjusted close",
      "as_of": "2026-10-15"
    },
    "… 55 more": "…"
  },
  "grounding": {
    "points_total": 32,
    "points_dropped": 3,
    "dropped": [
      {
        "section": "summary",
        "text": "Tata Consultancy Services (NSE: TCS) shows a Mixed stance with High confidence and an Unf…",
        "reason": "states number(s) not present in the cited evidence: 48.72, 29.85"
      },
      "… 2 more"
    ]
  },
  "metrics_as_of": "2026-10-15",
  "disclaimer": "Research output for information only. Scores and labels are general research classificati…"
}
```

</details>

### D.6 `GET /api/v1/instruments/NSE:TCS/concalls`

Concall analyzer for one company.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:41.511681+00:00"` |
| `instrument` | object | → id, symbol, name |
| `transcripts` | array |  |
| `transcripts[]` | object |  |
| `transcripts[].id` | integer | `3` |
| `transcripts[].fiscal_year` | integer | `2027` |
| `transcripts[].quarter` | integer | `2` |
| `transcripts[].label` | string | `"Q2 FY2027"` |
| `transcripts[].call_date` | string (date) | `"2026-10-14"` |
| `transcripts[].source` | string | `"NSE filing: Tata Consultancy Services Limite…` |
| `transcripts[].source_url` | string (url) | `"https://nsearchives.nseindia.com/corporate/T…` |
| `transcripts[].chars` | integer | `52193` |
| `transcripts[].sha256` | string | `"c87664365909"` |
| `transcripts[].uploaded_at` | string (date-time) | `"2026-10-15T17:39:19.591037+00:00"` |
| `transcripts[].is_demo` | boolean | `false` |
| `analyses` | array |  |
| `analyses[]` | object |  |
| `analyses[].analysis_id` | integer | `5` |
| `analyses[].created_at` | string (date-time) | `"2026-10-15T18:15:48.707902+00:00"` |
| `analyses[].mode` | string | `"llm"` |
| `analyses[].label` | string | `"Weak follow-through"` |
| `analyses[].score` | number | `40.0` |
| `analyses[].confidence` | string | `"Low"` |
| `analyses[].current` | string | `"Q2 FY2027"` |
| `analyses[].previous` | string | `"Q1 FY2027"` |
| `latest` | object |  |
| `latest.analysis_id` | integer | `5` |
| `latest.created_at` | string (date-time) | `"2026-10-15T18:15:48.707902+00:00"` |
| `latest.mode` | string | `"llm"` |
| `latest.llm` | object |  |
| `latest.llm.available` | boolean | `true` |
| `latest.llm.model_id` | string | `"z-ai/glm-5.3-flash"` |
| `latest.llm.reason` | null (no value in this capture) |  |
| `latest.previous` | object |  |
| `latest.previous.id` | integer | `4` |
| `latest.previous.fiscal_year` | integer | `2027` |
| `latest.previous.quarter` | integer | `1` |
| `latest.previous.label` | string | `"Q1 FY2027"` |
| `latest.previous.call_date` | string (date) | `"2026-07-15"` |
| `latest.previous.source` | string | `"NSE filing: Tata Consultancy Services Limite…` |
| `latest.previous.source_url` | string (url) | `"https://nsearchives.nseindia.com/corporate/T…` |
| `latest.previous.chars` | integer | `46187` |
| `latest.previous.sha256` | string | `"1c20f973df63"` |
| `latest.previous.uploaded_at` | string (date-time) | `"2026-10-15T17:39:20.215331+00:00"` |
| `latest.previous.is_demo` | boolean | `false` |
| `latest.current` | object |  |
| `latest.current.id` | integer | `3` |
| `latest.current.fiscal_year` | integer | `2027` |
| `latest.current.quarter` | integer | `2` |
| `latest.current.label` | string | `"Q2 FY2027"` |
| `latest.current.call_date` | string (date) | `"2026-10-14"` |
| `latest.current.source` | string | `"NSE filing: Tata Consultancy Services Limite…` |
| `latest.current.source_url` | string (url) | `"https://nsearchives.nseindia.com/corporate/T…` |
| `latest.current.chars` | integer | `52193` |
| `latest.current.sha256` | string | `"c87664365909"` |
| `latest.current.uploaded_at` | string (date-time) | `"2026-10-15T17:39:19.591037+00:00"` |
| `latest.current.is_demo` | boolean | `false` |
| `latest.assessments` | array |  |
| `latest.assessments[]` | object |  |
| `latest.assessments[].id` | string | `"c1"` |
| `latest.assessments[].commitment` | string | `"Exit FY27 quarters at operating margin of 25…` |
| `latest.assessments[].category` | string | `"margin"` |
| `latest.assessments[].importance` | string | `"high"` |
| `latest.assessments[].target` | string \| null | `"25%+ operating margin exit rate"` |
| `latest.assessments[].quote` | string | `"We want to exit at 25% plus and strive to ac…` |
| `latest.assessments[].verdict` | string | `"in_progress"` |
| `latest.assessments[].evidence_quote` | null (no value in this capture) |  |
| `latest.assessments[].evidence_source` | string \| null | `"web"` |
| `latest.assessments[].web_sources` | array |  |
| `latest.assessments[].web_sources[]` | object |  |
| `latest.assessments[].web_sources[].id` | string | `"W1"` |
| `latest.assessments[].web_sources[].title` | string | `"TCS Expects To Recoup Margin As AI Investmen…` |
| `latest.assessments[].web_sources[].url` | string (url) | `"https://www.msn.com/en-in/news/other/tcs-exp…` |
| `latest.assessments[].web_sources[].published_at` | string (date) \| null | `"2026-10-09"` |
| `latest.assessments[].web_sources[].source_type` | string | `"secondary"` |
| `latest.assessments[].rationale` | string | `"The FY27 exit target of 25%+ is not yet due …` |
| `latest.assessments[].grounded` | boolean | `true` |
| `latest.assessments[].grounding_note` | string \| null | `"the evidence quote was not found verbatim in…` |
| `latest.assessments[].credit` | number | `0.5` |
| `latest.assessments[].weight` | number | `3.0` |
| `latest.web` | object |  |
| `latest.web.available` | boolean | `true` |
| `latest.web.provider` | string | `"ddgs"` |
| `latest.web.queries` | integer | `6` |
| `latest.web.results` | integer | `7` |
| `latest.web.errors` | array |  |
| `latest.web.retrieved_at` | string (date-time) | `"2026-10-15T18:15:18.241799+00:00"` |
| `latest.score` | object |  |
| `latest.score.value` | number | `40.0` |
| `latest.score.label` | string | `"Weak follow-through"` |
| `latest.score.status` | string | `"ok"` |
| `latest.score.assessed` | integer | `6` |
| `latest.score.excluded_ungrounded` | integer | `6` |
| `latest.score.counts` | object |  |
| `latest.score.counts.in_progress` | integer | `2` |
| `latest.score.counts.partially_delivered` | integer | `2` |
| `latest.score.counts.not_addressed` | integer | `2` |
| `latest.score.confidence` | string | `"Low"` |
| `latest.score.formula` | string | `"sum(importance weight x verdict credit) / su…` |
| `latest.score.credit_table` | object |  |
| `latest.score.credit_table.delivered` | number | `1.0` |
| `latest.score.credit_table.partially_delivered` | number | `0.5` |
| `latest.score.credit_table.in_progress` | number | `0.5` |
| `latest.score.credit_table.deferred` | number | `0.25` |
| `latest.score.credit_table.not_delivered` | number | `0.0` |
| `latest.score.credit_table.not_addressed` | number | `0.0` |
| `latest.score.importance_weights` | object |  |
| `latest.score.importance_weights.high` | number | `3.0` |
| `latest.score.importance_weights.medium` | number | `2.0` |
| `latest.score.importance_weights.low` | number | `1.0` |
| `latest.score.min_commitments` | integer | `3` |
| `latest.score.version` | string | `"concall-v1"` |
| `latest.limitations` | array |  |
| `latest.is_demo` | boolean | `false` |
| `latest.disclaimer` | string | `"Follow-through is assessed from what managem…` |
| `llm` | object |  |
| `llm.available` | boolean | `true` |
| `llm.reason` | null (no value in this capture) |  |
| `nse_filings` | object |  |
| `nse_filings.available` | boolean | `true` |
| `nse_filings.reason` | null (no value in this capture) |  |
| `web_search` | object |  |
| `web_search.available` | boolean | `true` |
| `web_search.provider` | string | `"ddgs"` |
| `how_it_works` | object | → plain-text method description |
| `data_state` | string | `"ok"` |
| `data_state_reason` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:41.511681+00:00",
  "instrument": "see id, symbol, name",
  "transcripts": [
    {
      "id": 3,
      "fiscal_year": 2027,
      "quarter": 2,
      "label": "Q2 FY2027",
      "call_date": "2026-10-14",
      "source": "NSE filing: Tata Consultancy Services Limited has informed the Exchange about Transcript",
      "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_14102026155941_SEInt14102026_signed…",
      "chars": 52193,
      "sha256": "c87664365909",
      "uploaded_at": "2026-10-15T17:39:19.591037+00:00",
      "is_demo": false
    },
    "… 3 more"
  ],
  "analyses": [
    {
      "analysis_id": 5,
      "created_at": "2026-10-15T18:15:48.707902+00:00",
      "mode": "llm",
      "label": "Weak follow-through",
      "score": 40.0,
      "confidence": "Low",
      "current": "Q2 FY2027",
      "previous": "Q1 FY2027"
    },
    "… 4 more"
  ],
  "latest": {
    "analysis_id": 5,
    "created_at": "2026-10-15T18:15:48.707902+00:00",
    "mode": "llm",
    "llm": {"available": true, "model_id": "z-ai/glm-5.3-flash", "reason": null},
    "previous": {
      "id": 4,
      "fiscal_year": 2027,
      "quarter": 1,
      "label": "Q1 FY2027",
      "call_date": "2026-07-15",
      "source": "NSE filing: Tata Consultancy Services Limited has informed the Exchange about Transcript",
      "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_15072026193646_SEInt15072026_Signed…",
      "chars": 46187,
      "sha256": "1c20f973df63",
      "uploaded_at": "2026-10-15T17:39:20.215331+00:00",
      "is_demo": false
    },
    "current": {
      "id": 3,
      "fiscal_year": 2027,
      "quarter": 2,
      "label": "Q2 FY2027",
      "call_date": "2026-10-14",
      "source": "NSE filing: Tata Consultancy Services Limited has informed the Exchange about Transcript",
      "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_14102026155941_SEInt14102026_signed…",
      "chars": 52193,
      "sha256": "c87664365909",
      "uploaded_at": "2026-10-15T17:39:19.591037+00:00",
      "is_demo": false
    },
    "assessments": [
      {
        "id": "c1",
        "commitment": "Exit FY27 quarters at operating margin of 25% or above, striving to achieve it sooner",
        "category": "margin",
        "importance": "high",
        "target": "25%+ operating margin exit rate",
        "quote": "We want to exit at 25% plus and strive to achieve it sooner rather than later.",
        "verdict": "in_progress",
        "evidence_quote": null,
        "evidence_source": "web",
        "web_sources": ["{…}"],
        "rationale": "The FY27 exit target of 25%+ is not yet due (currently Q2 FY27). Management acknowledged …",
        "grounded": true,
        "grounding_note": null,
        "credit": 0.5,
        "weight": 3.0
      },
      "… 11 more"
    ],
    "web": {
      "available": true,
      "provider": "ddgs",
      "queries": 6,
      "results": 7,
      "errors": [],
      "retrieved_at": "2026-10-15T18:15:18.241799+00:00"
    },
    "score": {
      "value": 40.0,
      "label": "Weak follow-through",
      "status": "ok",
      "assessed": 6,
      "excluded_ungrounded": 6,
      "counts": {"in_progress": 2, "partially_delivered": 2, "not_addressed": 2},
      "confidence": "Low",
      "formula": "sum(importance weight x verdict credit) / sum(importance weight) x 100",
      "credit_table": {
        "delivered": 1.0,
        "partially_delivered": 0.5,
        "in_progress": 0.5,
        "deferred": 0.25,
        "not_delivered": 0.0,
        "not_addressed": 0.0
      },
      "importance_weights": {"high": 3.0, "medium": 2.0, "low": 1.0},
      "min_commitments": 3,
      "version": "concall-v1"
    },
    "limitations": [],
    "is_demo": false,
    "disclaimer": "Follow-through is assessed from what management said on the two calls. It is not an audit…"
  },
  "llm": {"available": true, "reason": null},
  "nse_filings": {"available": true, "reason": null},
  "web_search": {"available": true, "provider": "ddgs"},
  "how_it_works": "see plain-text method description",
  "data_state": "ok",
  "data_state_reason": null
}
```

</details>

### D.7 `GET /api/v1/concalls`

Concall overview list.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.629916+00:00"` |
| `llm` | object |  |
| `llm.available` | boolean | `true` |
| `llm.reason` | null (no value in this capture) |  |
| `llm.model_id` | string | `"z-ai/glm-5.3-flash"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].instrument_id` | integer | `1020` |
| `items[].symbol` | string | `"HDFCAMC"` |
| `items[].name` | string | `"HDFC Asset Management Company Limited"` |
| `items[].sector` | string | `"Financial Services"` |
| `items[].mode` | string | `"llm"` |
| `items[].score` | number | `50.0` |
| `items[].label` | string | `"Mixed follow-through"` |
| `items[].confidence` | string | `"Low"` |
| `items[].current` | string | `"Q1 FY2027"` |
| `items[].previous` | string | `"Q4 FY2026"` |
| `items[].created_at` | string (date-time) | `"2026-10-15T19:00:41.992305+00:00"` |
| `items[].counts` | object |  |
| `items[].counts.in_progress` | integer | `2` |
| `items[].counts.delivered` | integer · optional | `2` |
| `items[].counts.not_addressed` | integer | `3` |
| `items[].is_demo` | boolean | `false` |
| `items[].counts.partially_delivered` | integer · optional | `2` |
| `pending` | array |  |
| `pending[]` | object |  |
| `pending[].instrument_id` | integer | `995` |
| `pending[].symbol` | string | `"HAL"` |
| `pending[].name` | string | `"Hindustan Aeronautics Limited"` |
| `pending[].transcripts` | integer | `4` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.629916+00:00",
  "llm": {"available": true, "reason": null, "model_id": "z-ai/glm-5.3-flash"},
  "items": [
    {
      "instrument_id": 1020,
      "symbol": "HDFCAMC",
      "name": "HDFC Asset Management Company Limited",
      "sector": "Financial Services",
      "mode": "llm",
      "score": 50.0,
      "label": "Mixed follow-through",
      "confidence": "Low",
      "current": "Q1 FY2027",
      "previous": "Q4 FY2026",
      "created_at": "2026-10-15T19:00:41.992305+00:00",
      "counts": {"in_progress": 2, "delivered": 2, "not_addressed": 3},
      "is_demo": false
    },
    {
      "instrument_id": 79,
      "symbol": "TCS",
      "name": "Tata Consultancy Services",
      "sector": "Information Technology",
      "mode": "llm",
      "score": 40.0,
      "label": "Weak follow-through",
      "confidence": "Low",
      "current": "Q2 FY2027",
      "previous": "Q1 FY2027",
      "created_at": "2026-10-15T18:15:48.707902+00:00",
      "counts": {"in_progress": 2, "partially_delivered": 2, "not_addressed": 2},
      "is_demo": false
    }
  ],
  "pending": [{"instrument_id": 995, "symbol": "HAL", "name": "Hindustan Aeronautics Limited", "transcripts": 4}]
}
```

</details>

### D.8 `GET /api/v1/screener`

Screener table (zod: `ScreenerSchema`). Read from the latest metric snapshot.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:38.985627+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].instrument_id` | integer | `83` |
| `items[].symbol` | string | `"TECHM"` |
| `items[].name` | string | `"Tech Mahindra"` |
| `items[].exchange` | string | `"NSE"` |
| `items[].sector` | string | `"Information Technology"` |
| `items[].currency` | string | `"INR"` |
| `items[].values` | object |  |
| `items[].values.close` | number | `1770.0` |
| `items[].values.overall_score` | number | `76.3` |
| `items[].values.fundamentals_score` | number | `72.3` |
| `items[].values.valuation_score` | number | `51.8` |
| `items[].values.momentum_score` | number | `81.0` |
| `items[].values.risk_score` | number | `85.4` |
| `items[].values.context_score` | number | `90.9` |
| `items[].values.return_3m` | number | `12.530993705893568` |
| `items[].values.return_12m` | number | `21.382526402413937` |
| `items[].values.pe` | number | `28.392685274302213` |
| `items[].values.roe` | number | `16.88719463501875` |
| `items[].values.debt_equity` | number \| null | `0.07381970191184316` |
| `items[].values.volatility_252d` | number | `29.160202868539553` |
| `items[].values.completeness_pct` | number | `100.0` |
| `items[].missing` | object | → keys: `debt_equity` |
| `items[].stance` | string | `"Favorable"` |
| `items[].risk_label` | string | `"Moderate"` |
| `items[].confidence` | string | `"High"` |
| `items[].score_complete` | boolean | `true` |
| `items[].partial_score` | null (no value in this capture) |  |
| `items[].entry_state` | string | `"Setup favorable"` |
| `items[].freshness` | string | `"fresh"` |
| `items[].last_bar` | string (date) | `"2026-10-16"` |
| `items[].is_demo` | boolean | `false` |
| `items[].missing.{metric}` | object · optional |  |
| `items[].missing.{metric}.status` | string | `"not_applicable"` |
| `items[].missing.{metric}.reason` | string | `"not applicable to banks, NBFCs or insurers"` |
| `page` | integer | `1` |
| `page_size` | integer | `25` |
| `total` | integer | `2576` |
| `universe` | integer | `2576` |
| `excluded_for_missing_filter_metric` | integer | `0` |
| `sort` | string | `"overall_score"` |
| `order` | string | `"desc"` |
| `columns` | array |  |
| `columns[]` | object |  |
| `columns[].key` | string | `"close"` |
| `columns[].label` | string | `"Last close"` |
| `columns[].unit` | string | `"price"` |
| `columns[].group` | string | `"price"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `computed_at` | string (date-time) | `"2026-10-16T06:53:55.854799+00:00"` |
| `calculation_version` | object | → keys: `fundamentals`, `valuation`, `technicals`, `risk`, `score`, `entry`, `rrg`, `adjust`, … (9 total) |
| `calculation_version.{key}` | string | `"fundamentals-v1"` |
| `exchange` | string | `"NSE"` |
| `currency` | string | `"INR"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:38.985627+00:00",
  "items": [
    {
      "instrument_id": 83,
      "symbol": "TECHM",
      "name": "Tech Mahindra",
      "exchange": "NSE",
      "sector": "Information Technology",
      "currency": "INR",
      "values": {
        "close": 1770.0,
        "overall_score": 76.3,
        "fundamentals_score": 72.3,
        "valuation_score": 51.8,
        "momentum_score": 81.0,
        "risk_score": 85.4,
        "context_score": 90.9,
        "return_3m": 12.530993705893568,
        "return_12m": 21.382526402413937,
        "pe": 28.392685274302213,
        "roe": 16.88719463501875,
        "debt_equity": 0.07381970191184316,
        "volatility_252d": 29.160202868539553,
        "completeness_pct": 100.0
      },
      "missing": {},
      "stance": "Favorable",
      "risk_label": "Moderate",
      "confidence": "High",
      "score_complete": true,
      "partial_score": null,
      "entry_state": "Setup favorable",
      "freshness": "fresh",
      "last_bar": "2026-10-16",
      "is_demo": false
    },
    "… 24 more"
  ],
  "page": 1,
  "page_size": 25,
  "total": 2576,
  "universe": 2576,
  "excluded_for_missing_filter_metric": 0,
  "sort": "overall_score",
  "order": "desc",
  "columns": [{"key": "close", "label": "Last close", "unit": "price", "group": "price"}, "… 13 more"],
  "as_of": "2026-10-16",
  "computed_at": "2026-10-16T06:53:55.854799+00:00",
  "calculation_version": {"fundamentals": "fundamentals-v1", "… 8 more": "…"},
  "exchange": "NSE",
  "currency": "INR"
}
```

</details>

### D.9 `GET /api/v1/screener/metrics`

Filter builder catalog and scoring method.

| Field | Type | Real example |
|---|---|---|
| `metrics` | array |  |
| `metrics[]` | object |  |
| `metrics[].key` | string | `"revenue_growth_yoy"` |
| `metrics[].label` | string | `"Revenue growth YoY"` |
| `metrics[].unit` | string | `"pct"` |
| `metrics[].group` | string | `"fundamentals"` |
| `metrics[].higher_is_better` | boolean \| null | `true` |
| `operators` | array<string> |  |
| `default_columns` | array<string> |  |
| `scoring` | object |  |
| `scoring.version` | string | `"score-v1"` |
| `scoring.pillar_weights` | object |  |
| `scoring.pillar_weights.fundamentals` | number | `0.2` |
| `scoring.pillar_weights.valuation` | number | `0.2` |
| `scoring.pillar_weights.momentum` | number | `0.2` |
| `scoring.pillar_weights.risk` | number | `0.2` |
| `scoring.pillar_weights.context` | number | `0.2` |
| `scoring.pillar_labels` | object |  |
| `scoring.pillar_labels.fundamentals` | string | `"Fundamentals and financial quality"` |
| `scoring.pillar_labels.valuation` | string | `"Valuation"` |
| `scoring.pillar_labels.momentum` | string | `"Price action and momentum"` |
| `scoring.pillar_labels.risk` | string | `"Risk and volatility"` |
| `scoring.pillar_labels.context` | string | `"Market / sector context"` |
| `scoring.method` | object | → plain-text method description |
| `scoring.stance_rule` | string | `"Favorable: score >= 65. Mixed: 45 to below 6…` |
| `scoring.confidence_rule` | string | `"High: data completeness >= 85% and newest pr…` |
| `scoring.min_peers` | integer | `5` |
| `scoring.min_pillar_coverage` | number | `0.5` |
| `scoring.profile` | object |  |
| `scoring.profile.fundamentals` | array |  |
| `scoring.profile.fundamentals[]` | object |  |
| `scoring.profile.fundamentals[].metric` | string | `"roe"` |
| `scoring.profile.fundamentals[].label` | string | `"ROE"` |
| `scoring.profile.fundamentals[].higher_is_better` | boolean | `true` |
| `scoring.profile.fundamentals[].peer_relative` | boolean | `false` |
| `scoring.profile.valuation` | array |  |
| `scoring.profile.valuation[]` | object |  |
| `scoring.profile.valuation[].metric` | string | `"pe"` |
| `scoring.profile.valuation[].label` | string | `"P/E (TTM)"` |
| `scoring.profile.valuation[].higher_is_better` | boolean | `false` |
| `scoring.profile.valuation[].peer_relative` | boolean | `true` |
| `scoring.profile.momentum` | array |  |
| `scoring.profile.momentum[]` | object |  |
| `scoring.profile.momentum[].metric` | string | `"return_3m"` |
| `scoring.profile.momentum[].label` | string | `"Return 3M"` |
| `scoring.profile.momentum[].higher_is_better` | boolean | `true` |
| `scoring.profile.momentum[].peer_relative` | boolean | `false` |
| `scoring.profile.risk` | array |  |
| `scoring.profile.risk[]` | object |  |
| `scoring.profile.risk[].metric` | string | `"volatility_252d"` |
| `scoring.profile.risk[].label` | string | `"Volatility 1Y"` |
| `scoring.profile.risk[].higher_is_better` | boolean | `false` |
| `scoring.profile.risk[].peer_relative` | boolean | `false` |
| `scoring.profile.context` | array |  |
| `scoring.profile.context[]` | object |  |
| `scoring.profile.context[].metric` | string | `"sector_rs_ratio"` |
| `scoring.profile.context[].label` | string | `"Sector RS-Ratio"` |
| `scoring.profile.context[].higher_is_better` | boolean | `true` |
| `scoring.profile.context[].peer_relative` | boolean | `false` |

<details><summary>Real response (cut)</summary>

```json
{
  "metrics": [
    {
      "key": "revenue_growth_yoy",
      "label": "Revenue growth YoY",
      "unit": "pct",
      "group": "fundamentals",
      "higher_is_better": true
    },
    "… 97 more"
  ],
  "operators": ["gt", "… 5 more"],
  "default_columns": ["close", "… 13 more"],
  "scoring": {
    "version": "score-v1",
    "pillar_weights": {"fundamentals": 0.2, "valuation": 0.2, "momentum": 0.2, "risk": 0.2, "context": 0.2},
    "pillar_labels": {
      "fundamentals": "Fundamentals and financial quality",
      "valuation": "Valuation",
      "momentum": "Price action and momentum",
      "risk": "Risk and volatility",
      "context": "Market / sector context"
    },
    "method": "see plain-text method description",
    "stance_rule": "Favorable: score >= 65. Mixed: 45 to below 65. Unfavorable: below 45. Insufficient data: …",
    "confidence_rule": "High: data completeness >= 85% and newest price bar <= 3 days old. Medium: completeness >…",
    "min_peers": 5,
    "min_pillar_coverage": 0.5,
    "profile": {
      "fundamentals": [{"metric": "roe", "label": "ROE", "higher_is_better": true, "peer_relative": false}, "… 7 more"],
      "valuation": [{"metric": "pe", "label": "P/E (TTM)", "higher_is_better": false, "peer_relative": true}, "… 4 more"],
      "momentum": [{"metric": "return_3m", "label": "Return 3M", "higher_is_better": true, "peer_relative": false}, "… 5 more"],
      "risk": [
        {"metric": "volatility_252d", "label": "Volatility 1Y", "higher_is_better": false, "peer_relative": false},
        "… 3 more"
      ],
      "context": [
        {"metric": "sector_rs_ratio", "label": "Sector RS-Ratio", "higher_is_better": true, "peer_relative": false},
        "… 3 more"
      ]
    }
  }
}
```

</details>

### D.10 `GET /api/v1/sectors`

Sector list with rotation quadrant and returns.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.379063+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].sector` | string | `"Automobile"` |
| `items[].instruments` | integer | `87` |
| `items[].avg_score` | number | `52.9` |
| `items[].quadrant` | string | `"Lagging"` |
| `items[].returns` | object |  |
| `items[].returns.1M` | number | `-4.149306620707871` |
| `items[].returns.3M` | number | `-4.878273784813514` |
| `items[].returns.6M` | number | `-2.29316504155177` |
| `items[].returns.12M` | number | `-2.8076267607758054` |
| `items[].series_kind` | string | `"index"` |
| `items[].status` | string | `"ok"` |
| `benchmark` | string | `"NIFTY 50"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.379063+00:00",
  "as_of": "2026-10-16",
  "items": [
    {
      "sector": "Automobile",
      "instruments": 87,
      "avg_score": 52.9,
      "quadrant": "Lagging",
      "returns": {"1M": -4.149306620707871, "3M": -4.878273784813514, "6M": -2.29316504155177, "12M": -2.8076267607758054},
      "series_kind": "index",
      "status": "ok"
    },
    "… 21 more"
  ],
  "benchmark": "NIFTY 50"
}
```

</details>

### D.11 `GET /api/v1/sector-rotation`

RRG chart (zod: `RotationSchema`).

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:40.288015+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `on_demand` | boolean | `false` |
| `snapshot_id` | integer | `3` |
| `slot` | string | `"2026-10-16T00"` |
| `computed_at` | string (date-time) | `"2026-10-16T06:37:49.434001+00:00"` |
| `data_as_of` | string (date) | `"2026-10-16"` |
| `stale` | boolean | `false` |
| `age_hours` | number | `5.4` |
| `refresh_schedule` | string | `"0 */12 * * *"` |
| `version` | string | `"rrg-v1"` |
| `params` | object |  |
| `params.ratio_window` | integer | `12` |
| `params.momentum_window` | integer | `5` |
| `params.trail` | integer | `10` |
| `params.frequency` | string | `"W-FRI"` |
| `benchmark_last_bar` | string (date) | `"2026-10-16"` |
| `sectors` | object | → keys: `Automobile`, `Capital Goods`, `Chemicals`, `Construction`, `Construction Materials`, `Consumer Durables`, `Consumer Services`, `Diversified`, … (22 total) |
| `sectors.{sector}` | object |  |
| `sectors.{sector}.name` | string | `"Automobile"` |
| `sectors.{sector}.status` | string | `"ok"` |
| `sectors.{sector}.quadrant` | string | `"Lagging"` |
| `sectors.{sector}.trail` | array |  |
| `sectors.{sector}.trail[]` | object |  |
| `sectors.{sector}.trail[].date` | string (date) | `"2026-08-14"` |
| `sectors.{sector}.trail[].rs_ratio` | number | `105.42435560848416` |
| `sectors.{sector}.trail[].rs_momentum` | number | `101.25879851455947` |
| `sectors.{sector}.reason` | null (no value in this capture) |  |
| `sectors.{sector}.last_bar` | string (date) | `"2026-10-16"` |
| `sectors.{sector}.returns` | object |  |
| `sectors.{sector}.returns.1M` | number | `-4.149306620707871` |
| `sectors.{sector}.returns.3M` | number | `-4.878273784813514` |
| `sectors.{sector}.returns.6M` | number | `-2.29316504155177` |
| `sectors.{sector}.returns.12M` | number | `-2.8076267607758054` |
| `sectors.{sector}.direction` | string | `"north-east"` |
| `sectors.{sector}.series_kind` | string | `"index"` |
| `sectors.{sector}.series_note` | string | `"sector index NIFTY AUTO"` |
| `sectors.{sector}.members` | integer | `87` |
| `sectors.{sector}.index_symbol` | string \| null | `"NIFTY AUTO"` |
| `benchmark` | string | `"NIFTY 50"` |
| `benchmark_kind` | string | `"index"` |
| `flags` | array<string> |  |
| `method` | object | → plain-text method description |
| `price_basis` | string | `"split-adjusted closes (not total return)"` |
| `interpretation` | object | → plain-text help |
| `coverage` | object |  |
| `coverage.sectors_total` | integer | `22` |
| `coverage.sectors_plotted` | integer | `22` |
| `coverage.composites` | integer | `15` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:40.288015+00:00",
  "as_of": "2026-10-16",
  "on_demand": false,
  "snapshot_id": 3,
  "slot": "2026-10-16T00",
  "computed_at": "2026-10-16T06:37:49.434001+00:00",
  "data_as_of": "2026-10-16",
  "stale": false,
  "age_hours": 5.4,
  "refresh_schedule": "0 */12 * * *",
  "version": "rrg-v1",
  "params": {"ratio_window": 12, "momentum_window": 5, "trail": 10, "frequency": "W-FRI"},
  "benchmark_last_bar": "2026-10-16",
  "sectors": {
    "Automobile": {
      "name": "Automobile",
      "status": "ok",
      "quadrant": "Lagging",
      "trail": [{"date": "2026-08-14", "rs_ratio": 105.42435560848416, "rs_momentum": 101.25879851455947}, "… 9 more"],
      "reason": null,
      "last_bar": "2026-10-16",
      "returns": {"1M": -4.149306620707871, "3M": -4.878273784813514, "6M": -2.29316504155177, "12M": -2.8076267607758054},
      "direction": "north-east",
      "series_kind": "index",
      "series_note": "sector index NIFTY AUTO",
      "members": 87,
      "index_symbol": "NIFTY AUTO"
    },
    "… 21 more": "…"
  },
  "benchmark": "NIFTY 50",
  "benchmark_kind": "index",
  "flags": ["Capital Goods: equal-weight composite of 168 constituent(s) in the tracked universe; no s…", "… 14 more"],
  "method": "see plain-text method description",
  "price_basis": "split-adjusted closes (not total return)",
  "interpretation": "see plain-text help",
  "coverage": {"sectors_total": 22, "sectors_plotted": 22, "composites": 15}
}
```

</details>

### D.12 `GET /api/v1/sector-rotation/snapshots`

Snapshot picker for comparison.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.592346+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].snapshot_id` | integer | `3` |
| `items[].slot` | string | `"2026-10-16T00"` |
| `items[].computed_at` | string (date-time) | `"2026-10-16T06:37:49.434001+00:00"` |
| `items[].data_as_of` | string (date) | `"2026-10-16"` |
| `items[].coverage` | object |  |
| `items[].coverage.sectors_total` | integer | `22` |
| `items[].coverage.sectors_plotted` | integer | `22` |
| `items[].coverage.composites` | integer | `15` |
| `items[].quadrants` | object | → keys: `Automobile`, `Capital Goods`, `Chemicals`, `Construction`, `Construction Materials`, `Consumer Durables`, `Consumer Services`, `Diversified`, … (22 total) |
| `items[].quadrants.{quadrant}` | string | `"Lagging"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.592346+00:00",
  "items": [
    {
      "snapshot_id": 3,
      "slot": "2026-10-16T00",
      "computed_at": "2026-10-16T06:37:49.434001+00:00",
      "data_as_of": "2026-10-16",
      "coverage": {"sectors_total": 22, "sectors_plotted": 22, "composites": 15},
      "quadrants": {"Automobile": "Lagging", "Capital Goods": "Weakening", "… 20 more": "…"}
    },
    {
      "snapshot_id": 2,
      "slot": "2026-10-15T12",
      "computed_at": "2026-10-15T18:49:20.382657+00:00",
      "data_as_of": "2026-10-15",
      "coverage": {"sectors_total": 22, "sectors_plotted": 22, "composites": 15},
      "quadrants": {"Automobile": "Lagging", "Capital Goods": "Weakening", "… 20 more": "…"}
    }
  ]
}
```

</details>

### D.13 `GET /api/v1/overview`

Overview dashboard.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:41.351144+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `benchmark` | object |  |
| `benchmark.symbol` | string | `"NIFTY 50"` |
| `benchmark.close` | number | `23105.7` |
| `benchmark.last_bar` | string (date) | `"2026-10-16"` |
| `benchmark.return_1w` | number | `2.598749136895573` |
| `benchmark.return_1m` | number | `-0.48196195989248514` |
| `benchmark.sparkline` | array<number> |  |
| `benchmark_symbol` | string | `"NIFTY 50"` |
| `universe` | object |  |
| `universe.instruments` | integer | `2576` |
| `universe.complete_scores` | integer | `1054` |
| `universe.stale` | integer | `3` |
| `universe.breadth` | object |  |
| `universe.breadth.advancing_1w` | integer | `1481` |
| `universe.breadth.declining_1w` | integer | `1071` |
| `universe.breadth.instruments` | integer | `2570` |
| `market_breadth` | object |  |
| `market_breadth.state` | string | `"checking"` |
| `market_breadth.reason` | string | `"Checking NSE market breadth in the backgroun…` |
| `sectors` | array |  |
| `sectors[]` | object |  |
| `sectors[].sector` | string | `"Information Technology"` |
| `sectors[].quadrant` | string | `"Leading"` |
| `sectors[].return_1m` | number | `8.7384164757208` |
| `sectors[].return_3m` | number | `8.221448954035026` |
| `sectors[].status` | string | `"ok"` |
| `sectors[].series_kind` | string | `"index"` |
| `rotation` | object |  |
| `rotation.computed_at` | string (date-time) | `"2026-10-16T06:37:49.434001+00:00"` |
| `rotation.data_as_of` | string (date) | `"2026-10-16"` |
| `top_scores` | array |  |
| `top_scores[]` | object |  |
| `top_scores[].instrument_id` | integer | `83` |
| `top_scores[].symbol` | string | `"TECHM"` |
| `top_scores[].name` | string | `"Tech Mahindra"` |
| `top_scores[].sector` | string | `"Information Technology"` |
| `top_scores[].value` | number | `76.3` |
| `top_scores[].close` | number | `1770.0` |
| `top_momentum` | array |  |
| `top_momentum[]` | object |  |
| `top_momentum[].instrument_id` | integer | `1496` |
| `top_momentum[].symbol` | string | `"MBECL"` |
| `top_momentum[].name` | string | `"Mcnally Bharat Engineering Company Limited"` |
| `top_momentum[].sector` | string \| null | `"Capital Goods"` |
| `top_momentum[].value` | number | `7571.7352415026835` |
| `top_momentum[].close` | number | `428.85` |
| `weakest_momentum` | array |  |
| `weakest_momentum[]` | object |  |
| `weakest_momentum[].instrument_id` | integer | `451` |
| `weakest_momentum[].symbol` | string | `"BHAGYANGR"` |
| `weakest_momentum[].name` | string | `"Bhagyanagar India Limited"` |
| `weakest_momentum[].sector` | string \| null | `"Financial Services"` |
| `weakest_momentum[].value` | number | `-86.48544685216054` |
| `weakest_momentum[].close` | number | `52.7` |
| `theme_leaders` | array |  |
| `theme_leaders[]` | object |  |
| `theme_leaders[].theme_id` | integer | `1` |
| `theme_leaders[].theme` | string | `"Price momentum"` |
| `theme_leaders[].category` | string | `"Momentum and trend"` |
| `theme_leaders[].snapshot_id` | integer | `265` |
| `theme_leaders[].period` | string | `"2026-10"` |
| `theme_leaders[].refreshed_at` | string (date-time) | `"2026-10-16T06:38:06.988273+00:00"` |
| `theme_leaders[].symbol` | string | `"TBZ"` |
| `jobs` | array |  |
| `jobs[]` | object |  |
| `jobs[].job_id` | integer | `161` |
| `jobs[].kind` | string | `"alerts"` |
| `jobs[].job_key` | string | `"alerts"` |
| `jobs[].trigger` | string | `"scheduled"` |
| `jobs[].status` | string | `"succeeded"` |
| `jobs[].started_at` | string (date-time) | `"2026-10-16T12:00:00.029322+00:00"` |
| `jobs[].finished_at` | string (date-time) | `"2026-10-16T12:00:01.919182+00:00"` |
| `jobs[].detail` | object |  |
| `jobs[].detail.evaluated` | integer | `1` |
| `jobs[].detail.triggered` | integer | `0` |
| `jobs[].error` | null (no value in this capture) |  |
| `jobs[].requested_by` | null (no value in this capture) |  |
| `data_state` | string | `"ok"` |
| `data_state_reason` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:41.351144+00:00",
  "as_of": "2026-10-16",
  "benchmark": {
    "symbol": "NIFTY 50",
    "close": 23105.7,
    "last_bar": "2026-10-16",
    "return_1w": 2.598749136895573,
    "return_1m": -0.48196195989248514,
    "sparkline": [23869.6, "… 59 more"]
  },
  "benchmark_symbol": "NIFTY 50",
  "universe": {
    "instruments": 2576,
    "complete_scores": 1054,
    "stale": 3,
    "breadth": {"advancing_1w": 1481, "declining_1w": 1071, "instruments": 2570}
  },
  "market_breadth": {"state": "checking", "reason": "Checking NSE market breadth in the background; refresh in a few seconds."},
  "sectors": [
    {
      "sector": "Information Technology",
      "quadrant": "Leading",
      "return_1m": 8.7384164757208,
      "return_3m": 8.221448954035026,
      "status": "ok",
      "series_kind": "index"
    },
    "… 21 more"
  ],
  "rotation": {"computed_at": "2026-10-16T06:37:49.434001+00:00", "data_as_of": "2026-10-16"},
  "top_scores": [
    {
      "instrument_id": 83,
      "symbol": "TECHM",
      "name": "Tech Mahindra",
      "sector": "Information Technology",
      "value": 76.3,
      "close": 1770.0
    },
    "… 4 more"
  ],
  "top_momentum": [
    {
      "instrument_id": 1496,
      "symbol": "MBECL",
      "name": "Mcnally Bharat Engineering Company Limited",
      "sector": "Capital Goods",
      "value": 7571.7352415026835,
      "close": 428.85
    },
    "… 4 more"
  ],
  "weakest_momentum": [
    {
      "instrument_id": 451,
      "symbol": "BHAGYANGR",
      "name": "Bhagyanagar India Limited",
      "sector": null,
      "value": -86.48544685216054,
      "close": 52.7
    },
    "… 4 more"
  ],
  "theme_leaders": [
    {
      "theme_id": 1,
      "theme": "Price momentum",
      "category": "Momentum and trend",
      "snapshot_id": 265,
      "period": "2026-10",
      "refreshed_at": "2026-10-16T06:38:06.988273+00:00",
      "symbol": "TBZ"
    },
    "… 5 more"
  ],
  "jobs": [
    {
      "job_id": 161,
      "kind": "alerts",
      "job_key": "alerts",
      "trigger": "scheduled",
      "status": "succeeded",
      "started_at": "2026-10-16T12:00:00.029322+00:00",
      "finished_at": "2026-10-16T12:00:01.919182+00:00",
      "detail": {"evaluated": 1, "triggered": 0},
      "error": null,
      "requested_by": null
    },
    "… 7 more"
  ],
  "data_state": "ok",
  "data_state_reason": null
}
```

</details>

### D.14 `GET /api/v1/themes`

Theme cards.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.518503+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `1` |
| `items[].slug` | string | `"price-momentum"` |
| `items[].name` | string | `"Price momentum"` |
| `items[].category` | string | `"Momentum and trend"` |
| `items[].description` | string | `"Leaders on blended 1M, 3M, 6M and 12M return…` |
| `items[].methodology` | string | `"Universe: NSE EQ · Avg traded value 20D ≥ 5e…` |
| `items[].interpreted_filters` | array<string> |  |
| `items[].owner` | string | `"system"` |
| `items[].is_builtin` | boolean | `true` |
| `items[].available` | boolean | `true` |
| `items[].requires` | array<string> |  |
| `items[].version` | integer | `1` |
| `items[].archived` | boolean | `false` |
| `items[].scoring_version` | string | `"score-v1"` |
| `items[].created_at` | string (date-time) | `"2026-10-14T06:36:31.816712+00:00"` |
| `items[].updated_at` | string (date-time) | `"2026-10-14T06:36:31.817561+00:00"` |
| `items[].refresh_schedule` | string | `"30 6 1 * *"` |
| `items[].next_refresh` | string (date-time) | `"2026-11-01T06:30:00+05:30"` |
| `items[].last_refreshed` | string (date-time) \| null | `"2026-10-16T06:38:06.988273+00:00"` |
| `items[].latest_snapshot` | object \| null |  |
| `items[].latest_snapshot.id` | integer | `265` |
| `items[].latest_snapshot.period` | string | `"2026-10"` |
| `items[].latest_snapshot.revision` | integer | `7` |
| `items[].latest_snapshot.constituents` | integer | `25` |
| `items[].latest_snapshot.eligible_count` | integer | `455` |
| `items[].leaders` | array<string> |  |
| `categories` | array<string> |  |
| `refresh_schedule` | string | `"30 6 1 * *"` |
| `next_refresh` | string (date-time) | `"2026-11-01T06:30:00+05:30"` |
| `timezone` | string | `"Asia/Kolkata"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.518503+00:00",
  "items": [
    {
      "id": 1,
      "slug": "price-momentum",
      "name": "Price momentum",
      "category": "Momentum and trend",
      "description": "Leaders on blended 1M, 3M, 6M and 12M returns.",
      "methodology": "Universe: NSE EQ · Avg traded value 20D ≥ 5e+07 · Return 6M > 0 · Return 12M > 0 · Rank b…",
      "interpreted_filters": ["Universe: NSE EQ", "… 5 more"],
      "owner": "system",
      "is_builtin": true,
      "available": true,
      "requires": ["prices"],
      "version": 1,
      "archived": false,
      "scoring_version": "score-v1",
      "created_at": "2026-10-14T06:36:31.816712+00:00",
      "updated_at": "2026-10-14T06:36:31.817561+00:00",
      "refresh_schedule": "30 6 1 * *",
      "next_refresh": "2026-11-01T06:30:00+05:30",
      "last_refreshed": "2026-10-16T06:38:06.988273+00:00",
      "latest_snapshot": {"id": 265, "period": "2026-10", "revision": 7, "constituents": 25, "eligible_count": 455},
      "leaders": ["TBZ", "… 2 more"]
    },
    "… 69 more"
  ],
  "categories": ["Momentum and trend", "… 9 more"],
  "refresh_schedule": "30 6 1 * *",
  "next_refresh": "2026-11-01T06:30:00+05:30",
  "timezone": "Asia/Kolkata"
}
```

</details>

### D.15 `GET /api/v1/themes/1`

One theme with its current snapshot and constituents.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.845840+00:00"` |
| `as_of` | string (date) | `"2026-10-16"` |
| `theme` | object |  |
| `theme.id` | integer | `1` |
| `theme.slug` | string | `"price-momentum"` |
| `theme.name` | string | `"Price momentum"` |
| `theme.category` | string | `"Momentum and trend"` |
| `theme.description` | string | `"Leaders on blended 1M, 3M, 6M and 12M return…` |
| `theme.methodology` | string | `"Universe: NSE EQ · Avg traded value 20D ≥ 5e…` |
| `theme.interpreted_filters` | array<string> |  |
| `theme.owner` | string | `"system"` |
| `theme.is_builtin` | boolean | `true` |
| `theme.available` | boolean | `true` |
| `theme.requires` | array<string> |  |
| `theme.version` | integer | `1` |
| `theme.spec` | object |  |
| `theme.spec.name` | string | `"Price momentum"` |
| `theme.spec.universe` | object |  |
| `theme.spec.universe.exchange` | string | `"NSE"` |
| `theme.spec.universe.instrument_types` | array<string> |  |
| `theme.spec.universe.sectors` | null (no value in this capture) |  |
| `theme.spec.universe.min_avg_traded_value` | number | `50000000.0` |
| `theme.spec.filters` | array |  |
| `theme.spec.filters[]` | object |  |
| `theme.spec.filters[].metric` | string | `"return_6m"` |
| `theme.spec.filters[].op` | string | `"gt"` |
| `theme.spec.filters[].value` | number | `0.0` |
| `theme.spec.filters[].value2` | null (no value in this capture) |  |
| `theme.spec.rank_by` | array |  |
| `theme.spec.rank_by[]` | object |  |
| `theme.spec.rank_by[].metric` | string | `"return_1m"` |
| `theme.spec.rank_by[].direction` | string | `"desc"` |
| `theme.spec.limit` | integer | `25` |
| `theme.spec.exclude_red_flags` | boolean | `true` |
| `theme.archived` | boolean | `false` |
| `theme.scoring_version` | string | `"score-v1"` |
| `theme.created_at` | string (date-time) | `"2026-10-14T06:36:31.816712+00:00"` |
| `theme.updated_at` | string (date-time) | `"2026-10-14T06:36:31.817561+00:00"` |
| `theme.refresh_schedule` | string | `"30 6 1 * *"` |
| `theme.next_refresh` | string (date-time) | `"2026-11-01T06:30:00+05:30"` |
| `theme.last_refreshed` | string (date-time) | `"2026-10-16T06:38:06.988273+00:00"` |
| `theme.latest_snapshot` | object |  |
| `theme.latest_snapshot.id` | integer | `265` |
| `theme.latest_snapshot.period` | string | `"2026-10"` |
| `theme.latest_snapshot.revision` | integer | `7` |
| `theme.latest_snapshot.constituents` | integer | `25` |
| `theme.latest_snapshot.eligible_count` | integer | `455` |
| `snapshot` | object |  |
| `snapshot.id` | integer | `265` |
| `snapshot.theme_id` | integer | `1` |
| `snapshot.period` | string | `"2026-10"` |
| `snapshot.revision` | integer | `7` |
| `snapshot.theme_version` | integer | `1` |
| `snapshot.created_at` | string (date-time) | `"2026-10-16T06:38:06.988273+00:00"` |
| `snapshot.metrics_as_of` | string (date) | `"2026-10-16"` |
| `snapshot.universe_count` | integer | `2576` |
| `snapshot.eligible_count` | integer | `455` |
| `snapshot.trigger` | string | `"data_refresh"` |
| `snapshot.versions` | object | → calculation versions, as in D.3 |
| `snapshot.spec` | object | → same shape as `theme.spec` |
| `snapshot.is_demo` | boolean | `false` |
| `snapshot.constituents` | array |  |
| `snapshot.constituents[]` | object |  |
| `snapshot.constituents[].instrument_id` | integer | `2342` |
| `snapshot.constituents[].symbol` | string | `"TBZ"` |
| `snapshot.constituents[].name` | string | `"Tribhovandas Bhimji Zaveri Limited"` |
| `snapshot.constituents[].sector` | string \| null | `"Consumer Durables"` |
| `snapshot.constituents[].stance` | string | `"Mixed"` |
| `snapshot.constituents[].risk_label` | string | `"Very High"` |
| `snapshot.constituents[].rank` | integer | `1` |
| `snapshot.constituents[].score` | number | `99.011` |
| `snapshot.constituents[].values` | object | → metric map, as in D.8 `items[].values` |
| `snapshot.constituents[].price_at_snapshot` | number | `723.0` |
| `snapshot.constituents[].latest_close` | number | `759.15` |
| `snapshot.constituents[].change_since_snapshot_pct` | number | `5.0` |
| `snapshot.exclusions` | array |  |
| `snapshot.exclusions[]` | object |  |
| `snapshot.exclusions[].symbol` | string | `"TATASTEEL"` |
| `snapshot.exclusions[].reason` | string | `"failed: return_6m gt 0 (value -16.1042)"` |
| `snapshot.prices_moved_since_snapshot` | boolean | `true` |
| `snapshot.currency` | string | `"INR"` |
| `versions` | array |  |
| `versions[]` | object |  |
| `versions[].version` | integer | `1` |
| `versions[].created_at` | string (date-time) | `"2026-10-14T06:36:31.818396+00:00"` |
| `versions[].changed_by` | string | `"system"` |
| `metric_labels` | object | → keys: `revenue_growth_yoy`, `ebitda_growth_yoy`, `pat_growth_yoy`, `eps_growth_yoy`, `revenue_cagr_3y`, `pat_cagr_3y`, `revenue_cagr_5y`, `pat_cagr_5y`, … (98 total) |
| `metric_labels.{key}` | object |  |
| `metric_labels.{key}.label` | string | `"Revenue growth YoY"` |
| `metric_labels.{key}.unit` | string | `"pct"` |
| `data_state` | string | `"ok"` |
| `data_state_reason` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.845840+00:00",
  "as_of": "2026-10-16",
  "theme": {
    "id": 1,
    "slug": "price-momentum",
    "name": "Price momentum",
    "category": "Momentum and trend",
    "description": "Leaders on blended 1M, 3M, 6M and 12M returns.",
    "methodology": "Universe: NSE EQ · Avg traded value 20D ≥ 5e+07 · Return 6M > 0 · Return 12M > 0 · Rank b…",
    "interpreted_filters": ["Universe: NSE EQ", "… 5 more"],
    "owner": "system",
    "is_builtin": true,
    "available": true,
    "requires": ["prices"],
    "version": 1,
    "spec": {
      "name": "Price momentum",
      "universe": {"exchange": "NSE", "instrument_types": ["EQ"], "sectors": null, "min_avg_traded_value": 50000000.0},
      "filters": [{"metric": "return_6m", "op": "gt", "value": 0.0, "value2": null}, "… 1 more"],
      "rank_by": [{"metric": "return_1m", "direction": "desc"}, "… 3 more"],
      "limit": 25,
      "exclude_red_flags": true
    },
    "archived": false,
    "scoring_version": "score-v1",
    "created_at": "2026-10-14T06:36:31.816712+00:00",
    "updated_at": "2026-10-14T06:36:31.817561+00:00",
    "refresh_schedule": "30 6 1 * *",
    "next_refresh": "2026-11-01T06:30:00+05:30",
    "last_refreshed": "2026-10-16T06:38:06.988273+00:00",
    "latest_snapshot": {"id": 265, "period": "2026-10", "revision": 7, "constituents": 25, "eligible_count": 455}
  },
  "snapshot": {
    "id": 265,
    "theme_id": 1,
    "period": "2026-10",
    "revision": 7,
    "theme_version": 1,
    "created_at": "2026-10-16T06:38:06.988273+00:00",
    "metrics_as_of": "2026-10-16",
    "universe_count": 2576,
    "eligible_count": 455,
    "trigger": "data_refresh",
    "versions": "see calculation versions, as in D.3",
    "spec": "see same shape as `theme.spec`",
    "is_demo": false,
    "constituents": [
      {
        "instrument_id": 2342,
        "symbol": "TBZ",
        "name": "Tribhovandas Bhimji Zaveri Limited",
        "sector": "Consumer Durables",
        "stance": "Mixed",
        "risk_label": "Very High",
        "rank": 1,
        "score": 99.011,
        "values": "see metric map, as in D.8 `items[].values`",
        "price_at_snapshot": 723.0,
        "latest_close": 759.15,
        "change_since_snapshot_pct": 5.0
      },
      "… 24 more"
    ],
    "exclusions": [{"symbol": "TATASTEEL", "reason": "failed: return_6m gt 0 (value -16.1042)"}, "… 499 more"],
    "prices_moved_since_snapshot": true,
    "currency": "INR"
  },
  "versions": [{"version": 1, "created_at": "2026-10-14T06:36:31.818396+00:00", "changed_by": "system"}],
  "metric_labels": {"revenue_growth_yoy": {"label": "Revenue growth YoY", "unit": "pct"}, "… 97 more": "…"},
  "data_state": "ok",
  "data_state_reason": null
}
```

</details>

### D.16 `GET /api/v1/themes/1/snapshots`

Monthly snapshot history (snapshots are never modified).

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.876438+00:00"` |
| `theme_id` | integer | `1` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `265` |
| `items[].period` | string | `"2026-10"` |
| `items[].revision` | integer | `7` |
| `items[].created_at` | string (date-time) | `"2026-10-16T06:38:06.988273+00:00"` |
| `items[].trigger` | string | `"data_refresh"` |
| `items[].theme_version` | integer | `1` |
| `items[].metrics_as_of` | string (date) | `"2026-10-16"` |
| `items[].constituents` | integer | `25` |
| `items[].eligible_count` | integer | `455` |
| `items[].top` | array<string> |  |
| `items[].versions` | object | → keys: `fundamentals`, `valuation`, `technicals`, `risk`, `score`, `entry`, `rrg`, `adjust`, … (10 total) |
| `items[].versions.{key}` | string | `"fundamentals-v1"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.876438+00:00",
  "theme_id": 1,
  "items": [
    {
      "id": 265,
      "period": "2026-10",
      "revision": 7,
      "created_at": "2026-10-16T06:38:06.988273+00:00",
      "trigger": "data_refresh",
      "theme_version": 1,
      "metrics_as_of": "2026-10-16",
      "constituents": 25,
      "eligible_count": 455,
      "top": ["TBZ", "KABRAEXTRU", "… 3 more"],
      "versions": {"fundamentals": "fundamentals-v1", "valuation": "valuation-v1", "… 8 more": "…"}
    },
    {
      "id": 198,
      "period": "2026-10",
      "revision": 6,
      "created_at": "2026-10-15T18:49:37.928865+00:00",
      "trigger": "data_refresh",
      "theme_version": 1,
      "metrics_as_of": "2026-10-15",
      "constituents": 25,
      "eligible_count": 450,
      "top": ["TBZ", "KABRAEXTRU", "… 3 more"],
      "versions": {"fundamentals": "fundamentals-v1", "valuation": "valuation-v1", "… 8 more": "…"}
    },
    "… 5 more"
  ]
}
```

</details>

### D.17 `GET /api/v1/sources/status`

Data status page and header badge (zod: `SourcesSchema`).

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T12:00:37.394093+00:00"` |
| `connectors` | array |  |
| `connectors[]` | object |  |
| `connectors[].key` | string | `"nse_bhavcopy"` |
| `connectors[].label` | string | `"NSE bhavcopy MCP"` |
| `connectors[].role` | string | `"primary: end-of-day history, corporate actio…` |
| `connectors[].name` | string | `"nse_bhavcopy"` |
| `connectors[].url` | string (url) · optional | `"https://mcp.nseindia.in/bhavcopy/cm/mcp"` |
| `connectors[].state` | string | `"degraded"` |
| `connectors[].breaker` | string · optional | `"half_open"` |
| `connectors[].server` | object · optional |  |
| `connectors[].server.name` | string | `"nse-bhavcopy-redis-mcp"` |
| `connectors[].server.version` | string | `"1.0.0"` |
| `connectors[].tool_count` | integer · optional | `21` |
| `connectors[].tools` | array<string> · optional |  |
| `connectors[].blocked_tools` | array<string> · optional |  |
| `connectors[].discovered_at` | string (date-time) · optional | `"2026-10-15T18:58:15.881954+00:00"` |
| `connectors[].last_success_at` | string (date-time) · optional | `"2026-10-15T19:24:12.903083+00:00"` |
| `connectors[].last_error` | string \| null · optional | `"timeout calling nse_bhavcopy: ReadTimeout"` |
| `connectors[].last_error_at` | string (date-time) · optional | `"2026-10-16T06:53:04.656082+00:00"` |
| `connectors[].latency_ms` | number · optional | `616.5` |
| `connectors[].calls` | integer · optional | `2351` |
| `connectors[].errors` | integer · optional | `1562` |
| `connectors[].retries` | integer · optional | `718` |
| `connectors[].error_rate` | number · optional | `0.6644` |
| `connectors[].auth` | object · optional |  |
| `connectors[].auth.state` | string | `"login_required"` |
| `connectors[].auth.login_url` | null (no value in this capture) |  |
| `connectors[].auth.checked_at` | string (date-time) | `"2026-10-16T11:46:08.451331+00:00"` |
| `connectors[].auth.note` | string | `"Open the link, approve access in Kite, then …` |
| `connectors[].auth.issued_at` | null (no value in this capture) |  |
| `connectors[].instruments_with_data` | integer · optional | `0` |
| `connectors[].directory` | string · optional | `"/Users/rajeshsanapala/Documents/Hackathon/ba…` |
| `connectors[].allow_demo` | boolean · optional | `false` |
| `connectors[].note` | string · optional | `"Statements are loaded from uploaded files; n…` |
| `connectors[].provider` | string · optional | `"OpenRouter"` |
| `connectors[].reason` | null (no value in this capture) · optional |  |
| `connectors[].model_id` | string · optional | `"z-ai/glm-5.3-flash"` |
| `connectors[].base_url` | string (url) · optional | `"https://openrouter.ai/api/v1"` |
| `connectors[].verified` | object · optional |  |
| `connectors[].verified.ok` | boolean | `true` |
| `connectors[].verified.checked_at` | string (date-time) | `"2026-10-15T18:58:18.721345+00:00"` |
| `connectors[].usage` | object · optional |  |
| `connectors[].usage.calls` | integer | `5` |
| `connectors[].usage.errors` | integer | `0` |
| `connectors[].usage.invalid_outputs` | integer | `0` |
| `connectors[].usage.prompt_tokens` | integer | `9129` |
| `connectors[].usage.completion_tokens` | integer | `957` |
| `connectors[].usage.total_tokens` | integer | `10086` |
| `connectors[].usage.cost_usd` | number | `0.00186` |
| `connectors[].usage.latency_ms_avg` | number | `3652.5` |
| `connectors[].usage.last_error` | null (no value in this capture) |  |
| `connectors[].usage.last_call_at` | string (date-time) | `"2026-10-16T05:43:28.594706+00:00"` |
| `connectors[].usage.model_id` | string | `"z-ai/glm-5.3-flash"` |
| `connectors[].usage.day` | string (date) | `"2026-10-16"` |
| `connectors[].usage.daily_budget` | integer | `200` |
| `connectors[].usage.daily_token_budget` | integer | `2000000` |
| `connectors[].usage.note` | string | `"Cost (USD) is summed from the provider's usa…` |
| `capabilities` | object | → keys: `nse_mcp`, `kite_mcp` |
| `capabilities.{provider}` | object | → keys: `history`, `corporate_actions`, `search`, `bulk_quote`, `market_breadth`, `top_movers`, `quote`, `live_movers`, … (14 total) |
| `capabilities.{provider}.{capability}` | object |  |
| `capabilities.{provider}.{capability}.supported` | boolean | `true` |
| `capabilities.{provider}.{capability}.tool` | string \| null | `"get_stock_history"` |
| `capabilities.{provider}.{capability}.reason` | string \| null | `"no tool advertised by NSE CM market MCP prov…` |
| `precedence` | object |  |
| `precedence.default` | array<string> |  |
| `precedence.policy` | string | `"Correlating values from several providers.\n…` |
| `precedence.tolerances_pct` | object |  |
| `precedence.tolerances_pct.close` | number | `0.1` |
| `precedence.tolerances_pct.last_price` | number | `1.0` |
| `precedence.tolerances_pct.volume` | number | `5.0` |
| `coverage` | object |  |
| `coverage.instruments` | integer | `2576` |
| `coverage.bars_by_provider` | object |  |
| `coverage.bars_by_provider.kite_mcp` | integer | `23427` |
| `coverage.bars_by_provider.nse_bhavcopy_file` | integer | `1388347` |
| `coverage.bars_by_provider.nse_mcp` | integer | `3928` |
| `coverage.last_bar` | string (date) | `"2026-10-16"` |
| `coverage.instruments_with_statements` | integer | `2309` |
| `coverage.stale_instruments` | integer | `3` |
| `coverage.instruments_with_metrics` | integer | `2576` |
| `coverage.source_conflicts` | integer | `0` |
| `coverage.recent_source_calls` | array |  |
| `coverage.recent_source_calls[]` | object |  |
| `coverage.recent_source_calls[].provider` | string | `"kite_mcp"` |
| `coverage.recent_source_calls[].dataset` | string | `"history"` |
| `coverage.recent_source_calls[].status` | string | `"unavailable"` |
| `coverage.recent_source_calls[].count` | integer | `804` |
| `terms` | array<string> |  |
| `cache` | object |  |
| `cache.backend` | string | `"RedisCache"` |
| `cache.note` | null (no value in this capture) |  |
| `scheduler` | object |  |
| `scheduler.enabled` | boolean | `true` |
| `scheduler.jobs` | array |  |
| `scheduler.jobs[]` | object |  |
| `scheduler.jobs[].id` | string | `"instruments"` |
| `scheduler.jobs[].description` | string | `"Instrument master sync"` |
| `scheduler.jobs[].cron` | string | `"0 7 * * 1-5"` |
| `scheduler.jobs[].timezone` | string | `"Asia/Kolkata"` |
| `scheduler.jobs[].next_run` | string (date-time) | `"2026-10-17T07:00:00+05:30"` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T12:00:37.394093+00:00",
  "connectors": [
    {
      "key": "nse_bhavcopy",
      "label": "NSE bhavcopy MCP",
      "role": "primary: end-of-day history, corporate actions",
      "name": "nse_bhavcopy",
      "url": "https://mcp.nseindia.in/bhavcopy/cm/mcp",
      "state": "degraded",
      "breaker": "half_open",
      "server": {"name": "nse-bhavcopy-redis-mcp", "version": "1.0.0"},
      "tool_count": 21,
      "tools": ["compare_indices", "… 20 more"],
      "blocked_tools": [],
      "discovered_at": "2026-10-15T18:58:15.881954+00:00",
      "last_success_at": "2026-10-15T19:24:12.903083+00:00",
      "last_error": "timeout calling nse_bhavcopy: ReadTimeout",
      "last_error_at": "2026-10-16T06:53:04.656082+00:00",
      "latency_ms": 616.5,
      "calls": 2351,
      "errors": 1562,
      "retries": 718,
      "error_rate": 0.6644
    },
    "… 5 more"
  ],
  "capabilities": {
    "nse_mcp": {"history": {"supported": true, "tool": "get_stock_history", "reason": null}, "… 12 more": "…"},
    "… 1 more": "…"
  },
  "precedence": {
    "default": ["nse_mcp", "… 1 more"],
    "policy": "Correlating values from several providers.\n\nPolicy `reconcile-v1`\n- Precedence is per fie…",
    "tolerances_pct": {"close": 0.1, "last_price": 1.0, "volume": 5.0}
  },
  "coverage": {
    "instruments": 2576,
    "bars_by_provider": {"kite_mcp": 23427, "nse_bhavcopy_file": 1388347, "nse_mcp": 3928},
    "last_bar": "2026-10-16",
    "instruments_with_statements": 2309,
    "stale_instruments": 3,
    "instruments_with_metrics": 2576,
    "source_conflicts": 0,
    "recent_source_calls": [{"provider": "kite_mcp", "dataset": "history", "status": "unavailable", "count": 804}, "… 10 more"]
  },
  "terms": ["NSE states its MCP data is delayed and for informational and educational use; it is not l…", "… 1 more"],
  "cache": {"backend": "RedisCache", "note": null},
  "scheduler": {
    "enabled": true,
    "jobs": [
      {
        "id": "instruments",
        "description": "Instrument master sync",
        "cron": "0 7 * * 1-5",
        "timezone": "Asia/Kolkata",
        "next_run": "2026-10-17T07:00:00+05:30"
      },
      "… 4 more"
    ]
  }
}
```

</details>

### D.18 `GET /api/v1/jobs`

Job history on the Data status page (polled every 5 s). `GET /jobs/{id}` returns one item of the same shape.

| Field | Type | Real example |
|---|---|---|
| `items` | array |  |
| `items[]` | object |  |
| `items[].job_id` | integer | `161` |
| `items[].kind` | string | `"alerts"` |
| `items[].job_key` | string | `"alerts"` |
| `items[].trigger` | string | `"scheduled"` |
| `items[].status` | string | `"succeeded"` |
| `items[].started_at` | string (date-time) | `"2026-10-16T12:00:00.029322+00:00"` |
| `items[].finished_at` | string (date-time) | `"2026-10-16T12:00:01.919182+00:00"` |
| `items[].detail` | object |  |
| `items[].detail.evaluated` | integer | `1` |
| `items[].detail.triggered` | integer | `0` |
| `items[].error` | null (no value in this capture) |  |
| `items[].requested_by` | null (no value in this capture) |  |

<details><summary>Real response (cut)</summary>

```json
{
  "items": [
    {
      "job_id": 161,
      "kind": "alerts",
      "job_key": "alerts",
      "trigger": "scheduled",
      "status": "succeeded",
      "started_at": "2026-10-16T12:00:00.029322+00:00",
      "finished_at": "2026-10-16T12:00:01.919182+00:00",
      "detail": {"evaluated": 1, "triggered": 0},
      "error": null,
      "requested_by": null
    },
    {
      "job_id": 160,
      "kind": "alerts",
      "job_key": "alerts",
      "trigger": "scheduled",
      "status": "succeeded",
      "started_at": "2026-10-16T11:45:00.025649+00:00",
      "finished_at": "2026-10-16T11:45:01.140743+00:00",
      "detail": {"evaluated": 1, "triggered": 0},
      "error": null,
      "requested_by": null
    },
    "… 1 more"
  ]
}
```

</details>

### D.19 `GET /api/v1/instruments?q=TCS&page_size=8&instrument_type=EQ`

Instrument search box.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:11.037155+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `79` |
| `items[].exchange` | string | `"NSE"` |
| `items[].symbol` | string | `"TCS"` |
| `items[].name` | string | `"Tata Consultancy Services"` |
| `items[].isin` | string | `"INE467B01029"` |
| `items[].instrument_type` | string | `"EQ"` |
| `items[].currency` | string | `"INR"` |
| `items[].sector` | string | `"Information Technology"` |
| `items[].industry` | string | `"Computers - Software"` |
| `items[].sector_kind` | string | `"industrial"` |
| `items[].lot_size` | integer | `1` |
| `items[].trading_status` | string | `"active"` |
| `items[].classification_source` | string | `"reference_file"` |
| `items[].is_demo` | boolean | `false` |
| `items[].identifiers` | array |  |
| `items[].identifiers[]` | object |  |
| `items[].identifiers[].provider` | string | `"kite_mcp"` |
| `items[].identifiers[].kind` | string | `"instrument_token"` |
| `items[].identifiers[].value` | string | `"2953217"` |
| `items[].updated_at` | string (date-time) | `"2026-10-16T06:37:24.460937+00:00"` |
| `page` | integer | `1` |
| `page_size` | integer | `8` |
| `total` | integer | `2` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:11.037155+00:00",
  "items": [
    {
      "id": 79,
      "exchange": "NSE",
      "symbol": "TCS",
      "name": "Tata Consultancy Services",
      "isin": "INE467B01029",
      "instrument_type": "EQ",
      "currency": "INR",
      "sector": "Information Technology",
      "industry": "Computers - Software",
      "sector_kind": "industrial",
      "lot_size": 1,
      "trading_status": "active",
      "classification_source": "reference_file",
      "is_demo": false,
      "identifiers": [{"provider": "kite_mcp", "kind": "instrument_token", "value": "2953217"}],
      "updated_at": "2026-10-16T06:37:24.460937+00:00"
    },
    {
      "id": 2614,
      "exchange": "NSE",
      "symbol": "WSTCSTPAPR",
      "name": "West Coast Paper Mills Limited",
      "isin": "INE976A01021",
      "instrument_type": "EQ",
      "currency": "INR",
      "sector": "Forest Materials",
      "industry": "Paper And Paper Products",
      "sector_kind": "industrial",
      "lot_size": 1,
      "trading_status": "active",
      "classification_source": "NSE industry Paper And Paper Products: 1 of 1 classified peers",
      "is_demo": false,
      "identifiers": [{"provider": "kite_mcp", "kind": "instrument_token", "value": "972545"}],
      "updated_at": "2026-10-16T14:05:46.772768+00:00"
    }
  ],
  "page": 1,
  "page_size": 8,
  "total": 2
}
```

</details>

### D.20 `GET /api/v1/watchlists`

Watchlists page (monitoring only, no orders).

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.549857+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `1` |
| `items[].name` | string | `"klklk"` |
| `items[].created_at` | string (date-time) | `"2026-10-15T18:48:04.329786+00:00"` |
| `items[].items` | array |  |
| `items[].items[]` | object |  |
| `items[].items[].item_id` | integer | `1` |
| `items[].items[].instrument_id` | integer | `189` |
| `items[].items[].symbol` | string | `"AEPL"` |
| `items[].items[].name` | string | `"Artemis Electricals and Projects Limited"` |
| `items[].items[].sector` | null (no value in this capture) |  |
| `items[].items[].note` | null (no value in this capture) |  |
| `items[].items[].close` | number | `15.17` |
| `items[].items[].return_1w` | number | `2.8474576271186436` |
| `items[].items[].return_1m` | number | `-4.4108380592312475` |
| `items[].items[].overall_score` | null (no value in this capture) |  |
| `items[].items[].partial_score` | number | `42.3` |
| `items[].items[].stance` | string | `"Unfavorable"` |
| `items[].items[].risk_label` | string | `"Moderate"` |
| `items[].items[].entry_state` | string | `"Insufficient data"` |
| `items[].items[].freshness` | string | `"fresh"` |
| `items[].items[].last_bar` | string (date) | `"2026-10-16"` |
| `items[].items[].added_at` | string (date-time) | `"2026-10-15T18:48:13.904960+00:00"` |
| `currency` | string | `"INR"` |
| `note` | string | `"Watchlists are for monitoring only. No order…` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.549857+00:00",
  "items": [
    {
      "id": 1,
      "name": "klklk",
      "created_at": "2026-10-15T18:48:04.329786+00:00",
      "items": [
        {
          "item_id": 1,
          "instrument_id": 189,
          "symbol": "AEPL",
          "name": "Artemis Electricals and Projects Limited",
          "sector": null,
          "note": null,
          "close": 15.17,
          "return_1w": 2.8474576271186436,
          "return_1m": -4.4108380592312475,
          "overall_score": null,
          "partial_score": 42.3,
          "stance": "Unfavorable",
          "risk_label": "Moderate",
          "entry_state": "Insufficient data",
          "freshness": "fresh",
          "last_bar": "2026-10-16",
          "added_at": "2026-10-15T18:48:13.904960+00:00"
        }
      ]
    }
  ],
  "currency": "INR",
  "note": "Watchlists are for monitoring only. No orders can be placed from this application."
}
```

</details>

### D.21 `GET /api/v1/alerts`

Alerts and notification history.

| Field | Type | Real example |
|---|---|---|
| `data_mode` | string | `"live"` |
| `is_demo` | boolean | `false` |
| `retrieved_at` | string (date-time) | `"2026-10-16T17:34:12.577573+00:00"` |
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `2` |
| `items[].kind` | string | `"price_move"` |
| `items[].instrument_id` | integer | `69` |
| `items[].symbol` | string | `"HDFCBANK"` |
| `items[].params` | object |  |
| `items[].params.window` | string | `"1W"` |
| `items[].params.pct` | number | `0.1` |
| `items[].status` | string | `"active"` |
| `items[].channel` | string | `"in_app"` |
| `items[].created_at` | string (date-time) | `"2026-10-15T18:21:49.388042+00:00"` |
| `items[].last_triggered_at` | string (date-time) | `"2026-10-16T06:45:01.118615+00:00"` |
| `kinds` | array<string> |  |
| `history` | array |  |
| `history[]` | object |  |
| `history[].id` | integer | `2` |
| `history[].alert_id` | integer | `2` |
| `history[].kind` | string | `"price_move"` |
| `history[].message` | string | `"HDFCBANK moved +3.4% over 1W (threshold 0.1%…` |
| `history[].triggered_at` | string (date-time) | `"2026-10-16T06:45:01.115473+00:00"` |
| `history[].delivery_status` | string | `"recorded"` |
| `delivery_note` | string | `"Notifications are recorded in this in-app hi…` |

<details><summary>Real response (cut)</summary>

```json
{
  "data_mode": "live",
  "is_demo": false,
  "retrieved_at": "2026-10-16T17:34:12.577573+00:00",
  "items": [
    {
      "id": 2,
      "kind": "price_move",
      "instrument_id": 69,
      "symbol": "HDFCBANK",
      "params": {"window": "1W", "pct": 0.1},
      "status": "active",
      "channel": "in_app",
      "created_at": "2026-10-15T18:21:49.388042+00:00",
      "last_triggered_at": "2026-10-16T06:45:01.118615+00:00"
    }
  ],
  "kinds": ["price_move", "indicator_cross", "… 4 more"],
  "history": [
    {
      "id": 2,
      "alert_id": 2,
      "kind": "price_move",
      "message": "HDFCBANK moved +3.4% over 1W (threshold 0.1%), as of 2026-10-16",
      "triggered_at": "2026-10-16T06:45:01.115473+00:00",
      "delivery_status": "recorded"
    },
    {
      "id": 1,
      "alert_id": 2,
      "kind": "price_move",
      "message": "HDFCBANK moved +5.0% over 1W (threshold 0.1%), as of 2026-10-15",
      "triggered_at": "2026-10-15T18:30:00.467018+00:00",
      "delivery_status": "recorded"
    }
  ],
  "delivery_note": "Notifications are recorded in this in-app history. No email, SMS or push channel is configured, so none is cl…"
}
```

</details>

### D.22 `GET /api/v1/audit/events`

Audit trail on the Data status page. Examples are hidden because they contain the user's own questions.

| Field | Type | Real example |
|---|---|---|
| `items` | array |  |
| `items[]` | object |  |
| `items[].id` | integer | `54` |
| `items[].at` | string (date-time) | `"2026-10-16T05:44:41.350872+00:00"` |
| `items[].actor` | string | `"local"` |
| `items[].action` | string | `"concall.auto"` |
| `items[].entity` | string \| null | `"instrument"` |
| `items[].entity_id` | string \| null | `"995"` |
| `items[].detail` | object |  |
| `items[].detail.job_id` | integer · optional |  |
| `items[].request_id` | null (no value in this capture) |  |
| `items[].detail.question` | string · optional |  |
| `items[].detail.intent` | string · optional |  |
| `items[].detail.plan_source` | string · optional |  |
| `items[].detail.answer_source` | string · optional |  |
| `page` | integer | `1` |

### D.23 Error response

`GET /api/v1/instruments/NSE:NOSUCH` returns HTTP 404 with the header `x-request-id: 36151e65e6bb4642`:

```json
{
  "error": {
    "code": "instrument_not_found",
    "message": "NSE:NOSUCH is not in the instrument master.",
    "details": null,
    "request_id": "36151e65e6bb4642"
  }
}
```

### D.24 Request bodies the frontend sends (from the OpenAPI schema)

**`ResearchIn`**

| Field | Type | Required |
|---|---|---|
| `instrument` | string | yes |

**`AskIn`**

| Field | Type | Required |
|---|---|---|
| `question` | string | yes |

**`InterpretIn`**

| Field | Type | Required |
|---|---|---|
| `text` | string | yes |
| `name` | string \| null | no |

**`ThemeCreate`**

| Field | Type | Required |
|---|---|---|
| `spec` | ThemeSpec | yes |
| `description` | string | no |
| `confirmed` | boolean | no |

**`ThemePatch`**

| Field | Type | Required |
|---|---|---|
| `spec` | ThemeSpec \| null | no |
| `description` | string \| null | no |
| `archived` | boolean \| null | no |
| `confirmed` | boolean | no |

**`WatchlistIn`**

| Field | Type | Required |
|---|---|---|
| `name` | string | yes |

**`WatchItemIn`**

| Field | Type | Required |
|---|---|---|
| `instrument` | string | yes |
| `note` | string \| null | no |

**`AlertIn`**

| Field | Type | Required |
|---|---|---|
| `kind` | string | yes |
| `instrument_id` | integer \| null | no |
| `params` | object | no |
| `idempotency_key` | string | yes |

**`AnalyzeIn`**

| Field | Type | Required |
|---|---|---|
| `current_transcript_id` | integer \| null | no |

---

## Part E: end-to-end scenarios (NSE:TCS)

### E.1 Live quote: from NSE MCP to the API

**3a. Raw tool call** (Captured live)

```json
// request
{"jsonrpc": "2.0", "method": "tools/call", "id": 2,
 "params": {"name": "cm_get_stock_quote", "arguments": {"symbol": "TCS"}}}

// response: the payload is JSON inside result.content[0].text
{"jsonrpc": "2.0", "id": 2, "result": {"isError": false, "content": [{"type": "text", "text": "{…below…}"}]}}
```

```json
{"updatedAt": "2026-10-16T11:59:27.105411880Z",
 "stock": {"type": "CM", "symbol": "TCS", "series": "EQ",
           "openPrice": 2382.0, "highPrice": 2484.7, "lowPrice": 2375.7, "preClosePrice": 2350.0,
           "lastTradedPrice": 2465.0, "indicativeClosePrice": 0.0, "change": 115.0, "perChange": 4.89,
           "volume": 12166634, "value": 2968.318030248, "fiftyTwoWeekHigh": 3350.0, "fiftyTwoWeekLow": 1976.8,
           "perChange30d": 12.62, "latestTimestamp": "2026-10-16 16:00:28"}}
```

**3b. Parsing.** `parsing.unwrap_quote` finds the `stock` object. `QUOTE_KEYS` then maps vendor names to the app's field names: `lastTradedPrice` becomes `last_price`, `preClosePrice` becomes `prev_close`, and `perChange` becomes `change_pct`. `latestTimestamp` is read as IST and becomes `as_of`. Each field becomes a `DataPoint`. NSE quotes get the status `delayed`.

**3c. Asking both sources at once.** `CorrelatedMarketData.quote()` asks NSE and Kite in parallel. Each field is then put through `reconcile()`. At capture time Kite needed a login, so only NSE supplied values, and Kite's outcome was recorded alongside.

**3d. What the API returns.** This is the `quote` block of `GET /api/v1/instruments/NSE:TCS`, Captured live. Six of the seven fields are cut.

```json
"quote": {
  "symbol": "TCS", "status": "ok", "currency": "INR", "is_demo": false, "cache": "miss",
  "fields": {
    "last_price": {"value": 2465.0, "provider": "nse_mcp", "status": "delayed",
                   "reason": "NSE MCP live data is delayed by a few minutes",
                   "as_of": "2026-10-16T16:00:28+05:30", "retrieved_at": "2026-10-16T12:00:38.548633+00:00",
                   "currency": "INR", "source_id": "cm_get_stock_quote",
                   "filled_from_fallback": false, "conflict": null},
    "open": {…}, "high": {…}, "low": {…}, "prev_close": {…}, "volume": {…}, "change_pct": {…}},
  "conflicts": [],
  "sources": [
    {"provider": "nse_mcp",  "dataset": "quote", "status": "ok", "reason": null, "source_id": "cm_get_stock_quote", …},
    {"provider": "kite_mcp", "dataset": "quote", "status": "login_required",
     "reason": "broker login required: authorise the Kite session from the Data status page", "source_id": null, …}]}
```

**Caching:** the merged quote is cached for `CACHE_TTL_QUOTE`, 60 s by default. The next call within that time returns `"cache": "hit"`. A result with `status: unavailable` is never cached.

### E.2 Daily history for one symbol (NSE first, Kite fills gaps)

The steps are in `CorrelatedMarketData.history()`, with persistence in `ingestion._write_history()`.

**4a. NSE history, fetched page by page backwards** (Captured live)

```json
// arguments: {"symbol": "TCS", "months": 3, "endDate": "today"}
{"symbol": "TCS", "from_date": "2026-07-16", "to_date": "2026-10-16", "trading_days": 65,
 "source": "in-memory-cache", "query_ms": 0, "chunk_months": 3,
 "next_end_date": "2026-07-15",
 "summary": {"first_close": 2201.0, "last_close": 2465.0, "return_pct": 11.99,
             "period_low": 2032.4, "period_high": 2495.0, "avg_daily_vol": 3852620},
 "data": [
   {"symbol": "TCS", "series": "EQ", "date": "2026-07-16", "open": 2206.0, "high": 2231.4, "low": 2187.8, "close": 2201.0,
    "prevClose": 2189.2, "ltp": 2199.1, "volume": 5197595, "totalTradedValue": 11501044792.2, "mktType": "N", …},
   …,
   {"symbol": "TCS", "series": "EQ", "date": "2026-10-15", "open": 2347.2, "high": 2386.7, "low": 2335.0, "close": 2350.0,
    "prevClose": 2304.9, "volume": 9150523, "totalTradedValue": 21607212833.8, …},
   {"symbol": "TCS", "series": "EQ", "date": "2026-10-16", "open": 2382.0, "high": 2484.7, "low": 2375.7, "close": 2465.0,
    "prevClose": 2350.0, "volume": 12166634, "totalTradedValue": 29683213295.9, …}]}
```

The connector sends `next_end_date` back as `endDate` on the next call. It stops when it reaches `start`, or when the server answers with an error payload for older data. In the second case it keeps the pages it has and adds a warning.

**4b. Corporate actions, fetched alongside** (Captured live)

```json
// arguments: {"symbol": "TCS", "fromDate": "2021-10-12", "toDate": "2026-10-16"}
{"symbol": "TCS", "from": "2021-10-12", "to": "2026-10-16", "count": 23,
 "actions": [
   {"exDate": "2021-10-14", "actionType": "DIVIDEND", "purpose": "Interim Dividend - Rs 7 Per Share", "adjustmentFactor": 1.0},
   {"exDate": "2022-02-22", "actionType": "OTHER",    "purpose": "Buyback",                           "adjustmentFactor": 1.0},
   …]}
```

`parse_action()` reads `purpose`. Only splits and bonus issues change prices (`adjust-v1`). TCS has neither in this window, so the bars are unchanged. A close-to-close move of 40% or more that no action explains is flagged as a warning.

**4c. Kite fills gaps.** `resolve()` looks up the instrument token once with `search_instruments`, then calls `get_historical_data`. Shape only:

```json
// either rows: [{"date": "...", "open": .., "high": .., "low": .., "close": .., "volume": ..}, …]
// or candles:  {"data": {"candles": [["2026-10-15T00:00:00+0530", 2347.2, 2386.7, 2335.0, 2350.0, 9150523], …]}}
```

The token the app resolved is stored as an identifier. Captured live, from `instrument.identifiers`:

```json
[{"provider": "kite_mcp", "kind": "instrument_token", "value": "2953217"}]
```

**4d. Merging the sources.** `reconcile_bars()` takes each date's whole bar from the first source in precedence that has a close for it, so one bar never mixes sources. Where both sources have a value, any difference above tolerance becomes a conflict. Output:

```
(merged frame with a `provider` column, conflicts[], coverage{"nse_mcp": n, "kite_mcp": n, "overlap": n, "conflicts": n})
```

**4e. A real degraded run: NSE down, Kite fills in.** At 06:53 UTC the NSE bhavcopy circuit breaker was open, so Kite supplied the recent bars. These rows were written to `data_source_records`. From the database:

```
 provider |      dataset      |      source_id      |   status    | rows |  retrieved_at (UTC)
----------+-------------------+---------------------+-------------+------+---------------------
 kite_mcp | history           | get_historical_data | ok          |   10 | 2026-10-16 06:53:33
 nse_mcp  | history           |                     | unavailable |      | 2026-10-16 06:53:33
 nse_mcp  | corporate_actions |                     | unavailable |      | 2026-10-16 06:53:33
```

The bars were written to `price_bars`, which has one row per instrument and date (`UNIQUE (instrument_id, date)`) and stores prices as `numeric`. From the database:

```
    date    |   close   |   volume    | provider |   adjustment   |  retrieved_at (UTC)
------------+-----------+-------------+----------+----------------+---------------------
 2026-10-16 | 2433.1000 |  4779833.00 | kite_mcp | split_adjusted | 2026-10-16 06:53:33
 2026-10-15 | 2350.0000 |  9150523.00 | kite_mcp | split_adjusted | 2026-10-16 06:53:33
 2026-10-14 | 2304.9000 | 12145767.00 | kite_mcp | split_adjusted | 2026-10-16 06:53:33
```

> The 2026-10-16 bar was fetched at 12:23 IST, during trading hours. Its "close" of 2433.1 is the
> price at that moment; NSE's end-of-day close in 4a is 2465.0. The next price refresh updates
> the existing row for that date, because `_write_history` updates rows in place by date.
> The 2026-10-15 bar shows three independent sources agreeing exactly: Kite (above), NSE MCP
> history (4a: close 2350.0, volume 9150523) and the NSE bhavcopy file (B.1).

**4f. What the API returns:** `GET /api/v1/instruments/NSE:TCS/history`, Captured live and cut.

```json
{"data_mode": "live", "is_demo": false, "retrieved_at": "2026-10-16T12:00:38.823192+00:00", "as_of": "2026-10-16",
 "interval": "1d", "currency": "INR",
 "adjustment": "split_adjusted (splits and bonuses; cash dividends not adjusted)", "adjustment_version": "adjust-v1",
 "bars": [
   {"date": "2024-01-01", "open": 3790.0, "high": 3832.0, "low": 3773.0, "close": 3811.1, "volume": 825907.0, "provider": "nse_bhavcopy_file"},
   …,
   {"date": "2026-10-16", "open": 2382.0, "high": 2446.0, "low": 2375.7, "close": 2433.1, "volume": 4779833.0, "provider": "kite_mcp"}],
 "coverage": {"bars": 689, "by_provider": {"nse_bhavcopy_file": 679, "kite_mcp": 10}, "first": "2024-01-01", "last": "2026-10-16"},
 "freshness": "fresh", "data_age_days": 0, "conflicts": [],
 "corporate_actions": [
   {"ex_date": "2021-10-14", "kind": "dividend", "ratio": 7.0, "description": "Interim Dividend - Rs 7 Per Share", "provider": "nse_mcp"}, …],
 "lineage": [
   {"provider": "kite_mcp", "dataset": "history", "status": "ok", "rows": 10, "source_id": "get_historical_data",
    "adjustment": "split_adjusted", "retrieved_at": "2026-10-16T06:53:33.023711+00:00",
    "detail": {"coverage": {"kite_mcp": 10, "overlap": 0, "conflicts": 0},
               "warnings": ["Corporate actions unavailable (nse_bhavcopy: circuit open after repeated failures; retrying later); NSE prices could not be split-adjusted."]}},
   {"provider": "nse_mcp", "dataset": "history", "status": "unavailable",
    "reason": "nse_bhavcopy: circuit open after repeated failures; retrying later", …}, …],
 "data_state": "ok", "data_state_reason": null}
```

### E.3 How sources are combined and conflicts recorded (`reconcile-v1`)

The examples below come from running the real `domain.reconcile.reconcile()` on two real TCS prices from 2026-10-16: NSE's 2465.0 at 16:00 IST and Kite's 2433.1 from 12:23 IST. They were sampled at different times, which is exactly the kind of difference the conflict check exists to surface.

**Both sources have a value and they differ by more than the 1% tolerance for `last_price`.** The higher-precedence value is kept and the conflict is recorded:

```json
{"status": "conflict", "provider": "nse_mcp", "value": 2465.0,
 "conflict": {"field": "last_price", "values": {"nse_mcp": 2465.0, "kite_mcp": 2433.1},
              "diff_pct": 1.2941, "chosen": "nse_mcp", "tolerance_pct": 1.0}}
```

**NSE has no value (here, a timeout), so Kite fills the gap:**

```json
{"status": "ok", "provider": "kite_mcp", "value": 2433.1, "filled_from_fallback": true}
```

**Where the results go.** A field's `conflict` and the response's `conflicts[]` carry the record into the API (E.1). Bar conflicts are saved in `source_conflicts`, one row per field and date. Tolerances come from `GET /sources/status`:

```json
"precedence": {"default": ["nse_mcp", "kite_mcp"], "tolerances_pct": {"close": 0.1, "last_price": 1.0, "volume": 5.0}}
```

### E.4 Failures and degraded data

| Situation | What actually comes back | What the app does |
|---|---|---|
| Unknown symbol at NSE (Captured live) | `{"hint": "Use search_symbols tool to find valid symbols", "error": "Symbol not found: NOSUCHSYM"}` with `isError: false` | `payload_error()` detects it and raises `ServerRefused`. The status becomes `unavailable` and no rows are written |
| Timeout (Captured live: the first `initialize` attempt) | `httpx.ReadTimeout` | `McpUnavailable`, retried with backoff. Repeated failures open the circuit breaker: `"circuit open after repeated failures; retrying later"` |
| Kite session not approved (Captured live) | `"kite_mcp rejected the request (HTTP 400): Invalid session ID"` | `auth.state = "login_required"`. Kite rows get `status: login_required`, and the Data status page offers a fresh login link |
| Server lacks a tool | — | Capability shows `{"supported": false, "reason": "no tool advertised by NSE … provides this"}`, and the status is `not_supported` |
| Every price source fails | — | `DataUnavailable("no price history for X: nse_mcp: unavailable (…); kite_mcp: login_required (…)")`. Earlier stored data stays in place |

**Connector health**, Captured live from `GET /api/v1/sources/status` and cut:

```json
{"key": "nse_bhavcopy", "state": "degraded", "breaker": "half_open",
 "server": {"name": "nse-bhavcopy-redis-mcp", "version": "1.0.0"}, "tool_count": 21,
 "discovered_at": "2026-10-15T18:58:15.881954+00:00", "last_success_at": "2026-10-15T19:24:12.903083+00:00",
 "last_error": "timeout calling nse_bhavcopy: ReadTimeout", "last_error_at": "2026-10-16T06:53:04.656082+00:00",
 "latency_ms": 616.5, "calls": 2351, "errors": 1562, "retries": 718, "error_rate": 0.6644}
```

**API error format**, Captured live from `GET /api/v1/instruments/NSE:NOSUCH`. It returns HTTP 404 with the header `x-request-id: 36151e65e6bb4642`:

```json
{"error": {"code": "instrument_not_found", "message": "NSE:NOSUCH is not in the instrument master.",
           "details": null, "request_id": "36151e65e6bb4642"}}
```

### E.5 Stock research: evidence bundle, web sources and LLM grounding

`GET /api/v1/instruments/NSE:TCS/research` returns the latest stored report. `POST /api/v1/research/stock` makes a new one. The excerpts below are Captured live from report 12, generated on 2026-10-15.

**12a. The evidence bundle.** It has 56 items. Every item has an id, and the model may cite only these ids.

```json
"M:close":       {"kind": "calculation", "label": "Last close", "value": 2350.0, "unit": "price", "formula": "last adjusted close", "as_of": "2026-10-15"},
"Q:last_price":  {"kind": "quote", "label": "last price", "value": 2350.0, "provider": "nse_mcp", "as_of": "2026-10-15T16:00:28+05:30", "status": "delayed", "currency": "INR"},
"S2":            {"kind": "source", "source_type": "secondary",
                  "title": "TCS Q2 Results 2026 Live Updates: TCS Q2 profit rises 4% QoQ; company declares interim dividend of Rs 12/share",
                  "url": "https://economictimes.indiatimes.com/markets/stocks/live-blog/tcs-q2-results-live-…/liveblog/134779808.cms",
                  "published_at": "2026-10-08", "retrieved_at": "2026-10-15T18:16:21.039621+00:00", "snippet": "TCS is set to announce …"}
```

The web search that produced the `S*` items:

```json
"web": {"provider": "ddgs", "available": true, "results": 8, "query": "Tata Consultancy Services TCS NSE results announcement",
        "retrieved_at": "2026-10-15T18:16:21.039812+00:00"}
```

**12b. The model's output.** Each statement must cite evidence ids, and the `kinds` field tells the UI whether it rests on a source, a calculation or interpretation.

```json
"pros": [{"text": "Exceptional profitability: ROE 48.72%, ROCE 55.03%, EBITDA margin 27.11%, PAT margin 18.43%.",
          "evidence": ["M:roe", "M:roce", "M:ebitda_margin", "M:pat_margin"], "kinds": ["calculation"]}, …],
"news": [{"text": "Q2 FY27 profit rose 4% QoQ; interim dividend of Rs 12/share declared with record date the following week.",
          "evidence": ["S2", "S7"], "kinds": ["source"]}]
```

**12c. Grounding check.** A statement is dropped if it states numbers that are not in the evidence it cites. Here 3 of 32 were dropped, and the summary fell back to the rule-based line.

```json
"grounding": {"points_total": 32, "points_dropped": 3, "dropped": [
  {"section": "valuation_context", "text": "P/E (TTM) 17.28x sits at the 50th percentile among sector peers; …",
   "reason": "states number(s) not present in the cited evidence: 12.74, 46"}, …]},
"llm": {"available": true, "model_id": "z-ai/glm-5.3-flash",
        "reason": "The model's summary stated a figure not found in its cited evidence, so the rule-based summary line is shown."},
"summary": {"text": "Research stance is Mixed with High confidence, from deterministic rules over the available metrics. No language-model interpretation was applied.",
            "evidence": ["M:overall_score"], "kinds": ["calculation"]}
```

### E.6 Refresh schedule

| Job (cron, Asia/Kolkata) | Fetches |
|---|---|
| `instruments` (`0 7 * * 1-5`) | NSE equity list and industry files (B.1) → `instruments` |
| `prices` (`30 19 * * 1-5`) | 1. bhavcopy files (B.1)<br>2. per-symbol MCP history for indices and reference stocks (A.1.1, A.1.5, A.3.3)<br>3. Kite tokens (A.3.1)<br>4. NSE filings (B.2, B.3)<br>5. recompute metrics<br>6. clear the API cache |
| `rrg` (`0 */12 * * *`) | No external calls. Writes a new RRG snapshot from stored bars (D.11) |
| `themes` (`30 6 1 * *`) | No external calls. Writes a new immutable monthly snapshot (D.15, D.16) |
| `alerts` (`*/15 * * * *`) | Evaluates alerts against stored data (D.21) |
| On demand | Quote (A.2.1 + A.3.2) when an instrument page opens; research (B.4, B.5) and concalls on request |
