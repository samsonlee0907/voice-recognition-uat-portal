@description('Hosts this portal using an existing ACR and existing qualified Foundry endpoints. Does not deploy models.')
param location string = resourceGroup().location
param appName string
param registryName string
@description('Optional existing Container Apps environment in this resource group. Leave empty to create a new environment.')
param existingEnvironmentName string = ''
param containerImage string
param voiceLiveEndpoint string
param gptLiveEndpoint string
param gptLiveDeployment string = 'gpt-live-1'
param appVersion string = 'recognition-uat-20261104-r2'
@secure()
param portalUsername string
@secure()
param portalPasswordHash string
@secure()
param portalSessionSecret string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = if (empty(existingEnvironmentName)) {
  name: '${appName}-environment'
  location: location
  properties: {}
}
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [pull]
  properties: {
    managedEnvironmentId: empty(existingEnvironmentName)
      ? environment!.id
      : resourceId('Microsoft.App/managedEnvironments', existingEnvironmentName)
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: [
        { name: 'portal-username', value: portalUsername }
        { name: 'portal-password-hash', value: portalPasswordHash }
        { name: 'portal-session-secret', value: portalSessionSecret }
      ]
    }
    template: {
      containers: [{
        name: 'recognition-uat'
        image: containerImage
        env: [
          { name: 'VOICE_LIVE_ENDPOINT', value: voiceLiveEndpoint }
          { name: 'GPT_LIVE_ENDPOINT', value: gptLiveEndpoint }
          { name: 'GPT_LIVE_DEPLOYMENT', value: gptLiveDeployment }
          { name: 'AZURE_AUTH_MODE', value: 'managed_identity' }
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'APP_VERSION', value: appVersion }
          { name: 'PORTAL_USERNAME', secretRef: 'portal-username' }
          { name: 'PORTAL_PASSWORD_HASH', secretRef: 'portal-password-hash' }
          { name: 'PORTAL_SESSION_SECRET', secretRef: 'portal-session-secret' }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [
          { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 10, periodSeconds: 15 }
          { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 5, periodSeconds: 10 }
        ]
      }]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
