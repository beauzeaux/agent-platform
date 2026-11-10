# Inspection and operational entry points

[Index](README.md) | [Recovery dependencies](STATE-AND-RECOVERY.md).

## Browser entry points on this Windows host

| URL | Purpose | Verification |
|---|---|---|
| `http://localhost:18452/dashboard` | Read-only Swarm Manager dashboard | HTTP 200 |
| `http://localhost:18452/api/campaigns` | Read-only redacted campaign/topology projection | Available; do not dump task content into shared logs |
| `http://localhost:18452/events` | Swarm server-sent events | Documented route; stream not separately exercised |
| `http://localhost:18080` | **Factory** Kagent UI | HTTP 200; no longer the original pilot UI |
| `http://localhost:13000` | Grafana | Health HTTP 200 |
| `http://localhost:19090` | Existing Prometheus forward | Target API worked |
| `http://localhost:13100/ready` | Existing Loki forward | HTTP 200 |
| `http://localhost:13200` | Existing Tempo query forward | Stale/nonresponsive at inspection |

The authenticated WireGuard dashboard/control endpoint is `https://10.82.0.2:18451`.
Use the established CA and protected operator credentials; do not disable certificate validation.
The separate localhost dashboard cannot submit A2A writes.

## Safe Windows inventory

These commands inspect state; they do not start a distro, restore disks, or request inference.
`wsl --list` reports registered distributions; the later WSL commands assume the distribution is already running.

```powershell
wsl.exe --list --verbose
Get-NetTCPConnection -State Listen |
    Where-Object LocalPort -in 4141,5443,5480,5481,15442,13000,18080,18452
Get-ScheduledTask -TaskName 'Kagent Agency Broker'
Get-ScheduledTask | Where-Object TaskName -like '*MicroK8s*'
netsh interface portproxy show all
podman machine list
```

Token-free health:

```powershell
curl.exe --noproxy '*' --max-time 5 http://127.0.0.1:5481/health
curl.exe --noproxy '*' --max-time 5 --output NUL --write-out '%{http_code}' http://127.0.0.1:15442/health
curl.exe --noproxy '*' --max-time 5 http://172.24.160.1:4141/health
curl.exe --noproxy '*' --max-time 5 http://127.0.0.1:13000/api/health
```

Do not print full process environments, broker configuration, Kubernetes Secret objects, LLM credentials, or WAM token
responses. Command lines can also contain secrets; the investigation only inspected targeted/redacted launch metadata.

The LLM gateway is now running as an operator-requested detached process on the WSL-facing address, not on Windows
loopback. Its startup PID was 37432 and the background execution handle is `local-llm-gateway`.
Host/bridge metadata checks passed with 30 models. No scheduled task or permanent startup configuration was added.
Use the [recorded launch command](EVIDENCE.md) if deliberately restarting it later, after checking port ownership first.

## Safe WSL and Kubernetes inventory

Use root for MicroK8s status: the unprivileged wrapper prints a misleading permission traceback when reading its
arguments, even though ordinary user Kubernetes reads work.

```powershell
wsl.exe -d Ubuntu-26.04 --user root --exec /snap/bin/microk8s status
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get nodes -o wide
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get pods -A -o wide
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get deployments,statefulsets,daemonsets -A
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s helm3 list -A
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get sc,pv
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get pvc -A
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get agents,workerpools,remotemcpservers -A
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl get workflows,workflowtemplates,cronworkflows -A
wsl.exe -d Ubuntu-26.04 --exec systemctl --failed --no-pager
```

Inspect the networking/storage layers inside an existing WSL shell:

```bash
ip -brief address
ip route
findmnt -T /mnt/microk8s-hostpath -o SOURCE,TARGET,FSTYPE,PROPAGATION
findmnt -T /var/lib/ate -o SOURCE,TARGET,FSTYPE,PROPAGATION
df -h /mnt/microk8s-hostpath
systemctl status wg-quick@wg0 kagent-proxy-access kagent-ui-host slopmop-grafana-host --no-pager
systemctl list-timers --all --no-pager
```

Avoid `wg showconf` and `wg ... dump`, which can expose private key material.
If inspecting endpoints/handshakes, omit public-key columns from copied output as well.

## Substrate actors are a separate inventory

Kubernetes Pod readiness does not show actor state. In a root WSL shell:

```bash
export KUBECONFIG=/var/snap/microk8s/current/credentials/client.config
/home/nibeauli/kagent-lab/artifacts/kubectl-ate --preview get actors -a proxy-access
/home/nibeauli/kagent-lab/artifacts/kubectl-ate --preview get actors -a factory-executions
```

Read only metadata/state/assigned-node fields when retaining diagnostics. Actor output may otherwise include
configuration that should remain protected.
The original `engsys-agency-coder` crash is not a reason to delete its workspace or recreate it blindly.

The existing `test-byo-live.py`, `test-byo-workers.py`, `test_swarm_live.py`, actor/proxy tests, and contributor task
commands are **not passive inspection**: they can create sessions, execute work, change lifecycle state, or spend quota.
They were not run for this mapping task.

## Finding implementation and configuration

| Concern | First place to inspect |
|---|---|
| WireGuard desired site inventory | `Q:\src\tools\wireguard-hub\main.bicepparam` |
| WSL networking bootstrap | `Q:\src\tools\wireguard-hub\scripts\README.md` and `setup-wsl-wireguard.sh` |
| Storage attach/verify | `Q:\tools\microk8s-storage\Initialize-MicroK8sHostpath.ps1` |
| Base Kagent/Substrate installation | `Q:\tools\kagent-lab\install-platform.sh`, `kagent-values.yaml`, `substrate-values.yaml` |
| Protected prepared charts/source | `/home/nibeauli/kagent-lab/artifacts`, `source`, `config` |
| Host capability integration | `Q:\tools\kagent-lab\proxy-access\manage.py`, `host_bridge.py`, `chart` |
| Actual bridge target addresses | `/home/nibeauli/kagent-lab/proxy-access/bridge.json`, inspect selected nonsecret fields only |
| Windows ADO policy | `Q:\src\tools\ado-dev-proxy\proxy.example.json` |
| Windows LLM behavior/startup | `Q:\src\tools\LocalLlmGateway\README.md` |
| Agency MCP policy | `Q:\tools\kagent-lab\agency-broker\profiles.json`, `broker.py`, `configure.py` |
| Factory controller, roles, deployment | `Q:\tools\kagent-lab\factory-coding\install.py`, `controller.go`, `swarm_roles.py` |
| Gateway task persistence | `Q:\tools\kagent-lab\factory-coding\gateway_store.py`, `backend.py` |
| Autonomous loop, dashboard, traces | `swarm_autonomous.py`, `swarm_manager.py`, `otel_exporter.py` in `factory-coding` |
| Detailed component runbooks | `Q:\tools\kagent-lab\OPERATIONS.md`, `factory-coding\SWARM-OPERATIONS.md` |
| devbox-2 logon recovery | Its local `C:\ProgramData\MicroK8sStorage\Recovery` task/script/status, not primary's similarly named source package |

## Recovery order, not an automatic repair script

1. Establish which hosts and data are intact. Check dedicated ext4 mounts and saved attachment state before starting
   workloads. Use existing `Attach`/`Verify` paths for an existing disk, never create/format another disk to fix a mount.
2. Restore the WireGuard path and Kubernetes quorum. Verify all storage guards before MicroK8s runtime startup.
3. Verify XL PostgreSQL, RustFS, and registry availability, then Substrate/Kagent workers and actor state.
4. Verify the signed-in Windows capability services. Reauthentication must happen on Windows, not by handing tokens
   to actors. Restore the configured LLM listener/bind address before expecting model requests to work.
5. Verify the mTLS bridge and cluster proxy, then role frontends. Read uncertain/active tasks before any resubmission.
6. Re-establish UI/diagnostic access independently from backend recovery.

The existing storage initializer requires elevation. The coordinated backup helper interrupts services.
Broker restart closes its owned shared gateway/backend process tree. Proxy redeployment can invalidate RAM leases.
These operations need an explicit maintenance decision; they are not harmless inspection commands.

## Stale Tempo forward

Direct cluster readiness at `http://10.152.183.202:3200/ready` returned 200 while the existing localhost forward failed.
If diagnostic access is needed later, first identify the exact existing forward/owner. A separate foreground forward
on an unused port avoids killing an unrelated process:

```powershell
wsl.exe -d Ubuntu-26.04 --exec /snap/bin/microk8s kubectl -n observability port-forward service/tempo 13201:3200 --address=127.0.0.1
```

This starts an access process and should be stopped after use. It was not started during the investigation.
Do not infer a broken backend from a failed old localhost URL.
