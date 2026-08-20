param name string
param taskHubName string
param location string = resourceGroup().location
param tags object = {}

resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    ipAllowlist: [
      '0.0.0.0/0'
    ]
    sku: {
      name: 'Consumption'
    }
  }
}

resource taskHub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = {
  parent: scheduler
  name: taskHubName
}

output endpoint string = scheduler.properties.endpoint
output name string = scheduler.name
output taskHubName string = taskHub.name
