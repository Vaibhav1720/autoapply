// AutoApply Infrastructure — Main Bicep Template
// Deploys all Azure resources for the AutoApply platform

@description('Environment name (dev, staging, prod)')
param env string = 'dev'

@description('Default Azure region for compute/storage/messaging resources (matches existing deployed location).')
param location string = 'centralus'

@description('Region for Cosmos DB account. Pinned to existing deployed location.')
param cosmosLocation string = 'eastus'

@description('Region for the Azure AI Foundry resource. Pinned to existing deployed location.')
param aiLocation string = 'eastus2'

@description('Cosmos DB account name')
param cosmosAccountName string = 'myapp-cosmos-${env}'

@description('Storage account name')
param storageAccountName string = 'myappstor${env}'

@description('Function App name')
param functionAppName string = 'myapp-func-${env}'

@description('AI Foundry resource name')
param aiServiceName string = 'myapp-ai-${env}'

@description('Foundry deployment name used for the discover LLM rerank step.')
param aiRerankModel string = 'gpt4omini'

@description('Foundry deployment name used for the resume review critique.')
param aiReviewModel string = 'gpt4omini'

@description('Foundry deployment name used for resume parsing.')
param aiParseModel string = 'gpt41'

@description('Vector-score gap above which the LLM rerank is skipped.')
param rerankSkipGap int = 15

@description('Daily discover-call limit for free-tier users (0 disables).')
param freeTierDailyDiscoverLimit int = 50

@description('Admin token for /api/v1/admin/* endpoints. Override per environment; never commit a real value.')
@secure()
param adminApiToken string = ''

// ── Cosmos DB (Free Tier) ───────────────────────────────────────────────────

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: cosmosLocation
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: true
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0 }]
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'autoapply'
  properties: {
    resource: { id: 'autoapply' }
    // No database-level throughput — each container has its own autoscale (max 1000 RU/s).
    // The free-tier 1000 RU/s discount applies to the first container with throughput.
  }
}

// Containers WITHOUT vector search (shared database throughput, no per-container RU/s)
var basicContainers = [
  { name: 'companies', partitionKey: '/industry' }
  { name: 'applications', partitionKey: '/userId' }
  { name: 'users', partitionKey: '/id' }
  { name: 'job_results', partitionKey: '/userId' }
]

resource cosmosBasicContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [
  for c in basicContainers: {
    parent: cosmosDb
    name: c.name
    properties: {
      resource: {
        id: c.name
        partitionKey: { paths: [c.partitionKey], kind: 'Hash' }
      }
      options: { autoscaleSettings: { maxThroughput: 1000 } }
    }
  }
]

// Phase 7 — match_events container.
// One doc per discover invocation. Captures the scoring "audit trail" so we
// can answer "why did user X get those jobs?" without re-running the matcher.
// TTL 30 days = enough for debugging a week's worth of complaints, no PII bloat.
resource matchEventsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'match_events'
  properties: {
    resource: {
      id: 'match_events'
      partitionKey: { paths: ['/userId'], kind: 'Hash' }
      defaultTtl: 2592000   // 30 days in seconds
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/userId/?' }
          { path: '/companyId/?' }
          { path: '/timestamp/?' }
          { path: '/searchId/?' }
        ]
        excludedPaths: [
          { path: '/*' }
          { path: '/"_etag"/?' }
        ]
        compositeIndexes: [
          [
            { path: '/userId', order: 'ascending' }
            { path: '/timestamp', order: 'descending' }
          ]
        ]
      }
    }
    options: { autoscaleSettings: { maxThroughput: 1000 } }
  }
}

// Profiles container — with vector embedding policy + vector index
#disable-next-line BCP037
resource profilesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'profiles'
  properties: {
    resource: {
      id: 'profiles'
      partitionKey: { paths: ['/userId'], kind: 'Hash' }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/*' }]
        excludedPaths: [
          { path: '/profileEmbedding/*' }   // Exclude from standard index (saves RU on writes)
          { path: '/"_etag"/?' }
        ]
        vectorIndexes: [
          { path: '/profileEmbedding', type: 'quantizedFlat' }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/profileEmbedding'
            dataType: 'float32'
            // NOTE: existing container was created with 1536 dims (text-embedding-3-small).
            // Cosmos refuses to modify vector indexing policy after creation — keep at 1536
            // until the container is rebuilt. The app currently uses Python cosine similarity
            // (not Cosmos VectorDistance), so this index is informational only.
            dimensions: 1536
            distanceFunction: 'cosine'
          }
        ]
      }
    }
    options: { autoscaleSettings: { maxThroughput: 1000 } }
  }
}

// Jobs container — with vector embedding policy + vector index
#disable-next-line BCP037
resource jobsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'jobs'
  properties: {
    resource: {
      id: 'jobs'
      partitionKey: { paths: ['/companyId'], kind: 'Hash' }
      defaultTtl: -1   // Enable per-document TTL (set `ttl` on each doc; -1 = disabled by default, opt-in per doc)
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/*' }]
        excludedPaths: [
          { path: '/jobEmbedding/*' }
          { path: '/"_etag"/?' }
        ]
        vectorIndexes: [
          { path: '/jobEmbedding', type: 'quantizedFlat' }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/jobEmbedding'
            dataType: 'float32'
            // See profiles container note — existing container is 1536 dims, can't modify in-place.
            dimensions: 1536
            distanceFunction: 'cosine'
          }
        ]
      }
    }
    options: { autoscaleSettings: { maxThroughput: 1000 } }
  }
}

// Job vectors container — per-user, per-job embedding store for tailor flow.
// One doc per (user, job). Cosmos VectorDistance() over /embedding (3072-dim,
// matches text-embedding-3-large) lets us pull the candidate's best-fit jobs
// in a single query instead of re-embedding everything in Python.
#disable-next-line BCP037
resource jobVectorsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'job_vectors'
  properties: {
    resource: {
      id: 'job_vectors'
      partitionKey: { paths: ['/userId'], kind: 'Hash' }
      defaultTtl: -1
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/*' }]
        excludedPaths: [
          { path: '/embedding/*' }
          { path: '/"_etag"/?' }
        ]
        vectorIndexes: [
          { path: '/embedding', type: 'quantizedFlat' }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            dimensions: 3072
            distanceFunction: 'cosine'
          }
        ]
      }
    }
    options: { autoscaleSettings: { maxThroughput: 1000 } }
  }
}

// ── Storage Account (Blob + Queues) ─────────────────────────────────────────

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { accessTier: 'Hot' }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

var blobContainers = ['resumes', 'cover-letters', 'company-logos']

resource blobContainerResources 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for name in blobContainers: {
    parent: blobService
    name: name
    properties: { publicAccess: 'None' }
  }
]

// ── AI Foundry (AIServices) ────────────────────────────────────────────────

resource aiService 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: aiServiceName
  location: aiLocation
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: aiServiceName
    publicNetworkAccess: 'Enabled'
  }
}

// AI Foundry model deployments (declarative — must serialize, not parallel)
var foundryModels = [
  { name: 'gpt41',                  model: 'gpt-4.1',                version: '2025-04-14', sku: 'GlobalStandard', capacity: 50 }
  { name: 'gpt4o',                  model: 'gpt-4o',                 version: '2024-11-20', sku: 'GlobalStandard', capacity: 50 }
  { name: 'gpt4omini',              model: 'gpt-4o-mini',            version: '2024-07-18', sku: 'GlobalStandard', capacity: 50 }
  { name: 'o4mini',                 model: 'o4-mini',                version: '2025-04-16', sku: 'GlobalStandard', capacity: 50 }
  { name: 'text-embedding-3-large', model: 'text-embedding-3-large', version: '1',          sku: 'Standard',       capacity: 50 }
  { name: 'text-embedding-3-small', model: 'text-embedding-3-small', version: '1',          sku: 'Standard',       capacity: 50 }
]

@batchSize(1)
resource aiDeployments 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = [for m in foundryModels: {
  parent: aiService
  name: m.name
  sku: { name: m.sku, capacity: m.capacity }
  properties: {
    model: { format: 'OpenAI', name: m.model, version: m.version }
  }
}]

// ── Application Insights ───────────────────────────────────────────────────

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'autoapply-logs-${env}'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'autoapply-insights-${env}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ── Function App (Consumption) ─────────────────────────────────────────────

resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'autoapply-plan-${env}'
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: hostingPlan.id
    siteConfig: {
      pythonVersion: '3.11'
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value}' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        { name: 'COSMOS_ENDPOINT', value: cosmosAccount.properties.documentEndpoint }
        { name: 'COSMOS_KEY', value: cosmosAccount.listKeys().primaryMasterKey }
        { name: 'COSMOS_DATABASE', value: 'autoapply' }
        { name: 'BLOB_CONNECTION_STRING', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value}' }
        { name: 'AZURE_AI_ENDPOINT', value: aiService.properties.endpoint }
        { name: 'AZURE_AI_KEY', value: aiService.listKeys().key1 }
        { name: 'AI_RERANK_MODEL', value: aiRerankModel }
        { name: 'AI_REVIEW_MODEL', value: aiReviewModel }
        { name: 'AI_PARSE_MODEL', value: aiParseModel }
        { name: 'RERANK_SKIP_GAP', value: string(rerankSkipGap) }
        { name: 'FREE_TIER_DAILY_DISCOVER_LIMIT', value: string(freeTierDailyDiscoverLimit) }
        { name: 'ADMIN_API_TOKEN', value: adminApiToken }
      ]
      cors: { allowedOrigins: ['*'] }
    }
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────

output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output storageAccountName string = storageAccount.name
output appInsightsKey string = appInsights.properties.InstrumentationKey
output aiServiceEndpoint string = aiService.properties.endpoint
output aiServiceName string = aiService.name
