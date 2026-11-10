# Single-host cluster portal

Canonical hostname: **cluster.internal**. Windows hosts mapping:

```text
127.0.0.1 cluster.internal
```

The loopback-only Caddy front door also accepts localhost for local testing.
Existing service hostnames remain compatibility aliases.

| Path | Current state |
|---|---|
| `/` | Portal service list |
| `/kagent/` | Shared Kagent dashboard; pages/assets and UI prefix validated |
| `/headlamp/` | Read-only Headlamp, normal viewer-token login |
| `/tempo/ready` | Tempo readiness; other Tempo API paths beneath `/tempo/` |
| `/loki/ready` | Loki readiness; native query API at `/loki/api/v1/` |
| `/grafana/api/` | Grafana API proxy; browser UI temporarily redirects to its old hostname |
| `/prometheus/api/` | Prometheus API proxy; browser UI temporarily redirects to its old hostname |

Kagent assets, navigation, deep-link reloads and read-only RPCs were tested
by the UI integration check. Final front-door page/assets checks returned 200
for Kagent and Headlamp. Tempo/Loki readiness, Grafana health and Prometheus
build-info APIs returned 200 through the new prefix routes.

**Consolidation is not complete:** Grafana and Prometheus native browser subpaths
require configuration restarts. Both currently use ephemeral storage. No
data-losing restart was performed; preserve-data versus acceptable-reset policy
remains unconfirmed. Their transitional redirects are explicit, not claimed as
completed single-host browser routing.

No authentication, read-only authority, Kagent controller namespace watches,
model endpoints or agent runtime was changed by front-door routing.
Source: `Q:\src\tools\HostCapabilities\deployment\access`.

The legacy pilot Helm release `kagent`, `/pilot/` route and `pilot.internal`
alias were retired on 2026-11-06. Namespace `kagent` remains because it contains
shared PostgreSQL, registry and Grafana MCP infrastructure; `kagent-crds` remains
cluster-wide infrastructure used by the factory controller.
