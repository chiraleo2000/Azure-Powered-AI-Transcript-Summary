# Docker Deployment Guide

## Overview

This guide covers three Docker deployment scenarios:

1. **Local Development** - Using Docker Compose for quick local testing
2. **Local Docker Testing** - Using PowerShell scripts for more control
3. **Azure Production** - Deploying to Azure Container Registry and App Service

---

## Prerequisites

### All Scenarios

- Docker Desktop installed and running
- `.env` file configured with all required secrets

### Azure Deployment Only

- Azure CLI installed (`az --version`)
- Azure subscription access
- Logged in to Azure (`az login`)
- Existing Azure resources:
  - Resource Group: `AI-Summary-Internal`
  - App Service: `ai-summarize-service`
  - Key Vault: `ai-summary-keyvault`
  - Storage Account: `aisummarymeetingstorage`

---

## Scenario 1: Local Development with Docker Compose

**Best for:** Quick local testing with automatic restarts and volume persistence

### Steps

1. **Configure environment variables**

   ```powershell
   # Copy template if needed
   Copy-Item .env.example .env
   
   # Edit .env with your values
   notepad .env
   ```

2. **Start the application**

   ```powershell
   docker compose up --build -d
   ```

3. **View logs**

   ```powershell
   docker compose logs -f
   ```

4. **Access the application**

   ```text
   http://localhost:7860
   ```

5. **Stop the application**

   ```powershell
   docker compose down
   ```

### How `.env` works in Docker

- `docker-compose.yml` uses `env_file: .env`, so your API keys are injected into the container at runtime.
- `.env` is intentionally excluded from the Docker build context by `.dockerignore`, so secrets are not baked into the image.
- The compose file also forces `USE_KEY_VAULT=False` for local Docker, which makes the app use your `.env` values instead of trying Managed Identity or Key Vault.

### Features

- ✅ Automatic restart on failure
- ✅ Health checks enabled
- ✅ Persistent local storage via volumes
- ✅ Hot-reload capability (uncomment volume mounts in docker-compose.yml)

---

## Scenario 2: Local Docker Testing with PowerShell

**Best for:** Testing the exact production image locally before deployment

### Usage

```powershell
.\docker-build-test.ps1
```

### Parameters

| Parameter | Description | Default |
| --------- | ----------- | ------- |
| `-ImageName` | Docker image name | `ai-summary-app` |
| `-Tag` | Image tag | `latest` |
| `-Port` | Host port to expose | `7860` |
| `-SkipBuild` | Skip build and use existing image | `false` |
| `-NoCacheEnv` | Don't use .env file (for Key Vault testing) | `false` |

### Examples

**Basic build and run:**

```powershell
.\docker-build-test.ps1
```

**Custom port:**

```powershell
.\docker-build-test.ps1 -Port 8080
```

**Skip build (use existing image):**

```powershell
.\docker-build-test.ps1 -SkipBuild
```

**Custom image name and tag:**

```powershell
.\docker-build-test.ps1 -ImageName "my-app" -Tag "v1.0.0"
```

### What it does

1. ✅ Checks Docker is running
2. ✅ Validates `.env` file exists
3. ✅ Builds Docker image (unless `-SkipBuild`)
4. ✅ Stops and removes old container
5. ✅ Runs new container with env vars from `.env`
6. ✅ Waits for container to start
7. ✅ Opens browser automatically
8. ✅ Displays useful commands

### Troubleshooting

**Container fails to start:**

```powershell
docker logs ai-summary-app
```

**Check running containers:**

```powershell
docker ps -a
```

**Access container shell:**

```powershell
docker exec -it ai-summary-app /bin/bash
```

**Remove and rebuild:**

```powershell
docker stop ai-summary-app
docker rm ai-summary-app
docker rmi ai-summary-app:latest
.\docker-build-test.ps1
```

---

## Scenario 3: Azure Production Deployment

**Best for:** Deploying to production Azure App Service with Container Registry

### Deployment Usage

```powershell
.\docker-deploy-azure.ps1
```

### Deployment Parameters

| Parameter | Description | Default |
| --------- | ----------- | ------- |
| `-ResourceGroup` | Azure resource group name | `AI-Summary-Internal` |
| `-AppServiceName` | App Service name | `ai-summarize-service` |
| `-ACRName` | Azure Container Registry name | Auto-detected/created |
| `-ImageName` | Docker image name | `ai-summary-app` |
| `-Tag` | Image tag | `latest` |
| `-SkipBuild` | Skip build step | `false` |
| `-SkipPush` | Skip push to ACR | `false` |

### Deployment Examples

**Full deployment (build + push + deploy):**

```powershell
.\docker-deploy-azure.ps1
```

**Deploy to specific ACR:**

```powershell
.\docker-deploy-azure.ps1 -ACRName "myacr"
```

**Skip build (push existing image):**

```powershell
.\docker-deploy-azure.ps1 -SkipBuild
```

**Custom resource group and app:**

```powershell
.\docker-deploy-azure.ps1 `
    -ResourceGroup "MyResourceGroup" `
    -AppServiceName "my-app-service"
```

**Version tagging:**

```powershell
.\docker-deploy-azure.ps1 -Tag "v1.2.3"
```

### Deployment Steps

1. ✅ Checks Azure CLI installation
2. ✅ Verifies Azure login
3. ✅ Auto-detects or creates Azure Container Registry
4. ✅ Builds Docker image with ACR tag
5. ✅ Logs into Azure Container Registry
6. ✅ Pushes image to ACR
7. ✅ Configures App Service to use the image
8. ✅ Enables Managed Identity
9. ✅ Sets environment variables (`USE_KEY_VAULT=True`)
10. ✅ Restarts App Service
11. ✅ Displays app URL and useful commands

### Post-Deployment

**View application logs:**

```powershell
az webapp log tail `
    --name ai-summarize-service `
    --resource-group AI-Summary-Internal
```

**Enable container logging:**

```powershell
az webapp log config `
    --name ai-summarize-service `
    --resource-group AI-Summary-Internal `
    --docker-container-logging filesystem
```

**Check app status:**

```powershell
az webapp show `
    --name ai-summarize-service `
    --resource-group AI-Summary-Internal `
    --query state
```

**Restart app:**

```powershell
az webapp restart `
    --name ai-summarize-service `
    --resource-group AI-Summary-Internal
```

### Important Notes

⏳ **Startup time:** The app may take 2-5 minutes to fully start after deployment

🔐 **Managed Identity:** The script automatically enables system-assigned managed identity for secure Key Vault access

🔄 **Continuous Deployment:** Set `DOCKER_ENABLE_CI=true` to enable automatic deployments on image updates

---

## Environment Variables

### Local Testing (.env file)

```bash
USE_KEY_VAULT=False
LOCAL_TESTING_MODE=False

# Azure OpenAI
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=your-deployment
AZURE_OPENAI_API_VERSION=2024-02-15-preview

# Azure Speech
AZURE_SPEECH_KEY=your-key
AZURE_SPEECH_REGION=your-region

# Azure Computer Vision
AZURE_COMPUTER_VISION_KEY=your-key
AZURE_COMPUTER_VISION_ENDPOINT=https://your-resource.cognitiveservices.azure.com/

# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=your-connection-string
AZURE_STORAGE_ACCOUNT_NAME=your-account
AZURE_STORAGE_ACCOUNT_KEY=your-key

# Application
PASSWORD_SALT=your-salt
ADMIN_USERNAME=admin
SESSION_TIMEOUT_MINUTES=60
```

### Azure Production (App Service settings)

```bash
USE_KEY_VAULT=True
WEBSITES_PORT=7860
DOCKER_ENABLE_CI=true

# All secrets retrieved from Key Vault via Managed Identity
# No secrets stored in App Service configuration
```

---

## Architecture Overview

### Local Development Flow

```text
Docker Container
├── Loads .env file
├── USE_KEY_VAULT=False
├── Secrets from environment variables
└── Serves on http://localhost:7860
```

### Azure Production Flow

```text
App Service (Docker)
├── Managed Identity enabled
├── USE_KEY_VAULT=True
├── Secrets from Key Vault
│   ├── azure-openai-key
│   ├── azure-speech-key
│   ├── azure-computer-vision-key
│   ├── azure-storage-connection-string
│   └── password-salt
└── Serves on https://ai-summarize-service.azurewebsites.net
```

---

## Container Health Checks

The application includes health checks:

```yaml
healthcheck:
  test: curl -f http://localhost:7860/
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

- Checks every 30 seconds
- 40-second grace period on startup
- 3 retry attempts before marking unhealthy

---

## General Troubleshooting

**Problem:** Dependencies fail to install

```powershell
# Check Python version in image
docker run --rm ai-summary-app:latest python --version

# Rebuild without cache
docker build --no-cache -t ai-summary-app:latest .
```

**Problem:** Files not found during build

```powershell
# Check .dockerignore isn't excluding needed files
cat .dockerignore

# List files being copied
docker build --progress=plain -t ai-summary-app:latest . 2>&1 | Select-String "COPY"
```

### Runtime Issues

**Problem:** Container exits immediately

```powershell
# Check logs
docker logs ai-summary-app

# Run interactively
docker run -it --rm ai-summary-app:latest /bin/bash
```

**Problem:** Can't access on localhost

```powershell
# Verify port mapping
docker ps

# Check if port is in use
netstat -ano | findstr :7860

# Try different port
docker run -p 8080:7860 ai-summary-app:latest
```

### Azure Deployment Issues

**Problem:** ACR authentication fails

```powershell
# Re-login to ACR
az acr login --name your-acr-name

# Get ACR credentials
az acr credential show --name your-acr-name
```

**Problem:** App Service won't pull image

```powershell
# Enable ACR admin user
az acr update --name your-acr-name --admin-enabled true

# Verify webhook is configured
az webapp deployment container show-cd-url --name ai-summarize-service --resource-group AI-Summary-Internal
```

**Problem:** App shows "Application Error"

```powershell
# Check Key Vault access
az keyvault show --name ai-summary-keyvault

# Verify managed identity has permissions
az webapp identity show --name ai-summarize-service --resource-group AI-Summary-Internal

# Check application logs
az webapp log tail --name ai-summarize-service --resource-group AI-Summary-Internal
```

---

## Security Best Practices

✅ **Never commit `.env` file** - Use `.env.example` as template

✅ **Use Key Vault in production** - All secrets from Key Vault, not env vars

✅ **Enable Managed Identity** - Passwordless authentication to Azure services

✅ **Scan images regularly** - Use `docker scan ai-summary-app:latest`

✅ **Keep base images updated** - Rebuild regularly for security patches

✅ **Minimize image size** - Use `.dockerignore` to exclude unnecessary files

✅ **Run as non-root user** - Dockerfile uses `appuser` (already configured)

---

## Next Steps

1. **Test locally first:**

   ```powershell
   .\docker-build-test.ps1
   ```

2. **Deploy to Azure:**

   ```powershell
   .\docker-deploy-azure.ps1
   ```

3. **Monitor the application:**

   ```powershell
   az webapp log tail --name ai-summarize-service --resource-group AI-Summary-Internal
   ```

4. **Set up continuous deployment** (optional):
   - Configure GitHub Actions or Azure DevOps
   - Trigger on push to main branch
   - Automatically build and push to ACR

---

## Quick Reference Commands

### Docker Compose

```powershell
docker-compose up -d          # Start in background
docker-compose logs -f        # View logs
docker-compose restart        # Restart services
docker-compose down           # Stop and remove
docker-compose ps             # List services
```

### Docker CLI

```powershell
docker build -t app:tag .              # Build image
docker run -p 7860:7860 app:tag        # Run container
docker ps                               # List running
docker logs <container>                 # View logs
docker exec -it <container> /bin/bash   # Shell access
docker stop <container>                 # Stop
docker rm <container>                   # Remove
docker images                           # List images
docker rmi <image>                      # Remove image
```

### Azure CLI

```powershell
az login                                      # Login
az account show                               # Show account
az acr list                                   # List registries
az webapp list                                # List web apps
az webapp log tail --name <app> --resource-group <rg>  # View logs
```

---

For more information, see:

- [Dockerfile](Dockerfile)
- [docker-compose.yml](docker-compose.yml)
- [DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md)
- [QUICKSTART.md](QUICKSTART.md)
