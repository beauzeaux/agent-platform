param(
    [Parameter(Position=0)][ValidateSet('init-ca','rotate-ca','login','serve','doctor')][string]$Command = 'serve',
    [string]$Config = (Join-Path $PSScriptRoot '..\proxy.example.json'),
    [string]$State = (Join-Path $env:LOCALAPPDATA 'AdoDevProxy'),
    [switch]$SignIn,
    [switch]$Interactive
)
$ErrorActionPreference = 'Stop'
$dotnet = (Get-Command dotnet -ErrorAction SilentlyContinue).Source
if (-not $dotnet) { $dotnet = Join-Path $env:USERPROFILE '.dotnet\dotnet.exe' }
if (-not (Test-Path -LiteralPath $dotnet)) { throw 'Install the .NET 10 SDK first.' }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$project = Join-Path $PSScriptRoot '..\src\AdoDevProxy\AdoDevProxy.csproj'
$arguments = @('run','--project',$project,'--no-restore','--',$Command,'--config',$Config,'--state',$State)
if ($SignIn) { $arguments += '--sign-in' }
if ($Interactive) { $arguments += '--interactive' }
& $dotnet @arguments
exit $LASTEXITCODE
