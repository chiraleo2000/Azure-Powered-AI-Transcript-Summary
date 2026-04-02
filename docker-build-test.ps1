# =========================================================================
# Docker Build and Local Test Script
# =========================================================================
# This script builds the Docker image and runs it locally for testing

param(
    [Parameter(Mandatory=$false)]
    [string]$ImageName = "ai-summary-app",
    
    [Parameter(Mandatory=$false)]
    [string]$Tag = "latest",
    
    [Parameter(Mandatory=$false)]
    [int]$Port = 7860,
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipBuild = $false,
    
    [Parameter(Mandatory=$false)]
    [switch]$NoCacheEnv = $false
)

$ErrorActionPreference = "Stop"

Write-Host "🐳 Docker Build & Test Script" -ForegroundColor Green
Write-Host "=============================" -ForegroundColor Green

# Check Docker is running
Write-Host "`n🔍 Checking Docker..." -ForegroundColor Cyan
try {
    docker version | Out-Null
    Write-Host "✅ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker is not running. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

# Check .env file exists (only if not using NoCacheEnv)
if (!$NoCacheEnv) {
    if (!(Test-Path ".env")) {
        Write-Host "⚠️  .env file not found. Creating from .env.example..." -ForegroundColor Yellow
        if (Test-Path ".env.example") {
            Copy-Item ".env.example" ".env"
            Write-Host "✅ Created .env from template. Please edit it with your values." -ForegroundColor Green
            Write-Host "   Then run this script again." -ForegroundColor Yellow
            exit 0
        } else {
            Write-Host "❌ .env.example not found. Cannot proceed." -ForegroundColor Red
            exit 1
        }
    }
}

# Build Docker image
if (!$SkipBuild) {
    Write-Host "`n🔨 Building Docker image..." -ForegroundColor Cyan
    Write-Host "Image: ${ImageName}:${Tag}" -ForegroundColor Yellow
    
    docker build -t "${ImageName}:${Tag}" .
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Docker build failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Docker image built successfully" -ForegroundColor Green
} else {
    Write-Host "`n⏭️  Skipping build (using existing image)" -ForegroundColor Yellow
}

# Stop any running container with the same name
Write-Host "`n🧹 Cleaning up old containers..." -ForegroundColor Cyan
docker stop "${ImageName}" 2>$null
docker rm "${ImageName}" 2>$null

# Prepare environment variables
$envArgs = @()
if (!$NoCacheEnv) {
    Write-Host "📝 Using .env file for configuration" -ForegroundColor Cyan
    # Check if .env file exists
    if (!(Test-Path ".env")) {
        Write-Host "❌ .env file not found!" -ForegroundColor Red
        exit 1
    }
    # Use .env file and override Key Vault setting
    $envArgs += "--env-file"
    $envArgs += ".env"
    $envArgs += "--env"
    $envArgs += "USE_KEY_VAULT=False"
    $envArgs += "--env"
    $envArgs += "LOCAL_TESTING_MODE=False"
}

# Run Docker container
Write-Host "`n🚀 Starting container..." -ForegroundColor Cyan
Write-Host "Port: $Port" -ForegroundColor Yellow
Write-Host "Container name: $ImageName" -ForegroundColor Yellow

docker run -d `
    --name "${ImageName}" `
    -p "${Port}:7860" `
    @envArgs `
    --restart unless-stopped `
    "${ImageName}:${Tag}"

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to start container!" -ForegroundColor Red
    exit 1
}

# Wait for container to start
Write-Host "`n⏳ Waiting for application to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 5

# Check if container is running
$containerStatus = docker ps --filter "name=${ImageName}" --format "{{.Status}}"
if ($containerStatus) {
    Write-Host "✅ Container is running: $containerStatus" -ForegroundColor Green
} else {
    Write-Host "❌ Container failed to start!" -ForegroundColor Red
    Write-Host "`n📋 Container logs:" -ForegroundColor Yellow
    docker logs "${ImageName}"
    exit 1
}

# Display access information
Write-Host "`n🎉 Application is running!" -ForegroundColor Green
Write-Host "=============================" -ForegroundColor Green
Write-Host "📍 URL: http://localhost:$Port" -ForegroundColor Cyan
Write-Host "`n📝 Useful commands:" -ForegroundColor Yellow
Write-Host "  View logs:    docker logs -f ${ImageName}" -ForegroundColor Gray
Write-Host "  Stop:         docker stop ${ImageName}" -ForegroundColor Gray
Write-Host "  Restart:      docker restart ${ImageName}" -ForegroundColor Gray
Write-Host "  Remove:       docker rm -f ${ImageName}" -ForegroundColor Gray
Write-Host "  Shell access: docker exec -it ${ImageName} /bin/bash" -ForegroundColor Gray

# Open browser (optional)
Write-Host "`n🌐 Opening browser..." -ForegroundColor Cyan
Start-Sleep -Seconds 3
Start-Process "http://localhost:$Port"

Write-Host "`n✨ Done! Application is ready for testing." -ForegroundColor Green
