# new_analysis — Markets Intelligence: run locally & deploy as containers

This folder is a fresh, self-contained analysis of the repository, focused on two practical
questions: **how to run the system on your own machine** and **how to ship it as containers to
any cloud**. It was written from the current code (October 2026) and reflects what is actually
in the repository.

| Document | What it covers |
|---|---|
| [`01-system-analysis.md`](01-system-analysis.md) | What the system is, how it is built (services, data flow, stack), what runs where, and the configuration surface |
| [`02-running-locally.md`](02-running-locally.md) | Step-by-step local run guide: prerequisites, one-command start, demo vs live mode, running pieces separately, Docker Compose on a laptop, tests, troubleshooting |
| [`03-container-cloud-deployment.md`](03-container-cloud-deployment.md) | The container story: the two images, the one change needed for cloud, then full walkthroughs for Azure Container Apps, AWS ECS Fargate, Google Cloud Run, and plain Kubernetes — plus scaling and operations |

## TL;DR — three commands to run it locally

```bash
cp .env.example .env          # DATA_MODE=demo is the default: no accounts, no internet needed
./scripts/dev.sh              # PostgreSQL (Docker) + migrations + mock MCP servers + API + web
# open http://127.0.0.1:5173
```

Requirements: [uv](https://docs.astral.sh/uv/), Node 22+, Docker Desktop (running, not paused)
for the database. Full detail in [`02-running-locally.md`](02-running-locally.md).

## TL;DR — containers

Two images are built from this repo:

| Image | Dockerfile | Runs |
|---|---|---|
| Backend (API / worker / migrations) | `infra/docker/backend.Dockerfile` | `uvicorn app.main:app` on :8000 |
| Frontend (nginx) | `infra/docker/frontend.Dockerfile` | nginx on :8080, proxies `/api` and `/health` to the API |

Locally the whole stack is `docker compose --profile demo up --build`. In the cloud the same
images run next to a **managed PostgreSQL 16** (container disks are not persistent), typically as
one unit with the web + api containers side by side, one replica, scheduler on. Full detail in
[`03-container-cloud-deployment.md`](03-container-cloud-deployment.md).

## What this system is (one paragraph)

**Markets Intelligence** is an explainable research and screening platform for NSE-listed
instruments: a screener, rule-based themes with immutable monthly snapshots, a sector-rotation
(RRG) graph, an evidence-grounded research board, an entry-setup checklist, a conference-call
follow-through analyzer, watchlists and alerts. Market data arrives over **MCP** (Model Context
Protocol): NSE MCP as the primary source, Zerodha Kite MCP as gap-filler and cross-check. All
arithmetic is deterministic and versioned; the optional LLM (GLM through Hugging Face) only
writes summaries from supplied evidence. It is a research tool — it cannot place orders and
nothing it shows is investment advice.

## Pointers to the older analysis

The repository also has earlier documentation in `analysis/` (`HOW_TO_RUN.md`,
`RUN_AND_DEPLOY.md`, `ARCHITECTURE.md`, `REPOSITORY_GUIDE.md`) and `docs/`
(`architecture.md`, `methodology.md`, `data-formats.md`, `operations.md`). This folder is a
consolidated rewrite of the run/deploy parts; those files remain the deeper reference on
methodology and data formats.
