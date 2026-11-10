[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$names = @('.artifacts', '.state', '.venv', '__pycache__', 'bin', 'obj', 'node_modules')

$targets = Get-ChildItem -LiteralPath $Root -Directory -Recurse -Force | Where-Object {
    $_.Name -in $names
} | Sort-Object { $_.FullName.Length } -Descending

foreach ($target in $targets) {
    $resolved = [IO.Path]::GetFullPath($target.FullName)
    if (-not $resolved.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean path outside repository: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Write-Host "Cleaned generated repository directories"
