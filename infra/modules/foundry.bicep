// Microsoft Foundry: an AI Services account with project management, one project, one model deployment.
// Key auth is off; callers use Entra ID (AzureCliCredential locally, managed identity in Azure).

param name string
param location string
param tags object
param chatModelName string
param chatModelVersion string
param chatModelCapacity int
param principalId string

var roles = {
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  cognitiveServicesOpenAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: name
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'touchline'  // resource name kept from the working title; renaming would recreate the project
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'SIV'
    description: 'Agents that explain VAR checks and calls to fans, grounded in the match data'
  }
}

// The account accepts one operation at a time, so the deployment waits for the project.
resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  dependsOn: [project]
  name: chatModelName
  sku: { name: 'GlobalStandard', capacity: chatModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: chatModelName, version: chatModelVersion }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource userRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in items(roles): if (!empty(principalId)) {
    name: guid(account.id, principalId, role.value)
    scope: account
    properties: {
      principalId: principalId
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
      principalType: 'User'
    }
  }
]

output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/${project.name}'
output accountName string = account.name
