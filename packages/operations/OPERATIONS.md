# Three-host Kagent pilot

This is a model-free, cluster-private pilot, not a production or highly
available agent platform. Kagent and Substrate are pinned to `v1.0.0-rc1`.
The custom `deterministic-byo` agent replaces the LLM/framework layer while
retaining Kagent's private A2A/TaskStore and Substrate actor lifecycle.

## Placement and persistence

| Host | Kubernetes node | Responsibility |
|---|---|---|
| primary-devbox | cpc-nibea-8t76e | gVisor worker; recovery-test/backup destination |
| xl-devbox | cpc-nibea-2l35i | gVisor worker; PostgreSQL, RustFS, private registry |
| devbox-2 | cpc-nibea-4memo | gVisor worker |

Workers use a separately owned topology-spread field (`kagent-lab-placement`)
because the pinned WorkerPool API has no placement field for this constraint.
Operator reconciliation preserves it. Each worker requests 1 CPU/2 GiB,
limits 4 CPU/8 GiB; each default actor books 1 CPU/1 GiB.

The existing native ext4 VHDX backs `/mnt/microk8s-hostpath`.
`/var/lib/ate` is a guarded, shared bind mount into that disk.
PostgreSQL requests 10 GiB, RustFS 100 GiB, and the registry 5 GiB.
Hostpath capacity requests are **not quotas**. All three are initially
XL-local: loss of XL prevents new agent work despite three Kubernetes nodes.
No Ceph, extra VHD, external ingress, cloud resource, or model key was added.

Normal session settlement triggers automatic **VOLUMES** snapshots. It does
not preserve arbitrary process RAM, file descriptors, subprocesses, or an LLM
client's in-memory session. A bespoke harness must journal its continuation,
conversation, and real workspace files and reconstruct them after restore.
Task history is not general durable execution or exactly-once side effects.

The pinned controller enables golden preparation. Read projected actor
identity on every private storage call, and rebind cached state before SDK
admission. Never retain a golden/source session's task ownership in a fork.
The worker enables outbound DNS/egress only after its wakeup probe; readiness
must not depend on an outbound TaskStore recovery call. Admission must still
wait for safe recovery before it can create or execute a task.

## WSL restart and storage

The scripts no longer create recurring Windows disk-attachment tasks.
Primary has no such task. Final inspection found the legacy
`MicroK8s-Hostpath-Disk` task still registered on XL and devbox-2; stopping it
worked, but remote unregister returned Windows `Access is denied`.
Run this once in an **elevated Windows terminal on each of those two hosts**:

```powershell
Unregister-ScheduledTask -TaskName MicroK8s-Hostpath-Disk -Confirm:$false
```

After WSL/Windows restart,
run the existing host storage initializer from an **elevated** terminal on
each affected Windows host:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File Q:\tools\microk8s-storage\Initialize-MicroK8sHostpath.ps1
```

Do not provision another disk or format the existing disk. If MicroK8s remains
stopped after attachment, verify the existing mount, then start its guards
and services in WSL:

```bash
findmnt /mnt/microk8s-hostpath
systemctl start var-lib-ate.mount kagent-worker-storage-propagation.service
systemctl start snap.microk8s.daemon-containerd snap.microk8s.daemon-kubelite
findmnt -o SOURCE,TARGET,FSTYPE,PROPAGATION /var/lib/ate
```

The mount must be native ext4 and shared, not a Windows/DrvFS fallback.
The service guards intentionally stop MicroK8s when required storage is absent.

## Rebuild, install, and access

Windows package: `Q:\tools\kagent-lab`.
Linux artifacts/source: `/home/nibeauli/kagent-lab`.
Pinned source commits are checked by `build-bootstrap.sh`.
The isolated Go 1.27.3 toolchain and dependency cache were prepared during
installation; the bootstrap rebuild deliberately runs offline/read-only.

```powershell
& Q:\tools\kagent-lab\Invoke-LinuxScript.ps1 -ScriptPath Q:\tools\kagent-lab\build-bootstrap.sh
& Q:\tools\kagent-lab\Invoke-LinuxScript.ps1 -ScriptPath Q:\tools\kagent-lab\install-platform.sh
```

The installer preserves signing identities and generated RustFS credentials.
It uses the pinned local chart archives and upstream bootstrap library,
not the model-provider-dependent released CLI install path.
Only explicit dqlite `database is locked` Helm errors receive bounded retries.
That retry does not establish that the underlying datastore issue is fixed.
Read protected diagnostics before rerunning a failed operation.
The post-renderer sets Agentgateway's supported actor request-parking budget
to 60 seconds: its five-second default canceled cold restores before native
disk preparation/certificate issuance finished. This is bounded resume time,
not a replay policy or an extended task-execution timeout.

The harness source, dependency lock, build instructions, and OCI publisher are
under `byo-harness`. Publish only to the cluster-private registry and update
the digest in both `publish-byo-image.py` and `byo-agent.yaml` together.
The registry is ClusterIP-only; atelet rewrites the immutable
`localhost:15000/kagent-lab/deterministic-byo@sha256:...` reference to
`registry.kagent.svc:5000`. Registry transport is intentionally private HTTP.

For an interactive local session, use a manually started localhost-only
forward and stop it when finished:

```powershell
wsl.exe --distribution Ubuntu-26.04 --user root --exec /snap/bin/microk8s kubectl port-forward -n kagent service/kagent-controller 18083:8083 --address=127.0.0.1
```

Public A2A: `POST http://127.0.0.1:18083/agents/kagent/deterministic-byo`.
gRPC reflection is enabled on that same endpoint.
Create a session with `SessionService/CreateSession` and a stable `requestId`,
then send a message with that session's `contextId`. For continuation of a
parked task, retain `taskId` as well. Retry lifecycle mutations only with
the same input and request identity; `GetSession` only observes.
Retry A2A input only when its error explicitly proves
`KAGENT_SEND_NOT_ACCEPTED`, preserving the message identity.
There is no public SuspendSession/ResumeSession RPC in this release.
Use automatic quiescence and public checkpoint/fork APIs rather than
manipulating managed actors to claim a Kagent checkpoint.

**Authentication is intentionally insecure in this private pilot.**
Localhost forwarding does not secure the in-cluster API. Do not introduce
valuable source code, secrets, untrusted tenants, or external access until
real public authentication/authorization and agent egress policy are designed.
Private runtime storage still uses Substrate-injected actor JWTs.
Do not add gateway replicas and claim coordinated session ownership/HA.

## Private LLM and ADO dev proxy access

Helm release `agent-proxies` is installed in namespace `proxy-access`.
Source and reusable setup: `Q:\tools\kagent-lab\proxy-access`.
Packaged chart: `proxy-access\packages\agent-proxies-0.1.0.tgz`.
The scope exposes only
`Q:\src\tools\LocalLlmGateway` and `Q:\src\tools\ado-dev-proxy` to coding
actors. IMDS/token vending is out of scope; do not expose, reconfigure, or remove
the existing IMDS service as part of this integration.

Discovery on 2026-10-31 found the LLM gateway listening on the Windows WSL-facing
address `172.24.160.1:4141`. Primary WSL health returned 200 and unauthenticated
model discovery returned 401. The ADO proxy is listening on Windows loopback
`127.0.0.1:5480`, with token-free control health on `127.0.0.1:5481`
returning 200 from Windows. No tokens or inference were requested.
Those initial checks did not establish actor access. The integration now uses a
private TLS gateway plus the primary WSL host bridge, rather than exposing the
Windows listeners directly. The WSL-facing LLM listener address can change after
restart; inspect `bridge.json` and the existing Windows listener before restarting.

The LLM gateway supports bearer-key-protected `/v1/models` and
`/v1/responses`, including SSE and tool continuation. It keeps the GitHub
credential on Windows, defaults to four concurrent inference requests, and
does not replay inference. This is an unofficial Copilot subscription adapter,
not a supported billing API or a hard spending-budget service. Confirm permitted
automation and model entitlement before live use; routing all requests through
it does not by itself guarantee spend accounting or a monetary cap.

The ADO dev proxy obtains delegated ADO tokens directly through Windows WAM;
it does not need the separate IMDS HTTP broker. Tokens remain in memory/WAM.
It supports canonical Git smart HTTP (including push), Azure Artifacts npm
consumption/audit, and NuGet v3 restore through approved organizations/feeds.
ADO REST, package publishing, public registries, arbitrary tunnels, SSH Git,
and Git LFS are not supported routes. The example configuration selects MSASG
and the 1ES EngSys consumption feed; deployed configuration must be verified.
Configured organizations are not per-repository authorization, and reachable
callers share the backing Windows user's allowed dev-plane capability.

### Target boundary

```text
trusted Substrate admission -> opted-in actor in proxy-access
  model client -> authenticated model access route -> Windows LLM gateway
  Git/npm/NuGet -> runtime-local explicit proxy adapter
               -> authenticated CONNECT-capable transport
               -> primary WSL-to-Windows relay -> Windows ADO dev proxy
```

Upstream credentials stay on Windows. The primary WSL bridge reads the separate
LLM gateway key into RAM at startup; the cluster gateway holds only its own TLS
credentials. Actor JWTs are injected by Substrate's egress gateway, not copied
into actor volumes. The frontend verifies signature, issuer, audience, expiry,
and consistency of `actor.ate.dev` with the subject, then issues a RAM-only lease
bounded to five minutes. The adapter never writes leases to disk.

The release allows only the dedicated `proxy-access` atespace. **It does not grant
the existing `kagent` atespace access**, because the pilot's public Kagent API
remains unauthenticated. Do not broaden this grant to Kagent or untrusted callers
until admission and ownership authorization are secured.

**Front NuGet/npm rather than expose package-feed credentials.** Reuse the ADO
dev proxy's existing implementation, not a second package frontend. npm upstreams
must remain 1ES feeds only, with no public-registry fallback. Preserve canonical
Git URLs, project `.npmrc`, lockfiles, and `NuGet.Config`.

The ADO service is an explicit HTTP CONNECT proxy with HTTPS interception, not
an ordinary reverse-proxy REST endpoint. Install only its public `root.crt` in
opted-in actor images/runtime trust and configure OS trust plus
`NODE_EXTRA_CA_CERTS` for Node/npm. Never mount its state directory, DPAPI root
blob, signing key, Windows profile, or WAM cache, and never disable TLS validation
or install this synthetic root into Windows host trust.

Set container-scoped HTTP(S) proxy variables to the local adapter and keep ADO
hosts out of `NO_PROXY`. Route the LLM/private runtime endpoints separately; the
ADO proxy deliberately denies general/model traffic. Preserve runtime control,
DNS, TaskStore, and snapshot connectivity. Proxy variables alone are not an
egress boundary.

The ADO proxy authorizes socket IPs and strips `Proxy-Authorization`; a shared
relay IP therefore cannot prove actor identity. Authenticate the outer transport
before forwarding CONNECT traffic, with no unauthenticated bypass to the backend.
Its existing Docker-in-WSL bridge relay is not a three-host Substrate integration:
do not broaden its bridge INPUT rules or reuse its setup script on cluster
networking. Keep the control port 5481 local-only.

### Setup, client configuration, and teardown

Run on the primary Windows host; Linux root access is used through the existing
WSL distribution. No elevated Windows firewall change or polling task is needed:

```powershell
& Q:\tools\kagent-lab\proxy-access\Invoke-ProxyAccess.ps1 -Action Install
& Q:\tools\kagent-lab\proxy-access\Invoke-ProxyAccess.ps1 -Action Status
& Q:\tools\kagent-lab\proxy-access\Invoke-ProxyAccess.ps1 -Action Test
# Opt-in: the test below spends real inference quota once.
& Q:\tools\kagent-lab\proxy-access\Invoke-ProxyAccess.ps1 -Action Test -LiveInference
# Stop clients/agent work before taking their routes down.
& Q:\tools\kagent-lab\proxy-access\Invoke-ProxyAccess.ps1 -Action Remove
```

`Install` prepares protected certificates, starts the silent event-driven
`kagent-proxy-access.service`, refreshes public IdP keys over verified TLS,
publishes/reuses an offline-built immutable OCI image, preloads it into all three
nodes, installs Helm, and applies the reversible origin-trust overlay.
Lab values use `imagePullPolicy: Never`: node preloading avoids opening private
registry ingress or changing containerd registry configuration. If node image
caches are lost, rerun `Install`; ordinary TLS registries can instead use the
chart's configurable image settings.

Generated values/certificates and reports are root-only under
`/home/nibeauli/kagent-lab/proxy-access`. Private keys are not in the source
directory, Helm values, or runtime image. Only public CAs are baked into the image.
`Remove` uninstalls the release, removes its externally prepared TLS Secret,
restores the shared egress root path, removes the owned CA mount/ConfigMap, and
disables/removes the host service. It leaves existing Windows services, the empty
namespace/atespace, protected certificate state, and container image caches alone.
Retaining certificate state lets reinstall reuse trust rather than silently
rotating it.

The chart can also be installed with Helm using the generated non-secret values
and externally prepared Secret; the host bridge and egress-trust overlay still
need setup. The wrapper handles these non-Helm prerequisites and rollback.
The packaged chart contains the gateway/adapter implementation, not private keys
or the protected lab values.

Use full DNS names: actor DNS does **not** inherit Kubernetes Pod search suffixes.
The actor adapter uses:

```text
SESSION_URL=https://agent-proxies.proxy-access.svc.cluster.local:8443/session
DATA_URL=https://agent-proxies.proxy-access.svc.cluster.local:8444
GATEWAY_CA_FILE=/opt/proxy/gateway-ca.crt
EGRESS_CA_FILE=/run/ate/trust/trust-bundle.pem
NODE_EXTRA_CA_CERTS=/opt/proxy/ado-ca.crt
HTTP_PROXY=http://127.0.0.1:15480
HTTPS_PROXY=http://127.0.0.1:15480
```

Set lowercase proxy equivalents for tools that require them and exclude only
localhost and the private gateway from proxying; never exclude ADO hosts.
Local model access is `http://127.0.0.1:14141/v1`.
Project the built-in `egress-mitm.ate.dev` and `system-roots.ate.dev` trust bundles
at the configured egress file path. `examples\egress-policy.yaml` contains the
tested per-actor grant: inspected HTTPS on 8443 injects the signed JWT into an
existing Authorization placeholder, while 8444 is TLS passthrough for nested
CONNECT traffic. The logical JWT audience remains
`https://agent-proxies.proxy-access.svc/session`.

The nonroot .NET coding runtime also needs a writable HOME, XDG/cache directories,
and a per-actor `durableDir: {}` mounted at `/tmp`. The verified example in
`test_actors.py` initializes HOME with mode 0700 and sets `DOTNET_CLI_HOME`,
`XDG_DATA_HOME`, `NUGET_HTTP_CACHE_PATH`, and `NUGET_SCRATCH` under `/tmp`.
Without these, gVisor/.NET first-use can fail with `NuGet-Migrations` named-mutex
errors despite correct proxy connectivity. No global worker/runtime change or
new CSI driver is required.

Keep one gateway replica with the default RAM signing key. Restart invalidates
leases; the adapter obtains a new one for a subsequent request and never replays
an accepted model POST or Git write. TLS leaves expire after 90 days; the dedicated
CA lasts one year. Stop consumers before deliberate certificate rotation, update
external Secret/public image trust, and restart/revalidate. `RefreshKeys` refreshes
the pinned public IdP JWKS and rolls the Helm configuration. ADO CA rotation uses
the existing Windows proxy procedure, followed by rebuilding opted-in images.
Never install either synthetic root into Windows host trust.

The origin-trust overlay changes only Substrate's dynamic HTTPS backend root
path and mounts its exact original public root bundle plus the dedicated CA.
Uninstall was verified to restore the original config text byte-for-byte.
Reinstalling the base Substrate chart may overwrite that overlay; rerun the proxy
setup afterward. Back up protected certificate state and the external TLS Secret
securely if preserving this trust through disaster recovery.

### Verification and scope

Gateway authentication/streaming/CONNECT fixtures pass 22 tests in the nonroot
runtime image; eight host/overlay/OCI tests pass, including streamed chunked POST
reception with ambiguous framing and oversized uploads rejected before upstream
contact. Strict Helm lint and chart packaging pass. Authenticated host-bridge
model discovery and NuGet access passed, anonymous TLS/session/model access is
rejected, and full uninstall/reinstall passed.

Actual actor acceptance passed on all three workers on 2026-10-31: each discovered
30 models, read the 12-resource NuGet index, fetched TypeScript 7.0.2 metadata from
the approved 1ES feed, restored Microsoft.Identity.Client 4.91.0 into a cold cache,
and received explicit 403 denial for public npm and ADO REST. One bounded live
Responses SSE request completed with `PROXY_OK`; the fixture requests no upstream
storage and never automatically retries inference. Cold restoration produced a
new boot ID and fresh RAM authorization with the same model count. Native
wrong-audience identity returned 401. Owned actors and templates are deleted by
the runner.

The protected `actor-acceptance.json` report records worker-level results.
Accepted immutable runtime:
`sha256:3b28acf1b404dd5a39decb0ab649753f112f04cd7634ca3976f9cb5bebc2a63e`.
The installed root-only host bridge is separate from that image and is refreshed
by `Install`. Per-worker result files retain partial successes if a later check
fails; failures identify their stage without logging request bodies or tokens.

### Security and operational constraints

Keep the ADO proxy/WAM in the signed-in Windows user session. Expired host
authorization requires recovery through the existing Windows services, not actor
credentials or IMDS fallback. Preserve configured organizations, feeds, download
hosts, and client allowlists. No recurring Windows polling task was introduced;
the primary WSL service is event-driven. The Windows listener address, existing
proxy startup, and user sign-in remain host recovery dependencies.

This exposes reusable transport and a tested actor configuration; it does not
replace the bespoke coding harness or secure the existing Kagent public API.
Keep the dedicated atespace isolated. New actor grants need explicit trusted
admission and the supplied runtime egress policy; ordinary Pod NetworkPolicy and
proxy environment variables alone do not cover Substrate/gVisor networking.
Do not replay accepted inference or Git writes, add provider fallback, expose
control port 5481, or expose IMDS.

Live validation was read-only apart from the explicitly approved inference.
Git push, package publication, forced WAM reauthentication, certificate rotation,
and primary-host failure were not exercised against live upstreams. Git push is
an existing shared Windows-user capability, not per-repository authorization.
Primary Windows is a model/dev-plane availability dependency; three Kubernetes
workers do not make these host services HA.

The original proxy-exposure deployment preserved the Windows configurations.
The first coding-agent setup subsequently added only four exact SDK dependency
CDN hosts to the ADO proxy: `vsblobprodwussu3shard15.vsblob.vsassets.io`,
`vsblobprodwussu3shard28.vsblob.vsassets.io`,
`vsblobprodwussu3shard36.vsblob.vsassets.io`, and
`vsblobprodwussu3shard58.vsblob.vsassets.io`. Each was observed as a redirect from
an approved 1ES SDK dependency request. Its existing signed-URL capability check,
CA, account settings, and listeners remain unchanged. The same Windows ADO
service was restarted in the background to load these entries; no scheduled task
was added. LLM gateway and IMDS configuration remain untouched.
This is trusted dev-plane access, not a permission-reduction layer,
production multitenancy boundary, hard spend budget, or host-service HA.

Audit UI dependency restoration subsequently recorded 51 additional denied
Azure Artifacts download shards. Those exact hosts were added to `downloadHosts`;
the resulting set is `vsblobprodwussu3shard1` through `shard60` under
`.vsblob.vsassets.io`; shard31 was added only after a later serialized restore
observed its denial. No wildcard, public npm
registry, new feed, or credential projection was added. Existing signed-URL
capability checks remain required. The same Windows ADO service was restarted
in the background; its CA, account configuration, and listeners were preserved.

## First coding agent: engsys-coder

Source and operator entrypoint: `Q:\tools\kagent-lab\engsys-coder`.
The agent runs as a nonroot gVisor Substrate actor, `proxy-access/engsys-coder`,
using the official Node.js Copilot SDK pinned to `1.0.28` and its bundled Linux
runtime. Dependency lock URLs are restricted to the approved 1ES EngSys feed.
No npm lifecycle scripts, public-registry fallback, runtime download, GitHub login,
or GitHub credential projection is required.

This is a private native actor with a small task HTTP interface, **not a public
Kagent/A2A registration**. The unauthenticated Kagent controller admission path is
not granted access to this capability. Invoke through the existing authenticated
Substrate administrative client; the wrapper opens a bounded localhost-only
forward and closes it afterward.

Every SDK session, including cold resume, explicitly receives the custom provider:
`type: openai`, `wireApi: responses`, `baseUrl: http://127.0.0.1:14141/v1`,
model `gpt-5.6-sol`. The actor-local adapter forwards to the authenticated cluster
gateway and mTLS primary-host bridge, then **this Windows host's LLM gateway**.
The SDK has no direct-provider credential or fallback. Its provider key is only
a nonsecret loopback placeholder; real frontend leases remain in adapter RAM.
Copilot telemetry and cross-session discovery are disabled for this actor.

The repository is
`https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys`
(the project spelling is **Fundamentals**), default branch `main`.
First use makes a shallow, single-branch clone into `/workspace/engsys` through
the ADO proxy, disables credential helpers and Git hooks, and skips LFS smudging.
Existing repository origin is checked before reuse. Windows WAM retains the
actual Git/feed authorization.

Six custom tools provide repository status, tracked path listing, bounded UTF-8
file reads/writes, local diff, and `git diff --check`. Filesystem paths are confined
to the checkout; traversal, symlink escape, `.git` access, binary reads, and oversized
file contents are rejected. Only registered custom tools receive SDK approve-once
decisions; shell, MCP, web, unregistered tools, and managed human-review requests
are not automatically approved. Each turn has a 24-tool-call budget and bounded
execution time.

Requests are **read-only by default**. `-AllowEdits` enables local file changes and
a local `agent/engsys-<task-id>` branch when starting from `main`. There are no
commit, push, PR, or arbitrary shell tools. The local diff is the handoff artifact.
General repository-native build/test execution is unavailable; the explicit
Audit UI exception below runs only that frontend's fixed scripts in staging.

```powershell
$coder = 'Q:\tools\kagent-lab\engsys-coder\Invoke-EngsysCoder.ps1'
& $coder -Action Status
& $coder -Action Invoke -TaskId inspect-build-001 -Prompt 'Inspect the build configuration and summarize it.'
& $coder -Action GetTask -TaskId inspect-build-001
# Opt-in local edits only; no commit or push:
& $coder -Action Invoke -TaskId small-edit-001 -AllowEdits -Prompt '<specific small change>'
# Snapshot volumes without discarding the workspace:
& $coder -Action Suspend
# Rebuild from prepared locked dependencies, snapshot, repoint template, resume:
& $coder -Action Upgrade
# Fresh install or observe the owned existing instance:
& $coder -Action Install
# DESTRUCTIVE to this actor's workspace: export/review local changes first.
& $coder -Action Remove
```

Initial dependency preparation on Windows uses the existing loopback ADO proxy,
its public CA via `NODE_EXTRA_CA_CERTS`, and
`npm ci --ignore-scripts --os=linux --cpu=x64 --libc=glibc` from the source directory.
`Install`/`Upgrade` build offline from those prepared artifacts, publish a verified
immutable image to the private registry, and preload all three nodes.
Generated image/ownership manifests and operator reports are root-only under
`/home/nibeauli/kagent-lab/engsys-coder`.

Workspace, SDK state (`/workspace/copilot`), and task journal (`/workspace/tasks`)
are durable actor volumes. Stable task IDs deduplicate identical input; conflicting
input with the same ID is refused. Use `GetTask` after an ambiguous client failure,
not a new task ID and blind resend. An interrupted accepted/working operation is
marked interrupted after restart and is never automatically replayed. A new turn
resumes the SDK conversation with fresh provider/tool/permission registrations.
One task runs at a time. Volume snapshots do not preserve arbitrary process RAM
or guarantee exactly-once edits, and deleting the actor is not a backup/restore
procedure. Do not fork this first instance and assume coordinated ownership.

The native worker wakeup endpoint requires HTTP 200; the harness returns 200
locally without waiting on repository, SDK, or outbound recovery. Operator cold
readiness uses a bounded 30-second HTTP attempt rather than canceling startup
every three seconds.

### Coding-agent validation

Four focused tests pass: scoped SDK approvals, checkout path/symlink containment,
edit opt-in/size limits, and bounded root-first listing of a large nested tree.
On 2026-11-01 the actual SDK executed `repo_status` and `repo_list` against engsys
through the host gateway. It reported clean `main` at
`0f39747d4b8ba30a673ea7d4ca0d6d40e943a808` and identified
`Directory.Build.props`, `Directory.Build.targets`, `Directory.Packages.props`,
`global.json`, and `package.json`. The gateway recorded HTTP 200 model responses
during that turn without logging inference bodies. No live file edits, commits,
pushes, or repository-script execution were performed.

Volume-preserving upgrades changed the actor boot ID while retaining the checkout,
SDK state, and earlier task receipts; the successful turn ran after restoration.
Resubmitting its identical task ID/input returned the exact terminal receipt;
conflicting input returned HTTP 409 without new execution. Earlier diagnostic
receipts remain for transparency: a completed SDK turn alone does not establish
that requested tools succeeded.

Accepted coding image:
`sha256:5130d7f69ed7a192319394d9af28eea2f7c672e73489fdbef05ee833dd229237`.
Unchanged builds still republish/verify the retained OCI layout and preload all
three node caches. `Install` preserves an owned actor rather than silently
upgrading it; use `Upgrade` when the prepared image differs. Upgrades explicitly
resume the updated actor before opening its HTTP forward; short forwarded
requests can otherwise repeatedly cancel a cold restore.

### Operator-authorized Audit UI execution

`-AllowAuditUi` registers the additional `audit_ui` tool for that task only.
Its fixed operations are `prepare`, `install`, `build`, `test`, `smoke`, and `diagnose`.
There is no command-string, project-path, provider, environment, or port override.
The run authorization participates in task identity; changing it for the same ID
is refused rather than replaying an earlier read-only request with more capability.
The original six tools and read-only default are unchanged.

The tool copies only tracked SPA files from
`src/MAI.EngSys.AuditUI/MAI.EngSys.AuditUI.Spa` to `/workspace/audit-ui/spa`.
It does not copy ignored local secrets, credential caches, or node_modules.
Public npm tarball URLs in the staging lock are remapped to the existing approved
1ES feed while preserving locked versions and integrity hashes. The original
checkout and lock remain unchanged. The checked-in compact lock omits download
URLs/integrities for many transitive packages; `prepare` resolves only their exact
already-locked versions through the approved feed and caches those metadata
entries against the source lock digest. It does not choose new versions.
Other hosts/feeds, URL credentials, and signed
query strings in dependency locks are rejected.

Restoration prefetches compatible tarballs through four bounded verified streams
using npm's installed `pacote` library and the authenticated proxy, skipping
already-cached locked integrity hashes. An explicit proxy-aware npm agent and
`Connection: close` disable download keep-alive; the registry-fetch layer drops
a standalone `keepAlive` option. Each CDN tunnel closes before reusing its slot.
It then runs
`npm ci --offline`. This avoids exhausting the bounded proxy tunnel pool when
one registry redirects concurrently across many CDN shards. No lifecycle scripts,
credential provider, PAT, direct-registry fallback, or Windows credential files
are needed. Source build/test scripts are explicitly authorized by this flag;
they remain nonroot, actor-isolated, and subject to the existing egress policy.

```powershell
$coder = 'Q:\tools\kagent-lab\engsys-coder\Invoke-EngsysCoder.ps1'
& $coder -Action Invoke -TaskId audit-prepare-001 -AllowAuditUi -Prompt 'Run audit_ui prepare once and report the result.'
& $coder -Action Invoke -TaskId audit-restore-001 -AllowAuditUi -Prompt 'Run audit_ui install once and report the result.'
& $coder -Action Invoke -TaskId audit-smoke-001 -AllowAuditUi -Prompt 'Run audit_ui build, then smoke if build succeeds. Report exact results.'
```

The smoke operation starts Vite at actor-loopback `127.0.0.1:15173`, checks the
HTML shell and transformed JavaScript entry, terminates its owned process group,
and verifies the listener closes. It does **not** leave a preview service running
or prove browser rendering, Entra sign-in, backend API, or database functionality.
Full Audit UI local startup additionally needs SQL, Azurite, Kusto, and the
documented interactive identity; those are not silently provisioned in this actor.

Each npm command has a five-minute limit; bounded cache preparation is limited
separately. Task waits are longer only with this explicit run flag. Sanitized
operation results are persisted under `/workspace/audit-ui` and in task receipts;
raw npm cache logs stay inside the private actor workspace. Diagnosis redacts
complete URLs and capability query fragments before truncating log output.
The operator command also exits unsuccessfully when a completed SDK turn contains
a failed Audit operation; SDK turn completion alone is not application success.

### Audit UI validation

On 2026-11-01 the actual SDK restored, built, and started the frontend on clean
`main` at `0f39747d4b8ba30a673ea7d4ca0d6d40e943a808`.
`audit-explicit-agent-install-20261101` installed 406 packages with offline npm ci,
after 284 verified tarball downloads and reuse of 127 cached integrity hashes.
`audit-frontend-build-smoke-20261101` passed `tsc -b && vite build`, transformed
1,414 modules, and produced the staged production bundle. Third-party pure
annotation and large-chunk warnings were nonfatal.

The same SDK turn started loopback Vite, received HTML HTTP 200 and transformed
`/src/main.tsx` JavaScript HTTP 200 (1,891 bytes), then stopped Vite and confirmed
the listener closed. No checkout edits, commits, pushes, backend services, or
interactive sign-in were performed. This is frontend build/start evidence, not
full authenticated Audit UI functionality. Six focused harness tests also passed.
The checkout, installed staging dependencies, source-digest metadata cache,
production bundle, SDK session, and operation/task receipts remain actor-local
and persistent.

Accepted Audit-capable image, preloaded on all three nodes:
`sha256:bf4ed0225709c0a9675273ef0ab1d2b4af401e841489e5ab8f7bb4126036e840`.

## Agency contributor coding actor

**Retired standalone integration.** The following pilot description is historical,
not an installation or invocation procedure. Use the existing managed Kagent
`factory-coding` native Harness/AgentTemplate described below; do not reinstall
`proxy-access/engsys-agency-coder`. `operate.py`, `Install-EngsysAgency.ps1`, and
`Invoke-EngsysAgencyCoder.ps1` now fail explicitly without lifecycle operations.
The shared `agent.mjs` requires `AGENT_NATIVE_CONFIG=true` and a validated
`AGENT_COMPILED_CONFIG_PATH` before workspace/runtime startup. There is no
standalone model, MCP, permission, or credential fallback.
`build.py` and the base Dockerfile remain packaging inputs for factory/native
canary images; building that base is not permission to deploy a standalone actor.

`proxy-access/engsys-agency-coder` was the separate, write-capable Agency pilot.
The original `engsys-coder`, its accepted image and useful workspace/caches are
unchanged; its earlier read-only description above does not apply to this pilot.
The new actor uses Copilot SDK `1.0.28`, standalone Copilot CLI `1.0.105`, and
pinned Linux Agency `2026.10.29.2`. All restored npm packages use the approved
1ES feed, not public npm.

Inference still goes exclusively through this Windows host:
SDK Responses `127.0.0.1:14141` -> actor adapter -> authenticated cluster gateway
8444 -> primary mTLS bridge 15441 -> Windows LLM gateway 4141.
There is no guest provider credential, IMDS projection, or direct-provider fallback.

MCP uses an explicitly configured HTTP server, not transparent TLS interception:
SDK `127.0.0.1:15442/agency/mcp` -> actor adapter -> authenticated cluster gateway
8444 -> primary mTLS bridge 15442 -> fixed Windows relay -> loopback host broker
15442 -> **one shared Agency gateway process**. JSON-RPC IDs are multiplexed;
logical sessions have separate actor/profile/loaded-instance maps. Disconnect
and cancellation affect the logical client, not the shared gateway process.
Compiled Agency built-ins are used; arbitrary repository MCP commands are not
launched on the host.

### Host profiles, authentication, and plugins

`agency-broker\profiles.json` defines approved instances, exact nested tool names,
marketplaces, and repository/project write scope. The default `engsys-contributor`
profile enables ADO `{"organization":"msasg"}`, Bluebird, EngHub, and WorkIQ.
ADO repository writes are scoped to engsys; work-item writes verify
`Engineering Fundamentals` before forwarding. WorkIQ currently exposes its
search/read/ask subset. Fluent and Kusto have no initial profile authorization.
This pilot's checkout is fixed to engsys; other repository actors need their own
trusted checkout configuration as well as an appropriate host profile.

Only registered UID/subject pairs get the Agency lease capability. The cluster
gateway derives actor identity headers; actors cannot select another profile by
supplying headers. The protected Windows bridge key remains on Windows, never in
the guest/cluster. The authenticated policy response supplies approved instance
arguments and tool names, plus host-approved Git identity, to the actor.

Register a different agent without launching another host gateway:

```powershell
python Q:\tools\kagent-lab\agency-broker\configure.py --actor-uid <owned-uuid> --subject actor/<space>/<name> --profile <profile-name>
# Optional host-approved automation authorship overrides:
# add --git-author-name <name> --git-author-email <approved-email>
Stop-ScheduledTask -TaskName 'Kagent Agency Broker'
Start-ScheduledTask -TaskName 'Kagent Agency Broker'
wsl.exe --distribution Ubuntu-26.04 --user root --exec python3 /mnt/q/tools/kagent-lab/proxy-access/manage.py configure
wsl.exe --distribution Ubuntu-26.04 --user root --exec python3 /mnt/q/tools/kagent-lab/proxy-access/manage.py deploy
```

Re-registration preserves existing profile/author settings unless explicitly
overridden, and replacing the same subject's UID removes its stale binding.
Git identity defaults to the host's configured Git author on first registration;
agents cannot override it through tool arguments. It is stored in protected
broker state, not the public UID binding export. The repo rejects placeholder
author emails (`VS403702`); use an approved host identity rather than weakening
that policy or automatically rewriting commits.

The hidden continuous `Kagent Agency Broker` scheduled task has a logon trigger
and bounded failure restarts, not recurring polling. Restart it to activate
broker/profile changes. A restart closes the owned gateway/backend process tree;
the next client initializes a new single shared gateway.

The actor's checkout does not need an Agency config. The coding request may name
a checkout-relative working directory; the host verifies every selected
`agency.toml` or `agency.yaml` from checkout root through that cwd against the
pinned repository commit. TOML wins over YAML in one directory, closer layers
win, and sibling projects are not consulted. Agency itself parses and merges
the TOML/YAML layers, including validated `remote_config` layers; the broker
records bounded source paths, revisions and content hashes without returning
source text. No file still means normal empty repo configuration while host
policy and base capabilities remain mandatory.

Existing root-only clients may continue to send `{commit, configCommit,
configText}` during migration; the broker still checks the text against the
authoritative root file. New clients send only `{commit, workingDirectory}` and
cannot supply plugin config or forged source records. The broker fetches
approved marketplace plugins and dependencies and returns content-hash bundles.
No guest Azure token is needed. ZIP confinement, hashes, marketplace/dependency
policy and execution declarations are checked; unsupported hooks, MCPs,
extensions or artifacts fail closed, not silently dropped. Mutable remote refs
are not reused from the broker's preparation cache.
The current AIX SessionStart hook/activation files have exact reviewed hashes.
Skills and hook roots are explicitly supplied to the SDK because its empty mode
does not automatically discover downloaded plugin skills.

Bundles are atomically extracted under `/workspace/agency/bundles/<sha>`.
Warm reuse requires unchanged configuration, checkout HEAD, selected working
directory, host policy and profile identity; `-RefreshPlugins` reacquires at a
task boundary. The current
repository selects `mai-engsys` and `AIX-ES-Standards`. Actual available skill names
are `enghub-search`, `find-proof-of-presence-prs`, `init`, and
`review-merlinbot-comments`, without marketplace-qualified prefixes.
Script-oriented skills do not grant an unrestricted shell.

### Operation, recovery, and contributor behavior

The former standalone install/invoke scripts are retired. Submit and observe
work through the managed factory's public Kagent task/session interface instead.

Edits and contributions are enabled by default. `repo_commit` commits only
explicit files, preserves unrelated changes, checks whitespace and appends
Copilot/session trailers. `repo_push` verifies both fetch and effective push URLs,
then normally pushes an agent branch; it never force-pushes or pushes main.
`repo_start_branch` starts a fresh task branch from local main only with a clean
checkout, preserving prior branches/commits. ADO PR creation/update/comments and
work-item creation/update/comments/links are profile-controlled useful writes,
not blanket read-only operations.

`-AllowAuditUi` enables only the bounded staged frontend operations described
above. Normal tasks have 24 tool calls and a five-minute SDK turn boundary;
Audit tasks have a fifteen-minute boundary. Broker bodies are bounded to 8 MB,
with four service slots and one plugin-preparation slot. Cold restore can exceed
one bounded prewarm pass: inspect the failed receipt, then request a new install
task that reuses verified cache. Do not fall back to public npm or enable lifecycle
scripts. Build annotation/chunk-size warnings observed in dependencies are
nonfatal; no unrelated dependency fixes were made.

Private task/operation receipts and SDK/workspace caches are retained in the
actor. Exact matching task/runtime identity is deduplicated; changed inputs or
runtime identity conflict with HTTP 409. SDK turn completion is not proof of
requested external success: inspect tool results/answer and upstream state.
The operator exits unsuccessfully for failed recorded Audit/push operations.
Use `GetTask` after a transport timeout; do not blindly replay accepted writes.
Interrupted tasks are never automatically replayed on cold boot.

Host/profile outages fail closed before admission, including with warm plugin
bundles. If admission returns 503, inspect task ID first: an absent task can be
resubmitted after restoring the host; accepted/working tasks require inspection.
Short-lived RAM leases can also require a fresh request after gateway restart.
MCP cancellation is actor/request-scoped; timeout/cancellation is not a guarantee
that an already accepted remote write was rolled back. This is trusted development
authority, not production multitenancy, exactly-once writes, or host-service HA.

### Verified Agency acceptance (2026-11-02)

Accepted pilot image, published and preloaded on all three nodes:
`sha256:dd8fdc38a52744eceabc4d4e999e76801dac6290f8fc1a2ac3fef98e959b369f`.
The full installer reran successfully, preserved the actor/workspace, reused the
immutable image, and deployed proxy Helm revision 6.

| Evidence | Verified result |
|---|---|
| Real SDK through Agency, network-disabled runtime probe | External stdio connection and explicit skill-root discovery |
| `agency-profile-skills-20261102` | Actual ADO engsys read, Bluebird metadata, EngHub search and WorkIQ `list_agents` |
| `agency-explicit-skill-20261102` | Installed `enghub-search` skill invoked, then host EngHub search/fetch |
| `agency-contributor-final-20261102` | `init` skill, explicit-file commit, real normal push, draft PR 7327930 |
| `agency-work-item-20261102` | Task 11456626 created at revision 1 and updated with `Test /rev` to revision 2 |
| `agency-cleanup-artifacts-20261102` | PR description/thread updated and PR abandoned; work item revision 3, Closed |
| `agency-audit-install-resume-20261102` | 406 packages installed; 157 verified downloads plus 254 retained cached tarballs |
| `agency-audit-build-smoke-20261102` | 1,414 modules built; HTML/entry HTTP 200; listener stopped |
| `agency-refresh-20261102` | Explicit refresh retained the verified plugin identity and all four skills after cold resume |
| `agency-post-recovery-20261102` | Actual ADO read and warm Audit build/smoke after broker outage and cold actor restoration |
| Shared-process and transport tests | Two logical clients reuse one PID; disconnect keeps it; cancellation IDs stay actor-local |
| Lifecycle/operator checks | Warm dedup, changed-input 409, fresh cold boot with receipts/branch preserved, host outage 503 with no task admitted |

Upstream verification independently checked the pushed file and commit
`b9d769150385171caf9b1e96473c6b2a8d6f733a`, PR description/thread/abandoned status,
and work-item Closed state/revision. The remote acceptance branch is deliberately
retained as an audit artifact; nothing was merged. The earlier rejected
placeholder-author commit remains on its separate local-only test branch,
without amendment or publication.

Original actor UID `4dba5c63-b4c8-4ec5-8850-4cd539980678` and accepted
`bf4ed022...` image were preserved. Regression checks passed 27 Node transport/auth
tests, eight Python bridge tests, 11 broker tests, three pilot tests and strict
Helm lint. Audit validation is frontend build/start evidence only; authenticated
backend, interactive sign-in and browser rendering are not verified.

## Acceptance and monitoring

Run the persistent model-free live checks, not just pod readiness:

```powershell
wsl.exe --distribution Ubuntu-26.04 --user root --exec /usr/bin/python3 /mnt/q/tools/kagent-lab/test-byo-live.py
wsl.exe --distribution Ubuntu-26.04 --user root --exec /usr/bin/python3 /mnt/q/tools/kagent-lab/check-observability.py
```

The live helper checks independent contexts, physical workspace preservation,
automatic durable cold restoration, parked input/simulated approval,
checkpoint/fork isolation, committed task reads, and cancellation.
The approval command is a protocol fixture, not credential authorization.
Human approval therefore parks as INPUT_REQUIRED with `parked.kind=approval`.
The pinned A2A Go SDK does not finalize AUTH_REQUIRED execution for cleanup,
so emitting that state left public tasks SUBMITTED indefinitely. Legacy
auth-required receipts can be recovered, but the fixture no longer emits them.
`test-byo-workers.py` separately places nine concurrent bounded child tasks
across the three four-CPU worker capacities and checks actual owning nodes,
then cancels/reaps and removes only its own test sessions.
Acceptance reports are root-readable files under
`/home/nibeauli/kagent-lab/protected`; a missing or FAIL report is not success.
The raw runtime's cold and cross-node reports distinguish actual file
preservation from process-memory claims.
Final verification passed 18 harness tests, the complete live suite, and nine
concurrent WORKING tasks across all three hosts. Test sessions/checkpoints and
raw fixtures were removed after recovery; the managed agent remains Ready.

Prometheus has six valid authenticated/allowed pilot targets: Kagent
controller, two Substrate API pods, and three atelets. The declared Substrate
controller metrics port does not listen in this release/profile and is not
monitored. Loki receives stdout/stderr via the existing Promtail deployment.
Tempo receives traces only, with upstream default sampling (about 1%).
No OTLP log or metric stream is sent to Tempo.

## Coordinated backups and isolated recovery

After live acceptance passes and all test sessions reach durable suspension:

```powershell
wsl.exe --distribution Ubuntu-26.04 --user root --exec /usr/bin/python3 /mnt/q/tools/kagent-lab/backup-and-test-recovery.py
```

This is a short **maintenance interruption**: it stops pilot database writers,
captures logical PostgreSQL data/roles plus signing/configuration resources,
hashes objects through authenticated S3, stops RustFS/private registry, and
copies their actual data from XL to primary's native disk. It restores original
deployment replica counts in `finally`. Never run while external work is being accepted.
`--boundary-report byo-lifecycle-remainder.json` can select the separately
verified completed checkpoint/fork boundary; this does not substitute for
passing the full BYO acceptance suite. The helper additionally rejects every
active actor and every unresolved task before stopping anything.

Protected backups are at
`/mnt/microk8s-hostpath/kagent-lab/backups/<UTC timestamp>`.
This gives a second host copy, not an off-site or Windows-host disaster backup.
Backups contain signing keys and credentials; protect and encrypt any later
export and never upload them to a public registry/repository.
Keep PostgreSQL, snapshot objects, signing identities, and configuration from
the **same completed boundary** together.

The recovery helper creates a temporary ingress-isolated namespace on primary,
restores the logical database into a separate PostgreSQL instance, restores
the actual RustFS archive into a separate directory, and compares every
application table's content and every S3 object's SHA256. It also restores
the private registry and verifies the immutable BYO manifest bytes/digest.
It removes its own temporary namespace but retains the protected backup.
This verifies database/object/private-image recovery, **not a complete recovered Kagent
control-plane/session invocation after total cluster loss**.
That larger disaster-recovery drill still requires a separate cluster,
restored signing identities, pinned images/config, and real restored-session
invocation before production use. Never restore over live pilot data as a test.

Verified backup: `20261031T154641Z`, with 42 restored application tables,
55 restored snapshot objects, and restored BYO manifest
`sha256:9dbe4365efa1657aecbc51faf293512b6695b0a8c2e36bb14942f2194333f9e0`.
Idempotent replica scaling has narrowly bounded retries for the same dqlite
lock error. Intermittent MicroK8s datastore locking was observed again during
maintenance; its root cause is unresolved, even though the final run passed.

## Rollback boundaries

Original MicroK8s arguments and datastore backups exist on every host at
`/var/snap/microk8s/common/kagent-lab-backup-20261031`.
Do not restore an HA member's raw datastore independently into a running
three-member datastore or disable certificate APIs used by live workloads.

Before removing the pilot, stop admission, cancel/settle work, take a verified
coordinated backup, and delete owned sessions/checkpoints through Kagent.
Remove pilot Helm releases in reverse dependency order only after checking
for other consumers. PostgreSQL was created by the bootstrap; it is not
automatically removed with the Kagent chart.
Keep retained PVs, disks, signing secrets, backups, shared CRDs, networking,
observability, and all unrelated workloads intact.
Remove only the pilot's named bind-mount service/drop-ins after workers no
longer depend on them; restore host arguments one node at a time only if
certificate prerequisites are no longer needed. Never automate disk deletion
as rollback.

## Retired Argo-backed factory pilot

This section is historical only. The live factory no longer uses Argo or the
SQLite ledger described below. Current operation is documented in
**PostgreSQL-backed A2A coding gateway** at the end of this file.

The isolated factory exposes one coding definition, not one agent definition per
task. Private Argo v4.1.6 runs in `factory-workflows`, with no public server or
ingress. The authenticated Kagent controller, namespace-local two-worker pool,
`factory-coder` Agent, and reusable `factory-coding` Harness/AgentTemplate are in
`factory-executions`. Coding work executes in Kagent/Substrate actors; the Argo
pod only submits/observes it. Current placement uses the Ready primary/XL hosts.
The pre-existing Agency pilot on NotReady devbox-2 remains unchanged/unavailable.

The service is `https://10.82.0.2:18443`, enabled as primary-WSL
`factory-coding.service`. Its `/a2a` JSON-RPC operations are `SendMessage`,
`GetTask`, `ListTasks`, and `CancelTask`; polling is supported, streaming/push
notifications are not. The Agent Card is `/.well-known/agent-card.json`.
`/readyz` is process liveness, not proof of upstream or execution capacity.
TLS verifies the existing private CA; do not use insecure client overrides.
Bearer secrets live only in root-private state under
`/home/nibeauli/kagent-lab/factory-coding`. Never copy management secrets into
actors, workflow arguments, shell arguments, or source files.

Each stable caller/message ID owns one durable task and Workflow. A request has
one part, either text or structured `data` containing `prompt`, `allowEdits`,
`allowAuditUi`, `refreshPlugins`, and optional `continuation` (a completed owned
task ID). Repository/profile are fixed to the approved engsys contributor.
Set `allowEdits:false` for read-only tasks; the inherited contributor default is
true. Audit UI and plugin refresh default false. Maximum prompt is 16,000
characters; backlog is 100 queued requests. Test fixtures require a separately
authorized credential. Cross-owner task lookup/continuation is denied.

Independent tasks create fresh sessions. Explicit continuations reuse the
completed task's session/context and serialize against other active work there.
Normalized request identity includes the immutable runtime and internally pinned
WorkflowTemplate. Conflicting reuse of an ID is rejected; identical retries
return the original task. Do not manufacture new identities after an ambiguous
response. `ListTasks` supports owner-bound pagination and optional artifacts.

The shared `factory-coding-limit` semaphore and independent ownership ledger both
cap admission at three, including preparation, dispatch, and uncertain outcomes.
Argo's global 600-second default is explicitly overridden by each task's
3,600-second deadline. Synchronization belongs at workflow scope: nesting a
template semaphore under the global workflow semaphore self-deadlocks. Template
names are versioned, with `-v3` identifying the workflow-scope correction.
Workflow TTL is one day; pod GC occurs on workflow completion.

**Pilot deviation:** ownership uses primary-local SQLite WAL/FULL, not the plan's
preferred separate PostgreSQL ledger. This is single-primary, not HA. Kagent
uses an isolated `factory` PostgreSQL schema/roles in the existing XL-local TLS
database. Its SCRAM connection uses `verify-full` and explicitly disables channel
binding for pinned-server compatibility; TLS verification is not disabled.
Factory controller proxy variables are cleared so Kubernetes/ATE requests never
use a guest-only loopback proxy.

The private router admits the exact factory controller SPIFFE identity in
addition to its original two identities. Other router policies/TLS remain
unchanged. The runtime space has a scoped owner policy; namespace creation alone
does not create that space. `bin/post_render.py` preserves router admission on
bootstrap recovery only after the protected `router-owned.json` marker exists.
Do not widen this to arbitrary namespaces, service accounts, or anonymous access.

Deploy/reconcile from root WSL using `factory-coding/install.py` actions
`prepare`, `control-plane`, `proxy-bindings`, then `service`. The separate private
Argo installer owns its shared executor Role and semaphore; the factory verifies
them rather than broadening executor rights. `factory-coding/build.py` tests,
publishes, and preloads immutable images only on Ready primary/XL hosts.
`proxy-bindings` checks preserved pilots before any shared gateway activation.
Repeated identity registration uses atomic locked host bindings and projected
gateway authorization, not a gateway restart per actor. Revoked UIDs have
tombstones. Plugin preparation is host-serialized and guest preflight uses
bounded backoff; host-only inference and approved 1ES feeds remain required.

For recovery, preserve the ledger, Kagent schema, sessions, workspace journals,
root credentials, runtime descriptors, and archived Workflow receipts together.
Restarting the frontend/controller must reconcile existing assignments, not
recreate an empty ledger. The reaper repairs durable queued intents lacking a
Workflow UID. Only explicit native `KAGENT_SEND_NOT_ACCEPTED` permits repeating
the same native message identity. Unknown outcomes retain a reservation.
Likewise, only Node's explicit pre-task preflight rejection permits another
submission; accepted/interrupted contributor turns are never blindly replayed.
Workflow loss, timeout, lock release, or cancellation is not proof remote work
stopped. Observe the native terminal outcome; cancellation promises no rollback.

Inspect `journalctl -u factory-coding.service`, Argo Workflow phases/locks, and
the protected ledger/report files for queue age, state, reservation count, and
unresolved assignments. There is no Prometheus/HA claim for this pilot. Protected
`platform-error.log` and `a2a-error.json` retain platform diagnostics. Back up
SQLite consistently using its backup API, or stop the frontend and retain its
WAL/SHM with the database; do not copy only the live main file.

`operate.py revoke <task-id>` revokes settled session authority only when no other
active/unresolved task owns that session. `cleanup-workflow <task-id>` validates
terminal state, ownership labels, and UID, archives the Workflow, then deletes it
and its matching capability Secret using UID preconditions. Ledger/results and
session/workspace remain retained. `resolve-stopped-fixture` is restricted to
model-free fixtures with authoritative matching suspended actor identity and
empty native TaskStore; it is not a generic way to free uncertain coding slots.
Never edit native TaskStore tables, manually wake/kill managed actors, or delete
unrelated workflows to bypass ownership or admission.

Acceptance evidence is root-private `acceptance-*.json`. The managed seven-task
fixture passed with seven distinct sessions/Workflow UIDs and peak three
reservations. Real coding, lifecycle, and contribution acceptance remain in
progress; successful model turns alone do not establish contribution success.

## PostgreSQL-backed A2A coding gateway

`factory-coding.service` exposes one logical coding agent at
`https://10.82.0.2:18443`. It fronts only `factory-coder`; deploy another copy
for another agent type. The gateway is not a workflow engine or generic registry.
Kagent/Substrate owns sessions, actors, placement, snapshots, native A2A tasks,
and execution outcomes.

The gateway persists its queue and mappings in the single PostgreSQL table
`factory.gateway_tasks`. It runs one process and holds a PostgreSQL advisory lock
for best-effort restart overlap protection. There is no active-active or HA
claim. Three worker threads provide the entire concurrency limit. States
`dispatching`, `active`, and `uncertain` consume slots.

Supported operations are `SendMessage`, `GetTask`, `ListTasks`, and `CancelTask`.
Polling is supported; streaming and push notifications are not. The Agent Card
is `/.well-known/agent-card.json`. `/readyz` proves only process liveness.
Bearer credentials and database secrets remain root-private under
`/home/nibeauli/kagent-lab/factory-coding`.

Callers must use stable `messageId` values. Identical retries return the same
task; changed input conflicts. If `contextId` is absent, the gateway task ID is a
fresh context. If supplied, tasks for that owner/context reuse one Kagent session
and serialize. There is no separate context table or custom continuation field.

The gateway records delivery intent before `SendMessage` and uses a deterministic
native message ID. Explicit `KAGENT_SEND_NOT_ACCEPTED` permits retry. Unknown
delivery outcomes are observed by stable identity without replay and eventually
fail visibly if no native task appears. This is best-effort durability, not an
exactly-once guarantee. Queued work survives gateway restart; Kagent owns
recovery after native acceptance.

The PostgreSQL login assumes only `factory_readwrite`, verifies the database TLS
hostname and CA, and disables SCRAM channel binding only for pinned-stack
compatibility. The isolated controller/router/runtime-space security boundaries
remain unchanged. Dynamic actor UID bindings authorize the shared host Agency
gateway; plugin preparation is host-serialized with bounded guest backoff.

Deploy with `factory-coding/install.py`: `prepare`, `control-plane`,
`proxy-bindings`, then `service`. Build/publish with
`factory-coding/build.py managed`. The service depends on Ubuntu's
`python3-psycopg` package. Inspect it with:

```bash
systemctl status factory-coding.service
journalctl -u factory-coding.service --no-pager
```

The factory no longer creates Argo Workflows. Its retired terminal Workflows,
WorkflowTemplates, and task capability Secrets were archived in protected
`retired-argo-artifacts.json` and UID-deleted. The shared private Argo
installation remains untouched for unrelated future orchestration.

Live acceptance proved seven fixture tasks and seven real read-only coding tasks
with peak concurrency three, context/session reuse and serialization, durable
restart recovery, queued cancellation without execution, stable deduplication,
zero new Argo Workflows, clean Git state, and one shared host gateway PID.
Evidence is root-private `acceptance-gateway-*.json`.

## SlopMop autonomous swarm

The swarm reuses the direct A2A gateway as one deployment per role:

| Port | Agent |
|---:|---|
| 18443 | Coding |
| 18444 | Opportunity scout |
| 18445 | Planner |
| 18446 | Plan reviewer |
| 18447 | Implementation reviewer |
| 18448 | Documentation steward |
| 18449 | ADO triage |
| 18450 | PR pattern miner |
| 18451 | Swarm Manager |

The Swarm Manager is itself an A2A agent. Its deterministic runtime owns parent
and child task correlation while specialized agents own judgment. ADO work items,
Git/PRs, Kagent sessions, native A2A tasks, and Agency tools remain authoritative;
the dashboard is a read-only projection.

The disposable integration target is `users/nibeauli/slopmop-devel`. Swarm code
and documentation branches must begin with
`users/nibeauli/slopmop-demo-`. Broker policy enforces both source and target
branches before PR creation and resolves the explicit PR before allowing the
existing Agency `finish-pr` MCP.

Dashboard and live SSE:

```text
https://10.82.0.2:18451/dashboard
https://10.82.0.2:18451/events
http://localhost:18452/dashboard
http://localhost:18452/events
```

The localhost listener is a read-only projection for the Windows host. A2A
control remains authenticated and TLS-only on the overlay endpoint.

The Kagent UI is persistently forwarded to the Windows host at:

```text
http://localhost:18080
```

Full deployment, campaign, validation, and dry-run-first reset instructions are
in `factory-coding/SWARM-OPERATIONS.md`. Campaign reset archives exact lineage,
revokes actor bindings, deletes exact sessions/tasks/source branches, and resets
only `slopmop-devel` with a force-with-lease against the recorded baseline.
