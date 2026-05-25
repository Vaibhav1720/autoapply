#!/usr/bin/env bash
# Print Google OAuth redirect URIs to register for the Chrome extension.
# Web client: same GOOGLE_CLIENT_ID as extension/popup.js and Flutter --dart-define.
set -euo pipefail

STORE_ID="anjgpjhdecnibcbogkclafanemofndea"
echo "Add these to Google Cloud Console → Credentials → OAuth 2.0 Web client"
echo "→ Authorized redirect URIs (one per line, trailing slash matters):"
echo ""
echo "  https://${STORE_ID}.chromiumapp.org/"
echo "  https://autoapplynow.in/oauth2-redirect.html"
echo "  https://mango-ocean-0f1de6810.2.azurestaticapps.net/oauth2-redirect.html"
echo "  http://localhost:8080/oauth2-redirect.html"
echo ""
echo "Authorized JavaScript origins (web app):"
echo "  https://autoapplynow.in"
echo "  https://mango-ocean-0f1de6810.2.azurestaticapps.net"
echo "  http://localhost:8080"
echo ""
echo "If you load an UNPACKED extension, open chrome://extensions, copy its ID,"
echo "and also add: https://<that-id>.chromiumapp.org/"
