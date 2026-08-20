param schedulerName string
param principalId string
param principalType string = 'ServicePrincipal'

var durableTaskDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0ad04412-c4d5-4796-b79c-f76d14c8d402'
)

resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' existing = {
  name: schedulerName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(scheduler.id, principalId, durableTaskDataContributorRoleId)
  scope: scheduler
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: durableTaskDataContributorRoleId
  }
}
