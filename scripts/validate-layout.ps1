[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Packages = @(
    'host-gateway', 'host-network', 'agent-runtime', 'agent-workloads', 'kagent-platform', 'identity',
    'platform-state', 'observability', 'operations'
)

foreach ($package in $Packages) {
    $path = Join-Path $Root "packages\$package"
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Missing package: $package"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $path 'README.md') -PathType Leaf)) {
        throw "Missing package README: $package"
    }
}

$forbiddenNames = @('service.json', 'bridge.json', 'actors.json', 'host.production.json', 'proxy.production.json')
$violations = Get-ChildItem -LiteralPath $Root -File -Recurse | Where-Object {
    $_.Name -in $forbiddenNames -or $_.Extension -in @('.key', '.pfx', '.p12', '.pem', '.token', '.kubeconfig')
}
if ($violations) {
    $paths = $violations.FullName -join [Environment]::NewLine
    throw "Protected files found in repository:`n$paths"
}

$generatedDirectories = Get-ChildItem -LiteralPath $Root -Directory -Recurse | Where-Object {
    $_.Name -in @('.artifacts', '.state', '.venv', '__pycache__', 'bin', 'obj', 'node_modules')
}
if ($generatedDirectories) {
    $paths = $generatedDirectories.FullName -join [Environment]::NewLine
    throw "Generated directories found in repository:`n$paths"
}

Write-Host "PASS: package layout and protected-path checks"
