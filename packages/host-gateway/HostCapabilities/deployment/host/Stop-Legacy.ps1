[CmdletBinding()]
param(
    [Parameter(Mandatory)][int]$LlmPid,
    [Parameter(Mandatory)][int]$AdoPid,
    [Parameter(Mandatory)][int]$BrokerPid,
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'HostCapabilities'),
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$all = @(Get-CimInstance Win32_Process)
$expected = @(
    @{Id=$LlmPid; Name='dotnet.exe'; Marker='LocalLlmGateway.dll'},
    @{Id=$AdoPid; Name='dotnet.exe'; Marker='AdoDevProxy.dll'},
    @{Id=$BrokerPid; Name='pythonw.exe'; Marker='Q:\tools\kagent-lab\agency-broker\broker.py'}
)
$roots = @()
foreach ($item in $expected) {
    $process = $all | Where-Object ProcessId -eq $item.Id | Select-Object -First 1
    if (-not $process -or $process.Name -ne $item.Name -or
        $process.CommandLine.IndexOf($item.Marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "Refusing to stop unexpected legacy owner PID $($item.Id)."
    }
    $roots += $process
}
$task = Get-ScheduledTask -TaskName 'Kagent Agency Broker'
if (-not ($task.Actions.Arguments -match [regex]::Escape('Q:\tools\kagent-lab\agency-broker\broker.py'))) {
    throw 'Legacy broker task action differs from the recorded owner.'
}
$descendants = @()
$pending = @($BrokerPid)
$brokerStart = ($roots | Where-Object ProcessId -eq $BrokerPid).CreationDate
while ($pending.Count -gt 0) {
    $children = @($all | Where-Object { $_.ParentProcessId -in $pending })
    $children = @($children | Where-Object { $_.ProcessId -notin $descendants.ProcessId -and $_.CreationDate -ge $brokerStart })
    if ($children.Count -eq 0) { break }
    $descendants += $children
    $pending = @($children.ProcessId)
    if ($descendants.Count -gt 128) { throw 'Unexpected legacy broker process tree size.' }
}
if (-not $Apply) {
    Write-Output "Dry run: verified three exact legacy roots and $($descendants.Count) broker descendants; no state changed."
    return
}
$rollback = Join-Path $StateRoot 'rollback'
$null = New-Item -ItemType Directory -Force -Path $rollback
Export-ScheduledTask -TaskName $task.TaskName | Set-Content -LiteralPath (Join-Path $rollback 'legacy-broker-task.xml') -Encoding utf8NoBOM
$roots | Select-Object ProcessId,Name,ExecutablePath,CreationDate |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $rollback 'legacy-owners.json') -Encoding utf8NoBOM
Stop-ScheduledTask -TaskName $task.TaskName
$null = Disable-ScheduledTask -TaskName $task.TaskName
foreach ($process in @($descendants | Sort-Object CreationDate -Descending) + @($roots | Sort-Object CreationDate -Descending)) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $process.CreationDate -and $current.ExecutablePath -eq $process.ExecutablePath) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    }
}
Write-Output 'Only verified legacy owners and captured broker descendants stopped; rollback metadata retained.'
