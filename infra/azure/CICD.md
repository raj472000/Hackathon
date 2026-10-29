# CI/CD: automatic deployment to Azure on push to `main`

Workflow: `.github/workflows/ci.yml`. Deploy target: Container App `markets` in `HK-RG-04` (one replica, two
containers, `web` and `api`), images in `acrmarketshackathon.azurecr.io`.

## What runs

| Event | Jobs |
|---|---|
| Pull request | `backend`, `frontend`, `e2e`, `images` (build + smoke test, **no push**) |
| Push to `main` | All of the above, then `images` pushes to ACR, then `deploy` (only if every job passed) |

```
backend ─┐
frontend ┼─ e2e ─┐
images ──────────┴─ deploy (environment: production)
```

- **`images`** builds `markets-api` and `markets-web` for `linux/amd64` (layer cache in GitHub Actions). It starts them
  with a throwaway PostgreSQL, runs `alembic upgrade head` from the image, and checks `/`, `/health/live`,
  `/health/ready` and `/api/v1/sources/status` through nginx, the same path as on Azure
  (`scripts/smoke-images.sh`). On `main` it then pushes both images, tagged with the full commit SHA.
- **`deploy`** runs `infra/azure/deploy.sh`:
  1. reads the app's current template from Azure;
  2. changes **only** the `web` and `api` image fields. Env vars, Key Vault secret references, probes, the
     `/app/var` volume and scale settings stay exactly as configured in Azure;
  3. applies both images as **one** revision, `markets--sha-<sha7>-<run>-<attempt>`;
  4. waits up to 10 minutes for it to become Healthy and serve traffic, then smoke-tests the public URL;
  5. if any step fails, re-applies the previous images as `markets--rb-…` and fails the job.
- Deploys never run in parallel and are never cancelled half-way (`concurrency: deploy-production`).

Configuration changes (env vars, secrets) are **not** part of the pipeline. Make them with `configure.sh`, the
portal or `az containerapp update`. The next deploy keeps them.

## Authentication (no stored secrets)

GitHub signs in to Azure with OIDC as the user-assigned managed identity `id-markets-github`:

| Federated credential | Subject | Used by |
|---|---|---|
| `github-main` | `repo:raj-accellor/Hackathon:ref:refs/heads/main` | `images` (push) |
| `github-production` | `repo:raj-accellor/Hackathon:environment:production` | `deploy` |

| Role | Scope | Why |
|---|---|---|
| AcrPush, Reader | registry `acrmarketshackathon` | push images; `az acr login` looks the registry up |
| Contributor | container app `markets` | update the template, read revisions |
| Contributor | its managed environment | a revision update joins the environment |

The GitHub environment `production` accepts deployments from `main` only. Repository **variables** (not secrets;
none of these values grants access): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_ACR`,
`AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_APP`.

## One-time setup

Step-by-step guide in the Azure portal (no command line): [`CICD-SETUP.md`](CICD-SETUP.md). Command-line version: [`set-github.md`](../../set-github.md). Run once by someone who
is Owner on `HK-RG-04` and admin on the GitHub repository. It is safe to re-run:

```bash
AZ_RG=HK-RG-04 AZ_ACR=acrmarketshackathon AZ_APP=markets infra/azure/setup-github-deploy.sh
```

## Running the pieces by hand

```bash
# Build and smoke-test the images locally (Docker running)
docker build -t markets-api:smoke -f infra/docker/backend.Dockerfile .
docker build -t markets-web:smoke -f infra/docker/frontend.Dockerfile .
scripts/smoke-images.sh markets-api:smoke markets-web:smoke

# Preview a deploy: prints old -> new images, applies nothing
DRY_RUN=1 AZ_RG=HK-RG-04 AZ_APP=markets \
  IMAGES="web=acrmarketshackathon.azurecr.io/markets-web:<sha> api=acrmarketshackathon.azurecr.io/markets-api:<sha>" \
  infra/azure/deploy.sh
```

**Roll back to an earlier commit:** re-run that commit's `deploy` job in GitHub Actions. Its images are still in
ACR under its SHA. Or run `deploy.sh` with those tags.

## Prerequisite and known limits

- The app must already be healthy with its configuration. A deploy cannot fix a missing `DATABASE_URL`; it fails the
  health check and rolls back.
- Single replica: the switch is near zero-downtime (the old revision serves until the new one is ready), but the
  scheduler restarts with each deploy.
- No migration job: the API creates its schema at start-up (`AUTO_CREATE_SCHEMA=true`), as documented in
  `ENVIRONMENT.md`.
- Old image tags are not pruned. Add an ACR retention task if storage grows.
