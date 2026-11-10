[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$names = @('kagent.internal', 'pilot.internal', 'grafana.internal', 'prometheus.internal',
    'tempo.internal', 'loki.internal', 'swarm.internal')
$begin = '# BEGIN HostCapabilities stable services'
$end = '# END HostCapabilities stable services'
$block = $begin + "`r`n127.0.0.1 " + ($names -join ' ') + "`r`n" + $end
if (-not $Apply) { Write-Output $block; return }
$admin = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated PowerShell with -Apply, or add the printed block yourself.'
}
$file = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$original = [IO.File]::ReadAllText($file)
$owned = '(?ms)^' + [regex]::Escape($begin) + '\r?\n.*?^' + [regex]::Escape($end) + '\r?\n?'
$outside = [regex]::Replace($original, $owned, '')
foreach ($line in ($outside -split '\r?\n')) {
    $tokens = (($line -split '#', 2)[0] -split '\s+') | Where-Object { $_ }
    if ($tokens.Count -gt 1 -and @($tokens | Select-Object -Skip 1 | Where-Object { $_ -in $names }).Count) {
        throw 'A selected hostname already has an unowned mapping; resolve it explicitly before applying.'
    }
}
$backup = Join-Path $env:LOCALAPPDATA ('HostCapabilities\rollback\hosts-' + (Get-Date -Format yyyyMMddTHHmmss))
$null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup)
Copy-Item -LiteralPath $file -Destination $backup
[IO.File]::WriteAllText($file, $outside.TrimEnd() + "`r`n`r`n" + $block + "`r`n",
    [Text.UTF8Encoding]::new($false))
Clear-DnsClientCache
Write-Output 'Installed only the owned stable-service host mappings. Existing mappings preserved.'
