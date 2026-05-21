#!/usr/bin/env bash
# Apply config/deploy.public.json to pipeline YAML, Flutter defaults, extension, and templates.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/config/deploy.public.json"

if [[ ! -f "$CFG" ]]; then
  echo "Missing $CFG" >&2
  exit 1
fi

read_cfg() {
  python3 -c "import json; c=json.load(open('$CFG')); print(c['$1'])"
}

ADMIN="$(read_cfg adminEmail)"
FUNC="$(read_cfg functionAppName)"
RG="$(read_cfg resourceGroup)"
API="$(read_cfg apiBaseUrl)"
SWA_HOST="$(read_cfg swaHost)"
SWA_URL="$(read_cfg swaUrl)"

sed_inplace() {
  if [[ "$(uname)" == "Darwin" ]]; then sed -i '' "$@"; else sed -i "$@"; fi
}

echo "Syncing from config/deploy.public.json ..."
echo "  admin=$ADMIN api=$API swa=$SWA_HOST"

PIPE="$ROOT/azure-pipelines.yml"
sed_inplace \
  -e "s|^  functionAppName:.*|  functionAppName: $FUNC|" \
  -e "s|^  resourceGroup:.*|  resourceGroup: $RG|" \
  -e "s|^  apiBaseUrl:.*|  apiBaseUrl: $API|" \
  -e "s|^  adminEmails:.*|  adminEmails: $ADMIN|" \
  "$PIPE"

APP_CFG="$ROOT/app/lib/config/azure_config.dart"
sed_inplace \
  -e "s|defaultValue: 'https://<your-function-app>.azurewebsites.net'|defaultValue: '$API'|" \
  -e "s|defaultValue: 'https://autoapply-func-dev.azurewebsites.net'|defaultValue: '$API'|" \
  "$APP_CFG"

# Default admin email for local `flutter run` without --dart-define
for f in "$ROOT/app/lib/screens/main_shell.dart" "$ROOT/app/lib/screens/admin/admin_dashboard_screen.dart"; do
  sed_inplace "s|defaultValue: ''|defaultValue: '$ADMIN'|g" "$f" 2>/dev/null || true
done

EX="$ROOT/tools/pipeline-variables.env.example"
sed_inplace \
  -e "s|^adminEmails=.*|adminEmails=$ADMIN|" \
  -e "s|^functionAppName=.*|functionAppName=$FUNC|" \
  -e "s|^resourceGroup=.*|resourceGroup=$RG|" \
  -e "s|^apiBaseUrl=.*|apiBaseUrl=$API|" \
  -e "s|^swaHost=.*|swaHost=$SWA_HOST|" \
  "$EX"

TPL="$ROOT/api/local.settings.json.template"
sed_inplace "s|\"SUPER_ADMIN_EMAILS\": \"you@example.com\"|\"SUPER_ADMIN_EMAILS\": \"$ADMIN\"|" "$TPL"

# Extension already has API/SWA; ensure consistency
for js in popup.js background.js options.js; do
  f="$ROOT/extension/$js"
  [[ -f "$f" ]] || continue
  sed_inplace \
    -e "s|https://autoapply-func-dev.azurewebsites.net|$API|g" \
    -e "s|https://mango-ocean-0f1de6810.2.azurestaticapps.net|$SWA_URL|g" \
    "$f"
done

echo "Done. Google Client ID is NOT in Azure — set GOOGLE_CLIENT_ID in extension/popup.js after creating OAuth client in Google Cloud (see docs/PIPELINE_SETUP_GUIDE.md)."
