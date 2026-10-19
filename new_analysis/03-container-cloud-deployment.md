# 03 · Deploying as containers to any cloud

How to take this system from the repository to a cloud container platform. The same two images
and the same set of environment variables work on Azure, AWS, Google Cloud, or any Kubernetes —
only the "wrap it up" steps differ.

**Contents**

1. [The container images](#1-the-container-images)
2. [One change before any cloud deployment](#2-one-change-before-any-cloud-deployment)
3. [What every cloud needs](#3-what-every-cloud-needs)
4. [Google Cloud Run + Cloud SQL](#4-google-cloud-run--cloud-sql-shortest-path)
5. [Azure Container Apps + Azure Database for PostgreSQL](#5-azure-container-apps--azure-database-for-postgresql)
6. [AWS ECS Fargate + RDS + ALB](#6-aws-ecs-fargate--rds--alb)
7. [Any Kubernetes](#7-any-kubernetes)
8. [Scaling beyond one replica](#8-scaling-beyond-one-replica)
9. [Operations checklist](#9-operations-checklist)

> **Status:** none of these cloud paths has been executed from this repository. The steps are
> the standard way to run this container setup on each platform, adapted to this repo. Treat the
> first deployment as a trial. The Compose file/Dockerfiles themselves are also not yet
> exercised (see `01-system-analysis.md` §8).

---

## 1. The container images

Two images, both built from the repository root (the build context includes `infra/` and the
backend lockfile):

### Backend image — `infra/docker/backend.Dockerfile`

- `python:3.12-slim` + [uv](https://docs.astral.sh/uv/); installs exactly the locked runtime
  dependencies (`uv sync --frozen --no-dev`).
- Runs as a non-root user (`app`, uid 10001); `/app/var` is writable (uploaded financial
  statements land in `/app/var/fundamentals`).
- `EXPOSE 8000`; built-in `HEALTHCHECK` polls `http://127.0.0.1:8000/health/live`.
- Default command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

The same image serves three roles — selected by the command override:

| Role | Command |
|---|---|
| API | default (`uvicorn …`) |
| Worker (scheduler) | `python -m app.workers.run` |
| Migration | `alembic upgrade head` |

### Frontend image — `infra/docker/frontend.Dockerfile`

- Stage 1: `node:22-alpine`, `npm ci`, `npm run build` (TypeScript check + Vite build → `dist/`).
- Stage 2: `nginx:1.27-alpine` serving `dist/` with `infra/docker/nginx.conf`
  (`infra/docker/nginx.conf`), listening on **8080**:
  - `/api/` and `/health/` are proxied to the API;
  - static assets cached 30 days; security headers set (CSP, X-Frame-Options, …);
  - everything else falls back to `index.html` (client-side routing).

Build both:

```bash
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t markets-api:v1  .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t markets-web:v1 .
```

> **Always build `linux/amd64`** (or the platform your cloud runs) when building from an
> Apple Silicon Mac — the default `arm64` image is refused by most managed container services.

## 2. One change before any cloud deployment

`infra/docker/nginx.conf` hard-codes the upstream `proxy_pass http://api:8000;`. That hostname
only exists inside Docker Compose's network. In the cloud the usual topology puts **web and api
in one unit as side-by-side containers** sharing `127.0.0.1`, so the upstream must be
configurable. The nginx image has built-in support via templates:

1. Move `infra/docker/nginx.conf` → `infra/docker/nginx.conf.template`, and replace both
   `http://api:8000` occurrences with `http://${API_UPSTREAM}`:
   ```nginx
   location /api/    { proxy_pass http://${API_UPSTREAM}; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_read_timeout 120s; }
   location /health/ { proxy_pass http://${API_UPSTREAM}; }
   ```
2. In `infra/docker/frontend.Dockerfile` replace the config copy with:
   ```dockerfile
   COPY infra/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
   ENV API_UPSTREAM=api:8000
   ```
   At container start nginx substitutes `${API_UPSTREAM}` from the environment (only variables
   that are actually set are replaced, so `$host`, `$proxy_add_x_forwarded_for` etc. are safe).
3. Docker Compose keeps working unchanged (default `api:8000`). In the cloud set
   `API_UPSTREAM=127.0.0.1:8000`.

Alternative for the "one unit" pattern on platforms without sidecar networking: drop the web
container and let the API container also serve the static build (mount `dist/` and extend the
FastAPI static handling), or put a cloud load balancer/CDN in front that routes `/api/*` to the
API service and everything else to a static-site host.

## 3. What every cloud needs

### Topology (recommended first deployment)

```
 Internet ─HTTPS─▶ [ web container: nginx :8080 ] ──127.0.0.1:8000──▶ [ api container :8000 ]
                         (one unit, 1 replica, both containers side by side)        │
                                                                                     ▼
                                                                     Managed PostgreSQL 16
```

- **One replica**, scheduler running inside the API (`SCHEDULER_ENABLED=true`). With a single
  process, Redis is not required (in-process cache and locks).
- **Managed PostgreSQL 16** — container filesystems are wiped on restart, so the system of
  record must live outside the containers.
- **A one-off migration** (`alembic upgrade head`) before each new version, run as a job.
- **Live mode** (`DATA_MODE=live`). Demo mode needs three mock-MCP ports and is designed for a
  laptop, not the cloud.

### Environment variables

Set these as container env vars; secrets through the platform's secret store, never baked into
images:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATA_MODE` | `live` |
| `DATABASE_URL` | `postgresql+psycopg://markets:<password>@<host>:5432/markets?sslmode=require` (**secret**) |
| `AUTO_CREATE_SCHEMA` | `false` (schema comes from migrations) |
| `AUTO_INITIAL_REFRESH` | `true` (fills an empty database at first start) |
| `SCHEDULER_ENABLED` | `true` (single replica) |
| `REDIS_URL` | empty (single replica) |
| `API_AUTH_TOKEN` | long random value (**secret**), `openssl rand -hex 32` |
| `CORS_ORIGINS` | the public URL of the web app, e.g. `https://markets.example.com` |
| `NSE_BHAVCOPY_MCP_URL`, `NSE_CM_MARKET_MCP_URL`, `BROKER_MCP_URL` | as in `.env.example` |
| `LLM_ENABLED`, `HF_TOKEN` (**secret**), `HF_MODEL_ID` | only if you use the model |
| `WEB_SEARCH_PROVIDER` | `ddgs` or `none` |
| `API_UPSTREAM` (web container) | `127.0.0.1:8000` (after the §2 change) |

### Platform-specific facts to wire in

| Concern | Fact |
|---|---|
| Health checks | API liveness `GET /health/live`, readiness `GET /health/ready` (port 8000); web answers `GET /` on 8080 |
| Scheduler | Needs an always-on process — `minReplicas: 1`, or `--no-cpu-throttling` on Cloud Run, or a dedicated worker container |
| Uploaded statements | Written to `/app/var/fundamentals` in the API container — **lost on restart**. Mount persistent storage there (Azure Files / EFS / Cloud Run volume) or re-upload after restarts |
| Metrics | `GET /metrics` on the API (Prometheus format) |
| Logs | Structured JSON to stdout — ship to the platform's log service |
| Egress | The API must reach `mcp.nseindia.in`, `mcp.kite.trade`, optionally `huggingface.co` and search endpoints (443) |
| Compliance | NSE MCP data is delayed and for informational/educational use, not commercial deployment. Check NSE/Kite/search terms before going public; keep `API_AUTH_TOKEN` set |
| Rate of first ingest | First live refresh takes minutes (three months of history per call); keep job/startup timeouts generous |

### Pushing images (examples)

```bash
# Azure Container Registry
az acr create -g <rg> -n <acr> --sku Basic --admin-enabled true && az acr login -n <acr>
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t <acr>.azurecr.io/markets-api:v1 --push .
docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t <acr>.azurecr.io/markets-web:v1 --push .

# AWS ECR
aws ecr create-repository --repository-name markets-api
aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t <reg>/markets-api:v1 --push .

# Google Artifact Registry
gcloud artifacts repositories create markets --repository-format=docker --location=<region>
docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  -t <region>-docker.pkg.dev/<project>/markets/markets-api:v1 --push .
```

---

## 4. Google Cloud Run + Cloud SQL (shortest path)

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
CONN=$(gcloud sql instances describe markets-pg --format='value(connectionName)')
# Cloud Run reaches Cloud SQL through a Unix socket under /cloudsql:
DBURL="postgresql+psycopg://markets:$PGPASS@/markets?host=/cloudsql/$CONN"

# 3. Secrets
printf '%s' "$DBURL" | gcloud secrets create markets-database-url --data-file=-
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create markets-api-token --data-file=-
# Grant the Cloud Run service account roles/secretmanager.secretAccessor and roles/cloudsql.client

# 4. Migration as a Cloud Run job (run once per version)
gcloud run jobs create markets-migrate --region=$REGION --image=$IMG/markets-api:v1 \
  --command=alembic --args=upgrade,head \
  --set-cloudsql-instances=$CONN --set-secrets=DATABASE_URL=markets-database-url:latest
gcloud run jobs execute markets-migrate --region=$REGION --wait

# 5. Service: web (ingress, :8080) + api (sidecar, :8000)
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

- `--min-instances=1 --max-instances=1` — exactly one copy so the in-API scheduler runs once.
- `--no-cpu-throttling` — otherwise Cloud Run pauses CPU between requests, stalling the 12-hour
  and monthly scheduled jobs.
- `--depends-on=api` — the web container waits for the API.

After the first deploy, take the printed URL and set
`CORS_ORIGINS=https://<printed-url>`: `gcloud run services update markets --region=$REGION --container=api --update-env-vars=CORS_ORIGINS=…`.

If the sidecar cannot see `/cloudsql`, enable **private IP** on the Cloud SQL instance, deploy
with `--network=<vpc> --subnet=<subnet>`, and use the IP in `DATABASE_URL` with `?sslmode=require`.

Logs: Logs Explorer or `gcloud beta run services logs tail markets --region=$REGION`.
Database: `gcloud sql connect markets-pg --user=markets --database=markets`, or run
`cloud-sql-proxy $CONN --port 5432` for GUI tools.

## 5. Azure Container Apps + Azure Database for PostgreSQL

```bash
RG=markets-rg; LOC=centralindia; ACR=marketsacr$RANDOM; ENV=markets-env; PG=markets-pg-$RANDOM
PGPASS=$(openssl rand -hex 16)
az login && az group create -n $RG -l $LOC

# 1. Registry + images (see §3 for the buildx commands)
az acr create -g $RG -n $ACR --sku Basic --admin-enabled true && az acr login -n $ACR
# push markets-api:v1 and markets-web:v1 to $ACR.azurecr.io

# 2. PostgreSQL 16 (Burstable B1ms is the cheapest tier)
az postgres flexible-server create -g $RG -n $PG -l $LOC --version 16 \
  --tier Burstable --sku-name Standard_B1ms --storage-size 32 \
  --admin-user markets --admin-password "$PGPASS" --public-access 0.0.0.0  # 0.0.0.0 = allow Azure services
az postgres flexible-server db create -g $RG -s $PG -d markets
DBURL="postgresql+psycopg://markets:$PGPASS@$PG.postgres.database.azure.com:5432/markets?sslmode=require"

# 3. Container Apps environment
az extension add -n containerapp --upgrade
az containerapp env create -g $RG -n $ENV -l $LOC

# 4. Migration job, then run it
az containerapp job create -g $RG -n markets-migrate --environment $ENV \
  --trigger-type Manual --replica-timeout 600 \
  --image $ACR.azurecr.io/markets-api:v1 \
  --registry-server $ACR.azurecr.io --registry-username $ACR --registry-password "$(az acr credential show -n $ACR --query 'passwords[0].value' -o tsv)" \
  --secrets db-url="$DBURL" --env-vars DATABASE_URL=secretref:db-url \
  --command alembic --args upgrade head
az containerapp job start -g $RG -n markets-migrate
```

Two containers in one app need a YAML. Save as `aca-app.yaml` (fill the `<…>`):

```yaml
properties:
  managedEnvironmentId: <id from: az containerapp env show -g markets-rg -n markets-env --query id -o tsv>
  configuration:
    ingress: { external: true, targetPort: 8080, transport: auto }
    registries: [{ server: <acr>.azurecr.io, username: <acr>, passwordSecretRef: acr-pass }]
    secrets:
      - { name: acr-pass,  value: <ACRPASS> }
      - { name: db-url,    value: <DBURL> }
      - { name: api-token, value: <openssl rand -hex 32> }
      - { name: hf-token,  value: <HF token or placeholder> }
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
          - { name: HF_TOKEN, secretRef: hf-token }
          - { name: CORS_ORIGINS, value: "https://<the app URL>" }
        probes:
          - { type: Liveness,  httpGet: { path: /health/live,  port: 8000 } }
          - { type: Readiness, httpGet: { path: /health/ready, port: 8000 } }
```

```bash
az containerapp create -g $RG -n markets --yaml aca-app.yaml
az containerapp show -g $RG -n markets --query properties.configuration.ingress.fqdn -o tsv  # your URL
```

Put that URL into `CORS_ORIGINS`, then `az containerapp update -g $RG -n markets --yaml aca-app.yaml`.
New version: push new tags → update tags in the YAML → run the migration job → `az containerapp update`.
Logs: `az containerapp logs show -g $RG -n markets --container api --follow`.
Database access: add a firewall rule for your IP (`az postgres flexible-server firewall-rule create …`), `psql` in, remove the rule.

## 6. AWS ECS Fargate + RDS + ALB

AWS needs more networking pieces (VPC, subnets, security groups, ALB, ACM certificate) — the
console's "Create cluster → Create service" wizard can create the load balancer and security
groups for you.

```bash
REGION=ap-south-1; ACCT=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCT.dkr.ecr.$REGION.amazonaws.com

aws ecr create-repository --repository-name markets-api --region $REGION
aws ecr create-repository --repository-name markets-web --region $REGION
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REG
# build & push both images (see §3)

# PostgreSQL 16 (private)
aws rds create-db-instance --region $REGION --db-instance-identifier markets-pg \
  --engine postgres --engine-version 16 --db-instance-class db.t4g.micro \
  --allocated-storage 20 --db-name markets --master-username markets \
  --manage-master-user-password --no-publicly-accessible --backup-retention-period 7
# RDS keeps the generated password in Secrets Manager; build DATABASE_URL from it.

aws secretsmanager create-secret --name markets/database-url --secret-string '<DATABASE_URL>'
aws secretsmanager create-secret --name markets/api-token --secret-string "$(openssl rand -hex 32)"
aws ecs create-cluster --cluster-name markets
aws logs create-log-group --log-group-name /ecs/markets
```

Task definition (`task.json`) — both containers share `127.0.0.1` inside the Fargate task; the
execution role needs `AmazonECSTaskExecutionRolePolicy` plus `secretsmanager:GetSecretValue`:

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
      "name": "web", "image": "<reg>/markets-web:v1", "essential": true,
      "portMappings": [{ "containerPort": 8080 }],
      "environment": [{ "name": "API_UPSTREAM", "value": "127.0.0.1:8000" }],
      "dependsOn": [{ "containerName": "api", "condition": "HEALTHY" }],
      "logConfiguration": { "logDriver": "awslogs", "options": { "awslogs-group": "/ecs/markets", "awslogs-region": "ap-south-1", "awslogs-stream-prefix": "web" } }
    },
    {
      "name": "api", "image": "<reg>/markets-api:v1", "essential": true,
      "environment": [
        { "name": "APP_ENV", "value": "production" }, { "name": "DATA_MODE", "value": "live" },
        { "name": "AUTO_CREATE_SCHEMA", "value": "false" }, { "name": "SCHEDULER_ENABLED", "value": "true" },
        { "name": "CORS_ORIGINS", "value": "https://<your domain or ALB DNS>" }
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

# Migration: run the task once with the command overridden
aws ecs run-task --cluster markets --launch-type FARGATE --task-definition markets \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet>],securityGroups=[<sg>],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]},{"name":"web","command":["true"]}]}'

# Service behind an ALB (HTTPS listener + ACM cert; target group type ip, port 8080, health check /)
aws ecs create-service --cluster markets --service-name markets --task-definition markets \
  --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnets>],securityGroups=[<sg>],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=<tg-arn>,containerName=web,containerPort=8080"
```

Security groups: ALB → task on 8080; task → RDS on 5432; task → internet (443) for
NSE/Kite/HF. New version: push tags → register a new task-definition revision → run the
migration → `aws ecs update-service`. Logs: CloudWatch `/ecs/markets`. Database access: RDS is
private — enable ECS Exec (`--enable-execute-command`) and shell into the API container, or use
an SSM port-forward to the RDS endpoint.

## 7. Any Kubernetes

The same images run unchanged. Key points: the scheduler needs an always-on pod (single replica
with `SCHEDULER_ENABLED=true`, or a separate worker Deployment); migrations run as a Job/pre-sync
hook; the DB is a managed service, not in-cluster; uploads need a `ReadWriteMany`/`ReadWriteOnce`
volume mounted at `/app/var` **on the API pod** if you want them to survive restarts.

```yaml
# api deployment (excerpt)
metadata: { name: markets-api }
spec:
  replicas: 1                     # keep 1 while the scheduler runs here
  template:
    spec:
      containers:
        - name: api
          image: <registry>/markets-api:v1
          ports: [{ containerPort: 8000 }]
          env:
            - { name: APP_ENV, value: production }
            - { name: DATA_MODE, value: live }
            - { name: AUTO_CREATE_SCHEMA, value: "false" }
            - { name: SCHEDULER_ENABLED, value: "true" }
            - name: DATABASE_URL
              valueFrom: { secretKeyRef: { name: markets-secrets, key: database-url } }
            - name: API_AUTH_TOKEN
              valueFrom: { secretKeyRef: { name: markets-secrets, key: api-token } }
          readinessProbe:  { httpGet: { path: /health/ready, port: 8000 } }
          livenessProbe:   { httpGet: { path: /health/live,  port: 8000 } }
          resources: { requests: { cpu: 250m, memory: 512Mi }, limits: { cpu: "1", memory: 2Gi } }
---
# migration job (run before each new version)
apiVersion: batch/v1
kind: Job
metadata: { name: markets-migrate }
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: <registry>/markets-api:v1
          command: ["alembic", "upgrade", "head"]
          env:
            - name: DATABASE_URL
              valueFrom: { secretKeyRef: { name: markets-secrets, key: database-url } }
---
# web deployment: nginx :8080 with API_UPSTREAM=http://markets-api:8000 (Service DNS)
```

Expose the web Service through your ingress (TLS there); nginx only needs to reach the api
Service, so set `API_UPSTREAM=http://markets-api:8000` (with the §2 template change) — with a
Kubernetes Service you don't even need the sidecar pattern. Cron alternatives: the job crons are
configurable, so you can also disable the in-app scheduler and drive refreshes with
Kubernetes CronJobs hitting `POST /api/v1/jobs/refresh-all`.

## 8. Scaling beyond one replica

The only stateful singleton is the **scheduler**. To go wider:

1. Add managed Redis (Azure Managed Redis, Amazon ElastiCache, Google Memorystore) and set
   `REDIS_URL` (use `rediss://` when TLS is on) — this moves cache and job locks out of process.
2. Set `SCHEDULER_ENABLED=false` on the web+api units and raise their replica counts.
3. Run exactly one always-on backend container as the worker: `SCHEDULER_ENABLED=true`, command
   `python -m app.workers.run`, no ingress (a Container App without ingress, an ECS service with
   no load balancer, a Cloud Run worker-pool style service, or a Kubernetes Deployment with
   `replicas: 1` and a leader-lock if you want failover).

PostgreSQL sizing scales with universe size; screening filters run in memory over the latest
snapshots, which suits a universe of a few thousand instruments.

## 9. Operations checklist

| Item | Practice |
|---|---|
| Migrations | `alembic upgrade head` as a one-off job before every new version; `alembic check` in CI keeps models/migrations honest |
| Backups | Managed-DB automated backups + periodic `pg_dump -Fc`; test a restore |
| Secrets | Platform secret store only (Key Vault / Secrets Manager / Secret Manager / Kubernetes Secrets); rotate `API_AUTH_TOKEN` and `HF_TOKEN` |
| TLS | Terminate at the platform's ingress/ALB; nginx inside the container stays plain HTTP on 8080 |
| Observability | JSON logs → platform log service; scrape `GET /metrics` (Prometheus); alert on `/health/ready` failures, failed `job_runs`, stale data and LLM budget exhaustion (rules: `infra/monitoring/alerts.yml`) |
| Uploads | Mount persistent storage at `/app/var` (API container) or re-upload financial statements after restarts |
| Rollout | Single replica + migration job → push new image tags → run migration → update the service. Theme snapshots already taken are never rewritten |
| Cost control | `LLM_DAILY_BUDGET` / `LLM_DAILY_TOKEN_BUDGET` cap model spend; refresh endpoints are rate-limited; Redis is optional at one replica |
| Compliance | NSE MCP is delayed, informational/educational data — not licensed for commercial use or AI training; review NSE/Kite/search-provider terms and add jurisdiction-specific disclaimers before any public launch |

### Quick reference

| Task | Command |
|---|---|
| Build backend image | `docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile -t markets-api:v1 .` |
| Build web image | `docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile -t markets-web:v1 .` |
| Local full stack in containers | `docker compose --profile demo up --build` |
| Run migrations anywhere | `docker run -e DATABASE_URL=… <api-image> alembic upgrade head` |
| Run the worker anywhere | `docker run -e DATABASE_URL=… -e REDIS_URL=… <api-image> python -m app.workers.run` |
| Liveness / readiness | `GET :8000/health/live` · `GET :8000/health/ready` |
| Metrics | `GET :8000/metrics` |
