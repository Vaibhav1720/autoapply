# AutoApply Setup Summary & Quick Reference

## What Was Done

Created a complete setup infrastructure for making AutoApply end-to-end working:

### 1. **Configuration Templates & Guides**
- ✅ `SETUP_LOCAL.md` — Complete step-by-step local development guide
- ✅ `CONFIGURATION.md` — Technical reference for all placeholders
- ✅ `api/local.settings.json.template` — Backend configuration template

### 2. **Automated Setup Script**
- ✅ `tools/setup-local-dev.ps1` — PowerShell script that:
  - Prompts for Azure resource information
  - Creates `api/local.settings.json` with correct credentials
  - Sets up Python virtual environment (3.11)
  - Installs dependencies from `requirements.txt`
  - Automatically replaces URL placeholders in extension files

### 3. **Documentation**
All files include:
- Clear step-by-step instructions
- Example values and commands
- Troubleshooting sections
- Testing checklists

## Quick Start (3 Options)

### Option A: Automated Setup (Recommended)
```powershell
pwsh tools/setup-local-dev.ps1
```
This handles everything automatically.

### Option B: Manual Setup
```powershell
# 1. Backend
cd api
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp local.settings.json.template local.settings.json
# Edit local.settings.json with your Azure credentials

# 2. Extension URLs
$apiHost = "your-function-app.azurewebsites.net"
$swaHost = "your-static-web-app.azurestaticapps.net"
Get-ChildItem extension -File -Recurse | 
  Where-Object { $_.Extension -in '.js','.json','.html' } |
  ForEach-Object {
    (Get-Content -Raw $_.FullName) -replace '<your-function-app>\.azurewebsites\.net', $apiHost -replace '<your-static-web-app>\.azurestaticapps\.net', $swaHost |
      Set-Content -NoNewline $_.FullName
  }

# 3. Test
func start
```

### Option C: Hybrid (manual configuration + auto script)
```powershell
# Create and edit local.settings.json manually first
cp api/local.settings.json.template api/local.settings.json
# Edit the file...

# Then run script with parameters
pwsh tools/setup-local-dev.ps1 -ApiHost "your-host.azurewebsites.net" -SwaHost "your-host.azurestaticapps.net"
```

## What Needs to Happen Before Setup

### Prerequisites (One-Time)
- [ ] Install Python 3.11, Node 20 LTS, Flutter 3.11+, Azure CLI, Functions Core Tools v4
- [ ] Create Google OAuth Web Client ID (Console → APIs → Credentials → OAuth 2.0 Client IDs)

### Azure Resources (Must Exist)
- [ ] Resource Group (e.g., `rg-myapp-dev`)
- [ ] Cosmos DB (free tier, `autoapply` database)
- [ ] Storage Account (globally unique name)
- [ ] Azure AI Foundry (with 4 model deployments: `gpt41`, `gpt4omini`, `o4mini`, `text-embedding-3-large`)
- [ ] Function App (Flex Consumption, Python 3.11)
- [ ] Static Web App (for Flutter web)
- [ ] (Optional) Lemon Squeezy account (for Pro subscription tier)

See [SETUP.md](SETUP.md) § 1-2 for provisioning commands.

## Configuration Checklist

### Backend (`api/`)
- [ ] `local.settings.json` created (from template)
- [ ] All credential placeholders filled in
- [ ] Python venv created (`py -3.11 -m venv .venv`)
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] `func start` runs and health check works

### Frontend (`app/`)
- [ ] Google OAuth Client ID obtained
- [ ] Flutter dependencies installed (`flutter pub get`)
- [ ] Can run with `flutter run -d chrome --dart-define=...` flags
- [ ] Can build web: `flutter build web --release --dart-define=...`

### Extension (`extension/`)
- [ ] URL placeholders replaced in all `.js`, `.json`, `.html` files
- [ ] Can load unpacked in Chrome
- [ ] Can authenticate and see profile
- [ ] Can autofill forms on job sites

### Infrastructure
- [ ] AI models deployed (names hard-coded in `api/services/_runtime.py`)
- [ ] All credentials in `api/local.settings.json` match Azure resources
- [ ] JWT secret and admin token are cryptographically random
- [ ] Cosmos database and containers created (auto-created on first write if using SDK)

## Critical Files

| File | Purpose | Status |
|---|---|---|
| `SETUP.md` | Original comprehensive setup guide | ✅ Exists (read first) |
| `SETUP_LOCAL.md` | New step-by-step developer guide | ✅ Created |
| `CONFIGURATION.md` | Technical reference for placeholders | ✅ Created |
| `api/local.settings.json.template` | Backend config template | ✅ Created |
| `tools/setup-local-dev.ps1` | Automated setup script | ✅ Created |
| `api/local.settings.json` | **Must be created before `func start`** | ⚠️ Create from template |

## What's NOT Done (Requires Azure Account)

These require actual Azure resources (outside scope of this setup guide):
- [ ] Provisioning Azure resources (needs `az deployment group create`)
- [ ] Creating Cosmos DB and containers
- [ ] Deploying models to Azure AI Foundry
- [ ] Creating Google OAuth credentials
- [ ] Deploying to Azure (needs `func azure functionapp publish`)

See [SETUP.md](SETUP.md) § 0-2 for Azure provisioning.

## Testing

After setup, verify everything works:

```powershell
# 1. API health
curl http://localhost:7071/api/v1/health   # Should return {"status":"ok"}

# 2. Auth signup
curl -X POST http://localhost:7071/api/v1/auth/signup `
  -H "Content-Type: application/json" `
  -d '{"email":"test@example.com","password":"TestPass123!"}'

# 3. Flutter app runs
flutter run -d chrome --dart-define=API_BASE_URL="http://localhost:7071" ...

# 4. Extension loads in Chrome
# chrome://extensions → Load unpacked → extension/

# 5. Full flow
# - Sign in on web app with Google OAuth
# - Upload resume
# - Select companies
# - Click "Discover"
# - Extension should autofill forms
```

## What to Read Next

1. **[SETUP_LOCAL.md](SETUP_LOCAL.md)** — Step-by-step developer setup guide
2. **[SETUP.md](SETUP.md)** — Full reference (if you need Azure provisioning details)
3. **[CONFIGURATION.md](CONFIGURATION.md)** — Technical reference for all config values
4. **[README.md](README.md)** — Product overview and API documentation
5. **[PROJECT_STATE.md](PROJECT_STATE.md)** — Codebase structure and architecture

## Support

If setup fails:
1. Check `.gitignore` has `local.settings.json` (prevents credential leaks)
2. Verify all Azure resources exist and credentials are correct
3. Run `func start --verbose` for detailed error messages
4. Check `api/requirements.txt` for Python version compatibility
5. See "Troubleshooting" section in [SETUP_LOCAL.md](SETUP_LOCAL.md)

---

**Status:** ✅ Setup infrastructure is ready. Next: Provision Azure resources → Run setup script → Test locally.
