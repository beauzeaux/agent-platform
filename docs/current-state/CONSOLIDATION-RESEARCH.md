# Consolidation research and evidence

[Plan](CONSOLIDATION-PLAN.md) | [Environment index](README.md)

Research performed 2026-11-03 against the installed source pins and installed SDK, with primary documentation used
for context. Current website/default-branch material is not assumed to describe every installed behavior.
Three read-only investigations covered native integration, memory/export, and ADO credential overlap.

## Source baselines

| Component | Examined baseline |
|---|---|
| Kagent | `eed27237a715fdbc7664aab9e1089c9a05097e71`, installed `1.0.0-rc1` |
| Substrate | `71b1f9ec80848f8fd46f5d5d7e6b84ba36a80966`, installed `v1.0.0-rc1` |
| Active Substrate egress data plane | `cr.agentgateway.dev/agentgateway:v1.7.0`, not the legacy Go router |
| Copilot SDK | Installed package `1.0.28`; bundled CLI version `1.0.105` |
| Existing custom integration | `Q:\tools\kagent-lab`; Windows proxy source under `Q:\src\tools` |

Pinned source roots:

```text
\\wsl.localhost\Ubuntu-26.04\home\nibeauli\kagent-lab\source\kagent
\\wsl.localhost\Ubuntu-26.04\home\nibeauli\kagent-lab\source\substrate
```

Relative paths below refer to these roots unless a Windows path is given.

## R1. Native BYO contract, not an unconsumed central proxy

| Evidence | Finding |
|---|---|
| Kagent `go/core/internal/translator/byo/compiler.go:36-85` | BYO compiles ADK configuration, validates it, and compiles credentials |
| `proto/kagent/config/v1/adk.proto:10-45,39-158,240-292` | Runtime/model/MCP compiled schema; model optional for BYO |
| `go/adk/pkg/config/config_materialize.go:10-35` | `KAGENT_CONFIG_JSON`, agent card, and runtime token materialization contract |
| `go/core/internal/translator/adkconfig/mcp.go:12-18` | Compiled MCP uses direct URLs, explicitly no proxy URL |
| `go/core/internal/translator/adkconfig/model.go:69-125` | Model/base URL/API format/header mapping |
| `go/core/internal/translator/adkconfig/builder.go:111-138` | MCP protocol/tool/approval compilation |
| `go/core/internal/translator/credentials.go:20-40,122-145,369-391` | Placeholders and actor-JWT credential rules |
| `go/api/v1alpha3/modelconfig_types.go:528-556` | TLS options lack a custom-CA bundle field |

The current custom wrapper reads compiled configuration, but uses mainly runtime/controller information:
`Q:\tools\kagent-lab\factory-coding\managed-main.go:45-80`.
The Node adapter hardcodes provider/model/MCP settings:
`Q:\tools\kagent-lab\engsys-agency-coder\agent.mjs:149-175`.

Installed SDK contracts:

| File under `Q:\tools\kagent-lab\engsys-agency-coder\node_modules\@github\copilot-sdk` | Finding |
|---|---|
| `dist\types.d.ts:2891-2970` | Provider supports `baseUrl`, API key, `wireApi`, arbitrary `headers` |
| `dist\types.d.ts:2465-2490` | Session streaming controls emitted assistant delta events, not a guaranteed provider nonstreaming request |
| `dist\types.d.ts:1603-1655` | MCP HTTP/SSE URL, headers, tools, timeout supported |

Caller token passthrough/STS is distinct from injected actor identity. The initial adapter can defer/reject caller
passthrough without losing actor-JWT support. A model/MCP shared origin can work with identical credentials;
separate names are a policy recommendation, not a compiler requirement.

Primary docs:

- [Core concepts](https://kagent.dev/docs/kagent/1.x/about/core-concepts/)
- [Model providers and actor authentication](https://kagent.dev/docs/kagent/1.x/setup/model-providers/about-model-providers/)
- [BYO agents](https://kagent.dev/docs/kagent/1.x/agents/bring-your-own-agent.md)
- [Egress control](https://kagent.dev/docs/kagent/1.x/about/substrate-runtime/networking-and-egress.md)
- [Pinned BYO compiler](https://github.com/kagent-dev/kagent/blob/eed27237a715fdbc7664aab9e1089c9a05097e71/go/core/internal/translator/byo/compiler.go)

## R2. Host composition and telemetry seams

| Current source | Reusable behavior / gap |
|---|---|
| `Q:\src\tools\LocalLlmGateway\src\LocalLlmGateway\GatewayApplication.cs:5-43` | Explicit login, credential/capability construction, startup model check; isolate this check per capability when composing |
| `Q:\src\tools\LocalLlmGateway\src\LocalLlmGateway\GatewayServer.cs:13-104,121-210` | Kestrel routes, limits, metadata logging, byte-preserving streamed responses |
| `Q:\src\tools\LocalLlmGateway\src\LocalLlmGateway\CopilotClient.cs:7-32` | Separate host upstream authorization; upstream 401 invalidation |
| `Q:\tools\kagent-lab\agency-broker\broker.py:339-525` | Shared Agency child, multiplexed RPC, nested profile enforcement |
| `Q:\tools\kagent-lab\agency-broker\broker.py:537-653` | MCP lifecycle and logical-vs-HTTP error distinction; only coarse current counters |
| `Q:\tools\kagent-lab\agency-broker\Install-AgencyBroker.ps1:22-27` | Existing user-logon startup pattern |
| `Q:\tools\kagent-lab\proxy-access\host_bridge.py:90-105,172-207` | Explicit relay headers/context need deliberate propagation; do not assume end-to-end trace context survives |

Windows routing inspection found no explicit `10.82.*`, `10.152.183.*`, or `172.30.*` routes. This is a reason to verify
host OTLP transport, not proof that an arbitrary ClusterIP is directly reachable from Windows.
The restarted model gateway was still healthy at 14:20 PST.

Primary docs and compatibility caveats:

- [Kagent OTel stack](https://kagent.dev/docs/kagent/1.x/observability/otel-stack.md): standard Collector -> existing backends.
- [Kagent tracing](https://kagent.dev/docs/kagent/1.x/observability/tracing.md): BYO must instrument itself and read exporter
  configuration; model content-capture false does not remove every native tool-content attribute.
- [Kagent prompt auditing](https://kagent.dev/docs/kagent/1.x/observability/audit-prompts.md): spans are best-effort, not a
  complete/tamper-proof archive.
- [OTel .NET exporters](https://opentelemetry.io/docs/languages/dotnet/exporters/),
  [Collector configuration](https://opentelemetry.io/docs/collector/configuration/),
  [resilience](https://opentelemetry.io/docs/collector/resiliency/),
  [security](https://opentelemetry.io/docs/security/config-best-practices/).
- [Loki OTLP ingestion](https://grafana.com/docs/loki/latest/send-data/otel/): native OTLP relies on structured metadata.
  Our deployed Loki is 2.6.1; do not copy current Loki 3-era examples without a version-specific compatibility decision.
- [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai): current conventions remain
  in development; pin the emitted schema rather than letting metric names drift with upstream documentation.

## R3. Memory: reusable service, explicit prerequisites

| Pinned Kagent source | Finding |
|---|---|
| `proto/kagent/runtime/v1/memory.proto:10-23,26-93` | Actor-supported memory API; no general episode export API |
| `go/core/internal/service/memory/service.go:224-252` | Agent/creator scope derived from persisted session and authorized server-side |
| `go/core/internal/service/memory/service.go:18,276-281` | Exactly 768-dimensional vectors |
| `go/core/internal/translator/adkconfig/runtime.go:133-147` | Native Kagent memory wiring adds embedding model/env/egress; not automatically a BYO feature |
| `go/adk/pkg/memory/kagent_service.go:37-60` | Embedding configuration required |
| `go/api/v1alpha3/harness_types.go:92-110` | Native harness memory settings; BYO selector empty |
| `go/core/internal/database/memory.go:56-143` | TTL pruning/popularity extension behavior |
| `go/core/pkg/migrations/vector/000001_initial.sql:5-25` | Vector schema and limited native metadata model |
| `helm/README.md:29,201`, `DEVELOPMENT.md:211-213` | Stock PostgreSQL image lacks pgvector; extension/schema/permissions are an operator prerequisite |

`design/EP-1256-memory.md` includes older v1alpha2/Python material. It was not used as authority for the current BYO
wiring. Native memory is not a content-only/keyword store and does not provide reviewed-memory governance automatically.

## R4. Session export: SDK events, not assumed cloud capture

| Source | Finding |
|---|---|
| Kagent `proto/kagent/api/v1/sessions.proto:18-27,73-93` | Session lifecycle and metadata pagination, not full SDK transcript export |
| `proto/kagent/runtime/v1/task_store.proto:9-18,48-73` | A2A task storage and optional triggering StreamResponse events |
| `proto/a2a.proto:19-79` | Streaming/subscription for A2A task updates |
| `proto/kagent/api/v1/scheduled_runs.proto:119` | A2A remains task/transcript authority at its own abstraction |
| SDK `dist\session.d.ts:190-229,250-294` | Event subscription, `getEvents()`, nondestructive disconnect |
| SDK `dist\types.d.ts:2739-2745` | `enableSessionStore` is the shared cross-session search/retrieval store, not proof that event logs/resume are disabled |
| SDK `dist\generated\rpc.d.ts:12972-12980,27740-27748` | Cursor/event-log contract and documented local event-log path fallback |
| `Q:\src\agency-debug\src\UX\src\sessions\shared\transcript\api\transcriptRelay.ts:1-40` | Agency owning-daemon relay; private service cache/resumed-memory gaps |

No separate complete transcript/export/event-log service was found in the pinned Kagent/Substrate proto service
inventory. This does not deny A2A task history; it distinguishes that history from full Copilot message/tool events.
The deployed accepted profile uses `VOLUMES` snapshots; generic Substrate memory-snapshot documentation does not
establish RAM capture or transcript export for this setup.

## R5. ADO replacement: active AGW, not the legacy Go router

| Pinned source | Finding |
|---|---|
| Substrate `api/credprovider/v1/credprovider.proto:22-45` | FetchSecret receives credential URI and attested actor SPIFFE ID, returns opaque bytes without expiry |
| `docs/kubernetes-credential-provider.md:7-18,62-67` | Active AGW mTLS fetch, five-minute actor/URI credential cache, placeholder-header requirement |
| `charts/substrate/templates/atenet-egress.yaml:84-113` | Current Helm routes hardcode `k8s.io` provider endpoint |
| `cmd/ate-setup/internal/config/config.go:585-705` | Installer accepts custom/multiple named credential-provider endpoints |
| `cmd/ate-setup/internal/steps/overlay.go:371-389`, `overlay_test.go:60-89` | AGW provider list rendering and tests |
| Kagent `go/core/internal/translator/egress.go:95-131` | Normal credential bindings support Kubernetes Secret or actor JWT, not arbitrary external-provider URIs |
| `Q:\src\tools\ado-dev-proxy\src\AdoDevProxy\RequestPolicy.cs:20-64,94-103` | Method/path restrictions and exact signed-CDN capabilities |
| `Q:\src\tools\ado-dev-proxy\src\AdoDevProxy\ProxyHost.cs:148-209` | Credential stripping, injection, and safe-read-only refresh/retry |
| `Q:\src\tools\ado-dev-proxy\src\AdoDevProxy\WamTokenProvider.cs:54-80` | Host WAM caching/refresh margin |

Important correction made during research: timeout/registry behavior in
`cmd/atenet/internal/router/egress` belongs to the legacy Go path and was **not** treated as evidence for active AGW.
Actual AGW provider timeout/retry/expiry-cache behavior remains a version-specific test gate.

The user accepts short-lived tokens in trusted egress, which makes the provider approach viable without token projection
to actors. It does not remove the client-placeholder, chart/compiler, TLS, or request-policy parity work.

## Verification scope

This research verified source/API contracts and existing metadata health, not live model/tool execution, native
credential-provider interoperability, vector migrations, transcript uploads, or private TLS canaries.
Those are explicit implementation acceptance gates in the plan.
