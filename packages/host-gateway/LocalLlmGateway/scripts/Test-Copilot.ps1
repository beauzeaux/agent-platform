#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Model,
    [Parameter(Mandatory)]
    [string] $GatewayKeyFile,
    [uri] $BaseUrl = 'http://127.0.0.1:4141/v1'
)

$ErrorActionPreference = 'Stop'
if ($BaseUrl.Scheme -notin @('http', 'https')) {
    throw 'The gateway URL must use HTTP or HTTPS.'
}
$key = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $GatewayKeyFile)).Trim()
if ($key.Length -lt 32) {
    throw 'Use a separate gateway key of at least 32 characters.'
}
$scratch = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('local-llm-gateway-smoke-' + [guid]::NewGuid().ToString('N'))))
$work = Join-Path $scratch 'work'
$copilotHome = Join-Path $scratch 'copilot-home'
$saved = @{}
$names = @(
    @(Get-ChildItem Env: | Where-Object Name -Like 'COPILOT_*' | Select-Object -ExpandProperty Name)
    'GH_TOKEN', 'GITHUB_TOKEN', 'GH_CONFIG_DIR'
    'COPILOT_HOME', 'COPILOT_OFFLINE', 'COPILOT_PROVIDER_BASE_URL', 'COPILOT_PROVIDER_TYPE'
    'COPILOT_PROVIDER_WIRE_API', 'COPILOT_PROVIDER_TRANSPORT', 'COPILOT_PROVIDER_API_KEY'
) | Select-Object -Unique
$location = Get-Location

try {
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    [IO.Directory]::CreateDirectory($work) | Out-Null
    [IO.Directory]::CreateDirectory($copilotHome) | Out-Null
    $marker = 'GATEWAY_CLI_' + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText((Join-Path $work 'marker.txt'), $marker)
    $env:COPILOT_HOME = $copilotHome
    $env:GH_CONFIG_DIR = Join-Path $scratch 'gh-home'
    $env:COPILOT_OFFLINE = 'true'
    $env:COPILOT_PROVIDER_BASE_URL = $BaseUrl.AbsoluteUri.TrimEnd('/')
    $env:COPILOT_PROVIDER_TYPE = 'openai'
    $env:COPILOT_PROVIDER_WIRE_API = 'responses'
    $env:COPILOT_PROVIDER_TRANSPORT = 'http'
    $env:COPILOT_PROVIDER_API_KEY = $key

    Set-Location -LiteralPath $work
    $arguments = @(
        '--model', $Model, '--prompt', 'Use the view tool to read marker.txt in the current directory. Reply with only its exact contents. Do not access other paths.'
        '--available-tools', 'view', '--allow-tool', 'view'
        '--disable-builtin-mcps', '--no-custom-instructions', '--no-auto-update'
        '--log-level', 'none', '--output-format', 'json', '--stream', 'on'
    )
    $output = @(& copilot @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Copilot CLI exited with code $LASTEXITCODE."
    }
    $events = @(
        foreach ($line in $output) {
            $text = $line.ToString()
            if ($text.StartsWith('{')) {
                ConvertFrom-Json -InputObject $text -AsHashtable
            }
        }
    )
    $reads = @($events | Where-Object {
        $_.type -eq 'tool.execution_start' -and $_.data.toolName -eq 'view'
    })
    $completed = @($events | Where-Object {
        $_.type -eq 'tool.execution_complete' -and $_.data.success -eq $true
    })
    $answers = @($events | Where-Object {
        $_.type -eq 'assistant.message' -and $_.data.content -like "*$marker*"
    })
    if ($reads.Count -lt 1 -or $completed.Count -lt 1 -or $answers.Count -lt 1) {
        throw 'The CLI did not demonstrate a successful file-read tool round trip and marker output.'
    }
    Write-Output 'Passed: clean stock Copilot CLI, no GitHub credentials, live Responses streaming, and file-read tool round trip.'
}
finally {
    Set-Location -LiteralPath $location.Path
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    $key = $null
    if ([IO.Directory]::Exists($scratch)) {
        [IO.Directory]::Delete($scratch, $true)
    }
}
