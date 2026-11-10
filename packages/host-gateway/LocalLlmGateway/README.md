# Local LLM Gateway

A native .NET gateway that lets your own Copilot CLI sessions use your
GitHub Copilot subscription without copying GitHub credentials onto client
machines. This adapts authentication and upstream protocol behavior from
[copilot-api](https://github.com/ericc-ch/copilot-api); see
`THIRD-PARTY-NOTICES`.

The gateway runs on the machine where you authorize GitHub. Only that machine
stores the GitHub credential and originates GitHub inference traffic. Clients
use a separate shared gateway key with stock Copilot CLI's BYOK configuration.

```text
Copilot CLI --(gateway key)--> local gateway --(host-only credential)--> GitHub Copilot
```

Native Responses JSON, SSE, and tool-result continuation have been verified with
`gpt-5.6-sol`, including a clean stock Copilot CLI session and gateway restart.
Native Chat Completions JSON, SSE, and tool fields are covered by offline fixture
tests; live Chat Completions inference has not been verified.
Model availability depends on your subscription. Windows execution is verified;
Linux runtime execution has not been verified in this environment.

This is an **unofficial, reverse-engineered subscription integration**, not a
supported GitHub inference API. Service changes may break it; excessive automated
activity may trigger account restrictions. It does not provide extra quota or
permission to bypass subscription policies.

## Build and tests

Install the .NET 10 SDK. Commands from the repository root:

```powershell
dotnet restore LocalLlmGateway.slnx
dotnet build LocalLlmGateway.slnx --no-restore
dotnet test LocalLlmGateway.slnx --no-build
dotnet test tests\LocalLlmGateway.Tests --filter "FullyQualifiedName~AuthenticationTests.ConcurrentTokenRequestsPerformOneExchange"
dotnet format LocalLlmGateway.slnx --verify-no-changes
```

On Linux, use the native path separator in project paths.

## Start the gateway on Windows

Run these on the machine that will originate the GitHub traffic:

```powershell
dotnet run --project src\LocalLlmGateway -- login
dotnet run --project src\LocalLlmGateway -- probe
dotnet run --project src\LocalLlmGateway -- probe --model <entitled-model-id>
```

`login` displays a GitHub device authorization URL and code. Complete the browser
flow yourself. It verifies the Copilot token exchange before saving the host
credential. It does not read or export your existing Copilot CLI credentials.

`probe` without `--model` lists the subscription's model IDs. With `--model`, it
makes real inference requests that consume quota, verifying JSON output, SSE
output, a function call, and stateless tool-result continuation using Responses.
It is not a Chat Completions probe. A failed probe
is not silently converted into Chat Completions.

Create a separate gateway key outside the repository, then start the service:

```powershell
$keyFile = Join-Path $env:APPDATA 'local-llm-gateway\gateway-key'
if (-not (Test-Path -LiteralPath $keyFile)) {
    [IO.File]::WriteAllText($keyFile, [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)))
}
dotnet run --project src\LocalLlmGateway -- serve --gateway-key-file $keyFile
```

`serve` verifies the saved host login and model catalog before opening the
listener. It never starts an interactive login. To reauthorize an expired or
revoked host credential, stop the service, run `login`, and restart.

## Start the gateway on Linux

With the .NET 10 SDK installed:

```bash
dotnet run --project src/LocalLlmGateway -- login
dotnet run --project src/LocalLlmGateway -- probe
dotnet run --project src/LocalLlmGateway -- probe --model gpt-5.6-sol
umask 077
key_file="${XDG_CONFIG_HOME:-$HOME/.config}/local-llm-gateway/gateway-key"
if [ ! -e "$key_file" ]; then
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$key_file"
fi
dotnet run --project src/LocalLlmGateway -- serve --gateway-key-file "$key_file"
```

The example model must be present in your own catalog.

## Connect stock Copilot CLI

In another PowerShell terminal, on the same machine:

```powershell
$env:COPILOT_PROVIDER_BASE_URL = 'http://127.0.0.1:4141/v1'
$env:COPILOT_PROVIDER_TYPE = 'openai'
$env:COPILOT_PROVIDER_WIRE_API = 'responses'
$env:COPILOT_PROVIDER_TRANSPORT = 'http'
$env:COPILOT_PROVIDER_API_KEY = [IO.File]::ReadAllText((Join-Path $env:APPDATA 'local-llm-gateway\gateway-key')).Trim()
$env:COPILOT_OFFLINE = 'true'
copilot --model gpt-5.6-sol
```

Or on Linux:

```bash
export COPILOT_PROVIDER_BASE_URL='http://127.0.0.1:4141/v1'
export COPILOT_PROVIDER_TYPE='openai'
export COPILOT_PROVIDER_WIRE_API='responses'
export COPILOT_PROVIDER_TRANSPORT='http'
export COPILOT_PROVIDER_API_KEY="$(cat "${XDG_CONFIG_HOME:-$HOME/.config}/local-llm-gateway/gateway-key")"
export COPILOT_OFFLINE='true'
copilot --model gpt-5.6-sol
```

For a client on another machine, inject **only the gateway key** through your
existing secret mechanism and use the gateway's reachable address. Bind the
gateway appropriately with `--host`; loopback is the default. Private routing
and encrypted transport are your responsibility. The loopback HTTP example is
not suitable for sending secrets across an untrusted network.

Clear any inherited `COPILOT_PROVIDER_BEARER_TOKEN`,
`COPILOT_PROVIDER_API_KEY_COMMAND`, or conflicting provider settings before using
the API-key configuration above. There are no native provider URL/key CLI flags.

For a model advertising only `/chat/completions`, keep the same `/v1` base URL,
OpenAI provider, HTTP transport and credential source, but select
`COPILOT_PROVIDER_WIRE_API=completions` and the exact model ID (for example
`copilot --model gemini-4-argon` when entitled). The CLI's `completions` wire API
means **Chat Completions**, not the legacy `/v1/completions` endpoint. Do not select
`responses` for a chat-only model. This configuration is not live inference proof.

`COPILOT_OFFLINE=true` suppresses direct client GitHub requests; the gateway still
sends inference to GitHub from the host. GitHub MCP, code search, and delegation
require separate client GitHub authentication and are not provided by this
gateway.

## HTTP API

| Endpoint | Authentication | Behavior |
|---|---|---|
| `GET /health` | None | Minimal liveness response |
| `GET /v1/models` | Gateway bearer key | Catalog with known incompatible/disabled models excluded |
| `POST /v1/responses` | Gateway bearer key | Native OpenAI Responses JSON or incremental SSE |
| `POST /v1/chat/completions` | Gateway bearer key | Native OpenAI Chat Completions JSON or incremental SSE |

Model discovery preserves upstream metadata and caches results for five minutes.
It publishes the union of models supporting `/responses` or `/chat/completions`,
excluding disabled, embedding, non-tool-calling and explicitly unsupported entries.
Consumers must inspect each model's unchanged `supported_endpoints`; listing a model
does not imply support for both protocols. Requests for an explicitly unsupported
model/endpoint pairing receive HTTP 400 `unsupported_model_endpoint` before inference.
Absent endpoint metadata retains historical Responses admission only, not inferred
Chat support. Metadata is not a live compatibility guarantee.

Both native request formats and successful response bytes are preserved without
protocol translation, model aliases or fallback. Chat requests require a nonempty
`messages` array. Streaming tool-call deltas and `[DONE]` are relayed unchanged.
Only host-built authorization and compatibility headers are sent to
GitHub. Client credentials/headers, redirects, arbitrary upstream destinations,
token inspection routes, and CORS access are not forwarded or enabled.

By default, four inference requests across both endpoints can run simultaneously. Excess requests
receive 429 with `Retry-After: 1`; there is no hidden queue. Inference is never
automatically replayed. Upstream 401 becomes a host-side 503 rather than a client
gateway-key error; relevant other error statuses and `Retry-After` are retained,
without forwarding upstream error bodies. Broken streams are aborted, not
replaced with successful completion events.

Token exchange is coordinated across concurrent requests and refreshes on demand
before expiry. Transient token-exchange retries are bounded; a still-valid cached
token may be used with an explicit warning. Expired inference tokens are never
used. Request logs contain IDs, routes, model IDs, statuses, and durations, not
credentials, prompts, tool arguments, or generated content.

## Configuration

Defaults are read from `config.json` in the application's user configuration
directory: `%APPDATA%\local-llm-gateway` on Windows, and the .NET user application
data location (normally `$XDG_CONFIG_HOME/local-llm-gateway` or
`~/.config/local-llm-gateway`) on Linux.

Non-secret configuration precedence is **CLI options > environment variables >
JSON file > defaults**. Select a JSON file with `--config` or
`LOCAL_LLM_GATEWAY_CONFIG`. An explicitly selected missing file is an error.
Relative paths resolve against the working directory.

| JSON property | CLI option | Environment variable | Default |
|---|---|---|---|
| `Host` | `--host` | `LOCAL_LLM_GATEWAY_HOST` | `127.0.0.1` |
| `Port` | `--port` | `LOCAL_LLM_GATEWAY_PORT` | `4141` |
| `MaxConcurrentRequests` | `--max-concurrent-requests` | `LOCAL_LLM_GATEWAY_MAX_CONCURRENT_REQUESTS` | `4` |
| `RequestTimeoutSeconds` | `--request-timeout-seconds` | `LOCAL_LLM_GATEWAY_REQUEST_TIMEOUT_SECONDS` | `1800` |
| `MaxRequestBytes` | `--max-request-bytes` | `LOCAL_LLM_GATEWAY_MAX_REQUEST_BYTES` | `30000000` |
| `AccountType` | `--account-type` | `LOCAL_LLM_GATEWAY_ACCOUNT_TYPE` | `individual` |
| `DataDir` | `--data-dir` | `LOCAL_LLM_GATEWAY_DATA_DIR` | Application user configuration directory |
| `GatewayKeyFile` | `--gateway-key-file` | `LOCAL_LLM_GATEWAY_KEY_FILE` | Unset |

For example, `--account-type business` selects the business Copilot routing.
Options are available before or after the subcommand.

The relay key loader accepts **either** `LOCAL_LLM_GATEWAY_KEY` or the
selected key file, not both. Keys must contain at least 32 non-whitespace
characters. Never substitute your GitHub token for a gateway key or pass secrets
as CLI arguments.

### Credential storage

The long-lived GitHub credential is stored as plaintext `credentials.json` in
`DataDir`; short-lived inference tokens remain in memory. Credential writes are
atomic. New credential directories/files use owner-only modes on Unix; on
Windows, storage relies on the user's profile directory access controls.
There is no DPAPI or credential-vault dependency. Keep this directory private,
outside source control, and on the gateway host.

The gateway key must be distinct from the GitHub credential. Key rotation requires
restarting the gateway with the new key and updating clients.

## Opt-in live Copilot CLI smoke test

With the gateway running and PowerShell 7 plus `copilot` installed:

```powershell
.\scripts\Test-Copilot.ps1 -Model gpt-5.6-sol -GatewayKeyFile $keyFile
```

The script creates an isolated Copilot home and workspace, removes inherited
GitHub/provider credentials from the child environment, enables offline mode,
disables built-in MCP servers and custom instructions, and permits only the file
view tool. It requires successful tool execution and the final assistant response
to include a random marker read from that file. It restores the environment and
removes its temporary workspace afterward. This test spends real inference quota;
it is not part of routine `dotnet test`.
