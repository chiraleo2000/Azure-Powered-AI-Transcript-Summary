// =============================================================================
// AI Summary Production - Azure Infrastructure (Creates All Resources)
// =============================================================================
// This Bicep template creates ALL Azure resources from scratch for a new deployment.
// Includes: Storage, Key Vault, Speech (2 regions), OpenAI, Computer Vision,
//           ACR, App Service Plan, App Service (Docker), SAS tokens, RBAC.

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

@description('Base name prefix for all resources')
param baseName string = 'ai-summary-prod'

@description('Storage account name (must be globally unique, 3-24 lowercase alphanumeric)')
param storageAccountName string = 'aisummaryprodstore'

@description('Key Vault name')
param keyVaultName string = 'ai-summary-prod-kv'

@description('App Service name')
param appServiceName string = 'ai-summary-prod-app'

@description('App Service Plan name')
param appServicePlanName string = 'ai-summary-prod-plan'

@description('Azure Container Registry name (must be globally unique, alphanumeric)')
param acrName string = 'aisummaryprodacr'

@description('Docker image name (without registry prefix)')
param dockerImageName string = 'ai-summary'

@description('Docker image tag')
param dockerImageTag string = 'latest'

@description('App Service Plan SKU')
param appServicePlanSku string = 'S1'

@description('Cognitive Services SKU')
param cognitiveServicesSku string = 'S0'

@description('Speech Service primary region')
param speechPrimaryRegion string = 'westus'

@description('Speech Service backup region')
param speechBackupRegion string = 'eastus'

@description('Azure OpenAI region (gpt-4.1-mini not available in all regions)')
param openaiRegion string = 'eastus2'

@description('Azure OpenAI model deployment name')
param openaiDeploymentName string = 'gpt-4.1-mini'

@description('Azure OpenAI model name')
param openaiModelName string = 'gpt-4.1-mini'

@description('Azure OpenAI model version')
param openaiModelVersion string = '2025-04-14'

@description('Object ID of the admin user for Key Vault access')
param keyVaultAdminObjectId string

@description('SAS token expiry (ISO 8601 format)')
param sasTokenExpiry string = '2030-12-31T23:59:59Z'

@description('Password salt for user authentication')
@secure()
param passwordSalt string = ''

@description('Deployment timestamp (auto-populated)')
param deploymentDate string = utcNow('yyyy-MM-dd')

// =============================================================================
// VARIABLES
// =============================================================================

var speechServiceName = '${baseName}-speech'
var speechServiceBackupName = '${baseName}-speech-backup'
var openaiServiceName = '${baseName}-openai'
var computerVisionName = '${baseName}-ocr'

var sasPermissions = 'racwdl'

var tags = {
  Environment: environment
  Application: 'AI-Summary-Production'
  ManagedBy: 'Bicep'
  CreatedDate: deploymentDate
}

// =============================================================================
// STORAGE ACCOUNT
// =============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  tags: tags
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource transcriptsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'transcripts'
  properties: {
    publicAccess: 'None'
  }
}

resource responseChatContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'response-chats'
  properties: {
    publicAccess: 'None'
  }
}

resource userPasswordContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'user-password'
  properties: {
    publicAccess: 'None'
  }
}

resource metaStorageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'meta-storage'
  properties: {
    publicAccess: 'None'
  }
}

// =============================================================================
// KEY VAULT
// =============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: false
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: keyVaultAdminObjectId
        permissions: {
          secrets: [
            'get'
            'list'
            'set'
            'delete'
            'purge'
          ]
        }
      }
    ]
  }
}

// =============================================================================
// COGNITIVE SERVICES - Speech (Primary + Backup)
// =============================================================================

resource speechService 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: speechServiceName
  location: speechPrimaryRegion
  sku: {
    name: cognitiveServicesSku
  }
  kind: 'SpeechServices'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: speechServiceName
  }
}

resource speechServiceBackup 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: speechServiceBackupName
  location: speechBackupRegion
  sku: {
    name: cognitiveServicesSku
  }
  kind: 'SpeechServices'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: speechServiceBackupName
  }
}

// =============================================================================
// COGNITIVE SERVICES - Azure OpenAI
// =============================================================================

resource openaiService 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openaiServiceName
  location: openaiRegion
  sku: {
    name: cognitiveServicesSku
  }
  kind: 'OpenAI'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: openaiServiceName
  }
}

resource openaiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openaiService
  name: openaiDeploymentName
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: openaiModelName
      version: openaiModelVersion
    }
  }
}

// =============================================================================
// COGNITIVE SERVICES - Computer Vision (OCR)
// =============================================================================

resource computerVision 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: computerVisionName
  location: location
  sku: {
    name: 'S1'
  }
  kind: 'ComputerVision'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: computerVisionName
  }
}

// =============================================================================
// AZURE CONTAINER REGISTRY
// =============================================================================

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  tags: tags
  properties: {
    adminUserEnabled: true
  }
}

// =============================================================================
// APP SERVICE PLAN + APP SERVICE (Docker)
// =============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: appServicePlanSku
  }
  kind: 'linux'
  tags: tags
  properties: {
    reserved: true
  }
}

resource appService 'Microsoft.Web/sites@2023-12-01' = {
  name: appServiceName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acr.properties.loginServer}/${dockerImageName}:${dockerImageTag}'
      alwaysOn: true
      webSocketsEnabled: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      healthCheckPath: '/'
      appSettings: [
        // === Core Settings ===
        {
          name: 'USE_KEY_VAULT'
          value: 'False'
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
        // === Storage ===
        {
          name: 'AZURE_STORAGE_ACCOUNT_NAME'
          value: storageAccountName
        }
        {
          name: 'AZURE_BLOB_CONNECTION'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        // === SAS Tokens ===
        {
          name: 'TRANSCRIPTS_SAS_TOKEN'
          value: storageAccount.listServiceSas('2023-05-01', {
            canonicalizedResource: '/blob/${storageAccount.name}/transcripts'
            signedResource: 'c'
            signedPermission: sasPermissions
            signedExpiry: sasTokenExpiry
            signedProtocol: 'https'
          }).serviceSasToken
        }
        {
          name: 'CHAT_RESPONSES_SAS_TOKEN'
          value: storageAccount.listServiceSas('2023-05-01', {
            canonicalizedResource: '/blob/${storageAccount.name}/response-chats'
            signedResource: 'c'
            signedPermission: sasPermissions
            signedExpiry: sasTokenExpiry
            signedProtocol: 'https'
          }).serviceSasToken
        }
        {
          name: 'USER_PASSWORD_SAS_TOKEN'
          value: storageAccount.listServiceSas('2023-05-01', {
            canonicalizedResource: '/blob/${storageAccount.name}/user-password'
            signedResource: 'c'
            signedPermission: sasPermissions
            signedExpiry: sasTokenExpiry
            signedProtocol: 'https'
          }).serviceSasToken
        }
        {
          name: 'META_DATA_SAS_TOKEN'
          value: storageAccount.listServiceSas('2023-05-01', {
            canonicalizedResource: '/blob/${storageAccount.name}/meta-storage'
            signedResource: 'c'
            signedPermission: sasPermissions
            signedExpiry: sasTokenExpiry
            signedProtocol: 'https'
          }).serviceSasToken
        }
        // === Blob Containers ===
        {
          name: 'AZURE_CONTAINER'
          value: 'transcripts'
        }
        {
          name: 'CHAT_RESPONSES_CONTAINER'
          value: 'response-chats'
        }
        {
          name: 'USER_PASSWORD_CONTAINER'
          value: 'user-password'
        }
        {
          name: 'META_DATA_CONTAINER'
          value: 'meta-storage'
        }
        // === OpenAI ===
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openaiService.properties.endpoint
        }
        {
          name: 'AZURE_OPENAI_KEY'
          value: openaiService.listKeys().key1
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: openaiDeploymentName
        }
        {
          name: 'AZURE_OPENAI_API_VERSION'
          value: '2025-01-01-preview'
        }
        {
          name: 'AZURE_OPENAI_MAX_TOKENS'
          value: '128000'
        }
        {
          name: 'AZURE_OPENAI_COMPLETION_TOKENS'
          value: '16000'
        }
        // === Computer Vision ===
        {
          name: 'COMPUTER_VISION_ENDPOINT'
          value: computerVision.properties.endpoint
        }
        {
          name: 'COMPUTER_VISION_KEY'
          value: computerVision.listKeys().key1
        }
        {
          name: 'COMPUTER_VISION_REGION'
          value: location
        }
        // === Speech Service ===
        {
          name: 'AZURE_SPEECH_KEY'
          value: speechService.listKeys().key1
        }
        {
          name: 'AZURE_SPEECH_KEY_BACKUP'
          value: speechServiceBackup.listKeys().key1
        }
        {
          name: 'AZURE_REGION'
          value: speechPrimaryRegion
        }
        {
          name: 'AZURE_REGION_BACKUP'
          value: speechBackupRegion
        }
        {
          name: 'AZURE_SPEECH_KEY_ENDPOINT'
          value: 'https://${speechPrimaryRegion}.api.cognitive.microsoft.com/'
        }
        {
          name: 'AZURE_SPEECH_KEY_ENDPOINT_BACKUP'
          value: 'https://${speechBackupRegion}.api.cognitive.microsoft.com/'
        }
        // === Docker / Runtime ===
        {
          name: 'DOCKER_REGISTRY_SERVER_URL'
          value: 'https://${acr.properties.loginServer}'
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_USERNAME'
          value: acr.listCredentials().username
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_PASSWORD'
          value: acr.listCredentials().passwords[0].value
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
        // === Security ===
        {
          name: 'PASSWORD_SALT'
          value: passwordSalt
        }
      ]
    }
  }
}

// =============================================================================
// KEY VAULT ACCESS POLICY - App Service Managed Identity
// =============================================================================

resource keyVaultAccessPolicyApp 'Microsoft.KeyVault/vaults/accessPolicies@2023-07-01' = {
  parent: keyVault
  name: 'add'
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
// KEY VAULT SECRETS (Auto-populate from deployed resources)
// =============================================================================

resource kvSecretSpeechKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-speech-key'
  properties: {
    value: speechService.listKeys().key1
  }
}

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

resource kvSecretPasswordSalt 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(passwordSalt)) {
  parent: keyVault
  name: 'password-salt'
  properties: {
    value: passwordSalt
  }
}

// SAS tokens for each container (permissions: racwdlftxy — all except immutable storage)
resource kvSecretTranscriptsSas 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'transcripts-sas-token'
  properties: {
    value: storageAccount.listServiceSas('2023-05-01', {
      canonicalizedResource: '/blob/${storageAccount.name}/transcripts'
      signedResource: 'c'
      signedPermission: sasPermissions
      signedExpiry: sasTokenExpiry
      signedProtocol: 'https'
    }).serviceSasToken
  }
}

resource kvSecretChatResponsesSas 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'chat-responses-sas-token'
  properties: {
    value: storageAccount.listServiceSas('2023-05-01', {
      canonicalizedResource: '/blob/${storageAccount.name}/response-chats'
      signedResource: 'c'
      signedPermission: sasPermissions
      signedExpiry: sasTokenExpiry
      signedProtocol: 'https'
    }).serviceSasToken
  }
}

resource kvSecretUserPasswordSas 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'user-password-sas-token'
  properties: {
    value: storageAccount.listServiceSas('2023-05-01', {
      canonicalizedResource: '/blob/${storageAccount.name}/user-password'
      signedResource: 'c'
      signedPermission: sasPermissions
      signedExpiry: sasTokenExpiry
      signedProtocol: 'https'
    }).serviceSasToken
  }
}

resource kvSecretMetaDataSas 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'meta-data-sas-token'
  properties: {
    value: storageAccount.listServiceSas('2023-05-01', {
      canonicalizedResource: '/blob/${storageAccount.name}/meta-storage'
      signedResource: 'c'
      signedPermission: sasPermissions
      signedExpiry: sasTokenExpiry
      signedProtocol: 'https'
    }).serviceSasToken
  }
}

// =============================================================================
// RBAC ROLE ASSIGNMENTS
// =============================================================================

// Storage Blob Data Contributor for App Service
resource appServiceStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(storageAccount.id, appService.id, 'StorageBlobDataContributor')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services User for OpenAI
resource appServiceCognitiveRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openaiService
  name: guid(openaiService.id, appService.id, 'CognitiveServicesUser')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// AcrPull for App Service to pull Docker images
resource appServiceAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, appService.id, 'AcrPull')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
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
output keyVaultUri string = keyVault.properties.vaultUri
output acrLoginServer string = acr.properties.loginServer
output openaiEndpoint string = openaiService.properties.endpoint
output computerVisionEndpoint string = computerVision.properties.endpoint
output speechPrimaryEndpoint string = 'https://${speechPrimaryRegion}.api.cognitive.microsoft.com/'
output speechBackupEndpoint string = 'https://${speechBackupRegion}.api.cognitive.microsoft.com/'
