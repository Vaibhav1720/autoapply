#!/usr/bin/env bash
# LiteLLM proxy for Cursor Agent → Bedrock (Qwen / DeepSeek / GLM)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
PORT="${LITELLM_PORT:-4000}"

# Project .env (optional): AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# LiteLLM Bedrock uses boto3 — region for Qwen/DeepSeek/GLM models
export AWS_REGION_NAME="${AWS_REGION_NAME:-${AWS_REGION:-eu-north-1}}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION_NAME}"

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "WARNING: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY not set." >&2
  echo "  LiteLLM Bedrock needs IAM keys (AKIA...), not the ABSK bearer token alone." >&2
  echo "  Add them to $ROOT/.env or run: aws configure" >&2
fi

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -U pip
  "$VENV/bin/pip" install -r "$DIR/requirements.txt"
fi

echo "Starting LiteLLM on http://localhost:${PORT} (region ${AWS_REGION_NAME})"
echo "  Cursor Base URL: http://localhost:${PORT}/v1  (or http://localhost:${PORT})"
exec "$VENV/bin/litellm" --config "$DIR/config.yaml" --host 0.0.0.0 --port "$PORT"
