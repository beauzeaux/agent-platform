# Read-only Headlamp

Open **http://kagent.internal/headlamp/**. No new hosts entry, NodePort or public
ingress is required. The existing loopback Caddy front door preserves the
Headlamp base path and leaves the Kagent dashboard at its original URL.

Headlamp uses normal token login; it is not anonymous or cluster-admin.
Generate an expiring read-only login token directly into the Windows clipboard:

```powershell
& Q:\src\tools\HostCapabilities\deployment\headlamp\Copy-ViewerToken.ps1
```

Paste into Headlamp's token login. The requested lifetime is eight hours; run
the helper again when it expires. Tokens are never printed or stored in source.
The pod service account has no chart-created cluster-role binding. The separate
`headlamp-viewer` account can read namespaces, nodes, workloads, services/events,
storage metadata and selected Kagent/worker CRDs. Secrets, ConfigMaps, pod logs,
exec, port-forward and workload writes are not granted.

## Reapply

Official chart **0.46.0**, SHA-256
`faa471a8c5c55125b3dceed1cf73000e1ced18f07e2088e5e12e75292fb9ae62`.
Its protected cached archive is
`/home/nibeauli/kagent-lab/headlamp/headlamp-0.46.0.tgz`.
Run in primary WSL as root:

```bash
/snap/bin/microk8s kubectl apply -f /mnt/q/src/tools/HostCapabilities/deployment/headlamp/viewer.yaml
/snap/bin/microk8s helm3 upgrade --install headlamp /home/nibeauli/kagent-lab/headlamp/headlamp-0.46.0.tgz -n headlamp -f /mnt/q/src/tools/HostCapabilities/deployment/headlamp/values.yaml --wait
python3 -B /mnt/q/src/tools/HostCapabilities/deployment/access/manage.py --apply
```

Always supply the values overlay: the upstream chart's default binding is
cluster-admin, which this deployment explicitly disables. Never switch on
service-account-token authentication bypass or grant the backend account admin.
The local Kubernetes namespace and cluster RBAC objects carry explicit ownership;
do not adopt an unrelated same-named installation.

## Verified

Windows-facing page and JavaScript asset return 200, as does the unchanged
Kagent dashboard. An actual short-lived viewer token read namespaces/deployments
through Kubernetes and namespaces through Headlamp's own proxy (200). Secrets
reads and a dry-run namespace creation were denied (403); a token-authenticated
self-access review denied exec. No real resource-write or exec probe was executed.
