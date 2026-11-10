# Build and verification

Use the .NET 10 SDK selected by `global.json`.

```powershell
dotnet restore LocalLlmGateway.slnx
dotnet build LocalLlmGateway.slnx --no-restore
dotnet test LocalLlmGateway.slnx --no-build
dotnet test tests\LocalLlmGateway.Tests --filter "FullyQualifiedName~AuthenticationTests.ConcurrentTokenRequestsPerformOneExchange"
dotnet format LocalLlmGateway.slnx --verify-no-changes
dotnet run --project src\LocalLlmGateway -- --help
```

Warnings are errors via `Directory.Build.props`. Format with `dotnet format`.
Tests use xUnit and fake HTTP handlers/clocks; routine tests must not require
GitHub authorization or spend inference quota.

# Architecture

`Program.cs` builds System.CommandLine commands. `GatewayConfiguration` layers
user-directory JSON, explicit application-prefixed environment mappings, then
CLI options into validated `GatewayOptions`; do not use ASP.NET's implicit
working-directory settings or environment defaults for gateway options.

`GatewayApplication` owns per-command HTTP client/token-provider lifetimes and
validates host credentials/catalog before starting the HTTP listener.
`GitHubLogin` performs device authorization only for explicit `login`;
`CredentialStore` persists the host GitHub token. `CopilotTokenProvider`
coordinates expiry-aware, single-flight exchange for an in-memory inference
token. `CopilotClient` constructs upstream requests from host-owned authorization
and fixed GitHub/Copilot origins. `ResponsesProbe` checks native JSON, streaming,
and stateless tool-result continuation with an actual entitled model.

`GatewayServer` exposes only health, authenticated model discovery, and Responses.
The request pipeline owns deadlines, credential-safe errors, and metadata-only
logging. `ModelCatalog` preserves upstream metadata while excluding known
incompatible models and caching discovery for five minutes. The inference
semaphore spans the entire upstream stream; disconnects and failures must release
it. Successful response bodies are copied byte-for-byte and flushed incrementally,
not reconstructed from SSE events.

`ResponsesTests` run against actual loopback Kestrel with deterministic upstream
handlers/streams. `scripts\Test-Copilot.ps1` is an opt-in live stock-CLI test using
an isolated home/workspace, no GitHub credential, and a restricted file-view tool.
It consumes real quota and is not part of routine tests.

# Protocol and credential boundaries

- This is a .NET adaptation of MIT-licensed `ericc-ch/copilot-api`, not a wrapper
  around its executable. Preserve `THIRD-PARTY-NOTICES` when adapting its behavior.
- A gateway key and a GitHub credential are different secrets. Clients must never
  receive the host GitHub token or exchanged inference token. Never read the
  Copilot CLI's private credential store to avoid explicit host login.
- Persist credentials atomically under the user configuration directory, outside
  source control; no DPAPI dependency. Preserve Unix owner-only creation modes.
- `GatewayException` messages are safe for users; `UpstreamException` retains
  status/retry metadata but never includes upstream bodies. Do not print raw
  HTTP exceptions, headers, prompt/tool payloads, or response bodies.
- No inference retries or protocol fallbacks. Token exchange retries are
  separately bounded. Token refresh synchronization must not serialize streams.
- Invalid client keys return 401 without upstream requests. Upstream inference
  401 invalidates the cached inference token for subsequent requests and returns
  503; it must not replay the failed inference. Strip upstream error bodies.
- Preserve Responses fields and SSE events rather than translating to Chat
  Completions. Live model IDs and native Responses support must be verified
  against the actual subscription; mocks do not establish compatibility.
- Use stock Copilot provider environment variables with `--model`, not invented
  native provider flags. `COPILOT_OFFLINE=true` is needed to avoid direct client
  GitHub traffic; it does not make gateway inference offline.
- Runtime networking, Docker, Azure deployment, multi-user management, and other
  API protocols are outside this gateway's MVP.
