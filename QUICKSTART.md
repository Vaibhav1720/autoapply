#!/usr/bin/env pwsh
<#
.SYNOPSIS
    AutoApply Local Development - Quick Start Reference
    
.DESCRIPTION
    Copy this file to a notepad and follow the steps to get AutoApply running locally.
    
.NOTES
    All commands should be run from the repo root directory.
#>

# ╔═══════════════════════════════════════════════════════════════════════════════╗
# ║                      AutoApply Development - START HERE                        ║
# ╚═══════════════════════════════════════════════════════════════════════════════╝

# PREREQUISITES (Install once per machine)
# ─────────────────────────────────────────────────────────────────────────────
# See: https://github.com/Azure/azure-functions-python-worker/wiki/Python-Version-Support
#   □ Python 3.11.x (NOT 3.12+)
#   □ Node.js 20 LTS 
#   □ Flutter 3.11+
#   □ Azure CLI (az command)
#   □ Azure Functions Core Tools v4 (func command)

# STEP 1: Provision Azure Resources (if you haven't already)
# ─────────────────────────────────────────────────────────────────────────────
# Refer to SETUP.md § 1-2 for full instructions. You need:
#   □ Resource Group (e.g., rg-myapp-dev in eastus2)
#   □ Cosmos DB (free tier, database "autoapply")
#   □ Storage Account (globally unique name)
#   □ Azure AI Foundry (deploy 4 models: gpt41, gpt4omini, o4mini, text-embedding-3-large)
#   □ Function App (Flex Consumption Python 3.11)
#   □ Static Web App (Flutter web host)
#   □ Google OAuth credentials (https://console.cloud.google.com/apis/credentials)

# Once created, gather your resource info:
#   • Function App hostname: myapp-func-dev.azurewebsites.net
#   • Static Web App hostname: kind-bay-12345.azurestaticapps.net  
#   • Cosmos endpoint: https://myapp-cosmos.documents.azure.com:443/
#   • Cosmos primary key: (from Azure Portal → Cosmos → Keys)
#   • Storage connection string: (from Azure Portal → Storage Account → Connection string)
#   • AI endpoint: https://myapp-ai.cognitiveservices.azure.com/
#   • AI key: (from Azure Portal → Azure AI → Keys and Endpoint)

# STEP 2: Run Automated Setup (RECOMMENDED)
# ─────────────────────────────────────────────────────────────────────────────
pwsh tools/setup-local-dev.ps1

# This script will:
#   ✓ Ask for your Azure resource info
#   ✓ Create api/local.settings.json with credentials
#   ✓ Set up Python virtual environment
#   ✓ Install dependencies
#   ✓ Replace URL placeholders in extension files
#   ✓ Show next steps

# STEP 3: Test Backend (in one terminal)
# ─────────────────────────────────────────────────────────────────────────────
cd api
.\.venv\Scripts\Activate.ps1
func start

# In another terminal, verify it works:
curl http://localhost:7071/api/v1/health
# Should return: {"data":{"status":"ok"},"error":null}

# STEP 4: Test Frontend (in another terminal)
# ─────────────────────────────────────────────────────────────────────────────
cd app
flutter pub get
flutter run -d chrome `
  --dart-define=API_BASE_URL="http://localhost:7071" `
  --dart-define=GOOGLE_CLIENT_ID="<your-google-oauth-client-id>.apps.googleusercontent.com" `
  --dart-define=ADMIN_EMAILS="you@example.com"

# STEP 5: Load Chrome Extension
# ─────────────────────────────────────────────────────────────────────────────
# 1. Open chrome://extensions
# 2. Toggle "Developer mode" (top right)
# 3. Click "Load unpacked"
# 4. Select the extension/ folder from this repo
# 5. Click the extension icon → Options
# 6. Verify API Base URL is set to http://localhost:7071

# STEP 6: Full End-to-End Test
# ─────────────────────────────────────────────────────────────────────────────
# 1. Go to http://localhost:3000 (Flutter web)
# 2. Sign up with Google OAuth
# 3. Upload a resume (PDF or DOCX)
# 4. Go to Profile → select companies
# 5. Go to Discover → select a company → should show ranked jobs
# 6. Open a job posting (e.g., from Google Careers)
# 7. Extension FAB should appear on right edge
# 8. Click FAB → should autofill form fields

# ╔═══════════════════════════════════════════════════════════════════════════════╗
# ║                            TROUBLESHOOTING                                     ║
# ╚═══════════════════════════════════════════════════════════════════════════════╝

# "Chrome refuses to load extension" → manifest.json has invalid host permissions
#   Fix: Run setup-local-dev.ps1 to replace <your-function-app> placeholder

# "func start fails" → api/local.settings.json missing or has wrong credentials
#   Fix: Check that local.settings.json exists and all values are filled in

# "Flutter app won't connect to backend" → Wrong API_BASE_URL
#   Fix: Check --dart-define=API_BASE_URL matches your func start URL

# "Daily limit reached on Discover" → Hit 50/day free tier limit
#   Fix: Temporarily set profile.tier='admin' in Cosmos, or increase
#        FREE_TIER_DAILY_DISCOVER_LIMIT in local.settings.json

# "Forms don't autofill" → Extension can't reach backend
#   Fix: Open extension Options, verify API Base URL is correct and matches
#        what func start is running on

# ╔═══════════════════════════════════════════════════════════════════════════════╗
# ║                          FULL DOCUMENTATION                                    ║
# ╚═══════════════════════════════════════════════════════════════════════════════╝

# SETUP_LOCAL.md .......... Complete setup guide with all steps
# CONFIGURATION.md ........ Technical reference for all placeholders
# SETUP_READY.md .......... Summary of what was done + checklist
# SETUP.md ................ Original comprehensive guide
# README.md ............... Product overview + API documentation
# PROJECT_STATE.md ........ Codebase architecture + schema reference

# ╔═══════════════════════════════════════════════════════════════════════════════╗
# ║                        WHEN YOU'RE DONE LOCALLY                                ║
# ║                          Deploy to Azure                                       ║
# ╚═══════════════════════════════════════════════════════════════════════════════╝

# Backend: func azure functionapp publish $FUNCAPP --python
# Frontend: flutter build web --release [with dart-define flags]
#           + SWA deploy
# See SETUP.md § 5-6 for full deployment steps

# Questions? See:
#   • SETUP_LOCAL.md § Troubleshooting
#   • README.md § FAQ
#   • PROJECT_STATE.md § API Reference
