"""Build the narrated video "Debug locally with Docker Desktop, then deploy to the cloud".

Pipeline: slide definitions below -> build/slides.html -> PNG per slide (Playwright, from the
frontend's dev dependencies) -> narration per slide (macOS `say`) -> one MP4 with H.264 video,
AAC audio, soft English subtitles and chapter markers, plus a separate .srt file.

Run from the repository root (macOS; needs Node with `frontend/node_modules` installed):

    uv run --no-project --python 3.12 --with imageio-ffmpeg python videos/build_video.py

Terminal output on the slides is real output captured from the Compose stack on 2026-10-15
(videos/assets/terminal/*.txt). Screenshots are real (videos/assets/*.png).
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VID = ROOT / "videos"
ASSETS = VID / "assets"
BUILD = VID / "build"
OUT_MP4 = VID / "debug-and-deploy.mp4"
OUT_SRT = VID / "debug-and-deploy.srt"
VOICE = os.environ.get("VOICE", "Samantha")
RATE = os.environ.get("RATE", "178")  # words per minute for `say`
PAD = 0.7  # seconds of silence after each slide's narration


def term(name: str, max_lines: int = 18) -> str:
    """Real captured terminal output, trimmed for a slide."""
    text = (ASSETS / "terminal" / f"{name}.txt").read_text().rstrip("\n").splitlines()
    return "\n".join(line[:150] for line in text[:max_lines])


def code(text: str, title: str = "") -> str:
    head = f'<div class="code-title">{html.escape(title)}</div>' if title else ""
    return f'<div class="code">{head}<pre>{html.escape(text)}</pre></div>'


def bullets(*items: str) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def table(rows: list[tuple[str, ...]], head: tuple[str, ...]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def img(name: str, caption: str) -> str:
    return f'<figure><img src="../assets/{name}" alt=""><figcaption>{html.escape(caption)}</figcaption></figure>'


# ----------------------------------------------------------------------------------------------
# Slides: (part, title, body_html, narration). Narration uses spoken spellings where `say`
# would mispronounce a term (engine X for nginx, psycho P G for psycopg, M C P, N S E).
# ----------------------------------------------------------------------------------------------
COMPOSE_DIAGRAM = """<div class="diagram">
<div class="box web">web<br><small>nginx · :8080</small></div>
<div class="arrow">/api · /health →</div>
<div class="box api">api<br><small>FastAPI · :8000</small></div>
<div class="arrow">→</div>
<div class="stack"><div class="box db">postgres<br><small>16 · psycopg · :5432</small></div>
<div class="box db">redis<br><small>7 · cache + locks · :6379</small></div></div>
</div>
<div class="diagram second">
<div class="box side">migrate<br><small>alembic upgrade head, then exits</small></div>
<div class="box side">worker<br><small>runs the schedule</small></div>
<div class="box side">mock-mcp<br><small>demo profile only</small></div>
<div class="box ext">NSE MCP · Kite MCP<br><small>live mode, over the internet</small></div>
</div>"""

CLOUD_DIAGRAM = """<div class="diagram">
<div class="box ext">Internet<br><small>HTTPS</small></div>
<div class="arrow">→</div>
<div class="unit"><div class="unit-label">one unit · 1 replica</div>
<div class="box web">web<br><small>nginx :8080</small></div>
<div class="arrow small">127.0.0.1:8000 →</div>
<div class="box api">api<br><small>FastAPI :8000 + scheduler</small></div></div>
<div class="arrow">→</div>
<div class="box db">Managed PostgreSQL 16<br><small>sslmode=require</small></div>
</div>
<div class="diagram second">
<div class="box side">migration job<br><small>alembic upgrade head, before each version</small></div>
<div class="box side">secret store<br><small>DATABASE_URL · API_AUTH_TOKEN · LLM_API_KEY</small></div>
<div class="box side">container registry<br><small>markets-api · markets-web (linux/amd64)</small></div>
</div>"""

SLIDES: list[tuple[str, str, str, str]] = [
    ("title", "Debug Markets Intelligence locally with Docker Desktop, then deploy it to the cloud",
     '<p class="lede">Docker Compose on your Mac: start, inspect, trace and fix.<br>'
     "Then: backend, frontend and database in the cloud.</p>",
     "This video shows how to debug the Markets Intelligence repository on your own Mac with Docker "
     "Desktop and Docker Compose, and then how to deploy the backend, the frontend and the database "
     "to the cloud. Everything shown on screen was captured from the real stack running on this machine."),

    ("Overview", "What this video covers",
     '<div class="cols"><div><h3>Part 1 · Debug locally</h3>' + bullets(
         "What runs in the Compose stack", "Start it and check health", "Read logs and trace a request",
         "Look inside containers and the database", "Fix the common start-up failures",
         "Change code, rebuild, and use breakpoints") +
     '</div><div><h3>Part 2 · Deploy to the cloud</h3>' + bullets(
         "Target layout", "Build and push images", "Create the managed database",
         "Secrets and settings", "Run the migration", "Deploy, verify, update, scale") + "</div></div>",
     "In part one we start the Compose stack, check its health, read logs, trace one request by its id, "
     "look inside the containers and the database, and fix the failures you are most likely to meet. "
     "In part two we deploy the same images to the cloud with a managed Postgres database."),

    ("Part 1 · Debug locally", "The stack in Docker Compose",
     COMPOSE_DIAGRAM + '<p class="note">Defined in <code>docker-compose.yml</code>. Data lives in the '
     "<code>pgdata</code> Docker volume.</p>",
     "Here is the stack. The web container is engine X on port eighty eighty: it serves the built React "
     "app and forwards slash A P I and slash health to the A P I container on port eight thousand. The A P I "
     "talks to Postgres through psycho P G, and to Redis for caching and job locks. The migrate container "
     "applies the database migrations and exits. The worker runs the scheduled jobs. In live mode, market "
     "data comes from the N S E and Kite M C P servers; the mock M C P container is only for demo mode."),

    ("Part 1 · Debug locally", "Before you start",
     bullets(
         "Docker Desktop is <b>running and not paused</b> (Whale menu → Resume)",
         "<code>cp .env.example .env</code> — keep comments on their own lines",
         "Free ports: <b>8080</b> web, <b>8000</b> API, <b>5432</b> PostgreSQL, <b>6379</b> Redis",
         "<code>DATA_MODE=demo</code> for synthetic data, <code>live</code> for the real M C P servers",
         "Database password: <code>POSTGRES_PASSWORD</code> = the one in <code>DATABASE_URL</code> "
         "(default <code>change-me-locally</code>)"),
     "Before you start, make sure Docker Desktop is running and not paused. A paused Docker cannot start "
     "containers, and it keeps old port mappings reserved. Create your dot E N V file from dot E N V dot example, "
     "and keep comments on their own lines, because Compose passes a trailing comment through as part of the "
     "value. Choose demo mode for synthetic data, or live mode for the real servers."),

    ("Part 1 · Debug locally", "Start the stack and see what is running",
     code("docker compose --profile demo up -d --build --wait   # demo: adds the mock MCP servers\n"
          "docker compose up -d --build --wait                  # live mode", "Start") +
     code("$ docker compose ps\n" + term("ps"), "Real output"),
     "Start everything with docker compose up, dash D, dash dash build, dash dash wait. Add the demo profile in "
     "demo mode. The wait flag returns only when every health check passes, or when a container fails. Then "
     "run docker compose P S. Here every long-running service is up and healthy, and the ports are published "
     "only on one twenty seven dot zero dot zero dot one, so nothing is exposed to your network."),

    ("Part 1 · Debug locally", "In Docker Desktop",
     table([("Containers → <b>hackathon</b>", "Every service, its state, ports, and a start/stop button"),
            ("A container → <b>Logs</b>", "The same JSON logs as <code>docker compose logs</code>, searchable"),
            ("A container → <b>Exec</b>", "A shell inside it, like <code>docker compose exec api sh</code>"),
            ("A container → <b>Inspect</b>", "Environment variables, mounts, health-check results"),
            ("A container → <b>Stats</b>", "CPU and memory, useful during a big refresh"),
            ("Volumes → <b>hackathon_pgdata</b>", "The database files. Deleting it deletes all data")],
           ("Where", "What it shows")),
     "Docker Desktop shows the same things graphically. Under Containers, open the hackathon group to see each "
     "service. For any container, the Logs tab streams its logs, Exec opens a shell inside it, Inspect shows "
     "its environment and health-check results, and Stats shows C P U and memory. Under Volumes, the pgdata "
     "volume holds the database files; deleting it deletes all data."),

    ("Part 1 · Debug locally", "Step 1: check health",
     code("$ curl -s http://127.0.0.1:8080/health/ready | python3 -m json.tool\n" + term("ready"), "Real output") +
     bullets("<b>database</b>, <b>cache</b>: must be ok, or the API reports not ready (HTTP 503)",
             "<b>sources</b>: reported but not fatal — stored data is still served when N S E is down"),
     "When something looks wrong, check health first. Slash health slash ready through engine X on port eighty "
     "eighty tests the whole path. Database and cache must be ok. The market data sources are reported here too, "
     "but they never make the A P I unready, because stored data can still be served when N S E is down."),

    ("Part 1 · Debug locally", "Step 2: the Data status page",
     img("data-status.png", "http://127.0.0.1:8080/status — real screenshot, live mode"),
     "Next, open the Data status page. It shows each connector's state, the tools it advertises, latency, error "
     "rate, and its last error. In this real capture, the N S E bhavcopy server had a thirty percent error rate "
     "from read timeouts that day, and the Kite server shows seven allowed tools and fifteen blocked trading "
     "tools. Below it you find stored data counts, the broker login, and the job history."),

    ("Part 1 · Debug locally", "Step 3: read the logs",
     code("docker compose logs -f api            # follow the API\n"
          "docker compose logs --tail 100 worker  # scheduled jobs\n"
          "docker compose logs migrate           # did the migration run?", "Commands") +
     code(term("logs"), "Real output: one JSON line per request"),
     "Logs are the next stop. Follow the A P I with docker compose logs dash F A P I. The worker logs every "
     "scheduled job, and the migrate container tells you whether the schema was applied. Each request is one "
     "JSON line with its method, route, status, duration and a request id."),

    ("Part 1 · Debug locally", "Step 4: trace one failing request by its id",
     code("$ curl -s http://127.0.0.1:8080/api/v1/instruments/NSE:NOSUCH\n" + term("err"), "The error the user sees") +
     code('$ docker compose logs api | grep 477af77673804bac\n' + term("grep"), "The matching log line"),
     "Every error the app returns carries a request id, and the web app shows it next to the message. Copy that "
     "id and grep the A P I logs for it. Here a lookup of a symbol that does not exist returned a four oh four "
     "with request id four seven seven A F, and the matching log line shows the route and timing."),

    ("Part 1 · Debug locally", "Step 5: look inside the containers",
     code("$ docker compose exec api python -m app.devtools.preflight show   # then: db, redis\n" + term("preflight"),
          "Real output") +
     code("docker compose exec api sh                 # a shell in the API container\n"
          "docker compose exec api env | sort         # the settings it actually received", "More"),
     "To see what a container really received, run the pre-flight checks inside it. Show prints the effective "
     "settings with the password hidden, D B connects to Postgres, and Redis pings the cache. Inside Compose, the "
     "A P I uses the postgres and redis service names. You can also open a shell, or list the environment."),

    ("Part 1 · Debug locally", "Step 6: query the database",
     code('$ docker compose exec postgres psql -U markets -d markets \\\n'
          '    -c "select id, kind, status, trigger, error from job_runs order by id desc limit 5;"\n'
          + term("psql"), "Real output"),
     "For data questions, open sequel inside the postgres container with docker compose exec postgres P S Q L. "
     "The job runs table shows every refresh job with its status and error, and alembic version shows which "
     "migration the database is at. From your Mac, any database tool can connect to port five four three two."),

    ("Part 1 · Debug locally", "Common failures and their fixes (1/2)",
     table([("Port 8000 / 8080 already in use", "An old Compose stack holds them, even when Docker is paused",
             "<code>docker compose down</code>, or <code>API_PORT=8001 ./scripts/dev.sh</code>"),
            ("<code>Docker Desktop is manually paused</code>", "Docker was paused", "Whale menu → Resume"),
            ('<code>password authentication failed for user "markets"</code>',
             "The volume keeps the password it was created with", "Use the original password, or "
             "<code>ALTER USER</code> in psql; <code>down -v</code> deletes data"),
            ("<code>migrate</code> exited 1, API never starts", "Migration error",
             "<code>docker compose logs migrate</code>")],
           ("Symptom", "Cause", "Fix")),
     "These are the failures we actually hit on this machine. If port eight thousand is taken, an earlier Compose "
     "stack is holding it, even while Docker is paused; stop it with docker compose down. If the migrate container "
     "fails with password authentication failed, the database volume still has the password it was created with, "
     "because Postgres applies the password only once. Put the original password back, or change it in P S Q L."),

    ("Part 1 · Debug locally", "Common failures and their fixes (2/2)",
     table([("<code>has no tables: no migration has been applied</code>", "Schema missing",
             "<code>python -m app.devtools.preflight migrate</code>"),
            ("<code>relation … already exists</code> during migration", "Tables created without a migration record",
             "<code>preflight migrate</code> records and continues"),
            ("Settings wrong only in Docker", "<code>KEY=value  # note</code> in .env", "Comments on their own lines"),
            ("N S E shows unavailable, 502 / 504", "Upstream outage", "Wait; stored data is served; refresh later"),
            ("Broker shows login required", "Kite needs your approval", "Data status → Check broker session")],
           ("Symptom", "Cause", "Fix")),
     "The A P I refuses to start on a database without the current schema, and tells you to run preflight migrate. "
     "That command also repairs a database whose tables were created without a migration record. N S E outages "
     "appear as unavailable with the reason, never as invented data. And the Kite broker needs your approval "
     "through the link on the Data status page."),

    ("Part 1 · Debug locally", "Change code and rebuild",
     code("docker compose up -d --build api worker   # backend change\n"
          "docker compose up -d --build web          # frontend change\n"
          "docker compose run --rm migrate           # after a new migration\n"
          "docker compose logs -f api                # watch it come back", "Rebuild only what changed") +
     bullets("Images are rebuilt from the repository; <code>.dockerignore</code> keeps host <code>.venv</code> and "
             "<code>node_modules</code> out", "The database volume is kept across rebuilds"),
     "After a code change, rebuild only what changed: A P I and worker for backend code, web for frontend code. "
     "Run the migrate service again after adding a migration. The database volume is kept, so you do not lose data."),

    ("Part 1 · Debug locally", "Breakpoints: run the API on your Mac against the Compose database",
     code("docker compose stop api worker web     # keep postgres and redis running\n"
          "./scripts/dev.sh                       # API :8000 + web :5173 on your Mac, same database", "Hybrid mode") +
     code('{ "type": "debugpy", "request": "launch", "name": "API",\n'
          '  "module": "uvicorn", "args": ["app.main:app", "--port", "8000"],\n'
          '  "cwd": "${workspaceFolder}/backend", "envFile": "${workspaceFolder}/.env",\n'
          '  "python": "${workspaceFolder}/backend/.venv/bin/python" }', "VS Code launch configuration (standard; adapt as needed)"),
     "For step-through debugging, keep Postgres and Redis in Docker and run the A P I on your Mac. Stop the A P I, "
     "worker and web containers, then run scripts slash dev dot S H, which finds the Compose database, applies "
     "migrations, and starts the A P I and the web app. Or launch the A P I under your editor's debugger with a "
     "configuration like this one, and set breakpoints anywhere in the backend."),

    ("Part 1 · Debug locally", "Stop and reset",
     code("docker compose stop                 # stop, keep containers and data\n"
          "docker compose down                 # remove containers, keep the database volume\n"
          "docker compose down -v              # remove containers AND the database volume\n"
          "docker compose exec -T postgres pg_dump -U markets -Fc markets > backup.dump   # back up first",
          "Commands"),
     "Stop keeps everything. Down removes the containers but keeps the database volume. Down dash V also deletes "
     "the volume and all data, so take a backup with P G dump first if you might need it."),

    ("title", "Part 2: deploy the backend, frontend and database to the cloud",
     '<p class="lede">The same two images, one managed PostgreSQL, one migration job.<br>'
     "Full commands for Azure, AWS and Google Cloud: <code>analysis/RUN_AND_DEPLOY.md</code></p>",
     "Part two. We deploy the same two images, the A P I and the web app, next to a managed Postgres database. "
     "The complete commands for Azure, A W S and Google Cloud are in the R U N and deploy guide in the analysis folder."),

    ("Part 2 · Deploy", "Target layout",
     CLOUD_DIAGRAM,
     "The recommended first layout is one unit with two containers side by side: engine X on port eighty eighty "
     "takes the public traffic and forwards A P I calls over one twenty seven dot zero dot zero dot one to the A P I "
     "on port eight thousand. One replica runs the scheduler itself, so Redis is not needed yet. The database is "
     "a managed Postgres, a migration job runs before each new version, and secrets live in the cloud's secret store."),

    ("Part 2 · Deploy", "Step 1: build images for linux/amd64 and push them",
     code("docker buildx build --platform linux/amd64 -f infra/docker/backend.Dockerfile  \\\n"
          "  -t <registry>/markets-api:v1 --push .\n"
          "docker buildx build --platform linux/amd64 -f infra/docker/frontend.Dockerfile \\\n"
          "  -t <registry>/markets-web:v1 --push .", "From the repository root") +
     bullets("Apple Silicon builds <b>arm64</b> by default; most cloud services need <b>amd64</b>",
             "Registry: Azure Container Registry, Amazon ECR, or Google Artifact Registry"),
     "Step one. Build both images for linux A M D sixty four and push them to your cloud's container registry. "
     "This matters on an Apple Silicon Mac, which builds A R M images by default, and most cloud container "
     "services will refuse those."),

    ("Part 2 · Deploy", "Step 2: create the managed PostgreSQL 16",
     code("# Google Cloud SQL\n"
          "gcloud sql instances create markets-pg --database-version=POSTGRES_16 --region=asia-south1 --tier=db-f1-micro\n"
          "gcloud sql databases create markets --instance=markets-pg\n"
          "gcloud sql users create markets --instance=markets-pg --password=\"$PGPASS\"", "Example") +
     code("postgresql+psycopg://markets:<password>@<host>:5432/markets?sslmode=require\n"
          "postgresql+psycopg://markets:<password>@/markets?host=/cloudsql/<connection-name>   # Cloud Run socket",
          "DATABASE_URL (psycopg 3)"),
     "Step two. Create a managed Postgres sixteen database. This example is Google Cloud S Q L; Azure Database "
     "for Postgres and Amazon R D S work the same way. The connection string uses the psycho P G driver, with S S L "
     "mode require for a network connection, or the Cloud S Q L socket path on Cloud Run."),

    ("Part 2 · Deploy", "Step 3: secrets and settings",
     table([("<code>DATABASE_URL</code>", "the psycopg URL from step 2", "secret"),
            ("<code>API_AUTH_TOKEN</code>", "<code>openssl rand -hex 32</code>", "secret"),
            ("<code>LLM_API_KEY</code>", "only if you use the model", "secret"),
            ("<code>APP_ENV</code> / <code>DATA_MODE</code>", "<code>production</code> / <code>live</code>", ""),
            ("<code>AUTO_CREATE_SCHEMA</code>", "<code>false</code> — the migration job owns the schema", ""),
            ("<code>SCHEDULER_ENABLED</code>", "<code>true</code> with one replica", ""),
            ("<code>CORS_ORIGINS</code>", "the public URL of the app", ""),
            ("<code>API_UPSTREAM</code> (web container)", "<code>127.0.0.1:8000</code>", "")],
           ("Setting", "Value", "")),
     "Step three. Put the database URL, an A P I token and any model token in the secret store. Set app env to "
     "production and data mode to live. Leave auto create schema false, because the migration job owns the schema. "
     "And on the web container, set A P I upstream to one twenty seven dot zero dot zero dot one colon eight "
     "thousand, so engine X forwards to the A P I next to it. In Compose it defaults to A P I colon eight thousand."),

    ("Part 2 · Deploy", "Step 4: run the migration job",
     code("# Google Cloud Run job\n"
          "gcloud run jobs create markets-migrate --region=$REGION --image=$IMG/markets-api:v1 \\\n"
          "  --command=alembic --args=upgrade,head \\\n"
          "  --set-cloudsql-instances=$CONN --set-secrets=DATABASE_URL=markets-database-url:latest\n"
          "gcloud run jobs execute markets-migrate --region=$REGION --wait", "Example") +
     bullets("Azure: a Container Apps job with the same command", "AWS: <code>aws ecs run-task</code> with the command overridden",
             "Run it before every new version"),
     "Step four. Run the migration once, as a one-off job with the A P I image and the command alembic upgrade head. "
     "On Cloud Run that is a job, on Azure a Container Apps job, on A W S a one-off E C S task. Run it again before "
     "every new version."),

    ("Part 2 · Deploy", "Step 5: deploy web + api as one unit",
     code("gcloud run deploy markets --region=$REGION --allow-unauthenticated \\\n"
          "  --min-instances=1 --max-instances=1 --no-cpu-throttling --add-cloudsql-instances=$CONN \\\n"
          "  --container=web --image=$IMG/markets-web:v1 --port=8080 \\\n"
          "    --set-env-vars=API_UPSTREAM=127.0.0.1:8000 --depends-on=api \\\n"
          "  --container=api --image=$IMG/markets-api:v1 --cpu=1 --memory=2Gi \\\n"
          "    --set-env-vars=APP_ENV=production,DATA_MODE=live,AUTO_CREATE_SCHEMA=false,SCHEDULER_ENABLED=true \\\n"
          "    --set-secrets=DATABASE_URL=markets-database-url:latest,API_AUTH_TOKEN=markets-api-token:latest",
          "Google Cloud Run (shortest path)") +
     bullets("Azure Container Apps: a two-container app from a YAML file",
             "AWS ECS Fargate: a two-container task behind an Application Load Balancer on port 8080"),
     "Step five. Deploy the two containers together. On Cloud Run, one command defines the web container as the "
     "public entry on port eighty eighty and the A P I as a sidecar. Exactly one instance, and no C P U throttling, "
     "so the twelve hour and monthly jobs keep running. Azure uses a two container app defined in YAML, and A W S "
     "a two container task behind a load balancer."),

    ("Part 2 · Deploy", "Step 6: verify, then finish the setup",
     code("curl https://<app-url>/health/ready                # database ok, cache ok\n"
          "gcloud run services update markets --region=$REGION --container=api \\\n"
          "  --update-env-vars=CORS_ORIGINS=https://<app-url>\n"
          "gcloud beta run services logs tail markets --region=$REGION", "Verify") +
     bullets("Open the app → Data status → enter the API token under Access",
             "Check broker session → approve Kite → Refresh all data",
             "Upload financial statements if you have them"),
     "Step six. Check slash health slash ready on the public U R L, then set cores origins to that U R L. Open the "
     "app, enter the A P I token under Access on the Data status page, approve the Kite broker session, and run "
     "refresh all data. Follow the logs with your cloud's log tail command while the first refresh runs."),

    ("Part 2 · Deploy", "Updates and scaling",
     '<div class="cols"><div><h3>Ship a new version</h3>' + bullets(
         "Build and push images with a new tag (<code>:v2</code>)", "Update and run the migration job",
         "Deploy the new tags") +
     '</div><div><h3>Grow beyond one replica</h3>' + bullets(
         "Add managed Redis, set <code>REDIS_URL</code>", "<code>SCHEDULER_ENABLED=false</code> on web + api, raise replicas",
         "One always-on worker: <code>python -m app.workers.run</code>") + "</div></div>",
     "To ship a new version: push images with a new tag, run the migration job with the new image, then deploy "
     "the new tags. To grow beyond one replica, add a managed Redis, turn the scheduler off on the web and A P I "
     "unit, raise its replicas, and run one always-on worker that runs the schedule."),

    ("Wrap-up", "What was verified, and where to read more",
     table([("Docker Compose stack on Docker Desktop", "Verified on this machine: build, migrate, all services healthy"),
            ("Web container API address (<code>API_UPSTREAM</code>)", "Verified: Compose default and cloud override"),
            ("Cloud commands (Azure, AWS, Google Cloud)", "<b>Not run</b> against a real cloud account — "
             "treat the first deployment as a trial")],
           ("Item", "Status")) +
     table([("<code>analysis/HOW_TO_RUN.md</code>", "Local running, Docker, troubleshooting"),
            ("<code>analysis/RUN_AND_DEPLOY.md</code>", "Full Azure, AWS and Google Cloud commands"),
            ("<code>analysis/ARCHITECTURE.md</code>", "How the system is built")],
           ("Document", "Covers")),
     "To be clear about what was tested: the Docker Compose stack and the web container's A P I address were "
     "verified on this machine. The cloud commands follow each platform's standard approach but were not run "
     "against a real cloud account, so treat your first deployment as a trial. The how to run guide, the R U N and "
     "deploy guide and the architecture document in the analysis folder have every detail. Thanks for watching."),
]


# ----------------------------------------------------------------------------------------------
CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root { --ink:#14213d; --ink2:#5b6780; --ground:#edf0f4; --surface:#fff; --rule:#d6dce6; --accent:#e08a1e;
        --accent-soft:#fdf1df; --glyph:#2a4ba0; --codebg:#101a33; --codeink:#e6ebf5; }
* { box-sizing: border-box; margin: 0; }
body { background: #000; font-family: 'IBM Plex Sans', system-ui, sans-serif; }
.slide { width: 1920px; height: 1080px; background: var(--ground); color: var(--ink); position: relative;
         padding: 72px 96px 110px; display: flex; flex-direction: column; overflow: hidden; }
.slide.title { background: var(--ink); color: #fff; justify-content: center; }
.part { font-size: 24px; color: var(--ink2); margin-bottom: 14px; font-weight: 500; }
.slide.title .part { color: var(--accent); }
h1 { font-family: 'Bricolage Grotesque', sans-serif; font-size: 58px; line-height: 1.08; letter-spacing: -0.01em; margin-bottom: 34px; max-width: 1650px; }
.slide.title h1 { font-size: 78px; max-width: 1500px; }
.lede { font-size: 34px; line-height: 1.45; color: #c8d1e3; max-width: 1400px; }
.lede code { color: var(--accent); background: none; }
h3 { font-family: 'Bricolage Grotesque', sans-serif; font-size: 34px; margin-bottom: 16px; }
.body { flex: 1; display: flex; flex-direction: column; gap: 26px; min-height: 0; }
ul { padding-left: 34px; font-size: 31px; line-height: 1.5; }
li { margin: 6px 0; } li::marker { color: var(--accent); }
code { font-family: 'IBM Plex Mono', monospace; font-size: 0.86em; background: #dfe4ec; padding: 1px 8px; border-radius: 5px; }
.code { background: var(--codebg); color: var(--codeink); border-radius: 14px; padding: 20px 28px 22px; }
.code-title { font-size: 19px; color: #f0a23c; margin-bottom: 8px; font-weight: 600; }
.code pre { font-family: 'IBM Plex Mono', monospace; font-size: 22px; line-height: 1.42; white-space: pre-wrap; word-break: break-word; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 70px; }
table { border-collapse: collapse; width: 100%; font-size: 26px; background: var(--surface); border-radius: 12px; overflow: hidden; }
th { text-align: left; font-weight: 600; color: var(--ink2); font-size: 21px; padding: 14px 20px; background: #f5f7fa; border-bottom: 2px solid var(--rule); }
td { padding: 14px 20px; border-bottom: 1px solid var(--rule); vertical-align: top; line-height: 1.32; }
td code { font-size: 0.82em; }
figure { flex: 1; display: flex; flex-direction: column; align-items: center; min-height: 0; }
figure img { max-height: 790px; max-width: 100%; border-radius: 12px; border: 1px solid var(--rule); box-shadow: 0 10px 30px rgba(20,33,61,.15); }
figcaption { font-size: 21px; color: var(--ink2); margin-top: 12px; }
.note { font-size: 26px; color: var(--ink2); }
.diagram { display: flex; align-items: center; gap: 22px; justify-content: center; }
.diagram.second { margin-top: 34px; gap: 26px; }
.box { border-radius: 16px; padding: 22px 30px; font-size: 32px; font-weight: 600; text-align: center; border: 3px solid; background: #fff; min-width: 240px; }
.box small { display: block; font-size: 21px; font-weight: 400; color: var(--ink2); margin-top: 6px; }
.box.web { border-color: var(--accent); background: var(--accent-soft); }
.box.api { border-color: var(--glyph); background: #e8eefb; }
.box.db { border-color: #17734a; background: #e3f3ea; }
.box.side { border-color: var(--rule); font-size: 26px; }
.box.ext { border-color: var(--ink2); border-style: dashed; font-size: 26px; }
.stack { display: flex; flex-direction: column; gap: 18px; }
.arrow { font-size: 26px; color: var(--ink2); font-weight: 600; white-space: nowrap; }
.arrow.small { font-size: 21px; }
.unit { border: 3px dashed var(--accent); border-radius: 22px; padding: 44px 26px 26px; display: flex; gap: 18px; align-items: center; position: relative; }
.unit-label { position: absolute; top: 10px; left: 24px; font-size: 20px; color: #a35f00; font-weight: 600; }
.footer { position: absolute; left: 96px; right: 96px; bottom: 38px; display: flex; justify-content: space-between; font-size: 20px; color: var(--ink2); }
.slide.title .footer { color: #8b97b3; }
.brand { display: flex; align-items: center; gap: 12px; font-weight: 600; }
.brand i { display: inline-block; width: 26px; height: 26px; border-radius: 6px; background: var(--accent); }
"""


def build_html() -> Path:
    parts = []
    total = len(SLIDES)
    for n, (part, title, body, _narr) in enumerate(SLIDES, start=1):
        is_title = part == "title"
        label = ("Part 2" if "Part 2" in title else "Markets Intelligence") if is_title else part
        parts.append(
            f'<section class="slide{" title" if is_title else ""}" id="s{n:02d}">'
            f'<div class="part">{html.escape(label)}</div><h1>{html.escape(title)}</h1>'
            f'<div class="body">{body}</div>'
            f'<div class="footer"><span class="brand"><i></i>Markets Intelligence · debug and deploy</span>'
            f"<span>{n} / {total}</span></div></section>"
        )
    BUILD.mkdir(parents=True, exist_ok=True)
    page = BUILD / "slides.html"
    page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>")
    return page


RENDER_JS = r"""
const { createRequire } = require('module');
const req = createRequire(process.argv[2] + '/frontend/package.json');
const { chromium } = req('@playwright/test');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + process.argv[3], { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const ids = await page.$$eval('section.slide', els => els.map(e => e.id));
  for (const id of ids) {
    await page.locator('#' + id).screenshot({ path: process.argv[4] + '/' + id + '.png' });
  }
  // Fail loudly if any slide overflows its frame.
  const overflow = await page.$$eval('section.slide', els => els.filter(e => e.scrollHeight > e.clientHeight + 2).map(e => e.id));
  await browser.close();
  if (overflow.length) { console.error('OVERFLOW ' + overflow.join(',')); process.exit(3); }
  console.log('rendered ' + ids.length);
})();
"""


def ffmpeg() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        found = shutil.which("ffmpeg")
        if not found:
            sys.exit("ffmpeg not found: run with `uv run --with imageio-ffmpeg` or install ffmpeg")
        return found


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:4])} ...\n{r.stderr[-2000:]}")
    return r


def duration(path: Path) -> float:
    out = run(["afinfo", str(path)]).stdout
    m = re.search(r"estimated duration:\s*([\d.]+)", out)
    if not m:
        sys.exit(f"cannot read duration of {path}")
    return float(m.group(1))


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_text(narration: str) -> str:
    """Undo the spoken spellings for the written subtitles."""
    t = narration
    for spoken, written in [("engine X", "nginx"), ("psycho P G", "psycopg"), ("M C P", "MCP"), ("N S E", "NSE"),
                            ("A P I", "API"), ("dot E N V dot example", ".env.example"), ("dot E N V", ".env"),
                            ("P S Q L", "psql"), ("P G dump", "pg_dump"), ("one twenty seven dot zero dot zero dot one", "127.0.0.1"),
                            ("eighty eighty", "8080"), ("eight thousand", "8000"), ("A M D sixty four", "amd64"),
                            ("A R M", "ARM"), ("S S L", "SSL"), ("Cloud S Q L", "Cloud SQL"), ("A W S", "AWS"),
                            ("E C S", "ECS"), ("R D S", "RDS"), ("C P U", "CPU"), ("U R L", "URL"), ("cores origins", "CORS_ORIGINS"),
                            ("R U N and deploy", "RUN_AND_DEPLOY"), ("scripts slash dev dot S H", "scripts/dev.sh"),
                            ("slash A P I", "/api"), ("slash health slash ready", "/health/ready"), ("slash health", "/health"),
                            ("dash F", "-f"), ("dash D", "-d"), ("dash dash build", "--build"), ("dash dash wait", "--wait"),
                            ("docker compose P S", "docker compose ps"), ("down dash V", "down -v"), ("D B", "db"),
                            ("sequel", "SQL"), ("four oh four", "404"), ("four seven seven A F", "477af…"),
                            ("five four three two", "5432"), ("thirty percent", "30%")]:
        t = t.replace(spoken, written)
    return t.replace("/api /api", "/api")


def chunks(text: str, limit: int = 84) -> list[str]:
    """Split a caption into subtitle lines of at most ~2 x 42 characters, at sentence/clause breaks."""
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > limit and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if cur.endswith((".", "?", "!")) and len(cur) > limit * 0.55:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def main() -> None:
    page = build_html()
    frames = BUILD / "frames"
    audio = BUILD / "audio"
    clips = BUILD / "clips"
    for d in (frames, audio, clips):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    js = BUILD / "render.cjs"
    js.write_text(RENDER_JS)
    print(run(["node", str(js), str(ROOT), str(page), str(frames)]).stdout.strip())

    ff = ffmpeg()
    t0, srt, chapters, n_cap = 0.0, [], [], 1
    for n, (part, title, _body, narration) in enumerate(SLIDES, start=1):
        a = audio / f"s{n:02d}.aiff"
        run(["say", "-v", VOICE, "-r", RATE, "-o", str(a), narration])
        d = duration(a) + PAD
        clip = clips / f"s{n:02d}.mp4"
        run([ff, "-y", "-loop", "1", "-framerate", "30", "-i", str(frames / f"s{n:02d}.png"), "-i", str(a),
             "-filter_complex", f"[1:a]apad=whole_dur={d:.3f}[a]", "-map", "0:v", "-map", "[a]",
             "-c:v", "libx264", "-tune", "stillimage", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-t", f"{d:.3f}", str(clip)])
        # subtitles: spread the slide's caption chunks over its narration, in proportion to length
        caps = chunks(caption_text(narration))
        speak = d - PAD
        total_chars = sum(len(c) for c in caps)
        t = t0
        for c in caps:
            dt = speak * len(c) / total_chars
            srt.append(f"{n_cap}\n{srt_time(t)} --> {srt_time(t + dt)}\n{c}\n")
            n_cap += 1
            t += dt
        chapters.append((t0, t0 + d, f"{n}. {title}"))
        print(f"slide {n:02d}  {d:5.1f}s  {title[:60]}")
        t0 += d

    lst = BUILD / "clips.txt"
    lst.write_text("".join(f"file '{clips / f's{n:02d}.mp4'}'\n" for n in range(1, len(SLIDES) + 1)))
    joined = BUILD / "joined.mp4"
    run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    OUT_SRT.write_text("\n".join(srt))
    meta = BUILD / "chapters.txt"
    meta.write_text(";FFMETADATA1\ntitle=Markets Intelligence: debug locally with Docker Desktop, deploy to the cloud\n"
                    + "".join(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(a * 1000)}\nEND={int(b * 1000)}\ntitle={t}\n"
                              for a, b, t in chapters))
    run([ff, "-y", "-i", str(joined), "-i", str(OUT_SRT), "-i", str(meta), "-map", "0", "-map", "1",
         "-map_metadata", "2", "-map_chapters", "2", "-c", "copy", "-c:s", "mov_text",
         "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", str(OUT_MP4)])
    (VID / "chapters.json").write_text(json.dumps([{"start_seconds": round(a, 1), "title": t} for a, _b, t in chapters], indent=1))
    print(f"\nwrote {OUT_MP4.relative_to(ROOT)}  {t0 / 60:.1f} min  {OUT_MP4.stat().st_size / 1e6:.1f} MB")
    print(f"wrote {OUT_SRT.relative_to(ROOT)}, videos/chapters.json")


if __name__ == "__main__":
    main()
