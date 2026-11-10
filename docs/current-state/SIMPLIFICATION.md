# Consolidation and simplification candidates

[Index](README.md) | These are proposals, **not changes already made**.

For the detailed six-part integration architecture and staged acceptance gates, use the
[consolidation plan](CONSOLIDATION-PLAN.md) and its [source research](CONSOLIDATION-RESEARCH.md).
The table below remains the broader infrastructure cleanup inventory.

## Suggested supported baseline

Keep the existing three-host WireGuard + MicroK8s transport and shared Substrate execution platform.
Keep one clearly defined authenticated agent entry path, the existing Windows-owned ADO/MCP credential boundaries,
and the current Prometheus/Loki/Tempo/Grafana stack where those capabilities are needed.

Prefer consolidating ownership/configuration and retiring unused paths over creating another universal proxy,
workflow engine, service registry, PKI, or deployment manager.
There are already enough mechanisms to manage the supported baseline.

## Prioritized candidates

| Priority | Candidate | Evidence / benefit | Prerequisite and safety gate |
|---|---|---|---|
| P0 | Formalize the supported inference service and its launch ownership | LocalLlmGateway was absent despite green cluster health. It is now restored as an operator-requested background process, not managed startup. | Preserve the verified model/bridge path; choose explicit startup/recovery ownership and confirm entitlement. Require approval before inference; do not invent provider fallback. |
| P0 | Establish one versioned deployment/documentation home | Active kagent-lab and AdoDevProxy are not Git repositories; other experiments have untracked/dirty state. | Inventory and preserve generated secrets separately; choose repository ownership; capture current supported manifests before moving anything. |
| P0 | Make boot/recovery ownership consistent across hosts | Primary/XL have no matching recovery task; devbox-2 has a separate failing logon task. | One-host-at-a-time recovery test with retained disks, current UUIDs, and rollback. Preserve working storage guards. |
| P1 | Close the current backup gap before major simplification | Retained successful backup boundary is October 31; factory/swarm came later. All durable services remain XL-local. | Quiesce work, include current schemas/identities/objects/config, verify off-host encrypted restore. Existing helper is maintenance, not a health probe. |
| P1 | Retire the unused Argo factory path if no consumers remain | Controller is running; zero Workflows/WorkflowTemplates/CronWorkflows; direct factory docs explicitly retire Argo. | Confirm no users or external submissions, archive owned config, remove only owned controller/RBAC. Do not casually delete shared CRDs or namespace data. |
| P1 | Treat old Podman fabric/remote transports as archived experiments | Podman stopped; no live dependency found for Caddy/step-ca/Envoy fabric, Dev Tunnel/ICE, WAM IMDS. | Inspect stopped-machine state/secret volumes without deleting them; confirm no external consumers; retain rollback artifacts until accepted. |
| P1 | Remove unused legacy Docker relay/network only after confirmation | No Docker containers/volumes; bridge and socat relay remain on 5480. | Prove nothing uses that listener; use its component-owned teardown, not the Substrate proxy teardown. Preserve the running Windows ADO service. |
| P1 | Consolidate local access forwarding | UI/Grafana have systemd ownership; Prometheus/Loki/Tempo have ad hoc forwards; Tempo's is stale. | Select only supported UIs, reuse existing access-unit pattern, verify loopback binds and restart behavior. Avoid public ingress just to remove forwards. |
| P1 | Add boundary-aware health using existing monitoring | 34 Prometheus targets up, yet model path unavailable, MCP discovery failed, and an actor crashed. | Cover host listener, bridge, authenticated metadata, actor state, and forwarding separately; no inference/prompt traffic in routine checks. Reuse Prometheus/probes rather than a new dashboard backend. |
| P2 | Decide whether original insecure Kagent pilot remains needed | Separate controller/UI/three workers primarily retain deterministic lifecycle fixtures. | Preserve lifecycle/recovery tests and useful actor state. Migrate only if the supported controller can retain authorization and test separation. |
| P2 | Evaluate merging the two worker pools, not just deleting one | Five worker pods share one Substrate platform; factory currently only primary/XL. | Determine whether namespace/runtime-space isolation is a required boundary; measure actual booking/placement and restore behavior. Two pools may be intentional. |
| P2 | Reduce deployment duplication across eight role services | Shared Python gateway implementation and common PostgreSQL table; only configs/profiles/roles differ. | Reuse existing templating/service manager first. Do not discard per-role authorization/concurrency or replace Kagent's task lifecycle with another custom one. |
| P2 | Remove hardcoded NAT and ClusterIP assumptions | Model host uses `172.24.160.1`; legacy firewall pins WSL `172.24.161.36`; swarm OTLP uses service ClusterIP. | Separate configured names from resolved addresses; validate DNS from WSL and retain authenticated transport. Do not blanket-bind Windows to all interfaces. |
| P2 | Define telemetry durability and retention explicitly | All observability storage is ephemeral; some UI state is regenerated, retained data is not. | Decide whether telemetry is disposable or operationally required; use existing storage choices, with explicit capacity/backup tradeoffs. |

## Complexity worth keeping

| Mechanism | Why not simply remove it? |
|---|---|
| Windows-owned WAM and GitHub credentials | Agents should not receive the host user's long-lived credentials |
| Explicit Git/package proxy vs MCP broker | They provide different protocols and permissions; ADO REST tools are not Git smart HTTP |
| Actor identity -> RAM lease -> mTLS host bridge | Bridges a sandbox runtime to a Windows user-session capability with explicit identity boundaries |
| Original insecure pilot vs authenticated factory | Security scope differs; reducing controller count must not merge authority accidentally |
| PostgreSQL queue identity and uncertain-delivery handling | Removing it without a replacement contract can duplicate external writes |
| Disk UUID/mount guards | Prevents silently running worker state on the wrong filesystem after WSL restart |
| Retained PVs and old experiment secret volumes | Potentially useful data/identity; stopped is not equivalent to disposable |

Some custom controller bootstrap is justified by the current pinned Kagent release and authentication/egress needs.
Before building more around it, check whether a supported upstream version already supplies the missing behavior.
An upgrade is not assumed safe just because it reduces local code.

## A low-risk sequence

1. Freeze a supported inventory in version control, assign startup/config ownership, and choose the intended model path.
2. Restore observable dependency health and take a current, tested protected backup.
3. Archive or disable one demonstrably unused legacy path at a time; retain data and a tested rollback.
4. Standardize access/startup using the existing service mechanisms, then consider merging controllers/pools only if
   the required boundaries can be preserved.

Useful completion measures: supported entry points have named owners; a Windows/WSL restart has a repeatable result;
no credentials are duplicated to actors; all required state has a restore drill; active and experimental infrastructure
are unambiguous. Process count alone is not the success criterion.
