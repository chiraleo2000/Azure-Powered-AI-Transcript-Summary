# =========================================================================
# Docker Azure Deployment Script
# =========================================================================
# This script builds the Docker image, pushes to ACR, and deploys to App Service

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroup = "AI-Summary-Internal",
    
    [Parameter(Mandatory=$false)]
    [string]$AppServiceName = "ai-transcript-summarize-service",
    
    [Parameter(Mandatory=$false)]
    [string]$ACRName = "ocrservicecontainer",
    
    [Parameter(Mandatory=$false)]
    [string]$ImageName = "ai-summary-meeting",
    
    [Parameter(Mandatory=$false)]
    [string]$Tag = "0.1.38",
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipBuild = $false,
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipPush = $false
)

$ErrorActionPreference = "Stop"

Write-Host "☁️  Azure Docker Deployment Script" -ForegroundColor Green
Write-Host "===================================" -ForegroundColor Green

# Check Azure CLI is installed
Write-Host "`n🔍 Checking Azure CLI..." -ForegroundColor Cyan
try {
    az version | Out-Null
    Write-Host "✅ Azure CLI is installed" -ForegroundColor Green
} catch {
    Write-Host "❌ Azure CLI is not installed. Please install it first." -ForegroundColor Red
    Write-Host "   Download from: https://aka.ms/installazurecliwindows" -ForegroundColor Yellow
    exit 1
}

# Check if logged in to Azure
Write-Host "`n🔐 Checking Azure login..." -ForegroundColor Cyan
$account = az account show 2>$null | ConvertFrom-Json
if (!$account) {
    Write-Host "⚠️  Not logged in to Azure. Logging in..." -ForegroundColor Yellow
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Azure login failed!" -ForegroundColor Red
        exit 1
    }
    $account = az account show | ConvertFrom-Json
}
Write-Host "✅ Logged in as: $($account.user.name)" -ForegroundColor Green
Write-Host "   Subscription: $($account.name)" -ForegroundColor Gray

# Get or create ACR
if (!$ACRName) {
    Write-Host "`n🔍 Finding Azure Container Registry in resource group..." -ForegroundColor Cyan
    $acrs = az acr list --resource-group $ResourceGroup | ConvertFrom-Json
    if ($acrs.Count -eq 0) {
        Write-Host "⚠️  No ACR found. Creating one..." -ForegroundColor Yellow
        $ACRName = "aisummaryacr$(Get-Random -Minimum 1000 -Maximum 9999)"
        Write-Host "   Creating ACR: $ACRName" -ForegroundColor Cyan
        
        az acr create `
            --resource-group $ResourceGroup `
            --name $ACRName `
            --sku Basic `
            --admin-enabled true
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "❌ Failed to create ACR!" -ForegroundColor Red
            exit 1
        }
        Write-Host "✅ Created ACR: $ACRName" -ForegroundColor Green
    } else {
        $ACRName = $acrs[0].name
        Write-Host "✅ Found ACR: $ACRName" -ForegroundColor Green
    }
}

# Get ACR login server
$acrLoginServer = az acr show --name $ACRName --query "loginServer" -o tsv
Write-Host "   Login server: $acrLoginServer" -ForegroundColor Gray

# Check Docker is running
Write-Host "`n🔍 Checking Docker..." -ForegroundColor Cyan
try {
    docker version | Out-Null
    Write-Host "✅ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker is not running. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

# Build Docker image
if (!$SkipBuild) {
    Write-Host "`n🔨 Building Docker image..." -ForegroundColor Cyan
    $fullImageName = "${acrLoginServer}/${ImageName}:${Tag}"
    Write-Host "Image: $fullImageName" -ForegroundColor Yellow
    
    docker build -t $fullImageName .
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Docker build failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Docker image built successfully" -ForegroundColor Green
} else {
    Write-Host "`n⏭️  Skipping build (using existing image)" -ForegroundColor Yellow
    $fullImageName = "${acrLoginServer}/${ImageName}:${Tag}"
}

# Login to ACR
Write-Host "`n🔐 Logging in to Azure Container Registry..." -ForegroundColor Cyan
$acrCreds = az acr credential show --name $ACRName -o json | ConvertFrom-Json
docker login $acrLoginServer -u $acrCreds.username -p $acrCreds.passwords[0].value 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  Admin credential login failed, trying az acr login..." -ForegroundColor Yellow
    az acr login --name $ACRName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ ACR login failed!" -ForegroundColor Red
        exit 1
    }
}
Write-Host "✅ Logged in to ACR" -ForegroundColor Green

# Push image to ACR
if (!$SkipPush) {
    Write-Host "`n📤 Pushing image to ACR..." -ForegroundColor Cyan
    Write-Host "This may take a few minutes..." -ForegroundColor Yellow
    
    docker push $fullImageName
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Docker push failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Image pushed to ACR successfully" -ForegroundColor Green
} else {
    Write-Host "`n⏭️  Skipping push" -ForegroundColor Yellow
}

# Configure App Service to use Docker image
Write-Host "`n⚙️  Configuring App Service..." -ForegroundColor Cyan
Write-Host "App Service: $AppServiceName" -ForegroundColor Yellow

# Enable ACR on App Service
Write-Host "   Enabling ACR integration..." -ForegroundColor Gray
az webapp config container set `
    --name $AppServiceName `
    --resource-group $ResourceGroup `
    --container-image-name $fullImageName `
    --container-registry-url "https://${acrLoginServer}" `
    --container-registry-user $acrCreds.username `
    --container-registry-password $acrCreds.passwords[0].value

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to configure App Service!" -ForegroundColor Red
    exit 1
}

# Enable managed identity if not already enabled
Write-Host "   Enabling managed identity..." -ForegroundColor Gray
az webapp identity assign `
    --name $AppServiceName `
    --resource-group $ResourceGroup | Out-Null

# Set environment variables
Write-Host "   Setting environment variables..." -ForegroundColor Gray
az webapp config appsettings set `
    --name $AppServiceName `
    --resource-group $ResourceGroup `
    --settings `
        "USE_KEY_VAULT=True" `
        "WEBSITES_PORT=7860" `
        "DOCKER_ENABLE_CI=true" | Out-Null

# Restart App Service
Write-Host "`n🔄 Restarting App Service..." -ForegroundColor Cyan
az webapp restart `
    --name $AppServiceName `
    --resource-group $ResourceGroup

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  Restart command failed, but deployment may still be successful" -ForegroundColor Yellow
} else {
    Write-Host "✅ App Service restarted" -ForegroundColor Green
}

# Get App Service URL
$appUrl = az webapp show `
    --name $AppServiceName `
    --resource-group $ResourceGroup `
    --query "defaultHostName" -o tsv

# Display deployment information
Write-Host "`n🎉 Deployment complete!" -ForegroundColor Green
Write-Host "=============================" -ForegroundColor Green
Write-Host "📍 App URL: https://$appUrl" -ForegroundColor Cyan
Write-Host "🐳 Image: $fullImageName" -ForegroundColor Gray
Write-Host "`n⏳ Note: It may take 2-5 minutes for the app to start" -ForegroundColor Yellow
Write-Host "`n📝 Useful commands:" -ForegroundColor Yellow
Write-Host "  View logs:        az webapp log tail --name $AppServiceName --resource-group $ResourceGroup" -ForegroundColor Gray
Write-Host "  Stream logs:      az webapp log config --name $AppServiceName --resource-group $ResourceGroup --docker-container-logging filesystem" -ForegroundColor Gray
Write-Host "  Restart:          az webapp restart --name $AppServiceName --resource-group $ResourceGroup" -ForegroundColor Gray
Write-Host "  Check status:     az webapp show --name $AppServiceName --resource-group $ResourceGroup --query state" -ForegroundColor Gray

Write-Host "`n✨ Done! Check the app at https://$appUrl in a few minutes." -ForegroundColor Green
