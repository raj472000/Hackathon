#!/usr/bin/env bash
# Start the whole stack for local development:
#   1. PostgreSQL (and Redis, if REDIS_URL is set) — started with Docker Compose when they are local
#      and not already running.
#   2. Database migrations (alembic upgrade head).
#   3. Demo mode only: the mock MCP servers (synthetic data).
#   4. The API (port 8000) and the web app (port 5173).
# Ctrl+C stops the API, web app and mock servers. PostgreSQL/Redis containers keep running
# (stop them with `docker compose stop postgres redis`).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
export PATH="$HOME/.docker/bin:$PATH"
pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
say() { printf '[dev] %s\n' "$*"; }
fail() { printf '[dev] ERROR: %s\n' "$*" >&2; exit 1; }

port_owner() { { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; } | awk 'NR==2 {print $1 " (pid " $2 ")"}'; }
check_port() {  # $1 = port, $2 = what needs it, $3 = how to change it
  local owner; owner="$(port_owner "$1")"
  [ -z "$owner" ] && return 0
  case "$owner" in
    com.docke*|docker*) fail "port $1 (needed for $2) is held by Docker: a Compose container publishes it.
       Stop the Compose stack with \`docker compose down\` (or \`docker compose stop api web\`), or $3." ;;
    *) fail "port $1 (needed for $2) is already in use by $owner. Stop that process, or $3." ;;
  esac
}

cd "$ROOT"
[ -f .env ] || { cp .env.example .env; say "created .env from .env.example"; }
check_port "$API_PORT" "the API" "run with API_PORT=8001 ./scripts/dev.sh"
check_port 5173 "the web app" "stop the other Vite dev server"
cd "$ROOT/backend"
uv sync --quiet || fail "could not install backend dependencies (is uv installed? https://docs.astral.sh/uv/)"
eval "$(uv run --quiet python -m app.devtools.preflight show | sed 's/^/CFG_/')"
say "mode: $CFG_DATA_MODE, database: $CFG_DATABASE"

docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }
compose_up() {  # $1 = service
  if docker_ok; then
    say "starting $1 with Docker Compose"
    (cd "$ROOT" && docker compose up -d --wait "$1") || fail "docker compose could not start $1"
  else
    return 1
  fi
}

# --- PostgreSQL
if ! uv run --quiet python -m app.devtools.preflight db >/dev/null 2>&1; then
  if [ "$CFG_DATABASE_IS_LOCAL_POSTGRES" = "yes" ]; then
    compose_up postgres || fail "PostgreSQL is not reachable at $CFG_DATABASE and Docker is not available.
       Start Docker Desktop (and un-pause it), or run PostgreSQL yourself and set DATABASE_URL in .env.
       See analysis/HOW_TO_RUN.md, section 3."
  fi
fi
uv run --quiet python -m app.devtools.preflight db || fail "the database is not reachable; see the message above"

# --- Redis (optional)
if [ "$CFG_REDIS_IS_LOCAL" = "yes" ] && ! uv run --quiet python -m app.devtools.preflight redis >/dev/null 2>&1; then
  compose_up redis || say "Redis is not reachable and Docker is not available; the API will use its in-process cache"
fi
uv run --quiet python -m app.devtools.preflight redis || true

# --- Schema
say "applying database migrations"
uv run --quiet python -m app.devtools.preflight migrate || fail "migrations failed; see the error above"

# --- Mock MCP servers (demo mode)
if [ "$CFG_DATA_MODE" = "demo" ]; then
  for p in 0 1 2; do check_port $(( ${MOCK_MCP_PORT:-9100} + p )) "the demo mock MCP servers" "set MOCK_MCP_PORT=9200"; done
  say "demo mode: starting mock MCP servers (synthetic data)"
  uv run python -m app.devtools.mock_mcp --port "${MOCK_MCP_PORT:-9100}" & pids+=($!)
  sleep 2
fi

say "API on http://127.0.0.1:$API_PORT (API only; open the web app below in your browser)"
uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" & pids+=($!)
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
say "web app on http://127.0.0.1:5173  <- open this in your browser"
VITE_API_PROXY="http://127.0.0.1:$API_PORT" npm run dev -- --host 127.0.0.1 & pids+=($!)
wait
