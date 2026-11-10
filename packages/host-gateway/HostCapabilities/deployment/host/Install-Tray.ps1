[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$Config,
    [string]$TaskName = 'HostCapabilities Tray',
    [switch]$Remove,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

function Get-AbsolutePath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Label
    )

    if (-not [IO.Path]::IsPathFullyQualified($Path)) {
        throw "$Label must be an absolute path."
    }

    return [IO.Path]::GetFullPath($Path)
}

function Test-TaskAction {
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$ExpectedExecutable
    )

    $actions = @($Task.Actions)
    if ($actions.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$actions[0].Execute)) {
        return $false
    }

    try {
        $actual = [IO.Path]::GetFullPath([string]$actions[0].Execute)
    }
    catch {
        return $false
    }

    return [string]::Equals(
        $actual,
        $ExpectedExecutable,
        [StringComparison]::OrdinalIgnoreCase)
}

$executablePath = Get-AbsolutePath -Path $Executable -Label 'Executable'
$configPath = Get-AbsolutePath -Path $Config -Label 'Config'
$expectedArguments = "--config `"$configPath`""

if (-not $Remove) {
    if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
        throw 'Executable must exist for install.'
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw 'Config must exist for install.'
    }
}

$tasks = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
if ($tasks.Count -gt 1) {
    throw "More than one task named '$TaskName' exists; refusing to guess which task to change."
}
$existingTask = if ($tasks.Count -eq 1) { $tasks[0] } else { $null }
$taskPath = if ($existingTask) { [string]$existingTask.TaskPath } else { '\' }
$taskIsRunning = $existingTask -and [string]$existingTask.State -eq 'Running'

if ($existingTask -and -not (Test-TaskAction -Task $existingTask -ExpectedExecutable $executablePath)) {
    throw "Existing task '$TaskName' does not point only to the requested tray executable; no changes were made."
}

if ($Remove) {
    if (-not $existingTask) {
        Write-Output "Dry run or no-op: task '$TaskName' is not registered."
        return
    }

    if (-not $Apply) {
        Write-Output "Dry run: would remove task '$TaskName' because its only action points to the requested executable."
        return
    }

    $null = Unregister-ScheduledTask -InputObject $existingTask -Confirm:$false
    Write-Output "Removed task '$TaskName'."
    return
}

$existingArguments = if ($existingTask) { [string](@($existingTask.Actions)[0].Arguments) } else { $null }
if ($taskIsRunning -and -not [string]::Equals(
        $existingArguments,
        $expectedArguments,
        [StringComparison]::Ordinal)) {
    throw "Existing owned task '$TaskName' is running with different arguments. Exit the tray from its menu, then reapply; the tray was not stopped or replaced."
}

if ($taskIsRunning) {
    Write-Output "Task '$TaskName' is already running with the requested executable and config; no changes are needed."
    return
}

$operation = if ($existingTask) { 'replace' } else { 'register' }
if (-not $Apply) {
    Write-Output "Dry run: would $operation interactive at-logon task '$TaskName' for the current signed-in user."
    Write-Output "Dry run: task would run '$executablePath' directly with --config and the absolute config path."
    Write-Output 'Dry run: task would use unlimited execution time, restart-on-failure, battery-tolerant settings, and a single-instance policy.'
    Write-Output "Dry run: after $operation, the task would be started."
    return
}

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $executablePath -Argument $expectedArguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings

$null = Register-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -InputObject $definition -Force
Start-ScheduledTask -TaskName $TaskName -TaskPath $taskPath
Write-Output "Registered and started task '$TaskName' for the current signed-in user."
