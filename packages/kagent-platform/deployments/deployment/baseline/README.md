# Idle baseline and management fleet

Use the existing factory controller and `http://kagent.internal` dashboard.
Select `baseline` or `cluster-management` using the namespace selector.
No new controller/UI, recurring scheduler or model loop is deployed.

From primary WSL root:

```text
python3 /mnt/q/src/tools/HostCapabilities/deployment/baseline/fleet.py
python3 /mnt/q/src/tools/HostCapabilities/deployment/baseline/fleet.py --apply
```

`register.py --apply` enrolls both namespaces in the shared controller's explicit
watch/RBAC scope. `fleet.py --apply` then deploys namespace-local native worker
pools, runtime spaces, label-scoped credential grants and five persistent agents:

- `baseline/baseline-coder`: MAI Code 2, Responses, fixed public coding fixture.
- `baseline/baseline-sol`: GPT-6.1 Sol, Responses, read-only synthetic MCP verifier.
- `baseline/baseline-argon`: Gemini-4 Argon, Chat Completions, equivalent verifier.
- `cluster-management/cluster-manager`: official Go ADK, read-only Kubernetes.
- `cluster-management/cluster-observability`: official Go ADK, read-only Kubernetes,
  Prometheus, Grafana and Loki. Reuses the existing pinned `kagent/pilot-grafana-mcp`
  service with anonymous Grafana Viewer; no Grafana admin credential is copied.

The cached immutable fixture is reused without rebuilding. Baseline is created
with its live gate **off**: no SendMessage or inference starts during deployment.
Any future live validation requires a prospective explicit live setup. Recurring
work additionally requires an approved cadence and stop/quota policy. Never
enable the old factory admission services or resurrect retired Agent definitions.

Private rollback snapshots, namespace/space UIDs and resource receipts are stored
under `/home/nibeauli/kagent-lab/baseline`; fixture state uses the existing
`a2a-sanity/current.json` receipt contract. Existing credential grants, storage,
archives and the original pilot infrastructure are retained.

Legacy sessions whose native DeleteSession reports unsettled lifecycle cleanup
remain archived/retained. Do not force-edit the database or kill actors to remove
them; they do not prevent the new workload namespaces from becoming ready.
