# Azure configuration: Key Vault secrets vs container environment variables

## Deployment these values are for

| Item | Value |
|---|---|
| Subscription | Microsoft Partner Network |
| Resource group | `HK-RG-04` |
| Container app | `markets` — one replica, two containers: `web` (nginx, port 8080, public) and `api` (FastAPI, port 8000) |
| Public URL | `https://markets.greencliff-c30bacad.centralindia.azurecontainerapps.io` |
| Container registry | `acrmarketshackathon.azurecr.io` (images `markets-web:v1`, `markets-api:v1`) |
| Key Vault | `kv-markets-Hackathon` — `https://kv-markets-Hackathon.vault.azure.net/` |
| Managed identity | `id-markets-Hackathon` (client id `4e996a7d-d1f9-4fdb-bd7f-b17e6944ee24`) |
| PostgreSQL | `psql-markets-hackathon.postgres.database.azure.com`, database `markets`, admin user `pgadmin` |
| Redis | none for this project — see `REDIS_URL` below |
| Persistent volume | Azure Files share `markets-var` mounted as volume `var` (for `/app/var`) |

Rule of thumb: anything that grants access goes to **Key Vault**; everything else is a plain **container
environment variable** on the `api` container.

The complete, ready-to-use list including the real secret values is in **`.env.azure`** at the repo root
(gitignored, owner-only permissions). This document is committed to git, so secret values are not written here.

---

## 1. Key Vault secrets (`kv-markets-Hackathon`)

The vault currently holds no secrets. Add these, then reference them from the `api` container.

| Env var on `api` container | Key Vault secret name | Value | Source |
|---|---|---|---|
| `DATABASE_URL` | `database-url` | `postgresql+psycopg://pgadmin:<PGADMIN_PASSWORD>@psql-markets-hackathon.postgres.database.azure.com:5432/markets?sslmode=require` | Only the `pgadmin` password is missing: fill it into `.env.azure` (URL-encode `@ : / # ? %`) or reset it with `az postgres flexible-server update -g HK-RG-04 -n psql-markets-hackathon --admin-password <new>` |
| `LLM_API_KEY` | `llm-api-key` | In `.env.azure` (Azure OpenAI key 1 of `aif-markets-Hacakathon`) | `az cognitiveservices account keys list -n aif-markets-Hacakathon -g HK-RG-04 --query key1 -o tsv`. The old OpenRouter key is no longer used — revoke it at openrouter.ai (it was exposed) |
| `API_AUTH_TOKEN` | `api-auth-token` | In `.env.azure` (freshly generated, 64 hex chars) | Generated with `openssl rand -hex 32` |
| `WEB_SEARCH_API_KEY` | `web-search-api-key` | In `.env.azure` (copied from `.env`) | Tavily key. Unused while `WEB_SEARCH_PROVIDER=ddgs`; **rotate** it at tavily.com (exposed) |

Not added, on purpose:

| Variable | Why |
|---|---|
| `REDIS_URL` | No Redis exists for this project. With a single replica and the scheduler in the same container, the in-process cache and locks are correct. Add an Azure Cache for Redis and this secret (`rediss://:<key>@<name>.redis.cache.windows.net:6380/0`) only if you scale beyond one replica |
| `FRED_API_KEY` | Empty in `.env`; add when you get a key from fred.stlouisfed.org |

Commands (run from the repo root; values are read from `.env.azure`, never typed on the command line):

```bash
KV=kv-markets-Hackathon
put() { grep "^$1=" .env.azure | cut -d= -f2- | tr -d '\n' > /tmp/v && az keyvault secret set --vault-name $KV -n "$2" --file /tmp/v -o none; rm -f /tmp/v; }
put DATABASE_URL database-url
put LLM_API_KEY llm-api-key
put API_AUTH_TOKEN api-auth-token
put WEB_SEARCH_API_KEY web-search-api-key

# Let the app's identity read them, then link them to the container app
az role assignment create --assignee-object-id feea2679-b874-44bf-8713-f1a41c466055 --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" --scope $(az keyvault show -n $KV --query id -o tsv)
ID=$(az identity show -n id-markets-Hackathon -g HK-RG-04 --query id -o tsv)
VAULT=https://kv-markets-hackathon.vault.azure.net/secrets
az containerapp secret set -n markets -g HK-RG-04 --secrets \
  database-url=keyvaultref:$VAULT/database-url,identityref:$ID \
  llm-api-key=keyvaultref:$VAULT/llm-api-key,identityref:$ID \
  api-auth-token=keyvaultref:$VAULT/api-auth-token,identityref:$ID \
  web-search-api-key=keyvaultref:$VAULT/web-search-api-key,identityref:$ID
```

---

## 2. Container environment variables (`api` container)

Set on the `api` container of `markets`. The `web` container needs only what section 3 lists.

### App and runtime

| Variable | Azure value | Notes |
|---|---|---|
| `APP_ENV` | `production` | **CHANGE** (local: `local`) |
| `DATA_MODE` | `live` | |
| `LOG_LEVEL` | `INFO` | |
| `DEFAULT_MARKET_TIMEZONE` | `Asia/Kolkata` | |
| `CORS_ORIGINS` | `https://markets.greencliff-c30bacad.centralindia.azurecontainerapps.io` | **CHANGE** (local: `http://localhost:5173,http://127.0.0.1:5173`) |
| `AUTO_CREATE_SCHEMA` | `true` | **CHANGE** (local: `false`, where the Compose `migrate` service runs Alembic). There is no migration job on Azure, so the API creates the schema at start-up |
| `AUTO_INITIAL_REFRESH` | `true` | |
| `MAX_REQUEST_BYTES` | `5000000` | |

### Data source endpoints

| Variable | Azure value |
|---|---|
| `NSE_BHAVCOPY_MCP_URL` | `https://mcp.nseindia.in/bhavcopy/cm/mcp` |
| `NSE_CM_MARKET_MCP_URL` | `https://mcp.nseindia.in/cmmkt/mcp` |
| `BROKER_MCP_URL` | `https://mcp.kite.trade/mcp` |
| `TICKERTAPE_MCP_URL` | `https://mcp.tickertape.in/mcp` |
| `TICKERTAPE_REDIRECT_URI` | `https://markets.greencliff-c30bacad.centralindia.azurecontainerapps.io/api/v1/sources/tickertape/callback` — **CHANGE** (local: `http://127.0.0.1:5173/...`) |
| `NSE_FILINGS_BASE_URL` | `https://www.nseindia.com/api` |
| `NSE_FILINGS_DOCUMENT_HOSTS` | `nsearchives.nseindia.com` |
| `NSE_EQUITY_LIST_URL` | `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` |
| `NSE_INDUSTRY_LIST_URL` | `https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv` |
| `NSE_BHAVCOPY_URL_TEMPLATE` | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip` |
| `NSE_FO_BHAVCOPY_URL_TEMPLATE` | `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip` |
| `NSE_PARTICIPANT_OI_URL_TEMPLATE` | `https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv` |
| `MACRO_CHART_BASE_URL` | `https://query1.finance.yahoo.com/v8/finance/chart` |
| `NSDL_FPI_ARCHIVE_URL` | `https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx` |
| `MACRO_CPI_BASE_URL` | `https://api.mospi.gov.in/api/cpi` |

`{yyyymmdd}` / `{ddmmyyyy}` are literal placeholders the app fills in — store them exactly as shown.

### LLM (Azure OpenAI)

Provider: Azure AI Services resource `aif-markets-Hacakathon` (`HK-RG-04`), deployment `gpt-5.4-mini`
(model `gpt-5.4-mini` 2026-03-17, GlobalStandard). Verified end to end against this deployment with all seven
response schemas the app uses.

| Variable | Azure value | Why |
|---|---|---|
| `LLM_ENABLED` | `true` | |
| `LLM_PROVIDER_NAME` | `Azure OpenAI` | Display label only |
| `LLM_BASE_URL` | `https://aif-markets-hacakathon.cognitiveservices.azure.com/openai/v1` | Azure OpenAI v1 endpoint (OpenAI-compatible, accepts the key as `Bearer`) |
| `LLM_MODEL_ID` | `gpt-5.4-mini` | The **deployment** name |
| `LLM_MAX_TOKENS_PARAM` | `max_completion_tokens` | GPT-5 models reject `max_tokens` (HTTP 400) |
| `LLM_RESPONSE_FORMAT` | `json_object` | Azure strict `json_schema` rejects the app's Pydantic schemas (requires `additionalProperties: false` on every object). The schema still goes in the prompt and replies are validated with Pydantic, with one repair attempt |
| `LLM_VERIFY_MODEL_LIST` | `false` | Azure's `/models` lists base models, not deployments |
| `LLM_EXTRA_HEADERS` | *(empty)* | The OpenRouter `X-Title` header is not needed |
| `LLM_EXTRA_BODY` | *(empty)* | Do **not** set `reasoning_effort`: with it, GPT-5 models accept only temperature 1 and reject the app's 0.1. Empty = no reasoning tokens, low temperature |
| `LLM_TIMEOUT_SECONDS` | `60` | |
| `LLM_MAX_RETRIES` | `1` | |
| `LLM_MAX_OUTPUT_TOKENS` | `6000` | |
| `LLM_DAILY_BUDGET` | `200` | |
| `LLM_DAILY_TOKEN_BUDGET` | `2000000` | |

`LLM_API_KEY` (Key Vault `llm-api-key`) is the resource key:
`az cognitiveservices account keys list -n aif-markets-Hacakathon -g HK-RG-04 --query key1 -o tsv`.
It is already in `.env.azure`.

### Web search

| Variable | Azure value |
|---|---|
| `WEB_SEARCH_PROVIDER` | `ddgs` |
| `WEB_SEARCH_MAX_RESULTS` | `8` |

`WEB_SEARCH_BASE_URL` is empty locally and is not set on Azure.


### Schedules (cron)

| Variable | Azure value | Meaning |
|---|---|---|
| `THEME_REFRESH_CRON` | `30 6 1 * *` | Monthly, 1st at 06:30 |
| `RRG_REFRESH_CRON` | `0 */12 * * *` | Every 12 hours |
| `PRICE_REFRESH_CRON` | `30 19 * * 1-5` | Weekdays 19:30 |
| `INSTRUMENT_REFRESH_CRON` | `0 7 * * 1-5` | Weekdays 07:00 |
| `ALERTS_CRON` | `*/15 * * * *` | Every 15 minutes |
| `FII_DII_CRON` | `30 19,21 * * 1-5` | Weekdays 19:30 and 21:30 |

### Universe

| Variable | Azure value |
|---|---|
| `UNIVERSE_SCOPE` | `all` |
| `UNIVERSE_SERIES` | `EQ,BE` |
| `BENCHMARK_SYMBOL` | `NIFTY 50` (with the space) |
| `HISTORY_YEARS` | `3` |

### NSE filings, MCP connectors and circuit breaker

| Variable | Azure value |
|---|---|
| `NSE_FILINGS_MIN_INTERVAL_SECONDS` | `0.5` |
| `NSE_FILINGS_TIMEOUT_SECONDS` | `30` |
| `NSE_FILINGS_MAX_COMPANIES_PER_RUN` | `400` |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `10` |
| `MCP_TOOL_TIMEOUT_SECONDS` | `30` |
| `MCP_MAX_CONCURRENCY` | `4` |
| `MCP_MAX_RETRIES` | `2` |
| `NSE_MCP_MIN_INTERVAL_SECONDS` | `0.2` |
| `NSE_BHAVCOPY_SESSION_RETRIES` | `20` (default; resends on NSE's 404 "Session not found") |
| `NSE_CM_MARKET_SESSION_RETRIES` | `5` (default) |
| `MCP_SESSION_RETRY_DELAY_SECONDS` | `0.1` (default) |
| `BROKER_MCP_MIN_INTERVAL_SECONDS` | `0.35` |
| `BREAKER_FAILURE_THRESHOLD` | `5` |
| `BREAKER_RESET_SECONDS` | `60` |

### Caching and rate limits

| Variable | Azure value |
|---|---|
| `CACHE_TTL_QUOTE` | `60` |
| `CACHE_TTL_HISTORY` | `21600` |
| `CACHE_TTL_INSTRUMENT` | `86400` |
| `RATE_LIMIT_RESEARCH_PER_MINUTE` | `6` |
| `RATE_LIMIT_REFRESH_PER_MINUTE` | `3` |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | `300` |

---

## 3. Per-container settings

| Variable | `api` container | `web` container |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` — there is no separate worker, so the API runs the scheduled jobs. Keep the app at **one replica** (currently min 1 / max 1) so jobs never run twice | — |
| `API_UPSTREAM` | — | `127.0.0.1:8000` (already set; both containers share the replica's network) |
| Key Vault secrets (section 1) | yes | none |
| Env vars (section 2) | yes | none |
| Volume `var` (Azure Files `markets-var`) | mount at `/app/var` | — |

---

## 4. Do not deploy

| Variable | Reason |
|---|---|
| `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN` | Not read by the backend; Kite is accessed only through MCP. Remove from `.env` |
| `MOCK_MCP_HOST` | Local demo only (Docker Compose mock MCP server) |
| `POSTGRES_PASSWORD` | Docker Compose local Postgres only; Azure uses `DATABASE_URL` from Key Vault |
| `WEB_SEARCH_BASE_URL`, `FRED_API_KEY` | Empty locally; set only when you have a value |

---

## 5. Not environment variables, but needed

- **OAuth session files.** Kite and Tickertape tokens are written to `/app/var/kite_mcp_session.json` and
  `/app/var/tickertape_oauth.json`. The `var` volume (Azure Files `markets-var`) must be mounted at `/app/var` on
  the `api` container so they survive restarts.
- **Values must be bare.** Do not copy trailing `# comments` or leading spaces from `.env`; `.env.azure` is already
  clean.
- **Rotation.** Secret references use versionless URIs; after updating a secret in Key Vault, restart the revision:
  `az containerapp revision restart -n markets -g HK-RG-04 --revision $(az containerapp show -n markets -g HK-RG-04 --query properties.latestRevisionName -o tsv)`.
