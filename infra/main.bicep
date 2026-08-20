targetScope = 'subscription'

@minLength(1)
@maxLength(32)
param environmentName string

param location string

@description('Management group whose descendant subscriptions are inventoried.')
param managementGroupId string

@description('Object ID of the principal running azd, used to upload Function packages.')
param principalId string = deployer().objectId

var resourceGroupName = 'rg-capacity-${environmentName}'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    'azd-env-name': environmentName
    workload: 'azure-compute-capacity'
  }
}

module resources './resources.bicep' = {
  name: 'capacityResources'
  scope: resourceGroup
  params: {
    environmentName: environmentName
    location: location
    managementGroupId: managementGroupId
    principalId: principalId
  }
}

output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_FUNCTION_NAME string = resources.outputs.functionName
output AZURE_COLLECTOR_IDENTITY_ID string = resources.outputs.collectorIdentityId
output AZURE_COLLECTOR_PRINCIPAL_ID string = resources.outputs.collectorPrincipalId
output AZURE_MANAGEMENT_GROUP_ID string = managementGroupId
output AZURE_WORKBOOK_ID string = resources.outputs.workbookId
output AZURE_WORKBOOK_PORTAL_URL string = 'https://portal.azure.com/#@/resource${resources.outputs.workbookId}/workbook'
