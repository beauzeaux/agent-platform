@description('Azure region for all resources.')
param location string

@description('Short prefix used for resource names.')
param namePrefix string

@description('Tags applied to every taggable resource.')
param deploymentTags object

@description('One-time SSH public key required by Azure Linux provisioning.')
param provisioningSshPublicKey string

@description('First-party service tag used to attribute public IP allocations.')
param publicIpServiceTag string

@description('Fixed WireGuard sites deployed as one desired-state document.')
param sites array

@description('Sources permitted to reach UDP 51820.')
param wireGuardIngressSourcePrefixes array

@description('Smallest practical non-retiring Arm64 B-series v2 size available in West US 2.')
param vmSize string = 'Standard_B2pts_v2'

@description('Local administrator account. No password or public SSH ingress is configured.')
param adminUsername string = 'azureuser'

@description('Private IPv4 VNet address space.')
param virtualNetworkIpv4Prefix string = '10.81.0.0/24'

@description('Private IPv6 VNet address space. Azure requires an IPv6 subnet to be /64.')
param virtualNetworkIpv6Prefix string = 'fd81:0:0:1::/64'

@description('Dual-stack hub subnet IPv4 address space.')
param hubSubnetIpv4Prefix string = '10.81.0.0/27'

@description('Static private IPv4 address assigned to the hub.')
param hubPrivateIpv4Address string = '10.81.0.4'

@description('Static private IPv6 address assigned to the hub.')
param hubPrivateIpv6Address string = 'fd81:0:0:1::4'

@description('WireGuard overlay interface address.')
param wireGuardAddress string = '10.82.0.1/24'

@description('WireGuard overlay route advertised to attached Azure subnets.')
param wireGuardOverlayPrefix string = '10.82.0.0/24'

@description('Azure/VNet networks that WireGuard peers may reach through the hub. No Internet prefix is allowed.')
param attachedNetworkPrefixes array = [
  virtualNetworkIpv4Prefix
]

@description('WireGuard UDP listen port.')
@minValue(1)
@maxValue(65535)
param wireGuardPort int = 51820

var resourceSuffix = uniqueString(subscription().id, resourceGroup().id, namePrefix)
var networkSecurityGroupName = '${namePrefix}-nsg'
var networkInterfaceName = '${namePrefix}-nic'
var overlayRouteTableName = '${namePrefix}-overlay-rt'
var publicIpv4Name = '${namePrefix}-pip4'
var publicIpv4DnsLabel = take('${replace(namePrefix, '-', '')}-${resourceSuffix}-v4', 63)
var virtualMachineName = '${namePrefix}-vm'
var virtualNetworkName = '${namePrefix}-vnet'

var wireGuardConfigLibrary = replace(loadTextContent('../scripts/wireguard_config.py'), '\r\n', '\n')
var wireGuardSyncScript = replace(loadTextContent('../scripts/wireguard_sync.py'), '\r\n', '\n')
var wireGuardSites = string(sites)
var wireGuardEnvironment = format(
  'WIREGUARD_ADDRESS={0}\nWIREGUARD_ATTACHED_NETWORKS={1}\nWIREGUARD_HOST_INTERFACE=eth0\nWIREGUARD_INTERFACE=wg0\nWIREGUARD_LISTEN_PORT={2}\nWIREGUARD_SITES_PATH=/etc/wireguard/sites.json\n',
  wireGuardAddress,
  join(attachedNetworkPrefixes, ','),
  wireGuardPort
)
var wireGuardService = '''
[Unit]
Description=Apply the deployed WireGuard site configuration
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/wireguard/hub.env
ExecStart=/usr/local/sbin/wireguard-sync
'''
var wireGuardTimer = '''
[Unit]
Description=Periodically synchronize WireGuard peer configuration

[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
RandomizedDelaySec=5s
Unit=wireguard-sync.service

[Install]
WantedBy=timers.target
'''
var forwardingSysctl = '''
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
'''
var applySitesScript = format(
  'set -eu\nwhile [ ! -e /var/lib/cloud/instance/boot-finished ]; do sleep 2; done\nif ! command -v wg >/dev/null || ! command -v nft >/dev/null; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y nftables wireguard-tools; fi\ninstall -d -m 0700 /etc/wireguard\nprintf %s {0} | base64 -d > /etc/wireguard/sites.json\nprintf %s {1} | base64 -d > /usr/local/sbin/wireguard_config.py\nprintf %s {2} | base64 -d > /usr/local/sbin/wireguard-sync\nprintf %s {3} | base64 -d > /etc/wireguard/hub.env\nprintf %s {4} | base64 -d > /etc/systemd/system/wireguard-sync.service\nprintf %s {5} | base64 -d > /etc/systemd/system/wireguard-sync.timer\nprintf %s {6} | base64 -d > /etc/sysctl.d/90-wireguard-hub.conf\nchmod 0600 /etc/wireguard/hub.env /etc/wireguard/sites.json\nchmod 0644 /etc/systemd/system/wireguard-sync.service /etc/systemd/system/wireguard-sync.timer /etc/sysctl.d/90-wireguard-hub.conf /usr/local/sbin/wireguard_config.py\nchmod 0755 /usr/local/sbin/wireguard-sync\ntest -s /etc/wireguard/private.key || (umask 077; wg genkey > /etc/wireguard/private.key)\nwg pubkey < /etc/wireguard/private.key > /etc/wireguard/public.key\nsysctl --system\nsystemctl daemon-reload\nsystemctl enable --now wireguard-sync.timer\nsystemctl restart wireguard-sync.service\n',
  base64(wireGuardSites),
  base64(wireGuardConfigLibrary),
  base64(wireGuardSyncScript),
  base64(wireGuardEnvironment),
  base64(wireGuardService),
  base64(wireGuardTimer),
  base64(forwardingSysctl)
)

var wireGuardIngressIpv4Rules = [
  for (sourcePrefix, index) in wireGuardIngressSourcePrefixes: {
    name: 'AllowWireGuardIpv4${index}'
    properties: {
      access: 'Allow'
      destinationAddressPrefix: hubPrivateIpv4Address
      destinationPortRange: string(wireGuardPort)
      direction: 'Inbound'
      priority: 100 + index
      protocol: 'Udp'
      sourceAddressPrefix: sourcePrefix
      sourcePortRange: '*'
    }
  }
]

var wireGuardIngressIpv6Rules = [
  for (sourcePrefix, index) in wireGuardIngressSourcePrefixes: {
    name: 'AllowWireGuardIpv6${index}'
    properties: {
      access: 'Allow'
      destinationAddressPrefix: hubPrivateIpv6Address
      destinationPortRange: string(wireGuardPort)
      direction: 'Inbound'
      priority: 200 + index
      protocol: 'Udp'
      sourceAddressPrefix: sourcePrefix
      sourcePortRange: '*'
    }
  }
]

resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: networkSecurityGroupName
  location: location
  tags: deploymentTags
  properties: {
    securityRules: concat(
      wireGuardIngressIpv4Rules,
      wireGuardIngressIpv6Rules,
      [
        {
          name: 'DenyInternetInbound'
          properties: {
            access: 'Deny'
            destinationAddressPrefix: '*'
            destinationPortRange: '*'
            direction: 'Inbound'
            priority: 4096
            protocol: '*'
            sourceAddressPrefix: 'Internet'
            sourcePortRange: '*'
          }
        }
        {
          name: 'AllowHubHttpOutboundIpv4'
          properties: {
            access: 'Allow'
            destinationAddressPrefix: 'Internet'
            destinationPortRanges: [
              '80'
              '443'
            ]
            direction: 'Outbound'
            priority: 100
            protocol: 'Tcp'
            sourceAddressPrefix: '${hubPrivateIpv4Address}/32'
            sourcePortRange: '*'
          }
        }
        {
          name: 'AllowHubHttpOutboundIpv6'
          properties: {
            access: 'Allow'
            destinationAddressPrefix: 'Internet'
            destinationPortRanges: [
              '80'
              '443'
            ]
            direction: 'Outbound'
            priority: 110
            protocol: 'Tcp'
            sourceAddressPrefix: '${hubPrivateIpv6Address}/128'
            sourcePortRange: '*'
          }
        }
        {
          name: 'DenyForwardedInternetOutbound'
          properties: {
            access: 'Deny'
            destinationAddressPrefix: 'Internet'
            destinationPortRange: '*'
            direction: 'Outbound'
            priority: 200
            protocol: '*'
            sourceAddressPrefix: '*'
            sourcePortRange: '*'
          }
        }
      ]
    )
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: virtualNetworkName
  location: location
  tags: deploymentTags
  properties: {
    addressSpace: {
      addressPrefixes: [
        virtualNetworkIpv4Prefix
        virtualNetworkIpv6Prefix
      ]
    }
    subnets: [
      {
        name: 'hub'
        properties: {
          addressPrefixes: [
            hubSubnetIpv4Prefix
            virtualNetworkIpv6Prefix
          ]
          defaultOutboundAccess: false
          networkSecurityGroup: {
            id: networkSecurityGroup.id
          }
        }
      }
    ]
  }
}

resource hubSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  name: 'hub'
  parent: virtualNetwork
}

resource publicIpv4 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: publicIpv4Name
  location: location
  tags: deploymentTags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    dnsSettings: {
      domainNameLabel: publicIpv4DnsLabel
    }
    ipTags: [
      {
        ipTagType: 'FirstPartyUsage'
        tag: publicIpServiceTag
      }
    ]
    publicIPAddressVersion: 'IPv4'
    publicIPAllocationMethod: 'Static'
  }
}

resource networkInterface 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: networkInterfaceName
  location: location
  tags: deploymentTags
  properties: {
    enableAcceleratedNetworking: false
    enableIPForwarding: true
    ipConfigurations: [
      {
        name: 'ipv4'
        properties: {
          primary: true
          privateIPAddress: hubPrivateIpv4Address
          privateIPAddressVersion: 'IPv4'
          privateIPAllocationMethod: 'Static'
          publicIPAddress: {
            id: publicIpv4.id
          }
          subnet: {
            id: hubSubnet.id
          }
        }
      }
      {
        name: 'ipv6'
        properties: {
          primary: false
          privateIPAddress: hubPrivateIpv6Address
          privateIPAddressVersion: 'IPv6'
          privateIPAllocationMethod: 'Static'
          subnet: {
            id: hubSubnet.id
          }
        }
      }
    ]
  }
}

resource virtualMachine 'Microsoft.Compute/virtualMachines@2024-11-01' = {
  name: virtualMachineName
  location: location
  tags: deploymentTags
  properties: {
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
    hardwareProfile: {
      vmSize: vmSize
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterface.id
          properties: {
            primary: true
          }
        }
      ]
    }
    osProfile: {
      adminUsername: adminUsername
      computerName: take(virtualMachineName, 64)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              keyData: provisioningSshPublicKey
              path: '/home/${adminUsername}/.ssh/authorized_keys'
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        offer: 'ubuntu-24_04-lts'
        publisher: 'Canonical'
        sku: 'server-arm64'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        deleteOption: 'Delete'
        diskSizeGB: 30
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
  }
}

resource applySiteConfiguration 'Microsoft.Compute/virtualMachines/runCommands@2024-11-01' = {
  name: 'apply-wireguard-sites'
  parent: virtualMachine
  location: location
  properties: {
    asyncExecution: false
    source: {
      script: applySitesScript
    }
    timeoutInSeconds: 600
    treatFailureAsDeploymentFailure: true
  }
}

resource overlayRouteTable 'Microsoft.Network/routeTables@2024-05-01' = {
  name: overlayRouteTableName
  location: location
  tags: deploymentTags
  properties: {
    disableBgpRoutePropagation: false
    routes: [
      {
        name: 'wireguard-overlay'
        properties: {
          addressPrefix: wireGuardOverlayPrefix
          nextHopIpAddress: hubPrivateIpv4Address
          nextHopType: 'VirtualAppliance'
        }
      }
    ]
  }
}

output hubPrivateIpv4Address string = hubPrivateIpv4Address
output overlayRouteTableId string = overlayRouteTable.id
output publicIpv4Address string = publicIpv4.properties.ipAddress
output publicIpv4Fqdn string = publicIpv4.properties.dnsSettings.fqdn
output virtualMachineName string = virtualMachine.name
