#!/usr/bin/env bash
# Mirror Azure DevOps CI test steps locally.
# Usage: bash tools/ci-test.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Backend (pytest, excluding live scrapers) ==="
cd "$ROOT/api"
PY="${ROOT}/api/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi
"$PY" -m pip install -q -r requirements.txt
"$PY" -m pytest tests/ -m "not live" -q --tb=line

echo "=== Flutter (pub get + analyze + test) ==="
cd "$ROOT/app"
flutter pub get
flutter analyze --no-fatal-infos --no-fatal-warnings
flutter test

echo "=== CI test mirror: OK ==="
