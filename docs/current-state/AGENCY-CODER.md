# AgencyCoder deployment — 2026-11-06

**Deployment, retirement and identity-authorized repository/plugin preflight
verified.** No model generation or business write was requested.

## 2026-11-09 image factoring (built, not deployed)

The reusable OCI image chain is now:

```text
AgencyHarness base -> .NET AgencyCoder -> engsys coder / Audit coder
```

Build recipes use `Containerfile`. The base starts `/app/harness` and contains
the native bridge, Agency/Copilot, .NET 10 SDK, Node 24/npm, Git and optional
credential-transport code. It does not inherit the factory image or package
its controller, JavaScript coding agent, repository, host URLs, credentials or
deployment CA files. The .NET coding layer selects its adapter through the
new `AGENCY_AGENT_EXECUTABLE`/`AGENCY_AGENT_ENTRYPOINT` bridge inputs.
Old `AGENCY_CODER_*` inputs remain supported for existing-session compatibility.
Only the two derived images set their respective repository defaults.

All four images were built locally in WSL with tag `20261109-6b957f09`:

| Image repository | Local image/config ID (not a registry manifest digest) |
|---|---|
| `host-capabilities-agency-harness` | `805f6b975d54` |
| `host-capabilities-agency-coder-base` | `cd392b427db5` |
| `host-capabilities-engsys-coder` | `3bb51306ec47` |
| `host-capabilities-audit-coder` | `f251eccb4e3b` |

Verified: 16 .NET tests; complete native Go suite on Linux storage; native
source formatting and tracked diff checks; image inheritance, non-root
execution and absence of baked deployment bindings. The base's Agency and
Copilot executables run offline. Both derived images passed an isolated local
fixture dry run covering checkout, authenticated policy/plugin requests,
terminal result and duplicate/conflicting task identities. No real repository
coding or inference occurred. The updated native example passed server-side
CRD validation using the previously published immutable image reference.

No candidate image was pushed or applied. The live deployment below remains
unchanged. Repository CI/CD owns publishing, immutable image references and
chart deployment; no mandatory repository layout or shared chart was added.
The native example now explicitly injects transport URLs/trust/proxy settings
that used to leak in through the old factory base image.

## 2026-11-09 source-only configuration discovery correction (not deployed)

Source now treats absent repository Agency configuration as normal, accepts an
optional checkout-relative task `workingDirectory`, and lets the shipped Agency
binary resolve TOML/YAML ancestor layers from root through that cwd. The trusted
broker verifies selected repository files at the pinned checkout commit,
preserves local/remote source identities and hashes, and retains the old
root-only request shape for the deployed clients during migration. The
container base now supplies `AGENCY_GLOBAL_CONFIG_PATH` as an isolated empty
directory, matching Agency's directory contract.

This is source/build/test work only. No candidate image was pushed or applied,
and the live baseline digest below remains unchanged. Audit has no new
authorization or workflow-readiness claim: its real deployment, permitted
model use, repository/PR authorization and PR test remain outstanding.

Focused validation of the corrected source passed: 17 AgencyCoder .NET tests,
27 broker/Windows-relay tests with the Windows Agency 2026.11.6.4 resolver, five
factory gateway tests, five Agency host-boundary tests, 17 Linux host-bridge
tests, the complete managed native Go suite, .NET/Python/Go formatting checks
and a Linux-x64 Release publish. The corrected four-layer chain was rebuilt
only locally as `*:config-discovery-local`. A network-disabled, non-root
container smoke verified UID 10001, .NET 10.0.403, an empty
`/opt/agency/global-config`, no-config success, TOML-over-YAML selection,
root-to-cwd precedence, sibling exclusion, and malformed-config rejection with
the shipped Agency 2026.10.29.2 binary. Corrected local config IDs were
`793dc455c9c2` (harness), `a0ccf750c056` (coder base), `a662f783b12c`
(engsys), and `4164b0e9d27d` (Audit); they are not registry or deployed
digests.

Audit main at `5a47276c3eff412327a706428cad3158f43ce82c` has no root
`agency.toml`. The corrected source can use the empty repository-config case,
but this image build remains no evidence that Audit plugin onboarding,
permissions or the requested documentation/PR task have succeeded.

Build inputs, full local IDs and executable hashes:
`Q:\src\tools\HostCapabilities\.artifacts\agency-layers-20261109-6b957f09\verification.json`.
Base contract: `Q:\src\tools\HostCapabilities\src\AgencyHarness\README.md`.
Build and external deployment:
`Q:\src\tools\HostCapabilities\deployment\agency-coder\README.md`.

## Current inventory

### 2026-11-09 Audit acceptance rollout

The corrected Audit-derived image was published to the existing private registry
and cached on all three workers:
`10.152.183.131:5000/kagent-lab/audit-coder@sha256:4164b0e9d27dabeaabe514187450fdfb62d27a3f4fac8d16b7680861e3a0378d`.
The registry manifest bytes and digest were independently checked; its config
blob is `sha256:535bc9593679e800776e6dc889efed81090eac6bd5d0eea0c58af8e5fb4e5ac1`.
The earlier local-image IDs above are Docker's reported IDs, not necessarily
the OCI config-blob digests.

An idle, guarded HostCapabilities cutover loaded the reviewed discovery broker
from the protected acceptance artifact, with all prior host scopes retained and
only `audit-docs-contributor` added to admitted profiles. The profile targets
Audit repository `489b2a17-1281-42b5-8544-7f31f2a2e1d7`, PR target `main`
and source prefix `users/nibeauli/audit-docs-`; its toolsets are ADO repository/PR
operations and the existing PR watcher. The persistent owned logon task is
running, and LLM/Agency/ADO readiness passed. No credential or CA changed.

Created separate `baseline/audit-coder` Agent, Harness, AgentTemplate and
ModelConfig, plus actor-JWT `RemoteMCPServer/audit-agency` for the standard
Agency gateway tools. No engsys baseline, ADK reviewer, controller or historical
session was repointed or repaired.

**Stage 0 trust remediation (2026-11-09):** the supported native deployment
manifest removed its explicit `SSL_CERT_FILE`. Kagent owns and injects global
gateway trust, and Harnesses must not declare that reserved runtime variable.
The application-specific `EGRESS_CA_FILE` and `GATEWAY_CA_FILE` inputs remain:
the private transport reads both to construct its strict-TLS client. The
correction did not change the image, bypass validation, or bake trust into an
image. The corrected Audit Harness passed server-side dry run and was then
applied only by UID/resourceVersion-guarded removal of that environment entry.
Agent `baseline/audit-coder` is now generation 1 `Compatible=True` and
`Ready=True`, with desired and latest successful revision
`1efcf74052541c7eb616eb3a82f1d6ddd783bfc66c5621c637ac75d80c368eca`; its
immutable workload digest remains
`sha256:4164b0e9d27dabeaabe514187450fdfb62d27a3f4fac8d16b7680861e3a0378d`.
Existing agents remain unchanged and Ready. Command receipts are retained under
the Audit rollout artifact directory as `stage0-*` files. There is no Audit
session, actor, A2A task, inference, branch or PR at the end of Stage 0, and no
finish-pr monitor was started.

**Stage 1 deployment acceptance (2026-11-09):** three fresh native sessions
successfully created actors, enrolled their exact UIDs and accepted one
no-inference dry-run task each. All three tasks failed before policy/plugin
resolution with `repository_command_failed`. The local transport obtained its
actor-bound session token, but Git's nested CONNECT returned 502. Kagent egress
admitted and connected the 8444 TLS-passthrough route; `agent-proxies` recorded
no CONNECT request, proving failure during the private gateway TLS handshake.
The deployment had incorrectly set `GATEWAY_CA_FILE` to Kagent's egress MITM
bundle, which does not contain the private `agent-proxies` CA.

A temporary owned ClusterTrustBundle extension was tested and removed after ATE
did not include it in the generated actor trust projection. All three disposable
actor UIDs were revoked, the public projection was updated, and the diagnostic
actor was suspended. The sessions remain only as failure evidence:
`01a1c250-8eb1-71cf-9b9f-65130eec17f2`,
`01a1c258-2327-7bf2-a738-11791cf08e74`, and
`01a1c25a-6470-7310-a696-87df98d6dd8a`. No inference, repository modification,
branch, PR, reviewer or finish-pr action occurred. Acceptance remains blocked
while the credential-injecting MITM path is replaced; the private gateway CA
must not be baked into the repository-neutral image.

**Authentication pivot:** the replacement uses the existing
WAM-backed IMDS broker, native managed-identity hooks in Azure CLI, Git
Credential Manager and the Azure Artifacts Credential Provider, an
actor-authenticated relay, and a per-process environment launcher. It does not
intercept application traffic or inject arbitrary authorization headers. The
architecture, capability matrix, exact Azure DevOps audience prerequisite, and
approval-gated rollout are documented in
`Q:\src\engsys-worktrees\wam-imds-broker\src\MAI.EngSys.WamImdsBroker\AUTHENTICATION-FRAMEWORK.md`.

**Stage 2 managed-identity acceptance (2026-11-09):** the Audit image was
rebuilt with GCM-enabled checkout and deployed as
`10.152.183.131:5000/kagent-lab/audit-coder@sha256:97bc57240b35970d98413ca3a1961f828671ba343173df86cb71e457b577dfa9`.
The active revision is
`0f15d235be49d7df7d4ea18fef1ae4c34edf828dbcf85e93036bcc102d059609`.
The Agent now selects its exact egress policy and has direct allowlisted
`https://dev.azure.com` access. GCM uses the App Service managed-identity
contract on port 8446:

```text
IDENTITY_ENDPOINT=https://host-capabilities.proxy-access.svc.cluster.local:8446/metadata/identity/oauth2/token
IDENTITY_HEADER=Bearer kagent-credential-injected
```

Kagent replaces only the `X-IDENTITY-HEADER` placeholder with a short-lived
actor JWT. The WSL and Windows relays admit only the exact GET token route,
`api-version=2019-08-01`, and Azure DevOps resource
`499b84ac-1321-427f-aa17-267ca6975798`; they do not log credentials or token
responses. HostCapabilities revalidates the actor binding/profile before using
WAM. Port 8445 remains the separate Authorization-based model/Agency path.

Fresh session `01a1c2b6-bff8-7377-a5d4-d51f9c2ca1f4`, actor
`a12f4ddd-fa03-4157-9bae-781ed8a7c1b5`, and task
`01a1c2b7-5b6f-7004-b263-d2ed0fa2d673` completed the Audit repository and
policy/plugin dry run with `policyAndPluginsAuthorized=true` and
`inferenceRequested=false`. The missing pinned host Agency
`2026.10.29.2` executable was restored from the approved 1ES feed after it
caused a separate plugin-preparation 502. Receipt:
`Q:\src\tools\HostCapabilities\.artifacts\audit-imds-acceptance-20261109T1548-v9\preflight-observed.json`.

The retained JavaScript MITM adapter is not configured or started. The legacy
ADO proxy relay was stopped after acceptance, and the retained WSL bridge now
sets `legacy_ado_frontend_enabled=false`, so no port-15480 credential-injection
listener is bound. Disposable Stage 2 actors and sessions were revoked/deleted,
and the diagnostic pod and registry forwarding were removed. No inference,
repository modification, branch, PR, reviewer or finish-pr action occurred.

Protected rollout/rollback receipts and operational helpers:
`Q:\src\tools\HostCapabilities\.artifacts\audit-acceptance-20261109T1314`.
Key receipts: `registry-manifest.json`, `image-cache.json`,
`audit-manifest.json`, `audit-created-reconciled.json`, `host-ready-v2.json`,
`host-before.json`, `profiles-before.json`, and `host-task-before.xml`.

| Namespace | Agent(s) | Harness | State |
|---|---|---|---|
| baseline | baseline-coder | agency-coder (BYO native Go bridge + .NET adapter) | Ready; native identity, repository and two plugin bundles verified without inference |
| baseline | baseline-sol, baseline-argon | baseline-review (official Go ADK) | Ready, unchanged |
| cluster-management | cluster-manager, cluster-observability | cluster-management (official Go ADK) | Ready, unchanged |

AgencyCoder immutable image, Linux/amd64:

```text
10.152.183.131:5000/kagent-lab/agency-coder@sha256:0fa712fadcb95255a20b126f5c1cd4406a4d802d845e4662d19a209f45593d9a
```

Successful baseline-coder revision:
`97ac933a661543b7e4dc8d3f91050779ef14aeebc092729e415f9b3444e641f1`.
Registry manifest was verified and the image cached on all three nodes.
The native `/app/harness` owns A2A/session lifecycle; it launches the .NET
loopback `/tasks` service. Neither is replaced by adapter HTTP.

The shared factory controller needed a coupled correction: configuration watched
three namespaces, but its custom authorizer/compiler still admitted only
`factory-executions`. Regression-tested authorization now admits exactly
`baseline`, `cluster-management`, `factory-executions`, rejecting other spaces.
The existing factory-only runtime-memory boundary is unchanged.
Only the Deployment image changed; watches, RBAC, credentials, volumes, arguments
and other configuration were preserved:

```text
10.152.183.131:5000/kagent-lab/factory-coder@sha256:53138df25dd76e29c55a042d70d643fa20ff8ee3bd4291e7d1b992547108a940
```

## Retirement and protection

- Deleted only the obsolete `baseline/baseline-coder` **Harness**, UID
  `a4b847ef-52e1-42df-a836-a6d349be5176`, using UID/resourceVersion DELETE
  preconditions after Agent switch, readiness and zero-session checks.
- The Agent keeps its original UID and now references the new `agency-coder`
  Harness and AgentTemplate. The old fixture template/model and immutable
  image/snapshot artifacts were retained for rollback/recoverability.
- Official ADK Harness UIDs/specs are unchanged. Shared `kagent` namespace,
  PostgreSQL/registry/Grafana MCP services, PVCs and CRDs remain.
- The three legacy factory sessions remain with the same IDs. No actor/session
  metadata repair, old-task replay, namespace deletion or archive purge occurred.
- No baseline loop, recurring schedule or additional controller/UI was installed.

## Verification and enrollment follow-up

- .NET 10.0.403: Linux publish succeeded using existing authenticated 1ES feeds
  on the host. Docker received published output only, not feed credentials.
- `dotnet test tests\AgencyCoder.Tests\AgencyCoder.Tests.csproj --no-restore`:
  **16 passed**. Covers projected plugin credentials, safe status-only errors,
  read-only dry-run preparation without starting Copilot, and changed-mode
  duplicate refusal without changing legacy non-dry-run request digests.
- HostCapabilities tests: **42 passed**, including Python boundary tests,
  explicit additional-space enrollment, wrong-space/UID refusal and revocation.
- Existing broker/relay tests: **19 passed**, including exact reviewed manifest
  approval while changed executable hooks and unreviewed manifests remain denied.
- `dotnet format` verification passed for adapter and test projects.
- Native managed bridge and controller `go test -count=1 ./...`: passed.
  Covered W3C trace/baggage injection, explicit .NET child selection, no-replay
  submission behavior and enrolled-namespace authorization.
- Live server dry run passed; all five Agents are Ready at their current
  generations, with desired and successful revisions matching.
- Fresh disposable native session: an empty prompt reached .NET admission and
  returned **400 before repository/plugin/inference work**; native fixture A2A
  completed. Initial deletion was explicitly refused while the actor finalized;
  after suspension, normal deletion succeeded. Session and actor absence verified.
  No accepted message was resent.
- The Windows host now runs immutable payload
  `Q:\src\tools\HostCapabilities\.artifacts\runtime-agency-enrollment-20261106`
  through the existing startup task. LLM, Agency and ADO readiness returned 200.
  Original payload/config/task are retained under the protected rollback
  `...AppData\Local\HostCapabilities\rollbacks\agency-enrollment-20261106T150714`.
- The initial 401 was a real model-key/actor-identity mismatch. A separate
  namespace-local `ModelConfig/agency-coder` now uses native actor-JWT injection
  with exact host audience and 300-second expiry. The original model and key
  resources remain unchanged for compatibility/rollback.
- Host JWT configuration preserves primary `factory-executions` and adds only
  explicit `additionalSpaces: ["baseline"]`. Existing issuer, audience, JWKS,
  profiles, legacy compatibility and exact UID/subject/profile registry matching
  remain enforced. The existing registrar enrolled each disposable actor only
  after its actual namespace/name/UID was verified.
- The native Go bridge now supervises the existing private credential transport
  used for authenticated Git. Proxy policy adds only `baseline`, preserving all
  prior namespace, hostname, feed, method and credential restrictions. No host
  key or inference token was copied to an actor. Model/Agency authorization
  remains native per-origin JWT injection.
- A second real blocker was the native host TLS leaf, expired
  **2026-11-06 00:29:40 UTC**. Renewed only that leaf with the existing key,
  CA and exact SANs, using the existing native-host configuration helper.
  Active certificate: `native-host-20261106.crt`, valid through
  **2027-02-04 23:42:18 UTC**. CA/key/configuration were not rotated or weakened.
- The current main branch declares `mai-engsys` and `AIX-ES-Standards`. The
  latter's version 2.2.0 manifest changed while both executable hook hashes
  remained exactly reviewed. Reviewed its metadata and added only exact
  manifest SHA-256 `e474d7d65579513d42c988e7e333d596783f2ccd183a8b0fbafebf7cf66b5252`
  alongside the original pin. Spec, complete hook set, executable hashes and
  all MCP/artifact/engine/platform restrictions remain enforced.
- **Final actual-identity acceptance:** native session
  `01a1b349-b25a-7876-8f84-14d9cee31282`, actor UID
  `5cebedaa-5716-4b64-92d2-f263aff8fd86`, final-image A2A task
  `01a1b34a-316d-7ae6-94f1-6174ab7c3350` completed a read-only dry run:
  repository prepared, policy authorized, **two bundles hash-verified**,
  `inferenceRequested: false`. Policy hash:
  `c2a055fa7237efeb99c8bab2e2939b598d9642118f9f2341ba92f3535a0dae9a`.
  Session and actor were deleted normally, its host binding revoked and its
  public UID projection removed. All three disposable enrollment identities
  are revoked; baseline has zero sessions and the original three factory
  sessions remain unchanged. The running proxy projection contains none of
  their UIDs, and the final revoked identity returned 401 at Agency policy.

There is no remaining identity/plugin preflight blocker. Future native sessions
must follow the documented trusted-host enrollment/revocation lifecycle; no
anonymous or automatic self-enrollment was introduced. A billed coding/model
turn remains deliberately unrun, not silently counted as validated.

The migrated fixture had no native memory, remote archive export, SSE or native
MCP tools configured. None was silently removed. The .NET MVP still explicitly
rejects native memory/export, SSE and Chat; the historical factory's memory and
archive services and retained session revisions remain separate and preserved.

## External deployment and receipts

Portable instructions and parameterized native YAML:
`Q:\src\tools\HostCapabilities\deployment\agency-coder\README.md` and `native.yaml`.
They use an operator kubeconfig/context, namespace-local substrate, reviewed OCI
runtime/bridge artifacts and normal .NET publish/OCI tooling; no lab checkout,
WSL root path or devbox credential is part of that deployment contract.

Protected lab receipts/backups:
`/home/nibeauli/kagent-lab/agency-coder/` (`before-*`, `deployed.json`,
`controller-before.json`, `controller-after.json`, `smoke.json`, `after-*`,
`final-*`, `image.json`, OCI layout). The non-secret local policy probe receipt
is `Q:\src\tools\HostCapabilities\.artifacts\agency-coder\plugin-probe.json`.
Do not run the old fleet installer blindly: its fixture restoration settings
predate this migration. Use the documented guarded Agent-reference update.

Final enrollment receipts: `native-plugin-preflight-final.json`,
`enrollment-session-final.json`, `enrollment-final-agents.json`,
`enrollment-final-sessions-*`, `enrolled-deployed.json`, and `jwt-model.json`
under the same protected lab receipt directory.
