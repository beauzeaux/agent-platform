[CmdletBinding()]
param([string]$Distribution = 'Ubuntu-26.04')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$uid = & wsl.exe --distribution $Distribution --user root --exec python3 -c "import json;print(json.load(open('/home/nibeauli/kagent-lab/engsys-agency-coder/owned.json'))['uid'])"
if ($LASTEXITCODE -ne 0) { throw 'Owned Agency pilot UID is unavailable.' }
& python "$root\configure.py" --actor-uid $uid.Trim()
if ($LASTEXITCODE -ne 0) { throw 'Host broker state protection failed.' }
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (!(Test-Path $pythonw)) { throw 'Hidden Python host executable is unavailable.' }
$name = 'Kagent Agency Broker'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Arguments -notcontains ('"' + "$root\broker.py" + '"'))) {
    throw 'Refusing to replace an unowned logon task.'
}
if ($existing -and $existing.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 2
}
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + "$root\broker.py" + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Owned continuous loopback Agency broker. Logon trigger only; no polling or console windows.' -Force | Out-Null
Start-ScheduledTask -TaskName $name
for ($i = 0; $i -lt 30; $i++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:15442/health' -TimeoutSec 2
        if ($health.ready) {
            Write-Output "Hidden logon broker is responsive; policy $($health.policy)."
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 1
}
throw 'Host broker did not become responsive.'
