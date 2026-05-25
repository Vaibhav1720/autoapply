#!/usr/bin/env bash
# Push GOOGLE_CLIENT_SECRET to the Azure Function App (required for OAuth redirect sign-in).
#
# Get the secret: Google Cloud Console → APIs & Services → Credentials
#   → OAuth 2.0 Client IDs → your Web client → Client secret
#
# Usage:
#   export GOOGLE_CLIENT_SECRET='GOCSPX-...'
#   bash tools/set-google-client-secret.sh
#
# Or add GOOGLE_CLIENT_SECRET=... to tools/pipeline-variables.env and run without export.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/tools/pipeline-variables.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

RG="${resourceGroup:-rg-autoapply-dev}"
FUNC="${functionAppName:-autoapply-func-dev}"
SECRET="${GOOGLE_CLIENT_SECRET:-}"

if [[ -z "$SECRET" ]]; then
  echo "ERROR: Set GOOGLE_CLIENT_SECRET (export or in tools/pipeline-variables.env)." >&2
  exit 1
fi

echo "Setting GOOGLE_CLIENT_SECRET on $FUNC (resource group $RG) ..."
az functionapp config appsettings set \
  -g "$RG" \
  -n "$FUNC" \
  --settings "GOOGLE_CLIENT_SECRET=$SECRET" \
  -o none

echo "Done. Redirect-based Google sign-in should work after the Function App restarts (~1 min)."
