# Helm boundaries

| Package | Helm role |
|---|---|
| `host-gateway` | Cluster services/configuration may be charted; Windows processes and protected config are not |
| `host-network` | `proxy-access` is charted; WireGuard, WSL routes, firewall and disk attachment are not |
| `agent-runtime` | Builds OCI images; generic runtime manifests may be templates, not image-building hooks |
| `agent-workloads` | Repository charts/values consume prebuilt immutable image digests |
| `kagent-platform` | Primary owner of Kagent/Substrate/controller/worker charts and values |
| `identity` | Egress, service accounts and public policy projections may be charted; host actor registry/WAM are not |
| `platform-state` | Registry, databases, memory and archive services should be charted |
| `observability` | Collectors and telemetry backends should be charted |
| `operations` | May compose releases and run health/cleanup jobs; host lifecycle remains explicit automation |

Charts stay beside their owning package. A future umbrella chart may compose package charts but must not absorb their
templates or become the authoritative source for component configuration.
