# AutoApply Local Development Setup Script
# Run this from the repo root to configure everything at once
# Usage: pwsh tools/setup-local-dev.ps1

param(
    [string]$ApiHost = $null,
    [string]$SwaHost = $null,
    [string]$CosmosEndpoint = $null,
    [string]$CosmosKey = $null,
    [string]$BlobConnString = $null,
    [string]$AiEndpoint = $null,
    [string]$AiKey = $null,
    [string]$JwtSecret = $null,
    [string]$AdminToken = $null,
    [string]$AdminEmails = "you@example.com"
)

function Write-Header {
    param([string]$Message)
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║ $Message" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Blue
}

function Read-Input {
    param([string]$Prompt, [string]$Default = "")
    if ($Default) {
        $input = Read-Host "$Prompt [default: $Default]"
        return ($input -eq "") ? $Default : $input
    } else {
        return Read-Host $Prompt
    }
}

Write-Header "AutoApply Local Development Setup"

Write-Info "This script will:"
Write-Info "1. Gather your Azure resource information"
Write-Info "2. Create api/local.settings.json"
Write-Info "3. Set up Python virtual environment"
Write-Info "4. Replace placeholders in extension files"
Write-Info ""

# Step 1: Gather Azure Resource Info
Write-Header "Step 1/4: Azure Resource Information"

if (-not $ApiHost) {
    $ApiHost = Read-Input "Enter Function App hostname (e.g., myapp-func-dev.azurewebsites.net)"
}
Write-Step "API Host: $ApiHost"

if (-not $SwaHost) {
    $SwaHost = Read-Input "Enter Static Web App hostname (e.g., kind-bay-12345.azurestaticapps.net)"
}
Write-Step "SWA Host: $SwaHost"

if (-not $CosmosEndpoint) {
    $CosmosEndpoint = Read-Input "Enter Cosmos DB endpoint (e.g., https://myapp-cosmos.documents.azure.com:443/)"
}
Write-Step "Cosmos Endpoint: $CosmosEndpoint"

if (-not $CosmosKey) {
    $CosmosKey = Read-Input "Enter Cosmos DB primary key" 
}
Write-Step "Cosmos Key: (set)"

if (-not $BlobConnString) {
    Write-Info "Blob connection string format: DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
    $BlobConnString = Read-Input "Enter Blob Storage connection string"
}
Write-Step "Blob Connection String: (set)"

if (-not $AiEndpoint) {
    $AiEndpoint = Read-Input "Enter Azure AI endpoint (e.g., https://myapp-ai.cognitiveservices.azure.com/)"
}
Write-Step "AI Endpoint: $AiEndpoint"

if (-not $AiKey) {
    $AiKey = Read-Input "Enter Azure AI key"
}
Write-Step "AI Key: (set)"

# Generate secrets if not provided
if (-not $JwtSecret) {
    $JwtSecret = python -c "import secrets; print(secrets.token_urlsafe(48))"
}
Write-Step "JWT Secret: (generated)"

if (-not $AdminToken) {
    $AdminToken = python -c "import secrets; print(secrets.token_urlsafe(32))"
}
Write-Step "Admin Token: (generated)"

# Step 2: Create local.settings.json
Write-Header "Step 2/4: Creating api/local.settings.json"

$localSettingsContent = @"
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "COSMOS_ENDPOINT": "$CosmosEndpoint",
    "COSMOS_KEY": "$CosmosKey",
    "COSMOS_DATABASE": "autoapply",
    "BLOB_CONNECTION_STRING": "$BlobConnString",
    "AZURE_AI_ENDPOINT": "$AiEndpoint",
    "AZURE_AI_KEY": "$AiKey",
    "JWT_SECRET": "$JwtSecret",
    "ADMIN_API_TOKEN": "$AdminToken",
    "AI_RERANK_MODEL": "gpt4omini",
    "AI_PARSE_MODEL": "gpt41",
    "AI_REVIEW_MODEL": "gpt4omini",
    "RERANK_SKIP_GAP": "15",
    "FREE_TIER_DAILY_DISCOVER_LIMIT": "50",
    "RESUME_TAILOR_MAX_JOBS": "50",
    "SUPER_ADMIN_EMAILS": "$AdminEmails"
  },
  "Host": {
    "CORS": "*",
    "CORSCredentials": false
  }
}
"@

$localSettingsPath = "api\local.settings.json"
if (Test-Path $localSettingsPath) {
    Write-Host "Warning: $localSettingsPath already exists" -ForegroundColor Yellow
    $overwrite = Read-Host "Overwrite? (y/n) [default: n]"
    if ($overwrite -ne "y") {
        Write-Host "Skipping local.settings.json creation" -ForegroundColor Yellow
    } else {
        Set-Content -Path $localSettingsPath -Value $localSettingsContent
        Write-Step "Created $localSettingsPath"
    }
} else {
    Set-Content -Path $localSettingsPath -Value $localSettingsContent
    Write-Step "Created $localSettingsPath"
}

# Step 3: Set up Python venv
Write-Header "Step 3/4: Setting up Python Virtual Environment"

$venvPath = "api\.venv"
if (Test-Path $venvPath) {
    Write-Host "Virtual environment already exists at $venvPath" -ForegroundColor Yellow
    $reinstall = Read-Host "Reinstall? (y/n) [default: n]"
    if ($reinstall -eq "y") {
        Remove-Item -Path $venvPath -Recurse -Force
    } else {
        Write-Host "Skipping venv setup" -ForegroundColor Yellow
    }
}

if (-not (Test-Path $venvPath)) {
    Write-Info "Creating venv..."
    py -3.11 -m venv $venvPath
    Write-Step "Virtual environment created"
    
    Write-Info "Installing dependencies..."
    & "$venvPath\Scripts\pip.exe" install -r api/requirements.txt
    Write-Step "Dependencies installed"
}

# Step 4: Replace placeholders in extension files
Write-Header "Step 4/4: Configuring Chrome Extension"

Write-Info "Replacing placeholders in extension files..."

$extensionFiles = Get-ChildItem extension -File -Recurse | Where-Object { $_.Extension -in '.js','.json','.html' }
$replacementCount = 0

foreach ($file in $extensionFiles) {
    $content = Get-Content -Raw $file.FullName
    $originalContent = $content
    
    $content = $content -replace '<your-function-app>\.azurewebsites\.net', $ApiHost
    $content = $content -replace '<your-static-web-app>\.azurestaticapps\.net', $SwaHost
    
    if ($content -ne $originalContent) {
        Set-Content -Path $file.FullName -Value $content -NoNewline
        $replacementCount++
        Write-Info "Updated: $($file.Name)"
    }
}

Write-Step "Replaced placeholders in $replacementCount extension files"

# Step 5: Success message
Write-Header "Setup Complete! ✓"

Write-Info "Next steps:"
Write-Host ""
Write-Host "1. Backend (local dev):" -ForegroundColor Yellow
Write-Host "   cd api"
Write-Host "   .\.venv\Scripts\Activate.ps1"
Write-Host "   func start"
Write-Host ""
Write-Host "2. Frontend (Flutter):" -ForegroundColor Yellow
Write-Host "   cd app"
Write-Host "   flutter run -d chrome ``"
Write-Host "     --dart-define=API_BASE_URL=""https://$ApiHost"" ``"
Write-Host "     --dart-define=GOOGLE_CLIENT_ID=""<your-client-id>.apps.googleusercontent.com"" ``"
Write-Host "     --dart-define=ADMIN_EMAILS=""$AdminEmails"""
Write-Host ""
Write-Host "3. Extension:" -ForegroundColor Yellow
Write-Host "   chrome://extensions → Developer mode → Load unpacked → select extension/"
Write-Host ""
Write-Host "4. Test API:" -ForegroundColor Yellow
Write-Host "   curl http://localhost:7071/api/v1/health"
Write-Host ""
Write-Host "For more details, see SETUP_LOCAL.md" -ForegroundColor Cyan
Write-Host ""
