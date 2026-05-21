#requires -Version 7.0
<#
.SYNOPSIS
  Scale Azure OpenAI deployment TPM (tokens-per-minute) up or down on demand.

.DESCRIPTION
  TPM = sku.capacity * 1000 for Standard/GlobalStandard deployments.
  Bump before a load test / heavy regression run, drop back down after.
  No app restart needed; effective in seconds.

.EXAMPLE
  # Scale gpt4omini to 1000 TPM (capacity=1000)
  .\scripts\set-openai-tpm.ps1 -Deployment gpt4omini -Capacity 1000

  # Scale o4mini back down to baseline 250
  .\scripts\set-openai-tpm.ps1 -Deployment o4mini -Capacity 250

  # Show current capacity for all deployments
  .\scripts\set-openai-tpm.ps1 -Show
#>
param(
    [string]$ResourceGroup = "<your-resource-group>",
    [string]$Account       = "<your-ai-resource>",
    [string]$Deployment,
    [int]$Capacity,
    [switch]$Show
)

$ErrorActionPreference = "Stop"

if ($Show -or -not $Deployment) {
    az cognitiveservices account deployment list `
        -g $ResourceGroup -n $Account `
        --query "[].{name:name, sku:sku.name, capacity:sku.capacity, tpm:sku.capacity}" -o table
    return
}

if (-not $Capacity) {
    Write-Error "Provide -Capacity <int> (TPM in thousands, e.g. 250 = 250K TPM)"
    return
}

# Read current SKU name (Standard vs GlobalStandard) so we don't accidentally change tier.
$skuName = az cognitiveservices account deployment show `
    -g $ResourceGroup -n $Account --deployment-name $Deployment `
    --query "sku.name" -o tsv

if (-not $skuName) {
    Write-Error "Deployment '$Deployment' not found on account '$Account'."
    return
}

Write-Host "Scaling $Deployment ($skuName) -> capacity=$Capacity (~${Capacity}K TPM)..."
az cognitiveservices account deployment update `
    -g $ResourceGroup -n $Account `
    --deployment-name $Deployment `
    --sku-name $skuName --sku-capacity $Capacity -o none

Write-Host "Done. Current state:"
az cognitiveservices account deployment show `
    -g $ResourceGroup -n $Account --deployment-name $Deployment `
    --query "{name:name, sku:sku.name, capacity:sku.capacity}" -o table
