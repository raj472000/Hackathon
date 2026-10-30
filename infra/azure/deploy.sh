#!/usr/bin/env bash
# Roll new images onto an existing Azure Container App as ONE new revision, wait until it is healthy,
# smoke-test the public URL, and put the previous images back if anything fails.
#
# Only the image of each named container changes. Env vars, Key Vault secret references, probes and volumes are
# copied from the app as it is right now, so configuration done in the portal or by configure.sh is never
# overwritten by a deploy. The one exception is scale: it is pinned to exactly one replica (see step 2).
#
#   AZ_RG=HK-RG-04 AZ_APP=markets \
#   IMAGES="web=acrmarketshackathon.azurecr.io/markets-web:<sha> api=acrmarketshackathon.azurecr.io/markets-api:<sha>" \
#   infra/azure/deploy.sh
#
# Optional: REVISION_SUFFIX (default sha-<epoch>), SMOKE_PATHS ("/ /health/live /health/ready"),
#           WAIT_SECONDS (600), ROLLBACK (1), DRY_RUN (1 = print the image changes, apply nothing),
#           SINGLE_REPLICA (1 = pin the app to one replica; set 0 only once Redis and a separate worker exist).
# Needs only `az` (signed in) and `jq`; uses ARM directly, so no containerapp CLI extension is required.
set -euo pipefail

RG=${AZ_RG:?set AZ_RG}
APP=${AZ_APP:?set AZ_APP}
IMAGES=${IMAGES:?set IMAGES, e.g. "web=<registry>/markets-web:<tag> api=<registry>/markets-api:<tag>"}
SUFFIX=${REVISION_SUFFIX:-d$(date -u +%y%m%d%H%M%S)}
SMOKE_PATHS=${SMOKE_PATHS:-/ /health/live /health/ready}
WAIT=${WAIT_SECONDS:-600}
ROLLBACK=${ROLLBACK:-1}
DRY_RUN=${DRY_RUN:-0}
SINGLE_REPLICA=${SINGLE_REPLICA:-1}
API_VERSION=2024-03-01

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Revision names are <app>--<suffix>: lowercase letters, digits and '-', starting with a letter.
[[ $SUFFIX =~ ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ ]] || die "REVISION_SUFFIX '$SUFFIX' must be lowercase alphanumeric/'-', start with a letter"

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
SUB=$(az account show --query id -o tsv) || die "run 'az login' first"
APP_URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/containerApps/$APP"
arm() { az rest --method "$1" --url "$2?api-version=$API_VERSION" "${@:3}"; }

# ---- 1. Current state ---------------------------------------------------------------------------------------
arm get "$APP_URL" > "$work/app.json" || die "container app $APP not found in $RG"
jq '.properties.template' "$work/app.json" > "$work/previous.json"
FQDN=$(jq -r '.properties.configuration.ingress.fqdn // empty' "$work/app.json")
PREV_READY=$(jq -r '.properties.latestReadyRevisionName // empty' "$work/app.json")
log "app $APP: latest ready revision ${PREV_READY:-<none>}"

# ---- 2. New template = current template with the requested images --------------------------------------------
cp "$work/previous.json" "$work/next.json"
for pair in $IMAGES; do
  name=${pair%%=*}; image=${pair#*=}
  [[ $name != "$pair" && -n $image && $image == *:* ]] || die "bad IMAGES entry '$pair' (want container=registry/repo:tag)"
  jq -e --arg n "$name" 'any(.containers[]; .name == $n)' "$work/next.json" >/dev/null \
    || die "container '$name' not in $APP (has: $(jq -r '[.containers[].name] | join(", ")' "$work/next.json"))"
  printf '   %-6s %s\n          -> %s\n' "$name" "$(jq -r --arg n "$name" '.containers[] | select(.name == $n) | .image' "$work/next.json")" "$image"
  jq --arg n "$name" --arg i "$image" '(.containers[] | select(.name == $n) | .image) = $i' "$work/next.json" > "$work/tmp.json"
  mv "$work/tmp.json" "$work/next.json"
done

# The API runs the scheduler and holds the Zerodha Kite MCP session (Kite binds the account holder's approval to
# that one session) in process. A second replica runs every job twice and has its own, unapproved Kite session, so
# a login approved on one replica is missing on the other. Azure's default scale (and the HTTP scaler, which adds
# replicas at 10 concurrent requests) allows up to 10, so the app is pinned to one replica on every deploy.
if [[ $SINGLE_REPLICA == 1 ]]; then
  cur=$(jq -c '{min: .scale.minReplicas, max: .scale.maxReplicas}' "$work/next.json")
  [[ $cur == '{"min":1,"max":1}' ]] || log "scale $cur -> {\"min\":1,\"max\":1} (single replica: scheduler and Kite session live in the API)"
  jq '.scale = ((.scale // {}) + {minReplicas: 1, maxReplicas: 1} | del(.rules))' "$work/next.json" > "$work/tmp.json"
  mv "$work/tmp.json" "$work/next.json"
fi

if [[ $DRY_RUN == 1 ]]; then
  # Prove that nothing except images, scale and the revision suffix would change.
  diff <(jq -S '.containers |= map(del(.image)) | del(.scale)' "$work/previous.json") \
       <(jq -S '.containers |= map(del(.image)) | del(.scale)' "$work/next.json") >/dev/null \
    || die "internal error: template changed beyond images and scale"
  log "dry run: would create revision $APP--$SUFFIX (images and scale only); nothing applied"
  exit 0
fi

# ---- 3. Apply and wait --------------------------------------------------------------------------------------
apply() {  # template-file suffix
  jq --arg s "$2" '{properties: {template: (. + {revisionSuffix: $s})}}' "$1" > "$work/patch.json"
  arm patch "$APP_URL" --body @"$work/patch.json" -o none
}

wait_healthy() {  # revision-name -> 0 when Healthy and serving, 1 when failed or timed out
  local rev=$1 deadline=$(( SECONDS + WAIT )) state
  while (( SECONDS < deadline )); do
    state=$(arm get "$APP_URL/revisions/$rev" 2>/dev/null \
      | jq -r '.properties | "\(.provisioningState // "-")/\(.healthState // "-")/\(.runningState // "-")"') || state=pending
    # Azure validates the update before it creates the revision (e.g. an image tag it cannot pull). When that
    # fails, the revision never appears and only the app's provisioning state says so.
    if [[ $state == pending && $(arm get "$APP_URL" | jq -r '.properties.provisioningState') == Failed ]]; then
      echo "   $rev was not created: the app update failed (check that the images exist and are pullable)"; return 1
    fi
    # A crash-looping container ends in ActivationFailed (or stays Activating until the deadline).
    case $state in
      Provisioned/Healthy/Running*) echo "   $rev $state"; return 0 ;;
      Failed/*|*/*/Failed|*/*/ActivationFailed) echo "   $rev $state"; return 1 ;;
    esac
    echo "   $rev ${state:-pending} (waiting)"; sleep 15
  done
  echo "   $rev did not become healthy within ${WAIT}s"; return 1
}

smoke() {  # every path must return 200 through the public ingress, allowing a short warm-up
  [[ -n $FQDN ]] || { warn "app has no ingress; skipping HTTP smoke test"; return 0; }
  local path code
  for path in $SMOKE_PATHS; do
    for _ in $(seq 1 12); do
      code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' "https://$FQDN$path") || code=000
      [[ $code == 200 ]] && break
      sleep 10
    done
    echo "   GET $path -> $code"
    [[ $code == 200 ]] || return 1
  done
}

serving_revision_is() {  # in single-revision mode the new revision must actually take over
  local ready
  ready=$(arm get "$APP_URL" | jq -r '.properties.latestReadyRevisionName // empty')
  [[ $ready == "$1" ]] || { echo "   latest ready revision is ${ready:-<none>}, expected $1"; return 1; }
}

NEW_REV="$APP--$SUFFIX"
log "creating revision $NEW_REV"
apply "$work/next.json" "$SUFFIX"

if wait_healthy "$NEW_REV" && serving_revision_is "$NEW_REV" && { log "smoke test https://$FQDN"; smoke; }; then
  log "deployed $NEW_REV"
  [[ -n ${GITHUB_OUTPUT:-} ]] && { echo "revision=$NEW_REV"; echo "url=https://$FQDN"; } >> "$GITHUB_OUTPUT"
  exit 0
fi

warn "deployment of $NEW_REV failed"
if [[ $ROLLBACK == 1 ]]; then
  RB_SUFFIX="rb-${SUFFIX:0:27}"
  log "rolling back to the previous images as $APP--$RB_SUFFIX"
  # Previous images, but the same single-replica scale as the failed attempt.
  jq --argjson sc "$(jq '.scale' "$work/next.json")" '.scale = $sc' "$work/previous.json" > "$work/rollback.json"
  apply "$work/rollback.json" "$RB_SUFFIX"
  if wait_healthy "$APP--$RB_SUFFIX"; then
    warn "rolled back; production runs the previous images again"
  else
    warn "rollback revision is not healthy either (it was ${PREV_READY:+last ready as $PREV_READY, }check the app's configuration)"
  fi
fi
exit 1
