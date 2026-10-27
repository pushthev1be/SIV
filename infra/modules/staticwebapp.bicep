// The public demo: the SIV viewer, the evaluation page and their pre-built data, as a Static Web App.
// Free tier: no cost, no server, no secrets in the page (the agent desk's output is cached JSON).

@description('Static Web Apps region (Free tier is offered in a handful of regions; eastus2 is one).')
param location string
param name string
param tags object = {}

resource site 'Microsoft.Web/staticSites@2023-12-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Disabled'
  }
}

output name string = site.name
output url string = 'https://${site.properties.defaultHostname}'
