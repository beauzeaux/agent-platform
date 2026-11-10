# Services, ownership, and source catalog

[Index](README.md) | [Topology and ports](TOPOLOGY.md) | [Evidence](EVIDENCE.md).

## Primary Windows capability services

| Component | State | Implementation/configuration | Startup and responsibility |
|---|---|---|---|
| AdoDevProxy | Running .NET process; 5480/5481 loopback | `Q:\src\tools\ado-dev-proxy`; active CLI config is `proxy.example.json` | Existing background process; no matching scheduled task or Startup shortcut found. WAM remains in the signed-in user session. |
| Agency broker | Running Python process; loopback 15442 | `Q:\tools\kagent-lab\agency-broker\broker.py`; `profiles.json`; protected `state\broker.json` | Hidden logon-triggered task `Kagent Agency Broker`; bounded failure restarts |
| Local LLM Gateway | Started after inventory; listening on `172.24.160.1:4141`; 30 models discoverable | `Q:\src\tools\LocalLlmGateway`; `%APPDATA%\local-llm-gateway` | Operator-requested detached background process, PID 37432 at startup; not a new task/service or reboot guarantee |
| WAM IMDS broker | Not observed running | `Q:\src\tools\wam-imds-broker` | Older delegated-token-vending approach; not needed by AdoDevProxy's direct WAM implementation |

The running ADO process loads:

```text
Q:\src\tools\ado-dev-proxy\src\AdoDevProxy\bin\Release\net10.0-windows\AdoDevProxy.dll
serve --config Q:\src\tools\ado-dev-proxy\proxy.example.json
```

The restored LLM process loads the existing Release binary
`Q:\src\tools\LocalLlmGateway\src\LocalLlmGateway\bin\Release\net10.0\win-x64\LocalLlmGateway.dll`
through the existing user-local .NET runtime. It explicitly selects the WSL-facing address, port 4141, and the existing
gateway key file. No credential/configuration files were rewritten. See [the intervention record](EVIDENCE.md).

Despite the filename, **`proxy.example.json` is the live selected configuration**, not just an illustrative template.
It allows MSASG and the 1ES `EngSys_Consume` npm/NuGet feeds. Its 60 exact Azure Artifacts download shard hosts are
allowlisted; there is no public npm fallback.

The Agency broker launches/reuses one shared `agency mcp gateway --transport stdio --no-aec` process on demand.
It multiplexes logical clients and limits tools through host-owned profiles. This broker is distinct from
the MCP gateway connection available in an interactive terminal session.

## Primary WSL persistent services

Custom unit definitions are under `/etc/systemd/system`.

| Unit | Runtime implementation/configuration | Function |
|---|---|---|
| `wg-quick@wg0.service` | Protected `/etc/wireguard` configuration | Persistent overlay |
| `kagent-proxy-access.service` | Copied `/home/nibeauli/kagent-lab/proxy-access/host_bridge.py` and `bridge.json` | mTLS model, ADO, and Agency host transport |
| `kagent-ui-host.service` | `kubectl port-forward` | Factory UI on loopback 18080 |
| `slopmop-grafana-host.service` | `kubectl port-forward` | Grafana on loopback 13000 |
| `factory-coding.service` | `Q:\tools\kagent-lab\factory-coding\gateway_service.py`, through `/mnt/q`; protected `service.json` | Coding A2A frontend |
| Seven other `factory-<role>.service` units | Same shared gateway implementation; `service-<role>.json` | Role-specific A2A frontends |
| `factory-swarm-manager.service` | `swarm_manager.py`; `service-swarm-manager.json` | Parent/child campaign coordination and read-only dashboard |
| `factory-swarm-autonomous.timer` | Activates `factory-swarm-autonomous.service` | Bounded discovery/campaign submission per 12-hour UTC window, with jitter |
| `mnt-microk8s\x2dhostpath.mount` | ext4 disk by saved filesystem UUID | Dedicated hostpath storage |
| `var-lib-ate.mount` | Bind from dedicated disk | Substrate local worker state |
| `kagent-worker-storage-propagation.service` | `mount --make-rshared /var/lib/ate` | Required shared worker mount propagation; dependency-triggered oneshot |

The deployed host bridge hash matched its Windows source copy during inspection.
Factory service code executes directly from the Windows source mount, while much of the protected configuration and
build state lives on Linux. That distinction matters when consolidating files or changing source in place.

Prometheus, Loki, Tempo, and factory-controller forwards also had existing `kubectl` processes. Unlike the UI and
Grafana forwards, these were not found as dedicated persistent systemd access units. Do not treat them as equivalent
boot-managed services.

No failed systemd units were reported on any of the three WSL hosts. That result does not cover crashed Substrate actors.

## A2A role catalog

All role endpoints bind primary's WireGuard IP, `10.82.0.2`, using TLS. Their JSON-RPC endpoint is `/a2a`.
`/readyz` is process liveness, not an end-to-end dependency check.

| Port | WSL unit | Kagent Agent | Host Agency profile | Configured concurrent gateway slots |
|---:|---|---|---|---:|
| 18443 | `factory-coding` | `factory-coder` | `slopmop-contributor` | 3 |
| 18444 | `factory-opportunity-scout` | `slopmop-opportunity-scout` | `engsys-reader` | 2 |
| 18445 | `factory-planner` | `slopmop-planner` | `engsys-reader` | 3 |
| 18446 | `factory-plan-reviewer` | `slopmop-plan-reviewer` | `engsys-reader` | 3 |
| 18447 | `factory-implementation-reviewer` | `slopmop-implementation-reviewer` | `engsys-reader` | 3 |
| 18448 | `factory-documentation-steward` | `slopmop-documentation-steward` | `slopmop-contributor` | 1 |
| 18449 | `factory-ado-triage` | `slopmop-ado-triage` | `slopmop-triage` | 1 |
| 18450 | `factory-pr-pattern-miner` | `slopmop-pr-pattern-miner` | `engsys-reader` | 2 |
| 18451 | `factory-swarm-manager` | Not a separate Kagent Agent; deterministic WSL coordinator | Calls the other role gateways | One campaign coordinator |

These limits belong to individual gateways. They are not a single cluster-wide inference or monetary budget.
The autonomous loop skips submission while a campaign is active and limits candidates/implementations.
Its documented write scope is the disposable SlopMop integration branch, not `main`.

## Kubernetes workload catalog

### Shared Substrate: `ate-system`

| Workload | Replicas at inspection | Responsibility |
|---|---:|---|
| `ate-api-server` | 2 | Actor/session runtime API; object-store integration |
| `ate-controller` | 1 | Substrate reconciliation |
| `ate-idp-server` | 2 | Runtime identity issuer |
| `atelet` DaemonSet | 3 | Node-level runtime integration |
| `atenet-router` | 1 | Authenticated runtime ingress/routing |
| `atenet-egress` | 1 | Runtime egress and credential/header mediation |
| `k8s-credential-provider` | 1 | Kubernetes-secret credential integration |
| `rustfs` | 1 | S3-compatible snapshot/object storage; XL-local 100 GiB PVC |
| `rustfs-bucket-init` Job | Completed | Initial bucket creation |

Substrate images are pinned to `v1.0.0-rc1`. Agentgateway images are `v1.7.0`.
`podcertificate-controller-system` contains one `podcertificate-controller` deployment for workload certificates.
The installed pod certificate controller is not the MicroK8s cert-manager add-on; that add-on is disabled.

### Original Kagent pilot: `kagent`

| Workload/resource | Count | Responsibility |
|---|---:|---|
| `kagent-controller`, `kagent-ui` | One each | Original namespace-scoped, model-free pilot |
| `kagent-default` worker deployment | 3 | gVisor workers spread across all three hosts |
| `deterministic-byo` Agent and Harness | One each | Deterministic lifecycle/recovery test agent; Ready |
| `kagent-postgresql` | 1 | Shared database, including isolated factory schema; XL-local 10 GiB PVC |
| `kagent-lab-registry` | 1 | Cluster-private OCI image registry; XL-local 5 GiB PVC |
| `kagent-api` RemoteMCPServer | 1 | Accepted |

Kagent is pinned to `1.0.0-rc1`. The original controller configuration explicitly uses insecure public API
authentication. It must not be confused with the custom authenticated factory controller.
No actors were present in the `kagent` atespace at inspection.

### Isolated factory: `factory-executions`

| Workload/resource | Count | Responsibility |
|---|---:|---|
| `factory-controller` | 1 | Custom Kagent bootstrap with fixed bearer-token authentication and factory-scoped authorization |
| `factory-ui` | 1 | UI configured to authenticate to that controller |
| `kagent-default` worker deployment | 2 | gVisor execution pool; currently on primary and XL |
| Agents and AgentTemplates | 8 each | Coding plus seven specialized roles |
| `factory-coding` Harness | 1 | Shared actor harness for those roles |
| `factory-api` RemoteMCPServer | 1 | `Accepted=False`, reason `DiscoveryFailed` |

This controller is **not a Helm release named `factory`**. The installer renders the pinned Kagent chart using
`helm template`, substitutes its custom image, and applies the resources. `helm list` alone misses this deployment.
The custom controller injects its authenticator in Go even though its underlying rendered configuration says
`auth.mode: insecure`; interpreting only that YAML would be misleading.

The same immutable custom `factory-coder` image is used by the controller and coding runtime build:
`sha256:1e53b3317694866b4fe42c5667820d59ca075468d16d57c2ed9a34bc2b8826e5`.
Its deployment registry endpoint is `10.152.183.131:5000`.

### Private host capabilities: `proxy-access`

One `agent-proxies` deployment fronts the host bridge. Helm owns the chart release.
Its original coding actors are native Substrate actors, not Kubernetes Pods or Kagent Agent CRs:

| Actor | State at inspection | Assigned node |
|---|---|---|
| `engsys-coder` | Running | primary |
| `engsys-agency-coder` | Crashed | devbox-2 |

Factory actor sessions are separate: 86 retained actors were observed, mostly suspended. See [Evidence](EVIDENCE.md).
A Running worker pod does not imply all actors inside its runtime are healthy.

### Other namespaces

| Namespace | Components | Current interpretation |
|---|---|---|
| `factory-workflows` | One Argo workflow controller, RBAC/policy/CRDs | Retained old orchestration installation; no workflow resources found |
| `observability` | Grafana, Prometheus Operator, Prometheus, Alertmanager, kube-state-metrics, three node exporters, Loki, three Promtails, Tempo | Active monitoring/logging/tracing |
| `kube-system` | Three Calico nodes, Calico controller, CoreDNS, hostpath provisioner | Cluster networking and local storage |

### Actual Helm releases

| Namespace | Release | Chart |
|---|---|---|
| `ate-system` | `substrate`, `substrate-crds` | `1.0.0-rc1` |
| `podcertificate-controller-system` | `substrate-podcert` | `1.0.0-rc1` |
| `kagent` | `kagent`, `kagent-crds` | `1.0.0-rc1` |
| `proxy-access` | `agent-proxies` | `0.1.0` |
| `observability` | `kube-prom-stack` | `kube-prometheus-stack-77.6.2` |
| `observability` | `loki` | `loki-stack-2.10.3` |
| `observability` | `tempo` | `tempo-1.24.1` |

## Source/artifact locations

| Location | Role and observed source-control state |
|---|---|
| `Q:\tools\kagent-lab` | Active platform integration, bridge, broker, harnesses, factory, operations; not a Git repository |
| `/home/nibeauli/kagent-lab` | Prepared upstream source/builds, offline chart archives, protected generated config, diagnostics |
| `Q:\tools\microk8s-storage` | Shared Windows disk initializer and WSL mount helper; runtime installation also under `%ProgramData%\MicroK8sStorage` |
| `Q:\src\tools\ado-dev-proxy` | Active ADO proxy; not a Git repository |
| `Q:\src\tools\LocalLlmGateway` | Separate Git repository; clean working tree at inspection |
| `Q:\src\tools\wireguard-hub` | Active Azure infrastructure source; modified/untracked WSL setup additions present |
| `Q:\src\tools\remote-agent-gateway` | Older WireGuard/Dev Tunnel/ICE transport experiments; dirty working tree; not running here |
| `Q:\tools\agent-fabric` | Older Podman Caddy/DNS/step-ca/Envoy reference; local Git repository with untracked source; not running |
| `Q:\src\tools\wam-imds-broker` | Legacy delegated-token broker source; no live primary listener |
| `Q:\src\tools\openssh-reverse-hub` | Additional remote-connectivity infrastructure source; untracked source; no live dependency established |

The stopped Podman machine's stored name is `podman-machine-default`; it last ran October 29. Its containers/volumes
were not inspected by starting it. Docker Engine in Ubuntu is running, but had no containers or volumes; its owned
`ado-dev-proxy` bridge and listening relay remain.
