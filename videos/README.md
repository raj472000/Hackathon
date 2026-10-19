# Videos

## `debug-and-deploy.mp4` — debug locally with Docker Desktop, then deploy to the cloud

About 9 minutes, 1920×1080, narrated, with English subtitles (embedded, and in `debug-and-deploy.srt`)
and chapters (`chapters.json`).

**Part 1 — debug locally with Docker Compose** (slides 3–17): the stack, prerequisites, `docker compose up --wait`,
the Docker Desktop panels, health checks, the Data status page, logs, tracing a request id, preflight checks
inside containers, querying PostgreSQL, common start-up failures and fixes, rebuilding, breakpoints in hybrid
mode, stop/reset.

**Part 2 — deploy backend, frontend and database to the cloud** (slides 18–26): target layout, linux/amd64 image
builds, managed PostgreSQL 16 with psycopg, secrets and settings (`API_UPSTREAM`), the migration job, a Cloud Run
deployment (with Azure/AWS pointers), verification, updates and scaling. Every command is in
`analysis/RUN_AND_DEPLOY.md`.

What is real: the terminal output on the slides (`assets/terminal/`) and the screenshots (`assets/*.png`) were captured
from the Compose stack on this machine on 2026-10-15. The cloud commands follow each platform's standard approach but
**were not run against a real cloud account**.

## Rebuilding

macOS (it uses `say` for narration). Needs Node with `frontend/node_modules` installed (Playwright Chromium) and `uv`:

```bash
uv run --no-project --python 3.12 --with imageio-ffmpeg python videos/build_video.py
# optional: VOICE=Daniel RATE=170 ...
```

Slides, narration and subtitles are all defined in `build_video.py`. Intermediate files go to `videos/build/`
(git-ignored).
