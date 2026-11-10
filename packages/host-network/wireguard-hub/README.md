# WireGuard Hub

This deployment creates a small Azure WireGuard hub for pairing orchestration peers without giving those peers Internet
egress. It is designed for `CloudWorkStations + MSASGTools Email Bots` and defaults every taggable resource to
`owner=nibeauli`.

## Architecture

- `Standard_B2pts_v2` Arm64 Ubuntu 24.04 VM with a static Standard IPv4 public endpoint.
- Azure-managed DNS name on the Standard static IPv4 public IP.
- `FirstPartyUsage` attribution for the public IP allocation through the `/MicrosoftAIMsftCorp` service tag.
- UDP 51820 ingress restricted to the internal `CorpNetPublic` service tag by default.
- Deployment-managed fixed-site document and guest bootstrap delivered through Azure VM Run Command.
- NIC and Linux IP forwarding for gateway peers and attached Azure subnets.
- Route table that sends the overlay prefix to the hub VM; attach it to Azure subnets that need return routes.
- `nftables` forwarding policy that permits peer-to-peer and explicitly configured VNet routes but never masquerades or
  forwards peers to the Internet.

The hub generates its WireGuard private key locally with `wg genkey`. The private key never enters ARM, deployment logs,
or source control.

## CorpNet and IPv6 ingress constraints

`CorpNetPublic` is the internal service tag for ordinary Microsoft corporate-network sources. `CorpNetSaw` is narrower
and covers SAW devices. These tags can be configured through ARM/Bicep even though they are intentionally absent from the
Azure portal and public service-tag discovery API.

Internal Azure networking guidance also indicates that non-platform service tags such as `CorpNetPublic` currently have
no IPv6 prefixes. The deployment therefore exposes only the CorpNet-restricted IPv4 endpoint while retaining private
IPv6 on the VNet and hub NIC.

## Validate without deploying

```powershell
az bicep build --file .\main.bicep
python -m unittest discover -s .\tests -v
```

Azure requires a Linux provisioning credential even though this deployment exposes no SSH ingress. Generate a one-time
key, pass only its public half, and delete both files after validation or deployment. Azure Run Command remains the
management path.

```powershell
$subscription = 'CloudWorkStations + MSASGTools Email Bots'
$temporaryDirectory = Join-Path $env:TEMP ('wireguard-deploy-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
$keyPath = Join-Path $temporaryDirectory 'provisioning'
ssh-keygen -q -t ed25519 -N '' -f $keyPath
$env:WIREGUARD_PROVISIONING_SSH_PUBLIC_KEY = (Get-Content "$keyPath.pub" -Raw).Trim()

az deployment sub validate `
  --subscription $subscription `
  --location westus2 `
  --parameters .\main.bicepparam

Remove-Item Env:\WIREGUARD_PROVISIONING_SSH_PUBLIC_KEY
Remove-Item -LiteralPath $keyPath, "$keyPath.pub"
Remove-Item -LiteralPath $temporaryDirectory
```

## Deploy

Deployment is intentionally not performed by this repository setup.

```powershell
$subscription = 'CloudWorkStations + MSASGTools Email Bots'
$temporaryDirectory = Join-Path $env:TEMP ('wireguard-deploy-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
$keyPath = Join-Path $temporaryDirectory 'provisioning'
ssh-keygen -q -t ed25519 -N '' -f $keyPath
$env:WIREGUARD_PROVISIONING_SSH_PUBLIC_KEY = (Get-Content "$keyPath.pub" -Raw).Trim()

az deployment sub create `
  --subscription $subscription `
  --location westus2 `
  --name wireguard-hub `
  --parameters .\main.bicepparam

Remove-Item Env:\WIREGUARD_PROVISIONING_SSH_PUBLIC_KEY
Remove-Item -LiteralPath $keyPath, "$keyPath.pub"
Remove-Item -LiteralPath $temporaryDirectory
```

Use the deployment outputs to obtain the VM, public IPv4, DNS name, and overlay route table names.

## Define fixed sites

Sites are the deployment unit. Each site keeps its private key locally and contributes only:

- a stable name;
- its WireGuard public key;
- one overlay host address; and
- zero or more private subnet prefixes routed through that site.

Configure the complete inventory in a `.bicepparam` file:

```bicep
param sites = [
  {
    name: 'local-workstation'
    overlayAddress: '10.82.0.2/32'
    publicKey: '<wireguard-public-key>'
    subnetPrefixes: []
  }
  {
    name: 'azure-gateway'
    overlayAddress: '10.82.0.3/32'
    publicKey: '<wireguard-public-key>'
    subnetPrefixes: [
      '10.90.0.0/16'
    ]
  }
]
```

Redeploying updates the site document through an ARM-managed VM Run Command, so Azure deployment RBAC and activity logs
govern every site addition, removal, key rotation, or subnet change. The hub rejects malformed keys, unknown fields,
public/default routes, duplicate public keys, and overlapping site routes. A systemd timer reapplies local desired state
every 30 seconds.

## Retrieve the hub public key

No password or inbound SSH rule is configured. The provisioning key is intentionally discarded after deployment.
Retrieve the generated WireGuard public key and perform emergency management through Azure Run Command:

```powershell
az vm run-command invoke `
  --subscription 'CloudWorkStations + MSASGTools Email Bots' `
  --resource-group rg-mai-orchestration-wireguard `
  --name <virtual-machine-name> `
  --command-id RunShellScript `
  --scripts 'cat /etc/wireguard/public.key' `
  --query 'value[0].message' `
  --output tsv
```

## Attach Azure subnets

Associate the emitted `overlayRouteTableId` with any Azure subnet that needs to initiate or return traffic to the
`10.82.0.0/24` overlay. The VM NIC and Linux host both enable IP forwarding. Add each reachable Azure CIDR to
`attachedNetworkPrefixes`; traffic outside those networks remains denied by the host forwarding policy.

Do not add `0.0.0.0/0` or `::/0` to `attachedNetworkPrefixes` or a site's `subnetPrefixes`. The hub rejects public and
unspecified routes, and it does not configure NAT.

## Connect Podman sites

Use [`podman-site-gateway`](podman-site-gateway/README.md) to attach each fixed site's dedicated Podman `/24` to the
WireGuard overlay. The gateway runs as a container at `.2`, while application pods use stable addresses from the rest of
the site subnet. Route sidecars update each pod's shared network namespace without adding NAT.

## Connect Ubuntu / WSL devboxes

Use the [Ubuntu / WSL bootstrap](scripts/README.md) for persistent WireGuard configuration, optional container-gateway
forwarding, and public-key SSH restricted to the overlay. It covers generating/importing site identities, registering
and revoking peers, and recovering a replacement machine. Hub inventory updates remain explicit Azure redeployments.
