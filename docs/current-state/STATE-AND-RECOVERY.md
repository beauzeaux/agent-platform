# State, failure domains, and recovery

[Index](README.md) | [Inspection and operations](OPERATIONS.md).

## Disk layout

Each Windows host has a dedicated **750 GiB native ext4 VHDX**:

```text
Q:\MicroK8sStorage\hostpath.vhdx
  -> attached WSL block disk (currently /dev/sde)
  -> /mnt/microk8s-hostpath
  -> /mnt/microk8s-hostpath/kagent-lab/ate
  -> shared bind mount /var/lib/ate
```

All three disks and mount paths were checked live. `/var/lib/ate` had shared propagation on the remote hosts;
primary's active propagation unit makes it recursively shared. Mount units use a saved filesystem UUID, not the
unstable `/dev/sde` name.

Primary's WSL root is a **different** ext4 disk, currently `/dev/sdd`. `/mnt/q` is Windows DrvFS/9p, not the dedicated
ext4 path. Moving a file under `/mnt/q` is not equivalent to placing it on the Kubernetes storage disk.

| Host | Dedicated ext4 used / available at inspection |
|---|---|
| Primary | About 7.4 GiB / 723 GiB |
| XL | About 26 GiB / 705 GiB |
| devbox-2 | About 2.7 GiB / 728 GiB |

`Q:\tools\microk8s-storage\Initialize-MicroK8sHostpath.ps1` owns Windows attachment state.
The installed state/scripts are under `%ProgramData%\MicroK8sStorage`.
The initializer has separate `Setup`, `Attach`, `Verify`, and `RemoveTask` actions. **Do not use provisioning/formatting
as recovery for an existing disk.**

## Kubernetes persistent volumes

| Consumer | Requested size | Storage class | Binding/failure domain |
|---|---:|---|---|
| `ate-system/rustfs-data` | 100 GiB | `microk8s-hostpath-devdrive` | Node affinity to XL |
| `kagent/kagent-postgresql` | 10 GiB | `microk8s-hostpath-devdrive` | Node affinity to XL |
| `kagent/kagent-lab-registry` | 5 GiB | `microk8s-hostpath-devdrive` | Node affinity to XL |

The default class is `microk8s-hostpath-devdrive`: `Retain`, `WaitForFirstConsumer`, no expansion.
The older `microk8s-hostpath` class also exists and uses `Delete`.
Hostpath requested capacity is not an enforced filesystem quota or distributed storage allocation.

Retained PV data lives under claim-specific directories in `/mnt/microk8s-hostpath`, not a replicated cluster volume.
Moving a pod to another node does not move this data.

## Other state

| State | Location / durability | Handling |
|---|---|---|
| Kagent task/session database | XL PostgreSQL PVC | Original and isolated factory schema share one database service |
| Gateway queue/campaign task correlation | PostgreSQL `factory.gateway_tasks` | Individual role namespaces share this table via a gateway key column |
| Actor workspace/cache/runtime state | `/var/lib/ate` on assigned hosts plus Substrate snapshot objects in RustFS | Snapshot continuation is not arbitrary process/RAM restoration |
| Factory credentials, operator tokens, CA, service configuration | Root-private `/home/nibeauli/kagent-lab/factory-coding` | Do not copy into docs or source control |
| Host bridge certificates/configuration | Root-private `/home/nibeauli/kagent-lab/proxy-access` | Dedicated mTLS trust; copied bridge source is separate from config |
| Windows Agency authority/bindings | `Q:\tools\kagent-lab\agency-broker\state` | User/SYSTEM/admin ACL-protected; public UID binding export is separate |
| ADO CA/account selection | `%LOCALAPPDATA%\AdoDevProxy` | DPAPI-protected signing blob; public `root.crt`; WAM tokens not exported |
| Local LLM Gateway credential/key | `%APPDATA%\local-llm-gateway` | Host credential and gateway key are separate; files exist, contents not inspected |
| WireGuard identity | Each host's protected `/etc/wireguard` | Only public site identity belongs in infrastructure inventory |
| Kubernetes HA control plane | Three dqlite members | Raw member restoration is not safe against a running unrelated cluster state |

Normal Substrate settlement can snapshot volumes. Task history, retained actor metadata, and a successful SDK turn are
not proof that external side effects happened exactly once or that every in-memory agent context is recoverable.

## Observability is ephemeral

No observability PVCs were found.

| Component | Actual storage | Consequence |
|---|---|---|
| Prometheus | `emptyDir`; configured retention `10d` | Ten days is a retention target, not guaranteed survival of pod replacement |
| Loki | `emptyDir` | Logs can disappear on pod replacement |
| Grafana | `emptyDir`; dashboards/data sources also provisioned by sidecars | Provisioned content can return; ad hoc UI state is not equivalent to durable storage |
| Alertmanager | `emptyDir` | Local state is ephemeral |
| Tempo | Only `/conf` mounted; traces/WAL in container filesystem | Container recreation can discard traces; source config retention is 24h |

Do not treat these services as a durable audit record of agent work.

## Backups found

Primary contains these protected backup directories:

```text
/mnt/microk8s-hostpath/kagent-lab/backups/20261031T152556Z
/mnt/microk8s-hostpath/kagent-lab/backups/20261031T154412Z
/mnt/microk8s-hostpath/kagent-lab/backups/20261031T154641Z
```

The first two have incomplete-looking inventories; presence is not completion.
The existing operations record identifies `20261031T154641Z` as its verified completed boundary: logical database/roles,
configuration/signing identities, RustFS data, and private registry. That historical drill compared 42 application
tables and 55 objects and checked a restored image manifest. Those integrity checks were **not rerun** in this inventory.

The coordinated helper is `Q:\tools\kagent-lab\backup-and-test-recovery.py`. It deliberately stops writers/services
during a maintenance boundary and creates an isolated recovery namespace. **It is not a read-only health check.**

Important limitations:

- The verified backup predates the November 2-3 factory/swarm changes; recovery coverage for today's factory state is
  not established by that older result.
- XL-to-primary copying is a second-host copy, not an off-site/offline backup.
- Archives include credentials/signing authority and need protected encrypted export.
- A database/object comparison is not a full restored-cluster, restored-session execution drill.
- Existing raw MicroK8s argument/datastore backups under
  `/var/snap/microk8s/common/kagent-lab-backup-20261031` are historical rollback material, not proof of current recovery.

## Startup and failure domains

| Loss/event | Likely impact |
|---|---|
| Primary Windows user session unavailable | WAM-backed Git/package proxy and Agency/MCP/plugin authority unavailable; configured inference host also unavailable |
| Primary WSL unavailable | All role A2A gateways, Swarm Manager, host capability bridges, operator forwards unavailable |
| XL WSL/storage unavailable | PostgreSQL, RustFS snapshots, and private registry unavailable despite remaining Kubernetes quorum |
| Azure hub unavailable | Inter-host overlay breaks; cluster quorum/communication and actor control affected |
| Dedicated storage not attached | Guarded worker/MicroK8s services should not fall back silently onto the WSL root filesystem |
| One controller/worker pod ready | Does not establish database, actor, model, or host capability health |

`wg-quick` is enabled on primary and the three-node overlay was reachable.
The mount/propagation dependencies precede MicroK8s containerd/kubelite.
Windows disk attachment remains a separate prerequisite.
The LLM gateway was subsequently restored as a detached background process only; no automatic Windows logon/reboot
startup was installed by that action.

No `MicroK8s*` scheduled task was found on primary or XL. devbox-2 has:

```text
Task: MicroK8s-Host-Recovery
Trigger: interactive user's logon, delayed startup; not recurring polling
Script: C:\ProgramData\MicroK8sStorage\Recovery\Manage-MicroK8sHostRecovery.ps1
Last recorded run: 2026-11-03 19:01:32 UTC
Last task result: 1
Status Phase: Failed
Status message: wsl.exe exited with code 1
```

The task is currently Ready, while the Kubernetes node is also Ready. Therefore its last failure cannot be interpreted
as current node downtime, nor can current node health establish reliable next-logon recovery.
The old `MicroK8s-Hostpath-Disk` task described in earlier notes was not found on either remote host.

Recovery should proceed bottom-up: existing disk attachment/UUID -> WireGuard/SSH -> mounts/propagation ->
MicroK8s/quorum -> XL persistent services -> Substrate/Kagent -> Windows capability services and bridges ->
role frontends -> operator forwards. Do not automatically replay uncertain work while restoring dependencies.
