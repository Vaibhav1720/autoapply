# Azure DevOps CI/CD

**Step-by-step: where to find each value →** [`PIPELINE_SETUP_GUIDE.md`](PIPELINE_SETUP_GUIDE.md)

Pipeline definition: [`azure-pipelines.yml`](../azure-pipelines.yml)

## What runs automatically

| Stage | When | Notes |
|-------|------|-------|
| **BackendTest** | Every PR + push to `main` | `pytest -m "not live"` (~2 min, no live scraper HTTP) |
| **FlutterTest** | Every PR + push to `main` | `pub get`, `analyze`, `test` (includes `app/test/smoke_test.dart`) |
| **DeployBackend** | `main` only, when `enableDeploy` ≠ `false` | Needs `azureSubscription`, staging slot |
| **DeployWeb** | `main` only, when `enableDeploy` ≠ `false` | Needs `googleClientId`, `apiBaseUrl`, SWA token |
| **ConfigureExtension** | After DeployWeb on `main` | Runs `tools/configure-extension.sh`; zip/upload still manual |
| **Infrastructure** | `main` when `runInfraDeploy` is `true` and deploy is not disabled | Bicep — **off by default** |

## Required pipeline variables

Set in **Pipelines → Edit → Variables** (mark secrets as secret):

| Variable | Example | Secret? |
|----------|---------|---------|
| `azureSubscription` | Name of your Azure Resource Manager service connection | No |
| `functionAppName` | `autoapply-func-dev` | No |
| `resourceGroup` | `rg-autoapply-dev` | No |
| `apiBaseUrl` | `https://autoapply-func-dev.azurewebsites.net` | No |
| `googleClientId` | `123456789.apps.googleusercontent.com` | Yes (recommended) |
| `adminEmails` | `you@example.com` | No |
| `staticWebAppDeploymentToken` | From Azure Portal → Static Web App → Manage deployment token | **Yes** |
| `runInfraDeploy` | `false` in YAML — set to **`true`** in ADO only when you intend to run Bicep | No |
| `enableDeploy` | **`true` in YAML** (deploys on `main` after tests pass). Set to **`false`** in ADO to pause deploys without editing the repo | No |

**Test stages always run on PR/push.** Deploy stages run on **`main`** when tests succeed and `enableDeploy` is not set to `false` (string comparison is case-insensitive).

## Local CI mirror

```bash
bash tools/ci-test.sh
```

## Manual deploy (without Azure DevOps)

**Backend:**

```bash
cd api
func azure functionapp publish autoapply-func-dev --python
bash tools/configure-function-cors.sh   # required for login from autoapplynow.in
```

**Flutter web:**

```bash
cd app
flutter build web --release \
  --dart-define=API_BASE_URL=https://autoapply-func-dev.azurewebsites.net \
  --dart-define=GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com \
  --dart-define=ADMIN_EMAILS=you@example.com
```

**Extension:**

```bash
export GOOGLE_CLIENT_ID="<your-client-id>.apps.googleusercontent.com"
export API_BASE_URL="https://autoapply-func-dev.azurewebsites.net"
bash tools/configure-extension.sh
# Then load unpacked from extension/ in chrome://extensions
```

## Live scraper tests (optional, not in CI)

```bash
cd api
.venv/bin/pytest tests/ -m live -v
```
