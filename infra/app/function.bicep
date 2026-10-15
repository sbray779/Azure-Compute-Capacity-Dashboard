param name string
param location string = resourceGroup().location
param tags object = {}
param applicationInsightsName string
param appServicePlanId string
param appSettings object = {}
param storageAccountName string
param deploymentStorageContainerName string
param identityId string
param identityClientId string
param virtualNetworkSubnetId string

var applicationInsightsIdentity = 'ClientId=${identityClientId};Authorization=AAD'
var baseAppSettings = {
  AzureWebJobsStorage__credential: 'managedidentity'
  AzureWebJobsStorage__clientId: identityClientId
  AzureWebJobsStorage__blobServiceUri: storage.properties.primaryEndpoints.blob
  AzureWebJobsStorage__queueServiceUri: storage.properties.primaryEndpoints.queue
  AzureWebJobsStorage__tableServiceUri: storage.properties.primaryEndpoints.table
  APPLICATIONINSIGHTS_AUTHENTICATION_STRING: applicationInsightsIdentity
  APPLICATIONINSIGHTS_CONNECTION_STRING: applicationInsights.properties.ConnectionString
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

module functionApp 'br/public:avm/res/web/site:0.15.1' = {
  name: 'collector-flex-consumption'
  params: {
    kind: 'functionapp,linux'
    name: name
    location: location
    tags: union(tags, {
      'azd-service-name': 'collector'
    })
    serverFarmResourceId: appServicePlanId
    virtualNetworkSubnetId: virtualNetworkSubnetId
    vnetRouteAllEnabled: true
    managedIdentities: {
      systemAssigned: false
      userAssignedResourceIds: [
        identityId
      ]
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentStorageContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identityId
          }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 2048
        maximumInstanceCount: 10
      }
      runtime: {
        name: 'python'
        version: '3.13'
      }
    }
    siteConfig: {
      alwaysOn: false
      minTlsVersion: '1.2'
      cors: {
        allowedOrigins: [
          'https://portal.azure.com'
        ]
      }
    }
    basicPublishingCredentialsPolicies: [
      {
        name: 'ftp'
        allow: false
      }
      {
        name: 'scm'
        allow: false
      }
    ]
    appSettingsKeyValuePairs: union(appSettings, baseAppSettings)
  }
}

output functionName string = functionApp.outputs.name
