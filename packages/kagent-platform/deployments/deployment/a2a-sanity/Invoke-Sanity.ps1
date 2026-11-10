[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Run', 'Status', 'Remove', 'Check')][string]$Action = 'Status',
    [switch]$Apply,
    [switch]$Live,
    [string]$RunId,
    [ValidateSet('baseline', 'factory-executions')][string]$Namespace = 'baseline'
)
$ErrorActionPreference = 'Stop'
if ($RunId) { $RunId = ([guid]::Parse($RunId)).ToString() }
$arguments = @('-d', 'Ubuntu-26.04', '--user', 'root', '--exec', 'python3', '-B',
    '/mnt/q/src/tools/HostCapabilities/deployment/a2a-sanity/sanity.py', $Action.ToLowerInvariant(),
    '--namespace', $Namespace)
if ($Apply) { $arguments += '--apply' }
if ($Live) { $arguments += '--live' }
if ($RunId) { $arguments += @('--run-id', $RunId) }
& wsl.exe @arguments
if ($LASTEXITCODE -ne 0) { throw 'A2A sanity operation failed; retained state is available through Status.' }
