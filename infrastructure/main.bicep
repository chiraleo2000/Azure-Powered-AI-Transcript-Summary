// =============================================================================
// AI Summary Internal - Azure Infrastructure Configuration
// =============================================================================
// This Bicep template configures EXISTING Azure resources
// It references existing resources and sets up proper access policies

targetScope = 'resourceGroup'

// =============================================================================
// PARAMETERS
// =============================================================================

@description('Primary location for resources')
param location string = 'southeastasia'

@description('Environment')
@allowed([
  'dev'
  'staging'
  'prod'
])
param environment string = 'prod'

@description('Existing Storage account name')
param storageAccountName string = 'aisummarymeetingstorage'

@description('Existing Key Vault name')
param keyVaultName string = 'ai-summary-keyvault'

@description('Existing App Service name')
param appServiceName string = 'ai-summarize-service'

@description('Update App Service settings')
param updateAppService bool = true

@description('Docker image (optional, e.g., yourregistry.azurecr.io/ai-summary:latest)')
param dockerImage string = ''

@description('Use Docker container deployment')
param useDocker bool = false

// =============================================================================
// VARIABLES
// =============================================================================

var speechServiceName = 'AI-Summary-Internal-speech-to-text'
var speechServiceBackupName = 'AI-Summary-Internal-speech-to-text-backup'
var openaiServiceName = 'AI-Summary-Internal-openai'
var computerVisionName = 'AI-Summary-Internal-ocr'

var tags = {
  Environment: environment
  Application: 'AI-Summary-Internal'
  ManagedBy: 'Bicep'
  UpdatedDate: '2026-02-18'
}

// =============================================================================
// EXISTING RESOURCES REFERENCES
// =============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource speechService 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: speechServiceName
}

resource speechServiceBackup 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: speechServiceBackupName
}

resource openaiService 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: openaiServiceName
}

resource computerVision 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: computerVisionName
}

resource appService 'Microsoft.Web/sites@2023-01-01' existing = {
  name: appServiceName
}

// =============================================================================
// STORAGE CONTAINERS (Ensure they exist)
// =============================================================================

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource transcriptsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: 'transcripts'
  properties: {
    publicAccess: 'None'
  }
}

resource responseChatContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: 'response-chats'
  properties: {
    publicAccess: 'None'
  }
}

resource userPasswordContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: 'user-password'
  properties: {
    publicAccess: 'None'
  }
}

resource metaStorageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: 'meta-storage'
  properties: {
    publicAccess: 'None'
  }
}

// =============================================================================
// UPDATE APP SERVICE SETTINGS
// =============================================================================

resource appServiceConfig 'Microsoft.Web/sites/config@2023-01-01' = if (updateAppService) {
  parent: appService
  name: 'web'
  properties: {
    linuxFxVersion: useDocker && !empty(dockerImage) ? 'DOCKER|${dockerImage}' : 'PYTHON|3.11'
    alwaysOn: true
    ftpsState: 'Disabled'
    minTlsVersion: '1.2'
    appCommandLine: useDocker ? '' : 'python app.py'
    appSettings: [
      {
        name: 'USE_KEY_VAULT'
        value: 'True'
      }
      {
        name: 'AZURE_KEY_VAULT_URL'
        value: keyVault.properties.vaultUri
      }
      {
        name: 'AZURE_KEY_VAULT_NAME'
        value: keyVaultName
      }
      {
        name: 'LOCAL_TESTING_MODE'
        value: 'False'
      }
      {
        name: 'AZURE_STORAGE_ACCOUNT_NAME'
        value: storageAccountName
      }
      {
        name: 'AZURE_OPENAI_ENDPOINT'
        value: openaiService.properties.endpoint
      }
      {
        name: 'COMPUTER_VISION_ENDPOINT'
        value: computerVision.properties.endpoint
      }
      {
        name: 'COMPUTER_VISION_REGION'
        value: location
      }
      {
        name: 'AZURE_REGION'
        value: 'westus'
      }
      {
        name: 'AZURE_REGION_BACKUP'
        value: 'eastus'
      }
      {
        name: 'AZURE_SPEECH_KEY_ENDPOINT'
        value: 'https://westus.api.cognitive.microsoft.com/'
      }
      {
        name: 'AZURE_SPEECH_KEY_ENDPOINT_BACKUP'
        value: 'https://eastus.api.cognitive.microsoft.com/'
      }
      {
        name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
        value: 'true'
      }
      {
        name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE'
        value: 'false'
      }
      {
        name: 'WEBSITES_PORT'
        value: '7860'
      }
      {
        name: 'PYTHONUNBUFFERED'
        value: '1'
      }
    ]
  }
}

// =============================================================================
// KEY VAULT ACCESS POLICIES
// =============================================================================

resource keyVaultAccessPolicy 'Microsoft.KeyVault/vaults/accessPolicies@2023-07-01' = {
  name: 'add'
  parent: keyVault
  properties: {
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: appService.identity.principalId
        permissions: {
          secrets: [
            'get'
            'list'
          ]
        }
      }
    ]
  }
}

// =============================================================================
// KEY VAULT SECRETS (Auto-populate)
// =============================================================================

resource kvSecretSpeechKeyBackup 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-speech-key-backup'
  properties: {
    value: speechServiceBackup.listKeys().key1
  }
}

resource kvSecretOpenAIKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-openai-key'
  properties: {
    value: openaiService.listKeys().key1
  }
}

resource kvSecretComputerVisionKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'computer-vision-key'
  properties: {
    value: computerVision.listKeys().key1
  }
}

resource kvSecretStorageConnection 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-blob-connection'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
  }
}

// =============================================================================
// RBAC ROLE ASSIGNMENTS
// =============================================================================

resource appServiceStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, appService.id, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appServiceCognitiveRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openaiService.id, appService.id, 'CognitiveServicesUser')
  scope: openaiService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// =============================================================================
// OUTPUTS
// =============================================================================

output appServiceName string = appService.name
output appServiceUrl string = 'https://${appService.properties.defaultHostName}'
output appServicePrincipalId string = appService.identity.principalId
output storageAccountName string = storageAccount.name
output keyVaultName string = keyVault.name
output openaiEndpoint string = openaiService.properties.endpoint
output computerVisionEndpoint string = computerVision.properties.endpoint
output dockerEnabled string = useDocker ? 'Yes' : 'No'
