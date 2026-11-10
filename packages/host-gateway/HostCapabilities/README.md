# HostCapabilities

One **signed-in Windows user's** .NET 10 lifecycle, not LocalSystem. It composes
the sibling `LocalLlmGateway` and `ado-dev-proxy` projects and the `native-ado`
library by `ProjectReference`. It supervises one transitional Python Agency
broker, which retains its one shared Agency stdio child and existing nested
tool/plugin/hash/hook policy. No legacy service/task is changed by a build.

## AgencyCoder in Kagent

[AgencyCoder](src/AgencyCoder/README.md) is the .NET loopback coding adapter,
not an A2A replacement for the native Go harness.
[External deployment](deployment/agency-coder/README.md) provides portable
publish/container commands, parameterized native YAML and guarded migration
checks using an existing namespace-scoped shared Kagent installation.

## Build and offline verification

PowerShell 7, from `Q:\src\tools\HostCapabilities`:

```powershell
$dotnet = 'C:\Users\nibeauli\.dotnet\dotnet.exe'
$artifacts = Join-Path $PWD '.artifacts\build'
& $dotnet restore HostCapabilities.slnx --configfile NuGet.Config -p:UseArtifactsOutput=true "-p:ArtifactsPath=$artifacts"
$env:HOST_CAPABILITIES_TEST_PYTHON = (& python -c 'import sys; print(sys.executable)')
& $dotnet test HostCapabilities.slnx --no-restore --artifacts-path $artifacts
& $dotnet format HostCapabilities.slnx --no-restore --include src tests --verify-no-changes
& $dotnet publish src\HostCapabilities -c Release --no-restore --artifacts-path $artifacts -o .artifacts\publish-native-admission
.\tests\Test-PublishedLifecycle.ps1 -Executable .artifacts\publish-native-admission\HostCapabilities.exe -DotnetRoot C:\Users\nibeauli\.dotnet
```

SDK 10.0.403 is selected by `global.json`. Restore uses the configured 1ES feed;
never add nuget.org. The publish directory includes both reused applications,
native-ADO/gRPC, OTel, IdentityModel, WAM/native dependencies and third-party
notices. Install .NET/ASP.NET Core 10 x64 on the signed-in Windows host.
This machine's system installation is .NET 8. Launch the adjacent published DLL
with the .NET 10 installation's `dotnet.exe`, as shown in the canary commands.
No machine/user runtime environment variable is changed, and unrelated .NET 8
apps retain their existing runtime selection.
The isolated artifacts path is important: the live legacy proxy may lock its
source checkout's Release binaries; builds must not overwrite those binaries.
Stage each update in a separate publish directory; never overwrite the original
running canary/runtime copy. The parent owns draining and switching processes.
Published operation does not require either sibling source directory. The
explicitly configured Agency executable, Python broker directory (including
`prepare_runtime.py`), profiles and empty global configuration remain required.
`native-ado` and `deployment` have separate owners; do not format/edit those trees.

## System tray setup

Publish the tray project for Windows and retain its output directory. The
published output is expected to contain `HostCapabilities.Tray.exe`, which is
a WinExe and accepts `--config <absolute JSON path>`. Keep the JSON configuration
outside source control with the same protections and validated absolute paths
used for the console host.

From the repository root, preview the current-user at-logon task installation
before applying it:

```powershell
$tray = (Resolve-Path '.artifacts\publish-tray\HostCapabilities.Tray.exe').Path
$config = (Resolve-Path "$env:LOCALAPPDATA\HostCapabilities\config.local.json").Path
.\packages\host-gateway\HostCapabilities\deployment\host\Install-Tray.ps1 -Executable $tray -Config $config
.\packages\host-gateway\HostCapabilities\deployment\host\Install-Tray.ps1 -Executable $tray -Config $config -Apply
```

The apply step registers one interactive, battery-tolerant task for the
currently signed-in Windows user and starts it immediately. Subsequent runs
update only a task whose executable action exactly matches the requested tray
executable. The tray icon and menu show the tray host's lifecycle/readiness
status and its supported actions. Because the executable is a WinExe, startup
does not leave a console window. Task installation itself does not change the
host configuration or login state. The task is not machine-wide and runs only
when that user is signed in. Reapplying the same executable and configuration
while the task is already running is a no-op. To change the configuration of a
running tray, choose **Exit** from the tray menu, then run the apply command
again; the installer never abruptly terminates the tray or a legacy process.

Preview removal, then apply it with the same executable and config:

```powershell
.\packages\host-gateway\HostCapabilities\deployment\host\Install-Tray.ps1 -Executable $tray -Config $config -Remove
.\packages\host-gateway\HostCapabilities\deployment\host\Install-Tray.ps1 -Executable $tray -Config $config -Remove -Apply
```

Removal changes only the matching tray task. It does not stop or alter any
legacy task or service. The existing secure configuration, protected state,
TLS/identity authorization, and explicit `LocalLlmGateway`/`AdoDevProxy` login
boundaries remain unchanged.

## Native model protocols

The existing LLM module authenticates and observes both POST `/v1/responses` and
POST `/v1/chat/completions`, sharing one inference capacity limit and the same
host-owned Copilot credentials. Each format is relayed natively; there is no
translation, model substitution or retry of accepted inference.

GET `/v1/models` now includes models routable through either protocol and preserves
upstream `supported_endpoints`. Consumers must choose an advertised endpoint;
chat-only models are rejected on Responses before inference. Missing endpoint
metadata retains legacy Responses behavior without implying Chat support.
Chat SSE telemetry requires a finish reason plus `[DONE]`; errors and truncated
streams are not reported as successful. Only bounded protocol metadata is observed.

For stock Copilot CLI, select `COPILOT_PROVIDER_WIRE_API=completions` for Chat
Completions, retaining OpenAI type, HTTP transport and the `/v1` base URL.
Responses clients keep `responses`. Native actor clients also need their own
Chat-format configuration support. Parent-owned TLS frontends/bridges must admit
POST `/v1/chat/completions` before remote access is possible; a Windows source
build alone does not update those allowlists or the running immutable payload.
No live Chat/tool inference is implied by the offline tests.

## Configuration and canary (operator only)

Copy `host.example.json` **outside source control**, replace all placeholders
with absolute paths and protect the directory for the signed-in user. Secrets
are referenced by existing host-owned files, never passed on the CLI or embedded
in JSON. No implicit environment/working-directory configuration is loaded.
Unknown properties, null non-nullable settings, unsafe paths, port collisions,
non-TLS OTLP origins and incomplete JWT/native-ADO controls fail validation.

Before a canary, provision a distinct gateway key file (32+ random characters),
retain the original `%APPDATA%\local-llm-gateway` GitHub credential store, and
select the existing account type. The key must not equal the GitHub credential.
No login, credential copying, CA creation/rotation or trust installation is
performed by `serve` or `doctor`. Explicit recovery uses the original
`LocalLlmGateway login` and `AdoDevProxy login` commands on the Windows host.

Use an operator-owned copy of the ADO policy JSON, changing only `port` to 5482
and `controlPort` to 5483. Canary `ado.mode=delegate` transparently relays to the
live loopback proxy 5480/control 5481 and preserves its CA, policy and no-write-
replay behavior. It never opens the original ADO state/lock. Its telemetry is
**opaque transport**, not fabricated per-request HTTP success. Embedded mode
uses the original implementation and explicit state lock/certificate; enable
it only after the parent has stopped the previous owner, or with separately
provisioned canary state/CA. Never initialize/delete/rotate the live CA.

Default canary listeners: models 4142, ADO 5482/5483 (policy JSON), MCP 15443,
internal broker loopback 15444, common loopback control 14540. Keep the existing
host/WSL TLS transport; the compatibility listeners themselves are HTTP and
must not be exposed as anonymous public endpoints. Internal broker state must
be separate from its legacy `state` directory; its actor registry/bridge key
can remain in the existing protected configuration file.

```powershell
$exe = 'Q:\src\tools\HostCapabilities\.artifacts\publish-native-admission\HostCapabilities.exe'
$runtime = 'C:\Users\nibeauli\.dotnet'
$cfg = "$env:LOCALAPPDATA\HostCapabilities\config.local.json"
& "$runtime\dotnet.exe" ([IO.Path]::ChangeExtension($exe, '.dll')) doctor --config $cfg
Invoke-RestMethod http://127.0.0.1:14540/health/live
Invoke-RestMethod http://127.0.0.1:14540/health/llm/ready
Invoke-RestMethod http://127.0.0.1:14540/health/ado/ready
Invoke-RestMethod http://127.0.0.1:14540/health/agency/ready
```

The parent owns canary/deployment/cutover. Use the tray setup above for the
current signed-in user's supported tray registration; do not repoint legacy
tasks or services to the host.

## Readiness, protocol and telemetry contracts

`/health/live` means the control process responds. `/health/ready` requires all
**enabled** capabilities; `/health/{llm|ado|agency|native-ado}/ready` is independent.
Compatibility listeners also expose `/health/live` and `/health/ready`.
Model readiness checks cached catalog/auth, not paid inference. Agency readiness
checks the owned broker PID/policy and an already-started gateway's survival;
the stdio gateway/authentication is lazy until MCP initialization, not a proof of
tool entitlement. A failed module retries startup after 30 seconds without
stopping others. Streams retain their semaphore until completion/cancellation.
No inference, MCP write or Git write is automatically replayed.

OTel uses manual `ActivitySource`/`Meter` instrumentation, **not generic URL/header
capture**, so payloads, prompts, headers, queries and private errors are never
exported. W3C `traceparent` is preserved to the broker; the uninstrumented Agency
binary is not claimed to emit child spans. Bounded SSE inspection preserves every
response byte, records missing/failed/incomplete terminals and distinguishes
HTTP status from protocol outcome. JSON-RPC `error` and MCP `isError` under HTTP
200 are failures. Large uninspectable JSON is `unobserved`, never assumed success.
Approved outer gateway and nested tool calls have separate counters/spans.
Actor IDs are trace-only, never metric dimensions. Session gauge/open/close/
expiry counts are broker-authoritative snapshots refreshed every three seconds.
Idle sessions expire after ten minutes, excluding in-flight calls.

Configure `telemetry.otlpEndpoint=https://localhost:14318/` plus
`caCertificateFile`, `clientCertificateFile` (PFX) and optional
`clientCertificatePasswordFile`. The parent owns the Collector/WSL forward.
Only that exporter uses custom-root trust and mTLS; DNS, validity and explicit
server-auth EKU are always checked. `trustMode=online` remains the default:
unknown/offline revocation fails closed. The explicit `pinned-private` mode
additionally requires `serverCertificateFile`, an operator-owned public **leaf**
certificate. Its exact SHA-256 pin replaces CRL-based revocation only for this
private exporter, not public trust. Every export opens a fresh TLS connection and
reloads the pin: deleting/replacing the file revokes the old leaf, including for
an already-created exporter. Unknown leaves, wrong issuers, DNS mismatch, expired
certificates and wrong EKUs are denied. Use the parent's renewed 48-hour server/client leaves;
copy only the public `server.crt`, not a private key, into the protected telemetry
directory. No global root changes or accept-all TLS bypass exists. Queues/batches
and five-second exporter deadlines are bounded; Collector outages never trigger
business retries or block request handling. OTel logs/content capture is absent.

## Optional identity and native ADO

Actor JWT admission is implemented for models and MCP/plugins. Enable
`actorJwt.enabled`, pin HTTPS issuer, audience, local public JWKS, space,
profiles and `actorRegistryFile`, then optionally set `allowLegacyKeys=false`.
IdentityModel validates RS256/ES256 signature, lifetime and pinned claims on
every request. The default `claimFormat=substrate` consumes the **actual**
`actor.ate.dev` object (`atespace`, `actorName`, `actorUID`) and
`sub=actor/<atespace>/<name>`; it requires no custom UID/profile/space claims.
The structured claim, subject and configured space must agree. UID/subject
must match the current authoritative broker registry and its profile must be
allowed; token-supplied profile values and caller identity headers cannot
override that registry. Set `actorRegistryFile` to the same broker JSON used by
Agency (configuration validation rejects two different registries).
JWKS replacement and actor/subject/profile revocation are rechecked for model
and MCP requests. Optional `claimFormat=custom` retains the previous explicit
claim names but also requires a matching registry binding/profile. There is
no RAM `/session` lease exchange. Pin the actual native issuer/audience and
structured-claim contract before enabling this route.

Native `FetchSecret` is integrated **in-process, disabled by default**. Configure
its dedicated private HTTP/2 mTLS listener, serving PFX, exact gateway SPIFFE ID,
podidentity issuer roots/intermediates, exact actor/credential-URI/profile grants,
and existing WAM identity aliases/state. `nativeAdo.trustMode=online` remains
the default. For private short-lived podidentity SVIDs without CRLs, explicitly
select `short-lived-spiffe` and `maxLeafLifetimeHours` (1–24). This policy pins
the configured issuer bundle and exact gateway URI SAN and revalidates current
validity, client-auth EKU, non-CA leaf and the lifetime cap on every RPC. It does
not permit public-CA fallback, arbitrary identities or global trust changes.
Tokens are available only to the
authenticated gateway, never to actors. The adapter requires at least eight
minutes remaining lifetime by default (minimum six), performs at most one
credential refresh, and fails closed if insufficient for AGW's five-minute cache.
Native readiness verifies WAM availability/lifetime, not ADO policy parity.
**Current direct AGW-to-Windows native TLS is still blocked:** Schannel rejects
Ed25519-signed ECDSA SVIDs before the custom verifier runs. The host fails native
startup for configured Ed25519/Ed448 issuer bundles, before WAM/token acquisition,
rather than reporting false readiness. The explicit PKIX policy does not remove
this TLS-layer limitation. Keep native ADO disabled for the current podidentity
issuer; L4 forwarding and forwarded identity headers cannot fix it. A separately
reviewed, authenticated standard Linux TLS bridge is a parent-owned follow-up,
not part of this canary or permission to retire the existing proxy.
See `native-ado\README.md` and `PARITY-MATRIX.md` for mandatory live gates:
private routing/revocation, egress compiler policy, stock-client placeholder
headers, 401/cache behavior, redirects, org/feed/path/method policy and no-write-
replay tests. **Do not retire the compatibility proxy.**

Remaining observability gates: token-usage counters are not yet extracted from
Responses; unknown usage is not invented. Embedded ADO reports completed request
outcomes and duration after credential acquisition, not per-phase active spans
or CONNECT trace-context correlation. Delegated ADO exposes transport metrics
only. Agency child spans and end-to-end live tool/inference entitlement are
unverified. These limitations do not change bytes, policy or replay behavior.
