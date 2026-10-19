// VAR Narrative infrastructure. Deploy with `azd up` from the repo root.
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('azd environment name, used to name the resource group and resources')
param environmentName string

@description('Region for every resource. eastus2 has the widest model quota for this project.')
param location string = 'eastus2'

@description('Object ID of the developer running azd; gets data-plane roles for local runs')
param principalId string = ''

@description('Chat model deployment for all agents')
param chatModelName string = 'gpt-5-mini'
param chatModelVersion string = '2025-08-07'
@description('Thousands of tokens per minute')
param chatModelCapacity int = 100

var tags = { 'azd-env-name': environmentName, project: 'var-narrative' }
var token = toLower(uniqueString(subscription().id, environmentName, location))

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    name: 'ai-${token}'
    location: location
    tags: tags
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatModelCapacity: chatModelCapacity
    principalId: principalId
  }
}

module eventHubs 'modules/eventhubs.bicep' = {
  name: 'eventhubs'
  scope: rg
  params: {
    name: 'evh-${token}'
    location: location
    tags: tags
    principalId: principalId
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_MODEL string = chatModelName
output EVENTHUB_NAMESPACE string = eventHubs.outputs.fullyQualifiedNamespace
output EVENTHUB_NAME string = eventHubs.outputs.hubName
