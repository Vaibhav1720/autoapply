#!/usr/bin/env bash
# Build a minimal Chrome Web Store ZIP from main-branch extension (v1.6.15 base)
# with default API https://autoapplynow.in. Does not include manifest "key".
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO/release"
BUILD_DIR="$(mktemp -d)"
ZIP_NAME="applyright-chrome-store-1.16.1.zip"
GIT_REF="${1:-main}"

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR/$ZIP_NAME"

echo "Exporting extension/ from git ref: $GIT_REF"
git -C "$REPO" archive "$GIT_REF" extension \
  | tar -x -C "$BUILD_DIR" --strip-components=1

# Remove docs not needed in the package
rm -f "$BUILD_DIR/PUBLISH.md" "$BUILD_DIR/STORE_LISTING.md" "$BUILD_DIR/STORE_LOGIN_SETUP.md" \
  "$BUILD_DIR/CHROME_WEB_STORE_UPLOAD.md" "$BUILD_DIR/ats-hosts.json" 2>/dev/null || true

# --- Minimal patch: default API + migrate legacy Azure URL ---
for f in popup.js options.js background.js; do
  sed -i '' \
    's|const DEFAULT_API_BASE = "https://autoapply-func-dev.azurewebsites.net"|const DEFAULT_API_BASE = "https://autoapplynow.in"|g' \
    "$BUILD_DIR/$f"
done

# popup.js: migrate empty storage to new default (main only migrated explicit legacy)
if grep -q 'if (stored === LEGACY_API_BASE)' "$BUILD_DIR/popup.js"; then
  sed -i '' 's/if (stored === LEGACY_API_BASE)/if (!stored || stored === LEGACY_API_BASE)/' "$BUILD_DIR/popup.js"
fi
if grep -q 'if (stored === LEGACY_API_BASE)' "$BUILD_DIR/options.js"; then
  sed -i '' 's/if (stored === LEGACY_API_BASE)/if (!stored || stored === LEGACY_API_BASE)/' "$BUILD_DIR/options.js"
fi
if grep -q 'if (stored === LEGACY_API_BASE)' "$BUILD_DIR/background.js"; then
  sed -i '' 's/if (stored === LEGACY_API_BASE)/if (!stored || stored === LEGACY_API_BASE)/' "$BUILD_DIR/background.js"
fi

# manifest version must increase for each Web Store upload
sed -i '' 's/"version": "1.16.0"/"version": "1.16.1"/' "$BUILD_DIR/manifest.json"

# UI version label (patch after 1.6.15)
sed -i '' 's|v1.6.15|v1.6.16|' "$BUILD_DIR/popup.html"
sed -i '' 's|placeholder="https://your-backend.azurewebsites.net"|placeholder="https://autoapplynow.in"|' "$BUILD_DIR/popup.html"

if grep -q '"key"' "$BUILD_DIR/manifest.json"; then
  echo "ERROR: manifest must not contain key for Web Store upload" >&2
  exit 1
fi

(
  cd "$BUILD_DIR"
  zip -r "$OUT_DIR/$ZIP_NAME" . -x "*.DS_Store"
)

rm -rf "$BUILD_DIR"

echo ""
echo "Built: $OUT_DIR/$ZIP_NAME"
unzip -l "$OUT_DIR/$ZIP_NAME"
