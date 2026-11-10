[CmdletBinding()]
param([string]$Distribution = 'Ubuntu-26.04')

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'Initialize-MicroK8sHostpath.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) {
    throw ($errors | Out-String)
}
foreach ($name in @('Invoke-Wsl', 'Test-WslPrerequisites', 'Get-DiskInventory', 'Find-Filesystem', 'Invoke-Helper', 'Remove-AttachmentTask')) {
    $definition = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if (-not $definition) {
        throw "Function not found: $name"
    }
    . ([scriptblock]::Create($definition.Extent.Text))
}

Test-WslPrerequisites
$disks = @(Get-DiskInventory)
if (-not ($disks | Where-Object { $_.path -eq '/dev/sdd' -and $_.type -eq 'disk' })) {
    throw 'Disk inventory did not survive native argument passing.'
}
Write-Output 'PASS: actual preflight and JSON disk inventory'

$state = [pscustomobject]@{
    FilesystemUuid = '00000000-0000-0000-0000-000000000000'
    SizeBytes = 805306368000
}
if ($null -ne (Find-Filesystem)) {
    throw 'An absent filesystem must return null without a native command error.'
}
Write-Output 'PASS: absolute blkid path and normal absent-filesystem handling'
$state.FilesystemUuid = ((Invoke-Wsl -LinuxArguments @('findmnt', '-n', '-o', 'UUID', '/')) -join '').Trim()
$rootDevice = Find-Filesystem
if (-not $rootDevice -or $rootDevice -notin @($disks | ForEach-Object { $_.path })) {
    throw 'Existing-filesystem lookup did not return a known block device.'
}
$state.FilesystemUuid = '00000000-0000-0000-0000-000000000000'
Write-Output 'PASS: existing-filesystem lookup (read-only)'
$installedHelper = Join-Path $PSScriptRoot 'test-microk8s-stdin.sh'
$originalEncoding = $OutputEncoding
$originalInputEncoding = [Console]::InputEncoding
try {
    $OutputEncoding = [Text.UTF8Encoding]::new($true)
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($true)
    $output = Invoke-Helper -Mode stdin-probe -Device '-'
    if ($OutputEncoding.GetPreamble().Length -ne 3) {
        throw 'The helper failed to restore the caller output encoding.'
    }
    if ([Console]::InputEncoding.GetPreamble().Length -ne 3) {
        throw 'The helper failed to restore the console input encoding.'
    }
} finally {
    $OutputEncoding = $originalEncoding
    [Console]::InputEncoding = $originalInputEncoding
}
if ($output -notcontains 'STDIN_ARGUMENTS_OK') {
    throw 'Multiline Bash script/arguments were not preserved.'
}
Write-Output 'PASS: multiline stdin, arguments, CRLF/BOM handling, and systemd mount namespace'

$installedHelper = Join-Path $PSScriptRoot 'microk8s-hostpath-disk.sh'
$rejected = $false
try {
    Invoke-Helper -Mode transport-probe -Device '-'
} catch {
    if ($_.Exception.Message -notmatch 'Linux hostpath transport-probe failed \(exit 1\)') {
        throw
    }
    $rejected = $true
}
if (-not $rejected) {
    throw 'The real Linux helper did not reject an unknown mode.'
}
Write-Output 'PASS: real Linux helper receives mode/UUID/size and propagates failure'
& wsl.exe --distribution $Distribution --user root --exec /usr/bin/nsenter --target 1 --mount -- /usr/bin/mountpoint -q /mnt/microk8s-hostpath
if ($LASTEXITCODE -eq 0) {
    $state.FilesystemUuid = ((Invoke-Wsl -LinuxArguments @(
        '/usr/bin/nsenter', '--target', '1', '--mount', '--',
        '/usr/bin/findmnt', '-rn', '-M', '/mnt/microk8s-hostpath', '-o', 'UUID'
    )) -join '').Trim()
    $device = Find-Filesystem
    Invoke-Helper -Mode verify -Device $device
    Write-Output 'PASS: real mounted hostpath disk, UUID, ext4 filesystem, and MicroK8s services'
}
$taskName = 'test-task'
$script:mockTaskExists = $true
$script:mockDeleteCount = 0
function Get-ScheduledTask {
    param($TaskName, $ErrorAction)
    if ($script:mockTaskExists) {
        [pscustomobject]@{ TaskName = $TaskName }
    }
}
function Unregister-ScheduledTask {
    param($TaskName, [switch]$Confirm)
    $script:mockTaskExists = $false
    $script:mockDeleteCount++
}
Remove-AttachmentTask
Remove-AttachmentTask
if ($script:mockTaskExists -or $script:mockDeleteCount -ne 1) {
    throw 'Task removal failed or was not idempotent.'
}
Write-Output 'PASS: task removal and already-removed task handling (mocked scheduler)'
Write-Output 'No VHDs, mounts, services, or scheduled tasks were changed.'
