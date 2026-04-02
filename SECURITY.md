# Security Configuration Guide

## 🔐 Overview

This application uses **Azure Key Vault** for secure secret management. Secrets are never stored in code or configuration files in production environments.

## 🏗️ Security Architecture

### Authentication Flow
```
Application (Managed Identity)
    ↓
Azure Key Vault
    ↓
Retrieve Secrets
    ↓
Application Runtime
```

### Key Components

1. **Azure Key Vault** - Central secret storage
2. **Managed Identity** - Passwordless authentication
3. **RBAC** - Role-based access control
4. **Config Module** - Secure configuration loader
5. **Environment Variables** - Non-sensitive configuration

## 🔑 Secret Management

### Secrets Stored in Key Vault

| Secret Name (Key Vault) | Environment Variable | Purpose |
|------------------------|---------------------|---------|
| `azure-speech-key-backup` | `AZURE_SPEECH_KEY_BACKUP` | Speech Service backup key |
| `azure-openai-key` | `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `gpt4o-transcribe-api-key` | `GPT4O_TRANSCRIBE_API_KEY` | GPT-4o Transcribe key |
| `computer-vision-key` | `COMPUTER_VISION_KEY` | Computer Vision API key |
| `azure-blob-connection` | `AZURE_BLOB_CONNECTION` | Storage connection string |
| `transcripts-sas-token` | `TRANSCRIPTS_SAS_TOKEN` | Transcripts container SAS |
| `chat-responses-sas-token` | `CHAT_RESPONSES_SAS_TOKEN` | Chat responses SAS |
| `user-password-sas-token` | `USER_PASSWORD_SAS_TOKEN` | User password SAS |
| `meta-data-sas-token` | `META_DATA_SAS_TOKEN` | Metadata SAS |
| `password-salt` | `PASSWORD_SALT` | Password hashing salt |

### Secret Naming Convention

Key Vault secret names use **kebab-case** (lowercase with hyphens):
- Environment: `AZURE_OPENAI_KEY`
- Key Vault: `azure-openai-key`

The `config.py` module handles automatic conversion.

## 📝 Setting Up Secrets

### Option 1: PowerShell Script (Recommended)

Use the provided script to set all secrets:

```powershell
cd infrastructure
.\set-keyvault-secrets.ps1
```

### Option 2: Azure CLI

Set secrets manually:

```bash
# Set a secret
az keyvault secret set \
  --vault-name ai-summary-keyvault \
  --name "azure-openai-key" \
  --value "your-secret-value"

# View a secret (requires permissions)
az keyvault secret show \
  --vault-name ai-summary-keyvault \
  --name "azure-openai-key" \
  --query value -o tsv
```

### Option 3: Azure Portal

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Key Vault** → `ai-summary-keyvault`
3. Select **Secrets** from left menu
4. Click **+ Generate/Import**
5. Fill in:
   - **Name**: `azure-openai-key` (kebab-case)
   - **Value**: Your secret value
6. Click **Create**

## 🔒 Access Control

### Managed Identity Configuration

The App Service uses **System-Assigned Managed Identity** with:

```
App Service → Managed Identity → Key Vault Access Policy
```

### Required Permissions

The App Service needs:
- **Key Vault Secrets User** role
- Permissions: `GET`, `LIST` secrets

### Grant Access via Azure CLI

```bash
# Get App Service principal ID
APP_PRINCIPAL_ID=$(az webapp identity show \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal \
  --query principalId -o tsv)

# Grant Key Vault access
az keyvault set-policy \
  --name ai-summary-keyvault \
  --object-id $APP_PRINCIPAL_ID \
  --secret-permissions get list
```

## 🧪 Local Development

### Environment Variables

For local development, use `.env` file:

```bash
# Copy example file
cp .env.example .env

# Edit .env with your secrets
nano .env
```

### Disable Key Vault Locally

```env
USE_KEY_VAULT=False
LOCAL_TESTING_MODE=True
```

Then secrets are loaded from `.env` file.

### Test Key Vault Connection

```python
from azure_keyvault_client import get_keyvault_client

client = get_keyvault_client()
secret = client.get_secret("azure-openai-key")
print(f"Retrieved secret: {secret[:10]}...")
```

## 🔄 Secret Rotation

### Best Practices

1. **Rotate secrets every 90 days**
2. **Use versioned secrets** in Key Vault
3. **Update secrets without downtime:**
   - Add new secret version in Key Vault
   - App automatically uses latest version
   - No restart required

### Rotation Process

```bash
# Step 1: Generate new key in Azure service
az cognitiveservices account keys regenerate \
  --name AI-Summary-Internal-openai \
  --resource-group AI-Summary-Internal \
  --key-name key2

# Step 2: Get new key
NEW_KEY=$(az cognitiveservices account keys list \
  --name AI-Summary-Internal-openai \
  --resource-group AI-Summary-Internal \
  --query key2 -o tsv)

# Step 3: Update Key Vault secret
az keyvault secret set \
  --vault-name ai-summary-keyvault \
  --name "azure-openai-key" \
  --value "$NEW_KEY"

# Note: App will automatically use new secret within minutes
```

## 🛡️ Security Hardening

### Network Security

1. **Enable Key Vault Firewall** (optional):
   ```bash
   az keyvault update \
     --name ai-summary-keyvault \
     --default-action Deny
   
   # Add App Service subnet
   az keyvault network-rule add \
     --name ai-summary-keyvault \
     --subnet <app-service-subnet-id>
   ```

2. **Enable Private Endpoint** (recommended for production):
   - Creates private connection between App Service and Key Vault
   - Traffic never leaves Azure backbone

### Audit Logging

Enable diagnostic logging:

```bash
# Create Log Analytics workspace
az monitor log-analytics workspace create \
  --resource-group AI-Summary-Internal \
  --workspace-name ai-summary-logs

# Enable Key Vault diagnostics
az monitor diagnostic-settings create \
  --name kv-diagnostics \
  --resource /subscriptions/<sub-id>/resourceGroups/AI-Summary-Internal/providers/Microsoft.KeyVault/vaults/ai-summary-keyvault \
  --workspace ai-summary-logs \
  --logs '[{"category": "AuditEvent", "enabled": true}]'
```

### Monitoring

Monitor Key Vault access:

```bash
# View recent secret access
az monitor activity-log list \
  --resource-group AI-Summary-Internal \
  --offset 7d \
  --query "[?contains(resourceId, 'ai-summary-keyvault')]"
```

## 🚨 Security Incidents

### If Secrets Are Compromised

1. **Immediate Actions:**
   ```bash
   # Revoke compromised secret
   az keyvault secret set-attributes \
     --vault-name ai-summary-keyvault \
     --name "compromised-secret" \
     --enabled false
   
   # Rotate immediately
   # (follow rotation process above)
   ```

2. **Check audit logs:**
   ```bash
   # Check who accessed the secret
   az monitor activity-log list \
     --resource-group AI-Summary-Internal \
     --start-time 2025-01-01 \
     --query "[?contains(operationName.value, 'SECRET')]"
   ```

3. **Notify security team**
4. **Update incident response plan**

## 📋 Compliance

### GDPR & Data Protection

- Secrets contain no personal data
- Password salt stored securely in Key Vault
- User passwords hashed with SHA-256 + salt
- No plain-text password storage

### Audit Requirements

Key Vault provides:
- **90-day audit logs** (default)
- **Secret version history**
- **Access tracking**
- **Compliance reports**

## 🔍 Troubleshooting

### Common Issues

**1. "Access denied" error:**
```
Error: Client does not have permission to get secrets
```

**Solution:**
```bash
# Check access policy
az keyvault show --name ai-summary-keyvault \
  --query "properties.accessPolicies"

# Grant access
az keyvault set-policy \
  --name ai-summary-keyvault \
  --object-id <principal-id> \
  --secret-permissions get list
```

**2. "Key Vault not found":**

**Solution:** Verify Key Vault name and URL in environment variables.

**3. Managed Identity not working:**

**Solution:**
```bash
# Verify identity is enabled
az webapp identity show \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal

# If not enabled:
az webapp identity assign \
  --name AI-Summary-Internal-app-service \
  --resource-group AI-Summary-Internal
```

## 📚 Additional Resources

- [Azure Key Vault Documentation](https://docs.microsoft.com/azure/key-vault/)
- [Managed Identities](https://docs.microsoft.com/azure/active-directory/managed-identities-azure-resources/)
- [Azure Security Best Practices](https://docs.microsoft.com/azure/security/fundamentals/best-practices-and-patterns)

## 🆘 Emergency Contacts

- **Security Team**: security@yourcompany.com
- **On-Call Engineer**: oncall@yourcompany.com
- **Azure Support**: [Create support ticket](https://portal.azure.com/#blade/Microsoft_Azure_Support/HelpAndSupportBlade)
