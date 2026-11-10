# Architecture

```mermaid
flowchart LR
    U[Operator and repository CI] --> AW[agent-workloads]
    AW --> AR[agent-runtime OCI images]
    KP[kagent-platform] --> AR
    KP --> ID[identity]
    KP --> ST[platform-state]
    KP --> OB[observability]
    AR --> HG[host-gateway]
    ID --> HG
    HG --> WAM[Windows WAM]
    KP --> HN[host-network]
    HN --> WG[WireGuard overlay]
    OP[operations] -. lifecycle and validation .-> KP
    OP -. lifecycle and validation .-> HG
    OP -. lifecycle and validation .-> HN
```

## Boundaries

- `host-network` establishes stable node and cluster-to-host connectivity.
- `kagent-platform` owns control-plane, worker, actor/session, and golden-snapshot lifecycle.
- `agent-runtime` supplies repository-neutral execution images.
- `agent-workloads` supplies repository-specific derived images and deployment values.
- `identity` authorizes actors, profiles, resources, policy, and plugin preparation.
- `host-gateway` performs host-only WAM, IMDS, Agency, and inference operations.
- `platform-state` and `observability` are independently deployable dependencies.
- `operations` coordinates lifecycle without becoming another implementation of each package.
