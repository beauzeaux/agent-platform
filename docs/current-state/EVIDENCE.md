# Evidence, discrepancies, and limits

[Index](README.md)

## Method and confidence

Observed on 2026-11-03, approximately 21:50-22:00 UTC / 13:50-14:00 PST.
The environment was active while inspected: there were live factory tasks and an enabled autonomous timer.
Counts and pod placements are a snapshot, not an atomic cluster export.

Evidence priority:

1. Live listeners, read-only API/SSH/ARM responses, mounted filesystems, scheduled-task metadata.
2. Selected nonsecret fields from the actual runtime configuration, plus source implementing that path.
3. Existing operations records and past session summaries, explicitly identified as historical.

During the initial mapping, no live inference, token acquisition, Git/package operations, task submission, lifecycle
test, backup, installation, service restart, or Azure Run Command was performed. Database inspection used a read-only
transaction and returned only aggregate state counts. A subsequent operator-requested background gateway startup is
recorded in E09. Secret values, prompts, task outputs, and personal Git identities are not retained here.

## E01: Host and overlay

Live checks:

- Windows `wsl --list --verbose`: running default `Ubuntu-26.04`, WSL2.
- All three WSL hosts reached over existing SSH at `10.82.0.2`, `.3`, `.4`; remote hostnames match Kubernetes nodes.
- `wg0` peer endpoint on all three: `48.192.92.6:51820`.
- Primary peer routes include overlay `10.82.0.0/24`, Azure private `10.81.0.0/24`, and other two site pod `/24`s.
- Primary MTU 1420 WireGuard / 1370 Calico VXLAN.
- No failed systemd units on any host.

Source:

| File | Relevant section |
|---|---|
| `Q:\src\tools\wireguard-hub\main.bicepparam:10-33` | Named site -> overlay address -> pod subnet inventory |
| `Q:\src\tools\wireguard-hub\README.md:7-22` | Azure hub architecture and forwarding intent |
| `Q:\src\tools\wireguard-hub\scripts\setup-wsl-wireguard.sh` | WSL persistence/bootstrap implementation |

## E02: Azure hub

Read-only `az resource list`, `az network public-ip list`, `az vm list --show-details`,
`az network nsg list`, and `az network route-table list`, scoped to subscription
`CloudWorkStations + MSASGTools Email Bots`, resource group `nibeauli-hub`.

Confirmed VM running, public IP/DNS, private NIC IPs, `CorpNetPublic` UDP 51820 ingress, Internet deny rules, and overlay
route table. Hub guest WireGuard/nftables/runtime reconciliation was not inspected directly.

## E03: Kubernetes inventory

`sudo microk8s status`, Kubernetes reads, and `microk8s helm3 list -A` confirmed:

| Observation | Result |
|---|---|
| Kubernetes nodes | 3 Ready, all control-plane |
| dqlite masters | `10.82.0.2:19001`, `.3:19001`, `.4:19001` |
| Final pod phase summary | 45 Running, one Succeeded; no unready Running pod |
| Namespaces | `ate-system`, `default`, `factory-executions`, `factory-workflows`, `kagent`, Kubernetes system namespaces, `observability`, `podcertificate-controller-system`, `proxy-access` |
| Agent definitions | Original deterministic Agent plus eight factory Agents, all Ready |
| Worker pools | Original 3/3; factory 2/2 |
| Workflow resources | Zero Workflows, WorkflowTemplates, CronWorkflows |
| Ingress resources | None |
| PVCs | Three, all retained hostpath and XL-local |
| Remote MCP registrations | Original accepted; factory `Accepted=False`, `DiscoveryFailed` |

`microk8s status` as the ordinary user failed reading protected arguments. Root status succeeded and confirmed HA.
That first failure was a permission issue, not evidence that MicroK8s was down.

Relevant source:

| File | Lines / role |
|---|---|
| `Q:\tools\kagent-lab\kagent-values.yaml:1-47` | Original watch namespace, insecure API auth, workers, metrics |
| `Q:\tools\kagent-lab\substrate-values.yaml:1-16` | Registry replacement, router trust, trace endpoint |
| `Q:\tools\kagent-lab\factory-coding\install.py:191-259` | Render/apply isolated controller, worker pool, Agents/Harness |
| `Q:\tools\kagent-lab\factory-coding\controller.go:27-83` | Injected bearer authenticator, authorization, proxy egress compiler |

## E04: Windows and bridge path

Windows listeners and targeted process metadata confirmed ADO .NET and Agency Python processes.
Health returned 200 on Windows 5481 and 15442. No 4141/5443/8090 Windows listener was found.
Model health failed on both Windows localhost and actual configured WSL-to-Windows target.
Using existing mTLS client trust, bridge model health also timed out.

At 22:05 UTC / 14:05 PST, a targeted recheck found zero Windows `LocalLlmGateway` processes, including .NET-hosted
instances, and zero TCP 4141 listeners. Both remote WireGuard peers answered ping, all three Kubernetes nodes were
Ready, `agent-proxies` was 1/1, and `kagent-proxy-access.service` was active. This identifies a missing Windows inference
process, not a general cluster disconnection. It does not establish whether the process crashed or was stopped.

Actual protected `bridge.json` nonsecret fields select:

```text
listen_address = 10.82.0.2
llm_host = 172.24.160.1
llm_port = 4141
Windows ADO relay = Q:\tools\kagent-lab\proxy-access\windows-relay.py
Windows Agency relay = Q:\tools\kagent-lab\agency-broker\windows_relay.py
```

Windows scheduled tasks confirmed `Kagent Agency Broker` with logon trigger.
No relevant proxy startup shortcut or Windows service was found in the inspected sources. Registry Run keys and every
possible external launch mechanism were not exhaustively audited.
No Windows `netsh interface portproxy` entries existed.

One enabled legacy firewall rule, `Podman host service proxies`, allows TCP 4141/5443/8090 on local
`172.24.160.1` from `172.24.161.36`. It is not proof that those services are running.

| File | Lines / role |
|---|---|
| `Q:\tools\kagent-lab\proxy-access\host_bridge.py:90-105` | Configured model target and gateway authorization |
| `Q:\tools\kagent-lab\proxy-access\host_bridge.py:130-209` | Windows stdio ADO and Agency relays |
| `Q:\tools\kagent-lab\proxy-access\host_bridge.py:244-270` | mTLS startup and listeners |
| `Q:\tools\kagent-lab\agency-broker\broker.py:339-350` | Shared stdio Agency gateway |
| `Q:\tools\kagent-lab\agency-broker\profiles.json` | Approved tools and repository/write profiles |
| `Q:\src\tools\ado-dev-proxy\proxy.example.json` | Live-selected organizations/feeds/host allowlist |

## E05: Actors and task persistence

Read-only Substrate actor inventory, using the existing `kubectl-ate` administrative client:

| Atespace | Actors | State summary |
|---|---:|---|
| `kagent` | 0 | No retained actors |
| `proxy-access` | 2 | One Running, one Crashed |
| `factory-executions` | 86 | 82 Suspended, one Resuming, one Running, two Suspending |

The crashed native actor is `engsys-agency-coder`, assigned to devbox-2.
Factory current assignments included primary/XL; 79 retained actors had no assigned node.
Suspended actor counts are not counts of Kubernetes Pods or busy execution slots.

Read-only PostgreSQL aggregate, without request/result content:

| Gateway | State counts |
|---|---|
| coding | 54 completed, six failed, two canceled, one active |
| ado-triage | Two completed |
| opportunity-scout | Two completed, two failed |
| planner | Three completed |
| plan-reviewer | Three completed |
| pr-pattern-miner | Two completed, two failed |
| swarm-manager | One active |

This proves live state exists outside Argo and that maintenance could interrupt active work.
It does not diagnose historical task failures or establish current task progress.

Source: `Q:\tools\kagent-lab\factory-coding\gateway_store.py:22-76`, `backend.py:29-88`,
and `swarm_manager.py:829-915`.

## E06: Observability

| Check | Result |
|---|---|
| Grafana `/api/health` through localhost 13000 | Database OK, version 12.1.1 |
| Prometheus active targets through localhost 19090 | 34 up, none down |
| Loki readiness through localhost 13100 | 200 |
| Tempo readiness through localhost 13200 | Nonresponsive existing forward |
| Tempo direct service readiness `10.152.183.202:3200/ready` | 200 |
| WSL manager telemetry status | Enabled; 15 emitted; recorded success; no last error |
| Standalone OTel Collector deployment | Not found |
| Observability PVCs | None |

Deployed versions include Prometheus 3.5.0, Loki 2.6.1, Promtail 3.5.1, and Tempo 2.9.0.
The Loki chart's advertised app version differs from the actual image; the service catalog uses deployed image evidence.
Runtime trace sampling environment is `parentbased_traceidratio`, argument `0.01`; OTLP logs/metrics disabled.
Prometheus retention is 10d with no persistent storage specification.

Source: `Q:\tools\kagent-lab\factory-coding\observability-values.yaml`,
`tempo-values.yaml`, and `install.py:307-355,468-510`.

## E07: Storage, backups, and recovery tasks

All three hosts expose 750 GiB `Q:\MicroK8sStorage\hostpath.vhdx` files and mounted native ext4 storage.
Live PV node affinity pins database, RustFS, and registry to `cpc-nibea-2l35i`.
Primary backup directory names/file inventories were checked without reading secret-bearing archives.

Remote Windows task checks found no `MicroK8s*` task on XL, and only the described `MicroK8s-Host-Recovery` on devbox-2.
Its last exit was 1 and protected status says Failed (`wsl.exe exited with code 1`).
The remote source implements logon-triggered, bounded existing-disk attachment and service recovery.
It was not run or modified.

Public certificate expiration metadata:

| Public certificate | Expires UTC |
|---|---|
| Proxy access CA | 2027-10-31 21:32:57 |
| Bridge, gateway, bridge-client leaves | 2027-01-29 21:32:58 |
| Windows ADO interception CA | 2027-10-31 20:36:30 |

Sources: `Q:\tools\microk8s-storage\Initialize-MicroK8sHostpath.ps1:1-19,130-150`;
`Q:\tools\kagent-lab\proxy-access\manage.py:32-118`;
historical backup/recovery record in `Q:\tools\kagent-lab\OPERATIONS.md:766-808`.

## E08: Historical notes that need reinterpretation

| Existing statement | Current evidence / correction |
|---|---|
| `SWARM-OPERATIONS.md` says devbox-2 remains unavailable | Node is now Ready; original Agency actor is still crashed there |
| Original operations describe old disk attachment tasks on XL/devbox-2 | Neither old task found; devbox-2 instead has separate logon recovery |
| Earlier notes report Windows LLM health on 4141 | No such listener now; downstream config still targets it |
| Early pilot description says model-free | Applies to deterministic original pilot, not the later coding/swarm system |
| Factory is Argo-backed | Historical section explicitly retired; PostgreSQL-backed direct gateways are current |
| `localhost:18080` is the Kagent UI | Specifically the factory UI now |
| Model-free actors and registered Kagent Agents are the entire runtime | Native `proxy-access` actors and factory sessions require Substrate inventory |
| Three hosts / HA enabled implies application HA | XL data and primary Windows/WSL capabilities remain single failure domains |

Recent session history was used only as orientation, including October 31 cluster setup and November 2 proxy/storage
work. The cloud history was available; its local-store supplement did not support the same time-query syntax.
No historical session response is used as a substitute for the live checks above.

## E09: Subsequent approved Windows gateway startup

The operator asked to start the missing LLM gateway in the background. The existing Release binary was started as a
detached process using this command, from `Q:\src\tools\LocalLlmGateway`:

```powershell
& 'C:\Users\nibeauli\.dotnet\dotnet.exe' `
  'Q:\src\tools\LocalLlmGateway\src\LocalLlmGateway\bin\Release\net10.0\win-x64\LocalLlmGateway.dll' `
  serve --host 172.24.160.1 --port 4141 `
  --gateway-key-file (Join-Path $env:APPDATA 'local-llm-gateway\gateway-key')
```

Verification at 2026-11-03 22:08:17 UTC / 14:08:17 PST:

| Check | Result |
|---|---|
| Windows gateway process | PID 37432, user-local `dotnet.exe` |
| Windows listener | `172.24.160.1:4141` |
| Windows `/health` | 200, `status=ok` |
| Authenticated Windows `/v1/models` | 200, 30 models |
| Existing mTLS WSL bridge `10.82.0.2:15441/health` | 200 |
| Same bridge `/v1/models` | 200, 30 models |
| Detached execution handle | `local-llm-gateway`, still running after readiness verification |

This changes the initial missing-listener finding: the inference service is now available through the existing
host bridge. No inference request was made. No task/service registration, firewall change, credential rewrite, or
permanent startup configuration was added. The earlier process-exit cause remains unknown.

## Unverified or deliberately out of scope

- Successful current inference, model entitlement, forced WAM/MFA recovery, fresh Git/npm/NuGet requests, MCP tool
  execution, Git push, PR/work-item writes, or end-to-end campaign completion.
- Why the LLM service stopped, root cause of the crashed actor, task failures, or failed devbox-2 recovery.
- Stopped Podman machine filesystem/containers/volumes, remote Windows application inventories unrelated to storage,
  and dormant Dev Tunnel/ICE/OpenSSH deployments elsewhere.
- Hub guest firewall state, complete Internet/LAN exposure, penetration testing, or exhaustive RBAC audit.
- Current backup content integrity, post-October-31 factory recovery, complete disaster recovery, and failure injection.
- Every copied upstream checkout/build artifact. Active runtime paths were prioritized over exhaustive source-tree scans.

No infrastructure was simplified as a side effect of documenting it.
