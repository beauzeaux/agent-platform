[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Install','Remove')][string]$Action,
    [string]$Dotnet = (Join-Path $env:USERPROFILE '.dotnet\dotnet.exe'),
    [string]$App = (Join-Path $PSScriptRoot '..\src\AdoDevProxy\bin\Release\net10.0-windows\AdoDevProxy.dll'),
    [string]$Config = (Join-Path $PSScriptRoot '..\proxy.example.json')
)
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$path = Join-Path $startup 'AdoDevProxy.lnk'
if ($Action -eq 'Remove') {
    if (Test-Path -LiteralPath $path) {
        $shell = New-Object -ComObject WScript.Shell
        if ($shell.CreateShortcut($path).Description -ne 'AdoDevProxy: interactive-user WAM dev-plane proxy') {
            throw 'Refusing to remove an unowned logon shortcut.'
        }
    }
    if ($PSCmdlet.ShouldProcess($path, 'Remove owned logon shortcut')) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    return
}
foreach ($file in @($Dotnet,$App,$Config)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing startup prerequisite: $file" }
    if ($file.Contains('"')) { throw 'Invalid startup path.' }
}
if (Test-Path -LiteralPath $path) { throw 'Shortcut exists; inspect/remove it explicitly first.' }
if ($PSCmdlet.ShouldProcess($path, 'Install opt-in current-user logon shortcut')) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = (Resolve-Path -LiteralPath $Dotnet).Path
    $shortcut.Arguments = '"' + (Resolve-Path -LiteralPath $App).Path + '" serve --config "' + (Resolve-Path -LiteralPath $Config).Path + '"'
    $shortcut.WorkingDirectory = Split-Path -Parent (Resolve-Path -LiteralPath $Config).Path
    $shortcut.Description = 'AdoDevProxy: interactive-user WAM dev-plane proxy'
    $shortcut.Save()
}
