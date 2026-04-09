# Azure Infrastructure for AI Summary Internal

This directory contains the Infrastructure as Code (IaC) using Azure Bicep for deploying the AI Summary Internal application.

## 📋 Prerequisites

- Azure CLI installed ([Download](https://docs.microsoft.com/cli/azure/install-azure-cli))
- Azure subscription with appropriate permissions
- PowerShell 7+ (for deployment script)
- Bicep CLI (automatically installed with Azure CLI 2.20.0+)

## 🏗️ Architecture

The infrastructure deploys the following Azure resources:

### Compute

- **App Service Plan** (Linux, B1/B2 SKU)
- **App Service** (Python 3.11, with System Managed Identity)

### AI & Cognitive Services

- **Azure OpenAI Service** (West US) - GPT-4.1 Mini deployment
- **Speech Service Primary** (West US)
- **Speech Service Backup** (East US) - For failover
- **Computer Vision** (Southeast Asia) - OCR capabilities

### Storage

- **Storage Account** with 4 blob containers:
  - `transcripts` - Audio/video transcripts
  - `response-chats` - LLM conversation history  
  - `user-password` - User authentication data
  - `meta-storage` - Metadata and session info

### Security

- **Key Vault** (existing) - Secure secret storage
- **Managed Identity** - App Service authentication
- **RBAC Role Assignments** - Least privilege access

## 🚀 Deployment

### Option 1: PowerShell Script (Recommended)

```powershell
cd infrastructure
.\deploy.ps1 -ResourceGroupName "AI-Summary-Internal" -Location "southeastasia" -Environment "prod"
```

**Optional Parameters:**

- `-WhatIf` - Preview changes without deploying
- `-ResourceGroupName` - Custom resource group name
- `-Location` - Primary Azure region
- `-Environment` - Environment tag (dev/staging/prod)

### Option 2: Azure CLI

```bash
# Login to Azure
az login

# Create resource group
az group create --name AI-Summary-Internal --location southeastasia

# Get your Object ID for Key Vault access
OBJECT_ID=$(az ad signed-in-user show --query id -o tsv)

# Update parameters file with your Object ID
# Edit main.parameters.json and replace REPLACE_WITH_YOUR_OBJECT_ID

# Deploy infrastructure
az deployment group create \
  --resource-group AI-Summary-Internal \
  --template-file main.bicep \
  --parameters main.parameters.json \
  --name ai-summary-deployment
```

### Option 3: Azure Portal

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Resource Groups** → Create new or select existing
3. Click **+ Create** → Search for "Deploy a custom template"
4. Click "Build your own template in editor"
5. Paste the contents of `main.bicep`
6. Fill in parameters and deploy

## 🔐 Security Configuration

### Key Vault Secrets

After deployment, the following secrets are automatically created in Key Vault:

| Secret Name | Description | Auto-Created |
| ------------ | ------------- | -------------- |
| `azure-speech-key-backup` | Speech Service backup key | ✅ |
| `azure-openai-key` | Azure OpenAI API key | ✅ |
| `computer-vision-key` | Computer Vision API key | ✅ |
| `azure-blob-connection` | Storage connection string | ✅ |
| `password-salt` | Password hashing salt | ❌ Manual |
| `transcripts-sas-token` | Transcripts container SAS | ✅ Auto |
| `chat-responses-sas-token` | Chat responses SAS | ✅ Auto |
| `user-password-sas-token` | User password SAS | ✅ Auto |
| `meta-data-sas-token` | Metadata SAS | ✅ Auto |

**To add manual secrets:**

```powershell
# Set a secret
az keyvault secret set \
  --vault-name ai-summary-keyvault \
  --name "password-salt" \
  --value "your-secure-salt-value"
```

### Managed Identity

The App Service uses System-Assigned Managed Identity with:

- **Storage Blob Data Contributor** - Read/write access to storage
- **Cognitive Services User** - Access to AI services
- **Key Vault Secrets User** - Read secrets from Key Vault

## 🔧 Configuration

### Environment Variables

The following environment variables are configured in App Service:

- `USE_KEY_VAULT=True` - Enable Key Vault integration
- `AZURE_KEY_VAULT_URL` - Key Vault endpoint
- `LOCAL_TESTING_MODE=False` - Production mode
- `AZURE_STORAGE_ACCOUNT_NAME` - Storage account name
- Service endpoints for Speech, OpenAI, Computer Vision

### App Service Settings

- **Always On**: Enabled
- **HTTPS Only**: Enforced
- **Minimum TLS Version**: 1.2
- **FTPS**: Disabled
- **Linux Platform**: Python 3.11

## 📊 Monitoring

### Application Insights (Optional)

To enable Application Insights monitoring:

```powershell
# Create Application Insights
az monitor app-insights component create \
  --app ai-summary-insights \
  --location southeastasia \
  --resource-group AI-Summary-Internal \
  --application-type web

# Get instrumentation key
INSIGHTS_KEY=$(az monitor app-insights component show \
  --app ai-summary-insights \
  --resource-group AI-Summary-Internal \
  --query instrumentationKey -o tsv)

# Configure App Service
az webapp config appsettings set \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --settings APPINSIGHTS_INSTRUMENTATIONKEY=$INSIGHTS_KEY
```

## 🧪 Testing Deployment

Verify deployment:

```powershell
# Check App Service status
az webapp show \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --query "state" -o tsv

# Test App Service endpoint
$appUrl = az webapp show \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --query "defaultHostName" -o tsv

curl "https://$appUrl"
```

## 🔄 CI/CD Integration

### GitHub Actions

Create `.github/workflows/azure-deploy.yml`:

```yaml
name: Deploy to Azure

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Login to Azure
        uses: azure/login@v1
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      
      - name: Deploy Infrastructure
        run: |
          az deployment group create \
            --resource-group AI-Summary-Internal \
            --template-file infrastructure/main.bicep \
            --parameters infrastructure/main.parameters.json
```

## 🧹 Cleanup

To delete all resources:

```powershell
# WARNING: This will delete ALL resources in the resource group!
az group delete --name AI-Summary-Internal --yes --no-wait
```

## 📚 Additional Resources

- [Azure Bicep Documentation](https://docs.microsoft.com/azure/azure-resource-manager/bicep/)
- [Azure OpenAI Service](https://azure.microsoft.com/products/cognitive-services/openai-service/)
- [Azure App Service](https://docs.microsoft.com/azure/app-service/)
- [Azure Key Vault](https://docs.microsoft.com/azure/key-vault/)

## 🆘 Troubleshooting

### Common Issues

1. **Key Vault access denied**
   - Ensure your Object ID is correctly set in parameters
   - Verify you have Key Vault Administrator role

2. **Storage account name already taken**
   - Change `storageAccountName` parameter to unique value
   - Must be 3-24 characters, lowercase letters and numbers only

3. **Cognitive Services quota exceeded**
   - Check your subscription quotas
   - Request quota increase in Azure Portal

4. **Deployment fails with authorization error**
   - Verify you have Contributor role on subscription/resource group
   - Check Azure AD permissions

### Get Deployment Logs

```powershell
az deployment group show \
  --name ai-summary-deployment \
  --resource-group AI-Summary-Internal \
  --query properties.error
```

## 📧 Support

For issues or questions, please open an issue in the repository.
