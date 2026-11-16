# Running Locally and on Azure — Integration Plan

**Status:** plan only. No code has been changed yet.
**Related:** [`new_analysis/04-azure-migration.md`](new_analysis/04-azure-migration.md) (Azure resources, env-var mapping, cost) · [`new_analysis/azure-setup-guide.md`](new_analysis/azure-setup-guide.md) (portal walkthrough)

## 1. The goal in one paragraph

One codebase and one pair of container images should run in two places. On a laptop it should
behave exactly as it does today. On Azure it should run as an enterprise deployment: several
replicas, files in Blob Storage, Microsoft (Entra ID) sign-in, a passwordless database login and
Application Insights monitoring. A single setting, `DEPLOY_TARGET`, chooses between the two. It
defaults to `local`, so anyone who changes nothing sees no difference.

## 2. How the switch works

The backend already builds every dependency in one place, `build_container()` in
`backend/app/core/container.py`. The plan keeps that single wiring point. Each piece of
infrastructure gets a small interface with two versions, one local and one Azure, and the
container picks a version from the settings.

`DEPLOY_TARGET` only supplies **defaults**. Each concern has its own setting that can be overridden
on its own. For example, you can test Blob storage locally while still using the local login.

| Concern | Setting | `local` (default) | `azure` |
|---|---|---|---|
| File storage | `STORAGE_BACKEND` | Folder `backend/var` (unchanged) | Azure Blob Storage |
| API sign-in | `AUTH_MODE` | None, or the existing `API_AUTH_TOKEN` | Entra ID with roles |
| Database login | `DATABASE_AUTH` | Password in `DATABASE_URL` | Managed identity (no password) |
| Job locks | automatic | In-process lock (Redis if set) | PostgreSQL advisory locks |
| Rate limits | automatic | In-process counter | Shared through Redis |
| Monitoring | automatic | Console logs and `/metrics` | Application Insights, on when its connection string is set |
| Frontend settings | `/config.json` | Token mode, as today | Entra sign-in settings, written when the container starts |

The Azure libraries are an **optional extra**. They load only when an Azure option is in use, so a
laptop never needs them installed.

### Safety checks

- **Startup check.** With `DEPLOY_TARGET=azure`, the API and the worker refuse to start when the
  configuration is unsafe or incomplete. They list every problem at once. The checks cover:
  - a database URL that points at localhost, isn't PostgreSQL, or contains a password;
  - `AUTH_MODE=none`;
  - missing Entra or storage settings;
  - demo data in production;
  - automatic schema creation;
  - MCP URLs that point at a local mock.
- **`config_check` command.** `python -m app.devtools.config_check` prints which version of each
  concern will be used and any problems. It never prints secrets. It works on a laptop, in CI and
  inside a running Azure container.
- **Local Azure rehearsal.** A Compose profile, `azure-local`, adds **Azurite** (Microsoft's
  storage emulator) and an `api-azure` service that runs the Azure configuration against it. An
  emulator-only setting, `ALLOW_LOCAL_HOSTS`, relaxes just the "points at a local host" checks so
  this works. Every other check still applies.

## 3. What changes, concern by concern

### 3.1 File storage

Four things are written to disk today:
- uploaded financial statements;
- the NSE filings cache;
- the industry-label files;
- the saved Kite login session.

On Azure a container's disk is lost on every restart and isn't shared between replicas. All four
move behind one storage interface with a local-folder version and a Blob version.

- **Same layout:** the storage keys match today's folder layout, so existing local data keeps
  working without a migration.
- **File protection:** local files keep their owner-only permissions and are still replaced in one
  step, so a crash can't leave half a file.
- **Outages:** if Blob Storage is briefly unreachable, a filings read counts as a cache miss and
  the data shows as degraded. The request never fails with an error page.

### 3.2 Kite session shared between API and worker

On Azure the API and the worker run as separate apps. A Kite login done through the API has to
reach the worker. The worker's keep-alive loop checks the shared session file and adopts a newer
session without a restart.

**Not yet verified:** whether Kite accepts one session from two processes. This is tested against
the real Kite service on staging. If Kite refuses, the fallback is to run the API and the worker
as one app, which is the small topology in `04-azure-migration.md`.

### 3.3 Scheduled jobs across replicas

Today's job lock works within one process only. Two replicas could run the same scheduled job at
the same moment.
- **On PostgreSQL:** jobs use PostgreSQL advisory locks, so exactly one replica runs a job. The
  other replica records the run as `skipped`.
- **Interrupted-job clean-up:** a job that another replica is still running is no longer marked
  "interrupted".
- **On SQLite:** test runs keep the current lock.

### 3.4 Rate limits across replicas

When `REDIS_URL` is set, the rate limits are counted in Redis, so every replica shares one limit.
Without Redis, the in-process counter is used, as today.

### 3.5 Passwordless database login

With `DATABASE_AUTH=azure_ad`, each new database connection uses a short-lived Entra token from the
app's managed identity instead of a password. The same applies to migrations. Pooled connections
are recycled every 30 minutes so none outlives its token. There's a one-time setup step: register
the identity in PostgreSQL. The steps are in the infrastructure README.

### 3.6 Microsoft sign-in and roles

With `AUTH_MODE=entra`, the API accepts only valid Entra tokens. Each token's signature, issuer,
audience and expiry are checked. Anything invalid gets a 401 and never falls back to the
anonymous local user. Users are recorded as `entra:<user id>`.

| Role | Can do |
|---|---|
| Viewer (any signed-in user) | Every read-only page |
| `Markets.Analyst` | Research, ask, AI filters, concall analysis, and editing themes, watchlists and alerts |
| `Markets.Admin` (includes Analyst) | Broker login and logout, data refreshes, source rediscovery, financial uploads |

In `none` and `token` modes the single local user keeps every role, as today.

### 3.7 Frontend

- **Settings:** the web app reads `/config.json` at startup instead of having settings baked into
  the build. The nginx container writes that file from environment variables when it starts, and
  rejects any value with unsafe characters.
- **Sign-in:** in Entra mode the app signs users in with Microsoft and sends their token to the
  API. In token mode the existing token field on the Status page stays.
- **nginx:** three changes, all harmless locally:
  - send the upstream host name to the API, which Container Apps needs;
  - let the security policy allow the Microsoft login domain;
  - never cache `/config.json`.

### 3.8 Monitoring

When `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, the API and worker send traces, logs and
metrics to Application Insights as `markets-api` and `markets-worker`. Health and metrics endpoints
are excluded from traces. Without the connection string nothing changes.

## 4. Two ways to run on Azure

| | Small (in `04-azure-migration.md`) | Enterprise (this plan) |
|---|---|---|
| Layout | One Container App with web and API side by side. One replica. The scheduler runs in the API. | Separate web, API and worker apps, with a migration job |
| Replicas | Exactly 1 | Web 2, API 2–5 (scales on load), worker 1 |
| Files | Azure Files share mounted at `/app/var` | Blob Storage through the storage interface |
| Database login | Password from Key Vault | Managed identity |
| Sign-in | API token | Entra ID with roles |
| Entry point | Container Apps ingress | Front Door with a web application firewall, apps on internal ingress |
| Code changes needed | None | Everything in section 3 |
| Approx. cost | About USD 95–145 a month | Higher (Front Door, Redis, more replicas) |

Both use PostgreSQL Flexible Server 16 on a private network, Key Vault, a container registry and
one user-assigned managed identity.

## 5. Delivery

### 5.1 Infrastructure and pipeline

- **Infrastructure as code.** All Azure resources are defined as Bicep templates under
  `infra/azure/`, with separate parameter files for staging and production:
  - network and private endpoints;
  - monitoring;
  - managed identity;
  - registry, Key Vault, storage, PostgreSQL and Redis;
  - the Container Apps environment, the three apps and the migration job;
  - Front Door.

  Images are referenced by digest, never by a moving tag. The Entra app registrations and the
  one-time database identity step are documented scripts.
- **Deploy pipeline.** A GitHub Actions workflow signs in to Azure with OIDC, so no stored cloud
  secrets are needed. It then:
  1. builds each image once and records its digest;
  2. produces a software bill of materials and scans the images;
  3. deploys staging, runs migrations and smoke-tests the deployment;
  4. waits for approval;
  5. releases to production as a canary that takes 10% of traffic, then 100%, and rolls back
     automatically on failure.
- **CI.** CI runs the existing tests, plus the storage tests against Azurite and a `config_check`
  on the Azure profile. It also checks that the built image reports missing Azure settings cleanly
  instead of crashing.

### 5.2 Order of work

Each step ends with passing tests and its own commit. Steps 1–4 leave the laptop setup exactly as
it is, and each can ship on its own. Steps 5–8 only take effect when Azure values are set.

| # | Step | Result |
|---|---|---|
| 1 | Switch, settings, startup check, `config_check` | `DEPLOY_TARGET` exists; unsafe Azure configs refuse to start |
| 2 | Storage interface with local and Blob versions, Azurite profile | Blob is testable on a laptop |
| 3 | Move the four disk users onto storage, share the Kite session | No state left on container disks |
| 4 | Advisory job locks and Redis rate limits | Safe with several replicas |
| 5 | Managed-identity database login | No database password on Azure |
| 6 | Entra sign-in and roles in the API | Real users and permissions |
| 7 | Frontend runtime config, Microsoft sign-in, nginx fixes | Web app works behind Entra |
| 8 | Application Insights | Traces, logs and metrics on Azure |
| 9 | Images and CI for both profiles | Both paths tested on every push |
| 10 | Bicep infrastructure, staging deploy, real Kite check | Staging running on Azure |
| 11 | Deploy pipeline | Build once, promote, canary, rollback |
| 12 | Docs, `.env.example`, `PLAN.md` | Documented and recorded |

## 6. Beyond the code: running it the enterprise way

- **Platform:**
  - an Azure landing zone with separate subscriptions or resource groups for staging and
    production;
  - Front Door with a web application firewall in front;
  - private endpoints for the database, storage and Key Vault;
  - outbound traffic through Azure Firewall where company policy requires it.
- **Release:** build once and promote the same image digest from staging to production. Ship
  production as a canary with automatic rollback.
- **Operations:**
  - service-level objectives for availability and data freshness, with alerts;
  - scheduled database restore and disaster-recovery drills;
  - a key-rotation runbook.
- **Compliance:** review India's data-protection law (DPDP Act) and data residency (Central India
  region), market-data licensing for a hosted service, and the investment-advice disclaimers
  before any public launch.

## 7. Risks and open questions

| Risk | Impact | What we do |
|---|---|---|
| Kite may reject one session used by two processes | Worker can't use the broker data | Test on staging (step 10). Fall back to one combined API and worker app. |
| NSE sites may block or throttle cloud IP addresses | Live NSE data degrades on Azure | Test early from staging. A fixed outbound IP (NAT gateway) helps if the block is IP-based. |
| Entra and role setup needs tenant admin rights | Sign-in can't be turned on | Request the app registrations early. Token mode still works meanwhile. |
| `HF_TOKEN` is named in `.env.example` and `03-container-cloud-deployment.md`, but the code reads `LLM_*` settings | Confusing setup | Fix in step 12. |

## 8. Commands, once implemented

| Purpose | Command |
|---|---|
| Run locally (unchanged) | `./scripts/dev.sh` |
| Full local stack in containers | `docker compose --profile demo up --build` |
| Rehearse the Azure profile locally with Azurite | `docker compose --profile demo --profile azure-local up --build` |
| Show which local or Azure version each concern uses | `cd backend && uv run python -m app.devtools.config_check` |
| Deploy to staging, then production | Run the `deploy-azure` workflow from GitHub Actions |
