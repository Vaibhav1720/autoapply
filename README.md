# AutoApply

> AI-powered job-search aggregator + autofill assistant.
> Scrapes major company career sites + LinkedIn, ranks openings against your resume via vector embeddings + LLM rerank, tailors your resume per role, and ships a Chrome extension that autofills application forms.

**Live URLs are deployment-specific.** Replace `<your-function-app>` and `<your-static-web-app>` placeholders below with your own resource names after deploying.

---

## 1. Architecture at a glance

```
┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
│  Flutter web     │  │  Chrome ext.     │  │  (future) Mobile    │
│  (app/)          │  │  (extension/)    │  │                     │
└────────┬─────────┘  └────────┬─────────┘  └──────────┬──────────┘
         │                     │                       │
         └──────────────┬──────┴───────────────────────┘
                        │  HTTPS  /api/v1/*
                        ▼
        ┌────────────────────────────────────┐
        │  Azure Functions  (Python 3.11)    │   ← api/
        │   - auth, profile, jobs/discover,  │
        │     resume tailor, autofill,       │
        │     LinkedIn search, admin         │
        └────┬───────────┬──────────┬────────┘
             │           │          │
             ▼           ▼          ▼
        ┌─────────┐ ┌─────────┐ ┌──────────────────┐
        │ Cosmos  │ │ Storage │ │ Azure AI Foundry │
        │  DB     │ │  Blob   │ │  gpt-4.1, o4-mini │
        │         │ │ (resume)│ │  text-embed-3-lg  │
        └─────────┘ └─────────┘ └──────────────────┘
```

| Layer | Tech | Folder |
|---|---|---|
| Backend | Azure Functions, Python 3.11, v2 model | `api/` |
| Frontend | Flutter web | `app/` |
| Browser ext. | Chrome MV3 | `extension/` |
| Infra | Bicep (one file) | `infra/main.bicep` |
| Eval | pytest + regression harness | `api/tests/`, `api/eval/` |

---

## 2. Prerequisites — install on a brand-new machine

### 2.1 Required tooling

| Tool | Version | Install (Windows) | Install (macOS) |
|---|---|---|---|
| Git | latest | `winget install Git.Git` | `brew install git` |
| Python | 3.11.x (NOT 3.12+) | https://www.python.org/downloads/release/python-3119/ | `brew install python@3.11` |
| Node.js + npm | 20 LTS | `winget install OpenJS.NodeJS.LTS` | `brew install node@20` |
| Flutter SDK | 3.11+ (stable) | https://docs.flutter.dev/get-started/install/windows → unzip to `C:\flutter` | `brew install --cask flutter` |
| Azure CLI | latest | `winget install Microsoft.AzureCLI` | `brew install azure-cli` |
| Azure Functions Core Tools | v4 | `npm i -g azure-functions-core-tools@4 --unsafe-perm true` | same |
| Azure Static Web Apps CLI | latest | `npm i -g @azure/static-web-apps-cli` | same |
| Bicep CLI | bundled with `az` | `az bicep install` | same |
| (optional) Chrome | for ext. testing | `winget install Google.Chrome` | n/a |
| (optional) VS Code | for editing | `winget install Microsoft.VisualStudioCode` | `brew install --cask visual-studio-code` |

### 2.2 Verify

```pwsh
git --version
python --version            # must say 3.11.x
node --version              # v20.x
flutter --version
az --version
func --version              # v4.x
swa --version
```

### 2.3 Azure account

1. Free Azure account: https://azure.microsoft.com/free (gives $200 credit + always-free tier — covers the entire stack at AutoApply scale).
2. After signup, you get a **Subscription ID**. Keep it handy.
3. Sign in locally:
   ```pwsh
   az login
   az account set --subscription "<your-subscription-id>"
   ```

---

## 3. Clone & first-time setup

```pwsh
git clone <your-fork-url> AutoApply
cd AutoApply
```

The repo layout:

```
AutoApply/
├── api/              Azure Functions backend (Python)
├── app/              Flutter web frontend
├── extension/        Chrome MV3 extension
├── infra/main.bicep  All Azure resources (Cosmos, Storage, AI, Function App, SWA)
├── tools/            Build helpers (extension zip, screenshot resize)
├── scripts/          Misc PowerShell helpers
├── docs/             Design docs
└── README.md         (this file)
```

---

## 4. Provision Azure infrastructure (one-time, ~10 min)

### 4.1 Create resource group

```pwsh
$RG = "<your-resource-group>"
$LOC = "eastus2"
az group create --name $RG --location $LOC
```

### 4.2 Deploy Bicep

```pwsh
az deployment group create `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters envName=dev location=$LOC
```

What this creates:

| Resource | SKU / Plan | Purpose |
|---|---|---|
| `<your-cosmos-account>` | Cosmos DB (Free Tier, 1000 RU/s shared) | Profiles, jobs cache, usage events |
| `<your-storage-account>` | Storage v2 (Standard_LRS) | Resume blobs, function host storage |
| `<your-ai-resource>` | AI Foundry (S0) | LLM + embeddings |
| `<your-function-app>` | Functions Flex Consumption (Python 3.11) | API |
| `autoapply-web-dev` | Static Web App (Free) | Flutter web hosting |
| `autoapply-insights-dev` | App Insights | Telemetry |

> **Free tier Cosmos**: only 1 free-tier account allowed per subscription. If you've used it elsewhere, edit `infra/main.bicep` and set `enableFreeTier: false`.

### 4.3 Deploy AI models

The Bicep deploys the resource but not the models. From the Azure portal:

1. Navigate to `<your-ai-resource>` → **Model deployments** → **+ Deploy model**.
2. Deploy these (names matter — they're hard-coded as defaults in `api/services/_runtime.py`):
   | Deployment name | Model | Capacity (TPM) |
   |---|---|---|
   | `gpt41` | `gpt-4.1` | 50 |
   | `gpt4omini` | `gpt-4o-mini` | 100 |
   | `o4mini` | `o4-mini` | 50 |
   | `text-embedding-3-large` | `text-embedding-3-large` | 50 |

3. Copy the resource **Endpoint** and **Key 1** from the Keys & endpoint blade — you'll need these for app settings.

### 4.4 Seed Cosmos containers

The Bicep creates the database `autoapply` and the following containers (with vector indexes / TTLs as needed):

`users`, `profiles`, `companies`, `applications`, `job_results`, `jobs`, `usage_events`

The first time the API runs it will lazily create any container missing.

---

## 5. Backend (Azure Functions) — local dev

```pwsh
cd api

# Create + activate Python 3.11 venv
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Create local.settings.json from the template below
# (NEVER commit this file)
```

### 5.1 `api/local.settings.json` template

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",

    "COSMOS_ENDPOINT": "https://<your-cosmos-account>.documents.azure.com:443/",
    "COSMOS_KEY": "<from `az cosmosdb keys list -n <your-cosmos-account> -g <your-resource-group> --query primaryMasterKey -o tsv`>",
    "COSMOS_DATABASE": "autoapply",

    "BLOB_CONNECTION_STRING": "<from `az storage account show-connection-string -n <your-storage-account> -g <your-resource-group> -o tsv`>",

    "AZURE_AI_ENDPOINT": "https://<your-ai-resource>.cognitiveservices.azure.com/",
    "AZURE_AI_KEY": "<key1 from Foundry portal>",

    "JWT_SECRET": "<generate: `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`>",

    "AI_RERANK_MODEL": "gpt4omini",
    "AI_PARSE_MODEL": "gpt41",
    "AI_REVIEW_MODEL": "gpt4omini",
    "RERANK_SKIP_GAP": "15",
    "FREE_TIER_DAILY_DISCOVER_LIMIT": "50",
    "RESUME_TAILOR_MAX_JOBS": "50",

    "ADMIN_API_TOKEN": "<generate another secret>",

    "LEMONSQUEEZY_API_KEY": "<from app.lemonsqueezy.com → Settings → API>",
    "LEMONSQUEEZY_STORE_ID": "<numeric Store ID from Settings → General>",
    "LEMONSQUEEZY_WEBHOOK_SECRET": "<generated when you create the webhook>",
    "LEMONSQUEEZY_VARIANT_PRO_WEEKLY": "<variant ID for $3.49/wk plan>",
    "LEMONSQUEEZY_VARIANT_PRO_MONTHLY": "<variant ID for $9.99/mo plan>",
    "LEMONSQUEEZY_VARIANT_PRO_YEARLY": "<variant ID for $89.99/yr plan>",
    "BILLING_SUCCESS_URL": "https://<your-static-web-app>.azurestaticapps.net/#/billing/success"
  },
  "Host": {
    "CORS": "*",
    "CORSCredentials": false
  }
}
```

### 5.2 Run locally

```pwsh
cd api
func start
# → http://localhost:7071/api/v1/health  should return {"status":"ok"}
```

### 5.3 Deploy to Azure

```pwsh
cd api
func azure functionapp publish <your-function-app> --python
```

That's it — Oryx remote build resolves wheels, the package is uploaded, and the Function App restarts. Typical end-to-end time: ~3 min.

### 5.4 Set production app settings (one-time)

```pwsh
$RG = "<your-resource-group>"
$APP = "<your-function-app>"

az functionapp config appsettings set --name $APP --resource-group $RG --settings `
  COSMOS_ENDPOINT="..." `
  COSMOS_KEY="..." `
  COSMOS_DATABASE="autoapply" `
  BLOB_CONNECTION_STRING="..." `
  AZURE_AI_ENDPOINT="..." `
  AZURE_AI_KEY="..." `
  JWT_SECRET="..." `
  ADMIN_API_TOKEN="..." `
  AI_RERANK_MODEL="gpt4omini" `
  AI_PARSE_MODEL="gpt41" `
  AI_REVIEW_MODEL="gpt4omini" `
  RERANK_SKIP_GAP="15" `
  FREE_TIER_DAILY_DISCOVER_LIMIT="50" `
  RESUME_TAILOR_MAX_JOBS="50"
```

### 5.5 Run tests

```pwsh
cd api
.\.venv\Scripts\Activate.ps1
pytest tests/ -q
```

### 5.6 Lemon Squeezy billing setup (one-time)

AutoApply Pro is sold as a recurring subscription via [Lemon Squeezy](https://lemonsqueezy.com),
the Merchant of Record (they collect global VAT/GST/sales tax for you).

**Pricing** (set in code at `api/services/billing/routes.py`):

| Plan         | Price    | Config |
| ------------ | -------- | ------ |
| Free         | $0       | — |
| Pro Weekly (intl) | $0.99/wk | `LEMONSQUEEZY_VARIANT_PRO_WEEKLY`, `LEMONSQUEEZY_CHECKOUT_PRO_WEEKLY` |
| Pro Monthly (India) | ₹199/mo | `RAZORPAY_PLAN_PRO_MONTHLY` |

**Step-by-step setup:**

1. **Create an account** at <https://app.lemonsqueezy.com/register> and verify your email.
2. **Create a store** named `AutoApply` (Settings → Stores → New). You don't need to enter
   business / tax info to start — that's only required to leave Test Mode.
3. **Toggle Test Mode ON** (top-right of dashboard). All steps below use test data; flip the
   toggle off when you're ready to take real money.
4. **Create the product:**
   - Products → New → Subscription
   - Name: `AutoApply Pro`
   - Description: `Unlimited job searches, AI autofill, full resume tailoring`
   - Tax category: `SaaS / Software`
   - Add **one variant**:
     - `Weekly` — $0.99 — billing interval: 1 week, recurring
     - Checkout URL: `https://autoapplypayment.lemonsqueezy.com/checkout/buy/31d6a9da-598d-4372-ae7f-3c58d360b61d`
   - Save. Copy the numeric **variant IDs** (visible in the URL or variant settings).
5. **Get API credentials:**
   - Settings → API → "Create API key" → copy the `LEMONSQUEEZY_API_KEY` value.
   - Settings → General → copy the `Store ID` (a number like `12345`).
6. **Create the webhook:**
   - Settings → Webhooks → "+ Create"
   - URL: `https://<your-function-app>.azurewebsites.net/api/v1/webhooks/lemonsqueezy`
   - Signing secret: click "Generate" and copy it → `LEMONSQUEEZY_WEBHOOK_SECRET`
   - Subscribe to **all** of: `subscription_created`, `subscription_updated`, `subscription_cancelled`,
     `subscription_resumed`, `subscription_expired`, `subscription_paused`, `subscription_unpaused`,
     `subscription_payment_success`, `subscription_payment_failed`, `order_created`.
7. **Push the env vars to Azure** (replace placeholders):

   ```pwsh
   az functionapp config appsettings set `
     --name <your-function-app> `
     --resource-group <your-resource-group> `
     --settings `
       LEMONSQUEEZY_API_KEY="<key from step 5>" `
       LEMONSQUEEZY_STORE_ID="<store id from step 5>" `
       LEMONSQUEEZY_WEBHOOK_SECRET="<secret from step 6>" `
       LEMONSQUEEZY_VARIANT_PRO_WEEKLY="<weekly variant id from step 4>" `
       LEMONSQUEEZY_VARIANT_PRO_MONTHLY="<monthly variant id from step 4>" `
       LEMONSQUEEZY_VARIANT_PRO_YEARLY="<yearly variant id from step 4>" `
       BILLING_SUCCESS_URL="https://<your-static-web-app>.azurestaticapps.net/#/billing/success"
   ```

8. **End-to-end test (Test Mode):**
   - Open the app → Profile → "Subscription & billing" → click a Pro plan → "Subscribe".
   - Use test card `4242 4242 4242 4242`, any future expiry, any CVC, any ZIP.
   - You should land on `/billing/success`, the page polls `/api/v1/billing/subscription` and
     flips the badge to **Pro** within ~5 seconds.
   - Verify the webhook fired: Lemon Squeezy → Settings → Webhooks → click your endpoint →
     check "Recent deliveries" — all should be `200 OK`.
9. **Go live:**
   - Toggle Test Mode OFF.
   - Complete the payout/tax forms when prompted (W-8BEN for non-US sellers; bank or Wise
     account for payouts).
   - **Re-create variants in live mode** (test variant IDs do not work in live mode) and
     update the env vars again.
   - Re-register the webhook in live mode (the secret changes).

**Free vs. Pro tier behaviour** (defined in `api/services/_runtime.py`):

- `tier == "free"`: daily quotas on `/discover`, `/linkedin/search`, `/autofill`.
- `tier in ("pro", "lifetime", "admin")`: all quotas bypassed.

The webhook handler (`api/services/billing/routes.py::_handle_event`) writes the
authoritative subscription record to the `subscriptions` Cosmos container and
mirrors `tier`/`status`/`renewsAt`/`endsAt` onto the user's profile so the app can
read it without an extra round-trip.

---

## 6. Frontend (Flutter web)

### 6.1 Configure API base

Edit `app/lib/config/azure_config.dart` and set `apiBase` to your Function App URL:

```dart
class AzureConfig {
  static const String apiBase = 'https://<your-function-app>.azurewebsites.net';
}
```

### 6.2 Run locally

```pwsh
cd app
flutter pub get
flutter run -d chrome
# → opens http://localhost:<random>
```

### 6.3 Build production bundle

```pwsh
cd app
flutter build web --release
# Output: app/build/web/
```

### 6.4 Deploy to Static Web App

```pwsh
cd app
$token = az staticwebapp secrets list --name autoapply-web-dev --query "properties.apiKey" -o tsv
npx -y @azure/static-web-apps-cli deploy ./build/web --deployment-token $token --env default
```

The `--env default` flag publishes to the production slot at `https://<your-swa>.azurestaticapps.net`.

---

## 7. Chrome extension

### 7.1 Configure backend URL

The extension reads its API base from `chrome.storage.local.autoapply_api_base`. Default is hard-coded in `extension/background.js` (`DEFAULT_API_BASE`) — update it to your Function App URL before zipping for the Web Store.

### 7.2 Load unpacked (development)

1. Open `chrome://extensions`.
2. Toggle **Developer mode** on.
3. Click **Load unpacked** → select the `extension/` folder.
4. Pin AutoApply to the toolbar.

### 7.3 Build distributable zip

```pwsh
pwsh tools/build_extension_zip.ps1
# Output: app/web/autoapply-extension.zip  (also published with the SWA)
```

### 7.4 Publish to Chrome Web Store

1. One-time $5 fee at https://chrome.google.com/webstore/devconsole.
2. Upload `app/web/autoapply-extension.zip`.
3. Provide privacy URL: `https://<your-swa>.azurestaticapps.net/privacy.html`.
4. Provide store screenshots (1280×800), short + long description, justify each permission.
5. Submit for review (1-7 days).

---

## 8. End-to-end smoke test (after fresh deploy)

```pwsh
# 1. API health
curl https://<your-function-app>.azurewebsites.net/api/v1/health
# → {"status":"ok"}

# 2. Sign up
curl -X POST https://<your-function-app>.azurewebsites.net/api/v1/auth/signup `
  -H "Content-Type: application/json" `
  -d '{"email":"smoke@test.com","password":"smoke123!"}'
# → {"data":{"token":"eyJ...","userId":"user-..."}}

# 3. Open the web app, sign in, upload a resume PDF, click Discover.
```

If Discover returns jobs ranked by `aiScore` and the resume tailor button produces tailored suggestions, the deploy is healthy.

---

## 9. Common operations

### Tail logs

```pwsh
az functionapp log tail --name <your-function-app> --resource-group <your-resource-group>
# or in the portal: Function App → Log stream
```

### Reset a user's daily quota

```pwsh
# In Cosmos Data Explorer, run on the `usage_events` container:
SELECT * FROM c WHERE c.userId = 'user-XXXXXXXX' AND c.day = '2026-05-14'
# Delete the matching docs, or set profile.tier='admin' on the `profiles` container to bypass.
```

### Rotate JWT_SECRET

> **Warning:** invalidates every existing user session.

```pwsh
$NEW = python -c "import secrets; print(secrets.token_urlsafe(48))"
az functionapp config appsettings set --name <your-function-app> --resource-group <your-resource-group> --settings JWT_SECRET=$NEW
```

### Scale up Cosmos throughput (if RU 429s appear)

```pwsh
az cosmosdb sql database throughput update `
  --account-name <your-cosmos-account> --resource-group <your-resource-group> `
  --name autoapply --max-throughput 4000
```

---

## 10. Cost guardrails

Default deployment fits in **~$5-30/month** at low traffic:

| Component | Free tier covers | When you start paying |
|---|---|---|
| Cosmos DB | 1000 RU/s + 25 GB free | After 1000 RU/s sustained or > 25 GB data |
| Storage | 5 GB free | After 5 GB resumes |
| Functions Flex | 100K req/month free | After that, ~$0.20 per million GB-s |
| Static Web App | 100 GB bandwidth free | After 100 GB/mo egress |
| AI Foundry | Pay per token | Always (~$0.001 per Discover thanks to cache) |
| App Insights | 5 GB ingest free | After 5 GB/mo logs |

### Hard limits to set (recommended)

```pwsh
# Set a $50/month budget alert
az consumption budget create `
  --budget-name autoapply-monthly --amount 50 --time-grain Monthly `
  --start-date 2026-05-01 --end-date 2027-05-01 `
  --resource-group <your-resource-group>
```

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `func azure functionapp publish` warns "Local python version 3.x is different from 3.11" | You're on Python 3.12+ | Install Python 3.11 or use a 3.11 venv when publishing. Code still deploys (Oryx builds remotely with the right version). |
| Discover returns "Daily limit reached" instantly | Hit `FREE_TIER_DAILY_DISCOVER_LIMIT` | Set `profile.tier='admin'` on your user in Cosmos, or wait for 00:00 UTC. |
| Flutter `dart:html unsupported` warning | wasm dry-run noise | Safe to ignore — JS build still succeeds. |
| Extension popup says "API offline" | `apiBase` mismatch | In extension popup → Settings → set the right URL, or rebuild with the corrected `DEFAULT_API_BASE`. |
| Cosmos `403 Forbidden` from Function App | Stale `COSMOS_KEY` after rotation | `az cosmosdb keys regenerate ...` then update app settings. |
| AI calls 401 | `AZURE_AI_KEY` rotated or wrong endpoint | Re-copy from Foundry portal → Keys and endpoint blade. |

---

## 12. Project state & design docs

- `PROJECT_STATE.md` — living spec (cosmos schema, route table, change log, roadmap)
- `docs/DESIGN_DOCUMENT.txt`, `docs/DESIGN_V2.txt` — original design
- `app/README.md` — Flutter-specific notes

---

## 13. License

Proprietary — all rights reserved (until a license file is added).
