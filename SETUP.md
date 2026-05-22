# AutoApply — Setup Checklist

> Will the code run after cloning? **No, not until you fill in your own deployment values.**
> Sensitive URLs / IDs / credentials were stripped from this repo. Everywhere you see a `<placeholder>` token in source, you must replace it (or override it via env vars / build flags) before anything will work.
>
> This file is the minimum action list. The full reference docs live in [README.md](README.md).

---

## 0. What's broken today (and why)

| File | Placeholder it now contains | What breaks if you don't fix it |
|---|---|---|
| `extension/manifest.json` | `https://<your-function-app>.azurewebsites.net/*` in `host_permissions` | **Chrome refuses to load the extension** — invalid match pattern. |
| `extension/background.js`, `content.js`, `options.js`, `popup.js` | `DEFAULT_API_BASE = "https://<your-function-app>..."` | Extension can't reach the backend. |
| `extension/content.js` (line ~822) | `window.open("https://<your-static-web-app>...")` | "Upgrade" link is a dead URL. |
| `app/lib/config/azure_config.dart` | `apiBaseUrl` default + `googleClientId` default | Web app calls go to a non-existent host; Google sign-in button can't render. |
| `app/lib/screens/main_shell.dart`, `admin/admin_dashboard_screen.dart` | empty `ADMIN_EMAILS` dart-define | Admin tab hidden for everyone (intended fallback). |
| `api/local.settings.json` | **file deleted** | `func start` won't have any Cosmos / Storage / AI / JWT config. |
| `api/services/_runtime.py` | `SUPER_ADMIN_EMAILS` env default = `""` | No automatic admin promotion (intended). |
| `azure-pipelines.yml` | `<placeholder>` for service connection / RG / function app | Pipeline run fails on first task. |
| `infra/main.bicep` | resource name defaults are `myapp-*-${env}` | Will deploy, but **storage account names are globally unique** — you may hit a collision. Pass `-p storageAccountName=<unique>` to `az deployment`. |
| `extension.pem` | **deleted** (extension signing key) | You can still load the extension unpacked; you'll need a fresh key (auto-generated) if you publish to the Chrome Web Store. |
| `app/web/autoapply-extension.zip` | **deleted** (pre-built bundle had baked-in URLs) | The in-app **Download Extension** button on the Profile screen will return 404 until you regenerate the zip — see [step 7](#7-chrome-extension). |
| `api/.venv/`, `api/.venv311/`, all `__pycache__/` | **deleted** (had absolute paths in `.pyc` files) | Recreated automatically when you run `python -m venv` and `pip install` in [step 4](#4-backend--local-dev). |

Everything else (function code, Flutter screens, scrapers, evals) is intact and unchanged.

---

## 1. Prerequisites (one-time, per machine)

Install: Git, Python 3.11.x (NOT 3.12+), Node 20 LTS, Flutter 3.11+, Azure CLI, Azure Functions Core Tools v4. See [README.md §2](README.md) for OS-specific commands.

```pwsh
az login
az account set --subscription "<your-subscription-id>"
```

---

## 2. Provision Azure resources (~10 min)

```pwsh
$RG = "rg-myapp-dev"
$LOC = "eastus2"

az group create --name $RG --location $LOC

# Storage names must be globally unique — append a short random suffix.
$STOR = "myappstor$((Get-Random -Maximum 9999))"

az deployment group create `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters envName=dev location=$LOC storageAccountName=$STOR
```

This creates: Cosmos DB (free tier), Storage, Function App (Consumption Python 3.11), AI Foundry, Static Web App, App Insights.

**Then deploy the AI models** (portal → AI Foundry resource → Model deployments). Names are hard-coded in `api/services/_runtime.py`:

| Deployment name | Model | TPM |
|---|---|---|
| `gpt41` | gpt-4.1 | 50 |
| `gpt4omini` | gpt-4o-mini | 100 |
| `o4mini` | o4-mini | 50 |
| `text-embedding-3-large` | text-embedding-3-large | 50 |

---

## 3. Capture the values you'll reuse everywhere

Run these and keep the output handy:

```pwsh
# Function App URL (drop the https://)
$FUNCAPP = az deployment group show -g $RG -n main --query "properties.outputs.functionAppName.value" -o tsv
$API_HOST = "$FUNCAPP.azurewebsites.net"

# Static Web App default hostname
$SWA_HOST = az staticwebapp show -g $RG --name (az staticwebapp list -g $RG --query "[0].name" -o tsv) --query "defaultHostname" -o tsv

# Cosmos / Storage / AI keys
$COSMOS_NAME = az cosmosdb list -g $RG --query "[0].name" -o tsv
$COSMOS_ENDPOINT = "https://$COSMOS_NAME.documents.azure.com:443/"
$COSMOS_KEY = az cosmosdb keys list -n $COSMOS_NAME -g $RG --query primaryMasterKey -o tsv
$BLOB_CONN = az storage account show-connection-string -n $STOR -g $RG -o tsv
$AI_NAME = az cognitiveservices account list -g $RG --query "[0].name" -o tsv
$AI_ENDPOINT = "https://$AI_NAME.cognitiveservices.azure.com/"
$AI_KEY = az cognitiveservices account keys list -n $AI_NAME -g $RG --query key1 -o tsv

# Generate JWT secret + admin token
$JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
$ADMIN_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"

Write-Host "API_HOST   = $API_HOST"
Write-Host "SWA_HOST   = $SWA_HOST"
Write-Host "COSMOS_KEY = (set)"
```

---

## 4. Backend — local dev

```pwsh
cd api
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create **`api/local.settings.json`** (gitignored) — paste the values from step 3:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "COSMOS_ENDPOINT": "<COSMOS_ENDPOINT>",
    "COSMOS_KEY": "<COSMOS_KEY>",
    "COSMOS_DATABASE": "autoapply",
    "BLOB_CONNECTION_STRING": "<BLOB_CONN>",
    "AZURE_AI_ENDPOINT": "<AI_ENDPOINT>",
    "AZURE_AI_KEY": "<AI_KEY>",
    "JWT_SECRET": "<JWT_SECRET>",
    "ADMIN_API_TOKEN": "<ADMIN_TOKEN>",
    "AI_RERANK_MODEL": "gpt4omini",
    "AI_PARSE_MODEL": "gpt41",
    "AI_REVIEW_MODEL": "gpt4omini",
    "RERANK_SKIP_GAP": "15",
    "FREE_TIER_DAILY_DISCOVER_LIMIT": "50",
    "RESUME_TAILOR_MAX_JOBS": "50",
    "SUPER_ADMIN_EMAILS": "you@example.com"
  },
  "Host": { "CORS": "*", "CORSCredentials": false }
}
```

Run:

```pwsh
func start
# In another terminal:
curl http://localhost:7071/api/v1/health   # → {"status":"ok"}
```

---

## 5. Backend — deploy to Azure

```pwsh
cd api
func azure functionapp publish $FUNCAPP --python
```

Push the same settings to the live Function App (or a subset — see [README §5.4](README.md)):

```pwsh
az functionapp config appsettings set -g $RG -n $FUNCAPP --settings `
  COSMOS_ENDPOINT=$COSMOS_ENDPOINT `
  COSMOS_KEY=$COSMOS_KEY `
  COSMOS_DATABASE=autoapply `
  BLOB_CONNECTION_STRING="$BLOB_CONN" `
  AZURE_AI_ENDPOINT=$AI_ENDPOINT `
  AZURE_AI_KEY=$AI_KEY `
  JWT_SECRET=$JWT_SECRET `
  ADMIN_API_TOKEN=$ADMIN_TOKEN `
  AI_RERANK_MODEL=gpt4omini `
  AI_PARSE_MODEL=gpt41 `
  AI_REVIEW_MODEL=gpt4omini `
  RERANK_SKIP_GAP=15 `
  FREE_TIER_DAILY_DISCOVER_LIMIT=50 `
  RESUME_TAILOR_MAX_JOBS=50 `
  SUPER_ADMIN_EMAILS=you@example.com
```

---

## 6. Frontend (Flutter web)

The placeholders in `azure_config.dart` are read via `String.fromEnvironment`, so you don't have to edit the file — just pass `--dart-define` flags at build time.

```pwsh
cd app
flutter pub get

# Local dev
flutter run -d chrome `
  --dart-define=API_BASE_URL="https://$API_HOST" `
  --dart-define=GOOGLE_CLIENT_ID="<your-google-oauth-web-client-id>" `
  --dart-define=ADMIN_EMAILS="you@example.com"

# Production build
flutter build web --release `
  --dart-define=API_BASE_URL="https://$API_HOST" `
  --dart-define=GOOGLE_CLIENT_ID="<your-google-oauth-web-client-id>" `
  --dart-define=ADMIN_EMAILS="you@example.com"

# Deploy
$SWA_NAME = az staticwebapp list -g $RG --query "[0].name" -o tsv
$SWA_TOKEN = az staticwebapp secrets list --name $SWA_NAME --query "properties.apiKey" -o tsv
npx -y @azure/static-web-apps-cli deploy ./build/web --deployment-token $SWA_TOKEN --env default
```

> **Google OAuth web client ID:** create at <https://console.cloud.google.com/apis/credentials> → "OAuth 2.0 Client IDs" → Web. Add `https://$SWA_HOST` and `http://localhost:*` to **Authorized JavaScript origins**.

---

## 7. Chrome extension

The extension has 6 files with `<your-function-app>` placeholders. Easiest fix: a one-shot find/replace before zipping.

```pwsh
$apiHost = $API_HOST   # from step 3, e.g. "myapp-func-dev.azurewebsites.net"
$swaHost = $SWA_HOST   # e.g. "kind-bay-12345.azurestaticapps.net"

Get-ChildItem extension -File -Recurse |
  Where-Object { $_.Extension -in '.js','.json','.html' } |
  ForEach-Object {
    (Get-Content -Raw $_.FullName) `
      -replace '<your-function-app>\.azurewebsites\.net', $apiHost `
      -replace '<your-static-web-app>\.azurestaticapps\.net', $swaHost |
      Set-Content -NoNewline $_.FullName
  }
```

> **Don't commit those edits if you plan to keep the repo public** — they re-introduce your real URLs. Either keep the placeholders in git and run this script locally before each `Load unpacked` / build, or fork the repo to a private one.

Load it:

1. `chrome://extensions` → toggle **Developer mode**.
2. **Load unpacked** → select `extension/`.
3. Click the extension icon → **Options** → enter your API base URL there too (it persists in `chrome.storage.local` and overrides `DEFAULT_API_BASE`).

### 7a. Regenerate the bundled zip (required for in-app download button)

The Flutter app's Profile screen has a **Download Extension** button that serves `/autoapply-extension.zip` from the Static Web App. That zip was deleted from the repo (it had baked-in URLs from a prior build), so you must regenerate it **after** the find/replace above and **before** `flutter build web`:

```pwsh
pwsh tools/build_extension_zip.ps1
# Output: app/web/autoapply-extension.zip
```

Then re-run `flutter build web` (step 6) so the new zip is included in the SWA deploy. Without this, the in-app download button returns 404.

For the Chrome Web Store: see [README §7.4](README.md). Upload the same `app/web/autoapply-extension.zip`. A new `extension.pem` will be auto-generated on first upload (the original is gone).

---

## 8. CI/CD (Azure DevOps Pipelines) — optional

Edit `azure-pipelines.yml` and replace:

| Placeholder | With |
|---|---|
| `azureSubscription: '<placeholder>'` | Your DevOps service connection name |
| `resourceGroup: '<placeholder>'` | `$RG` from step 2 |
| `functionAppName: '<placeholder>'` | `$FUNCAPP` from step 3 |

Or inject them as pipeline variables instead of editing the file.

---

## 9. (Optional) Lemon Squeezy billing

Only needed if you want the Pro subscription flow. See [README §5.6](README.md) for the full setup — create store, create product, generate API key, register webhook at `https://$API_HOST/api/v1/webhooks/lemonsqueezy`, push 6 env vars to the Function App.

If you skip this, all users stay on the free tier (50 discoveries/day cap).

---

## 10. Smoke test

```pwsh
# API
curl "https://$API_HOST/api/v1/health"

# Sign up
curl -X POST "https://$API_HOST/api/v1/auth/signup" `
  -H "Content-Type: application/json" `
  -d '{"email":"smoke@test.com","password":"smoke123!"}'

# Web app
Start-Process "https://$SWA_HOST"
```

If sign-up returns a token and the web app loads → you're done.

---

## Recap — what you must replace

```text
1. infra/main.bicep param overrides ............ at deploy time (-p storageAccountName=...)
2. api/local.settings.json ..................... create from template (step 4)
3. Function App settings (live) ................ az functionapp config appsettings set (step 5)
4. Flutter --dart-define flags ................. every build (step 6)
5. extension/*.{js,json,html} placeholders ..... script in step 7
6. app/web/autoapply-extension.zip ............. regenerate via tools/build_extension_zip.ps1 (step 7a)
7. azure-pipelines.yml ......................... only if using Azure DevOps (step 8)
8. Lemon Squeezy env vars ...................... only if using Pro tier (step 9)
```

That's the full list. Nothing else needs to change.
