# Azure Setup Guide (beginner, Azure portal) — deploying Markets Intelligence into your company resource group

This guide shows you how to create every Azure service this repository needs **by clicking in
the Azure portal** (https://portal.azure.com) and how to get the app running. It assumes:

- Your company has **already created a resource group (RG) for you**. Everything goes inside it.
  You do **not** create a resource group.
- You have never used Azure before. Each term is explained the first time it appears.

It is the beginner, portal-only companion to [`04-azure-migration.md`](04-azure-migration.md),
which is the full production plan (private networking, CI/CD, DR, cost) written with `az` CLI
commands. Here we take the **simplest path that is still safe**: no virtual network, a PostgreSQL
server reachable only from Azure services and your own IP address, secrets in Key Vault, HTTPS
on by default.

> **Status:** written from the code in this repository (October 2026); not yet run against your
> company's subscription. The Azure portal changes its screens from time to time — if a button
> has moved, look for the same words nearby. Company policies can block some steps; §2 shows how
> to check before you start, and every step that needs extra permissions has a "Plan B".

> **The only two places you type anything other than form values** are unavoidable in a pure UI:
> building the two container images (§11) and creating the database user (§9.4). Both are done
> **inside the portal** in the browser-based **Cloud Shell**, by pasting a few lines that are
> given to you in full. No software is installed on your computer.

---

## Contents

1. [What you are going to build](#1-what-you-are-going-to-build)
2. [Before you start: check what your company lets you do](#2-before-you-start-check-what-your-company-lets-you-do)
3. [Portal basics you will use in every step](#3-portal-basics-you-will-use-in-every-step)
4. [Choose your names (write them down)](#4-choose-your-names-write-them-down)
5. [Service 1 — Log Analytics workspace (logs)](#5-service-1--log-analytics-workspace-logs)
6. [Service 2 — Container Registry (stores your images)](#6-service-2--container-registry-stores-your-images)
7. [Service 3 — Managed Identity (the app's own login)](#7-service-3--managed-identity-the-apps-own-login)
8. [Service 4 — Key Vault (secrets)](#8-service-4--key-vault-secrets)
9. [Service 5 — PostgreSQL Flexible Server (database)](#9-service-5--postgresql-flexible-server-database)
10. [Service 6 — Storage account + file share (persistent files)](#10-service-6--storage-account--file-share-persistent-files)
11. [Build the two container images](#11-build-the-two-container-images)
12. [Service 7 — Container Apps environment](#12-service-7--container-apps-environment)
13. [Service 8 — Migration job (creates the database tables)](#13-service-8--migration-job-creates-the-database-tables)
14. [Service 9 — The Container App (the website + API)](#14-service-9--the-container-app-the-website--api)
15. [Service 10 — Azure OpenAI model in Azure AI Foundry (LLM)](#15-service-10--azure-openai-model-in-azure-ai-foundry-llm)
16. [First start and checks](#16-first-start-and-checks)
17. [Optional services](#17-optional-services)
18. [Deploying a new version later](#18-deploying-a-new-version-later)
19. [Troubleshooting](#19-troubleshooting)
20. [Cost, pausing and deleting](#20-cost-pausing-and-deleting)
21. [Final checklist](#21-final-checklist)

---

## 1. What you are going to build

The app is two containers (small packaged programs) plus a database:

| Piece in this repo | What it is | Where it runs on Azure |
|---|---|---|
| `infra/docker/frontend.Dockerfile` → **web** image | nginx serving the React website on port 8080 and forwarding `/api` and `/health` to the API | Container App, container `web` |
| `infra/docker/backend.Dockerfile` → **api** image | Python FastAPI server on port 8000, with the scheduler (monthly themes, 12-hour sector rotation, daily prices) inside it | Container App, container `api` (same app; talks to `web` over `127.0.0.1`) |
| Same **api** image, different command | `alembic upgrade head` — creates/updates the database tables | Container Apps **Job** (run once per release) |
| PostgreSQL 16 | The system-of-record database | Azure Database for PostgreSQL Flexible Server |
| `/app/var` folder | Kite login session, uploaded statements, NSE filings cache | Azure Files share mounted into the `api` container |

```
   Browser ──HTTPS──▶ Container App "markets"
                      ├─ web (nginx :8080)  ──127.0.0.1:8000──▶ api (FastAPI + scheduler)
                      │                                           │        │
                      └───────────────────────────────────────────┼────────┼──────────
                                                                  ▼        ▼
                                          PostgreSQL Flexible Server   Azure Files (/app/var)
                                                  │ HTTPS (research summaries only)
                                                  ▼
                                   Azure OpenAI model deployment (Azure AI Foundry)
   Supporting: Container Registry (images) · Key Vault (secrets) · Managed Identity (access)
               · Log Analytics (logs) · Container Apps Job (migrations)
```

### The full shopping list (create them in this order)

| # | Azure service | Why this app needs it | Required? |
|---|---|---|---|
| 1 | Log Analytics workspace | Collects the containers' logs so you can read them | Yes |
| 2 | Container Registry | Private store for the two images | Yes |
| 3 | User-assigned Managed Identity | Lets the app pull images and read secrets without passwords | Yes (Plan B exists) |
| 4 | Key Vault | Stores the database URL, API token, LLM key | Yes (Plan B exists) |
| 5 | Azure Database for PostgreSQL – Flexible Server | The database | Yes |
| 6 | Storage account + file share | Keeps `/app/var` across restarts (Kite login, uploads) | Yes |
| 7 | Container Apps environment | The "hosting area" that container apps live in | Yes |
| 8 | Container Apps Job | Runs database migrations | Yes |
| 9 | Container App | Runs the website + API | Yes |
| 10 | Azure AI Foundry resource + Azure OpenAI model deployment | The LLM that writes the research-board summaries from evidence the app supplies | Yes, for AI summaries (the rest of the app works without it) |
| — | Application Insights, custom domain, private network, Redis | Monitoring / hardening / scale-out | Optional (§17) |

### How many of each you create (exact counts)

| What | How many | Names | Notes |
|---|---|---|---|
| Container registry | **1** | `acrmarkets<suffix>` | Holds **2 images** (repositories): `markets-api`, `markets-web` |
| Container Apps environment | **1** | `cae-markets-<suffix>` | Hosts the app and the job |
| Container App | **1** | `markets` | Runs **2 containers** side by side: `web` (nginx) and `api` (FastAPI + scheduler), **1 replica** |
| Container Apps Job | **1** | `markets-migrate` | **1 container** from the `markets-api` image; runs `alembic upgrade head` and stops |
| PostgreSQL Flexible Server | **1** | `psql-markets-<suffix>` | Contains **1 database**: `markets`, with **2 logins**: `pgadmin` (admin) and `markets` (app) |
| Storage account | **1** | `stmarkets<suffix>` | Contains **1 file share**: `var` (mounted at `/app/var`) |
| Key Vault | **1** | `kv-markets-<suffix>` | Holds **4 secrets**: `api-auth-token`, `pg-admin-password`, `llm-api-key`, `database-url` |
| Managed identity | **1** | `id-markets-<suffix>` | Gets **2 role assignments** (AcrPull, Key Vault Secrets User); you get 1 (Key Vault Secrets Officer) |
| Log Analytics workspace | **1** | `log-markets-<suffix>` | |
| Azure AI Foundry resource | **1** | `aif-markets-<suffix>` | Contains **1 project** (`markets`) and **1 model deployment** (`gpt-4.1-mini`) |
| **Total Azure resources** | **10** | | + Application Insights (1) if you add monitoring (§17.1) |

**Running containers in total: 2 always on** (`web` + `api` in the one Container App), plus
**1 short-lived** migration container each time you run the job.

**Not created on Azure** (they exist in `docker-compose.yml` but are not needed here):

| Compose service | Why not on Azure |
|---|---|
| `postgres` | Replaced by the managed PostgreSQL Flexible Server |
| `redis` | Not needed with one replica (in-process cache/locks) |
| `worker` | The scheduler runs inside `api` (`SCHEDULER_ENABLED=true`), which also holds the Kite login |
| `migrate` | Replaced by the Container Apps Job |
| `mock-mcp` | Demo mode only; live mode uses the real NSE and Kite MCP servers |

**Why exactly one copy of the app?** The scheduler, the in-memory cache and locks, and the Kite
login all live inside one API process. Two copies would run every scheduled job twice. So the app
is set to **1 replica, always on** (minimum 1, maximum 1). Redis is therefore not needed.

---

## 2. Before you start: check what your company lets you do

### 2.1 Facts to collect from your Azure admin

| Question | Why | Example answer |
|---|---|---|
| Subscription name | You pick it in every form | `Contoso-Dev` |
| Resource group name | Everything goes here | `rg-hackathon-rajesh` |
| Resource group region | Use the same region for everything | `Central India` |
| Your role on the RG | Decides which steps you can do yourself | `Contributor` or `Owner` |
| Allowed regions / sizes? | Policies may block regions or sizes | "Only Central India / South India" |
| Required tags? | Policies may reject resources without tags | `owner`, `costCenter` |
| Is public network access allowed? | Some policies forbid public databases/storage | "Denied" → see §17.3 first |

### 2.2 Check your role in the portal

1. In the top search bar type **Resource groups** → open it → click your resource group.
2. Left menu → **Access control (IAM)** → **View my access** (button near the top).
3. Read the role:

| Your role | What you can do | What you cannot |
|---|---|---|
| **Owner** | Everything in this guide | — |
| **Contributor** | Create all resources | **Add role assignments** (§7.2, §8.2) → ask an admin to do those 3 clicks, or use Plan B |
| **Reader** | Nothing useful | Ask for Contributor |

Also note the **Location** on the resource group's **Overview** page — that is your region.

### 2.3 Check the resource providers

Azure must "enable" each service type on the subscription once.

1. Search bar → **Subscriptions** → click your subscription.
2. Left menu → **Settings** → **Resource providers**.
3. Use the filter box to find each of these and check the **Status** column says **Registered**:
   `Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.DBforPostgreSQL`,
   `Microsoft.KeyVault`, `Microsoft.Storage`, `Microsoft.OperationalInsights`,
   `Microsoft.ManagedIdentity`.
4. If one says **NotRegistered**, select it and click **Register** at the top. If that is greyed
   out or fails, ask your admin to register it.

---

## 3. Portal basics you will use in every step

- **Creating something:** search bar at the top → type the service name (e.g. *Key vaults*) →
  open it → **+ Create** (top left).
- **Every create form starts the same way ("Basics" tab):**
  - **Subscription:** your company subscription.
  - **Resource group:** pick **your existing one** from the dropdown. Never click "Create new".
  - **Region / Location:** the same region as your resource group.
- **Tags tab:** if your company requires tags, fill them in on the **Tags** tab of every form.
- **Finishing:** **Review + create** → wait for "Validation passed" → **Create** → wait for
  "Your deployment is complete" → **Go to resource**.
- **Validation failed?** Click the error — a message mentioning *policy* means a company rule
  blocked it (wrong region, size, missing tag, public access). Fix that field and try again.
- **Finding things later:** open your resource group — everything you made is listed there.
- **Notifications:** the bell icon (top right) shows the progress of each deployment.

---

## 4. Choose your names (write them down)

Some names must be **unique across all of Azure** (registry, key vault, storage, database server).
Pick a short suffix that is yours, e.g. your initials + `01` → `rs01`. Fill in this table and keep
it open — later steps refer to these names.

| Service | Name pattern | Your name | Rules |
|---|---|---|---|
| Log Analytics workspace | `log-markets-<suffix>` | | |
| Container registry | `acrmarkets<suffix>` | | 5–50 lowercase letters/digits, **no dashes**, global |
| Managed identity | `id-markets-<suffix>` | | |
| Key vault | `kv-markets-<suffix>` | | 3–24 chars, letters/digits/dashes, global |
| PostgreSQL server | `psql-markets-<suffix>` | | lowercase, dashes ok, global |
| Storage account | `stmarkets<suffix>` | | 3–24 lowercase letters/digits, **no dashes**, global |
| Container Apps environment | `cae-markets-<suffix>` | | |
| Migration job | `markets-migrate` | | |
| Container App | `markets` | | |
| Azure AI Foundry resource | `aif-markets-<suffix>` | | 2–64 chars, letters/digits/dashes, global (it becomes part of the endpoint URL) |
| Model deployment | `gpt-4.1-mini` | | the name the app sends as `LLM_MODEL_ID`; simplest to keep it equal to the model name |
| Image version | `v1` | | increase to `v2`, `v3`… for each release |

The portal shows a green tick next to a name field when the name is free, and a red message if
it is taken — just change the suffix.

**Passwords and tokens.** You will need three random values below (database admin password,
database app password, API token). Generate them with your company password manager, **using
only letters and digits** (no symbols — symbols would break the database connection string), at
least 32 characters long. Keep them in the password manager; you will paste them into Key Vault.

---

## 5. Service 1 — Log Analytics workspace (logs)

**What it is:** a searchable store for logs. Every line the containers print ends up here.

1. Search **Log Analytics workspaces** → **+ Create**.
2. **Basics:** Subscription, your resource group, **Name** `log-markets-<suffix>`, **Region** = yours.
3. **Review + Create** → **Create** → wait → **Go to resource**.
4. (Optional) Left menu → **Usage and estimated costs** → **Data Retention** → 30 days → **OK**.

---

## 6. Service 2 — Container Registry (stores your images)

**What it is:** a private Docker Hub. You build the two images into it; the Container App
downloads ("pulls") them from it.

1. Search **Container registries** → **+ Create**.
2. **Basics:**
   - Resource group: yours · **Registry name** `acrmarkets<suffix>` · **Location** = yours
   - **Pricing plan:** **Basic**
3. Leave the other tabs at their defaults.
4. **Review + create** → **Create** → **Go to resource**.
5. Left menu → **Settings** → **Access keys**: make sure **Admin user** is **unticked** (Plan B
   in §14.6 turns it on only if you cannot use the managed identity).

Write down the **Login server** shown on the Overview page, e.g. `acrmarketsrs01.azurecr.io`.

---

## 7. Service 3 — Managed Identity (the app's own login)

**What it is:** an Azure identity that belongs to the app, not to a person. You give it two
permissions — "may pull images from the registry" and "may read secrets from Key Vault" — so no
passwords are stored anywhere.

### 7.1 Create it

1. Search **Managed Identities** → **+ Create**.
2. **Basics:** Resource group: yours · **Region** = yours · **Name** `id-markets-<suffix>`.
3. **Review + create** → **Create**.

### 7.2 Allow it to pull images (needs Owner or User Access Administrator)

1. Open your **container registry** → left menu **Access control (IAM)**.
2. **+ Add** → **Add role assignment**.
3. **Role** tab: search **AcrPull** → select it → **Next**.
4. **Members** tab: **Assign access to** → **Managed identity** → **+ Select members** →
   Managed identity dropdown: **User-assigned managed identity** → click `id-markets-<suffix>`
   → **Select**.
5. **Review + assign** → **Review + assign**.

> **"You do not have permissions to add role assignments" / the button is greyed out?** You are a
> Contributor. Ask your admin to do steps 1–5 (and §8.2), or use **Plan B** (§14.6).

---

## 8. Service 4 — Key Vault (secrets)

**What it is:** a locked safe for secrets. The Container App reads them at start-up using the
managed identity. Secrets never appear in code, Git or in the app's settings page.

### 8.1 Create it

1. Search **Key vaults** → **+ Create**.
2. **Basics:** Resource group: yours · **Key vault name** `kv-markets-<suffix>` · **Region** = yours
   · **Pricing tier** **Standard** · leave soft-delete at its default.
3. **Access configuration** tab: **Permission model** → **Azure role-based access control
   (recommended)**.
4. **Networking** tab: leave **Enable public access** on, **All networks** (simplest; tighten later).
5. **Review + create** → **Create** → **Go to resource**.

### 8.2 Give yourself and the app access (needs Owner or User Access Administrator)

You need **two** role assignments on the key vault. Open the key vault → **Access control
(IAM)** → **+ Add** → **Add role assignment**, and do it twice:

| # | Role | Assign access to | Member |
|---|---|---|---|
| 1 | **Key Vault Secrets Officer** (lets you create secrets) | User, group, or service principal | yourself |
| 2 | **Key Vault Secrets User** (lets the app read secrets) | Managed identity → User-assigned | `id-markets-<suffix>` |

Role assignments take 1–5 minutes to apply. If the next step shows *"The operation is not
allowed by RBAC"* or *"You are unauthorized to view these contents"*, wait a few minutes and
refresh.

> **Plan B (cannot assign roles):** skip creating secrets here; you will paste the values
> directly into the Container App and Job as their own secrets (§14.6).

### 8.3 Create the secrets

Key vault → left menu **Objects** → **Secrets** → **+ Generate/Import**. For each row below:
**Upload options** *Manual* · **Name** as shown · **Secret value** as shown · leave the rest →
**Create**. Secret names may contain only letters, digits and dashes.

| Name | Secret value | When |
|---|---|---|
| `api-auth-token` | a random 64-character letters+digits value from your password manager | Now. The browser asks for this token before showing data; without it anyone with the URL can use the app. |
| `pg-admin-password` | the database **admin** password you generated | Now (used in §9) |
| `llm-api-key` | the word `unset` for now; replaced with the Azure OpenAI key in §15.5 | Now (the app definition expects it to exist) |
| `database-url` | the connection string | **Later, in §9.5** |

Do **not** upload your local `.env` file — it contains local values (`DATA_MODE=demo`, `localhost`).

To read a secret later: Secrets → click the name → click the current version → **Show Secret Value**.

---

## 9. Service 5 — PostgreSQL Flexible Server (database)

**What it is:** a managed PostgreSQL 16 database. Azure handles patching and daily backups.

### 9.1 Create the server

1. Search **Azure Database for PostgreSQL flexible servers** → **+ Create** (if asked, choose
   **Flexible server**).
2. **Basics** tab:
   - Resource group: yours · **Server name** `psql-markets-<suffix>` · **Region** = yours
   - **PostgreSQL version:** **16**
   - **Workload type:** **Development**
   - **Compute + storage** → **Configure server**:
     - **Compute tier:** **Burstable** · **Compute size:** **Standard_B1ms** (cheapest) or
       **Standard_B2s** (smoother first data load)
     - **Storage size:** 32 GiB · **Storage autogrowth:** on
     - **High availability:** off
     - **Backup retention period:** 7 days (14 for anything important)
     - **Save**
   - **Authentication method:** **PostgreSQL authentication only**
   - **Admin username:** `pgadmin`
   - **Password / Confirm password:** the value you stored as `pg-admin-password`
3. **Networking** tab:
   - **Connectivity method:** **Public access (allowed IP addresses)**
   - Tick **Allow public access from any Azure service within Azure to this server** (the
     Container App and the Cloud Shell need this)
   - Click **+ Add current client IP address** (lets your browser's Cloud Shell/tools connect)
4. **Security** tab: leave the defaults (data encrypted by Azure).
5. **Review + create** → **Create**. This takes 5–10 minutes.

> "Allow Azure services" means services running in Azure can reach the server's *login prompt*;
> they still need the password, and encrypted connections (TLS) are enforced. If your company
> forbids public database access, stop and read §17.3 first.

### 9.2 Check that encrypted connections are enforced

Server → left menu **Settings** → **Server parameters** → search `require_secure_transport` →
it must be **ON** (the default).

### 9.3 Create the database

Server → left menu **Settings** → **Databases** → **+ Add** → **Name** `markets` → **Save**.

### 9.4 Create a dedicated app user (not the admin)

The app should log in with its own user that owns only the `markets` database. The portal has no
form for this, so you use the built-in **Connect** button, which opens a SQL prompt in the browser.

1. Server → left menu **Settings** → **Connect** (or **Overview** → **Connect**).
2. Choose database **markets**, then **Open Cloud Shell** / **Connect from browser**. The first
   time, Cloud Shell asks to create a small storage account for itself — accept (if your
   company blocks that, ask the admin which storage account Cloud Shell should use).
3. When asked, paste the **admin** password (`pg-admin-password`). You will see `markets=>`.
4. Paste the following, replacing `PASTE_APP_PASSWORD_HERE` with the **app** password you
   generated (letters and digits only), then press Enter:

   ```sql
   CREATE ROLE markets LOGIN PASSWORD 'PASTE_APP_PASSWORD_HERE';
   GRANT CONNECT ON DATABASE markets TO markets;
   ALTER DATABASE markets OWNER TO markets;
   GRANT USAGE, CREATE ON SCHEMA public TO markets;
   ```

   You should see `CREATE ROLE`, `GRANT`, `ALTER DATABASE`, `GRANT`. Type `\q` to leave.

If **Connect** times out, go to **Settings → Networking** and click **+ Add current client IP
address** again (Cloud Shell may use a different IP than your browser), then **Save**.

### 9.5 Store the connection string in Key Vault

Write this string, replacing the two placeholders:

```
postgresql+psycopg://markets:<APP_PASSWORD>@<SERVER_NAME>.postgres.database.azure.com:5432/markets?sslmode=require
```

- `<APP_PASSWORD>` — the app password from §9.4
- `<SERVER_NAME>` — e.g. `psql-markets-rs01` (the full host name is on the server's **Overview**
  page as **Server name**)

Then Key vault → **Secrets** → **+ Generate/Import** → **Name** `database-url` → **Secret value**
= the string → **Create**.

---

## 10. Service 6 — Storage account + file share (persistent files)

**Why:** a container's own disk is wiped on every restart. The API writes three things it must
keep to `/app/var`: the Kite login session, uploaded financial statements, and the NSE
filings/transcripts cache. An Azure **file share** mounted at `/app/var` keeps them.

### 10.1 Storage account

1. Search **Storage accounts** → **+ Create**.
2. **Basics:** Resource group: yours · **Storage account name** `stmarkets<suffix>` · **Region**
   = yours · **Primary service:** *Azure Files* (if asked) · **Performance** **Standard** ·
   **Redundancy** **Locally-redundant storage (LRS)**.
3. **Advanced** tab: **Require secure transfer** ticked · **Minimum TLS version** **1.2** ·
   **Allow enabling anonymous access on individual containers** **unticked**.
4. **Networking** tab: **Enable public access from all networks** (simplest; tighten later).
5. **Review + create** → **Create** → **Go to resource**.

### 10.2 File share

1. Storage account → left menu **Data storage** → **File shares** → **+ File share**.
2. **Name** `var` · **Access tier** *Transaction optimized* · (if shown) **Provisioned
   capacity/quota** 5 GiB.
3. **Review + create** → **Create**.

### 10.3 Copy the access key (needed in §12)

Storage account → **Security + networking** → **Access keys** → **key1** → **Show** → copy the
**Key**. Paste it straight into §12.2 — don't save it anywhere else.

---

## 11. Build the two container images

The registry needs the two images built from this repository. Azure builds them for you in the
cloud on the right CPU type (`linux/amd64`), which also avoids the Apple-Silicon problem where
images built on a Mac don't run on Azure. The portal has no "build from my code" button for a
multi-image repo like this one, so you paste two lines into **Cloud Shell** (it runs in the browser).

### 11.1 Open Cloud Shell and get the code

1. Click the **Cloud Shell** icon `>_` in the portal's top bar → choose **Bash**.
2. Get the repository into Cloud Shell — one of:
   - **From Git:** `git clone <your repository URL>` then `cd <folder name>`.
     (For a private GitHub repo, use a personal access token as the password when asked.)
   - **From a zip:** on your Mac, zip the repository folder (without `node_modules`, `.venv`
     and `.env`). In Cloud Shell click **Manage files** → **Upload** → pick the zip. Then
     `unzip Hackathon.zip && cd Hackathon`.
3. Make sure the subscription is right: `az account show --query name -o tsv`. If not:
   `az account set --subscription "<subscription name>"`.

### 11.2 Build

Replace `acrmarketsrs01` with **your registry name** and `v1` with your image version:

```bash
az acr build -r acrmarketsrs01 -t markets-api:v1 -f infra/docker/backend.Dockerfile  .
az acr build -r acrmarketsrs01 -t markets-web:v1 -f infra/docker/frontend.Dockerfile .
```

Each takes a few minutes and ends with `Run ID: … was successful`. `.dockerignore` already keeps
`.env`, `backend/var`, `node_modules` and `.venv` out of the upload.

### 11.3 Check in the portal

Container registry → left menu **Services** → **Repositories** → you should see `markets-api`
and `markets-web`, each with tag `v1`. Build history is under **Services** → **Tasks** → **Runs**.

---

## 12. Service 7 — Container Apps environment

**What it is:** the shared hosting area (network, logging) that your Container App and Job live in.

### 12.1 Create it

1. Search **Container Apps Environments** → **+ Create**.
2. **Basics:** Resource group: yours · **Environment name** `cae-markets-<suffix>` · **Region**
   = yours · **Environment type** **Workload profiles** · **Zone redundancy** off.
3. **Monitoring** tab: **Logs destination** **Azure Log Analytics** → **Log Analytics
   workspace** `log-markets-<suffix>`.
4. **Networking** tab: **Public network access** **Enable** · **Use your own virtual network**
   **No** (unless you are following §17.3).
5. **Review + create** → **Create** (a few minutes).

### 12.2 Connect the file share to the environment

1. Open the environment → left menu **Settings** → **Azure Files** → **+ Add**.
2. Fill in:
   - **Name:** `markets-var` (the app refers to the share by this name)
   - **Storage account name:** `stmarkets<suffix>`
   - **Storage account key:** paste the key from §10.3
   - **File share:** `var`
   - **Access mode:** **Read/Write**
3. **Add** (and **Save** if shown).

---

## 13. Service 8 — Migration job (creates the database tables)

**What it is:** a container that runs one command and stops: `alembic upgrade head`, which
creates or updates every table. Run it on first deploy and after every new release, **before**
updating the app.

### 13.1 Create the job

1. Search **Container App Jobs** → **+ Create**.
2. **Basics:**
   - Resource group: yours · **Container job name** `markets-migrate`
   - **Region** = yours · **Container Apps Environment** `cae-markets-<suffix>`
   - **Trigger type:** **Manual**
   - **Replica timeout (seconds):** `900` · **Replica retry limit:** `0` · **Parallelism:** `1`
     · **Replica completion count:** `1`
3. **Container** tab:
   - **Name:** `markets-migrate`
   - **Image source:** **Azure Container Registry** → **Registry** your registry · **Image**
     `markets-api` · **Image tag** `v1`
   - **Authentication type** (or **Managed identity**): choose **Managed identity** →
     **User-assigned** → `id-markets-<suffix>`. (Plan B: see §14.6.)
   - **Command override:** `alembic`
   - **Arguments override:** `upgrade, head`
   - **CPU and Memory:** 0.5 CPU cores, 1 Gi memory
   - **Environment variables:** add these four now (the database one comes in step 13.2):

     | Name | Source | Value |
     |---|---|---|
     | `APP_ENV` | Manual entry | `production` |
     | `DATA_MODE` | Manual entry | `live` |
     | `AUTO_CREATE_SCHEMA` | Manual entry | `false` |
     | `SCHEDULER_ENABLED` | Manual entry | `false` |
4. **Review + create** → **Create** → **Go to resource**.

### 13.2 Give the job its identity and the database secret

1. Job → left menu **Settings** → **Identity** → **User assigned** tab → **+ Add** →
   `id-markets-<suffix>` → **Add**. (Skip if already listed.)
2. Job → **Settings** → **Secrets** → **+ Add**:
   - **Key:** `database-url`
   - **Type:** **Key Vault reference**
   - **Key Vault secret URL:** `https://kv-markets-<suffix>.vault.azure.net/secrets/database-url`
     (Key vault → Secrets → `database-url` → **Secret Identifier** shows this; drop the version
     part at the end so the app always reads the latest value)
   - **Managed identity:** `id-markets-<suffix>`
   - **Add**.
3. Job → **Application** → **Containers** (or **Containers**) → **Edit and deploy** → click the
   container `markets-migrate` → **Environment variables** → **+ Add**:
   **Name** `DATABASE_URL` · **Source** **Reference a secret** · **Value** `database-url` →
   **Save** → **Create/Save**.

### 13.3 Run it

1. Job → **Overview** → **Run now**.
2. Left menu **Execution history** → refresh until the status is **Succeeded** (1–2 minutes).
3. If it says **Failed**, click the execution → **Console logs** (or **Logs**) to see why — see §19.

---

## 14. Service 9 — The Container App (the website + API)

This is the main piece. It ends up with two containers (`web` and `api`) sharing `127.0.0.1`,
one always-on replica, public HTTPS on the `web` container, the file share at `/app/var` in `api`,
and secrets from Key Vault. The portal's create wizard only takes one container, so you create
the app with `web` first (§14.1), then add secrets (§14.2), then add `api` and the volume (§14.3).

### 14.1 Create the app with the `web` container

1. Search **Container Apps** → **+ Create** → **Container App**.
2. **Basics:**
   - Resource group: yours · **Container app name** `markets`
   - **Deployment source:** **Container image**
   - **Region** = yours · **Container Apps Environment** `cae-markets-<suffix>`
3. **Container** tab:
   - Untick **Use quickstart image**
   - **Name:** `web`
   - **Image source:** **Azure Container Registry** → your registry · **Image** `markets-web`
     · **Image tag** `v1`
   - **Authentication type / Managed identity:** **Managed identity** → **User-assigned** →
     `id-markets-<suffix>`. (Plan B: §14.6.)
   - **Workload profile:** **Consumption**
   - **CPU and Memory:** **0.25 CPU cores, 0.5 Gi memory**
   - **Environment variables:** **+ Add** → **Name** `API_UPSTREAM` · **Source** Manual entry ·
     **Value** `127.0.0.1:8000` (nginx forwards API calls to the `api` container next to it)
4. **Ingress** tab:
   - **Ingress:** **Enabled**
   - **Ingress traffic:** **Accepting traffic from anywhere**
   - **Ingress type:** **HTTP**
   - **Client certificate mode:** Ignore
   - **Transport:** Auto
   - **Insecure connections:** **unticked** (HTTP is redirected to HTTPS)
   - **Target port:** `8080`
5. **Review + create** → **Create** → **Go to resource**.

The app now runs only the website; API calls fail until `api` is added. That is expected.

On **Overview**, note the **Application Url** (`https://markets.<random>.<region>.azurecontainerapps.io`).

### 14.2 Add the identity and the secrets

1. App → **Settings** → **Identity** → **User assigned** → **+ Add** → `id-markets-<suffix>` →
   **Add** (skip if already listed).
2. App → **Settings** → **Secrets** → **+ Add**, three times, each as **Key Vault reference**
   with managed identity `id-markets-<suffix>`:

   | Key | Key Vault secret URL |
   |---|---|
   | `database-url` | `https://kv-markets-<suffix>.vault.azure.net/secrets/database-url` |
   | `api-auth-token` | `https://kv-markets-<suffix>.vault.azure.net/secrets/api-auth-token` |
   | `llm-api-key` | `https://kv-markets-<suffix>.vault.azure.net/secrets/llm-api-key` |

### 14.3 Add the volume, the `api` container, and the scale rule

App → left menu **Application** → **Revisions and replicas** (or **Containers**) → **Create new
revision** / **Edit and deploy**. You will now see the revision editor with tabs **Container
image**, **Scale**, **Volumes**.

**a) Volumes tab** → **+ Add**:
- **Volume type:** **Azure file volume**
- **Name:** `var`
- **File share name:** `markets-var` (the name from §12.2)
- **Mount options:** `uid=10001,gid=10001,dir_mode=0700,file_mode=0600,nobrl`
  (the api container runs as user 10001; without this it cannot write the share)
- **Add**

**b) Container image tab** → **+ Add** → **App container**:

- **Basics** (inside the side panel):
  - **Name:** `api`
  - **Image source:** Azure Container Registry → your registry · **Image** `markets-api` ·
    **Image tag** `v1`
  - **CPU and Memory:** **1 CPU core, 2 Gi memory** (together with web = 1.25 / 2.5 Gi, a valid
    combination)
  - Leave **Command override** and **Arguments override** empty (the image starts the API itself)
  - **Environment variables:** add every row of the table below with **+ Add**. Rows marked
    *secret* use **Source: Reference a secret**; all others use **Manual entry**.

    | Name | Source | Value |
    |---|---|---|
    | `APP_ENV` | Manual | `production` |
    | `DATA_MODE` | Manual | `live` |
    | `LOG_LEVEL` | Manual | `INFO` |
    | `DEFAULT_MARKET_TIMEZONE` | Manual | `Asia/Kolkata` |
    | `SCHEDULER_ENABLED` | Manual | `true` |
    | `AUTO_CREATE_SCHEMA` | Manual | `false` |
    | `AUTO_INITIAL_REFRESH` | Manual | `true` |
    | `DATABASE_URL` | **secret** | `database-url` |
    | `API_AUTH_TOKEN` | **secret** | `api-auth-token` |
    | `NSE_BHAVCOPY_MCP_URL` | Manual | `https://mcp.nseindia.in/bhavcopy/cm/mcp` |
    | `NSE_CM_MARKET_MCP_URL` | Manual | `https://mcp.nseindia.in/cmmkt/mcp` |
    | `BROKER_MCP_URL` | Manual | `https://mcp.kite.trade/mcp` |
    | `KITE_PERSIST_SESSION` | Manual | `true` |
    | `NSE_FILINGS_BASE_URL` | Manual | `https://www.nseindia.com/api` |
    | `NSE_FILINGS_DOCUMENT_HOSTS` | Manual | `nsearchives.nseindia.com` |
    | `UNIVERSE_SCOPE` | Manual | `reference` for a fast first run, `all` for every NSE equity |
    | `UNIVERSE_SERIES` | Manual | `EQ,BE` |
    | `BENCHMARK_SYMBOL` | Manual | `NIFTY 50` |
    | `HISTORY_YEARS` | Manual | `3` |
    | `LLM_ENABLED` | Manual | `false` for now — §15.6 switches it on with the Azure OpenAI values |
    | `LLM_API_KEY` | **secret** | `llm-api-key` |
    | `WEB_SEARCH_PROVIDER` | Manual | `ddgs` |
    | `THEME_REFRESH_CRON` | Manual | `30 6 1 * *` |
    | `RRG_REFRESH_CRON` | Manual | `0 */12 * * *` |
    | `PRICE_REFRESH_CRON` | Manual | `30 19 * * 1-5` |
    | `INSTRUMENT_REFRESH_CRON` | Manual | `0 7 * * 1-5` |
    | `ALERTS_CRON` | Manual | `*/15 * * * *` |

    Do **not** set `REDIS_URL`, `CORS_ORIGINS` or `POSTGRES_PASSWORD` — leaving them out is
    correct here. Every other setting uses the code default from `backend/app/core/config.py`,
    which matches `.env.example`; the full list with cloud values is in
    [`04-azure-migration.md` §5](04-azure-migration.md#5-environment-variables-complete-cloud-mapping).

- **Health probes** (tab in the side panel) — enable all three, **HTTP**, **port 8000**:

  | Probe | Path | Period (s) | Failure threshold |
  |---|---|---|---|
  | Liveness | `/health/live` | 30 | 3 |
  | Readiness | `/health/ready` | 15 | 3 |
  | Startup | `/health/live` | 10 | 30 |

- **Volume mounts** (tab in the side panel) → **+ Add** → **Volume name** `var` → **Mount path**
  `/app/var`.
- **Add**.

**c) Scale tab:** **Min replicas** `1` · **Max replicas** `1`. Remove any default scale rule.
(At 0 the scheduled jobs never fire and the Kite session drops; above 1 every job runs twice.)

**d)** Click **Create** at the bottom. A new revision starts with both containers.

### 14.4 Single revision mode

App → **Application** → **Revisions and replicas** → **Choose revision mode** → **Single:
One active revision at a time** → **Apply**. This makes each new version replace the old one.

### 14.5 Check it is running

App → **Revisions and replicas** → the newest revision should show **Running** and
**Healthy**/**Provisioned**. Then continue with §15 (the LLM). You can also skip to §16 and add the LLM later — the app runs without it.

### 14.6 Plan B — if you could not add role assignments (§7.2 / §8.2)

Use the registry's admin password and plain Container App secrets instead of the managed identity:

1. Container registry → **Access keys** → tick **Admin user** → copy **Username** and **password**.
2. In the job (§13.1) and app (§14.1) **Container** tabs, choose **Authentication type:
   Admin credentials** (the portal fills them in from the registry) instead of Managed identity.
3. In **Secrets** (§13.2, §14.2) choose **Type: Container Apps secret** and paste the **value**
   itself (the connection string, the token, the LLM key or `unset`) instead of a Key Vault URL.
4. Skip all **Identity** steps.

Anyone with Contributor on the resource group can read these secrets, so move to the
managed-identity setup once an admin can grant the roles, then untick **Admin user**.

---

## 15. Service 10 — Azure OpenAI model in Azure AI Foundry (LLM)

**What it is:** **Azure AI Foundry** is Azure's home for AI models. You create one *Foundry
resource* in your resource group, *deploy* an OpenAI model in it (a deployment is your own named,
rate-limited copy of the model), and the app calls that deployment over HTTPS with a key.

**What the app uses it for:** only to write the research-board summaries, pros/cons wording and
explanations **from evidence the app has already gathered**. Prices, ratios, scores and rankings
are always calculated by the app's own code, never by the model. With the LLM off, the app shows
a deterministic summary instead.

**How the app talks to it (from `backend/app/connectors/llm.py`):** it sends
`POST {LLM_BASE_URL}/chat/completions` with the header `Authorization: Bearer <LLM_API_KEY>` and
a body containing `model`, `messages`, `temperature`, `max_tokens` and (for structured output)
`response_format` with a JSON schema. Azure OpenAI's **v1 API**
(`https://<resource>.openai.azure.com/openai/v1`) accepts exactly this OpenAI-style request,
with the **deployment name** as `model` — so no code change is needed. That leads to three rules:

| Rule | Why |
|---|---|
| Use the **`/openai/v1`** base URL, not the older `/openai/deployments/...?api-version=` URL and not the Foundry *project* endpoint (`…services.ai.azure.com/api/projects/…`) | Only the v1 route takes the OpenAI-style request and Bearer key the app sends |
| Pick a **non-reasoning chat model** — **`gpt-4.1-mini`** (recommended), `gpt-4.1`, or `gpt-4o-mini` | The app sends `max_tokens` and `temperature: 0.1`. Reasoning models (the **GPT-5 family**, **o1/o3/o4-mini**) reject both on Azure ("use max_completion_tokens" / "temperature not supported") and every call would fail |
| **Key authentication must be allowed** on the Foundry resource | The app sends a static key. If your company disables keys ("local authentication disabled") and allows only Microsoft Entra ID sign-in, the app cannot call the model without a code change — raise this with your admin before you start |

### 15.1 Create the Azure AI Foundry resource

1. Azure portal search bar → **Azure AI Foundry** → open it → in the left menu choose
   **AI Foundry** (sometimes shown as **Use with AI Foundry → AI Foundry**) → **+ Create** →
   **Foundry** (if offered a choice between *Foundry resource* and *Hub*, pick the **Foundry
   resource** — it is the newer, simpler option).
   > Seeing only **Azure OpenAI** instead? That works too: search **Azure OpenAI** → **+ Create**
   > → same fields → it gives the same `…openai.azure.com` endpoint and the same deploy flow.
2. **Basics** tab:
   - **Subscription** · **Resource group:** yours
   - **Name:** `aif-markets-<suffix>`
   - **Region:** a region where `gpt-4.1-mini` is offered. Check first: Microsoft Learn →
     *Azure OpenAI models — model summary table and region availability*. If your resource
     group's region (e.g. Central India) does not list it, choose a nearby region that does —
     resources in a resource group may live in any region unless a company policy says otherwise.
   - **Default project name:** `markets` (a project is a workspace inside the resource; the app
     does not use it, but the Foundry portal needs one)
   - **Pricing tier:** **Standard S0**
3. **Network** tab: **All networks, including the internet, can access this resource**. (The
   Container App's outgoing IP address is not fixed, so "selected networks" would block it. To
   lock this down, see §17.3 and ask your network team about a private endpoint.)
4. **Identity** tab: leave defaults. **Tags** tab: company tags if required.
5. **Review + create** → **Create** → wait → **Go to resource**.

### 15.2 Check key authentication is allowed

Foundry resource → left menu **Resource Management** → **Keys and Endpoint**. You should see
**KEY 1**, **KEY 2** and endpoints. If instead the page says keys are disabled / *local
authentication is disabled*, stop here: the app cannot use this resource until your admin allows
key authentication on it (see the third rule above).

### 15.3 Deploy the model

1. Foundry resource → **Overview** → **Go to Azure AI Foundry portal** (opens https://ai.azure.com
   in your project `markets`).
2. Left menu **Models + endpoints** (or **My assets → Models + endpoints**) → **+ Deploy model**
   → **Deploy base model**.
3. Search **gpt-4.1-mini** → select it → **Confirm**.
4. In the deployment panel click **Customize** and set:
   - **Deployment name:** `gpt-4.1-mini` (write it down — this is `LLM_MODEL_ID`)
   - **Deployment type:**
     - **Standard** — requests are processed in the resource's region (pick this if your company
       requires data to stay in a region), or
     - **Global Standard** — highest availability and quota, but requests may be processed in any
       Azure region; or **Data Zone Standard** where offered
   - **Model version:** the default (latest) · **Version update policy:** *Once a new default
     version is available* is fine
   - **Tokens per Minute Rate Limit:** **30K–50K** is plenty for this app (it makes a few calls
     per research request; `LLM_DAILY_BUDGET` caps it at 200 calls a day)
   - **Content filter:** **DefaultV2** (the default)
5. **Deploy**. After a few seconds the deployment shows **Succeeded**.

> "Insufficient quota" or the model is greyed out? Your subscription has no quota for that model
> and type in that region. Try the other deployment type, another region (a new Foundry resource),
> or ask your admin to request quota (Foundry portal → **Management center** → **Quota**).

### 15.4 Try it in the playground

1. On the deployment's page click **Open in playground** (or left menu **Playgrounds → Chat**,
   then pick deployment `gpt-4.1-mini`).
2. Type *Reply with the single word OK.* → you should get `OK`. This proves the deployment works
   before the app is involved.

### 15.5 Copy the endpoint and key into Key Vault

1. Azure portal → Foundry resource → **Resource Management** → **Keys and Endpoint**.
2. Find the **Azure OpenAI** endpoint, which looks like `https://aif-markets-<suffix>.openai.azure.com/`
   (in some layouts it is on an **Azure OpenAI** tab; the Foundry portal's deployment page also
   shows it under **Endpoint → Target URI** — keep only the part up to `.azure.com/`).
   Your **base URL** for the app is that endpoint plus `openai/v1`:

   ```
   https://aif-markets-<suffix>.openai.azure.com/openai/v1
   ```

   (Some newer resources show `https://aif-markets-<suffix>.cognitiveservices.azure.com/` or
   `https://aif-markets-<suffix>.services.ai.azure.com/` instead — append `openai/v1` the same way.)
3. Click the copy icon next to **KEY 1**.
4. Key vault → **Objects** → **Secrets** → `llm-api-key` → **+ New Version** → paste the key as
   **Secret value** → **Create**. (Plan B users: paste it into the Container App secret
   `llm-api-key` instead — App → **Settings** → **Secrets** → `llm-api-key` → **Edit**.)

### 15.6 Turn the LLM on in the Container App

App → **Application** → **Revisions and replicas** → **Create new revision** → click container
**`api`** → **Environment variables**. Change `LLM_ENABLED` and add the rest:

| Name | Source | Value |
|---|---|---|
| `LLM_ENABLED` | Manual | `true` |
| `LLM_PROVIDER_NAME` | Manual | `Azure OpenAI (AI Foundry)` (label shown in the app) |
| `LLM_BASE_URL` | Manual | `https://aif-markets-<suffix>.openai.azure.com/openai/v1` |
| `LLM_API_KEY` | **secret** | `llm-api-key` (already there from §14.3) |
| `LLM_MODEL_ID` | Manual | your **deployment name**, e.g. `gpt-4.1-mini` |
| `LLM_RESPONSE_FORMAT` | Manual | `json_schema` (structured output; supported by gpt-4.1 / gpt-4o-mini) |
| `LLM_VERIFY_MODEL_LIST` | Manual | `false` (Azure's `/models` list shows model names, not your deployment names, so the check would wrongly fail) |
| `LLM_MAX_OUTPUT_TOKENS` | Manual | `6000` |
| `LLM_TIMEOUT_SECONDS` | Manual | `60` (must stay below nginx's 120 s) |
| `LLM_MAX_RETRIES` | Manual | `1` |
| `LLM_DAILY_BUDGET` | Manual | `200` (model calls per UTC day — your cost cap) |
| `LLM_DAILY_TOKEN_BUDGET` | Manual | `2000000` |

**Do not add `LLM_EXTRA_BODY` or `LLM_EXTRA_HEADERS`.** `.env.example` has OpenRouter-only
values for them (`usage`, `reasoning`) that Azure rejects with *400 Unrecognized request
argument*. Leaving them out is correct for Azure.

**Save** → **Create**. A new revision starts (about a minute).

Because the key lives in Key Vault, rotating it later is just: Foundry resource → **Regenerate
Key 2**, put it in `llm-api-key` as a new version, restart the revision (App → **Revisions and
replicas** → **Restart**), then regenerate Key 1.

### 15.7 Check the app can use the model

1. Open the app → **Data status** page → the LLM / model entry should show provider
   *Azure OpenAI (AI Foundry)*, model `gpt-4.1-mini`, and **available**. The app checks this with
   one tiny call ("Reply with the single word OK.").
2. Open any stock's **Research board** and run a research request. The summary should appear
   with its cited sources; token usage appears on the Data status page. Azure does not send a
   cost figure with each reply, so the app shows **tokens** — see real money in §20.
3. If it shows **unavailable**, read the reason shown there and §19 (LLM rows).

---

## 16. First start and checks

1. **Watch it start:** App → **Monitoring** → **Log stream** → pick the `api` container. You
   will see JSON log lines. On an empty database the app starts its first full data load
   (`AUTO_INITIAL_REFRESH`); with `UNIVERSE_SCOPE=all` in live mode this can take a long time.
2. **Health checks:** in a browser open
   - `https://<Application Url>/health/live` → `{"status":"ok"}`
   - `https://<Application Url>/health/ready` → shows `ready` and `"database": "ok"`
3. **Open the app** at the Application Url. It asks for the API token: Key vault → Secrets →
   `api-auth-token` → current version → **Show Secret Value** → copy → paste.
4. **Log in to Kite (Zerodha) once:** in the app, open the **Data status** page → connect Kite →
   open the link it shows → approve in Zerodha. The session is saved on the file share, so it
   survives restarts. Kite expires sessions periodically; log in again the same way.
5. **Data status page:** check that the NSE MCP tools were discovered, the last-success times,
   and the next scheduled runs (shown in IST).
6. **Check the file share works:** Storage account → **File shares** → `var` → **Browse**. After
   the Kite login you should see `kite_mcp_session.json` (and later `nse_filings/`).

---

## 17. Optional services

### 16.1 Application Insights + an "app is down" email

1. Search **Application Insights** → **+ Create** → Resource group: yours · **Name**
   `appi-markets-<suffix>` · **Region** = yours · **Resource Mode** **Workspace-based** ·
   **Log Analytics Workspace** `log-markets-<suffix>` → **Review + create** → **Create**.
2. Open it → **Investigate** → **Availability** → **+ Add Standard test** → **Test name**
   `markets-live` · **URL** `https://<Application Url>/health/live` · **Test frequency** 5 min
   · tick **Enable alerts** → **Create**.
3. Open the alert it created (**Monitoring** → **Alerts** → **Alert rules**) → **Edit** →
   **Actions** → **+ Create action group** → **Notifications** → **Email** → your address → save.

### 16.2 Custom domain with a free certificate

1. App → **Settings** → **Custom domains** → **+ Add custom domain** → **Managed certificate**.
2. Enter `markets.<yourdomain>`. The panel shows a **CNAME** and a **TXT (asuid)** record.
3. Add both records at your DNS provider (ask whoever manages your company domain).
4. Back in the panel → **Validate** → **Add**. The certificate is issued within ~20 minutes.

### 16.3 Private networking (if your company forbids public endpoints)

This needs a virtual network with two subnets, PostgreSQL in **Private access (VNet
Integration)** mode, and the Container Apps environment created **inside** the VNet. It must be
chosen **before** creating the database (§9) and the environment (§12) — they cannot be moved
into a network later. In the portal:

1. Your network team may already own a virtual network — ask them for two subnets:
   one `/23` delegated to `Microsoft.App/environments` (for the environment) and one `/28`
   delegated to `Microsoft.DBforPostgreSQL/flexibleServers` (for the database).
   To create your own: search **Virtual networks** → **+ Create** → address space `10.40.0.0/16`
   → **IP addresses** tab → add both subnets → on each subnet set **Subnet delegation** accordingly.
2. §9.1 **Networking** tab: **Private access (VNet Integration)** → pick the VNet and the `/28`
   subnet → private DNS zone: create new.
3. §12.1 **Networking** tab: **Use your own virtual network: Yes** → pick the VNet and the `/23`
   subnet.
4. §9.4 then needs a machine inside the network to run the SQL (ask your network team, or see
   `04-azure-migration.md` §6.4).
5. Foundry resource (§15.1 **Network** tab): **Disabled** public access + **+ Add private
   endpoint** in the same VNet (sub-resource `account`), so the app reaches the model privately.
   The playground then works only from inside the network.

### 16.4 Redis

Not needed while the app runs one replica. Only when scaling out (see `04-azure-migration.md` §14).

---

## 18. Deploying a new version later

1. **Build the new images** with a new tag (e.g. `v2`) in Cloud Shell, exactly as §11
   (`git pull` first, or upload a fresh zip).
2. **Migrations first:** Job `markets-migrate` → **Containers** → **Edit and deploy** → container
   → **Image tag** `v2` → **Save** → **Overview** → **Run now** → wait for **Succeeded**.
3. **Then the app:** App → **Revisions and replicas** → **Create new revision** → click `api` →
   **Image tag** `v2` → **Save**; click `web` → **Image tag** `v2` → **Save** → **Create**.

Avoid deploying at 19:30 IST, 00:00/12:00 IST, or 06:30 IST on the 1st of the month (scheduled
jobs). To automate releases with GitHub Actions, see `04-azure-migration.md` §9.

**Roll back:** App → **Revisions and replicas** → create a new revision using the previous
image tag (in single revision mode, old revisions cannot simply be re-activated alongside).

---

## 19. Troubleshooting

| Symptom (where you see it) | Likely cause | Fix |
|---|---|---|
| "Validation failed … disallowed by policy" (create form) | Company policy: region, size, tags, public access | Read the policy name in the error; change that field or ask the admin |
| "The subscription is not registered to use namespace…" | Provider not registered | §2.3 |
| Role assignment button greyed out | You are Contributor | Ask admin, or Plan B (§14.6) |
| Key Vault Secrets page says "unauthorized" | Your Secrets Officer role is missing or not yet applied | §8.2; wait 5 minutes and refresh |
| Cloud Shell `az acr build` fails at `uv sync` | Network blip or out-of-date lockfile | Run it again; check `backend/uv.lock` is in the code you uploaded |
| Revision **Failed**/**Degraded**, system logs say image pull unauthorized | `AcrPull` missing or identity not chosen for the registry | §7.2; App → **Settings** → **Registries** → identity = `id-markets-<suffix>` |
| "Unable to fetch secret … from Key Vault" | Identity lacks **Key Vault Secrets User**, or URL typo | §8.2; compare the URL with the secret's **Secret Identifier** |
| Migration **Failed**, log says `password authentication failed` | Wrong `database-url` | Recheck §9.4/§9.5; Key Vault → new version of `database-url`; run the job again |
| Migration **Failed**, log says `timeout` / `could not connect` | Firewall | Server → **Networking** → "Allow public access from any Azure service" ticked → **Save** |
| `/health/ready` shows 503 | Database unreachable or stopped | Check `database-url`, firewall, and that the server's **Status** is *Ready* (Burstable servers can be stopped) |
| `api` log: `Permission denied: /app/var/...` | Mount options missing | §14.3 a): mount options with `uid=10001,gid=10001` |
| Kite asks to log in after every restart | Volume not mounted on `api` | §14.3 b) **Volume mounts**; check the share has `kite_mcp_session.json` |
| Browser keeps asking for the token | Wrong value pasted | §16 step 3 |
| Website loads but every page shows API errors | `api` container missing or not ready | §14.3; check `API_UPSTREAM=127.0.0.1:8000` on `web` |
| Scheduled refreshes never run | Replicas scaled to 0 | §14.3 c): min 1, max 1 |
| LLM **401 Unauthorized** / *Access denied due to invalid subscription key* | Wrong or old key in `llm-api-key`, or the revision still has the old value | §15.5; restart the revision after adding a new secret version |
| LLM **404 Resource not found** / *DeploymentNotFound* | `LLM_BASE_URL` missing `/openai/v1`, a project endpoint was used, or `LLM_MODEL_ID` ≠ deployment name | §15.5 step 2 and §15.6; the deployment name is in Foundry → **Models + endpoints** |
| LLM **400** *Unsupported parameter: 'max_tokens'* or *'temperature' does not support 0.1* | A reasoning model (GPT-5 family, o-series) was deployed | Deploy `gpt-4.1-mini` (or `gpt-4.1` / `gpt-4o-mini`) and point `LLM_MODEL_ID` at it (§15.3) |
| LLM **400** *Unrecognized request argument* (`usage`, `reasoning`) | `LLM_EXTRA_BODY` copied from `.env.example` | Delete `LLM_EXTRA_BODY` / `LLM_EXTRA_HEADERS` from the `api` container (§15.6) |
| LLM **400** *content_filter* / *ResponsibleAIPolicyViolation* | Azure's content filter blocked a prompt or reply (rare with financial text) | The app falls back to its deterministic summary; retry, or review the filter under Foundry → **Guardrails + controls** |
| LLM **429 Too Many Requests** | Deployment's tokens-per-minute limit reached | Raise the TPM on the deployment (Foundry → deployment → **Edit**), or wait a minute |
| LLM shows *model not listed* | `LLM_VERIFY_MODEL_LIST` left at `true` | Set it to `false` (§15.6) |
| LLM **403** / connection refused from the app only (playground works) | Foundry resource networking restricted | §15.1 step 3: allow all networks, or set up a private endpoint (§17.3) |
| Keys page says local authentication disabled | Company policy forces Entra ID sign-in | Ask the admin to allow key auth on this resource; otherwise the LLM needs a code change |

**Where to read logs:**

- Live: App → **Monitoring** → **Log stream** (choose **Console** for app output, **System** for
  image pulls/secrets/probes).
- History: App → **Monitoring** → **Logs** → close the queries pop-up → paste
  `ContainerAppConsoleLogs_CL | order by TimeGenerated desc | take 200` → **Run**. For the job,
  use **Execution history** → the execution → **Console logs**.
- Inside the container: App → **Monitoring** → **Console** → container `api` → `/bin/sh` →
  e.g. `ls -la /app/var`.

---

## 20. Cost, pausing and deleting

Rough monthly cost at these sizes (use the Azure pricing calculator for your region and company
discounts):

| Service | Size | Rough cost |
|---|---|---|
| Container App | 1.25 vCPU / 2.5 GiB, always on | largest item |
| PostgreSQL Flexible Server | Burstable B1ms, 32 GiB | small |
| Container Registry | Basic | small |
| Storage, Key Vault, Log Analytics | low usage | very small |
| Azure OpenAI deployment (Standard / Global Standard) | pay per token; no charge when idle | small at `LLM_DAILY_BUDGET=200` calls/day — `gpt-4.1-mini` is one of the cheapest models |

To see real spend: resource group → **Cost Management** → **Cost analysis**.

**Pause when not in use**

- App: **Revisions and replicas** → **Create new revision** → **Scale** → min `0`, max `1`
  → **Create**. Note: scheduled jobs stop running. Or **Overview** → **Stop** (if shown).
- Database: server → **Overview** → **Stop**. (Azure restarts a stopped server automatically
  after 7 days.)
- Resume: database **Start**; app back to min `1` / max `1` (or **Start**).

**Delete everything you created** — you cannot delete the company resource group, so delete the
resources inside it:

1. Open your resource group → tick only the resources you created in this guide (check each name
   against your table in §4).
2. **Delete** (top bar) → type `delete` to confirm → **Delete**. If some fail because of
   dependencies, repeat — delete the Container App and Job first, then the environment, then the rest.
3. The key vault is kept as "soft-deleted" for 90 days: search **Key vaults** → **Manage deleted
   vaults** → **Purge** if you need the name back.
4. The Foundry resource is soft-deleted too (and its quota stays reserved for 48 hours): search
   **Azure AI Foundry** → **AI Foundry** → **Manage deleted resources** → select it → **Purge**.
   Delete its model deployment first (Foundry portal → **Models + endpoints** → select → **Delete**).

---

## 21. Final checklist

- [ ] §2 Subscription, resource group, region, role, policies and required tags known
- [ ] §2.3 All resource providers **Registered**
- [ ] §4 Names chosen and written down; three passwords/tokens generated
- [ ] §5 Log Analytics workspace created
- [ ] §6 Container registry created (Admin user off)
- [ ] §7 Managed identity created; **AcrPull** granted (or Plan B)
- [ ] §8 Key vault created; roles granted; `api-auth-token`, `pg-admin-password`, `llm-api-key` stored
- [ ] §9 PostgreSQL created; `markets` database and `markets` user; `database-url` stored
- [ ] §10 Storage account + `var` file share
- [ ] §11 `markets-api:v1` and `markets-web:v1` visible under **Repositories**
- [ ] §12 Container Apps environment + `markets-var` Azure Files link
- [ ] §13 Migration job ran → **Succeeded**
- [ ] §14 Container App with `web` + `api`, volume, secrets, min = max = 1, single revision mode
- [ ] §15 Foundry resource created, key auth allowed, `gpt-4.1-mini` deployed and answers in the playground
- [ ] §15 `llm-api-key` holds the Azure key; `LLM_*` variables set (`/openai/v1` base URL, deployment name, model-list check off, no extra body); Data status shows the LLM **available**
- [ ] §16 `/health/ready` OK, token works, Kite logged in, Data status page healthy
- [ ] §20 You know how to pause and delete it
