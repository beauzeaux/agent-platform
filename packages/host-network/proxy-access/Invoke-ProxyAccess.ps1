[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Install', 'Remove', 'Test', 'Status', 'RefreshKeys')]
    [string]$Action,
    [string]$Distribution = 'Ubuntu-26.04',
    [switch]$LiveInference
)

$ErrorActionPreference = 'Stop'
$linux = '/mnt/q/tools/kagent-lab/proxy-access'

function Invoke-LinuxPython {
    param([string]$Script, [string[]]$Arguments = @())
    & wsl.exe --distribution $Distribution --user root --exec /usr/bin/python3 "$linux/$Script" @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Proxy access command failed: $Script"
    }
}

switch ($Action) {
    'Install' {
        Invoke-LinuxPython 'manage.py' @('prepare-host')
        Invoke-LinuxPython 'manage.py' @('refresh-keys')
        Invoke-LinuxPython 'build_image.py'
        Invoke-LinuxPython 'manage.py' @('configure')
        Invoke-LinuxPython 'manage.py' @('cache-images')
        Invoke-LinuxPython 'manage.py' @('deploy')
        Invoke-LinuxPython 'egress_overlay.py' @('apply')
    }
    'Remove' {
        Invoke-LinuxPython 'manage.py' @('remove')
    }
    'Test' {
        $arguments = @()
        if ($LiveInference) { $arguments += '--live-inference' }
        Invoke-LinuxPython 'test_actors.py' $arguments
    }
    'RefreshKeys' {
        Invoke-LinuxPython 'manage.py' @('refresh-keys')
        Invoke-LinuxPython 'manage.py' @('configure')
        Invoke-LinuxPython 'manage.py' @('deploy')
    }
    'Status' {
        & wsl.exe --distribution $Distribution --user root --exec /usr/bin/systemctl is-active kagent-proxy-access.service
        if ($LASTEXITCODE -ne 0) { throw 'Host bridge is not active.' }
        & wsl.exe --distribution $Distribution --user root --exec /snap/bin/microk8s kubectl get pods,services --namespace proxy-access -o wide
        if ($LASTEXITCODE -ne 0) { throw 'Cluster proxy status failed.' }
    }
}
