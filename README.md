# Agent Platform

`agent-platform` is the consolidated source repository for the three-devbox agent environment. It separates
independently deployable concerns while keeping their contracts and operational documentation together.

| Package | Responsibility |
|---|---|
| `host-gateway` | Windows HostCapabilities, local LLM gateway, WAM-backed IMDS, and retained ADO proxy source |
| `host-network` | Azure WireGuard hub, WSL routing, proxy-access bridge, and MicroK8s host storage networking |
| `agent-runtime` | Repository-neutral Agency harness/coder runtime and OCI build inputs |
| `agent-workloads` | Repository-specific agent image layers and deployment values |
| `kagent-platform` | Kagent/Substrate installation, worker pools, factory controller, and native platform resources |
| `identity` | Actor enrollment, authorization profiles, policy/plugin broker, and management surfaces |
| `platform-state` | Registry, snapshot/archive, database, backup, and recovery deployment source |
| `observability` | OTel, metrics, logs, traces, dashboards, and observability integration values |
| `operations` | Health checks, lifecycle automation, certificates, maintenance, and operator runbooks |

The current live system is not deployed from this repository yet. See `docs/MIGRATION.md` for source provenance,
cutover gates, and retirement conditions.

## Repository rules

- Every package owns its source, tests, documentation, build recipe, and Helm chart where applicable.
- Helm deploys Kubernetes resources only. Host installation remains explicit PowerShell, shell, Bicep, or systemd
  automation.
- OCI images are built before Helm deployment and consumed by immutable registry digest.
- Repository-specific layers remain thin and inherit the shared runtime.
- Generated `.artifacts`, protected host configuration, actor/session state, and runtime caches remain outside Git.

## Refreshing the imported baseline

The initial migration is reproducible:

```powershell
.\scripts\import-current.ps1
.\scripts\validate-layout.ps1
```

`import-current.ps1` copies authoritative source files from their original locations while respecting Git ignore
rules and explicit protected-path exclusions. It does not remove or alter the original live sources.
