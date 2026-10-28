#!/usr/bin/env bash
# Start the built api and web images side by side (web proxies /api and /health to api, as on Azure) and
# check that both serve. Runs offline against a throwaway PostgreSQL: migrations from the image, demo mode,
# no scheduler, no initial refresh.
#
#   scripts/smoke-images.sh <api-image> <web-image>
set -euo pipefail

API_IMAGE=${1:?usage: $0 <api-image> <web-image>}
WEB_IMAGE=${2:?usage: $0 <api-image> <web-image>}
NAME=markets-smoke-$$
PORT=${SMOKE_PORT:-18080}

cleanup() {
  [[ ${ok:-0} == 1 ]] || { echo "--- api logs"; docker logs "$NAME-api" 2>&1 | tail -40 || true
                           echo "--- web logs"; docker logs "$NAME-web" 2>&1 | tail -20 || true; }
  docker rm -f "$NAME-api" "$NAME-web" "$NAME-db" >/dev/null 2>&1 || true
  docker network rm "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

DB_URL=postgresql+psycopg://markets:smoke-only@db:5432/markets
docker network create "$NAME" >/dev/null
docker run -d --name "$NAME-db" --network "$NAME" --network-alias db \
  -e POSTGRES_USER=markets -e POSTGRES_PASSWORD=smoke-only -e POSTGRES_DB=markets postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do docker exec "$NAME-db" pg_isready -h 127.0.0.1 -U markets -d markets >/dev/null 2>&1 && break; sleep 1; done
echo "migrate: $(docker run --rm --network "$NAME" -e DATABASE_URL="$DB_URL" "$API_IMAGE" alembic upgrade head 2>&1 | tail -1)"
docker run -d --name "$NAME-api" --network "$NAME" --network-alias api \
  -e APP_ENV=test -e DATA_MODE=demo -e SCHEDULER_ENABLED=false -e AUTO_INITIAL_REFRESH=false \
  -e DATABASE_URL="$DB_URL" "$API_IMAGE" >/dev/null
docker run -d --name "$NAME-web" --network "$NAME" -e API_UPSTREAM=api:8000 -p "127.0.0.1:$PORT:8080" \
  "$WEB_IMAGE" >/dev/null

check() {  # path expected-substring
  local body
  for _ in $(seq 1 30); do
    if body=$(curl -fsS -m 5 "http://127.0.0.1:$PORT$1" 2>/dev/null) && [[ $body == *"$2"* ]]; then
      echo "ok   GET $1"; return 0
    fi
    sleep 2
  done
  echo "FAIL GET $1 (expected '$2')"; return 1
}

check / '<div id="root">'
check /health/live ok
check /health/ready '"status":"ready"'
check /api/v1/sources/status '{'
ok=1
