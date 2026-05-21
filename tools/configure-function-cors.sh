#!/usr/bin/env bash
# Apply CORS allowed origins on the Azure Function App (required for web login from custom domain).
# Usage: bash tools/configure-function-cors.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/config/deploy.public.json"

if [[ ! -f "$CFG" ]]; then
  echo "Missing $CFG" >&2
  exit 1
fi

read -r FUNC RG CUSTOM_DOMAIN SWA_HOST < <(
  python3 -c "
import json
c = json.load(open('$CFG'))
print(c['functionAppName'], c['resourceGroup'], c.get('customDomain', ''), c['swaHost'])
"
)

ORIGINS=(
  "https://${CUSTOM_DOMAIN:-autoapplynow.in}"
  "https://www.${CUSTOM_DOMAIN:-autoapplynow.in}"
  "https://${SWA_HOST}"
  "http://localhost:3000"
  "http://localhost:8080"
)

echo "Setting CORS on $FUNC (rg=$RG) ..."
for o in "${ORIGINS[@]}"; do
  echo "  + $o"
  az functionapp cors add \
    --name "$FUNC" \
    --resource-group "$RG" \
    --allowed-origins "$o" \
    2>/dev/null || true
done

az functionapp cors show --name "$FUNC" --resource-group "$RG" -o table
echo "CORS configured."
