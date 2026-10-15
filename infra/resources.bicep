@minLength(1)
param environmentName string
param location string
param durableTaskSchedulerLocation string
param managementGroupId string
param principalId string

var suffix = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var prefix = 'capacity-${environmentName}'
var streamName = 'Custom-ComputeCapacity_CL'
var runStreamName = 'Custom-ComputeCapacityRuns_CL'
var functionName = 'func-${prefix}-${suffix}'
var functionStorageName = 'st${suffix}'
var deploymentContainerName = 'app-package-${take(suffix, 12)}'
var workContainerName = 'capacity-work'
var schedulerName = 'dts-${prefix}-${suffix}'
var taskHubName = 'capacity'
var virtualNetworkName = 'vnet-${prefix}'
var functionSubnetName = 'snet-function'
var functionNatPublicIpName = 'pip-${prefix}-function'
var functionNatGatewayName = 'nat-${prefix}-function'
var metricsPublisherRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)
var storageBlobDataOwnerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
)
var storageQueueDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
)
var storageTableDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
)
var tableColumns = [
  {
    name: 'TimeGenerated'
    type: 'datetime'
  }
  {
    name: 'CollectionId_s'
    type: 'string'
  }
  {
    name: 'PartitionId_s'
    type: 'string'
  }
  {
    name: 'RecordId_s'
    type: 'string'
  }
  {
    name: 'SubscriptionId_s'
    type: 'string'
  }
  {
    name: 'SubscriptionName_s'
    type: 'string'
  }
  {
    name: 'Region_s'
    type: 'string'
  }
  {
    name: 'ComputeSku_s'
    type: 'string'
  }
  {
    name: 'VmFamily_s'
    type: 'string'
  }
  {
    name: 'VmFamilyBase_s'
    type: 'string'
  }
  {
    name: 'SkuGeneration_d'
    type: 'real'
  }
  {
    name: 'LatestGeneration_d'
    type: 'real'
  }
  {
    name: 'GenerationLag_d'
    type: 'real'
  }
  {
    name: 'SkuRestrictions_s'
    type: 'string'
  }
  {
    name: 'AvailabilityZones_s'
    type: 'string'
  }
  {
    name: 'ResourceKind_s'
    type: 'string'
  }
  {
    name: 'ResourceCount_d'
    type: 'real'
  }
  {
    name: 'DeployedInstances_d'
    type: 'real'
  }
  {
    name: 'VCpusPerInstance_d'
    type: 'real'
  }
  {
    name: 'DeployedVCpus_d'
    type: 'real'
  }
  {
    name: 'ReservedInstances_d'
    type: 'real'
  }
  {
    name: 'ReservedVCpus_d'
    type: 'real'
  }
  {
    name: 'QuotaLimitVCpus_d'
    type: 'real'
  }
  {
    name: 'QuotaUsageVCpus_d'
    type: 'real'
  }
  {
    name: 'QuotaAvailableVCpus_d'
    type: 'real'
  }
]
var runTableColumns = [
  {
    name: 'TimeGenerated'
    type: 'datetime'
  }
  {
    name: 'CollectionId_s'
    type: 'string'
  }
  {
    name: 'Status_s'
    type: 'string'
  }
  {
    name: 'SubscriptionCount_d'
    type: 'real'
  }
  {
    name: 'PartitionCount_d'
    type: 'real'
  }
  {
    name: 'RecordCount_d'
    type: 'real'
  }
  {
    name: 'StartedAt_t'
    type: 'datetime'
  }
  {
    name: 'CompletedAt_t'
    type: 'datetime'
  }
]

resource functionNatPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: functionNatPublicIpName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 10
  }
}

resource functionNatGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: functionNatGatewayName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [
      {
        id: functionNatPublicIp.id
      }
    ]
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: virtualNetworkName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/16'
      ]
    }
  }
}

resource functionIntegrationSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: virtualNetwork
  name: functionSubnetName
  properties: {
    addressPrefix: '10.42.0.0/24'
    defaultOutboundAccess: false
    natGateway: {
      id: functionNatGateway.id
    }
    delegations: [
      {
        name: 'flex-consumption'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource capacityRunsTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'ComputeCapacityRuns_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: {
      name: 'ComputeCapacityRuns_CL'
      columns: runTableColumns
    }
  }
}

resource capacityTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'ComputeCapacity_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: {
      name: 'ComputeCapacity_CL'
      columns: tableColumns
    }
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    DisableLocalAuth: true
    WorkspaceResourceId: workspace.id
  }
}

resource dataCollectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: '${prefix}-dce'
  location: location
  properties: {
    description: 'Compute capacity Logs Ingestion API endpoint'
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource dataCollectionRule 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: '${prefix}-dcr'
  location: location
  kind: 'Direct'
  properties: {
    dataCollectionEndpointId: dataCollectionEndpoint.id
    streamDeclarations: {
      '${streamName}': {
        columns: tableColumns
      }
      '${runStreamName}': {
        columns: runTableColumns
      }
    }
    destinations: {
      logAnalytics: [
        {
          name: 'capacityWorkspace'
          workspaceResourceId: workspace.id
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          streamName
        ]
        destinations: [
          'capacityWorkspace'
        ]
        transformKql: 'source'
        outputStream: streamName
      }
      {
        streams: [
          runStreamName
        ]
        destinations: [
          'capacityWorkspace'
        ]
        transformKql: 'source'
        outputStream: runStreamName
      }
    ]
  }
  dependsOn: [
    capacityTable
    capacityRunsTable
  ]
}

resource collectorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-identity'
  location: location
}

module durableTaskScheduler './app/dts.bicep' = {
  name: 'durableTaskScheduler'
  params: {
    name: schedulerName
    taskHubName: taskHubName
    location: durableTaskSchedulerLocation
    tags: {
      'azd-env-name': environmentName
      workload: 'azure-compute-capacity'
    }
  }
}

module durableTaskAccess './app/dts-access.bicep' = {
  name: 'durableTaskAccess'
  params: {
    schedulerName: durableTaskScheduler.outputs.name
    principalId: collectorIdentity.properties.principalId
  }
}

module durableTaskDashboardAccess './app/dts-access.bicep' = if (!empty(principalId)) {
  name: 'durableTaskDashboardAccess'
  params: {
    schedulerName: durableTaskScheduler.outputs.name
    principalId: principalId
    principalType: 'User'
  }
}

module functionStorage 'br/public:avm/res/storage/storage-account:0.8.3' = {
  name: 'functionStorage'
  params: {
    name: functionStorageName
    location: location
    skuName: 'Standard_LRS'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    dnsEndpointType: 'Standard'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    blobServices: {
      containers: [
        {
          name: deploymentContainerName
        }
        {
          name: workContainerName
        }
      ]
    }
    minimumTlsVersion: 'TLS1_2'
    tags: {
      'azd-env-name': environmentName
      SecurityControl: 'Ignore'
      workload: 'azure-compute-capacity'
    }
  }
}

resource functionStorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: functionStorageName
}

resource collectorStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorageAccount.id, collectorIdentity.id, storageBlobDataOwnerRoleId)
  scope: functionStorageAccount
  properties: {
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataOwnerRoleId
  }
  dependsOn: [
    functionStorage
  ]
}

resource collectorStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorageAccount.id, collectorIdentity.id, storageQueueDataContributorRoleId)
  scope: functionStorageAccount
  properties: {
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageQueueDataContributorRoleId
  }
  dependsOn: [
    functionStorage
  ]
}

resource collectorStorageTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorageAccount.id, collectorIdentity.id, storageTableDataContributorRoleId)
  scope: functionStorageAccount
  properties: {
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageTableDataContributorRoleId
  }
  dependsOn: [
    functionStorage
  ]
}

resource deployerStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(functionStorageAccount.id, principalId, storageBlobDataOwnerRoleId)
  scope: functionStorageAccount
  properties: {
    principalId: principalId
    roleDefinitionId: storageBlobDataOwnerRoleId
  }
  dependsOn: [
    functionStorage
  ]
}

resource dcrMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataCollectionRule.id, collectorIdentity.id, 'metrics-publisher')
  scope: dataCollectionRule
  properties: {
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: metricsPublisherRoleId
  }
}

resource insightsMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(applicationInsights.id, collectorIdentity.id, 'metrics-publisher')
  scope: applicationInsights
  properties: {
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: metricsPublisherRoleId
  }
}

module functionPlan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'functionPlan'
  params: {
    name: 'plan-${prefix}-${suffix}'
    sku: {
      name: 'FC1'
      tier: 'FlexConsumption'
    }
    reserved: true
    location: location
    tags: {
      'azd-env-name': environmentName
      workload: 'azure-compute-capacity'
    }
  }
}

module collectorFunction './app/function.bicep' = {
  name: 'collectorFunction'
  params: {
    name: functionName
    location: location
    applicationInsightsName: applicationInsights.name
    appServicePlanId: functionPlan.outputs.resourceId
    storageAccountName: functionStorageName
    deploymentStorageContainerName: deploymentContainerName
    identityId: collectorIdentity.id
    identityClientId: collectorIdentity.properties.clientId
    virtualNetworkSubnetId: functionIntegrationSubnet.id
    appSettings: {
      TIMER_SCHEDULE: '0 0 0 * * *'
      AZURE_CLIENT_ID: collectorIdentity.properties.clientId
      AZURE_MANAGEMENT_GROUP_ID: managementGroupId
      AZURE_DATA_COLLECTION_ENDPOINT: dataCollectionEndpoint.properties.logsIngestion.endpoint
      AZURE_DATA_COLLECTION_RULE_ID: dataCollectionRule.properties.immutableId
      AZURE_DATA_COLLECTION_STREAM: streamName
      AZURE_DATA_COLLECTION_RUN_STREAM: runStreamName
      AZURE_STORAGE_BLOB_ENDPOINT: functionStorageAccount.properties.primaryEndpoints.blob
      AZURE_CAPACITY_WORK_CONTAINER: workContainerName
      DURABLE_TASK_SCHEDULER_CONNECTION_STRING: 'Endpoint=${durableTaskScheduler.outputs.endpoint};Authentication=ManagedIdentity;ClientID=${collectorIdentity.properties.clientId}'
      TASKHUB_NAME: durableTaskScheduler.outputs.taskHubName
    }
    tags: {
      'azd-env-name': environmentName
      workload: 'azure-compute-capacity'
    }
  }
  dependsOn: [
    collectorStorageBlobOwner
    collectorStorageQueueContributor
    collectorStorageTableContributor
    dcrMetricsPublisher
    durableTaskAccess
    durableTaskDashboardAccess
    insightsMetricsPublisher
  ]
}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'azure-compute-capacity-workbook')
  location: location
  kind: 'shared'
  properties: {
    category: 'workbook'
    displayName: 'Azure Compute Capacity'
    sourceId: workspace.id
    serializedData: replace(
      loadTextContent('../workbook/workbook.json'),
      '__WORKSPACE_ID__',
      workspace.id
    )
    version: '1.0'
  }
}

output functionName string = collectorFunction.outputs.functionName
output collectorIdentityId string = collectorIdentity.id
output collectorPrincipalId string = collectorIdentity.properties.principalId
output durableTaskSchedulerName string = durableTaskScheduler.outputs.name
output workbookId string = workbook.id
output virtualNetworkId string = virtualNetwork.id
output functionNatPublicIpAddress string = functionNatPublicIp.properties.ipAddress
