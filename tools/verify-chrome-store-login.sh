#!/usr/bin/env bash
# Verify backend + config needed for Chrome Web Store extension Google login.
set -euo pipefail

API="${API_BASE_URL:-https://autoapply-func-dev.azurewebsites.net}"
CLIENT_ID="${GOOGLE_CLIENT_ID:-8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com}"
STORE_EXT_ID="anjgpjhdecnibcbogkclafanemofndea"
REDIRECT="https://${STORE_EXT_ID}.chromiumapp.org/"

echo "=== Chrome Web Store extension login checklist ==="
echo ""
echo "1) Google Cloud → Web client ${CLIENT_ID}"
echo "   Authorized redirect URI (required):"
echo "   ${REDIRECT}"
echo "   https://${STORE_EXT_ID}.chromiumapp.org   (no trailing slash — add both)"
echo ""
echo "   OAuth consent screen must be Production OR user Gmail added as Test user."
echo "   https://console.cloud.google.com/apis/credentials/consent"
echo ""

echo "2) API health: ${API}/api/v1/health"
if code=$(curl -sS -o /tmp/health.json -w "%{http_code}" "${API}/api/v1/health"); then
  echo "   HTTP ${code} $(head -c 120 /tmp/health.json)"
else
  echo "   FAILED — network/DNS/TLS"
  exit 1
fi
echo ""

echo "3) Auth endpoint reachable: POST ${API}/api/v1/auth/google"
code=$(curl -sS -o /tmp/auth.json -w "%{http_code}" -X POST "${API}/api/v1/auth/google" \
  -H "Content-Type: application/json" \
  -d '{"idToken":"invalid-test-token"}')
echo "   HTTP ${code} (expect 401/400 if GOOGLE_CLIENT_IDS is set; 5xx if misconfigured)"
head -c 200 /tmp/auth.json 2>/dev/null; echo ""
echo ""

echo "4) Azure Function App settings (run locally if az login):"
if command -v az >/dev/null 2>&1; then
  az functionapp config appsettings list -g rg-autoapply-dev -n autoapply-func-dev \
    --query "[?name=='GOOGLE_CLIENT_IDS' || name=='JWT_SECRET'].{name:name, set:value!=null}" -o table 2>/dev/null || \
    echo "   (az failed — check GOOGLE_CLIENT_IDS and JWT_SECRET in portal)"
else
  echo "   Install Azure CLI and verify GOOGLE_CLIENT_IDS + JWT_SECRET on autoapply-func-dev"
fi
echo ""
echo "Done. Store users need §1 + §2; see extension/STORE_LOGIN_SETUP.md"
