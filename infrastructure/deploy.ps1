# =========================================================================
# Azure Infrastructure Deployment Script
# =========================================================================
# This script deploys the AI Summary Internal infrastructure using Bicep

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "AI-Summary-Internal",
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "southeastasia",
    
    [Parameter(Mandatory=$false)]
    [string]$Environment = "prod",
    
    [Parameter(Mandatory=$false)]
    [switch]$WhatIf
)

# Set error action preference
$ErrorActionPreference = "Stop"

# Colors for output
function Write-ColorOutput($ForegroundColor) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    if ($args) {
        Write-Output $args
    }
    $host.UI.RawUI.ForegroundColor = $fc
}

Write-ColorOutput Green "🚀 AI Summary Internal - Infrastructure Deployment"
Write-ColorOutput Green "=================================================="

# Check if Azure CLI is installed
try {
    $azVersion = az version --output json | ConvertFrom-Json
    Write-ColorOutput Cyan "✅ Azure CLI version: $($azVersion.'azure-cli')"
} catch {
    Write-ColorOutput Red "❌ Azure CLI not found. Please install Azure CLI first."
    Write-ColorOutput Yellow "   Download from: https://docs.microsoft.com/cli/azure/install-azure-cli"
    exit 1
}

# Check if logged in to Azure
Write-ColorOutput Cyan "`n🔐 Checking Azure login status..."
$accountInfo = az account show 2>$null
if (!$accountInfo) {
    Write-ColorOutput Yellow "⚠️  Not logged in to Azure. Initiating login..."
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput Red "❌ Azure login failed"
        exit 1
    }
} else {
    $account = $accountInfo | ConvertFrom-Json
    Write-ColorOutput Green "✅ Logged in as: $($account.user.name)"
    Write-ColorOutput Green "   Subscription: $($account.name) ($($account.id))"
}

# Get current user's Object ID for Key Vault access
Write-ColorOutput Cyan "`n🔍 Getting current user Object ID..."
$currentUser = az ad signed-in-user show --query id -o tsv
if (!$currentUser) {
    Write-ColorOutput Red "❌ Failed to get user Object ID"
    exit 1
}
Write-ColorOutput Green "✅ User Object ID: $currentUser"

# Create or check resource group
Write-ColorOutput Cyan "`n📦 Checking resource group: $ResourceGroupName"
$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "false") {
    Write-ColorOutput Yellow "⚠️  Resource group doesn't exist. Creating..."
    az group create --name $ResourceGroupName --location $Location
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput Red "❌ Failed to create resource group"
        exit 1
    }
    Write-ColorOutput Green "✅ Resource group created: $ResourceGroupName"
} else {
    Write-ColorOutput Green "✅ Resource group exists: $ResourceGroupName"
}

# Update parameters file with Object ID
$paramsFile = Join-Path $PSScriptRoot "main.parameters.json"
$params = Get-Content $paramsFile | ConvertFrom-Json
$params.parameters.keyVaultAdminObjectId.value = $currentUser
$params | ConvertTo-Json -Depth 10 | Set-Content $paramsFile
Write-ColorOutput Green "✅ Updated parameters file with Object ID"

# Deploy Bicep template
Write-ColorOutput Cyan "`n🚀 Deploying infrastructure..."
$bicepFile = Join-Path $PSScriptRoot "main.bicep"

$deploymentName = "ai-summary-deployment-$(Get-Date -Format 'yyyyMMddHHmmss')"

if ($WhatIf) {
    Write-ColorOutput Yellow "`n⚠️  Running in What-If mode (no actual deployment)"
    az deployment group what-if `
        --resource-group $ResourceGroupName `
        --template-file $bicepFile `
        --parameters $paramsFile `
        --name $deploymentName
} else {
    Write-ColorOutput Cyan "`n🔨 Starting deployment: $deploymentName"
    
    $deployment = az deployment group create `
        --resource-group $ResourceGroupName `
        --template-file $bicepFile `
        --parameters $paramsFile `
        --name $deploymentName `
        --output json | ConvertFrom-Json
    
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput Red "`n❌ Deployment failed!"
        exit 1
    }
    
    Write-ColorOutput Green "`n✅ Deployment completed successfully!"
    
    # Display outputs
    Write-ColorOutput Cyan "`n📊 Deployment Outputs:"
    Write-ColorOutput Yellow "  App Service Name: $($deployment.properties.outputs.appServiceName.value)"
    Write-ColorOutput Yellow "  App Service URL: $($deployment.properties.outputs.appServiceUrl.value)"
    Write-ColorOutput Yellow "  Storage Account: $($deployment.properties.outputs.storageAccountName.value)"
    Write-ColorOutput Yellow "  Key Vault: $($deployment.properties.outputs.keyVaultName.value)"
    Write-ColorOutput Yellow "  OpenAI Endpoint: $($deployment.properties.outputs.openaiEndpoint.value)"
    
    # Post-deployment instructions
    Write-ColorOutput Green "`n🎉 Infrastructure deployment complete!"
    Write-ColorOutput Cyan "`n📝 Next Steps:"
    Write-ColorOutput Yellow "  1. Configure GitHub Actions or Azure DevOps for CI/CD"
    Write-ColorOutput Yellow "  2. Set any additional secrets in Key Vault manually:"
    Write-ColorOutput Yellow "     - gpt4o-transcribe-api-key"
    Write-ColorOutput Yellow "     - password-salt"
    Write-ColorOutput Yellow "     - transcripts-sas-token"
    Write-ColorOutput Yellow "     - chat-responses-sas-token"
    Write-ColorOutput Yellow "     - user-password-sas-token"
    Write-ColorOutput Yellow "     - meta-data-sas-token"
    Write-ColorOutput Yellow "  3. Deploy application code to App Service"
    Write-ColorOutput Yellow "  4. Configure custom domain (optional)"
    Write-ColorOutput Yellow "  5. Enable Application Insights for monitoring (optional)"
}

Write-ColorOutput Green "`n✨ Script completed successfully!"
