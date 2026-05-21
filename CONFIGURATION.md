# AutoApply Setup Configuration Guide

This document outlines what needs to be configured to make AutoApply end-to-end working.

## Critical Placeholders That Must Be Replaced

### 1. Extension Files (Chrome)
- **Files affected:** `manifest.json`, `background.js`, `content.js`, `popup.js`, `options.js`
- **Placeholders:**
  - `<your-function-app>.azurewebsites.net` → Function App hostname
  - `<your-static-web-app>.azurestaticapps.net` → Static Web App hostname
- **How to fix:** Run `pwsh tools/setup-local-dev.ps1` or use find/replace script

### 2. Flutter Web App (Config)
- **File:** `app/lib/config/azure_config.dart`
- **Placeholders:**
  - `API_BASE_URL` environment variable → Set via `--dart-define` flag
  - `GOOGLE_CLIENT_ID` environment variable → Set via `--dart-define` flag
- **How to fix:** Pass flags at build/run time: `--dart-define=API_BASE_URL="https://..."` `--dart-define=GOOGLE_CLIENT_ID="..."`

### 3. Backend Configuration (Python)
- **File:** `api/local.settings.json` (git-ignored, must be created)
- **Required keys:**
  - `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE`
  - `BLOB_CONNECTION_STRING`
  - `AZURE_AI_ENDPOINT`, `AZURE_AI_KEY`
  - `JWT_SECRET`, `ADMIN_API_TOKEN` (generate with Python secrets module)
  - `SUPER_ADMIN_EMAILS`
  - Model names: `AI_RERANK_MODEL`, `AI_PARSE_MODEL`, `AI_REVIEW_MODEL`
- **How to fix:** Copy `api/local.settings.json.template`, fill in actual values, save as `api/local.settings.json`

### 4. CI/CD Pipeline (Optional)
- **File:** `azure-pipelines.yml`
- **Placeholders:** Service connection name, resource group, function app name
- **How to fix:** Edit file or inject as pipeline variables (only needed for Azure DevOps)

## Setup Methods

### Method 1: Automated (Recommended)
Run the PowerShell setup script:
```powershell
pwsh tools/setup-local-dev.ps1
```
This will prompt for Azure resource info and automatically create/configure all local files.

### Method 2: Manual
1. Copy `api/local.settings.json.template` to `api/local.settings.json`
2. Edit the file with your actual Azure credentials
3. Run find/replace on extension files to swap placeholder URLs
4. Run `py -3.11 -m venv api/.venv && .venv\Scripts\pip install -r requirements.txt`

## Resources Created

- `SETUP_LOCAL.md` — Complete setup guide with all steps explained
- `api/local.settings.json.template` — Template for backend configuration
- `tools/setup-local-dev.ps1` — Automated setup script

## Testing Checklist

After setup:
- [ ] `func start` runs without errors and `/api/v1/health` returns 200
- [ ] Flutter app compiles and loads with correct API endpoint
- [ ] Extension loads in Chrome without manifest errors
- [ ] API endpoints are reachable from both frontend and extension

## Key Files to Understand

- `api/function_app.py` — All HTTP routes defined here
- `api/shared/` — Core services (auth, embeddings, Cosmos, scrapers)
- `app/lib/services/api_service.dart` — Flutter API client
- `app/lib/providers/` — Flutter state management
- `extension/background.js` — Extension service worker
- `infra/main.bicep` — Infrastructure as Code template
