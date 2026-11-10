[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Copy-File {
    param([string]$Source, [string]$Destination)

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Copy-GitPaths {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$Paths = @('')
    )

    $files = & git -C $Source ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to enumerate Git source: $Source"
    }

    foreach ($relative in $files) {
        $normalized = $relative.Replace('\', '/')
        $selected = $false
        foreach ($path in $Paths) {
            $prefix = $path.Trim('/').Replace('\', '/')
            if ($prefix -eq '' -or $normalized -eq $prefix -or $normalized.StartsWith($prefix + '/')) {
                $selected = $true
                break
            }
        }
        if (-not $selected) {
            continue
        }
        Copy-File (Join-Path $Source $relative) (Join-Path $Destination $relative)
    }
}

function Copy-Tree {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludedDirectories = @(),
        [string[]]$ExcludedFiles = @()
    )

    Get-ChildItem -LiteralPath $Source -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($Source.Length).TrimStart('\')
        $segments = $relative.Split('\')
        if ($segments | Where-Object { $_ -in $ExcludedDirectories }) {
            return
        }
        if ($_.Name -in $ExcludedFiles) {
            return
        }
        Copy-File $_.FullName (Join-Path $Destination $relative)
    }
}

function Copy-SelectedFiles {
    param([string]$Source, [string]$Destination, [string[]]$Names)

    foreach ($name in $Names) {
        $path = Join-Path $Source $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Copy-File $path (Join-Path $Destination $name)
        }
    }
}

$hostCapabilities = 'Q:\src\tools\HostCapabilities'
$localLlmGateway = 'Q:\src\tools\LocalLlmGateway'
$adoDevProxy = 'Q:\src\tools\ado-dev-proxy'
$wireGuardHub = 'Q:\src\tools\wireguard-hub'
$environmentMap = 'Q:\src\environment-map'
$kagentLab = 'Q:\tools\kagent-lab'

Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\host-gateway\HostCapabilities') @(
    '.gitattributes', '.gitignore', 'AGENTS.md', 'Directory.Build.props', 'Directory.Packages.props', 'global.json',
    'host.example.json', 'NuGet.Config', 'README.md', 'native-ado', 'src/HostCapabilities',
    'tests/HostCapabilities.Tests', 'deployment/host', 'deployment/native-host'
)
Copy-GitPaths $localLlmGateway (Join-Path $Root 'packages\host-gateway\LocalLlmGateway')
Copy-GitPaths $adoDevProxy (Join-Path $Root 'packages\host-gateway\ado-dev-proxy')

Copy-GitPaths $wireGuardHub (Join-Path $Root 'packages\host-network\wireguard-hub')
Copy-Tree (Join-Path $kagentLab 'proxy-access') (Join-Path $Root 'packages\host-network\proxy-access') `
    @('__pycache__', 'packages', 'public') @('bridge.json')
Copy-Tree 'Q:\tools\microk8s-storage' (Join-Path $Root 'packages\host-network\microk8s-storage')

Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\agent-runtime') @(
    '.gitattributes', '.gitignore', 'Directory.Build.props', 'Directory.Packages.props', 'global.json', 'NuGet.Config',
    'src/AgencyCoder', 'src/AgencyHarness', 'tests/AgencyCoder.Tests', 'deployment/agency-coder/README.md',
    'deployment/agency-coder/model.yaml', 'deployment/agency-coder/native.yaml'
)
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\agent-workloads') @(
    'deployment/agency-coder/audit', 'deployment/agency-coder/engsys'
)

$platformFiles = @(
    'AGENTS.md', 'build-bootstrap.sh', 'byo-agent.yaml', 'golden-template.yaml', 'install-platform.sh',
    'kagent-values.yaml', 'publish-byo-image.py', 'smoke-byo.py', 'substrate-bootstrap-values.yaml',
    'substrate-values.yaml', 'test-byo-live.py', 'test-byo-workers.py', 'test-golden-runtime.py',
    'worker-placement.yaml'
)
Copy-SelectedFiles $kagentLab (Join-Path $Root 'packages\kagent-platform') $platformFiles
Copy-Tree (Join-Path $kagentLab 'bootstrap-platform') (Join-Path $Root 'packages\kagent-platform\bootstrap-platform')
Copy-Tree (Join-Path $kagentLab 'factory-coding') (Join-Path $Root 'packages\kagent-platform\factory-coding') `
    @('__pycache__', '.venv', 'artifacts', 'logs', 'state') @('service.json', 'controller-token')
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\kagent-platform\deployments') @(
    'deployment/a2a-sanity', 'deployment/baseline', 'deployment/headlamp'
)

Copy-SelectedFiles (Join-Path $kagentLab 'agency-broker') (Join-Path $Root 'packages\identity\agency-broker') @(
    'broker.py', 'configure.py', 'empty-config.toml', 'Install-AgencyBroker.ps1', 'prepare_runtime.py',
    'profiles.json', 'test_broker.py', 'test_windows_relay.py', 'verify_pr.py', 'verify_shared.py', 'windows_relay.py'
)
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\identity\deployments') @(
    'deployment/access', 'deployment/management'
)

Copy-SelectedFiles $kagentLab (Join-Path $Root 'packages\platform-state') @(
    'backup-and-test-recovery.py', 'backup-node.sh', 'prepare-worker-storage.sh', 'registry.yaml'
)
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\platform-state\deployments') @(
    'deployment/archive', 'deployment/memory'
)

Copy-SelectedFiles $kagentLab (Join-Path $Root 'packages\observability') @(
    'check-observability.py', 'substrate-monitors.yaml'
)
Copy-SelectedFiles (Join-Path $kagentLab 'factory-coding') (Join-Path $Root 'packages\observability\factory-values') @(
    'observability-values.yaml', 'tempo-values.yaml', 'otel_exporter.py', 'swarm_otel_replay.py'
)
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\observability\deployments') @('deployment/otel')

Copy-SelectedFiles $kagentLab (Join-Path $Root 'packages\operations') @(
    'enable-certificates.sh', 'Invoke-LinuxScript.ps1', 'lab_transport.py', 'OPERATIONS.md'
)
Copy-GitPaths $hostCapabilities (Join-Path $Root 'packages\operations\deployments') @(
    'deployment/maintenance', 'deployment/native-canary'
)
Copy-GitPaths $environmentMap (Join-Path $Root 'docs\current-state')

Write-Host "Imported current authoritative source into $Root"
