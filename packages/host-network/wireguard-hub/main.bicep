targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'westus2'

@description('Resource group created for the WireGuard hub.')
param resourceGroupName string = 'nibeauli-hub'

@description('Short prefix used for globally unique resource names.')
@minLength(3)
@maxLength(18)
param namePrefix string = 'nibeauli-hub'

@description('Alias recorded on every taggable resource.')
param ownerAlias string = 'nibeauli'

@description('First-party service tag used to attribute the public IPv4 allocation.')
param publicIpServiceTag string = '/MicrosoftAIMsftCorp'

@description('One-time SSH public key required by Azure Linux provisioning. No inbound SSH rule is created.')
param provisioningSshPublicKey string

@description('Fixed WireGuard sites. Each site has one peer public key, one overlay host address, and private routed subnets.')
param sites array = []

@description('Sources permitted to reach UDP 51820. CorpNetPublic covers Microsoft corporate networks; CorpNetSaw may be added for SAW devices.')
param wireGuardIngressSourcePrefixes array = [
  'CorpNetPublic'
]

var deploymentTags = {
  owner: ownerAlias
  purpose: 'orchestration-environment'
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: deploymentTags
}

module hub 'modules/hub.bicep' = {
  name: 'wireguard-hub'
  scope: resourceGroup
  params: {
    deploymentTags: deploymentTags
    location: location
    namePrefix: namePrefix
    provisioningSshPublicKey: provisioningSshPublicKey
    publicIpServiceTag: publicIpServiceTag
    sites: sites
    wireGuardIngressSourcePrefixes: wireGuardIngressSourcePrefixes
  }
}

output hubPrivateIpv4Address string = hub.outputs.hubPrivateIpv4Address
output overlayRouteTableId string = hub.outputs.overlayRouteTableId
output publicIpv4Address string = hub.outputs.publicIpv4Address
output publicIpv4Fqdn string = hub.outputs.publicIpv4Fqdn
output virtualMachineName string = hub.outputs.virtualMachineName
