$ErrorActionPreference = 'Stop'
$token = (& wsl.exe -d Ubuntu-26.04 --user root --exec /snap/bin/microk8s kubectl `
    -n headlamp create token headlamp-viewer --duration=8h | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $token.StartsWith('eyJ')) { throw 'Read-only viewer token creation failed.' }
Set-Clipboard -Value $token
$token = $null
Write-Output 'Read-only Headlamp login token copied to the clipboard (requested lifetime: 8 hours).'
