#!/usr/bin/env bash
# Apply non-secret values from tools/pipeline-variables.env into the repo and extension.
# Secrets (googleClientId, staticWebAppDeploymentToken) are printed as ADO instructions only.
#
# Usage:
#   cp tools/pipeline-variables.env.example tools/pipeline-variables.env
#   # edit pipeline-variables.env
#   bash tools/apply-pipeline-config.sh
#
# Optional: push variables to Azure DevOps (requires az devops extension + login):
#   az extension add --name azure-devops
#   export AZURE_DEVOPS_ORG=... AZURE_DEVOPS_PROJECT=...

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/tools/pipeline-variables.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from tools/pipeline-variables.env.example" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

PIPELINE_YML="$ROOT/azure-pipelines.yml"

update_yaml_var() {
  local key="$1" val="$2"
  if [[ -z "$val" ]]; then
    return
  fi
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|^  ${key}:.*|  ${key}: ${val}|" "$PIPELINE_YML"
  else
    sed -i "s|^  ${key}:.*|  ${key}: ${val}|" "$PIPELINE_YML"
  fi
  echo "  azure-pipelines.yml → ${key}"
}

echo "=== Updating azure-pipelines.yml defaults ==="
update_yaml_var functionAppName "${functionAppName:-}"
update_yaml_var resourceGroup "${resourceGroup:-}"
update_yaml_var apiBaseUrl "${apiBaseUrl:-}"
update_yaml_var enableDeploy "${enableDeploy:-true}"
update_yaml_var runInfraDeploy "${runInfraDeploy:-false}"

if [[ -n "${googleClientId:-}" && -n "${apiBaseUrl:-}" ]]; then
  echo "=== Configuring extension ==="
  export GOOGLE_CLIENT_ID="$googleClientId"
  export API_BASE_URL="$apiBaseUrl"
  export SWA_HOST="${swaHost:-}"
  bash "$ROOT/tools/configure-extension.sh"
else
  echo "Skip extension (need googleClientId + apiBaseUrl in env file)"
fi

echo ""
echo "=== Set these in Azure DevOps → Pipelines → Variables ==="
[[ -n "${azureSubscription:-}" ]] && echo "  azureSubscription = $azureSubscription"
[[ -n "${googleClientId:-}" ]] && echo "  googleClientId = (secret) ***"
[[ -n "${adminEmails:-}" ]] && echo "  adminEmails = $adminEmails"
[[ -n "${staticWebAppDeploymentToken:-}" ]] && echo "  staticWebAppDeploymentToken = (secret) ***"
echo ""
echo "Done. Commit azure-pipelines.yml + extension changes if desired."
