[CmdletBinding()]
param(
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'HostCapabilities')
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$null = New-Item -ItemType Directory -Path $StateRoot -Force
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $StateRoot /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect host state directory.' }
$keyFile = Join-Path $StateRoot 'canary-gateway-key'
if (-not (Test-Path -LiteralPath $keyFile)) {
    [IO.File]::WriteAllText($keyFile, [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)))
}
$ado = Get-Content -LiteralPath 'Q:\src\tools\ado-dev-proxy\proxy.example.json' -Raw | ConvertFrom-Json
$ado.port = 5482
$ado.controlPort = 5483
$adoPath = Join-Path $StateRoot 'ado.canary.json'
$ado | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $adoPath -Encoding utf8NoBOM
$settings = Get-Content -LiteralPath (Join-Path $repository 'host.example.json') -Raw | ConvertFrom-Json
$settings.stateDirectory = Join-Path $StateRoot 'canary'
$settings.llm.options.dataDir = Join-Path $env:APPDATA 'local-llm-gateway'
$settings.llm.options.gatewayKeyFile = $keyFile
$settings.ado.configurationFile = $adoPath
$settings.ado.stateDirectory = Join-Path $StateRoot 'ado-canary'
$settings.agency.pythonExecutable = 'C:\ProgramData\Tools\Python\3.12.10\x64\python.exe'
$settings.agency.brokerScript = 'Q:\tools\kagent-lab\agency-broker\broker.py'
$settings.agency.brokerConfigurationFile = 'Q:\tools\kagent-lab\agency-broker\state\broker.json'
$settings.agency.stateDirectory = Join-Path $StateRoot 'agency-canary'
$settings.agency.profilesFile = 'Q:\tools\kagent-lab\agency-broker\profiles.json'
$settings.agency.agencyExecutable = 'C:\.Tools\agency\2026.10.29.2\agency.exe'
$settings.agency.globalConfigurationFile = 'Q:\tools\kagent-lab\agency-broker\empty-config.toml'
$settings.telemetry = [pscustomobject]@{serviceName = 'host-capabilities-canary'}
$path = Join-Path $StateRoot 'host.canary.json'
$settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $path -Encoding utf8NoBOM
Write-Output "Prepared protected canary configuration: $path"
