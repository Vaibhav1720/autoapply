#!/usr/bin/env bash
# Build Chrome Web Store upload zip (no manifest key — see extension/PUBLISH.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
VER="$(python3 -c "import json; print(json.load(open('$EXT/manifest.json'))['version'])")"
OUT="$ROOT/autoapply-extension-v${VER}.zip"

cd "$EXT"
zip -r "$OUT" \
  manifest.json \
  content.js \
  background.js \
  popup.js \
  popup.html \
  options.js \
  options.html \
  applyright_mark.png \
  applyright_logo.png \
  icons \
  _locales \
  -x "*.DS_Store"

echo "Created $OUT"
unzip -l "$OUT" | head -25
