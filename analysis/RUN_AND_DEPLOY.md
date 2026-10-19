# Running, inspecting and deploying Markets Intelligence

A start-here guide for someone who has never run the system. It covers:

1. [What the system is made of](#1-what-the-system-is-made-of)
2. [Running locally, Docker, and the database](#25-running-locally-docker-and-the-database) (moved to `HOW_TO_RUN.md`)
6. [Deploying to the cloud (Azure, AWS, Google Cloud Run)](#6-deploying-to-the-cloud)
7. [Quick reference](#7-quick-reference)

Live-mode setup (real NSE/Kite data, OpenRouter model, web search, uploading financial
statements) and a troubleshooting table are in [`HOW_TO_RUN.md`](HOW_TO_RUN.md). This guide does
not repeat them.

All commands run from the project root (`/Users/rajeshsanapala/Documents/Hackathon`) unless a `cd`
is shown.

---

## 1. What the system is made of

```
  Browser
     │
     ▼
┌──────────────┐  /api, /health   ┌──────────────────┐     ┌────────────────────────────┐
│ Web app      │ ───────────────▶ │ API (FastAPI)    │ ──▶ │ NSE MCP / Kite MCP          │
│ React + Vite │                  │ + scheduler      │     │ (or local mock MCP in demo) │
│ :5173 (dev)  │                  │ :8000            │     └────────────────────────────┘
│ :8080 (nginx)│                  └──────┬───────────┘
└──────────────┘                         │
                         ┌───────────────┼──────────────────┐
                         ▼               ▼                  ▼
                 ┌──────────────┐ ┌─────────────┐  ┌──────────────────┐
                 │ Database     │ │ Redis       │  │ Worker (optional)│
                 │ PostgreSQL 16│ │ (optional)  │  │ runs the schedule│
                 │ via psycopg 3│ │ cache+locks │  │ instead of API   │
                 └──────────────┘ └─────────────┘  └──────────────────┘
```

| Part | Required? | Default when you do nothing |
|---|---|---|
| Web app | Yes | Vite dev server on port 5173 |
| API | Yes | Port 8000; also runs the scheduled jobs |
| Database | Yes | PostgreSQL 16 through psycopg 3; `scripts/dev.sh` starts it with Docker Compose when local |
| Redis | No | In-process cache and locks (fine for a single API process) |
| Worker | No | The API runs the schedule itself (`SCHEDULER_ENABLED=true`) |
| Mock MCP servers | Demo mode only | Ports 9100, 9101, 9102, synthetic data |

**Demo mode vs live mode** is controlled by `DATA_MODE` in `.env`:

- `demo` (default) — everything is synthetic, symbols start with `DEMO`, and every page shows an
  orange banner. No accounts or internet needed.
- `live` — real NSE MCP (and optionally Kite) data. See `HOW_TO_RUN.md` §4.

---

## 2–5. Running locally, Docker, and the database

These sections moved to [`HOW_TO_RUN.md`](HOW_TO_RUN.md), which is the single guide for local use:

| Topic | Where |
|---|---|
| One-command start (`./scripts/dev.sh`) | `HOW_TO_RUN.md` §4 |
| PostgreSQL setup (Docker, Homebrew, own server) | §3 |
| Starting each part separately | §8 |
| The whole stack in Docker Compose | §9 |
| Looking at the data, backups | §10 |
| Updating code, schema, dependencies | §11 |
| Troubleshooting | §14 |

---

## 6. Deploying to the cloud

> **Status:** none of these cloud paths has been run. The steps are the standard way to run this
> kind of container setup on each platform, adapted to this repository. Treat the first
> deployment as a trial and expect to adjust.

### 6.1 What to deploy (the same on every cloud)

A small, recommended first topology:

```
 Internet ─HTTPS─▶ [ web container: nginx :8080 ] ──127.0.0.1:8000──▶ [ api container :8000 ]
                         (one unit, 1 replica, both containers side by side)        │
                                                                                     ▼
                                                                    Managed PostgreSQL 16
```

- **web + api run together** in one unit (a Container App with two containers, an ECS task, a
  Cloud Run service with a sidecar). nginx serves the web app and forwards `/api` and `/health`
  to the API over `127.0.0.1`, so the browser only ever sees one origin.
- **One replica**, with the scheduler running inside the API (`SCHEDULER_ENABLED=true`). With a
  single process Redis is not needed. Add Redis and a separate worker later if you need more
  replicas (see 6.6).
- **Managed PostgreSQL** — container disks are wiped on restart, so the database must live outside the containers.
- **A one-off migration** (`alembic upgrade head`) before each new version.
- **Live mode.** Demo mode needs the mock MCP servers on three ports, which is not set up for
  the cloud.

### 6.2 The web container's API address (done)

nginx in the web image forwards `/api` and `/health` to `${API_UPSTREAM}`
(`infra/docker/nginx.conf.template`). The image defaults it to `api:8000`, which is what Docker
Compose needs. In the cloud, where web and api run side by side in one unit, set
`API_UPSTREAM=127.0.0.1:8000` on the web container. Verified on 2026-10-15: the Compose stack works
with the default, and the override renders a valid nginx configuration.

### 6.3 Settings every cloud needs

Set these as environment variables (secrets through the cloud's secret store, never in images):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATA_MODE` | `live` |
| `DATABASE_URL` | `postgresql+psycopg://markets:<password>@<host>:5432/markets?sslmode=require` (**secret**) |
| `AUTO_CREATE_SCHEMA` | `false` (tables come from the migration) |
| `AUTO_INITIAL_REFRESH` | `true` |
| `SCHEDULER_ENABLED` | `true` |
| `REDIS_URL` | empty (single replica) |
| `API_AUTH_TOKEN` | a long random value (**secret**); enter it under Data status → Access |
| `CORS_ORIGINS` | the public URL of the web app, e.g. `https://markets.example.com` |
| `NSE_BHAVCOPY_MCP_URL`, `NSE_CM_MARKET_MCP_URL`, `BROKER_MCP_URL` | as in `.env.example` |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_API_KEY` (**secret**), `LLM_MODEL_ID`, `LLM_PROVIDER_NAME`, `LLM_EXTRA_HEADERS`, `LLM_EXTRA_BODY` | if you use the model (any OpenAI-compatible provider) |
| `WEB_SEARCH_PROVIDER` | `ddgs`, or `none` |

Generate a token with `openssl rand -hex 32`.

**Build images for `linux/amd64`.** Your Mac (Apple Silicon) builds `arm64` images by default,
which most cloud container services will refuse to run. Always build from the project root:

```bash
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t <registry>/markets-api:v1 --push .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t <registry>/markets-web:v1 --push .
```

**Uploaded financial statements** are written to `/app/var/fundamentals` inside the API
container, which is lost when the container restarts. Either mount persistent storage there
(Azure Files, Amazon EFS, a Cloud Storage volume on Cloud Run) or re-upload after a restart.

**Health checks:** liveness `GET /health/live`, readiness `GET /health/ready` on port 8000; the
web container answers `GET /` on 8080.

**Compliance:** NSE states its MCP data is for informational/educational use, not commercial
deployment. Check NSE, Kite and search-provider terms before making a deployment public, and keep
`API_AUTH_TOKEN` set.

### 6.4 Azure — Container Apps + Azure Database for PostgreSQL

```bash
# Variables
RG=markets-rg; LOC=centralindia; ACR=marketsacr$RANDOM; ENV=markets-env; PG=markets-pg-$RANDOM
PGPASS=$(openssl rand -hex 16)

az login
az group create -n $RG -l $LOC

# 1. Container registry and images
az acr create -g $RG -n $ACR --sku Basic --admin-enabled true
az acr login -n $ACR
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t $ACR.azurecr.io/markets-api:v1 --push .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t $ACR.azurecr.io/markets-web:v1 --push .

# 2. PostgreSQL 16 (Burstable B1ms is the cheapest tier)
az postgres flexible-server create -g $RG -n $PG -l $LOC --version 16 \
  --tier Burstable --sku-name Standard_B1ms --storage-size 32 \
  --admin-user markets --admin-password "$PGPASS" --public-access 0.0.0.0   # 0.0.0.0 = allow Azure services
az postgres flexible-server db create -g $RG -s $PG -d markets
DBURL="postgresql+psycopg://markets:$PGPASS@$PG.postgres.database.azure.com:5432/markets?sslmode=require"

# 3. Container Apps environment
az extension add -n containerapp --upgrade
az containerapp env create -g $RG -n $ENV -l $LOC
ACRPASS=$(az acr credential show -n $ACR --query "passwords[0].value" -o tsv)

# 4. Migration job, then run it
az containerapp job create -g $RG -n markets-migrate --environment $ENV \
  --trigger-type Manual --replica-timeout 600 \
  --image $ACR.azurecr.io/markets-api:v1 \
  --registry-server $ACR.azurecr.io --registry-username $ACR --registry-password "$ACRPASS" \
  --secrets db-url="$DBURL" --env-vars DATABASE_URL=secretref:db-url \
  --command alembic --args "upgrade" "head"
az containerapp job start -g $RG -n markets-migrate
```

Two containers in one app are defined with a YAML file. Save as `aca-app.yaml` (replace the
`<…>` values):

```yaml
properties:
  managedEnvironmentId: <output of: az containerapp env show -g markets-rg -n markets-env --query id -o tsv>
  configuration:
    ingress: { external: true, targetPort: 8080, transport: auto }
    registries: [{ server: <acr>.azurecr.io, username: <acr>, passwordSecretRef: acr-pass }]
    secrets:
      - { name: acr-pass,  value: <ACRPASS> }
      - { name: db-url,    value: <DBURL> }
      - { name: api-token, value: <openssl rand -hex 32> }
      - { name: openrouter-key, value: <your OpenRouter key or leave a placeholder> }
  template:
    scale: { minReplicas: 1, maxReplicas: 1 }
    containers:
      - name: web
        image: <acr>.azurecr.io/markets-web:v1
        resources: { cpu: 0.25, memory: 0.5Gi }
        env: [{ name: API_UPSTREAM, value: "127.0.0.1:8000" }]
      - name: api
        image: <acr>.azurecr.io/markets-api:v1
        resources: { cpu: 1.0, memory: 2Gi }
        env:
          - { name: APP_ENV, value: production }
          - { name: DATA_MODE, value: live }
          - { name: AUTO_CREATE_SCHEMA, value: "false" }
          - { name: SCHEDULER_ENABLED, value: "true" }
          - { name: DATABASE_URL, secretRef: db-url }
          - { name: API_AUTH_TOKEN, secretRef: api-token }
          - { name: LLM_API_KEY, secretRef: openrouter-key }
          - { name: CORS_ORIGINS, value: "https://<will be the app URL>" }
        probes:
          - { type: Liveness,  httpGet: { path: /health/live,  port: 8000 } }
          - { type: Readiness, httpGet: { path: /health/ready, port: 8000 } }
```

```bash
az containerapp create -g $RG -n markets --yaml aca-app.yaml
az containerapp show -g $RG -n markets --query properties.configuration.ingress.fqdn -o tsv   # your URL
```

Put that URL into `CORS_ORIGINS` and run `az containerapp update -g $RG -n markets --yaml aca-app.yaml`.

**New version:** push images with a new tag, update the tags in the YAML, run the migration job
(`az containerapp job update … --image …:v2` then `job start`), then `az containerapp update`.

**Logs:** `az containerapp logs show -g $RG -n markets --container api --follow`.

**Database access:** add your IP to the firewall, then use `psql` or a GUI:

```bash
az postgres flexible-server firewall-rule create -g $RG -n $PG -r me --start-ip-address <your-ip> --end-ip-address <your-ip>
psql "host=$PG.postgres.database.azure.com port=5432 dbname=markets user=markets sslmode=require"
```

Remove the rule when done. Secrets can also live in Azure Key Vault and be referenced from the app.

### 6.5 AWS — ECS Fargate + RDS PostgreSQL + Application Load Balancer

AWS needs more networking pieces. The AWS console's "Create cluster → Create service" wizard can
create the load balancer and security groups for you; the pieces are:

```bash
REGION=ap-south-1; ACCT=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCT.dkr.ecr.$REGION.amazonaws.com

# 1. Registry and images
aws ecr create-repository --repository-name markets-api --region $REGION
aws ecr create-repository --repository-name markets-web --region $REGION
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REG
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t $REG/markets-api:v1 --push .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t $REG/markets-web:v1 --push .

# 2. PostgreSQL 16 (in the default VPC; keep it NOT publicly accessible)
aws rds create-db-instance --region $REGION --db-instance-identifier markets-pg \
  --engine postgres --engine-version 16 --db-instance-class db.t4g.micro \
  --allocated-storage 20 --db-name markets --master-username markets \
  --manage-master-user-password --no-publicly-accessible --backup-retention-period 7
# RDS stores the generated password in Secrets Manager; build DATABASE_URL from it:
#   postgresql+psycopg://markets:<password>@<rds-endpoint>:5432/markets?sslmode=require

# 3. Store app secrets
aws secretsmanager create-secret --name markets/database-url --secret-string '<DATABASE_URL>'
aws secretsmanager create-secret --name markets/api-token    --secret-string "$(openssl rand -hex 32)"

# 4. Cluster and logs
aws ecs create-cluster --cluster-name markets
aws logs create-log-group --log-group-name /ecs/markets
```

5. **Task definition** — save as `task.json` (replace `<…>`). Both containers share `127.0.0.1`
   inside a Fargate task. The execution role needs `AmazonECSTaskExecutionRolePolicy` plus
   `secretsmanager:GetSecretValue` on the two secrets.

```json
{
  "family": "markets",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024", "memory": "2048",
  "runtimePlatform": { "cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX" },
  "executionRoleArn": "arn:aws:iam::<acct>:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "web", "image": "<acct>.dkr.ecr.ap-south-1.amazonaws.com/markets-web:v1", "essential": true,
      "portMappings": [{ "containerPort": 8080 }],
      "environment": [{ "name": "API_UPSTREAM", "value": "127.0.0.1:8000" }],
      "dependsOn": [{ "containerName": "api", "condition": "HEALTHY" }],
      "logConfiguration": { "logDriver": "awslogs", "options": { "awslogs-group": "/ecs/markets", "awslogs-region": "ap-south-1", "awslogs-stream-prefix": "web" } }
    },
    {
      "name": "api", "image": "<acct>.dkr.ecr.ap-south-1.amazonaws.com/markets-api:v1", "essential": true,
      "environment": [
        { "name": "APP_ENV", "value": "production" }, { "name": "DATA_MODE", "value": "live" },
        { "name": "AUTO_CREATE_SCHEMA", "value": "false" }, { "name": "SCHEDULER_ENABLED", "value": "true" },
        { "name": "CORS_ORIGINS", "value": "https://<your domain or ALB DNS name>" }
      ],
      "secrets": [
        { "name": "DATABASE_URL",   "valueFrom": "arn:aws:secretsmanager:ap-south-1:<acct>:secret:markets/database-url" },
        { "name": "API_AUTH_TOKEN", "valueFrom": "arn:aws:secretsmanager:ap-south-1:<acct>:secret:markets/api-token" }
      ],
      "healthCheck": { "command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)\""], "interval": 30, "retries": 3, "startPeriod": 30 },
      "logConfiguration": { "logDriver": "awslogs", "options": { "awslogs-group": "/ecs/markets", "awslogs-region": "ap-south-1", "awslogs-stream-prefix": "api" } }
    }
  ]
}
```

```bash
aws ecs register-task-definition --cli-input-json file://task.json
```

6. **Migration** — run the task once with the API command overridden:

```bash
aws ecs run-task --cluster markets --launch-type FARGATE --task-definition markets \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet>],securityGroups=[<sg>],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]},{"name":"web","command":["true"]}]}'
```

(Or temporarily set `AUTO_CREATE_SCHEMA=true` for the very first start only.)

7. **Service + load balancer** — create an Application Load Balancer (HTTPS listener with an ACM
   certificate), a target group of type `ip` on port **8080** with health check path `/`, and:

```bash
aws ecs create-service --cluster markets --service-name markets --task-definition markets \
  --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnets>],securityGroups=[<sg>],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=<tg-arn>,containerName=web,containerPort=8080"
```

Security groups: ALB → task on 8080; task → RDS on 5432; task → internet (443) for NSE/Kite/HF.

**New version:** push new image tags, register a new task-definition revision, run the migration,
then `aws ecs update-service --cluster markets --service markets --task-definition markets`.

**Logs:** CloudWatch log group `/ecs/markets`, or `aws logs tail /ecs/markets --follow`.

**Database access:** RDS is private, so go through something inside the VPC. Simplest: enable ECS
Exec on the service (`--enable-execute-command`) and open a shell in the API container:

```bash
aws ecs execute-command --cluster markets --task <task-id> --container api --interactive --command "/bin/sh"
```

or run a small EC2 bastion and port-forward with SSM Session Manager
(`AWS-StartPortForwardingSessionToRemoteHost` to the RDS endpoint, port 5432), then connect
`psql`/DBeaver to `localhost:5432`.

### 6.6 Google Cloud Run + Cloud SQL for PostgreSQL

```bash
PROJECT=<your-project-id>; REGION=asia-south1; REPO=markets
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# 1. Registry and images
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
gcloud auth configure-docker $REGION-docker.pkg.dev
IMG=$REGION-docker.pkg.dev/$PROJECT/$REPO
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t $IMG/markets-api:v1 --push .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t $IMG/markets-web:v1 --push .

# 2. Cloud SQL PostgreSQL 16
gcloud sql instances create markets-pg --database-version=POSTGRES_16 --region=$REGION --tier=db-f1-micro
gcloud sql databases create markets --instance=markets-pg
PGPASS=$(openssl rand -hex 16)
gcloud sql users create markets --instance=markets-pg --password="$PGPASS"
CONN=$(gcloud sql instances describe markets-pg --format='value(connectionName)')   # PROJECT:REGION:markets-pg

# Cloud Run reaches Cloud SQL through a Unix socket under /cloudsql:
DBURL="postgresql+psycopg://markets:$PGPASS@/markets?host=/cloudsql/$CONN"

# 3. Secrets
printf '%s' "$DBURL"              | gcloud secrets create markets-database-url --data-file=-
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create markets-api-token --data-file=-
# Grant the Cloud Run service account (default: PROJECT_NUMBER-compute@developer.gserviceaccount.com)
# roles/secretmanager.secretAccessor and roles/cloudsql.client.

# 4. Migration as a Cloud Run job
gcloud run jobs create markets-migrate --region=$REGION --image=$IMG/markets-api:v1 \
  --command=alembic --args=upgrade,head \
  --set-cloudsql-instances=$CONN --set-secrets=DATABASE_URL=markets-database-url:latest
gcloud run jobs execute markets-migrate --region=$REGION --wait

# 5. Service: web (ingress, port 8080) + api (sidecar, port 8000)
gcloud run deploy markets --region=$REGION --allow-unauthenticated \
  --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --add-cloudsql-instances=$CONN \
  --container=web --image=$IMG/markets-web:v1 --port=8080 \
    --set-env-vars=API_UPSTREAM=127.0.0.1:8000 --depends-on=api \
  --container=api --image=$IMG/markets-api:v1 --cpu=1 --memory=2Gi \
    --set-env-vars=APP_ENV=production,DATA_MODE=live,AUTO_CREATE_SCHEMA=false,SCHEDULER_ENABLED=true \
    --set-secrets=DATABASE_URL=markets-database-url:latest,API_AUTH_TOKEN=markets-api-token:latest
```

Why these flags:

- `--min-instances=1 --max-instances=1` — exactly one copy, so the built-in scheduler runs once.
- `--no-cpu-throttling` — Cloud Run otherwise pauses the CPU between requests, which would stop
  the 12-hour and monthly scheduled jobs.
- `--depends-on=api` — web waits for the API container to start.

After the first deploy, take the printed URL, then:

```bash
gcloud run services update markets --region=$REGION --container=api \
  --update-env-vars=CORS_ORIGINS=https://<printed-url>
```

If the API container cannot see `/cloudsql`, connect to Cloud SQL by private IP instead (enable a
private IP on the instance, deploy with `--network=<vpc> --subnet=<subnet>` and use the IP in
`DATABASE_URL` with `?sslmode=require`).

**New version:** push new tags, update and execute the migration job (`gcloud run jobs update
markets-migrate --image …:v2`), then re-run the `gcloud run deploy` command with the new tags.

**Logs:** `gcloud beta run services logs tail markets --region=$REGION` or Logs Explorer.

**Database access:**

```bash
gcloud sql connect markets-pg --user=markets --database=markets      # temporarily allow-lists your IP
# or, for GUI tools, run the Cloud SQL Auth Proxy and connect to localhost:5432:
cloud-sql-proxy $CONN --port 5432
```

### 6.7 Scaling beyond one replica (all clouds)

When one replica is not enough:

1. Add managed Redis (Azure Managed Redis, Amazon ElastiCache, Google Memorystore) and set
   `REDIS_URL` (use `rediss://` when TLS is on).
2. Set `SCHEDULER_ENABLED=false` on the web+api unit and raise its maximum replicas.
3. Run one extra always-on backend container with `SCHEDULER_ENABLED=true` and the command
   `python -m app.workers.run` (a Container App with no ingress, an ECS service with no load
   balancer, or a Cloud Run worker pool / always-on service).

### 6.8 Which cloud to pick

| | Azure Container Apps | AWS ECS Fargate | Google Cloud Run |
|---|---|---|---|
| Setup effort | Medium | Highest (VPC, ALB, IAM) | Lowest |
| HTTPS | Automatic | ALB + ACM certificate | Automatic |
| Always-on scheduler | `minReplicas: 1` | Always on | Needs `--no-cpu-throttling` |
| Database access from your Mac | Firewall rule + `psql` | Via VPC (ECS Exec / SSM) | `gcloud sql connect` |

For a first deployment, Google Cloud Run is the shortest path.

---

## 7. Quick reference

| Task | Command |
|---|---|
| Start everything locally | `./scripts/dev.sh` |
| Web app (local / Docker) | http://127.0.0.1:5173 / http://127.0.0.1:8080 |
| API docs | http://127.0.0.1:8000/docs |
| Is the API healthy? | `curl http://127.0.0.1:8000/health/ready` |
| Refresh all data | `curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all` |
| Open the database | `docker compose exec postgres psql -U markets -d markets` |
| Open Docker PostgreSQL | `docker compose exec postgres psql -U markets -d markets` |
| Apply migrations | `cd backend && uv run alembic upgrade head` |
| Run backend tests | `cd backend && uv run pytest` |
| Run frontend tests | `cd frontend && npm test` |
| Start completely fresh (local) | stop the app, `rm -rf backend/var`, `./scripts/dev.sh` |
| Free busy ports | `lsof -ti :8000,:5173,:9100,:9101,:9102 \| xargs kill` |
