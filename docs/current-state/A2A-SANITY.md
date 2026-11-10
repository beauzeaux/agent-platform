# Disposable A2A sanity check

The public/synthetic acceptance fixture is implemented under
`Q:\src\tools\HostCapabilities\deployment\a2a-sanity`.
It is separate from the inactive twelve-role business-project experiment.

## Run it

```powershell
$sanity = 'Q:\src\tools\HostCapabilities\deployment\a2a-sanity\Invoke-Sanity.ps1'
& $sanity -Action Check                         # preview; no resources or inference
& $sanity -Action Check -Apply -Live             # setup, three A2A assignments, teardown
& $sanity -Action Status                        # retained result / private report
& $sanity -Action Remove -Apply                 # idempotent cleanup retry
```

For manual inspection, use `Setup -Apply -Live`, `Run -Live`, then `Remove -Apply`.
The source README documents prerequisites, exact flags, recovery and retention.
Setup creates no timer or background discovery loop. A live check uses real model
requests; tool continuations mean three assignments need not mean three requests.

## What it proves

| Role | Model / runtime | Actual proof |
|---|---|---|
| Coder | `mai-code-2`; existing Copilot SDK/managed harness | Confined file edits and seven immutable executable checks on a tiny public function |
| Reviewer | `gpt-6.1-sol`; official Go ADK | Reviews that coding artifact and obtains a fresh nonce through official read-only Kubernetes MCP |
| Tool verifier | `gemini-4-argon`; official Go ADK, native Chat | Independently obtains another fresh MCP nonce and confirms the same artifact hash |

The native Session/A2A APIs carry the tasks and actual coder artifact.
Nonces are not included in prompts, so a model cannot pass by merely asserting
that it called an MCP tool. Native model/endpoint metadata and the configured
credential boundary are checked before work. Unknown delivery is never blindly
resent; request/task IDs and receipts persist.

## Verified result, 2026-11-05

Run `da6b1942-be01-4200-bff5-a157e83ec65d` passed all three native A2A tasks,
the seven coding checks and both MCP nonce/hash checks. Automatic cleanup removed
14 owned Kubernetes objects and three native sessions. Independent checks found
zero remaining owned objects and zero session actors. A second Remove was harmless;
shared Windows LLM/ADO/MCP remained Ready.

Protected report:
`/home/nibeauli/kagent-lab/a2a-sanity/da6b1942-be01-4200-bff5-a157e83ec65d/state.json`.
Image: `sha256:82321e3ca831eb95b23103b9047205613fa38f757de18e4ed63d3ac556e658da`.
Code artifact SHA-256:
`4e8fc3abb55da5c1fbe4afddd1833dca66e2dff87a9d30c13e7607a4c7388872`.

The SDK fixture has focused lifecycle/write-scope tests; the native bridge has
passthrough, authentication, framing, streaming and no-replay regression tests.
Future cleanup also checks native actor absence directly, not just session rows.

## Scope and retained state

The fixture reuses the factory controller/pool, protected operator client,
existing management-model trusted-egress Secret grant, official read-only tool
server, and private image publisher/cache. It adds only run-ID/UID-owned native
resources and synthetic ConfigMaps; it never deletes a namespace or shared service.

The native bridge now admits `/v1/chat/completions`, with verified TLS and
anonymous 401. Only that native route was added; legacy transports were not widened.
This shared capability remains after teardown, as do image caches, the private
receipt and platform snapshot/history/telemetry retention.

The proof uses Secret-backed trusted native egress. It does not prove Windows
Agency nested-tool profiles, new actor-JWT profile grants, remote Git/ADO writes,
memory isolation, archive retention, every SDK's Chat support, HA or recovery.
The existing custom business SDK harness still accepts Responses only; the
official ADK Argon path is now demonstrated. The business swarm remains inactive.

## Baseline namespace request

The shared factory controller now watches `factory-executions`, `baseline` and
`cluster-management`. Its namespace selector reports all three Active.
`baseline-coder`, `baseline-sol` and `baseline-argon` are Ready in baseline;
`cluster-manager` and `cluster-observability` are Ready in cluster-management.
Both namespace-local worker pools are 1/1 Ready. No second controller/dashboard
was created, and no scheduler or sustained model work is running.

The persistent baseline is prospectively non-live; do not use Remove/Check to
replace those observation resources accidentally. Its deployment and readiness
receipt are managed by `HostCapabilities\deployment\baseline\fleet.py` and
`/home/nibeauli/kagent-lab/baseline/fleet-readiness.json`.

Workload, cadence and stop/quota policy await selection. Repeating finite public
cycles with a five-minute pause and one-hour stop was proposed, not approved.
An unattended or infinite paid loop was not started while the user was unavailable.
Preparation is recorded under `HostCapabilities\deployment\baseline`.
