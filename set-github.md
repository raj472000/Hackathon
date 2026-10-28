# Set up GitHub → Azure automatic deployment

After these steps, every push to `main` runs the tests, builds the two images, pushes them to ACR and deploys them
to the `markets` Container App. How the pipeline works: [`infra/azure/CICD.md`](infra/azure/CICD.md).

You do this **once**. You need:
- `az` signed in as **Owner** on resource group `HK-RG-04`: `az login`, then `az account set -s 38b180cf-5218-4924-8ba2-b8218edd2b02`;
- `gh` signed in as an **admin** of `raj-accellor/Hackathon`: `gh auth status`.

There are two ways to do it: the script (section A) or the same steps by hand (section B). Then do section C.

---

## 0. Before the first deploy: make the live app healthy

A deploy only swaps images. It keeps whatever configuration the app has. Today the API container fails start-up
with `DATABASE_URL is not set`, because the Key Vault secrets aren't linked to the app. Every deploy would fail
its health check and roll back until that is fixed. See `infra/azure/ENVIRONMENT.md`, section 1. Check:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://markets.greencliff-c30bacad.centralindia.azurecontainerapps.io/health/ready   # want 200
```

---

## A. With the script (recommended)

From the repository root:

```bash
AZ_RG=HK-RG-04 AZ_ACR=acrmarketshackathon AZ_APP=markets infra/azure/setup-github-deploy.sh
```

It is safe to re-run. It performs every step in section B. Then go to section C.

---

## B. By hand

### B1. Variables used below

```bash
RG=HK-RG-04
ACR=acrmarketshackathon
APP=markets
REPO=raj-accellor/Hackathon
IDENTITY=id-markets-github
LOCATION=centralindia
```

### B2. Create the identity GitHub signs in as

```bash
az identity create -n $IDENTITY -g $RG -l $LOCATION -o none
CLIENT_ID=$(az identity show -n $IDENTITY -g $RG --query clientId -o tsv)
PRINCIPAL=$(az identity show -n $IDENTITY -g $RG --query principalId -o tsv)
```

### B3. Trust this repository (OIDC federated credentials, no password)

```bash
# Used by the "images" job (push to ACR) on pushes to main
az identity federated-credential create -n github-main --identity-name $IDENTITY -g $RG \
  --issuer https://token.actions.githubusercontent.com \
  --subject "repo:$REPO:ref:refs/heads/main" --audiences api://AzureADTokenExchange

# Used by the "deploy" job, which runs in the GitHub environment "production"
az identity federated-credential create -n github-production --identity-name $IDENTITY -g $RG \
  --issuer https://token.actions.githubusercontent.com \
  --subject "repo:$REPO:environment:production" --audiences api://AzureADTokenExchange
```

The subject must match exactly, including case. If the repository is renamed or moved, update both.

### B4. Grant only what the pipeline needs

```bash
ACR_ID=$(az acr show -n $ACR -g $RG --query id -o tsv)
APP_ID=$(az containerapp show -n $APP -g $RG --query id -o tsv)
CAE_ID=$(az containerapp show -n $APP -g $RG --query properties.environmentId -o tsv)

role() { az role assignment create --assignee-object-id $PRINCIPAL --assignee-principal-type ServicePrincipal --role "$1" --scope "$2" -o none; }
role AcrPush     $ACR_ID   # push images
role Reader      $ACR_ID   # az acr login looks the registry up
role Contributor $APP_ID   # update the app's template, read revisions
role Contributor $CAE_ID   # a revision update joins the Container Apps environment
```

Role assignments can take a few minutes to apply.

### B5. GitHub environment "production" (main only)

```bash
gh api -X PUT repos/$REPO/environments/production --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api -X POST repos/$REPO/environments/production/deployment-branch-policies -f name=main -f type=branch
```

In the browser: **Settings → Environments → New environment → `production`**, then under *Deployment branches and
tags* choose **Selected branches and tags** and add `main`. Optional: add **Required reviewers** to approve each
deploy by hand.

### B6. Repository variables

These are identifiers, not secrets. The workflow reads them as `vars.*`. Nothing goes under *Secrets*.

```bash
gh variable set AZURE_CLIENT_ID       --repo $REPO --body "$CLIENT_ID"
gh variable set AZURE_TENANT_ID       --repo $REPO --body "$(az account show --query tenantId -o tsv)"
gh variable set AZURE_SUBSCRIPTION_ID --repo $REPO --body "$(az account show --query id -o tsv)"
gh variable set AZURE_ACR             --repo $REPO --body "$ACR"
gh variable set AZURE_RESOURCE_GROUP  --repo $REPO --body "$RG"
gh variable set AZURE_CONTAINER_APP   --repo $REPO --body "$APP"
```

In the browser: **Settings → Secrets and variables → Actions → Variables → New repository variable**.

---

## C. Check the setup

```bash
az identity federated-credential list --identity-name id-markets-github -g HK-RG-04 --query "[].subject" -o tsv
#   repo:raj-accellor/Hackathon:ref:refs/heads/main
#   repo:raj-accellor/Hackathon:environment:production

az role assignment list --assignee $(az identity show -n id-markets-github -g HK-RG-04 --query principalId -o tsv) \
  --all --query "[].{role:roleDefinitionName, scope:scope}" -o table
#   AcrPush, Reader (registry) · Contributor (markets) · Contributor (managed environment)

gh variable list --repo raj-accellor/Hackathon        # the six AZURE_* variables
gh api repos/raj-accellor/Hackathon/environments --jq '.environments[].name'   # production
```

Optional: preview a deploy. It reads the app and prints old → new images, and applies nothing:

```bash
DRY_RUN=1 AZ_RG=HK-RG-04 AZ_APP=markets \
  IMAGES="web=acrmarketshackathon.azurecr.io/markets-web:test api=acrmarketshackathon.azurecr.io/markets-api:test" \
  infra/azure/deploy.sh
```

## D. First deploy

1. Push `dev` and open a pull request to `main`. CI runs, including the new `images` job (build + smoke test,
   no push).
2. Merge the pull request. In **Actions → ci** the run shows `backend`, `frontend`, `e2e`, `images` and then
   `deploy`.
3. The `deploy` job logs the old and new images, the revision state, and the smoke test, and ends with
   `deployed markets--sha-<sha7>-<run>-<attempt>`. The environment link opens the site.
4. If it fails, the job turns red and production is put back on the previous images (`markets--rb-…`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `deploy` fails at "Deployment settings present" | A variable from B6 is missing |
| `AADSTS70021: No matching federated identity record found` | Subject in B3 does not match: wrong repo name, branch, or environment name (case-sensitive) |
| `AuthorizationFailed ... Microsoft.App/managedEnvironments/join/action` | Missing Contributor on the managed environment (B4) |
| `denied: requested access to the resource is denied` on push | Missing AcrPush on the registry (B4), or the role assignment hasn't applied yet |
| `... was not created: the app update failed` | Azure rejected the update, usually because an image it cannot pull |
| Revision `ActivationFailed`, rolled back | The new image starts and crashes: check `az containerapp logs show -n markets -g HK-RG-04 --container api --revision <rev>` |
| Health check `/health/ready` not 200 | Configuration problem (database, secrets); see section 0 |

To undo everything: `az identity delete -n id-markets-github -g HK-RG-04` (its federated credentials go with it;
remove the role assignments with `az role assignment delete --assignee <principal id>` first), delete the
`production` environment, and delete the six variables.
