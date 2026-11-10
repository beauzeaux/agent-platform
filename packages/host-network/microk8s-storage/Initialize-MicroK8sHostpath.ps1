#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Attach', 'Verify', 'RemoveTask')]
    [string]$Action = 'Setup',
    [string]$Distribution = 'Ubuntu-26.04',
    [string]$VhdPath = 'Q:\MicroK8sStorage\hostpath.vhdx'
)

$ErrorActionPreference = 'Stop'
$sizeBytes = 750GB
$reserveBytes = 600GB
$installDirectory = Join-Path $env:ProgramData 'MicroK8sStorage'
$statePath = Join-Path $installDirectory 'hostpath.json'
$installedScript = Join-Path $installDirectory 'Initialize-MicroK8sHostpath.ps1'
$installedHelper = Join-Path $installDirectory 'microk8s-hostpath-disk.sh'
$taskName = 'MicroK8s-Hostpath-Disk'

function Remove-AttachmentTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Scheduled task still exists: $taskName"
    }
}

if ($Action -eq 'RemoveTask') {
    Remove-AttachmentTask
    if ((Test-Path -LiteralPath $installedScript) -and
        [IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($installedScript)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $installedScript -Force
    }
    Write-Host 'Recurring attachment task removed. Disk attachments and MicroK8s were not changed.'
    return
}

function Invoke-Wsl {
    param([string[]]$LinuxArguments)
    $result = & wsl.exe --distribution $Distribution --user root --exec @LinuxArguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed (exit $LASTEXITCODE): $($LinuxArguments -join ' ')"
    }
    return $result
}

function Get-DiskInventory {
    $raw = Invoke-Wsl -LinuxArguments @('lsblk', '--json', '--bytes', '--nodeps', '--output', 'PATH,SIZE,TYPE')
    return @((($raw -join "`n") | ConvertFrom-Json).blockdevices)
}

function Test-WslPrerequisites {
    $init = Invoke-Wsl -LinuxArguments @('ps', '-p', '1', '-o', 'comm=')
    if (($init -join '').Trim() -ne 'systemd') {
        throw 'The WSL distribution must run systemd as PID 1.'
    }
    Invoke-Wsl -LinuxArguments @('test', '-x', '/usr/sbin/blkid')
    Invoke-Wsl -LinuxArguments @('test', '-x', '/usr/bin/nsenter')
    Invoke-Wsl -LinuxArguments @('which', 'mkfs.ext4', 'wipefs', 'blockdev', 'python3')
}

function Find-Filesystem {
    $result = & wsl.exe --distribution $Distribution --user root --exec /usr/sbin/blkid -U $script:state.FilesystemUuid
    switch ($LASTEXITCODE) {
        0 { return ($result -join '').Trim() }
        2 { return $null }
        default { throw "Filesystem lookup failed (exit $LASTEXITCODE)." }
    }
}

function Install-SetupScripts {
    $helperSource = Join-Path $PSScriptRoot 'microk8s-hostpath-disk.sh'
    if (-not (Test-Path -LiteralPath $helperSource)) {
        throw "Missing companion helper: $helperSource"
    }
    if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($installedScript)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $installedScript -Force
    }
    $helperText = [IO.File]::ReadAllText($helperSource).TrimStart([char]0xFEFF).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($installedHelper, $helperText, [Text.UTF8Encoding]::new($false))
}

function Invoke-Helper {
    param([string]$Mode, [string]$Device = '-')
    $helperText = [IO.File]::ReadAllText($installedHelper).TrimStart([char]0xFEFF)
    $stdinScript = $helperText.Replace("`r", '')
    $arguments = @(
        '--distribution', $Distribution, '--user', 'root', '--exec', '/usr/bin/nsenter',
        '--target', '1', '--mount', '--', '/usr/bin/bash', '-s', '--',
        $Mode, $Device, [string]$state.FilesystemUuid, [string]$state.SizeBytes
    )
    if (@($arguments | Where-Object { $_.Contains('"') -or $_.EndsWith('\') }).Count) {
        throw 'Unsupported quote or trailing backslash in a WSL argument.'
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo.FileName = Join-Path $env:SystemRoot 'System32\wsl.exe'
    $process.StartInfo.Arguments = ($arguments | ForEach-Object {
        if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ }
    }) -join ' '
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $previousInputEncoding = [Console]::InputEncoding
    try {
        [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($stdinScript)
        $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $process.StandardInput.Close()
        $process.WaitForExit()
        $output = $stdout.GetAwaiter().GetResult()
        $errorOutput = $stderr.GetAwaiter().GetResult()
        $helperExitCode = $process.ExitCode
        if ($output) {
            Write-Output $output.TrimEnd([char[]]"`r`n")
        }
        if ($errorOutput) {
            [Console]::Error.Write($errorOutput)
        }
    } finally {
        $process.Dispose()
        [Console]::InputEncoding = $previousInputEncoding
    }
    if ($helperExitCode -ne 0) {
        throw "Linux hostpath $Mode failed (exit $helperExitCode)."
    }
}

if ($Action -eq 'Setup') {
    Remove-AttachmentTask
    if (-not (Test-Path -LiteralPath $statePath)) {
        if (Test-Path -LiteralPath $VhdPath) {
            throw "Refusing to adopt or format an existing VHDX without setup state: $VhdPath"
        }
        $helperSource = Join-Path $PSScriptRoot 'microk8s-hostpath-disk.sh'
        if (-not (Test-Path -LiteralPath $helperSource)) {
            throw "Missing companion helper: $helperSource"
        }
        $distros = ((& wsl.exe --list --quiet) -replace "`0", '') |
            ForEach-Object { $_.Trim() } | Where-Object { $_ }
        if ($Distribution -notin $distros) {
            throw "WSL distribution '$Distribution' not found. Use -Distribution with its exact name."
        }
        Test-WslPrerequisites
        $volume = Get-Volume -FilePath ([IO.Path]::GetPathRoot($VhdPath))
        if ($volume.SizeRemaining -lt ($sizeBytes + $reserveBytes + 1GB)) {
            throw 'Need at least 1,351 GiB free: 750 GiB VHDX, 600 GiB reserved headroom, and metadata margin.'
        }
        if (-not (Get-Command New-VHD -ErrorAction SilentlyContinue)) {
            throw 'The Hyper-V PowerShell New-VHD command is required. No disks have been changed.'
        }
        New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
            $identity = [Security.Principal.SecurityIdentifier]::new($sid)
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
            )
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $installDirectory -AclObject $acl
        New-Item -ItemType Directory -Path (Split-Path -Parent $VhdPath) -Force | Out-Null
        Write-Host "Creating a fixed 750-GiB VHDX at $VhdPath. This may take several minutes."
        New-VHD -Path $VhdPath -SizeBytes $sizeBytes -Fixed | Out-Null
        $state = [pscustomobject]@{
            Distribution = $Distribution
            VhdPath = $VhdPath
            SizeBytes = $sizeBytes
            FilesystemUuid = [guid]::NewGuid().ToString()
            WindowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        }
        $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    }
} elseif (-not (Test-Path -LiteralPath $statePath)) {
    throw 'No saved setup state. Run -Action Setup first.'
}

$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$Distribution = $state.Distribution
$VhdPath = $state.VhdPath
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -ne $state.WindowsUser) {
    throw "Run as the original Windows user ($($state.WindowsUser)); WSL distributions are per-user."
}
if (-not (Test-Path -LiteralPath $VhdPath)) {
    throw "Expected VHDX is missing: $VhdPath"
}
if ($Action -eq 'Setup') {
    Install-SetupScripts
}
$device = Find-Filesystem
if ($Action -ne 'Verify' -and -not $device) {
    $before = @(Get-DiskInventory | ForEach-Object { $_.path })
    & wsl.exe --mount --vhd $VhdPath --bare
    if ($LASTEXITCODE -ne 0) {
        throw 'VHD attachment failed. No device will be formatted. If already attached but unformatted, detach this exact VHDX and rerun Setup.'
    }
    $device = Find-Filesystem
    if (-not $device) {
        if ($Action -ne 'Setup') {
            throw 'Expected filesystem UUID not found after attachment. Attach mode never formats disks.'
        }
        $candidates = @(Get-DiskInventory | Where-Object {
            $_.type -eq 'disk' -and $_.path -notin $before -and [long]$_.size -eq [long]$state.SizeBytes
        })
        if ($candidates.Count -ne 1) {
            throw 'Could not identify exactly one newly attached 750-GiB disk. Refusing to format anything.'
        }
        $device = $candidates[0].path
    }
}
if ($Action -eq 'Setup') {
    Invoke-Helper -Mode initialize -Device $device
    Write-Host 'No scheduled task created. After restarting WSL, run this script with -Action Attach from Administrator PowerShell.'
} elseif ($Action -eq 'Attach') {
    Invoke-Helper -Mode attach -Device $device
} else {
    Invoke-Helper -Mode verify -Device $device
}
Write-Host "Hostpath disk: $VhdPath; ext4 UUID: $($state.FilesystemUuid)"
Write-Host 'Future Ceph disk has NOT been created. The remaining free space is headroom, not a filesystem quota.'
