[CmdletBinding()]
param(
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'HostCapabilities')
)
$ErrorActionPreference = 'Stop'
$canary = Join-Path $StateRoot 'host.canary.json'
$settings = Get-Content -LiteralPath $canary -Raw | ConvertFrom-Json
$settings.stateDirectory = Join-Path $StateRoot 'service'
$settings.llm.options.host = '172.24.160.1'
$settings.llm.options.port = 4141
$settings.llm.options.gatewayKeyFile = Join-Path $env:APPDATA 'local-llm-gateway\gateway-key'
$settings.ado.mode = 'embedded'
$settings.ado.stateDirectory = Join-Path $env:LOCALAPPDATA 'AdoDevProxy'
$policy = Join-Path $StateRoot 'ado.production.json'
Copy-Item -LiteralPath 'Q:\src\tools\ado-dev-proxy\proxy.example.json' -Destination $policy
$settings.ado.configurationFile = $policy
$settings.agency.port = 15442
$settings.agency.stateDirectory = Join-Path $StateRoot 'agency'
$settings.telemetry.serviceName = 'host-capabilities'
$settings.nativeAdo.enabled = $false
$path = Join-Path $StateRoot 'host.production.json'
$settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $path -Encoding utf8NoBOM
Write-Output "Prepared protected production configuration: $path"
