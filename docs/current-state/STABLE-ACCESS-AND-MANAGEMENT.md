# Stable host access and native cluster management

Approved follow-up to the consolidation rollout, 2026-11-04.

## Stable hostnames on the Windows host

A standard, checksum-pinned **Caddy 2.11.8** runs in primary WSL as
`host-service-access.service`, listening **only on 127.0.0.1:80**.
Its standard systemd readiness notification prevents a successful restart from
being reported before the listener is available. Reinstallation over the running
binary and immediate Windows access were verified.
It routes directly to stable Kubernetes Service addresses, not transient Pods or
Kubernetes API port-forward streams. All seven routes returned HTTP 200 from
Windows using explicit Host resolution. No public listener or root CA was added.
Existing numbered URLs remain compatible.

| Stable URL | Service |
|---|---|
| `http://kagent.internal` | Factory UI, including the new management agents |
| `http://pilot.internal` | Original Kagent pilot UI |
| `http://grafana.internal` | Grafana |
| `http://prometheus.internal` | Prometheus |
| `http://tempo.internal/ready` | Tempo query/readiness API; use Grafana for trace exploration |
| `http://loki.internal/ready` | Loki API/readiness |
| `http://swarm.internal/dashboard` | Read-only Swarm dashboard |

**Windows hostname setup is now verified:** all seven names resolve to
`127.0.0.1` and their routes return HTTP 200 without explicit curl resolution.
The host mappings were installed outside this non-elevated session; no permission
bypass was attempted. The required hosts-file entry is:

```text
127.0.0.1 kagent.internal pilot.internal grafana.internal prometheus.internal tempo.internal loki.internal swarm.internal
```

To install or repair these entries, use an elevated PowerShell:

```powershell
& Q:\src\tools\HostCapabilities\deployment\access\Install-Hosts.ps1 -Apply
```

Without `-Apply`, that script only prints the owned block. Apply backs up the
original, preserves unrelated mappings, and refuses pre-existing unowned entries
for these names.
`http://localhost` already serves a plain-text list on the stable default port.

Source: `Q:\src\tools\HostCapabilities\deployment\access`.
Generated configuration: `/etc/host-service-access/caddy.json`.
Pinned official binary/license: `/usr/local/lib/host-service-access`.
Protected evidence/backups: `/home/nibeauli/kagent-lab/host-service-access`.

Re-render deliberately after a Kubernetes Service is deleted/recreated or renamed:

```bash
python3 /mnt/q/src/tools/HostCapabilities/deployment/access/manage.py --apply
```

Pod replacement alone does not require re-rendering. No global trust, existing
host capability process, model address, or credential store is changed.
Stop/disable only `host-service-access.service` to roll back the new front door;
remove only the marked hosts block if reverting Windows names.

## Native management agents

Two native `api.kagent.dev/v1alpha3` agents are declared in `factory-executions`:

| Agent | Intended surface |
|---|---|
| `cluster-manager` | Read-only Kubernetes resources, events, descriptions and logs |
| `cluster-observability` | The same workload context plus Prometheus queries/targets |

They use the **official built-in Go ADK harness**, not the bespoke coding
harness: `ghcr.io/kagent-dev/kagent/golang-adk@sha256:5b06741be8cb69c9293477bdbf3203c6526127c533fa940fd9f595a82a801701`.
The installed 1.0 release does not package the old 0.x `k8s-agent` charts.
These are small native 1.x declarations using the supported built-in runtime
and official tools, not an incompatible installation of the old API group.

The official `kagent-tools` **0.3.0** chart was reused from the exact installed
Kagent release artifact. Its image is pinned:
`ghcr.io/kagent-dev/kagent/tools@sha256:dab4019f593becf20466f21e3c9dba1895122bbdaf07cb737539eee20b7b0274`.
It runs in namespace `cluster-management`, Helm release
`cluster-management-tools`, with application `--read-only` **and** read-only
Kubernetes RBAC. Kubernetes queries are additionally restricted by the selected
native MCP tool names. No delete/apply/patch/exec/scale tools are selected.

Read-only was the conservative default because the user was unavailable when
authority was requested. Actual ServiceAccount checks allow pods/nodes reads
and deny Secrets, Pod creation, exec, deployment patch and token creation.
A real MCP node query succeeded; a Secret query was rejected. Prometheus
`vector(1)` succeeded with the explicit cluster URL. Upstream's optional
`prometheus_url` argument otherwise defaults to localhost despite its environment
setting, so the agent instructions explicitly supply the correct private URL.

NetworkPolicy permits management MCP access from the factory namespace and
Substrate egress, and restricts outbound traffic to DNS, the Kubernetes API and
Prometheus. No management tool server is exposed to Windows/public networks.

The model uses standard native Kagent Secret-backed credential injection into
trusted egress. Only the existing host gateway **admission key**, not the GitHub
credential or WAM token, is held in the owned Kubernetes Secret. Actors receive
the compiled inert placeholder, not the key. This avoids inventing a second
per-session host actor-registration controller for these interactive native agents.
No existing business agent or its host role/profile was changed.

The native Kubernetes credential provider required an explicit namespace grant.
Only Secrets carrying **both** management owner/access labels are admitted from
the factory atespace; existing grants were preserved. `grant_model.py` backed up
the original policy and rolled only that provider to load it. Desired values are
also recorded in `Q:\tools\kagent-lab\substrate-values.yaml` and the protected
`cluster-management/substrate-management-grant.values.yaml` overlay. Include that
overlay in a future Substrate upgrade; do not broaden the grant to every factory
Secret. A first public runtime probe was correctly denied before provider access;
after the narrow grant, an isolated native ADK turn reached `TASK_STATE_COMPLETED`
using only the fixed prompt `Reply exactly MANAGEMENT_RUNTIME_OK.`
No cluster data/tool result was sent in that test.
Both exact smoke sessions and their temporary Agent/Template were removed.

Source/reapply: `Q:\src\tools\HostCapabilities\deployment\management`.
From primary WSL as root, using the retained exact tools subchart:

```bash
source=/mnt/q/src/tools/HostCapabilities/deployment/management
runtime=/home/nibeauli/kagent-lab/cluster-management
/home/nibeauli/kagent-lab/bin/helm upgrade --install cluster-management-tools \
  "$runtime/tools-chart" --namespace cluster-management --create-namespace \
  --values "$source/tools-values.yaml" --wait --timeout 120s
python3 "$source/manage.py" --apply
python3 "$source/grant_model.py"
/snap/bin/microk8s kubectl apply -f "$source/network-policy.yaml"
```

Final post-policy checks passed for actual MCP node/Prometheus reads and Secret
rejection; RBAC denied writes/exec/token creation. Both Agents were Ready, the
RemoteMCPServer reported successful discovery of 14 tools, and the tools Helm
release was deployed at revision 6. Only the labelled management-model Secret
matched the new grant. Focused source tests passed and temporary probe forwards
were closed.

Tool endpoint:
`http://cluster-management-tools.cluster-management.svc.cluster.local:8084/mcp`.
Runtime snapshots use the new private `gs://ate-snapshots/cluster-management/`
prefix. To retire these agents, delete only their owned Agent/AgentTemplate/
Harness/ModelConfig/RemoteMCPServer resources and owned Secret; uninstall only
the `cluster-management-tools` Helm release. Do not remove shared Substrate,
PostgreSQL, the factory controller or business agent resources.
Also remove only the exact management-model namespace grant from live policy
and desired upgrade values, preserving other grants, then reload only the
credential-provider deployment.

## Grafana anonymous viewing

The user explicitly approved anonymous **Viewer** access on these devboxes.
`http://grafana.internal` now opens without a login. Unauthenticated dashboard
reads and searches return HTTP 200; an actual fresh-dashboard write attempt
returned **403**, without creating a dashboard. Authenticated editing and
administration remain available; the login form was not disabled.

The change is stored in the `kube-prom-stack` Helm release, revision **11**,
using the same `kube-prometheus-stack` chart **77.6.2** and reusing all existing
values. Source overlay:
`Q:\src\tools\HostCapabilities\deployment\management\grafana-anonymous.values.yaml`.
Preserve it on future chart upgrades. The anonymous organization is `Main Org.`;
update that setting if the organization is renamed.

This access also applies to Grafana's private cluster endpoint, not only the
Windows reverse proxy. It is not anonymous Editor/Admin access. No Grafana
password, account or credential store was removed or exported.

Protected pre-change Helm values/manifests/status are under
`/home/nibeauli/kagent-lab/pilot-observability/grafana-before-*`.
To roll back only this behavior, keep the current release values and set
`grafana.grafana.ini.auth.anonymous.enabled` to `false` in a values overlay.
Do not roll back the entire monitoring stack merely to restore login.

## Grafana-capable agent in the original pilot

`observability-agent` is deployed in namespace **`kagent`**, the original pilot
at `http://pilot.internal`, not in the factory UI/namespace. These are two Kagent
instances in the **same Kubernetes cluster**, not separate clusters.

The upstream [registry observability agent](https://kagent.dev/agents/observability-agent)
uses older tool names. This is a read-only native 1.x adaptation of that role,
using the official Go ADK Harness, a native OpenAI Responses ModelConfig routed
through the existing host gateway, and official Grafana MCP **2.2.0**:

```text
docker.io/grafana/mcp-grafana@sha256:697336440e34878f8e0b26a69675db8d7e97b4d0daec25ec5441c85ac6c60e81
```

The two native tool bindings provide Grafana dashboards/datasources, PromQL,
Loki queries and read-only Kubernetes resource/event inspection. The Kubernetes
binding reuses the already-deployed official read-only management tools rather
than starting another copy. This agent cannot create dashboards or change the
cluster; it can explain proposed changes.

Grafana MCP itself uses the new anonymous Viewer access, so **no Grafana password
or service-account token is copied into it or into an agent**. A separate random
MCP admission token protects its private HTTP endpoint. Native Secret-backed
egress injects that header; the actor receives the inert placeholder, not the
token. The existing pilot namespace provider grant already covers the two owned
admission/model Secrets; no shared provider policy was broadened or restarted.
The model Secret contains only the existing host gateway admission key, not
GitHub/WAM credentials.

The official MCP server has `--disable-write`, selected read-only tool categories,
50-line log bounds, usage reporting disabled, metadata metrics enabled, strict
Host/Origin checks, no Kubernetes token mount, and a read-only non-root container.
NetworkPolicy permits only the pilot controller and trusted Substrate egress
inbound, with outbound access limited to DNS and the existing Grafana workload.
A separate additive ingress policy admits pilot controller discovery to the
existing Kubernetes tools; factory access and its egress restrictions are unchanged.

Source and focused boundary tests:
`Q:\src\tools\HostCapabilities\deployment\management\pilot.py` and `test_pilot.py`.
Reapply from primary WSL root:

```bash
python3 /mnt/q/src/tools/HostCapabilities/deployment/management/pilot.py --apply
```

The live Grafana server discovered **22 tools**. Every selected tool was checked
against its actual catalog, including the current `get_datasource` name.
Dashboard search and datasource reads succeeded through the MCP server with
anonymous Grafana authorization. Dashboard summary, Prometheus `vector(1)` and
a bounded Loki query against a unique nonexistent namespace also passed locally,
without reading private log payloads. Loki 2.9 does not support Prometheus's
`vector(1)` syntax; that is not an authentication failure.

The actual native pilot Agent then completed one public-only model/tool turn,
returned the exact assistant reply `PILOT_GRAFANA_OK`, and incremented the official
MCP server's `query_prometheus` invocation metric. Both the failed pre-inference
probe and successful test Session were retired; the real Agent remains deployed.
No accepted model request was replayed. Native egress uses the canonical DNS
authority without a port, so the server explicitly allows its own DNS name both
with and without `:8000`; unknown hosts still fail closed. Missing MCP admission
tokens were rejected with 401, and unknown hosts with 403.

The real Windows browser-protocol request to
`http://pilot.internal/api/kagent.api.v1.AgentService/ListAgents` returned the new
Agent over gRPC-web. Both MCP bindings are Accepted, the Agent is Ready, and the
original `deterministic-byo` pilot Agent remained Ready. After a completed task,
allow native lifecycle cleanup to settle before deleting its Session; a transient
`FailedPrecondition` there is not a reason to resend inference.

Protected runtime evidence and owned-resource backups live under
`/home/nibeauli/kagent-lab/pilot-observability`; do not commit them.

To retire this addition, delete only resources carrying
`app.kubernetes.io/managed-by=host-capabilities-pilot-observability` in `kagent`
and its one additional NetworkPolicy in `cluster-management`. Do not delete the
pilot controller, shared tools release, existing pilot agents, business agents or
the Grafana service. Retiring the agent does not disable anonymous Grafana viewing.

## Control-plane store warning

Helm encountered `database is locked` in **MicroK8s k8s-dqlite/Kine**, not in
the agent PostgreSQL database. Read-only journal history already shows these
errors at `2026-11-03T18:04:20Z` (10:04 PST yesterday), before the consolidation.
They recur on controller lease/TTL writes as well as occasional configuration
writes. API `/readyz` (including storage), all three nodes, PostgreSQL and disk
space checks currently pass; that does not make the intermittent write failures
healthy or establish a cause/corruption diagnosis.

No disk/quorum reset, broad restart or power-policy change was performed.
A separate metrics-port change in the new tools chart caused a Helm Service
merge conflict; only that owned Service's port list was repaired. The resulting
Helm release reconciled successfully. Do not confuse that configuration error
with the pre-existing datastore contention.
