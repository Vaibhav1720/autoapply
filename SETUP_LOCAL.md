# AutoApply Local Setup Guide

This guide helps you set up AutoApply for local development without needing to manually edit configuration files.

## Prerequisites

1. **Tools Required** (one-time install):
   - Python 3.11.x (NOT 3.12+)
   - Node.js 20 LTS
   - Flutter 3.11+
   - Azure CLI
   - Azure Functions Core Tools v4

2. **Azure Resources** (must be provisioned first):
   - Resource Group
   - Cosmos DB
   - Azure Storage Account
   - Azure AI/OpenAI Foundry
   - Azure Function App
   - Static Web App

## Quick Start

### Step 1: Gather Your Azure Resource Information

After provisioning your Azure resources, gather these values:

```powershell
# Example values (replace with your own)
$API_HOST = "myapp-func-dev.azurewebsites.net"
$SWA_HOST = "kind-bay-12345.azurestaticapps.net"
$COSMOS_ENDPOINT = "https://myapp-cosmos.documents.azure.com:443/"
$COSMOS_KEY = "your-cosmos-key-here"
$BLOB_CONN = "DefaultEndpointsProtocol=https;AccountName=mystorageaccount..."
$AI_ENDPOINT = "https://myapp-ai.cognitiveservices.azure.com/"
$AI_KEY = "your-ai-key-here"

# Generate secrets (run in PowerShell)
$JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
$ADMIN_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Step 2: Configure Backend (Python)

```powershell
cd api

# Create virtual environment
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Create local.settings.json from template
# Use the values from Step 1
Copy-Item local.settings.json.template local.settings.json

# Edit local.settings.json and replace all placeholders with your actual values
# Then test:
func start
# In another terminal: curl http://localhost:7071/api/v1/health
```

### Step 3: Configure Frontend (Flutter)

```powershell
cd app

flutter pub get

# Create Google OAuth Client ID
# Go to: https://console.cloud.google.com/apis/credentials
# Create OAuth 2.0 Client ID (Web)
# Add authorized origins:
#   - https://$SWA_HOST
#   - http://localhost:*

# Run locally (replace placeholders):
flutter run -d chrome `
  --dart-define=API_BASE_URL="https://$API_HOST" `
  --dart-define=GOOGLE_CLIENT_ID="<your-google-oauth-web-client-id>.apps.googleusercontent.com" `
  --dart-define=ADMIN_EMAILS="you@example.com"
```

### Step 4: Configure Chrome Extension

```powershell
# Use the provided script to replace placeholders
# First, update extension URLs in extension/*.js, *.json, *.html files

$apiHost = "myapp-func-dev.azurewebsites.net"
$swaHost = "kind-bay-12345.azurestaticapps.net"

Get-ChildItem extension -File -Recurse |
  Where-Object { $_.Extension -in '.js','.json','.html' } |
  ForEach-Object {
    (Get-Content -Raw $_.FullName) `
      -replace '<your-function-app>\.azurewebsites\.net', $apiHost `
      -replace '<your-static-web-app>\.azurestaticapps\.net', $swaHost |
      Set-Content -NoNewline $_.FullName
  }

# Load the extension:
# 1. chrome://extensions → toggle Developer mode
# 2. Load unpacked → select extension/ folder
```

### Step 5: Build Extension Zip (for Flutter app download button)

```powershell
# Must run AFTER step 4 (after placeholders are replaced)
cd <repo-root>
pwsh tools/build_extension_zip.ps1
# Output: app/web/autoapply-extension.zip
```

## Deployment

### Deploy Backend to Azure

```powershell
cd api
func azure functionapp publish $FUNCAPP --python

# Set live configuration
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

### Deploy Frontend to Azure

```powershell
cd app

flutter build web --release `
  --dart-define=API_BASE_URL="https://$API_HOST" `
  --dart-define=GOOGLE_CLIENT_ID="<your-google-client-id>.apps.googleusercontent.com" `
  --dart-define=ADMIN_EMAILS="you@example.com"

$SWA_NAME = az staticwebapp list -g $RG --query "[0].name" -o tsv
$SWA_TOKEN = az staticwebapp secrets list --name $SWA_NAME --query "properties.apiKey" -o tsv
npx -y @azure/static-web-apps-cli deploy ./build/web --deployment-token $SWA_TOKEN --env default
```

## Smoke Test

```powershell
# Test API
curl "https://$API_HOST/api/v1/health"

# Test sign-up
curl -X POST "https://$API_HOST/api/v1/auth/signup" `
  -H "Content-Type: application/json" `
  -d '{"email":"test@example.com","password":"TestPass123!"}'

# Test web app
Start-Process "https://$SWA_HOST"
```

## Troubleshooting

### "Chrome refuses to load the extension"
- Manifest.json has invalid match pattern
- Check that `<your-function-app>.azurewebsites.net` was replaced with actual URL

### Extension can't reach backend
- Check extension options (click extension icon → Options)
- Set API base URL to correct `https://$API_HOST`
- Verify JWT token is stored (should auto-populate after login)

### "Daily limit reached" on Discover
- Set `profile.tier='admin'` in Cosmos DB (in profiles container)
- Or increase `FREE_TIER_DAILY_DISCOVER_LIMIT` in Function App settings

### Flutter web app won't load profile
- Check `--dart-define=API_BASE_URL` matches your backend
- Verify JWT token is saved in local storage
- Check browser console for CORS errors

## Configuration Reference

| File | Placeholder | How to Replace |
|---|---|---|
| `extension/manifest.json` | `<your-function-app>.azurewebsites.net` | Run find/replace script in Step 4 |
| `extension/*.js` | `DEFAULT_API_BASE` | Run find/replace script in Step 4 |
| `extension/content.js:822` | Upgrade URL | Run find/replace script in Step 4 |
| `app/lib/config/azure_config.dart` | `--dart-define` flags | Pass at build/run time (Step 3) |
| `api/local.settings.json` | All values | Copy from template, fill in actual values |
| `azure-pipelines.yml` | Service connection, RG, Function App name | Only needed if using Azure DevOps |

## Complete End-to-End Test Checklist

- [ ] Python venv created and dependencies installed
- [ ] `api/local.settings.json` created with real credentials
- [ ] `func start` runs and `/api/v1/health` returns 200
- [ ] Flutter app runs locally and loads profile
- [ ] Extension loaded unpacked in Chrome
- [ ] Extension can autofill a form
- [ ] Google OAuth sign-in works on web app
- [ ] Discover page shows companies and ranks jobs
- [ ] Backend deployed to Azure Function App
- [ ] Frontend built and deployed to Static Web App
- [ ] Live URLs work end-to-end

---

**See [SETUP.md](SETUP.md) for full reference documentation.**
