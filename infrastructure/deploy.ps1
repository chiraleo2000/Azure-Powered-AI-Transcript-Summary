# =========================================================================
# Azure Infrastructure Deployment Script (Creates All Resources)
# =========================================================================
# This script deploys ALL AI Summary infrastructure from scratch using Bicep,
# then builds and pushes Docker image to ACR.

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "ai-summary-prod-rg",
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "southeastasia",
    
    [Parameter(Mandatory=$false)]
    [string]$Environment = "prod",
    
    [Parameter(Mandatory=$false)]
    [string]$SubscriptionName = "",
    
    [Parameter(Mandatory=$false)]
    [string]$AcrName = "aisummaryprodacr",
    
    [Parameter(Mandatory=$false)]
    [string]$DockerImageName = "ai-summary",
    
    [Parameter(Mandatory=$false)]
    [string]$DockerImageTag = "latest",
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipDockerBuild,
    
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

Write-ColorOutput Green "🚀 AI Summary Production - Full Infrastructure Deployment"
Write-ColorOutput Green "=========================================================="

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
    $accountInfo = az account show 2>$null
}

$account = $accountInfo | ConvertFrom-Json
Write-ColorOutput Green "✅ Logged in as: $($account.user.name)"
Write-ColorOutput Green "   Subscription: $($account.name) ($($account.id))"

# Switch subscription if specified
if ($SubscriptionName) {
    Write-ColorOutput Cyan "`n🔄 Switching to subscription: $SubscriptionName"
    az account set --subscription $SubscriptionName
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput Red "❌ Failed to switch subscription"
        exit 1
    }
    $account = az account show | ConvertFrom-Json
    Write-ColorOutput Green "✅ Active subscription: $($account.name) ($($account.id))"
}

# Get current user's Object ID for Key Vault access
Write-ColorOutput Cyan "`n🔍 Getting current user Object ID..."
$currentUser = az ad signed-in-user show --query id -o tsv
if (!$currentUser) {
    Write-ColorOutput Red "❌ Failed to get user Object ID"
    exit 1
}
Write-ColorOutput Green "✅ User Object ID: $currentUser"

# Create resource group
Write-ColorOutput Cyan "`n📦 Creating resource group: $ResourceGroupName"
$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "false") {
    az group create --name $ResourceGroupName --location $Location --tags Environment=$Environment Application=AI-Summary-Production
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput Red "❌ Failed to create resource group"
        exit 1
    }
    Write-ColorOutput Green "✅ Resource group created: $ResourceGroupName"
} else {
    Write-ColorOutput Green "✅ Resource group already exists: $ResourceGroupName"
}

# Update parameters file with Object ID
$paramsFile = Join-Path $PSScriptRoot "main.parameters.json"
$params = Get-Content $paramsFile | ConvertFrom-Json
$params.parameters.keyVaultAdminObjectId.value = $currentUser
$params | ConvertTo-Json -Depth 10 | Set-Content $paramsFile
Write-ColorOutput Green "✅ Updated parameters file with Object ID"

# Deploy Bicep template
Write-ColorOutput Cyan "`n🚀 Deploying infrastructure (creates all resources)..."
$bicepFile = Join-Path $PSScriptRoot "main.bicep"
$deploymentName = "ai-summary-prod-$(Get-Date -Format 'yyyyMMddHHmmss')"

if ($WhatIf) {
    Write-ColorOutput Yellow "`n⚠️  Running in What-If mode (no actual deployment)"
    az deployment group what-if `
        --resource-group $ResourceGroupName `
        --template-file $bicepFile `
        --parameters $paramsFile `
        --name $deploymentName
} else {
    Write-ColorOutput Cyan "`n🔨 Starting deployment: $deploymentName"
    Write-ColorOutput Yellow "   This will create: Storage, Key Vault, Speech (2 regions), OpenAI, Computer Vision, ACR, App Service"
    
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
    
    Write-ColorOutput Green "`n✅ Infrastructure deployment completed successfully!"
    
    # Display outputs
    Write-ColorOutput Cyan "`n📊 Deployment Outputs:"
    Write-ColorOutput Yellow "  App Service:      $($deployment.properties.outputs.appServiceName.value)"
    Write-ColorOutput Yellow "  App URL:          $($deployment.properties.outputs.appServiceUrl.value)"
    Write-ColorOutput Yellow "  Storage Account:  $($deployment.properties.outputs.storageAccountName.value)"
    Write-ColorOutput Yellow "  Key Vault:        $($deployment.properties.outputs.keyVaultName.value)"
    Write-ColorOutput Yellow "  ACR Login Server: $($deployment.properties.outputs.acrLoginServer.value)"
    Write-ColorOutput Yellow "  OpenAI Endpoint:  $($deployment.properties.outputs.openaiEndpoint.value)"
    Write-ColorOutput Yellow "  CV Endpoint:      $($deployment.properties.outputs.computerVisionEndpoint.value)"
    Write-ColorOutput Yellow "  Speech Primary:   $($deployment.properties.outputs.speechPrimaryEndpoint.value)"
    Write-ColorOutput Yellow "  Speech Backup:    $($deployment.properties.outputs.speechBackupEndpoint.value)"
    
    # Build and push Docker image to ACR (cloud build — no local Docker needed)
    if (-not $SkipDockerBuild) {
        Write-ColorOutput Cyan "`n🐳 Building and pushing Docker image to ACR..."
        $acrLoginServer = $deployment.properties.outputs.acrLoginServer.value
        $projectRoot = Split-Path $PSScriptRoot -Parent
        
        Write-ColorOutput Yellow "   Building in ACR cloud (no local Docker required)..."
        Write-ColorOutput Yellow "   Image: ${acrLoginServer}/${DockerImageName}:${DockerImageTag}"
        
        az acr build `
            --registry $AcrName `
            --resource-group $ResourceGroupName `
            --image "${DockerImageName}:${DockerImageTag}" `
            --file "$projectRoot/Dockerfile" `
            $projectRoot
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput Red "❌ Docker build failed"
            Write-ColorOutput Yellow "   You can retry later with: az acr build --registry $AcrName --image ${DockerImageName}:${DockerImageTag} ."
        } else {
            Write-ColorOutput Green "✅ Docker image built and pushed: ${acrLoginServer}/${DockerImageName}:${DockerImageTag}"
            
            # Restart App Service to pull latest image
            Write-ColorOutput Cyan "`n🔄 Restarting App Service to pull latest image..."
            $appName = $deployment.properties.outputs.appServiceName.value
            az webapp restart --name $appName --resource-group $ResourceGroupName
            Write-ColorOutput Green "✅ App Service restarted"
        }
    } else {
        Write-ColorOutput Yellow "`n⏭️  Skipping Docker build (use -SkipDockerBuild:$false to build)"
    }
    
    # Post-deployment instructions
    Write-ColorOutput Green "`n🎉 Full deployment complete!"
    Write-ColorOutput Cyan "`n📝 Next Steps:"
    Write-ColorOutput Yellow "  1. Set the password-salt secret in Key Vault (if not set via parameter):"
    Write-ColorOutput Yellow "     az keyvault secret set --vault-name $($deployment.properties.outputs.keyVaultName.value) --name password-salt --value '<your-salt>'"
    Write-ColorOutput Yellow "  2. Access the app at: $($deployment.properties.outputs.appServiceUrl.value)"
    Write-ColorOutput Yellow "  3. Default admin login: admin / admin123 (change immediately!)"
    Write-ColorOutput Yellow "  4. Configure custom domain (optional)"
}

Write-ColorOutput Green "`n✨ Script completed successfully!"
