[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot 'validate-layout.ps1')

$dotnet = if (Test-Path 'C:\Users\nibeauli\.dotnet\dotnet.exe') {
    'C:\Users\nibeauli\.dotnet\dotnet.exe'
} else {
    'dotnet'
}

try {
    & $dotnet test (Join-Path $Root 'packages\agent-runtime\tests\AgencyCoder.Tests\AgencyCoder.Tests.csproj') `
        --nologo --verbosity minimal
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $hostArtifacts = Join-Path $Root 'packages\host-gateway\HostCapabilities\.artifacts\'
    & $dotnet test `
        (Join-Path $Root 'packages\host-gateway\HostCapabilities\tests\HostCapabilities.Tests\HostCapabilities.Tests.csproj') `
        --nologo --verbosity minimal -p:UseArtifactsOutput=true "-p:ArtifactsPath=$hostArtifacts"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $projects = @(
        'packages\host-gateway\HostCapabilities\native-ado\tests\NativeAdo.Tests\NativeAdo.Tests.csproj',
        'packages\host-gateway\LocalLlmGateway\tests\LocalLlmGateway.Tests\LocalLlmGateway.Tests.csproj',
        'packages\host-gateway\ado-dev-proxy\tests\AdoDevProxy.Tests\AdoDevProxy.Tests.csproj'
    )
    foreach ($project in $projects) {
        & $dotnet test (Join-Path $Root $project) --nologo --verbosity minimal
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} finally {
    & (Join-Path $PSScriptRoot 'clean.ps1')
}
