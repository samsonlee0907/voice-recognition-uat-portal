@description('Development only. Creates a new Foundry resource, not a GPT-Live deployment or model access approval.')
param name string
param location string = 'eastus2'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

output resourceId string = account.id
output voiceLiveEndpoint string = 'https://${name}.services.ai.azure.com'
output gptLiveEndpoint string = 'https://${name}.openai.azure.com'
