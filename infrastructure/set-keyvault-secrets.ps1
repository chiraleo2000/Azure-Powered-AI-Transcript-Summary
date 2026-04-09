# =========================================================================
# Set Azure Key Vault Secrets Script
# =========================================================================
# This script helps you securely set all required secrets in Azure Key Vault

param(
    [Parameter(Mandatory=$false)]
    [string]$KeyVaultName = "ai-summary-prod-kv",
    
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "ai-summary-prod-rg",
    
    [Parameter(Mandatory=$false)]
    [switch]$Interactive,
    
    [Parameter(Mandatory=$false)]
    [switch]$FromEnvFile,
    
    [Parameter(Mandatory=$false)]
    [string]$EnvFilePath = ".env"
)

$ErrorActionPreference = "Stop"

# Colors for output
function Write-ColorOutput($ForegroundColor, $Message) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    Write-Output $Message
    $host.UI.RawUI.ForegroundColor = $fc
}

Write-ColorOutput Green "🔐 Azure Key Vault Secrets Configuration"
Write-ColorOutput Green "========================================="

# Check Azure CLI
try {
    $null = az version 2>$null
    Write-ColorOutput Cyan "✅ Azure CLI detected"
} catch {
    Write-ColorOutput Red "❌ Azure CLI not found. Please install it first."
    exit 1
}

# Check login
$accountInfo = az account show 2>$null
if (!$accountInfo) {
    Write-ColorOutput Yellow "⚠️  Not logged in. Logging in..."
    az login
}

$account = $accountInfo | ConvertFrom-Json
Write-ColorOutput Green "✅ Logged in as: $($account.user.name)"

# Verify Key Vault exists
Write-ColorOutput Cyan "`n🔍 Checking Key Vault: $KeyVaultName"
$kvExists = az keyvault show --name $KeyVaultName --resource-group $ResourceGroupName 2>$null
if (!$kvExists) {
    Write-ColorOutput Red "❌ Key Vault '$KeyVaultName' not found in resource group '$ResourceGroupName'"
    Write-ColorOutput Yellow "Please deploy infrastructure first using: infrastructure\deploy.ps1"
    exit 1
}
Write-ColorOutput Green "✅ Key Vault found: $KeyVaultName"

# Define secrets to set
$secrets = @(
    @{
        Name = "azure-speech-key-backup"
        EnvVar = "AZURE_SPEECH_KEY_BACKUP"
        Description = "Azure Speech Service Backup Key (East US)"
        Auto = $false
    },
    @{
        Name = "azure-openai-key"
        EnvVar = "AZURE_OPENAI_KEY"
        Description = "Azure OpenAI API Key"
        Auto = $true
        ServiceName = "ai-summary-prod-openai"
    },
    @{
        Name = "computer-vision-key"
        EnvVar = "COMPUTER_VISION_KEY"
        Description = "Computer Vision API Key"
        Auto = $true
        ServiceName = "ai-summary-prod-ocr"
    },
    @{
        Name = "azure-blob-connection"
        EnvVar = "AZURE_BLOB_CONNECTION"
        Description = "Azure Blob Storage Connection String"
        Auto = $true
        StorageAccount = $true
    },
    @{
        Name = "password-salt"
        EnvVar = "PASSWORD_SALT"
        Description = "Password Hashing Salt"
        Auto = $false
        Generate = $true
    },
    @{
        Name = "transcripts-sas-token"
        EnvVar = "TRANSCRIPTS_SAS_TOKEN"
        Description = "Transcripts Container SAS Token"
        Auto = $false
    },
    @{
        Name = "chat-responses-sas-token"
        EnvVar = "CHAT_RESPONSES_SAS_TOKEN"
        Description = "Chat Responses Container SAS Token"
        Auto = $false
    },
    @{
        Name = "user-password-sas-token"
        EnvVar = "USER_PASSWORD_SAS_TOKEN"
        Description = "User Password Container SAS Token"
        Auto = $false
    },
    @{
        Name = "meta-data-sas-token"
        EnvVar = "META_DATA_SAS_TOKEN"
        Description = "Metadata Container SAS Token"
        Auto = $false
    }
)

# Function to generate secure random string
function New-SecureRandomString {
    param([int]$Length = 64)
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes)
}

# Function to set secret
function Set-KeyVaultSecretSafe {
    param(
        [string]$Name,
        [string]$Value,
        [string]$Description
    )
    
    Write-ColorOutput Cyan "  Setting secret: $Name"
    
    try {
        az keyvault secret set `
            --vault-name $KeyVaultName `
            --name $Name `
            --value $Value `
            --description $Description `
            --output none
        
        Write-ColorOutput Green "  ✅ Secret set: $Name"
        return $true
    } catch {
        Write-ColorOutput Red "  ❌ Failed to set secret: $Name"
        Write-ColorOutput Red "  Error: $_"
        return $false
    }
}

# Load from .env file if requested
$envValues = @{}
if ($FromEnvFile -and (Test-Path $EnvFilePath)) {
    Write-ColorOutput Cyan "`n📄 Loading values from $EnvFilePath"
    Get-Content $EnvFilePath | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            if ($value -and $value -ne "<managed-by-key-vault>") {
                $envValues[$key] = $value
            }
        }
    }
    Write-ColorOutput Green "✅ Loaded $($envValues.Count) values from .env"
}

# Process each secret
Write-ColorOutput Cyan "`n🔑 Configuring secrets..."
$successCount = 0
$skippedCount = 0

foreach ($secret in $secrets) {
    Write-ColorOutput Yellow "`n📝 $($secret.Description)"
    Write-ColorOutput Gray "   Key Vault name: $($secret.Name)"
    
    $value = $null
    
    # Try to auto-retrieve from Azure
    if ($secret.Auto) {
        Write-ColorOutput Cyan "   Attempting to auto-retrieve from Azure..."
        
        if ($secret.StorageAccount) {
            # Get storage connection string
            try {
                $storageKey = az storage account keys list `
                    --account-name "aisummaryprodstore" `
                    --resource-group $ResourceGroupName `
                    --query "[0].value" -o tsv
                
                if ($storageKey) {
                    $value = "DefaultEndpointsProtocol=https;AccountName=aisummaryprodstore;AccountKey=$storageKey;EndpointSuffix=core.windows.net"
                    Write-ColorOutput Green "   ✅ Retrieved from Storage Account"
                }
            } catch {
                Write-ColorOutput Yellow "   ⚠️  Could not auto-retrieve storage key"
            }
        } elseif ($secret.ServiceName) {
            # Get cognitive service key
            try {
                $cogKey = az cognitiveservices account keys list `
                    --name $secret.ServiceName `
                    --resource-group $ResourceGroupName `
                    --query "key1" -o tsv
                
                if ($cogKey) {
                    $value = $cogKey
                    Write-ColorOutput Green "   ✅ Retrieved from $($secret.ServiceName)"
                }
            } catch {
                Write-ColorOutput Yellow "   ⚠️  Could not auto-retrieve from $($secret.ServiceName)"
            }
        }
    }
    
    # Try to load from .env file
    if (!$value -and $envValues.ContainsKey($secret.EnvVar)) {
        $value = $envValues[$secret.EnvVar]
        Write-ColorOutput Green "   ✅ Loaded from .env file"
    }
    
    # Generate if requested
    if (!$value -and $secret.Generate) {
        $value = New-SecureRandomString -Length 64
        Write-ColorOutput Green "   ✅ Generated secure random value"
    }
    
    # Interactive prompt if no value yet
    if (!$value -and $Interactive) {
        Write-ColorOutput Yellow "   Enter value (or press Enter to skip):"
        Write-Host "   > " -NoNewline -ForegroundColor Cyan
        $inputValue = Read-Host
        if ($inputValue) {
            $value = $inputValue
        }
    }
    
    # Set secret if we have a value
    if ($value) {
        $success = Set-KeyVaultSecretSafe -Name $secret.Name -Value $value -Description $secret.Description
        if ($success) {
            $successCount++
        }
    } else {
        Write-ColorOutput Gray "   ⏭️  Skipped"
        $skippedCount++
    }
}

# Summary
Write-ColorOutput Green "`n✨ Configuration Complete!"
Write-ColorOutput Cyan "Summary:"
Write-ColorOutput Green "  ✅ Secrets set: $successCount"
if ($skippedCount -gt 0) {
    Write-ColorOutput Yellow "  ⏭️  Secrets skipped: $skippedCount"
}

if ($skippedCount -gt 0) {
    Write-ColorOutput Yellow "`n⚠️  Some secrets were skipped. Set them manually:"
    Write-ColorOutput Cyan "az keyvault secret set --vault-name $KeyVaultName --name <secret-name> --value <secret-value>"
}

Write-ColorOutput Green "`n🎉 Key Vault configuration complete!"
Write-ColorOutput Cyan "Verify secrets:"
Write-ColorOutput Gray "az keyvault secret list --vault-name $KeyVaultName --query '[].name' -o table"
