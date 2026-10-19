#!/usr/bin/env bash
# Regenerate pip-style requirement files from backend/uv.lock. The lockfile stays the source of truth.
set -euo pipefail
cd "$(dirname "$0")/../backend"
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt >/dev/null
uv export --frozen --no-hashes --no-emit-project -o requirements-dev.txt >/dev/null
echo "wrote backend/requirements.txt ($(grep -vc '^\s*#' requirements.txt) lines) and backend/requirements-dev.txt"
