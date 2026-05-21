# Pipeline & deployment setup guide

**Already in the repo:** see [`CONFIG_IN_REPO.md`](CONFIG_IN_REPO.md) and [`config/deploy.public.json`](../config/deploy.public.json). Run `bash tools/sync-deploy-config.sh` after editing that JSON.

Use this guide to **find** remaining values (Google OAuth, ADO secrets), then paste them into [`tools/pipeline-variables.env`](../tools/pipeline-variables.env) (copy from the `.example` file).

**Two places secrets live:**

| Where | What goes there |
|-------|-----------------|
| **Azure DevOps → Pipeline variables** | CI/CD deploy (`azureSubscription`, `googleClientId`, SWA token, …) |
| **Azure Function App → Configuration → Application settings** | Runtime API (`COSMOS_KEY`, `JWT_SECRET`, `AZURE_AI_KEY`, …) |

Pipeline variables **do not** replace Function App settings. Both must be set for a full production stack.

---

## Part A — Azure DevOps (for CI/CD deploy)

### A1. Create the pipeline (one time)

1. Open [https://dev.azure.com](https://dev.azure.com) → your organization → project.
2. **Pipelines** → **New pipeline** → **Azure Repos Git** (or GitHub) → select this repo.
3. **Existing Azure Pipelines YAML file** → branch `main` → `/azure-pipelines.yml` → **Save** (run once; deploy may fail until variables below are set).

### A2. Service connection — `azureSubscription`

**What it is:** The *name* of the ARM service connection in Azure DevOps (not your Azure subscription GUID).

**How to get / create:**

1. **Project settings** (bottom-left) → **Service connections** → **New service connection**.
2. Choose **Azure Resource Manager** → **Service principal (automatic)**.
3. Pick your **Subscription** and **Resource group** (`rg-autoapply-dev` or yours) → **Save**.
4. Copy the **connection name** exactly (e.g. `azure-autoapply-dev`).

**Set as:** Pipeline variable `azureSubscription` = that name (not a secret).

---

### A3. Function App name — `functionAppName`

**What it is:** The Azure Functions app resource name (no `https://`).

**Where to find:**

- [Azure Portal](https://portal.azure.com) → **Resource groups** → your RG → open the **Function App** (icon: lightning bolt).
- **Overview** → name at the top, e.g. `autoapply-func-dev`.

**Or CLI:**

```bash
az functionapp list -g rg-autoapply-dev --query "[].name" -o tsv
```

**Set as:** `functionAppName` = `autoapply-func-dev` (already defaulted in `azure-pipelines.yml` if yours matches).

**Related:** `apiBaseUrl` = `https://<functionAppName>.azurewebsites.net` (no trailing slash).

---

### A4. Resource group — `resourceGroup`

**Where to find:**

- Portal → Function App → **Overview** → **Resource group** link.

**Or CLI:**

```bash
az group list --query "[?contains(name,'autoapply')].name" -o tsv
```

**Set as:** `resourceGroup` = e.g. `rg-autoapply-dev`.

---

### A5. Static Web App deploy token — `staticWebAppDeploymentToken`

**What it is:** Secret token used by the pipeline to upload `app/build/web`.

**Where to find:**

1. Portal → **Static Web App** (e.g. hostname like `mango-ocean-….azurestaticapps.net`).
2. **Overview** → note **URL** / default hostname → use for extension/SWA steps later.
3. **Manage deployment token** (or **Settings** → **Deployment token**) → **Copy**.

**Or CLI:**

```bash
SWA_NAME=$(az staticwebapp list -g rg-autoapply-dev --query "[0].name" -o tsv)
az staticwebapp secrets list -n "$SWA_NAME" -g rg-autoapply-dev --query "properties.apiKey" -o tsv
```

**Set as:** Pipeline variable `staticWebAppDeploymentToken` — mark **Keep this value secret** ✓

---

### A6. Google OAuth Web Client ID — `googleClientId`

**What it is:** Web client ID for Sign in with Google (Flutter web + extension).

**How to get:**

1. [Google Cloud Console](https://console.cloud.google.com/) → select/create a project.
2. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**.
3. Application type: **Web application**.
4. **Authorized JavaScript origins** — add **both**:
   - `https://<your-static-web-app-hostname>` (e.g. `https://mango-ocean-0f1de6810.2.azurestaticapps.net`)
   - `http://localhost:8080` (or `http://localhost:*` if the console allows)
5. For the **Chrome extension**, also add the extension redirect URI (shown in `chrome://extensions` when you load the unpacked extension — looks like `https://<extension-id>.chromiumapp.org/`).
6. Copy **Client ID** → ends with `.apps.googleusercontent.com`.

**Set as:** Pipeline variable `googleClientId` — mark **secret** ✓

---

### A7. Admin emails — `adminEmails`

**What it is:** Comma-separated emails that see the Admin tab in the web app.

**Example:** `vibhuu1720@gmail.com` or `you@example.com,other@example.com`

**Set as:** Pipeline variable `adminEmails` (not secret).

**Also set on Function App** (runtime): `SUPER_ADMIN_EMAILS` with the same value (see Part B).

---

### A8. Optional toggles

| Variable | Default | When to change |
|----------|---------|----------------|
| `enableDeploy` | `true` in YAML | Set to `false` in ADO to pause deploys |
| `runInfraDeploy` | `false` | Set to `true` only when intentionally running Bicep from CI |

---

### A9. Set variables in Azure DevOps (manual)

1. **Pipelines** → select **AutoApply** pipeline → **Edit** → **Variables** (top right).
2. Add each name/value; check **Keep this value secret** for `googleClientId` and `staticWebAppDeploymentToken`.
3. **Save**.

**Or** fill `tools/pipeline-variables.env` and run:

```bash
bash tools/apply-pipeline-config.sh
```

(requires Azure CLI + `az extension add --name azure-devops` and `az devops login` — see script header).

---

## Part B — Azure Function App (runtime API)

These are **not** pipeline variables. Set them on the live Function App so `/api/v1/*` works after deploy.

**Where:** Portal → **Function App** → **Settings** → **Environment variables** (or **Configuration** → **Application settings**).

| Setting | Where to get the value |
|---------|-------------------------|
| `COSMOS_ENDPOINT` | Portal → **Azure Cosmos DB** → **Overview** → **URI** |
| `COSMOS_KEY` | Cosmos → **Keys** → **PRIMARY KEY** |
| `COSMOS_DATABASE` | `autoapply` (fixed) |
| `BLOB_CONNECTION_STRING` | Portal → **Storage account** → **Access keys** → **Connection string** |
| `AZURE_AI_ENDPOINT` | Portal → **Azure AI services** / Foundry resource → **Keys and Endpoint** → Endpoint |
| `AZURE_AI_KEY` | Same blade → **Key 1** |
| `JWT_SECRET` | Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_API_TOKEN` | Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `SUPER_ADMIN_EMAILS` | Same as pipeline `adminEmails` |
| `AI_RERANK_MODEL` | `gpt4omini` |
| `AI_PARSE_MODEL` | `gpt41` |
| `AI_REVIEW_MODEL` | `gpt4omini` |

**CLI one-shot** (replace names):

```bash
az functionapp config appsettings set \
  -g rg-autoapply-dev -n autoapply-func-dev \
  --settings \
    COSMOS_ENDPOINT="https://....documents.azure.com:443/" \
    COSMOS_KEY="..." \
    COSMOS_DATABASE="autoapply" \
    BLOB_CONNECTION_STRING="..." \
    AZURE_AI_ENDPOINT="https://....services.ai.azure.com/" \
    AZURE_AI_KEY="..." \
    JWT_SECRET="..." \
    ADMIN_API_TOKEN="..." \
    SUPER_ADMIN_EMAILS="you@example.com"
```

**Local dev:** same keys in `api/local.settings.json` (gitignored).

---

## Part C — What gets updated in the repo when you provide values

When you paste values (chat or `tools/pipeline-variables.env`), the agent/script can:

| Target | Variables used |
|--------|----------------|
| `azure-pipelines.yml` | `functionAppName`, `resourceGroup`, `apiBaseUrl` |
| `extension/*.js` via `tools/configure-extension.sh` | `GOOGLE_CLIENT_ID`, `API_BASE_URL`, `SWA_HOST` |
| `tools/pipeline-variables.env` | Local copy only (gitignored) — your source of truth |

**Cannot be committed:** secrets (`googleClientId`, SWA token, Cosmos keys, JWT). Those stay in Azure DevOps or Function App settings only.

---

## Part D — Checklist before first deploy

- [ ] Pipeline created from `azure-pipelines.yml`
- [ ] `azureSubscription` service connection exists and name matches
- [ ] `staticWebAppDeploymentToken` and `googleClientId` set in ADO (secret)
- [ ] `adminEmails` set in ADO and `SUPER_ADMIN_EMAILS` on Function App
- [ ] Function App has **staging** slot (pipeline swaps staging → production)
- [ ] Function App application settings filled (Part B)
- [ ] Google OAuth origins include SWA URL + localhost
- [ ] Push to `main` → BackendTest + FlutterTest green → Deploy stages run

**Smoke test after deploy:**

```bash
curl -fsS https://autoapply-func-dev.azurewebsites.net/api/v1/health
# Open https://<your-swa-host> in browser → sign up / login
```

---

## Part E — Paste template (send this back filled in)

Copy, fill in, and send in chat (you can redact secrets partially like `COSMOS_KEY=***last4` if you only want non-secret fields applied in-repo):

```text
# --- Azure DevOps pipeline ---
azureSubscription=
functionAppName=
resourceGroup=
apiBaseUrl=
googleClientId=
adminEmails=
staticWebAppDeploymentToken=

# --- Static Web App (hostname only, for extension) ---
swaHost=

# --- Optional ---
enableDeploy=true
runInfraDeploy=false

# --- Function App runtime (only if you want help with az CLI command) ---
COSMOS_ENDPOINT=
COSMOS_KEY=
BLOB_CONNECTION_STRING=
AZURE_AI_ENDPOINT=
AZURE_AI_KEY=
JWT_SECRET=
ADMIN_API_TOKEN=
```

After you send this, we can update the repo config and give you exact `az` / ADO commands for anything that must stay in the cloud.
