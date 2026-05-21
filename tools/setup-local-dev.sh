#!/bin/bash

# AutoApply Local Development Setup Script (macOS/Linux)
# Usage: bash tools/setup-local-dev.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

function print_header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║ $1${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
}

function print_step() {
    echo -e "${GREEN}✓ $1${NC}"
}

function print_info() {
    echo -e "${BLUE}→ $1${NC}"
}

function read_input() {
    local prompt="$1"
    local default="$2"
    local response
    
    if [ -n "$default" ]; then
        read -p "$(echo -e ${BLUE}$prompt [default: $default]${NC}): " response
        echo "${response:-$default}"
    else
        read -p "$(echo -e ${BLUE}$prompt${NC}): " response
        echo "$response"
    fi
}

print_header "AutoApply Local Development Setup"

print_info "This script will:"
print_info "1. Gather your Azure resource information"
print_info "2. Create api/local.settings.json"
print_info "3. Set up Python virtual environment"
print_info "4. Replace placeholders in extension files"
echo ""

# Step 1: Gather Azure Resource Info
print_header "Step 1/4: Azure Resource Information"

API_HOST=$(read_input "Enter Function App hostname (e.g., myapp-func-dev.azurewebsites.net)")
print_step "API Host: $API_HOST"

SWA_HOST=$(read_input "Enter Static Web App hostname (e.g., kind-bay-12345.azurestaticapps.net)")
print_step "SWA Host: $SWA_HOST"

COSMOS_ENDPOINT=$(read_input "Enter Cosmos DB endpoint (e.g., https://myapp-cosmos.documents.azure.com:443/)")
print_step "Cosmos Endpoint: $COSMOS_ENDPOINT"

print_info "Enter Cosmos DB primary key (will not be echoed):"
read -s COSMOS_KEY
echo ""
print_step "Cosmos Key: (set)"

print_info "Enter Blob Storage connection string (will not be echoed):"
read -s BLOB_CONN
echo ""
print_step "Blob Connection String: (set)"

AI_ENDPOINT=$(read_input "Enter Azure AI endpoint (e.g., https://myapp-ai.cognitiveservices.azure.com/)")
print_step "AI Endpoint: $AI_ENDPOINT"

print_info "Enter Azure AI key (will not be echoed):"
read -s AI_KEY
echo ""
print_step "AI Key: (set)"

# Generate secrets
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
print_step "JWT Secret: (generated)"

ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
print_step "Admin Token: (generated)"

GOOGLE_CLIENT_ID=$(read_input "Enter Google OAuth Client ID (e.g., xxx.apps.googleusercontent.com)")
print_step "Google Client ID: (set)"

ADMIN_EMAILS=$(read_input "Enter admin emails (comma-separated)" "you@example.com")
print_step "Admin Emails: $ADMIN_EMAILS"

# Step 2: Create local.settings.json
print_header "Step 2/4: Creating api/local.settings.json"

printf "%b" "${YELLOW}Overwrite if exists? (y/n) [default: y]${NC}: "
read overwrite
overwrite=${overwrite:-y}

if [ "$overwrite" = "y" ] || [ "$overwrite" = "Y" ]; then
    cat > api/local.settings.json << EOF
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "COSMOS_ENDPOINT": "$COSMOS_ENDPOINT",
    "COSMOS_KEY": "$COSMOS_KEY",
    "COSMOS_DATABASE": "autoapply",
    "BLOB_CONNECTION_STRING": "$BLOB_CONN",
    "AZURE_AI_ENDPOINT": "$AI_ENDPOINT",
    "AZURE_AI_KEY": "$AI_KEY",
    "JWT_SECRET": "$JWT_SECRET",
    "ADMIN_API_TOKEN": "$ADMIN_TOKEN",
    "AI_RERANK_MODEL": "gpt4omini",
    "AI_PARSE_MODEL": "gpt41",
    "AI_REVIEW_MODEL": "gpt4omini",
    "RERANK_SKIP_GAP": "15",
    "FREE_TIER_DAILY_DISCOVER_LIMIT": "50",
    "RESUME_TAILOR_MAX_JOBS": "50",
    "SUPER_ADMIN_EMAILS": "$ADMIN_EMAILS"
  },
  "Host": {
    "CORS": "*",
    "CORSCredentials": false
  }
}
EOF
    print_step "Created api/local.settings.json"
else
    echo -e "${YELLOW}Skipping local.settings.json creation${NC}"
fi

# Step 3: Set up Python venv
print_header "Step 3/4: Setting up Python Virtual Environment"

if [ -d "api/.venv" ]; then
    echo -e "${YELLOW}Virtual environment already exists at api/.venv${NC}"
    printf "%b" "${YELLOW}Reinstall? (y/n) [default: n]${NC}: "
    read reinstall
    if [ "$reinstall" = "y" ] || [ "$reinstall" = "Y" ]; then
        rm -rf api/.venv
    else
        echo -e "${YELLOW}Skipping venv setup${NC}"
        reinstall="n"
    fi
else
    reinstall="y"
fi

if [ "$reinstall" = "y" ] || [ "$reinstall" = "Y" ]; then
    print_info "Creating venv..."
    python3 -m venv api/.venv
    print_step "Virtual environment created"
    
    print_info "Installing dependencies..."
    source api/.venv/bin/activate
    pip install -r api/requirements.txt
    deactivate
    print_step "Dependencies installed"
fi

# Step 4: Replace placeholders in extension files
print_header "Step 4/4: Configuring Chrome Extension"

print_info "Replacing placeholders in extension files..."

cd extension
replacement_count=0

for file in $(find . -type f \( -name "*.js" -o -name "*.json" -o -name "*.html" \)); do
    if grep -q '<your-function-app>\|<your-static-web-app>' "$file" 2>/dev/null; then
        sed -i '' "s|<your-function-app>\.azurewebsites\.net|$API_HOST|g" "$file"
        sed -i '' "s|<your-static-web-app>\.azurestaticapps\.net|$SWA_HOST|g" "$file"
        replacement_count=$((replacement_count + 1))
        print_info "Updated: $file"
    fi
done

cd ..

print_step "Replaced placeholders in $replacement_count extension files"

# Final success message
print_header "Setup Complete! ✓"

print_info "Next steps:"
echo ""
echo -e "${YELLOW}1. Backend (local dev):${NC}"
echo "   cd api"
echo "   source .venv/bin/activate"
echo "   func start"
echo ""
echo -e "${YELLOW}2. Frontend (Flutter):${NC}"
echo "   cd app"
echo "   flutter run -d chrome \\"
echo "     --dart-define=API_BASE_URL=\"https://$API_HOST\" \\"
echo "     --dart-define=GOOGLE_CLIENT_ID=\"$GOOGLE_CLIENT_ID\" \\"
echo "     --dart-define=ADMIN_EMAILS=\"$ADMIN_EMAILS\""
echo ""
echo -e "${YELLOW}3. Extension:${NC}"
echo "   chrome://extensions → Developer mode → Load unpacked → select extension/"
echo ""
echo -e "${YELLOW}4. Test API:${NC}"
echo "   curl http://localhost:7071/api/v1/health"
echo ""
echo -e "${CYAN}For more details, see SETUP_LOCAL.md${NC}"
echo ""
