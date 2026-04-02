# 🚀 Quick Start Guide

Get AI Summary Internal up and running in 15 minutes!

## Prerequisites

- ✅ Azure subscription
- ✅ Azure CLI installed ([Download](https://aka.ms/cli))
- ✅ PowerShell 7+ ([Download](https://aka.ms/powershell))
- ✅ Git

## Step 1: Clone Repository (2 min)

```bash
git clone <your-repo-url>
cd Azure_Powered_AI_SummaryV0.2
```

## Step 2: Login to Azure (1 min)

```powershell
az login
```

Select your subscription:
```powershell
az account set --subscription "<your-subscription-id>"
```

## Step 3: Deploy Infrastructure (5 min)

```powershell
cd infrastructure
.\deploy.ps1
```

This creates:
- ✅ Resource Group
- ✅ App Service
- ✅ Azure OpenAI
- ✅ Speech Services
- ✅ Computer Vision
- ✅ Storage Account
- ✅ Key Vault access

**Note:** Deployment takes ~5-7 minutes. ☕

## Step 4: Configure Secrets (3 min)

```powershell
# Still in infrastructure directory
.\set-keyvault-secrets.ps1
```

The script will:
1. Auto-retrieve keys from Azure services
2. Generate secure password salt
3. Prompt for any missing values

**Important:** Save the generated password salt somewhere safe!

## Step 5: Deploy Application Code (2 min)

### Option A: Deploy from Local

```powershell
cd ..  # Back to root directory

# Create deployment package
zip -r deploy.zip . -x "*.git*" "local_storage/*" "*.pyc"

# Deploy to App Service
az webapp deployment source config-zip \
  --resource-group AI-Summary-Internal \
  --name AI-Summary-Internal-app-service \
  --src deploy.zip
```

### Option B: Deploy via Docker

```powershell
# Build Docker image
docker build -t ai-summary:latest .

# Tag for Azure Container Registry
docker tag ai-summary:latest <your-acr>.azurecr.io/ai-summary:latest

# Push to ACR
az acr login --name <your-acr>
docker push <your-acr>.azurecr.io/ai-summary:latest

# Configure App Service
az webapp config container set \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --docker-custom-image-name <your-acr>.azurecr.io/ai-summary:latest
```

## Step 6: Verify Deployment (2 min)

1. **Get App URL:**
   ```powershell
   az webapp show \
     --name AI-Summary-Internal-app-service \
     --resource-group AI-Summary-Internal \
     --query "defaultHostName" -o tsv
   ```

2. **Open in browser:**
   ```
   https://<your-app>.azurewebsites.net
   ```

3. **Create first user account**

4. **Test transcription:**
   - Upload a sample audio file (MP3, WAV)
   - Select language
   - Click "Start Transcription"
   - Verify transcript appears

## 🎉 Success!

Your AI Summary service is now running!

---

## Next Steps

### 1. Configure Custom Domain (Optional)

```powershell
# Add custom domain
az webapp config hostname add \
  --webapp-name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --hostname "aisummary.yourdomain.com"

# Enable HTTPS
az webapp config ssl bind \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --certificate-thumbprint <cert-thumbprint> \
  --ssl-type SNI
```

### 2. Enable Application Insights

```powershell
# Create Application Insights
az monitor app-insights component create \
  --app ai-summary-insights \
  --location southeastasia \
  --resource-group AI-Summary-Internal

# Get instrumentation key
$insightsKey = az monitor app-insights component show \
  --app ai-summary-insights \
  --resource-group AI-Summary-Internal \
  --query instrumentationKey -o tsv

# Configure App Service
az webapp config appsettings set \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --settings "APPINSIGHTS_INSTRUMENTATIONKEY=$insightsKey"
```

### 3. Set Up Backup & Monitoring

```powershell
# Enable backup
az webapp config backup create \
  --resource-group AI-Summary-Internal \
  --webapp-name AI-Summary-Internal-app-service \
  --backup-name "daily-backup" \
  --container-url "<storage-sas-url>"

# Configure alerts
az monitor metrics alert create \
  --name "High-CPU-Alert" \
  --resource-group AI-Summary-Internal \
  --scopes "/subscriptions/<sub-id>/resourceGroups/AI-Summary-Internal/providers/Microsoft.Web/sites/AI-Summary-Internal-app-service" \
  --condition "avg Percentage CPU > 80" \
  --window-size 5m \
  --evaluation-frequency 1m
```

### 4. Review Security

See [SECURITY.md](SECURITY.md) for:
- Secret rotation procedures
- Access control best practices
- Audit logging setup
- Compliance requirements

---

## Troubleshooting

### Issue: Deployment fails with "Key Vault not found"

**Solution:** Ensure Key Vault was created successfully:
```powershell
az keyvault show --name ai-summary-keyvault
```

### Issue: "Access denied" when retrieving secrets

**Solution:** Grant Managed Identity access:
```powershell
$principalId = az webapp identity show \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --query principalId -o tsv

az keyvault set-policy \
  --name ai-summary-keyvault \
  --object-id $principalId \
  --secret-permissions get list
```

### Issue: App Service shows "Service Unavailable"

**Solution:** Check application logs:
```powershell
az webapp log tail \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal
```

### Issue: Transcription fails

**Solution:** Verify Cognitive Services keys:
```powershell
# Check Speech Service
az cognitiveservices account keys list \
  --name AI-Summary-Internal-speech-to-text \
  --resource-group AI-Summary-Internal

# Verify key in Key Vault
az keyvault secret show \
  --vault-name ai-summary-keyvault \
  --name azure-speech-key-backup
```

---

## Getting Help

1. **Check Logs:**
   - Application logs: `az webapp log tail ...`
   - Key Vault logs: Azure Portal → Key Vault → Diagnostic settings

2. **Review Documentation:**
   - [Infrastructure README](infrastructure/README.md)
   - [SECURITY.md](SECURITY.md)
   - [Main README](README.md)

3. **Common Issues:**
   - See troubleshooting section above
   - Check [GitHub Issues](link-to-issues)

4. **Contact Support:**
   - Email: support@yourcompany.com
   - Teams: #ai-summary-support

---

## Clean Up (If Testing)

**WARNING:** This deletes all resources!

```powershell
az group delete --name AI-Summary-Internal --yes --no-wait
```

---

## What's Next?

- 📊 Set up monitoring dashboards
- 🔄 Configure CI/CD pipeline
- 🧪 Run integration tests
- 📱 Mobile-friendly UI improvements
- 🌐 Multi-language support expansion

Happy transcribing! 🎙️✨
