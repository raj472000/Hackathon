# CI/CD setup in the Azure portal: GitHub → Azure Container Apps

Everything here is done by clicking in the **Azure portal** (https://portal.azure.com) and on **github.com**.
No command line is needed.

When you finish, every push or merge to `dev` (the default branch) automatically:
tests the code → builds the `web` and `api` images → pushes them to the container registry → updates the
Container App `markets` → rolls back if the new version is unhealthy.

The pipeline itself is already written in `.github/workflows/ci.yml`. This guide only connects GitHub to Azure.

---

## What you will create

| # | Where | What | Why |
|---|---|---|---|
| 1 | Azure | Managed identity `id-markets-github` | The "user" GitHub signs in to Azure as |
| 2 | Azure | Federated credentials on that identity | Azure trusts GitHub's sign-in tokens, so **no password is stored anywhere** |
| 3 | Azure | 4 role assignments | Lets the identity push images and update the app, nothing more |
| 4 | Azure | Key Vault secrets linked to the app | The app's own passwords (database, AI key), kept out of GitHub |
| 5 | GitHub | Environment `production` + 6 variables | Tells the workflow which Azure resources to use |

## Names used in this guide

| Item | Name |
|---|---|
| Subscription | Microsoft Partner Network (`38b180cf-5218-4924-8ba2-b8218edd2b02`) |
| Resource group | `HK-RG-04` |
| Region | Central India |
| Container registry | `acrmarketshackathon` |
| Container App | `markets` |
| Key Vault | `kv-markets-Hackathon` |
| App identity (already exists) | `id-markets-Hackathon` |
| GitHub repository | `popcornapps/HK-RG-04` (https://github.com/popcornapps/HK-RG-04) |
| Branch that deploys | `dev` (set in `.github/workflows/ci.yml`) |

## Who can do this

- **Azure:** you must be **Owner** on resource group `HK-RG-04`.
  Check: **Resource groups → HK-RG-04 → Access control (IAM) → View my access**.
  If you are only Contributor, the **Add role assignment** button in Part 3 will be greyed out. Ask an Owner to do
  Parts 1–3 for you.
- **GitHub:** Part 5 needs **admin** on `popcornapps/HK-RG-04`, which only the owner account `popcornapps` has
  (collaborators get *write*). Check: open the repository. If you can see the **Settings** tab, you are admin.
  Either ask `popcornapps` to do Part 5, or follow
  [Appendix: no GitHub admin access](#appendix-no-github-admin-access) instead.

> **Deploys come from `dev`, the repository's default branch.** Every push or merge into `dev` runs the full
> pipeline and deploys. Work on a feature branch and open pull requests into `dev`; pull requests only test and
> build, they never deploy. Finish Parts 1–5 before the next push to `dev`, or that run's `deploy` job will fail.

---

## Part 1 — Create the pipeline identity

1. In the top search bar, type **Managed Identities** and open it.
2. Click **+ Create**.
3. On the **Basics** tab fill in:

   | Field | Value |
   |---|---|
   | Subscription | Microsoft Partner Network |
   | Resource group | `HK-RG-04` |
   | Region | Central India |
   | Name | `id-markets-github` |

4. Click **Review + create**, then **Create**. Wait for *Your deployment is complete*.
5. Click **Go to resource**.
6. On the **Overview** page, copy the **Client ID** and paste it somewhere (Notepad). You need it in Part 5.

## Part 2 — Let Azure trust GitHub (federated credentials)

Still on **id-markets-github**:

1. In the left menu open **Settings → Federated credentials**.
2. Click **+ Add credential**.
3. Fill in the first credential, for pushes to `dev`:

   | Field | Value |
   |---|---|
   | Federated credential scenario | **GitHub Actions deploying Azure resources** |
   | Organization | `popcornapps` |
   | Repository | `HK-RG-04` |
   | Entity | **Branch** |
   | Branch | `dev` |
   | Name | `github-dev` |

   Leave *Issuer* and *Audience* as they are. Click **Add**.

4. Click **+ Add credential** again and fill in the second credential, for the deploy job:

   | Field | Value |
   |---|---|
   | Federated credential scenario | **GitHub Actions deploying Azure resources** |
   | Organization | `popcornapps` |
   | Repository | `HK-RG-04` |
   | Entity | **Environment** |
   | Environment | `production` |
   | Name | `github-production` |

   Click **Add**.

5. You should now see two rows. Check that their **Subject identifier** reads exactly:
   - `repo:popcornapps/HK-RG-04:ref:refs/heads/dev`
   - `repo:popcornapps/HK-RG-04:environment:production`

> Spelling and capital letters must match GitHub exactly. If you rename the repository, edit both credentials.

## Part 3 — Give the identity the permissions it needs

You add four role assignments. The steps are the same each time:

> Open the resource → **Access control (IAM)** → **+ Add** → **Add role assignment** →
> **Role** tab: search for and select the role → **Next** →
> **Members** tab: *Assign access to* = **Managed identity** → **+ Select members** →
> *Managed identity* drop-down = **User-assigned managed identity** → select **id-markets-github** → **Select** →
> **Review + assign** → **Review + assign**.

Do it for each row:

| # | Open this resource (search bar) | Role |
|---|---|---|
| 3.1 | **Container registries → acrmarketshackathon** | **AcrPush** |
| 3.2 | **Container registries → acrmarketshackathon** | **Reader** |
| 3.3 | **Container Apps → markets** | **Contributor** (on the *Privileged administrator roles* tab) |
| 3.4 | The Container Apps environment of `markets` (open **markets → Overview**, click the link next to *Container Apps Environment*) | **Contributor** (on the *Privileged administrator roles* tab) |

Check: open **id-markets-github → Azure role assignments**. You should see the four roles. New assignments can
take up to 5 minutes to appear.

## Part 4 — App secrets in Key Vault

The pipeline does not handle the app's passwords. The Container App reads them from Key Vault, and deployments
leave them untouched. Do this part once per secret.

| Env var on the `api` container | Secret name (use in Key Vault and in the app) | Needed |
|---|---|---|
| `DATABASE_URL` | `database-url` | Yes (already set up) |
| `LLM_API_KEY` | `llm-api-key` | Yes, when AI is enabled (already set up) |
| `API_AUTH_TOKEN` | `api-auth-token` | Recommended; without it the API is open to anyone |
| `WEB_SEARCH_API_KEY` | `web-search-api-key` | Only for a paid search provider |

The steps below add `api-auth-token`. Repeat them with another name for any other secret.

### 4.1 Allow yourself to write secrets (once)

1. Open **Key vaults → kv-markets-Hackathon → Access control (IAM) → + Add → Add role assignment**.
2. Role: **Key Vault Secrets Officer** → **Next**.
3. *Assign access to*: **User, group, or service principal** → **+ Select members** → select yourself →
   **Select** → **Review + assign**.

### 4.2 Allow the app to read secrets (once; usually already done)

1. On **kv-markets-Hackathon → Access control (IAM)**, open the **Role assignments** tab.
2. Look for **id-markets-Hackathon** with role **Key Vault Secrets User**.
3. If it is missing: **+ Add → Add role assignment → Key Vault Secrets User → Next →** *Managed identity* →
   **id-markets-Hackathon** → **Review + assign**.

### 4.3 Create the secret in Key Vault

1. On **kv-markets-Hackathon**, open **Objects → Secrets → + Generate/Import**.
2. Fill in:
   - **Upload options:** Manual
   - **Name:** `api-auth-token`
   - **Secret value:** a long random string (at least 32 characters; a password generator works)
3. Click **Create**.
4. Click the new secret, then its **Current version**, and copy the **Secret Identifier**.
   Delete the last part (the long version number) so it ends in `/secrets/api-auth-token`:
   `https://kv-markets-hackathon.vault.azure.net/secrets/api-auth-token`
   Without the version, the app always uses the newest value.

### 4.4 Link the secret to the Container App

1. Open **Container Apps → markets → Settings → Secrets → + Add**.
2. Fill in:
   - **Key:** `api-auth-token`
   - **Type:** **Key Vault reference**
   - **Key Vault secret URL:** the URL from 4.3
   - **Managed identity:** **id-markets-Hackathon**
3. Click **Add**.

### 4.5 Give the secret to the `api` container

1. On **markets**, open **Application → Containers** and click **Edit and deploy**.
2. In the *Container image* list, tick **api** and click **Edit**.
3. Open the **Environment variables** tab and click **+ Add**:
   - **Name:** `API_AUTH_TOKEN`
   - **Source:** **Reference a secret**
   - **Value:** `api-auth-token`
4. Click **Save**, then **Create** at the bottom. Do not change the image or the `web` container.
5. Open **Application → Revisions and replicas**. Wait until the new revision shows **Running** and **Healthy**.

> Once `API_AUTH_TOKEN` is set, the API rejects requests without the token. If the website stops loading data,
> repeat 4.5 and delete the variable to switch the check off.

### Changing a secret later

1. **kv-markets-Hackathon → Secrets →** click the secret **→ + New Version** → paste the new value → **Create**.
2. **markets → Application → Revisions and replicas →** click the active revision → **Restart**.
   The container reads secrets only when it starts.

## Part 5 — Connect GitHub (on github.com)

### 5.1 Find your tenant ID

In the Azure portal, search **Microsoft Entra ID** → **Overview** → copy the **Tenant ID**.

### 5.2 Create the `production` environment

1. Open https://github.com/popcornapps/HK-RG-04 → **Settings** → **Environments** → **New environment**.
2. Name: `production` → **Configure environment**.
3. Under **Deployment branches and tags**, choose **Selected branches and tags** → **Add deployment branch or tag
   rule** → enter `dev` → **Add rule**.
4. Optional: tick **Required reviewers** and add yourself, if you want to approve each deploy by hand.
5. Click **Save protection rules**.

### 5.3 Add the six variables

1. **Settings → Secrets and variables → Actions**.
2. Open the **Variables** tab (not *Secrets*) → **New repository variable**.
3. Add each row, one at a time:

   | Name | Value |
   |---|---|
   | `AZURE_CLIENT_ID` | Client ID from Part 1, step 6 |
   | `AZURE_TENANT_ID` | Tenant ID from 5.1 |
   | `AZURE_SUBSCRIPTION_ID` | `38b180cf-5218-4924-8ba2-b8218edd2b02` |
   | `AZURE_ACR` | `acrmarketshackathon` |
   | `AZURE_RESOURCE_GROUP` | `HK-RG-04` |
   | `AZURE_CONTAINER_APP` | `markets` |

These are names and IDs, not passwords, so they go under *Variables*. Nothing goes under *Secrets*.

## Part 6 — First deploy

1. On GitHub, open a **pull request** from a feature branch into `dev`.
   The checks run tests and build the images. Nothing is deployed yet.
   All checks must be green. If *Dependency audit* fails, a Python package needs upgrading before you can deploy.
2. **Merge** the pull request (a direct push to `dev` also deploys).
3. Open the **Actions** tab → the newest **ci** run. Jobs run in this order:
   `backend`, `frontend` → `e2e` → `images` → `deploy`.
   (If you added required reviewers in 5.2, click **Review deployments → Approve**.)
4. When `deploy` is green, check in Azure: **markets → Application → Revisions and replicas**. A new revision
   named `markets--sha-…` is **Running**, **Healthy** and receiving **100%** of traffic.
5. Open the app URL (**markets → Overview → Application Url**) and check the site loads.

From now on, every push or merge into `dev` deploys automatically.

## Rolling back

- **Automatic:** if the new version is not healthy within 10 minutes, the pipeline switches back to the previous
  images and the `deploy` job turns red.
- **By hand, in Azure:** the app runs one revision at a time, so roll back by redeploying older images.
  1. **markets → Application → Revisions and replicas** → open the last good revision and note the image tags
     of `web` and `api` (the part after `:`, a commit SHA).
  2. **Application → Containers → Edit and deploy** → tick **api** → **Edit** → set **Image tag** to the old tag →
     **Save**. Do the same for **web**.
  3. Click **Create** and wait until the new revision is **Running** and **Healthy**.
- **By hand, in GitHub:** **Actions** → open the run of the commit you want → **Re-run jobs** → `deploy`.

## When something fails

| What you see | Where it failed | Fix |
|---|---|---|
| `deploy` job never starts | A test job is red | Open the red job in **Actions**, fix the cause, push again |
| *Deployment settings present* step fails | GitHub variables | A variable from 5.3 is missing or misspelled |
| `AADSTS70021: No matching federated identity record found` | Part 2 | Subject does not match the repository/branch/environment exactly |
| `AuthorizationFailed … managedEnvironments/join/action` | Part 3 | Missing row 3.4 |
| `denied: requested access to the resource is denied` while pushing images | Part 3 | Missing row 3.1, or wait 5 minutes for it to apply |
| New revision shows **Activation failed**, then rolled back | The app crashes on start | **markets → Monitoring → Log stream** → container `api` |
| Site is up but data does not load | Part 4 | Check the secret links: **markets → Settings → Secrets** |
| Secret change did nothing | Part 4 | Restart the revision ("Changing a secret later", step 2) |

## Removing the setup

1. Azure: **Managed Identities → id-markets-github → Delete**. Its federated credentials are deleted with it.
   Then on each resource from Part 3 open **Access control (IAM) → Role assignments** and remove the leftover
   *Identity not found* entries.
2. GitHub: **Settings → Environments → production → Delete**, and delete the six variables under
   **Settings → Secrets and variables → Actions → Variables**.

---

## Appendix: no GitHub admin access

Without admin rights you cannot create the `production` environment or repository variables (Part 5). The
Azure side (Parts 1–4) is the same, with one change, and the workflow file needs a small edit.

This is your situation on `popcornapps/HK-RG-04` unless the owner account does Part 5 for you.

**Azure:** in Part 2, create only the first credential (`github-dev`: Organization `popcornapps`, Repository
`HK-RG-04`, Entity **Branch**, `dev`). Its subject must read `repo:popcornapps/HK-RG-04:ref:refs/heads/dev`.

**GitHub:** edit `.github/workflows/ci.yml` (in the browser: open the file → pencil icon):

1. Under the top-level `env:` add the six values from 5.3:
   ```yaml
   env:
     PUSH_IMAGES: ${{ github.event_name == 'push' && github.ref == 'refs/heads/dev' }}
     AZURE_CLIENT_ID: <Client ID from Part 1>
     AZURE_TENANT_ID: <Tenant ID from 5.1>
     AZURE_SUBSCRIPTION_ID: 38b180cf-5218-4924-8ba2-b8218edd2b02
     AZURE_ACR: acrmarketshackathon
     AZURE_RESOURCE_GROUP: HK-RG-04
     AZURE_CONTAINER_APP: markets
   ```
2. Replace every `vars.AZURE_` with `env.AZURE_`.
3. In the `deploy` job, delete the three lines starting at `environment:` (`name: production` and `url: …`).
4. Commit the change to `dev`.

What you give up: there is no approval step, so anyone who can merge into `dev` deploys. Other branches still
cannot deploy, because Azure only trusts `dev`.
