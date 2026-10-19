// Event Hubs Basic: one hub for the match feed. Basic allows only the $Default consumer group
// and one day of retention, which is fine because only the engine reads the hub.

param name string
param location string
param tags object
param principalId string

var roles = {
  dataSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  dataReceiver: 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
}

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic', capacity: 1 }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: 'match-events'
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

resource userRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in items(roles): if (!empty(principalId)) {
    name: guid(namespace.id, principalId, role.value)
    scope: namespace
    properties: {
      principalId: principalId
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
      principalType: 'User'
    }
  }
]

output fullyQualifiedNamespace string = '${namespace.name}.servicebus.windows.net'
output hubName string = hub.name
