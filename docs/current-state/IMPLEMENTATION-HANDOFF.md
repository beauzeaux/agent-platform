# Overnight implementation handoff

**2026-11-06 AgencyCoder update:** the baseline fixture BYO Harness was retired
and `baseline-coder` now uses the native Go bridge plus published .NET adapter.
All five Agents remain Ready; non-inference A2A/adapter verification passed.
The shared controller's namespace authorization mismatch was corrected without
changing watches or credential grants. The old three factory sessions remain.
**Follow-up resolved identity/plugin admission:** native JWT enrollment, the
expired native-host leaf, and an exact reviewed plugin-manifest compatibility
pin were repaired without relaxing TLS, executable-hook or tool scopes. A fresh
final-image native identity completed repository/policy/two-bundle plugin dry
run without inference, then was deleted/revoked. No model turn is claimed.
See [current images, retirement receipts and external deployment](AGENCY-CODER.md).

User authorized execution and interruption of test runs on 2026-11-03.
**Approved staged rollout completed.** This is the current implementation record;
the other inventory pages preserve the initial read-only baseline. The remaining
limitations below are explicit, not false readiness.

**Recovery update, 2026-11-04:** all three nodes are now Ready. Third node
`cpc-nibea-4memo` (`10.82.0.4`) recovered at **09:58:40 PST** after its last
lease at **06:27:16**. Kubernetes recovered controller/UI/Collector on XL
overnight; Tempo was recreated on XL at **09:43**, Ready **09:44**, before the
third node returned. Its ephemeral storage does not prove old traces survived.
Primary and XL had no observed Ready transition. Hibernation remains unconfirmed;
no forced recovery or accepted-work replay was performed. Overnight monitoring
was stopped when the user returned.

The [stable access and native management follow-up](STABLE-ACCESS-AND-MANAGEMENT.md)
records the new default-port front door and verified Windows hostnames,
read-only native agents, and the separate, pre-existing intermittent
MicroK8s dqlite/Kine `database is locked` write-contention warning.

## What is running now

**Fleet reset, 2026-11-05:** the old 14 Agent definitions and 32 associated
agent configuration objects were retired, and the old admission services/timer
were stopped/disabled. The shared factory controller/UI now watches
`factory-executions`, `baseline` and `cluster-management`; no new controller/UI
was created. Five new Agents are Ready: `baseline-coder`, `baseline-sol`,
`baseline-argon` in `baseline`, and `cluster-manager`, `cluster-observability`
in `cluster-management`. Both namespace-local worker pools are 1/1 Ready.
The observability agent retains Grafana/Loki through the pinned read-only MCP.
Receipt: `/home/nibeauli/kagent-lab/baseline/fleet-readiness.json`.

No model turn or recurring scheduler was started during redeployment. Old conversation cleanup removed 88 records with verified session/actor absence:
factory orphans decreased from 90 to 3, and pilot from 1 to 0. Three retired
factory sessions remain behind native lifecycle blockers. Targeted abandonment
or metadata repair is approval-required; no guard bypass or DB write was made. Controllers, databases, storage and archives were preserved. Detailed
eight-role memory/archive validation below is historical; it does not assert that
the new baseline fixture exercises those features.

| Area | Deployed state | Remaining gate |
|---|---|---|
| Windows host | One HostCapabilities owner for LLM, embedded ADO, MCP and common control; original addresses preserved | Native ADO credential provider deliberately disabled |
| Native Kagent model/MCP | Two management and three baseline Agents Ready in their own namespaces under the shared factory instance | Old conversation cleanup remains in progress; recurring baseline work disabled |
| Host OTel | Production exporter -> private mTLS Collector -> existing Tempo/Prometheus; Grafana dashboard provisioned | Token usage is not inferred; retention remains ephemeral |
| Native memory | Offline 768D embeddings, pgvector/HNSW, real actor memory/foreign-Agent isolation and fresh-session recurrence verified; enabled for future factory invocations | Candidate review/promotion remains deliberate |
| Private session export | Real SDK logical events, durable outbox, private RustFS objects and PostgreSQL index verified; enabled for future invocations | 4 MiB cumulative upload bound; honest SDK ancestry gaps; scoped bucket credentials/retention hardening |
| ADO proxy assessment | Keep compatible policy-enforcing proxy embedded in the unified process | Native credential injection is not feed/method/redirect/no-write-replay parity; real Schannel interop fails |

### Windows process and startup

Verified production owner PID **46504** owns `172.24.160.1:4141` (LLM) and
loopback `5480` (ADO), `5481` (ADO control), `15442` (MCP), `14540` (common control).
The PID is an observation, not a hardcoded recovery target.

- Runtime: `Q:\src\tools\HostCapabilities\.artifacts\runtime-chat-20261105-375cb3d8`.
  This immutable published copy must not be overwritten while running.
- Previous `runtime-service-20261103` payload is retained for rollback.
- Protected config: `%LOCALAPPDATA%\HostCapabilities\host.production.json`.
- Lifecycle: existing ownership-checked Start/Stop scripts. The original
  `host-capabilities-service` execution exited cleanly during the approved cutover.
- Logon task: `HostCapabilities-<current-user-SID>`, enabled. It uses the explicit
  per-user `C:\Users\nibeauli\.dotnet\dotnet.exe`; no global DOTNET_ROOT change.
- Old `Kagent Agency Broker` task is disabled, retained for rollback.
- Original gateway PID 37432, ADO PID 24136, broker PID 49512 and its six verified
  descendants were stopped by exact ownership checks. They are not parallel owners.
- One supervised transitional Agency broker child remains, preserving existing
  approved stdio/plugin policy; this is one service boundary, not a rewrite of Agency.

Original consolidation acceptance: all three capabilities Ready; 30 authenticated models;
Linux NuGet metadata through the embedded proxy returned 12 resources with CA
verification; MCP initialize/list/DELETE returned five approved gateway tools.
No live business write was replayed.

**Chat routing update, 2026-11-05:** the user approved an idle shared-service
restart. Old owner 6832 exited gracefully; the tested payload above became owner
46504, and the existing enabled logon action now targets it. The cutover took
14.29 seconds; production configuration and listener addresses were unchanged.
LLM, ADO and MCP readiness passed. Authenticated discovery exposed exact
`gemini-4-argon` with `/chat/completions` only, and anonymous Chat admission
returned 401. The observed catalog had 34 models; counts are not invariants.
No inference or swarm/session was started during that cutover. Startup XML, prior owner and logs are
protected under `%LOCALAPPDATA%\HostCapabilities\rollbacks\chat-20261105T195836Z-1d033467`.
The separate [disposable A2A sanity check](A2A-SANITY.md) subsequently enabled the
native bridge Chat route and proved real SDK coding plus Sol/Argon official-ADK
MCP work. It removed all 14 owned objects and three sessions, with zero remaining
session actors. The custom business SDK still needs Chat-format support if used
for Argon; the twelve-role business swarm has not been started.

Safe health:

```powershell
Invoke-RestMethod http://127.0.0.1:14540/health/live
Invoke-RestMethod http://127.0.0.1:14540/health/ready
```

`native-ado: disabled/configuration` is intentional, not failed LLM/ADO/MCP readiness.

### Native frontend

Service: `https://host-capabilities.proxy-access.svc.cluster.local:8445`.
It forwards through the existing WSL bridge at `10.82.0.2:15445`; no new daemon
or lease was introduced. Its leaf is under the existing private proxy CA.
Legacy mTLS routes/certificates remain intact.

Actor JWT audience: `https://host-capabilities.proxy-access.svc/session`.
Issuer: `https://idp.ate-system.svc`, ES256 public JWKS. Windows validates
signature/issuer/audience and real `actor.ate.dev` identity, then derives approved
profile from the authoritative broker registry. Unknown actors are not trusted
because they have a well-signed token. Native requests do not receive host secrets.

Actual strict-TLS checks: anonymous model discovery 401, forbidden IMDS route
403, unchanged legacy authenticated bridge still returned 30 models.
Real actor metadata failed before host registry admission and succeeded after
read-only profile binding: advertised native model, five MCP tools, initialize
and DELETE. One fixed public SDK turn returned `NATIVE_HOST_OK` using
`gpt-5.4-nano`. No private repository, instructions, plugins or identity were
sent to that model. Actual host metric recorded exactly one successful native
Responses request (gateway/downstream 200); Tempo trace
`6ab494fdbe98a2f6b7ca99b496a80c2d`, 1014 ms.

### Telemetry

Official Collector chart `0.178.2`, contrib `0.163.2`, Helm release `host-otel`,
namespace `observability`, revision **7**. Host endpoint `https://localhost:14318`
uses standard systemd socket activation/proxy to a stable ClusterIP, not a fragile
Kubernetes API port-forward. Exact source-IP NetworkPolicy plus client certificates;
no public ingress, NodePort or Windows root-store change.

Production host pin profile validates private root, DNS, time, EKU and exact
SHA-256 leaf. Server/client leaves have explicit 48-hour lifetime. Renewal:
`deployment\otel\manage.py install --renew-leaves`; reload/restart the exporter
with its updated public pin after renewing. Do not silently relax validation.

Verified `2026-11-04T00:37:38Z`: authenticated ingestion, anonymous denial,
Tempo/Prometheus delivery, injected payload attributes/events stripped, and retained
operation/profile/tool-layer metadata. Trace `8bad247560e7eb0a803f667e4662d678`.
Root-private report: `/home/nibeauli/kagent-lab/host-observability/verification.json`.
Actual production `host_requests_total`, readiness, and MCP sessions appeared;
the Windows model span was independently found in Tempo.

Dashboard: **http://localhost:13000/d/host-capabilities**. All eight queries
executed successfully against live Prometheus; the Grafana sidecar installed its
owned ConfigMap. It distinguishes MCP transport sessions from conversations,
known HTTP status from unknown, and disabled native credentials from failed
compatibility capabilities. No prompt, response body, signed URL or credential
is telemetry. Metric service selector is `exported_job="host-capabilities"`.

### Memory backend and database protection

TEI CPU 1.9.4 runs pinned `BAAI/bge-base-en-v1.5` revision
`a5beb1e3e68b9ab74eb54cfd186867f64f240e1a` entirely offline inside the cluster.
Service:
`http://local-embeddings.factory-executions.svc.cluster.local:8080/v1`.
Native client must explicitly allow this exact private HTTP origin; arbitrary
HTTP/public destinations remain forbidden. Exactly 768 finite dimensions;
no fabricated, padded or fallback embeddings.

Backend acceptance covered semantic retrieval, real native vector migrations/
HNSW, idempotency, invalid dimensions, role DDL denial and denied ingress/egress.
The exact native embedding request with `dimensions:768` returned HTTP 200 and
768 finite floats. These are backend proofs, not actor authorization proofs.
Subsequent real native MemoryService proof seeded a public Paris fact in Agent A,
retrieved it there, and returned zero rows to distinct Agent B under the same
creator. A fresh native session of Agent A retrieved both the original and its
second explicit public proposal, proving cross-invocation retention. No remote
embedding provider was used.

Before business mutation, protected logical restore accepted 62 tables/2171 rows,
then an offline physical PVC copy was restored and checked for PostgreSQL system
ID, page checksums, amcheck, roles, ownership and ACL. We did not weaken pg_hba
after local replication was denied.

Physical backup:
`/home/nibeauli/kagent-lab/memory-canary/backups/offline-20261104T001407Z-156b42/postgres18.offline-pvc.tar`;
79,667,200 bytes; SHA-256
`85e00d10c9de97b782154b880c42593d7caf75c21cf0e2d77f76d144d116f4b7`.
Recorded database downtime 23.638 seconds; Ready at 41.771 seconds.
Original replicas and admission activity were restored; no work resubmitted.

Business PostgreSQL now uses:
`10.152.183.131:5000/kagent-lab/memory-postgres@sha256:4172ce00709b7cece0e6390cae8db5c00ecef4120965b43ced14e3dc7f73e273`.
Exact original Alpine 18.4 base, UID 70, PGDATA, entrypoint and existing PVC were
preserved. Image fully cached on all three nodes. `extensions.vector` 0.8.7 and
factory role USAGE are enabled; factory vector configuration is active.
After the controller rollout, real `factory.memory`, native
`factory.vector_schema_migrations`, and `idx_memory_embedding_hnsw` were verified.
Do not swap to an incompatible Debian data image or undo this by deleting data.

### Archive and harness

Promoted factory harness image, fully cached on all three nodes:
`10.152.183.131:5000/kagent-lab/factory-coder@sha256:e80e623c4557806465b58ae95545c6d27eb306efb2fcd7241bba75297bd9f7bf`.
Existing actor/runtime images remain pinned independently of controller updates.
The trusted controller remains on the verified `5d08...` image; its Go archive/
memory services are unchanged. All eight factory Agent revisions are Ready with
native `host-capabilities-llm` and `host-capabilities-agency` references, private
embedding egress, memory and archive flags. `gpt-5.6-sol`, original prompts,
approved host tool/profile policy and snapshot prefix are preserved. Native
`requireApproval:false` intentionally preserves the existing approval boundary,
rather than introducing an unrequested new approval requirement.

Promotion helper: `deployment\maintenance\promote_harness.py` (dry-run default).
It gates on real canary evidence, checks ownership/spec equality, refreshes only
status-changed versions, and requires paused admission for apply. Protected
rollback: `/home/nibeauli/kagent-lab/host-maintenance/native-before-20261104T031253Z.json`.
Original admission activity was restored and verified. No old session was
repointed and no business work replayed.

Flags are explicit: `AGENT_NATIVE_CONFIG=true`, `AGENT_SESSION_ARCHIVE=true`,
`SESSION_ARCHIVE_EXPORT_ENABLED=true`; native memory requires a validated
configuration file or JSON (mutually exclusive). SDK shared session-store and
payload telemetry remain false. Native compiled models/tools/approvals/timeouts
are consumed instead of replacing SDK or Agency with a new orchestration stack.

SDK logical events/results are reconciled from subscriptions/getEvents,
deduplicated and sanitized into append-only JSONL/manifests/durable outbox.
Private native RPC derives session/creator from verified runtime binding,
exports compressed JSONL plus canonical manifest to `bespoke-session-archives`,
and indexes `(session_id,checkpoint_hash)` in PostgreSQL. Actors hold no S3
credentials. Only an explicit exported ACK clears pending export.
The public real-model turn exported 38 SDK/operator logical events, including
user/assistant messages and real turn/status/usage events. Compressed JSONL,
content hash, canonical manifest hash and PostgreSQL index were fetched and
verified through temporary private operator access. SDK events contained genuine
nonempty dangling parent references; the manifest correctly remains `partial`
with `missing_parent_event`. Do not fabricate ancestry or replay inference to
change that flag. Operator-only probes honestly have no assistant turn.

Current bounds: cumulative exports over 4 MiB remain visibly pending; no chunks/
cursor API yet. Trusted controller currently reuses infrastructure RustFS
credentials, so bucket-scoped credentials/retention/independent backups remain
production-hardening work. Full private transcripts do not enter OTel.

Protected archive state: `/home/nibeauli/kagent-lab/factory-coding/archive`.
Schema/Secret/policy and controller rollout completed. The deployment is Available,
uses the verified `5d08...` controller image, and has the explicit archive config/mount.
The helper validates a fresh protected dump and uses resourceVersion optimistic
concurrency. Original admission states were verified restored after activation;
no business work was replayed.

## Validation and rollback

Source verification: host 29, reused LLM 57, reused ADO 38, broker 15, native ADO
21 tests; Node native config/archive/memory 29 combined; targeted Go managed
facade/controller suites passed. Fourteen memory deployment/restore guard tests
passed. Host tests were rechecked after the checkpoint (including the explicit
Python-supervision selector without skips); required formatting passed.
Immutable publish passed.
Public-data-only native actor/SDK integration passed. All seven exact canary
sessions were deleted, their host bindings revoked, and prospective paid canary
inference disabled. Private archives and evidence were retained.

Initial public-canary image `0d948ba00e94bf11cbb800f18d35f93593a4bbf60913fe64636556cc31efe98d`
was verified against the exact cached base, published privately, and cached on
all three nodes. Two new native Agents were Accepted/Compatible/EgressApplied/
Ready with independent golden snapshots; fresh sessions were created with native
idempotent request IDs. The first harmless metadata task failed **before SDK or
provider traffic**: the adapter rejected the real compiled agent-level
`session_db_url:"sqlite+aiosqlite:////data/sessions.db"` default. OpenAI
`api_format:"responses"` already worked; the initial line-number diagnosis was
incorrect. Actual HTTP MCP config also emits
`terminate_on_close:true`. Fixture tests had not covered these pinned compiler
defaults; the production adapter was corrected and verified against the actual
compiled JSON with networking disabled. It recognizes only the exact ADK-only
SQLite default without opening that database. Documented SDK `stopServer`
implements HTTP termination; actual pinned SDK/Agency reconnect/DELETE tests
passed with zero provider requests. Runtime projection must match `session-UUID`
for the Go UUID handoff; native startup derives only compiled provider/model and
clears conflicting aliases/credentials. Twelve focused tests passed.

Public-canary-only startup fixes removed CLI flags not accepted by the real
stock binary and supplied its documented offline-provider environment. Real
SDK start/session-create/getEvents passed in a network-disabled container before
the live turn; no database copying or private context was used.
Final public canary:
`aa154ae7c5dd539ed74778454d9e3fcd247008bf8eb3a1dbabfbe24e46658f03`,
source SHA-256 `7c00e2809aad3b6263cc67d329514e5b24bcb2ebd0c1b5fc2ab59e80458cb5a7`.
All old failed pins were retained until evidence collection, then only the seven
owned public sessions were retired. Native production code additionally fixed the
Go UUID/projected-name handoff and compiled Agency startup environment before
promotion.

Ownership-safe host stop:

```powershell
& Q:\src\tools\HostCapabilities\scripts\Stop-HostCapabilities.ps1 `
  -Config "$env:LOCALAPPDATA\HostCapabilities\host.production.json" -Apply
```

Disable the new current-SID logon task before reverting. Legacy task XML/process
metadata are under `%LOCALAPPDATA%\HostCapabilities\rollback`. Restore only the
recorded commands after new listeners are confirmed released; never kill by
process name or start duplicate port owners. Existing standalone source/binaries
and original credential stores were retained.

For archive rollback, use the protected `controller-before-<timestamp>.json`
as evidence for an ownership-checked reverse template patch; do not blindly
replace a live Deployment, replay sessions, drop additive tables, or overwrite
newer data from an old dump. Physical restore is an explicit outage/recovery
operation, not an automatic rollback action.

## Versioned rollback points

| Source | Baseline | Implemented checkpoint |
|---|---|---|
| `Q:\src\tools\HostCapabilities` | `9eecb6f` | `329ed41` (foundation `ffb9a97`), `users/nibeauli/consolidation` |
| `Q:\src\tools\ado-dev-proxy` | `20ee732` | `02a1dd8`, `users/nibeauli/consolidation` |
| `Q:\src\tools\LocalLlmGateway` | Previously clean `f302fe331738db6427dc2c97c4b4eee9de3be328` | `2add697`, `users/nibeauli/host-integration` |
| Selected kagent-lab integration | External source-only `84f9b8b` | `24f9589` (after `c6b6e31`, `50bf3e5`), `users/nibeauli/consolidation` |

External Git directory:
`C:\Users\nibeauli\.copilot\session-state\6b957f09-e5e3-455c-a84f-1dd271458b8b\files\kagent-lab-baseline.git`;
explicit work tree `Q:\tools\kagent-lab`. No runtime secrets, certificates,
node_modules or binaries belong in these commits. Consolidated documents now
have their own local source-only repository (`fc58cb9` baseline). No remote push
was performed.

## Overnight monitoring

All three nodes remained Ready through the 2026-11-04 06:14 PST bounded check.
Schedule #1 checks every 15 minutes until genuine user return or
2026-11-04 09:00 America/Los_Angeles. A scheduled prompt is not a user return.
Do not disable hibernation, reinitialize disks, rewrite quorum, or blindly replay
accepted work if a node sleeps. XL still holds local PostgreSQL/RustFS/registry;
three control-plane nodes do not make those datasets HA.

### 2026-11-04 06:31 PST: third-node heartbeat loss

- `cpc-nibea-4memo` last lease renewal was `2026-11-04T14:27:16.560907Z`;
  Ready became Unknown at `14:28:11Z` with `NodeStatusUnknown` / "Kubelet
  stopped posting node status." Unreachable NoSchedule/NoExecute taints appeared.
  Two bounded ICMP probes to `10.82.0.4` received no replies. This does not
  distinguish hibernation, host loss, kubelet failure or a network interruption.
- Primary and XL remain Ready and the Kubernetes API responds. Windows owner
  PID 6832 still owns all five original ports; LLM/ADO/Agency readiness is good.
  PostgreSQL and local embeddings were independently checked Ready (1/1).
- Nine pods on the unreachable node have Pod Ready=False, including the factory
  controller/UI, an original pilot worker, host OTel Collector and Tempo.
  Factory controller and Collector are 0/1 Available; Tempo is 0/1 Ready.
  Native session/memory/archive control and host telemetry delivery are therefore
  degraded despite healthy Windows listeners.
- Container-ready flags remained stale True while node/pod readiness changed.
  Subsequent monitoring must check the Pod Ready condition, not just container
  status. The earlier "Unhealthy: []" output at this tick was not end-to-end health.
- Evidence was recorded in the session database. No accepted work was replayed;
  no node rejoin, forced pod deletion, broad restart, disk initialization, quorum
  rewrite or host power-policy change was attempted. Continue bounded observation.

### 2026-11-04 06:49 PST: automatic partial recovery

- Third node remains Ready Unknown; primary and XL remain Ready.
- Kubernetes created Ready replacements on XL for factory controller
  (`factory-controller-7b76c7d78d-nmv8r`), UI (`factory-ui-687cb84c77-ks8f6`),
  and Collector (`host-otel-collector-bcf9c7684-zf5pl`). All three Deployments
  are now 1/1 Available. Old third-node pods are terminating.
- `tempo-0` is still terminating on the unreachable node; StatefulSet readiness
  remains 0/1. Collector recovery does not prove trace delivery while Tempo is down.
- Original pilot replacement `kagent-default-748f99c967-hw4bd` is Pending:
  one node has an untolerated taint and the other two fail topology spread
  constraints. No scheduling policy or replica count was changed to force it.
- PostgreSQL/embeddings remain 1/1 and Windows capabilities remain Ready.
  Recovery above was Kubernetes reconciliation, not manual pod deletion,
  resubmitted business work, or an inferred node wakeup.
