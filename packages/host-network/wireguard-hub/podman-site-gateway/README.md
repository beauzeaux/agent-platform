# Podman site gateway

This image connects a dedicated Podman bridge to the Azure WireGuard hub without NAT. Run exactly one gateway with each
site's private key. The hub already routes `172.30.0.0/24` to `primary-devbox` and `172.30.1.0/24` to `xl-devbox`.

## Start a site

Run these commands from Windows PowerShell. Replace the environment file and private-key path for the current site.

```powershell
$settings = @{}
Get-Content .\sites\primary.env.example | ForEach-Object {
  if ($_ -match '^(?<key>[A-Z0-9_]+)=(?<value>.*)$') {
    $settings[$Matches['key']] = $Matches['value'].Trim()
  }
}

$requiredSettings = 'GATEWAY_IPV4', 'HUB_ENDPOINT', 'HUB_PUBLIC_KEY', 'REMOTE_PREFIXES', 'SITE_NAME', 'SITE_SUBNET',
  'WIREGUARD_ADDRESS'
$requiredSettings | ForEach-Object {
  if ([string]::IsNullOrWhiteSpace($settings[$_])) {
    throw "Missing required site setting: $_"
  }
}

$gatewayIpv4 = $settings['GATEWAY_IPV4']
$hubEndpoint = $settings['HUB_ENDPOINT']
$hubPublicKey = $settings['HUB_PUBLIC_KEY']
$remotePrefixes = $settings['REMOTE_PREFIXES']
$siteName = $settings['SITE_NAME']
$siteSubnet = $settings['SITE_SUBNET']
$wireGuardAddress = $settings['WIREGUARD_ADDRESS']

podman machine start
podman machine ssh sudo systemctl --global enable podman-restart.service
podman build --tag localhost/wg-gateway:latest .
podman network create --subnet $siteSubnet --gateway ($siteSubnet -replace '0/24$', '1') "$siteName-network"
podman secret create "$siteName-wireguard-key" C:\path\to\site.private.key

podman run --detach `
  --name "$siteName-gateway" `
  --network "$siteName-network" `
  --ip $gatewayIpv4 `
  --cap-add NET_ADMIN `
  --cap-add NET_RAW `
  --sysctl net.ipv4.ip_forward=1 `
  --secret "$siteName-wireguard-key,target=wg-private-key,mode=0400" `
  --env "HUB_ENDPOINT=$hubEndpoint" `
  --env "HUB_PUBLIC_KEY=$hubPublicKey" `
  --env "REMOTE_PREFIXES=$remotePrefixes" `
  --env "WIREGUARD_ADDRESS=$wireGuardAddress" `
  --restart always `
  localhost/wg-gateway:latest
```

The restart service and `always` policies restore the gateway and test pod after a graceful `podman machine stop`.

Do not run another peer with the same private key. Verify the gateway:

```powershell
podman exec "$siteName-gateway" wg show wg0
```

## Connect a pod

Create the pod on the site network, then add a small route sidecar to its shared network namespace:

```powershell
podman pod create `
  --name hello `
  --network "$siteName-network" `
  --ip ($siteSubnet -replace '0/24$', '10') `
  --publish 127.0.0.1:8080:8080

podman run --detach --pod hello --name hello-http --restart always docker.io/library/python:3.12-alpine `
  sh -c "mkdir -p /srv; printf 'hello from Podman\n' > /srv/index.html; exec python -m http.server 8080 --directory /srv"

podman run --detach `
  --pod hello `
  --name hello-routes `
  --cap-add NET_ADMIN `
  --entrypoint /bin/sh `
  --restart always `
  localhost/wg-gateway:latest `
  -c "ip route replace 10.81.0.0/24 via $gatewayIpv4; ip route replace 10.82.0.0/24 via $gatewayIpv4; ip route replace $($remotePrefixes.Split(',')[-1]) via $gatewayIpv4; exec sleep infinity"
```

The opposite site can reach the hello server at the pod's fixed `.10` address after its own gateway is connected.
