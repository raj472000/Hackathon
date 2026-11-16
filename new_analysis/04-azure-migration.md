# 04 · Moving Markets Intelligence to Azure

A complete, Azure-only plan for running this repository as a cloud service: which Azure
services to use and why, how to wire the network, storage, secrets and identity, every
environment variable with its cloud value, CI/CD, monitoring, backups, cost, and the
day-2 runbook. Written from the code as it is in October 2026 (`backend/app/core/config.py`,
`infra/docker/*`, `docker-compose.yml`, `.github/workflows/ci.yml`).

> **Status:** this is a plan, not a record of a deployment. Nothing here has been executed
> against an Azure subscription from this repository. Run it first in a throw-away resource
> group, and check each `az` command against `az <group> <command> --help` for your CLI version.

**Contents**

1. [Recommended architecture at a glance](#1-recommended-architecture-at-a-glance)
2. [Why these services (and what was rejected)](#2-why-these-services-and-what-was-rejected)
3. [What in the code drives the design](#3-what-in-the-code-drives-the-design)
4. [Azure resource inventory and naming](#4-azure-resource-inventory-and-naming)
5. [Environment variables: complete cloud mapping](#5-environment-variables-complete-cloud-mapping)
6. [Step-by-step provisioning (az CLI)](#6-step-by-step-provisioning-az-cli)
7. [The Container App definition (YAML)](#7-the-container-app-definition-yaml)
8. [First start: data, Kite login, verification](#8-first-start-data-kite-login-verification)
9. [CI/CD with GitHub Actions (OIDC, no stored passwords)](#9-cicd-with-github-actions-oidc-no-stored-passwords)
10. [Security hardening](#10-security-hardening)
11. [Monitoring, logs and alerts](#11-monitoring-logs-and-alerts)
12. [Backups, restore and disaster recovery](#12-backups-restore-and-disaster-recovery)
13. [Environments: dev / staging (demo) / production](#13-environments-dev--staging-demo--production)
14. [Scaling beyond one replica](#14-scaling-beyond-one-replica)
15. [Cost estimate](#15-cost-estimate)
16. [Risks and open questions](#16-risks-and-open-questions)
17. [Day-2 runbook (quick reference)](#17-day-2-runbook-quick-reference)
18. [Migration checklist](#18-migration-checklist)

---

## 1. Recommended architecture at a glance

```
                     Users (browser)
                           │ HTTPS (managed certificate, custom domain)
                           ▼
            ┌──────────────────────────────────────────────┐
            │ Azure Container Apps environment (VNet)       │
            │                                               │
            │  Container App "markets"  (1 replica, always on)
            │  ┌────────────────┐  127.0.0.1  ┌────────────────────────┐
            │  │ web  (nginx)   │────────────▶│ api  (FastAPI+scheduler)│
            │  │ :8080 ingress  │  :8000      │ SCHEDULER_ENABLED=true  │
            │  └────────────────┘             └──────────┬─────────────┘
            │                                            │ /app/var
            │  Container Apps Job "markets-migrate"      ▼
            │  (alembic upgrade head, per release)  Azure Files share
            └───────────────┬────────────────────────────┬──────────────┘
                            │ private (VNet)             │ egress 443
                            ▼                            ▼
      Azure Database for PostgreSQL         mcp.nseindia.in · mcp.kite.trade
      Flexible Server 16 (private access)   nseindia.com / nsearchives · LLM provider
                                            · web search
   Supporting: Azure Container Registry · Key Vault · User-assigned managed identity ·
               Log Analytics + Application Insights · (optional) NAT Gateway, Front Door/WAF,
               Azure Managed Redis (only when scaling out)
```

**In one sentence:** the two existing images run unchanged as one Azure Container App (web
and api containers side by side, one always-on replica, scheduler inside the API), backed by
a private PostgreSQL Flexible Server, an Azure Files share for `/app/var`, Key Vault for every
secret, and a Container Apps Job for migrations.

## 2. Why these services (and what was rejected)

| Need | Choice | Why | Rejected alternatives |
|---|---|---|---|
| Run containers | **Azure Container Apps (ACA)**, workload-profiles environment, Consumption profile | Runs the Compose topology almost 1:1 (multi-container app sharing `localhost`, jobs, built-in HTTPS, revisions, managed identity, Key Vault refs, Azure Files mounts). No cluster to operate. | **AKS** — more power than one always-on replica needs; you operate the cluster. **App Service for Containers** — sidecars and jobs are less natural; migrations need a separate mechanism. **Azure Container Instances** — no ingress/TLS/revisions. |
| System-of-record DB | **Azure Database for PostgreSQL – Flexible Server 16** | The app requires PostgreSQL (psycopg 3, Alembic). Managed backups, PITR, HA option, private VNet access. | PostgreSQL in a container — container disks are not durable. Cosmos DB for PostgreSQL — unnecessary distribution. |
| Persistent files (`/app/var`) | **Azure Files (SMB) share mounted into the api container** | The API writes the Kite session, uploaded statements and the NSE filings cache to disk (§3). | Blob Storage — would need code changes (the code uses the filesystem). |
| Secrets | **Azure Key Vault**, referenced by the Container App through a **user-assigned managed identity** | Secrets never live in YAML, images, or GitHub. Rotation without redeploying the YAML. | ACA inline secrets (fine for a trial, but values sit in the app definition). |
| Images | **Azure Container Registry (Basic → Standard)** with **ACR Tasks** for builds | `az acr build` builds `linux/amd64` in Azure, which removes the Apple-Silicon `arm64` image problem. Pull via managed identity (`AcrPull`), no admin user. | Docker Hub/GHCR — extra credentials, egress. |
| Migrations | **Container Apps Job** (manual trigger) using the backend image | Same image, same secrets, runs `alembic upgrade head` once per release before the app update. | Running migrations at API start (`AUTO_CREATE_SCHEMA=true`) — explicitly unsupported by the repo for PostgreSQL. |
| Scheduler | **Inside the API** (`SCHEDULER_ENABLED=true`) on a **single always-on replica** | The Kite authorisation is bound to the API process's MCP session (§3). One replica = one scheduler = no duplicate runs. | Separate worker app — possible later (§14), but the worker would not share the Kite login. Azure Functions timers — would duplicate scheduling logic. |
| Cache/locks | **None (in-process)** at one replica | `REDIS_URL` blank = in-process cache and locks, which is correct for one process. | Azure Managed Redis — add only when scaling out (§14). |
| Observability | **Log Analytics** (container stdout JSON logs) + **Application Insights** availability test + Azure Monitor alerts | Logs are already structured JSON; health endpoints already exist. | Self-hosted Prometheus/Grafana — optional (§11). |
| Region | **Central India** (`centralindia`), DR pair **South India** | Closest to NSE/Kite endpoints and Indian users; Indian data residency. | — |

## 3. What in the code drives the design

These facts come from the code and determine the topology. Do not change the topology without
re-checking them.

| Fact (file) | Consequence on Azure |
|---|---|
| One backend image serves three roles: API (`uvicorn app.main:app`), worker (`python -m app.workers.run`), migrations (`alembic upgrade head`) — `infra/docker/backend.Dockerfile`, `docker-compose.yml` | Same image for the Container App's `api` container and for the migration job. |
| The frontend image is nginx on **:8080**, proxying `/api/` and `/health` to `${API_UPSTREAM}` (default `api:8000`) — `infra/docker/nginx.conf.template` | Set `API_UPSTREAM=127.0.0.1:8000` on the `web` container; ingress `targetPort: 8080`. The browser only ever talks to one origin, so CORS is not needed. |
| The backend runs as uid **10001**, writes to `/app/var` — Dockerfile | Azure Files must be mounted with `uid=10001,gid=10001` so the non-root user can write. |
| `/app/var/kite_mcp_session.json` holds the Kite MCP session id (owner-only, written atomically with `os.replace`) — `connectors/mcp_client.py`, `KITE_PERSIST_SESSION` | Without persistent storage the Kite login is lost on every restart/revision. |
| `/app/var/fundamentals/live/` holds uploaded statements; `/app/var/nse_filings/` caches filings/transcripts — `core/container.py`, `connectors/nse_filings.py` | Same share; uploads otherwise disappear on restart. |
| The scheduler (APScheduler) and the Kite login live in one process; job de-duplication uses the cache lock (`core/cache.py`), which is in-process unless `REDIS_URL` is set | **Exactly one replica** while `REDIS_URL` is blank. `minReplicas: 1` so cron jobs (12-hour RRG, monthly themes, 19:30 IST prices) actually fire. |
| `/health/live` (process up) and `/health/ready` (DB + cache; returns 503 when not ready; upstream sources reported but never fail readiness) — `app/main.py` | Liveness → `/health/live`, readiness → `/health/ready`, both on port 8000 of the api container. |
| `/metrics` serves Prometheus text — `app/main.py` | Optional scraping (§11). It is not proxied by nginx (only `/api/` and `/health`), so it stays internal. |
| Settings are read from environment variables (pydantic-settings, `extra="ignore"`); no `.env` file is copied into the image (`.dockerignore`) | All configuration is container env vars + Key Vault secret refs. |
| `DATABASE_URL` in any `postgres://`/`postgresql://` form is rewritten to `postgresql+psycopg://`; `?ssl=` → `?sslmode=` | Use `...?sslmode=require` for Flexible Server. |
| `AUTO_INITIAL_REFRESH=true` fills an empty database on first start (takes minutes in live mode) | Expect a long first ingest; readiness does not wait for it. |
| `DATA_MODE=demo` points connectors at mock MCP servers on `MOCK_MCP_HOST:9100-9102` | A demo/staging app can run the mock MCP as a third container on `127.0.0.1` (§13). |

## 4. Azure resource inventory and naming

Use one resource group per environment. Names below assume `prod`; replace with `stg`/`dev`.

| Resource | Example name | SKU / setting | Notes |
|---|---|---|---|
| Resource group | `rg-markets-prod-cin` | Central India | Everything below lives here. |
| Virtual network | `vnet-markets-prod` | `10.40.0.0/16` | |
| Subnet: ACA | `snet-aca` | `10.40.0.0/23`, delegated to `Microsoft.App/environments` | /27 is the minimum for workload-profiles environments; /23 leaves room. |
| Subnet: PostgreSQL | `snet-pg` | `10.40.2.0/28`, delegated to `Microsoft.DBforPostgreSQL/flexibleServers` | |
| Private DNS zone | `markets-prod.private.postgres.database.azure.com` | linked to the VNet | Created by the PG create command. |
| NAT Gateway + public IP (optional) | `ng-markets-prod`, `pip-markets-egress` | Standard | Static outbound IP for upstream allow-listing (§16). |
| Log Analytics workspace | `log-markets-prod` | PerGB2018, 30-day retention | |
| Application Insights | `appi-markets-prod` | workspace-based | Availability tests, alerts. |
| Container registry | `acrmarketsprod` (globally unique, alphanumeric) | Basic (Standard for geo-replication/higher throughput) | Admin user **disabled**. |
| User-assigned managed identity | `id-markets-prod` | — | `AcrPull` on ACR, `Key Vault Secrets User` on the vault. |
| Key Vault | `kv-markets-prod` (globally unique) | Standard, RBAC authorization, purge protection on | |
| Storage account | `stmarketsprod` (globally unique) | StorageV2, Standard LRS (ZRS for prod HA), TLS 1.2 min, no public blob access | |
| File share | `var` | 5 GiB quota (raise as filings cache grows), transaction-optimized | Mounted at `/app/var`. |
| PostgreSQL Flexible Server | `psql-markets-prod` | v16, Burstable `Standard_B2s` (start; `B1ms` for dev), 32 GiB, backups 14 days | Private access in `snet-pg`. Zone-redundant HA = General Purpose tier, optional. |
| Database | `markets` | | Owned by the app user `markets`. |
| ACA environment | `cae-markets-prod` | workload profiles, Consumption profile, VNet `snet-aca` | Logs → `log-markets-prod`. |
| ACA environment storage | `markets-var` | AzureFile, ReadWrite | Points at the `var` share. |
| Container App | `markets` | web 0.25 vCPU/0.5 GiB + api 1.0 vCPU/2 GiB, min=max=1, single revision mode | Public ingress on 8080. |
| Container Apps Job | `markets-migrate` | Manual, 1 replica, timeout 900 s, retry 0 | `alembic upgrade head`. |
| (Optional) Azure Front Door Standard/Premium + WAF | `afd-markets-prod` | | Only if you need WAF/global edge (§10). |

## 5. Environment variables: complete cloud mapping

Every setting the backend reads (`backend/app/core/config.py`) plus the frontend container's
variable. **Where** is how it is supplied on Azure:

- **KV** = Key Vault secret, referenced by the Container App via managed identity (`secretRef`).
- **Env** = plain container environment variable in the Container App YAML.
- **Default** = leave unset; the code default is right for Azure.

> Correction to older docs: `.env.example` header and `03-container-cloud-deployment.md`
> mention `HF_TOKEN`/`HF_MODEL_ID`. The code does **not** read those. The LLM is configured with
> the `LLM_*` variables below (any OpenAI-compatible endpoint, including a Hugging Face
> endpoint via its OpenAI-compatible route).

### 5.1 Application

| Variable | Container | Production value | Where | Notes |
|---|---|---|---|---|
| `APP_ENV` | api, job | `production` | Env | |
| `DATA_MODE` | api, job | `live` | Env | `demo` only for the staging/demo app (§13). |
| `LOG_LEVEL` | api, job | `INFO` | Env | `DEBUG` temporarily when diagnosing. |
| `DEFAULT_MARKET_TIMEZONE` | api | `Asia/Kolkata` | Env | All cron expressions are evaluated in this zone, not in the container's UTC. |
| `API_AUTH_TOKEN` | api | 64-hex random (`openssl rand -hex 32`) | **KV** `api-auth-token` | Turns on `Authorization: Bearer` for `/api/v1`. The web app asks for it once per browser session. Keep it set on any public URL. |
| `CORS_ORIGINS` | api | *(empty)* | Env | Empty = same-origin only, which is correct because nginx proxies `/api`. Set only if another origin calls the API directly. |
| `SCHEDULER_ENABLED` | api | `true` | Env | `false` on the migration job (it exits anyway) and on extra replicas in §14. |
| `AUTO_CREATE_SCHEMA` | api, job | `false` | Env | Schema comes from Alembic only. |
| `AUTO_INITIAL_REFRESH` | api | `true` | Env | First start on an empty DB ingests everything. |
| `MAX_REQUEST_BYTES` | api | `5000000` | Default | nginx also caps bodies at 9 MB. |

### 5.2 Database and cache

| Variable | Production value | Where | Notes |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://markets:<pwd>@psql-markets-prod.postgres.database.azure.com:5432/markets?sslmode=require` | **KV** `database-url` | Use a dedicated app user (`markets`), not the server admin. URL-encode special characters in the password (or use hex passwords). |
| `REDIS_URL` | *(empty)* | Default | Single replica. With Azure Managed Redis (§14): `rediss://:<key>@<name>.<region>.redis.azure.net:10000/0` as **KV** `redis-url`. |
| `POSTGRES_PASSWORD` | — | not used | Only for the local Compose database. Do not set in Azure. |

### 5.3 Market data (MCP and NSE files)

| Variable | Production value | Where | Notes |
|---|---|---|---|
| `NSE_BHAVCOPY_MCP_URL` | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | Env | |
| `NSE_CM_MARKET_MCP_URL` | `https://mcp.nseindia.in/cmmkt/mcp` | Env | |
| `BROKER_MCP_URL` | `https://mcp.kite.trade/mcp` | Env | Kite login is interactive (§8). No Kite API key/secret is used by this code. |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `10` | Default | |
| `MCP_TOOL_TIMEOUT_SECONDS` | `30` | Default | |
| `MCP_MAX_CONCURRENCY` | `4` | Default | |
| `MCP_MAX_RETRIES` | `2` | Default | |
| `NSE_MCP_MIN_INTERVAL_SECONDS` | `0.2` | Default | Respect provider rate limits; do not lower. |
| `BROKER_MCP_MIN_INTERVAL_SECONDS` | `0.35` | Default | |
| `KITE_PERSIST_SESSION` | `true` | Env | Needs the Azure Files mount, otherwise lost on restart. |
| `KITE_SESSION_FILE` | `/app/var/kite_mcp_session.json` | Default | Code default resolves to this path in the image. |
| `KITE_KEEPALIVE_SECONDS` | `300` | Default | |
| `BREAKER_FAILURE_THRESHOLD` | `5` | Default | |
| `BREAKER_RESET_SECONDS` | `60` | Default | |
| `NSE_FILINGS_BASE_URL` | `https://www.nseindia.com/api` | Env | Empty disables filings/transcript fetching (see risk in §16). |
| `NSE_FILINGS_DOCUMENT_HOSTS` | `nsearchives.nseindia.com` | Env | Allow-list for document downloads (SSRF guard). |
| `NSE_FILINGS_USER_AGENT` | code default | Default | |
| `NSE_FILINGS_MIN_INTERVAL_SECONDS` | `1.0` | Env | |
| `NSE_FILINGS_TIMEOUT_SECONDS` | `30` | Env | |
| `NSE_FILINGS_MAX_COMPANIES_PER_RUN` | `400` | Env | |
| `NSE_FILINGS_CACHE_DIR` | `/app/var/nse_filings` | Default | On the Azure Files share. |
| `FUNDAMENTALS_DIR` | `/app/var/fundamentals` | Default | On the Azure Files share. |
| `UNIVERSE_SCOPE` | `all` | Env | `reference` = curated CSV in the image (smaller, faster first ingest). |
| `UNIVERSE_SERIES` | `EQ,BE` | Env | |
| `UNIVERSE_FILE` | code default (in image) | Default | |
| `NSE_EQUITY_LIST_URL` | `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | Env | |
| `NSE_INDUSTRY_LIST_URL` | `https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv` | Env | |
| `NSE_BHAVCOPY_URL_TEMPLATE` | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip` | Env | Keep the literal `{yyyymmdd}`. |
| `BENCHMARK_SYMBOL` | `NIFTY 50` | Env | |
| `HISTORY_YEARS` | `3` | Env | |
| `INGEST_MAX_CONCURRENCY` | `4` | Default | |
| `INGEST_RETRY_DELAY_SECONDS` | `65` | Default | |
| `MOCK_MCP_HOST` / `MOCK_MCP_PORT` | — | not used in live | Demo app only: `127.0.0.1` / `9100`. |

### 5.4 LLM (optional)

| Variable | Production value | Where | Notes |
|---|---|---|---|
| `LLM_ENABLED` | `false` until configured, then `true` | Env | |
| `LLM_PROVIDER_NAME` | e.g. `OpenRouter` | Env | Display label. |
| `LLM_BASE_URL` | e.g. `https://openrouter.ai/api/v1` | Env | Or an Azure AI Foundry / Azure OpenAI **OpenAI-compatible** endpoint if you want inference inside Azure — verify the route accepts `POST /chat/completions` with `Authorization: Bearer`. |
| `LLM_API_KEY` | provider key | **KV** `llm-api-key` | Never in YAML or GitHub. |
| `LLM_MODEL_ID` | e.g. `z-ai/glm-5.3-flash` | Env | Exact id as the provider lists it. |
| `LLM_EXTRA_HEADERS` | `{"X-Title": "Markets Intelligence"}` | Env | JSON; quote the whole value in YAML. |
| `LLM_EXTRA_BODY` | `{"usage": {"include": true}, "reasoning": {"effort": "low", "exclude": true}}` | Env | JSON. |
| `LLM_RESPONSE_FORMAT` | `json_schema` | Env | |
| `LLM_VERIFY_MODEL_LIST` | `true` | Env | |
| `LLM_TIMEOUT_SECONDS` | `60` | Env | Must stay below nginx `proxy_read_timeout 120s`. |
| `LLM_MAX_RETRIES` | `1` | Env | |
| `LLM_MAX_OUTPUT_TOKENS` | `6000` | Env | |
| `LLM_TEMPERATURE` | `0.1` | Default | |
| `LLM_DAILY_BUDGET` | `200` | Env | Calls per UTC day — your cost cap. |
| `LLM_DAILY_TOKEN_BUDGET` | `2000000` | Env | |

### 5.5 Web research

| Variable | Production value | Where | Notes |
|---|---|---|---|
| `WEB_SEARCH_PROVIDER` | `brave` recommended for production, `ddgs` acceptable for a trial, `none` to disable | Env | `ddgs` scrapes a public search front-end and may be rate-limited from cloud IPs. |
| `WEB_SEARCH_API_KEY` | Brave key | **KV** `web-search-api-key` | Only for `brave`. |
| `WEB_SEARCH_BASE_URL` | `https://api.search.brave.com/res/v1/news/search` | Env | Only for `brave`. |
| `WEB_SEARCH_MAX_RESULTS` | `8` | Env | |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `15` | Default | |

### 5.6 Schedules (cron in `DEFAULT_MARKET_TIMEZONE`)

| Variable | Value | Meaning |
|---|---|---|
| `THEME_REFRESH_CRON` | `30 6 1 * *` | Monthly theme snapshots, 06:30 IST on the 1st |
| `RRG_REFRESH_CRON` | `0 */12 * * *` | Sector rotation every 12 hours |
| `PRICE_REFRESH_CRON` | `30 19 * * 1-5` | End-of-day prices after NSE close |
| `INSTRUMENT_REFRESH_CRON` | `0 7 * * 1-5` | Instrument master |
| `ALERTS_CRON` | `*/15 * * * *` | Alert evaluation |

All **Env**. These fire only while the replica is running, which is why `minReplicas: 1` is mandatory.

### 5.7 Cache TTLs and rate limits

| Variable | Value | Where |
|---|---|---|
| `CACHE_TTL_QUOTE` / `CACHE_TTL_HISTORY` / `CACHE_TTL_INSTRUMENT` | `60` / `21600` / `86400` | Env |
| `RATE_LIMIT_RESEARCH_PER_MINUTE` | `6` | Env |
| `RATE_LIMIT_REFRESH_PER_MINUTE` | `3` | Env |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | `300` | Env |

Rate limiters are in-process (per replica) — another reason to stay at one replica for now.

### 5.8 Frontend (`web` container)

| Variable | Value | Notes |
|---|---|---|
| `API_UPSTREAM` | `127.0.0.1:8000` | Read by the nginx template at container start. |

The frontend has no build-time `VITE_*` variables; the image is environment-independent, so the
same image is promoted from staging to production.

### 5.9 Key Vault secret list

| Key Vault secret name | Feeds | Required |
|---|---|---|
| `database-url` | `DATABASE_URL` | Yes |
| `api-auth-token` | `API_AUTH_TOKEN` | Yes (public URL) |
| `llm-api-key` | `LLM_API_KEY` | If `LLM_ENABLED=true` |
| `web-search-api-key` | `WEB_SEARCH_API_KEY` | If `brave` |
| `redis-url` | `REDIS_URL` | Only when scaling out |
| `storage-account-key` | ACA environment storage (Azure Files) | Yes — used by `az` at setup, not by the app |

Key Vault secret names allow only letters, digits and `-`, hence the kebab-case names.

## 6. Step-by-step provisioning (az CLI)

Run from the repository root in bash/zsh with Azure CLI ≥ 2.60 and the `containerapp`
extension. Every value in `<…>` is yours to choose.

### 6.0 Variables and login

```bash
az login
az account set --subscription "<subscription-id>"
az extension add -n containerapp --upgrade
az provider register -n Microsoft.App --wait
az provider register -n Microsoft.OperationalInsights --wait
az provider register -n Microsoft.DBforPostgreSQL --wait

ENVN=prod
LOC=centralindia
RG=rg-markets-$ENVN-cin
VNET=vnet-markets-$ENVN
LAW=log-markets-$ENVN
APPI=appi-markets-$ENVN
ACR=acrmarkets$ENVN$RANDOM          # must be globally unique, lowercase alphanumeric
KV=kv-markets-$ENVN-$RANDOM         # globally unique, 3-24 chars
ST=stmarkets$ENVN$RANDOM            # globally unique, 3-24 lowercase alphanumeric
PG=psql-markets-$ENVN
ID=id-markets-$ENVN
CAE=cae-markets-$ENVN
APP=markets
JOB=markets-migrate
TAG=$(git rev-parse --short HEAD)
```

### 6.1 Resource group, network, monitoring

```bash
az group create -n $RG -l $LOC

az network vnet create -g $RG -n $VNET -l $LOC --address-prefixes 10.40.0.0/16
az network vnet subnet create -g $RG --vnet-name $VNET -n snet-aca \
  --address-prefixes 10.40.0.0/23 --delegations Microsoft.App/environments
az network vnet subnet create -g $RG --vnet-name $VNET -n snet-pg \
  --address-prefixes 10.40.2.0/28 --delegations Microsoft.DBforPostgreSQL/flexibleServers

az monitor log-analytics workspace create -g $RG -n $LAW -l $LOC --retention-time 30
LAW_ID=$(az monitor log-analytics workspace show -g $RG -n $LAW --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n $LAW --query primarySharedKey -o tsv)
az monitor app-insights component create -g $RG -a $APPI -l $LOC \
  --workspace $(az monitor log-analytics workspace show -g $RG -n $LAW --query id -o tsv)
```

Optional static egress IP (recommended if any upstream needs IP allow-listing):

```bash
az network public-ip create -g $RG -n pip-markets-egress --sku Standard
az network nat gateway create -g $RG -n ng-markets-$ENVN --public-ip-addresses pip-markets-egress
az network vnet subnet update -g $RG --vnet-name $VNET -n snet-aca --nat-gateway ng-markets-$ENVN
```

### 6.2 Identity, registry, Key Vault

```bash
az identity create -g $RG -n $ID
ID_RES=$(az identity show -g $RG -n $ID --query id -o tsv)
ID_PRINCIPAL=$(az identity show -g $RG -n $ID --query principalId -o tsv)

az acr create -g $RG -n $ACR --sku Basic --admin-enabled false
az role assignment create --assignee-object-id $ID_PRINCIPAL --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope $(az acr show -n $ACR --query id -o tsv)

az keyvault create -g $RG -n $KV -l $LOC --enable-rbac-authorization true --enable-purge-protection true
KV_ID=$(az keyvault show -n $KV --query id -o tsv)
az role assignment create --assignee-object-id $ID_PRINCIPAL --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" --scope $KV_ID
# You (the operator) need to write secrets:
az role assignment create --assignee $(az ad signed-in-user show --query id -o tsv) \
  --role "Key Vault Secrets Officer" --scope $KV_ID
```

### 6.3 Build the images in Azure (no local Docker needed)

ACR Tasks builds on `linux/amd64` in the cloud, so the Apple-Silicon `arm64` problem
disappears. The build context is the repo root; `.dockerignore` already keeps `.env`,
`backend/var`, `node_modules` and `.venv` out.

```bash
az acr build -r $ACR -t markets-api:$TAG -f infra/docker/backend.Dockerfile  .
az acr build -r $ACR -t markets-web:$TAG -f infra/docker/frontend.Dockerfile .
```

> Recommended code change (small): the backend Dockerfile copies uv from
> `ghcr.io/astral-sh/uv:latest`. Pin it (e.g. `ghcr.io/astral-sh/uv:0.x.y`) so cloud builds are
> reproducible.

### 6.4 PostgreSQL Flexible Server (private)

```bash
PG_ADMIN_PASS=$(openssl rand -hex 24)
az postgres flexible-server create -g $RG -n $PG -l $LOC --version 16 \
  --tier Burstable --sku-name Standard_B2s --storage-size 32 --storage-auto-grow Enabled \
  --backup-retention 14 \
  --admin-user pgadmin --admin-password "$PG_ADMIN_PASS" \
  --vnet $VNET --subnet snet-pg \
  --private-dns-zone markets-$ENVN.private.postgres.database.azure.com \
  --yes
az postgres flexible-server db create -g $RG -s $PG -d markets
# Keep the admin password only in Key Vault:
az keyvault secret set --vault-name $KV -n pg-admin-password --value "$PG_ADMIN_PASS" >/dev/null
```

Create the least-privilege application user. The server is private, so run `psql` from inside
the VNet — easiest is a one-off Container Apps Job using the `postgres:16-alpine` image after the
environment exists (§6.6), or temporarily from Azure Cloud Shell with VNet integration:

```sql
CREATE ROLE markets LOGIN PASSWORD '<APP_DB_PASS>';
GRANT CONNECT ON DATABASE markets TO markets;
\c markets
GRANT USAGE, CREATE ON SCHEMA public TO markets;
ALTER DATABASE markets OWNER TO markets;   -- Alembic creates and alters tables
```

Then store the connection string:

```bash
APP_DB_PASS=<the hex password used above>   # e.g. $(openssl rand -hex 24), generated before the SQL step
az keyvault secret set --vault-name $KV -n database-url \
  --value "postgresql+psycopg://markets:$APP_DB_PASS@$PG.postgres.database.azure.com:5432/markets?sslmode=require" >/dev/null
```

Quick-start alternative (no VNet; acceptable for dev only): create the server with
`--public-access 0.0.0.0` (allows Azure services) and skip the subnet; still use `sslmode=require`.

### 6.5 Other secrets

```bash
az keyvault secret set --vault-name $KV -n api-auth-token --value "$(openssl rand -hex 32)" >/dev/null
# Only if used — enter values without echoing them to the terminal history:
read -rs LLMKEY && az keyvault secret set --vault-name $KV -n llm-api-key --value "$LLMKEY" >/dev/null; unset LLMKEY
read -rs BRAVEKEY && az keyvault secret set --vault-name $KV -n web-search-api-key --value "$BRAVEKEY" >/dev/null; unset BRAVEKEY
```

Do **not** upload the local `.env` file wholesale: it contains local values (`DATA_MODE=demo`,
`localhost` URLs). Copy only the secret values listed in §5.9, and only after checking them.

### 6.6 Storage for `/app/var`, Container Apps environment

```bash
az storage account create -g $RG -n $ST -l $LOC --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false
az storage share-rm create -g $RG --storage-account $ST -n var --quota 5
ST_KEY=$(az storage account keys list -g $RG -n $ST --query '[0].value' -o tsv)

az containerapp env create -g $RG -n $CAE -l $LOC \
  --infrastructure-subnet-resource-id $(az network vnet subnet show -g $RG --vnet-name $VNET -n snet-aca --query id -o tsv) \
  --enable-workload-profiles \
  --logs-workspace-id $LAW_ID --logs-workspace-key $LAW_KEY

az containerapp env storage set -g $RG -n $CAE --storage-name markets-var \
  --azure-file-account-name $ST --azure-file-account-key "$ST_KEY" \
  --azure-file-share-name var --access-mode ReadWrite
unset ST_KEY
```

Restrict the storage account to the ACA subnet afterwards (`az storage account network-rule add`
+ default action Deny) once mounting is confirmed to work.

### 6.7 Migration job

```bash
az containerapp job create -g $RG -n $JOB --environment $CAE \
  --trigger-type Manual --replica-timeout 900 --replica-retry-limit 0 --parallelism 1 --replica-completion-count 1 \
  --image $ACR.azurecr.io/markets-api:$TAG \
  --registry-server $ACR.azurecr.io --registry-identity $ID_RES \
  --mi-user-assigned $ID_RES \
  --secrets "database-url=keyvaultref:https://$KV.vault.azure.net/secrets/database-url,identityref:$ID_RES" \
  --env-vars DATABASE_URL=secretref:database-url APP_ENV=production DATA_MODE=live AUTO_CREATE_SCHEMA=false SCHEDULER_ENABLED=false \
  --cpu 0.5 --memory 1Gi \
  --command alembic --args "upgrade" "head"

EXEC=$(az containerapp job start -g $RG -n $JOB --query name -o tsv)
az containerapp job execution show -g $RG -n $JOB --job-execution-name $EXEC --query properties.status -o tsv
# Repeat until Succeeded (or Failed → check logs, §11)
```

### 6.8 The Container App

Fill the placeholders in the YAML from §7 (save as `infra/azure/containerapp.yaml`; the repo
does not have this folder yet), then:

```bash
az containerapp create -g $RG -n $APP --yaml infra/azure/containerapp.yaml
az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv
```

### 6.9 Custom domain and certificate (free managed cert)

```bash
FQDN=$(az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv)
# DNS: CNAME markets.<yourdomain> -> $FQDN, and TXT asuid.markets.<yourdomain> -> verification id:
az containerapp show -g $RG -n $APP --query properties.customDomainVerificationId -o tsv
az containerapp hostname add  -g $RG -n $APP --hostname markets.<yourdomain>
az containerapp hostname bind -g $RG -n $APP --hostname markets.<yourdomain> --environment $CAE --validation-method CNAME
```

## 7. The Container App definition (YAML)

Replace `<…>`. `identityref`/`identity` use the managed identity resource id (`$ID_RES`).

```yaml
location: centralindia
identity:
  type: UserAssigned
  userAssignedIdentities:
    "<ID_RES>": {}
properties:
  managedEnvironmentId: "<az containerapp env show -g RG -n CAE --query id -o tsv>"
  workloadProfileName: Consumption
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: 8080              # nginx (web); the API is reachable only through it
      transport: auto
      allowInsecure: false          # HTTP is redirected to HTTPS
    registries:
      - server: <ACR>.azurecr.io
        identity: "<ID_RES>"
    secrets:
      - name: database-url
        keyVaultUrl: https://<KV>.vault.azure.net/secrets/database-url
        identity: "<ID_RES>"
      - name: api-auth-token
        keyVaultUrl: https://<KV>.vault.azure.net/secrets/api-auth-token
        identity: "<ID_RES>"
      - name: llm-api-key           # remove if the LLM is not used
        keyVaultUrl: https://<KV>.vault.azure.net/secrets/llm-api-key
        identity: "<ID_RES>"
  template:
    scale:
      minReplicas: 1                # cron jobs must fire; Kite session must stay alive
      maxReplicas: 1                # in-process scheduler, locks, rate limits (§14 to go wider)
    volumes:
      - name: var
        storageType: AzureFile
        storageName: markets-var
        mountOptions: "uid=10001,gid=10001,dir_mode=0700,file_mode=0600,nobrl"
    containers:
      - name: web
        image: <ACR>.azurecr.io/markets-web:<TAG>
        resources: { cpu: 0.25, memory: 0.5Gi }
        env:
          - { name: API_UPSTREAM, value: "127.0.0.1:8000" }
        probes:
          - type: Liveness
            httpGet: { path: /, port: 8080 }
            periodSeconds: 30
      - name: api
        image: <ACR>.azurecr.io/markets-api:<TAG>
        resources: { cpu: 1.0, memory: 2Gi }   # web+api = 1.25 vCPU / 2.5 GiB, a valid Consumption combination
        volumeMounts:
          - { volumeName: var, mountPath: /app/var }
        env:
          # --- application
          - { name: APP_ENV, value: production }
          - { name: DATA_MODE, value: live }
          - { name: LOG_LEVEL, value: INFO }
          - { name: DEFAULT_MARKET_TIMEZONE, value: Asia/Kolkata }
          - { name: SCHEDULER_ENABLED, value: "true" }
          - { name: AUTO_CREATE_SCHEMA, value: "false" }
          - { name: AUTO_INITIAL_REFRESH, value: "true" }
          - { name: CORS_ORIGINS, value: "" }
          - { name: DATABASE_URL, secretRef: database-url }
          - { name: API_AUTH_TOKEN, secretRef: api-auth-token }
          # --- market data
          - { name: NSE_BHAVCOPY_MCP_URL, value: "https://mcp.nseindia.in/bhavcopy/cm/mcp" }
          - { name: NSE_CM_MARKET_MCP_URL, value: "https://mcp.nseindia.in/cmmkt/mcp" }
          - { name: BROKER_MCP_URL, value: "https://mcp.kite.trade/mcp" }
          - { name: KITE_PERSIST_SESSION, value: "true" }
          - { name: NSE_FILINGS_BASE_URL, value: "https://www.nseindia.com/api" }
          - { name: NSE_FILINGS_DOCUMENT_HOSTS, value: "nsearchives.nseindia.com" }
          - { name: NSE_FILINGS_MIN_INTERVAL_SECONDS, value: "1.0" }
          - { name: NSE_FILINGS_TIMEOUT_SECONDS, value: "30" }
          - { name: NSE_FILINGS_MAX_COMPANIES_PER_RUN, value: "400" }
          - { name: UNIVERSE_SCOPE, value: all }
          - { name: UNIVERSE_SERIES, value: "EQ,BE" }
          - { name: NSE_EQUITY_LIST_URL, value: "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv" }
          - { name: NSE_INDUSTRY_LIST_URL, value: "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv" }
          - { name: NSE_BHAVCOPY_URL_TEMPLATE, value: "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip" }
          - { name: BENCHMARK_SYMBOL, value: "NIFTY 50" }
          - { name: HISTORY_YEARS, value: "3" }
          # --- LLM (set LLM_ENABLED=true once the key is in Key Vault)
          - { name: LLM_ENABLED, value: "false" }
          - { name: LLM_PROVIDER_NAME, value: OpenRouter }
          - { name: LLM_BASE_URL, value: "https://openrouter.ai/api/v1" }
          - { name: LLM_API_KEY, secretRef: llm-api-key }
          - { name: LLM_MODEL_ID, value: "z-ai/glm-5.3-flash" }
          - { name: LLM_EXTRA_HEADERS, value: '{"X-Title": "Markets Intelligence"}' }
          - { name: LLM_EXTRA_BODY, value: '{"usage": {"include": true}, "reasoning": {"effort": "low", "exclude": true}}' }
          - { name: LLM_RESPONSE_FORMAT, value: json_schema }
          - { name: LLM_MAX_OUTPUT_TOKENS, value: "6000" }
          - { name: LLM_DAILY_BUDGET, value: "200" }
          - { name: LLM_DAILY_TOKEN_BUDGET, value: "2000000" }
          # --- web research
          - { name: WEB_SEARCH_PROVIDER, value: ddgs }
          - { name: WEB_SEARCH_MAX_RESULTS, value: "8" }
          # --- schedules (Asia/Kolkata)
          - { name: THEME_REFRESH_CRON, value: "30 6 1 * *" }
          - { name: RRG_REFRESH_CRON, value: "0 */12 * * *" }
          - { name: PRICE_REFRESH_CRON, value: "30 19 * * 1-5" }
          - { name: INSTRUMENT_REFRESH_CRON, value: "0 7 * * 1-5" }
          - { name: ALERTS_CRON, value: "*/15 * * * *" }
          # --- cache / limits
          - { name: CACHE_TTL_QUOTE, value: "60" }
          - { name: CACHE_TTL_HISTORY, value: "21600" }
          - { name: CACHE_TTL_INSTRUMENT, value: "86400" }
          - { name: RATE_LIMIT_RESEARCH_PER_MINUTE, value: "6" }
          - { name: RATE_LIMIT_REFRESH_PER_MINUTE, value: "3" }
          - { name: RATE_LIMIT_DEFAULT_PER_MINUTE, value: "300" }
        probes:
          - type: Startup               # first start runs migrations-check + seeding; give it time
            httpGet: { path: /health/live, port: 8000 }
            periodSeconds: 10
            failureThreshold: 30
          - type: Liveness
            httpGet: { path: /health/live, port: 8000 }
            periodSeconds: 30
          - type: Readiness
            httpGet: { path: /health/ready, port: 8000 }
            periodSeconds: 15
```

Notes:

- The Key Vault secret referenced by `llm-api-key` must exist, even if `LLM_ENABLED=false`
  (store a placeholder like `unset`), or remove both the secret and the env entry.
- If Azure Files mount options are rejected in your region/CLI version, drop `mountOptions`
  and check whether uid 10001 can write `/app/var` (`az containerapp exec … -- touch /app/var/x`).
  The SMB mount ignores `chmod`; the code's `0o600` request is harmless there.
- In single-revision mode, an update starts the new replica before stopping the old one, so for
  a short window two schedulers can exist. Cron jobs are minutes/hours apart and `jobs.run`
  records each run, so the practical risk is low; avoid deploying exactly at 19:30 IST, the
  12-hour RRG marks, or 06:30 IST on the 1st.

## 8. First start: data, Kite login, verification

1. **Watch the start:** `az containerapp logs show -g $RG -n $APP --container api --follow`.
   Expect JSON log lines; with an empty DB, `AUTO_INITIAL_REFRESH` triggers the full ingest.
2. **Health:** `curl https://<fqdn>/health/live` → `{"status":"ok"}`;
   `curl https://<fqdn>/health/ready` → `ready`, with `checks.database: ok` and per-source states.
3. **Open the app**, paste the `API_AUTH_TOKEN` (from Key Vault: `az keyvault secret show
   --vault-name $KV -n api-auth-token --query value -o tsv`) when prompted.
4. **Kite login (once, then after Kite ends the session):** Data status page → connect Kite →
   open the freshly issued link → approve in Zerodha. The approval binds to the API's MCP session;
   the session id is saved to `/app/var/kite_mcp_session.json` on Azure Files, so it survives
   restarts and new revisions. If Kite ends the session (it does so periodically), log in again.
5. **Data status page:** confirm NSE MCP tools were discovered, coverage, last-success times,
   and scheduler `next_run` values in IST.
6. **Trigger a manual refresh** if needed: `POST /api/v1/jobs/refresh-all` (rate-limited) and
   follow it with `GET /api/v1/jobs/{job_id}`.

## 9. CI/CD with GitHub Actions (OIDC, no stored passwords)

Keep the existing `.github/workflows/ci.yml` (lint, types, tests, migrations check, e2e). Add a
deploy workflow that runs on `main` after CI passes.

### 9.1 One-time setup: federated credential

```bash
APP_ID=$(az ad app create --display-name gh-markets-deploy --query appId -o tsv)
az ad sp create --id $APP_ID
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<github-owner>/<repo>:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}'
SP_ID=$(az ad sp show --id $APP_ID --query id -o tsv)
# Least privilege: push images + manage the app and job in this resource group
az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal \
  --role AcrPush --scope $(az acr show -n $ACR --query id -o tsv)
az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal \
  --role Contributor --scope $(az group show -n $RG --query id -o tsv)
```

GitHub → Settings → Environments → `production` (add required reviewers) → variables (not
secrets — none of these are secret): `AZURE_CLIENT_ID=$APP_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, `AZ_RG`, `AZ_ACR`, `AZ_APP=markets`, `AZ_JOB=markets-migrate`.

### 9.2 `.github/workflows/deploy-azure.yml` (proposed)

```yaml
name: deploy-azure
on:
  workflow_run: { workflows: [ci], types: [completed], branches: [main] }
  workflow_dispatch:
permissions: { id-token: write, contents: read }
concurrency: { group: deploy-production, cancel-in-progress: false }

jobs:
  deploy:
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    environment: production
    env:
      TAG: ${{ github.event.workflow_run.head_sha || github.sha }}
    steps:
      - uses: actions/checkout@v4
        with: { ref: "${{ env.TAG }}" }
      - uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      - run: az extension add -n containerapp --upgrade

      - name: Build images in ACR (linux/amd64)
        run: |
          az acr build -r ${{ vars.AZ_ACR }} -t markets-api:$TAG -f infra/docker/backend.Dockerfile .
          az acr build -r ${{ vars.AZ_ACR }} -t markets-web:$TAG -f infra/docker/frontend.Dockerfile .

      - name: Migrate (must succeed before the app is updated)
        run: |
          IMG=${{ vars.AZ_ACR }}.azurecr.io/markets-api:$TAG
          az containerapp job update -g ${{ vars.AZ_RG }} -n ${{ vars.AZ_JOB }} --image $IMG
          EXEC=$(az containerapp job start -g ${{ vars.AZ_RG }} -n ${{ vars.AZ_JOB }} --query name -o tsv)
          for i in $(seq 1 90); do
            S=$(az containerapp job execution show -g ${{ vars.AZ_RG }} -n ${{ vars.AZ_JOB }} --job-execution-name $EXEC --query properties.status -o tsv)
            echo "migration: $S"; [ "$S" = Succeeded ] && exit 0
            [ "$S" = Failed ] && exit 1; sleep 10
          done; exit 1

      - name: Deploy both containers in one revision
        run: |
          sed -e "s|<TAG>|$TAG|g" infra/azure/containerapp.yaml > /tmp/app.yaml
          az containerapp update -g ${{ vars.AZ_RG }} -n ${{ vars.AZ_APP }} --yaml /tmp/app.yaml

      - name: Smoke test
        run: |
          FQDN=$(az containerapp show -g ${{ vars.AZ_RG }} -n ${{ vars.AZ_APP }} --query properties.configuration.ingress.fqdn -o tsv)
          for i in $(seq 1 30); do curl -fsS https://$FQDN/health/ready && exit 0; sleep 10; done; exit 1
```

For this to work, commit `infra/azure/containerapp.yaml` with every value filled in **except**
`<TAG>` — it contains only resource ids and Key Vault URLs, never secret values.

Migrations must stay **backward compatible** with the previous image for one release (expand →
deploy → contract), because the old revision keeps running until the new one is ready and a
rollback reuses the migrated schema.

## 10. Security hardening

| Area | Practice |
|---|---|
| Secrets | Only in Key Vault; app reads via managed identity. Nothing secret in YAML, GitHub, images or logs. Purge protection on. Rotate `api-auth-token`, DB password, LLM key at least every 90 days (update the KV secret → `az containerapp revision restart`). |
| Identity | User-assigned managed identity for ACR pull and KV; GitHub deploys via OIDC (no client secret). ACR admin user disabled. |
| Network | PostgreSQL private access only (no public endpoint). Storage account restricted to the ACA subnet. Only the `web` container's port 8080 is exposed; the API's `:8000` and `/metrics` are not reachable from the internet. |
| Access to the app | `API_AUTH_TOKEN` is a single shared token. For a team, add **ACA built-in authentication (Easy Auth) with Microsoft Entra ID** so only signed-in users reach the site: `az containerapp auth microsoft update …` and `az containerapp auth update --unauthenticated-client-action RedirectToLoginPage --excluded-paths /health/live,/health/ready`. Keep `API_AUTH_TOKEN` as a second layer. |
| Edge | Optional **Azure Front Door Premium + WAF** (managed rule set, rate limiting, geo-filter to India if appropriate). Then lock ACA ingress to Front Door (IP restrictions to the `AzureFrontDoor.Backend` service tag + check the `X-Azure-FDID` header). |
| TLS | Managed certificate on the custom domain; `allowInsecure: false`. nginx already sets CSP, `X-Frame-Options DENY`, `nosniff`, `Referrer-Policy`. Add `Strict-Transport-Security` in `nginx.conf.template` once the domain is final. |
| Images | Pin base images (`python:3.12-slim`, `node:22-alpine`, `nginx:1.27-alpine`) by digest and pin the uv image tag; enable Microsoft Defender for Containers (registry scanning) or run `trivy` in CI. Non-root already. |
| Database | App user `markets` owns only the `markets` database; the server admin password lives only in KV. Enforce TLS (`require_secure_transport=on`, the default). Consider Entra ID authentication for operators. |
| Egress | NAT Gateway gives one static IP; if you need to restrict egress, add Azure Firewall with an FQDN allow-list: `mcp.nseindia.in`, `mcp.kite.trade`, `kite.zerodha.com`, `www.nseindia.com`, `nsearchives.nseindia.com`, the LLM host, the search host, plus Azure platform dependencies. |
| Audit | Azure Activity Log for control-plane changes; the app's own `audit_events` table for user actions. |
| Compliance | NSE MCP data is delayed and for informational/educational use. Review NSE, Zerodha/Kite and search-provider terms, data redistribution limits, and add jurisdiction-specific disclaimers before any public launch. Keep the app private (Easy Auth) until that review is done. |

## 11. Monitoring, logs and alerts

**Logs.** Container stdout (structured JSON) lands in Log Analytics table
`ContainerAppConsoleLogs_CL`; platform events in `ContainerAppSystemLogs_CL`.

```kusto
// API errors in the last hour
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "markets" and ContainerName_s == "api"
| where Log_s has "\"level\": \"error\"" or Log_s has "unhandled_error"
| project TimeGenerated, Log_s
| order by TimeGenerated desc

// Restarts, probe failures, OOM
ContainerAppSystemLogs_CL
| where ContainerAppName_s == "markets"
| where Reason_s in ("ProbeFailed", "ContainerTerminated", "BackOff") or Log_s has "OOM"
```

**Availability.** Application Insights standard availability test against
`https://<domain>/health/ready` every 5 minutes from 3+ locations (excluded from Easy Auth).

**Alerts (Azure Monitor, to an action group with email/Teams):**

| Alert | Signal |
|---|---|
| Site down / not ready | Availability test failing 2 of 3 locations |
| Container restarts | `RestartCount` metric on the Container App > 2 in 15 min |
| CPU/memory pressure | `UsageNanoCores` / `WorkingSetBytes` > 85 % for 15 min (raise api to 2 vCPU/4 GiB if hit) |
| Scheduled job failures | Log query for failed `job_runs` entries in the logs (e.g. job status `failed`) in 24 h |
| Stale data | Log/`/health/ready` source state not `ok` for > 6 h on trading days |
| PostgreSQL | CPU > 80 %, storage > 80 %, connection failures |
| LLM budget | Log lines about daily budget exhausted |
| Storage | File share capacity > 80 % of quota |

The existing Prometheus rules in `infra/monitoring/alerts.yml` describe the same signals; to
use them as-is, run a small Prometheus/Grafana (or **Azure Monitor managed Prometheus + Azure
Managed Grafana**) that scrapes `api:8000/metrics` from inside the environment — e.g. an
OpenTelemetry Collector container in the same app scraping `127.0.0.1:8000/metrics` and
exporting to Azure Monitor. Optional; logs + availability tests cover the essentials.

## 12. Backups, restore and disaster recovery

| Data | Backup | Restore |
|---|---|---|
| PostgreSQL (system of record: instruments, prices, metrics, theme snapshots, RRG snapshots, research reports, watchlists, audit) | Automatic daily backups + WAL, 14-day PITR (`--backup-retention 14`, up to 35). Prod: enable **geo-redundant backup** at create time. Weekly logical `pg_dump -Fc` from a scheduled Container Apps Job to a Blob container with immutability policy. | PITR to a **new** server: `az postgres flexible-server restore -g $RG -n psql-markets-restore --source-server $PG --restore-time "2026-10-25T10:00:00Z"`, then point `database-url` in KV at it and restart the revision. |
| Azure Files `/app/var` (Kite session, uploaded statements, filings cache) | Azure Backup for Azure Files (daily snapshots, 30 days) or `az storage share snapshot` on a schedule. | Restore the share/snapshot; restart the revision. The filings cache can always be re-fetched; uploaded statements cannot — back them up. |
| Configuration | The YAML in git + Key Vault (soft-delete + purge protection). | Re-run §6 against a new resource group. |
| Images | ACR keeps every tag; enable a retention policy for untagged manifests only. | Redeploy any previous tag. |

**Targets (suggested):** RPO ≤ 15 min (PITR), RTO ≤ 2 h for a full region rebuild into
South India using geo-restored DB + the YAML and scripts above. Test a restore quarterly and
write the result into `docs/operations.md`.

**Rollback of a bad release:** redeploy the previous tag (`sed` the old SHA into the YAML and
`az containerapp update --yaml`). Do not roll back migrations automatically; that is why
migrations are expand-then-contract.

## 13. Environments: dev / staging (demo) / production

| Env | Resource group | Mode | Sizing | Notes |
|---|---|---|---|---|
| dev | `rg-markets-dev-cin` | `demo` or `live` | PG `B1ms`, public access + firewall OK | Can scale to zero overnight (scheduler then does not run). |
| staging | `rg-markets-stg-cin` | `demo` | PG `B1ms` | Runs the e2e suite against a cloud URL. Same images as prod. |
| prod | `rg-markets-prod-cin` | `live` | PG `B2s`+, api 1–2 vCPU | Required reviewers on the GitHub environment. |

**Demo mode in the cloud** works because containers in one app share `127.0.0.1`. Add a third
container to the staging YAML and set two env vars on `api`:

```yaml
      - name: mock-mcp
        image: <ACR>.azurecr.io/markets-api:<TAG>
        command: ["python", "-m", "app.devtools.mock_mcp", "--port", "9100", "--host", "127.0.0.1"]
        resources: { cpu: 0.25, memory: 0.5Gi }
# api env: DATA_MODE=demo, MOCK_MCP_HOST=127.0.0.1   (web+api+mock = 1.5 vCPU / 3 GiB)
```

The UI labels everything demo/synthetic, so staging can never be mistaken for live data.
Never point a demo app at the production database.

## 14. Scaling beyond one replica

One replica comfortably serves a small team; scale vertically first (api 2 vCPU / 4 GiB). To
scale horizontally:

1. **Add Azure Managed Redis** (or Azure Cache for Redis), private endpoint in the VNet, store
   `rediss://…` in KV as `redis-url`, set `REDIS_URL=secretref:redis-url`. This moves cache and
   job locks out of process (`core/cache.py`).
2. **Split the scheduler out**: a second Container App `markets-worker` with **no ingress**,
   same backend image, `command: ["python","-m","app.workers.run"]`, `SCHEDULER_ENABLED=true`,
   `minReplicas: maxReplicas: 1`, same `/app/var` mount.
3. On the public `markets` app set `SCHEDULER_ENABLED=false` and allow `maxReplicas: 3` with
   an HTTP scale rule (e.g. 50 concurrent requests).

**Blocker to verify first:** the Kite authorisation is tied to one process's MCP session. With
a separate worker, the worker restores the saved session id from the shared Azure Files file
at start-up, but whether Kite accepts the same session from two processes, and how a re-login
in the API reaches the worker, is **untested**. Until that is verified, keep one replica and
scale vertically. Rate limiters are also per-process (they would allow N× the limit).

## 15. Cost estimate

Rough monthly pay-as-you-go estimate for **one production environment** in Central India,
before taxes, with no reservations. These are planning approximations, not quotes — confirm in
the Azure Pricing Calculator for your subscription and currency.

| Item | Assumption | Approx. USD / month |
|---|---|---|
| Container App (always on) | 1.25 vCPU + 2.5 GiB, 730 h, Consumption | 50 – 70 |
| Migration job | a few minutes per release | < 1 |
| PostgreSQL Flexible Server | Burstable B2s + 32 GiB + backups | 35 – 55 (B1ms: 15 – 25) |
| Azure Files | 5 GiB transaction-optimized + snapshots | 1 – 3 |
| Container Registry | Basic | ~5 |
| Key Vault | a few thousand operations | < 1 |
| Log Analytics + App Insights | 1–3 GB ingestion | 3 – 10 |
| NAT Gateway (optional) | gateway + IP + low data | 35 – 45 |
| Front Door Premium + WAF (optional) | base fee | 300+ |
| LLM / search providers | billed by those providers; capped by `LLM_DAILY_BUDGET` | your budget |

**Typical total without optional items: ≈ USD 95 – 145 / month.** Cheapest viable setup for a
personal/hackathon deployment: PG `B1ms`, public access instead of VNet, no NAT/Front Door ≈
USD 75 – 100. Savings: 1-year reserved PG capacity; Log Analytics daily cap; scale dev to zero.

## 16. Risks and open questions

| Risk | Impact | Mitigation |
|---|---|---|
| `www.nseindia.com` / `nsearchives.nseindia.com` often throttle or block data-centre IP ranges | Filings, transcripts, bhavcopy files and the equity list may fail from Azure even though they work from a home connection | Test from the cloud early (§18 step 4). The app degrades honestly (sources show unavailable). Options: static egress IP via NAT Gateway and ask for allow-listing; rely on NSE MCP (an official endpoint) for prices; set `NSE_FILINGS_BASE_URL` empty and upload transcripts/statements instead. Do not bypass access controls. |
| NSE MCP / Kite MCP availability and terms | Live data gaps; licensing | Connector health on the Data status page; read their terms before exposing the app publicly. |
| Kite session expiry | Broker gap-filling stops until someone logs in again | Alert on `kite_mcp` state; document "re-login" in the runbook. |
| Single replica | Brief downtime during platform maintenance or a restart | Acceptable for research use; §14 for HA. PostgreSQL zone-redundant HA if the DB is the concern. |
| SMB semantics for `/app/var` | Atomic `os.replace` and file permissions behave differently on SMB | Verified mount options in §7; test a Kite login + restart in staging before prod. |
| `ddgs` web search from cloud IPs | Search rate-limited/blocked | Use `brave` with an API key in production. |
| Unpinned `uv:latest` in the backend Dockerfile | Non-reproducible builds | Pin the tag (§6.3). |
| Compose/Dockerfiles not previously exercised in a cloud | First deploy surprises | Do staging first; keep this doc's §18 checklist. |

**Decisions you need to make:** custom domain name; whether access is team-only (Easy Auth)
or public; LLM provider and budget; Brave vs ddgs; whether NAT Gateway / Front Door are worth
their cost; dev+staging+prod or prod only.

## 17. Day-2 runbook (quick reference)

| Task | Command |
|---|---|
| Tail API logs | `az containerapp logs show -g $RG -n markets --container api --follow` |
| Tail web logs | `az containerapp logs show -g $RG -n markets --container web --follow` |
| Shell into API | `az containerapp exec -g $RG -n markets --container api --command sh` |
| Restart | `az containerapp revision restart -g $RG -n markets --revision $(az containerapp show -g $RG -n markets --query properties.latestRevisionName -o tsv)` |
| Run migrations | `az containerapp job start -g $RG -n markets-migrate` |
| List revisions | `az containerapp revision list -g $RG -n markets -o table` |
| Roll back | deploy the previous image tag via the YAML (§12) |
| Rotate a secret | `az keyvault secret set --vault-name $KV -n api-auth-token --value "$(openssl rand -hex 32)"` → restart revision |
| Change a setting | edit the env entry in `infra/azure/containerapp.yaml` → `az containerapp update --yaml …` (creates a new revision) |
| Manual full refresh | `POST https://<domain>/api/v1/jobs/refresh-all` with the bearer token |
| DB shell | `az containerapp exec` into api, or a one-off job with `postgres:16-alpine` running `psql "$DATABASE_URL"` (strip `+psycopg` from the scheme for psql) |
| PITR restore | `az postgres flexible-server restore …` (§12) |
| Kite re-login | Data status page → connect Kite |

## 18. Migration checklist

1. [ ] Decide §16 open questions (domain, access model, LLM, search, optional network items).
2. [ ] Small repo changes: pin uv image tag; add `infra/azure/containerapp.yaml` (and the
       staging variant); add `.github/workflows/deploy-azure.yml`; add HSTS header to nginx
       once the domain is final; fix the `HF_TOKEN` mentions in `.env.example`/older docs.
3. [ ] Provision **staging** with §6 (`DATA_MODE=demo`, §13) and run the Playwright e2e suite
       against its URL.
4. [ ] Provision **production** with §6; from `az containerapp exec` in the api container, test
       reachability: `python -c "import urllib.request;print(urllib.request.urlopen('https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv',timeout=15).status)"`
       and the same for `https://mcp.nseindia.in/cmmkt/mcp` (any HTTP response, even 4xx, proves reachability).
5. [ ] Run the migration job → Succeeded.
6. [ ] Create the Container App → `/health/ready` is `ready`.
7. [ ] Kite login; restart the revision; confirm the session was restored (Data status page shows
       `restored_from_previous_run`).
8. [ ] Upload one statement file; restart; confirm it is still there (Azure Files works).
9. [ ] Wait for (or trigger) the RRG and price jobs; confirm `job_runs` succeed and timestamps
       are in IST.
10. [ ] Custom domain + managed cert; Easy Auth if team-only.
11. [ ] Alerts and availability test in place; send a test alert.
12. [ ] Backups: confirm PITR window; run one test restore to a scratch server; Azure Files backup on.
13. [ ] Wire GitHub OIDC deploy; do one release end-to-end through the pipeline.
14. [ ] Update `PLAN.md` and `docs/operations.md` with what was actually deployed and verified.
