# AutoApply Setup Validation Checklist

Use this checklist to verify your setup is complete and working correctly.

## Prerequisites ✓

- [ ] Python 3.11.x installed (`python --version`)
- [ ] Node.js 20 LTS installed (`node --version`)
- [ ] Flutter 3.11+ installed (`flutter --version`)
- [ ] Azure CLI installed (`az --version`)
- [ ] Azure Functions Core Tools v4 installed (`func --version`)

## Azure Resources Provisioned ✓

- [ ] Resource Group created (e.g., `rg-myapp-dev`)
- [ ] Cosmos DB created (free tier, `autoapply` database)
- [ ] Storage Account created (globally unique name)
- [ ] Azure AI Foundry created with 4 model deployments:
  - [ ] `gpt41` (GPT-4 1 with 50 TPM)
  - [ ] `gpt4omini` (GPT-4o Mini with 100 TPM)
  - [ ] `o4mini` (o4-mini with 50 TPM)
  - [ ] `text-embedding-3-large` (Embeddings with 50 TPM)
- [ ] Function App created (Flex Consumption, Python 3.11)
- [ ] Static Web App created (Flutter web host)
- [ ] Google OAuth Web Client ID created (https://console.cloud.google.com/apis/credentials)
  - [ ] Authorized JavaScript origins include: `https://<your-swa-host>`
  - [ ] Authorized JavaScript origins include: `http://localhost:*`

## Backend Setup ✓

- [ ] Python venv created: `api\.venv\` directory exists
- [ ] Dependencies installed: `pip list | grep flask azure-functions`
- [ ] `api/local.settings.json` exists (created from template)
- [ ] All values in `local.settings.json` are filled in (not placeholders):
  - [ ] `COSMOS_ENDPOINT` — https://...documents.azure.com:443/
  - [ ] `COSMOS_KEY` — actual key from Azure
  - [ ] `BLOB_CONNECTION_STRING` — actual connection string
  - [ ] `AZURE_AI_ENDPOINT` — https://...cognitiveservices.azure.com/
  - [ ] `AZURE_AI_KEY` — actual key from Azure
  - [ ] `JWT_SECRET` — random 48-char token (not placeholder)
  - [ ] `ADMIN_API_TOKEN` — random 32-char token (not placeholder)
  - [ ] `SUPER_ADMIN_EMAILS` — real email address

### Backend Runtime Test ✓

```powershell
cd api
.\.venv\Scripts\Activate.ps1
func start
```

- [ ] `func start` runs without errors
- [ ] Shows "Azure Functions Core Tools started"
- [ ] Shows "Now listening on: http://0.0.0.0:7071"
- [ ] No errors in startup log

### Backend Health Check ✓

In another terminal:
```powershell
curl http://localhost:7071/api/v1/health
```

- [ ] Returns 200 status code
- [ ] Response body contains `{"data":{"status":"ok"}}`

## Frontend Setup ✓

- [ ] Flutter dependencies installed: `flutter pub get` runs
- [ ] Google OAuth Client ID obtained from Google Cloud Console
- [ ] `app/lib/config/azure_config.dart` file exists (placeholders are OK, overridden by flags)

### Frontend Runtime Test ✓

```powershell
cd app
flutter run -d chrome `
  --dart-define=API_BASE_URL="http://localhost:7071" `
  --dart-define=GOOGLE_CLIENT_ID="<your-client-id>.apps.googleusercontent.com" `
  --dart-define=ADMIN_EMAILS="you@example.com"
```

- [ ] Flutter compiles without errors
- [ ] Chrome browser opens with Flutter app
- [ ] Page loads (shows login/signup screen)
- [ ] Browser console has no CORS errors
- [ ] Network tab shows requests to `http://localhost:7071`

### Frontend Build Test ✓

```powershell
cd app
flutter build web --release `
  --dart-define=API_BASE_URL="http://localhost:7071" `
  --dart-define=GOOGLE_CLIENT_ID="<your-client-id>.apps.googleusercontent.com" `
  --dart-define=ADMIN_EMAILS="you@example.com"
```

- [ ] Build completes without errors
- [ ] `app/build/web/` directory created
- [ ] `app/build/web/index.html` exists

## Chrome Extension Setup ✓

- [ ] All placeholder URLs replaced in extension files:
  - [ ] `extension/manifest.json` — no `<your-` placeholders
  - [ ] `extension/background.js` — `DEFAULT_API_BASE` has real URL
  - [ ] `extension/popup.js` — `DEFAULT_API_BASE` has real URL
  - [ ] `extension/options.js` — `DEFAULT_API_BASE` has real URL
  - [ ] `extension/content.js` — `window.open()` has real URL

### Extension Loading ✓

1. Open `chrome://extensions`
2. Toggle "Developer mode" (top-right switch)
3. Click "Load unpacked" button
4. Select `extension/` folder from repo
5. Extension appears in list

- [ ] Extension loads without errors
- [ ] Extension icon appears in Chrome toolbar
- [ ] No error message in red

### Extension Functionality ✓

1. Click extension icon in toolbar
2. Click "Options" link or extension icon → gear icon
3. Verify "API Base URL" field exists and shows correct endpoint
4. You can change it to test different backends

- [ ] Options page loads
- [ ] API Base URL field is accessible
- [ ] Can type/modify API Base URL
- [ ] Value persists after refresh

## Integration Tests ✓

### Auth Flow ✓

```powershell
# Test signup via API
curl -X POST http://localhost:7071/api/v1/auth/signup `
  -H "Content-Type: application/json" `
  -d '{"email":"test@example.com","password":"TestPassword123!"}'
```

- [ ] Returns 201 status (created)
- [ ] Response includes `token` field (JWT)
- [ ] Token can be used in subsequent requests

### Profile Flow ✓

1. Open Flutter app at http://localhost:3000
2. Sign up with test email/password or Google OAuth
3. Upload resume (PDF or DOCX)
4. Fill in profile fields
5. Click "Save"

- [ ] Profile saves without errors
- [ ] Resume is parsed (AI extracts name, experience, etc.)
- [ ] Profile data persists across page refresh

### Discover Flow ✓

1. Go to Discover page
2. Select companies (Google, Amazon, Netflix, etc.)
3. Click "Discover" button

- [ ] Page shows loading state
- [ ] Each company shows ranked jobs
- [ ] Jobs are sorted by relevance
- [ ] Click a job card shows details

### Autofill Flow ✓

1. Open a real job posting (e.g., https://google.com/careers)
2. Wait for form to load
3. Extension FAB (button) should appear on right edge
4. Click "AutoApply" button

- [ ] Extension FAB is visible (right-aligned, purple/gradient)
- [ ] FAB expands to show "AutoApply" text on hover
- [ ] Clicking FAB triggers autofill
- [ ] Form fields populate with data from profile
- [ ] Toast notification shows "Filled N fields"

## Deployment Readiness ✓

(Only needed if deploying to Azure)

- [ ] `api/local.settings.json` is in `.gitignore` (checked)
- [ ] No credentials in any source files (checked)
- [ ] Function App published: `func azure functionapp publish $FUNCAPP --python`
- [ ] Live settings configured: `az functionapp config appsettings set ...`
- [ ] Flutter app built: `flutter build web --release ...`
- [ ] Web app deployed to SWA: `npx @azure/static-web-apps-cli deploy ...`
- [ ] Extension zip generated: `pwsh tools/build_extension_zip.ps1`
- [ ] Extension included in web deploy: `app/web/autoapply-extension.zip` exists

## Troubleshooting ✓

If any check fails:

1. **Backend won't start** → Check `api/local.settings.json` exists and has valid JSON
2. **API returns 401** → JWT_SECRET in local.settings.json doesn't match frontend expectations
3. **Frontend won't load** → Check API_BASE_URL --dart-define flag is correct
4. **Extension loads but can't autofill** → Check extension Options has correct API Base URL
5. **Forms don't autofill** → Check browser console for errors, verify extension token is set
6. **"Daily limit reached"** → Set user tier to `admin` in Cosmos DB or increase limit in local.settings.json

## Final Sanity Check ✓

```powershell
# All three running simultaneously:
# Terminal 1 (Backend):
cd api && func start

# Terminal 2 (Frontend):
cd app && flutter run -d chrome --dart-define=... 

# Terminal 3 (Extension):
# chrome://extensions → Extension loaded from unpacked folder
```

- [ ] Backend runs on http://localhost:7071
- [ ] Frontend runs on http://localhost:3000
- [ ] Extension loads in Chrome
- [ ] Can sign in via Google OAuth on both web and extension
- [ ] Can upload resume and see it parsed
- [ ] Can discover jobs and see them ranked
- [ ] Can visit a job posting and extension autofills it

---

**If all checks pass: ✅ Setup is complete and working!**

Next steps:
- Customize profile fields and preferences
- Test with real job postings
- Configure Lemon Squeezy for Pro tier (optional, see README.md § 5.6)
- Deploy to Azure for production use (see SETUP.md § 5-6)

For help: See [SETUP_LOCAL.md](SETUP_LOCAL.md) Troubleshooting section.
