# What is already in the repo vs what you must set externally

**Single source of truth (non-secret):** [`config/deploy.public.json`](../config/deploy.public.json)  
**Sync into code:** `bash tools/sync-deploy-config.sh`

| Value | In repo? | Where |
|-------|----------|--------|
| Admin email `vibhuu1720@gmail.com` | Yes | `deploy.public.json`, `api/local.settings.json`, `azure-pipelines.yml`, Flutter `ADMIN_EMAILS` default |
| API URL `autoapply-func-dev` | Yes | Extension JS, `azure_config.dart`, pipeline, `deploy.public.json` |
| SWA host `mango-ocean-….azurestaticapps.net` | Yes | Extension, `deploy.public.json` |
| Cosmos / Storage / AI **names** | Yes | `deploy.public.json` (derived from your `local.settings.json`) |
| Cosmos key, JWT, AI key, blob key | **No** (gitignored) | `api/local.settings.json` + Azure Function App settings |
| **Google OAuth Client ID** | **No** | Only in **Google Cloud Console** — not stored in Azure |
| SWA deploy token | **No** | Azure Portal → Static Web App → deployment token → ADO secret |
| Azure DevOps service connection name | **No** | Created in DevOps UI per project |

## Why I cannot “log in as” your Azure / Google account

- **Azure:** Needs your browser login (`az login`) or a service principal. This environment has no `az` CLI and no access to your Microsoft credentials.
- **Google OAuth:** Client IDs are created in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) under **your** Google account (`vibhuu1720@gmail.com`). They are never exported by Azure.

## What you do once (5 minutes) for Google Sign-In

1. Sign in to Google Cloud as **vibhuu1720@gmail.com**.
2. **Credentials** → **Create OAuth client ID** → **Web application**.
3. **Authorized JavaScript origins:**
   - `https://mango-ocean-0f1de6810.2.azurestaticapps.net`
   - `http://localhost:8080`
4. Copy **Client ID** and either:
   - Paste it in chat so we can run `GOOGLE_CLIENT_ID=... bash tools/configure-extension.sh`, or
   - Set ADO pipeline variable `googleClientId` (secret) and add to `tools/pipeline-variables.env` locally.

## Pull Azure details yourself (then paste Client ID only if needed)

```bash
# Install: brew install azure-cli
az login   # use vibhuu1720@gmail.com
az account set --subscription "<your-subscription>"

az functionapp list -g rg-autoapply-dev -o table
az staticwebapp list -g rg-autoapply-dev -o table
az staticwebapp secrets list -n <swa-name> -g rg-autoapply-dev --query properties.apiKey -o tsv
```

Your **`api/local.settings.json`** already matches the dev resources above; keep using it for `func start` locally.
