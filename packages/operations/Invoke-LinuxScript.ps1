[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [ValidateSet('10.82.0.2', '10.82.0.3', '10.82.0.4')][string]$HostAddress = '10.82.0.2'
)
$ErrorActionPreference = 'Stop'
$scriptText = (Get-Content -Raw -LiteralPath $ScriptPath).TrimStart([char]0xfeff).Replace("`r`n", "`n")
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = 'wsl.exe'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
$start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$start.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
if ($HostAddress -eq '10.82.0.2') {
    $start.Arguments = '--distribution Ubuntu-26.04 --user root --exec /usr/bin/bash -s'
} else {
    $start.Arguments = "--distribution Ubuntu-26.04 --exec /usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 $HostAddress sudo -n /usr/bin/bash -s"
}
$process = [Diagnostics.Process]::new()
$process.StartInfo = $start
try {
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write($scriptText)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $output = $stdout.GetAwaiter().GetResult()
    $errors = $stderr.GetAwaiter().GetResult()
    if ($output) { Write-Output $output.TrimEnd() }
    if ($errors) { Write-Output $errors.TrimEnd() }
    if ($process.ExitCode -ne 0) {
        throw "Linux script on $HostAddress failed with exit $($process.ExitCode)."
    }
} finally {
    $process.Dispose()
}
