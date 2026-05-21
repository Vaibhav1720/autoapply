#!/usr/bin/env bash
# Substitute extension placeholders from environment variables.
# Usage:
#   export GOOGLE_CLIENT_ID="xxx.apps.googleusercontent.com"
#   export API_BASE_URL="https://autoapply-func-dev.azurewebsites.net"
#   export SWA_HOST="mango-ocean-0f1de6810.2.azurestaticapps.net"
#   bash tools/configure-extension.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"

API_BASE_URL="${API_BASE_URL:-https://autoapply-func-dev.azurewebsites.net}"
SWA_HOST="${SWA_HOST:-mango-ocean-0f1de6810.2.azurestaticapps.net}"

if [[ -z "${GOOGLE_CLIENT_ID:-}" ]] || [[ "$GOOGLE_CLIENT_ID" == *"<your-google-client-id>"* ]]; then
  echo "ERROR: set GOOGLE_CLIENT_ID (non-placeholder) before configuring the extension." >&2
  exit 1
fi

for file in popup.js background.js options.js content.js; do
  path="$EXT/$file"
  [[ -f "$path" ]] || continue
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' \
      -e "s|<your-google-client-id>\\.apps\\.googleusercontent\\.com|${GOOGLE_CLIENT_ID}|g" \
      -e "s|<your-function-app>\\.azurewebsites\\.net|${API_BASE_URL#https://}|g" \
      -e "s|<your-static-web-app>\\.azurestaticapps\\.net|${SWA_HOST}|g" \
      "$path"
  else
    sed -i \
      -e "s|<your-google-client-id>\\.apps\\.googleusercontent\\.com|${GOOGLE_CLIENT_ID}|g" \
      -e "s|<your-function-app>\\.azurewebsites\\.net|${API_BASE_URL#https://}|g" \
      -e "s|<your-static-web-app>\\.azurestaticapps\\.net|${SWA_HOST}|g" \
      "$path"
  fi
done

# popup.js may use a full client id string without the placeholder prefix.
if grep -q '<your-google-client-id>' "$EXT/popup.js" 2>/dev/null; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|<your-google-client-id>\\.apps\\.googleusercontent\\.com|${GOOGLE_CLIENT_ID}|g" "$EXT/popup.js"
  else
    sed -i "s|<your-google-client-id>\\.apps\\.googleusercontent\\.com|${GOOGLE_CLIENT_ID}|g" "$EXT/popup.js"
  fi
fi

echo "Extension configured (API=${API_BASE_URL}, SWA=${SWA_HOST})"
