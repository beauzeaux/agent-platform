using './main.bicep'

param location = 'westus2'
param namePrefix = 'nibeauli-hub'
param ownerAlias = 'nibeauli'
param provisioningSshPublicKey = readEnvironmentVariable('WIREGUARD_PROVISIONING_SSH_PUBLIC_KEY')
param resourceGroupName = 'nibeauli-hub'

// Each site uses a stable WireGuard public key, overlay host address, and optional private routed subnets.
param sites = [
  {
    name: 'primary-devbox'
    overlayAddress: '10.82.0.2/32'
    publicKey: '0aoSNz44+fLqr30aJV3tlWGq3n2dT2hQD/GGbB5gRGI='
    subnetPrefixes: [
      '172.30.0.0/24'
    ]
  }
  {
    name: 'xl-devbox'
    overlayAddress: '10.82.0.3/32'
    publicKey: 'D3GY83G4qeNoSJdyor1SR2z1ijjri6oTwu9wfK5wXzQ='
    subnetPrefixes: [
      '172.30.1.0/24'
    ]
  }
  {
    name: 'devbox-2'
    overlayAddress: '10.82.0.4/32'
    publicKey: 'UWa0jCMdVkFUOEWRDoKvxVwGi/BhX3ggwB7MejytDAw='
    subnetPrefixes: [
      '172.30.2.0/24'
    ]
  }
]

// CorpNetPublic is an internal ARM-only tag and therefore absent from the public service-tag inventory.
// Add CorpNetSaw only when SAW-originated access is required.
param wireGuardIngressSourcePrefixes = [
  'CorpNetPublic'
]
