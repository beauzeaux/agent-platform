[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Install','Remove')][string]$Action,
    [Parameter(Mandatory)][string]$LocalAddress,
    [Parameter(Mandatory)][string[]]$RemoteAddress,
    [ValidateRange(1,65535)][int]$Port = 5480
)
$ErrorActionPreference = 'Stop'
$parsedAddress = $null
if (-not [Net.IPAddress]::TryParse($LocalAddress, [ref]$parsedAddress) -or $LocalAddress -in @('0.0.0.0','::')) {
    throw 'Use an explicit VM-facing listener IP.'
}
foreach ($remote in $RemoteAddress) {
    $parts = $remote.Split('/')
    $parsedRemote = $null
    if ($parts.Length -gt 2 -or -not [Net.IPAddress]::TryParse($parts[0], [ref]$parsedRemote)) {
        throw 'Remote clients must be explicit IPs or CIDRs, never Any/LocalSubnet.'
    }
    $bits = $parsedRemote.GetAddressBytes().Length * 8
    $prefix = $bits
    if (($parts.Length -eq 2 -and -not [int]::TryParse($parts[1], [ref]$prefix)) -or $prefix -lt 1 -or $prefix -gt $bits) {
        throw 'Rejecting an invalid or unrestricted client CIDR.'
    }
}
$name = "AdoDevProxy-Data-$Port"
$existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($Action -eq 'Install') {
    if ($existing) { throw 'Owned rule already exists; inspect/remove it explicitly before changing it.' }
    if ($PSCmdlet.ShouldProcess("$LocalAddress`:$Port", 'Allow selected VM clients')) {
        New-NetFirewallRule -Name $name -DisplayName $name -Group 'AdoDevProxy' -Direction Inbound `
            -Action Allow -Protocol TCP -LocalAddress $LocalAddress -RemoteAddress $RemoteAddress -LocalPort $Port -Profile Any
    }
} elseif ($existing) {
    if ($existing.Group -ne 'AdoDevProxy') { throw 'Refusing to remove an unowned firewall rule.' }
    if ($PSCmdlet.ShouldProcess($name, 'Remove owned firewall rule')) { $existing | Remove-NetFirewallRule }
}
