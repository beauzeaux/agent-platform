# Migration plan and status

The repository currently mirrors authoritative source. The live platform still runs from the original locations.
Cutover is package-by-package; copying source is not itself a deployment change.

| Current source | Destination | Import status | Cutover gate |
|---|---|---|---|
| `Q:\src\tools\HostCapabilities` host service | `packages/host-gateway/HostCapabilities` | Imported | Host tests, publish, protected-config migration and service restart |
| `Q:\src\tools\LocalLlmGateway` | `packages/host-gateway/LocalLlmGateway` | Imported | Gateway tests and listener acceptance |
| `Q:\src\tools\ado-dev-proxy` | `packages/host-gateway/ado-dev-proxy` | Imported, retained | WAM tests; legacy MITM frontend remains disabled |
| `Q:\src\tools\wireguard-hub` | `packages/host-network/wireguard-hub` | Imported | Bicep validation and three-peer route/MTU checks |
| `Q:\tools\kagent-lab\proxy-access` | `packages/host-network/proxy-access` | Imported source only | Unit tests, chart render and bridge health |
| `Q:\tools\microk8s-storage` | `packages/host-network/microk8s-storage` | Imported | Existing-disk attach/verify checks; never format during migration |
| HostCapabilities Agency runtime | `packages/agent-runtime` | Imported | AgencyCoder tests, OCI smoke, golden revision |
| HostCapabilities engsys/Audit layers | `packages/agent-workloads` | Imported | Repository-specific image build and disposable actor acceptance |
| `Q:\tools\kagent-lab` platform/factory source | `packages/kagent-platform` | Imported selected source | Controller cache health, chart/value validation, worker and session tests |
| `Q:\tools\kagent-lab\agency-broker` | `packages/identity/agency-broker` | Imported source/policy | Broker/relay tests and exact actor enrollment/revocation |
| Kagent registry/archive/memory source | `packages/platform-state` | Imported | Backup, restore and state-owner verification |
| OTel/Tempo/monitor source | `packages/observability` | Imported | Collector/backend readiness and trace/metric/log correlation |
| `Q:\src\environment-map` and lifecycle scripts | `docs/current-state`, `packages/operations` | Imported | Links updated and operator runbook exercised |

## Migration rules

1. Make behavior-preserving path/build changes first.
2. Publish immutable artifacts from this repository and record digests.
3. Back up the exact live resource/configuration before cutover.
4. Change one owning subsystem at a time.
5. Run its package acceptance and cross-boundary health check.
6. Retain the former source/service as rollback until the new owner is accepted.
7. Retire duplicates only after source, deployment, documentation, and recovery ownership are all transferred.

## Explicit exclusions

- `Q:\tools\agent-fabric`: separate uncommitted prototype; not imported.
- legacy `engsys-coder`, `engsys-agency-coder`, and old BYO harness implementations: superseded by `agent-runtime`;
  retain externally until migration review determines whether historical code is needed.
- runtime `.artifacts`, package caches, node modules, compiled binaries, actor/session state, plugin archives, registry
  contents, database data, private configuration, credentials, certificates, and keys.
