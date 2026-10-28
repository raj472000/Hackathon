#!/usr/bin/env bash
# One-time setup so GitHub Actions can push images and deploy to Azure without any stored password:
#   - a user-assigned managed identity that GitHub signs in as through OIDC (federated credentials),
#   - the narrowest roles the pipeline needs: push to the registry, update one container app,
#   - a GitHub "production" environment limited to main, and the repository variables the workflow reads.
#
#   AZ_RG=HK-RG-04 AZ_ACR=acrmarketshackathon AZ_APP=markets infra/azure/setup-github-deploy.sh
#
# Optional: AZ_DEPLOY_IDENTITY (id-markets-github), GH_REPO (owner/name; defaults to this checkout's origin).
# Re-runnable. Needs `az` signed in as Owner (or User Access Administrator) on the resource group, and `gh`
# signed in with admin rights on the repository.
set -euo pipefail

RG=${AZ_RG:?set AZ_RG}
ACR=${AZ_ACR:?set AZ_ACR}
APP=${AZ_APP:?set AZ_APP}
IDENTITY=${AZ_DEPLOY_IDENTITY:-id-markets-github}
REPO=${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
LOCATION=$(az group show -n "$RG" --query location -o tsv)
ACR_ID=$(az acr show -n "$ACR" -g "$RG" --query id -o tsv)
APP_ID=$(az containerapp show -n "$APP" -g "$RG" --query id -o tsv)
CAE_ID=$(az containerapp show -n "$APP" -g "$RG" --query properties.environmentId -o tsv)

log "managed identity $IDENTITY"
az identity show -n "$IDENTITY" -g "$RG" -o none 2>/dev/null || az identity create -n "$IDENTITY" -g "$RG" -l "$LOCATION" -o none
CLIENT_ID=$(az identity show -n "$IDENTITY" -g "$RG" --query clientId -o tsv)
PRINCIPAL=$(az identity show -n "$IDENTITY" -g "$RG" --query principalId -o tsv)

log "federated credentials for $REPO"
federate() {  # name subject
  az identity federated-credential show -n "$1" --identity-name "$IDENTITY" -g "$RG" -o none 2>/dev/null \
    || az identity federated-credential create -n "$1" --identity-name "$IDENTITY" -g "$RG" \
         --issuer https://token.actions.githubusercontent.com --subject "$2" --audiences api://AzureADTokenExchange -o none
  echo "   $2"
}
federate github-main "repo:$REPO:ref:refs/heads/main"                # images job: push to ACR
federate github-production "repo:$REPO:environment:production"     # deploy job

log "role assignments"
assign() {  # role scope
  [[ -n $(az role assignment list --assignee "$PRINCIPAL" --role "$1" --scope "$2" --query '[0].id' -o tsv) ]] \
    || az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
         --role "$1" --scope "$2" -o none
  echo "   $1 on ${2##*/}"
}
assign AcrPush "$ACR_ID"            # docker push
assign Reader "$ACR_ID"             # az acr login looks the registry up
assign Contributor "$APP_ID"        # PATCH the app's template, read revisions
assign Contributor "$CAE_ID"        # a revision update joins the managed environment

log "GitHub environment 'production' (main only) and repository variables"
gh api -X PUT "repos/$REPO/environments/production" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api "repos/$REPO/environments/production/deployment-branch-policies" -q '.branch_policies[].name' | grep -qx main \
  || gh api -X POST "repos/$REPO/environments/production/deployment-branch-policies" -f name=main -f type=branch >/dev/null
for kv in AZURE_CLIENT_ID="$CLIENT_ID" AZURE_TENANT_ID="$TENANT" AZURE_SUBSCRIPTION_ID="$SUB" \
          AZURE_ACR="$ACR" AZURE_RESOURCE_GROUP="$RG" AZURE_CONTAINER_APP="$APP"; do
  gh variable set "${kv%%=*}" --repo "$REPO" --body "${kv#*=}"
  echo "   ${kv%%=*}"
done

log "done: the next push to main builds, tests and deploys. Role assignments can take a few minutes to apply."
