# AgencyCoder

AgencyCoder is the generic .NET adapter for the native Kagent coding path. The
Kagent-managed Go host remains responsible for A2A admission, the projected
Session identity, durable task ownership, cancellation, and scheduling. This
process owns the loopback `/tasks` adapter and Copilot SDK execution.
It is the coding layer above the reusable [Agency harness image](../AgencyHarness/README.md).

## Contract

The Go native harness starts `AgencyCoder` inside the session workspace, waits
for `GET /readyz`, submits structured work to `POST /tasks`, polls
`GET /tasks/{id}`, and maps cancellation to `POST /tasks/{id}/cancel`. An accepted
task ID and request digest are durable. Restarted `accepted` or `working` tasks
become `interrupted`; they are reported but never replayed.

Pipeline stages are named in `HarnessOrchestrator`: validate/admit, prepare
workspace/repository, resolve hash-verified host plugins, start the Copilot
runtime, execute one task, persist an archive manifest, and clean up. Kagent owns
agent/session configuration; the adapter accepts only the supported subset of
compiled native configuration.

Required environment:

- `AGENT_NATIVE_CONFIG=true`
- `AGENT_COMPILED_CONFIG_PATH` (Kagent compiled configuration)
- `AGENT_DATA` (durable, private data root)
- `AGENT_SESSION_ID` and optionally `AGENT_RUNTIME_IDENTITY_PATH`
- `AGENCY_REPOSITORY_URL`, `AGENCY_POLICY_URL`, `AGENCY_PLUGINS_URL`
- `OPENAI_API_KEY` must be the Kagent-injected credential placeholder
- `AGENCY_COPILOT_EXECUTABLE` defaults to `/opt/agency/agency`
- `AGENCY_TASK_TIMEOUT` may override the default `00:30:00`
- `AGENCY_WORKSPACE_NAME` may override the default `repository`
- `AGENCY_GIT_EXECUTABLE` defaults to `git`

The native bridge can supervise the existing host credential transport using
`AGENCY_TRANSPORT_EXECUTABLE` and `AGENCY_TRANSPORT_ENTRYPOINT`. This is needed
when the configured Git route uses that private transport; it is not an
alternative A2A server or permission to expose host credentials.

Use a `POST /tasks` body with `id`, `prompt`, `allowEdits`, `baseBranch`,
optional `workBranch`, optional checkout-relative `workingDirectory`,
`refreshPlugins`, and exact `nativeToolApprovals`.
Set `dryRun: true` with `allowEdits: false` and no work branch to verify repository,
Agency policy and hash-bound plugins without starting Copilot or inference.
Repository URLs and branch naming are configuration/input driven; there are no
organization, project, or product constants.

## Safety and supported semantics

- OpenAI **Responses** is supported. Chat Completions is rejected.
- Native HTTPS model/MCP destinations, bounded timeouts, header confinement,
  projected credential placeholders, exact MCP tools, and approval-only
  servers are supported.
- TLS bypass, custom CA replacement, token passthrough, custom auth, unknown
  fields, non-pinned session stores, and non-HTTPS remote origins fail closed.
- Copilot CLI is controlled through `GitHub.Copilot.SDK` 1.0.22, backed by the
  package's pinned CLI/runtime. No standalone actor or bespoke JSON-RPC protocol
  is used.
- Copilot receives no unrestricted shell. It receives bounded repository
  `repo_list`, `repo_read`, `repo_write`, and `repo_delete` tools plus approved
  native MCP tools. Paths must remain below the workspace and symlinks are
  refused.
- `workingDirectory` selects the Copilot process/session directory within the
  checkout; omitted or `"."` preserves the checkout-root behavior and shipped
  task identity. Repository tools remain rooted at the whole checkout.
- Archive output is a local manifest with credential redaction. It is a real
  local result artifact, not a claim of remote archive or memory export.

## Agency configuration

An `agency.toml` or `agency.yaml` is optional at every level. A repository with
no Agency file still performs host policy and base-capability preparation. When
files exist, the trusted host fetches the selected file from each ancestor
between checkout root and `workingDirectory`, at the pinned checkout revision.
Agency then owns TOML/YAML parsing, root-to-cwd precedence and merge semantics;
TOML wins when both formats exist in one directory, and sibling projects are not
visited.

The adapter sends only the pinned repository commit and bounded working
directory. It never sends caller-selected config text or source claims. The
broker validates authoritative repository content and returns bounded local and
remote layer identities (format, revision, path and SHA-256) without content.
Native `remote_config` layers remain subject to Agency validation, existing
MCP/profile policy rejection and marketplace/plugin approval. Malformed present
configuration fails closed. The broker's native-mode cache is also bypassed
for mutable branch/default-branch remote config, while the root-only
`{commit, configCommit, configText}` wire request remains accepted for the
previously deployed clients during migration.

The pinned request digest includes the normalized non-root
`workingDirectory`, so a changed directory cannot replay the same task.
Explicit root (`"."`) and an omitted directory retain the legacy digest.

The .NET SDK 1.0.22 exposes a unified HTTP remote MCP configuration and does not
preserve the JS `type: "sse"` discriminator. Non-empty native SSE tool lists
therefore fail closed until the pinned SDK demonstrates an explicit SSE contract.
Native memory/export services are not silently simulated: they remain outside
this MVP and must be implemented behind explicit host adapters.

`AgencyPluginResolver` is intentionally named for host-prepared **Agency**
plugin bundles. It is not a generic Kagent plugin resolver.

## Build and test

With the repository-pinned .NET 10.0.403 available, run from the HostCapabilities root:

```powershell
dotnet restore tests\AgencyCoder.Tests\AgencyCoder.Tests.csproj
dotnet test tests\AgencyCoder.Tests\AgencyCoder.Tests.csproj --no-restore
dotnet format src\AgencyCoder\AgencyCoder.csproj --no-restore --verify-no-changes
dotnet publish src\AgencyCoder\AgencyCoder.csproj --runtime linux-x64 --configuration Release
```

Publish with the owning repository's normal authenticated build, then use
`Containerfile` to add the output to the Agency harness base:

```powershell
dotnet publish src\AgencyCoder\AgencyCoder.csproj -c Release -r linux-x64 `
  --self-contained false -p:UseAppHost=false -o "$Context\publish"
docker build --build-arg "AGENCY_HARNESS_IMAGE=$BaseImage" `
  -f src\AgencyCoder\Containerfile -t $CoderImage $Context
```

`$BaseImage` is the published base's manifest-digest reference. The resulting
image contains the coding implementation but no repository choice. The engsys
and Audit Containerfiles under `deployment/agency-coder` derive from it and
provide their repository defaults. They currently share .NET 10 SDK and Node 24;
repository-specific tools can be added in those layers when required.

The base starts `/app/harness`, which launches the adapter selected by
`AGENCY_AGENT_EXECUTABLE` and `AGENCY_AGENT_ENTRYPOINT`. The coding layer sets
these to `dotnet` and `AgencyCoder.dll`. No factory controller or JavaScript
coding adapter is inherited. No feed credentials or authenticated restore run
inside these image builds; the only coding-layer input is published output.

See [external deployment](../../deployment/agency-coder/README.md) for portable
kubeconfig/context, namespace, registry and image parameters, native YAML,
integration dependencies, readiness checks and guarded retirement.

## Configuration and tracing

The provider chain is:

1. `AddEnvironmentVariables()` for Kagent `AGENT_*`, `AGENCY_*` and `OTEL_*`.
2. `AddJsonFile(AGENT_COMPILED_CONFIG_PATH)` for the Kagent compiled wire file.
3. `AddEnvironmentVariables()` again, so runtime environment overrides the file.

Application settings bind directly from the root provider into
`HarnessOptions`; native settings bind from `agent` into `NativeAgentOptions`.
Both use standard `AddOptions<T>().Bind(...)`, `IValidateOptions<T>` and
`ValidateOnStart`. The JSON file is the Kagent wire document, not appsettings.

Kagent's Go ADK creates an active A2A span and its current telemetry stack
configures W3C Trace Context plus baggage. The factory `managed-main.go` injects
that context into loopback `/tasks` HTTP headers. ASP.NET Core instrumentation
extracts it; `AgencyCoderTracing` then creates task/stage child spans. The OTLP
exporter reads the inherited `OTEL_*` environment and standard resource
configuration. Correlation uses task and session IDs only; prompts and
credentials are not span tags.

The repository directory intentionally selects `global.json` 10.0.403. Do not
work around that pin or validate against the end-of-life .NET 8 SDK.
