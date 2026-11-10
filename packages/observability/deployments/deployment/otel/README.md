# Private host telemetry

Pinned official Collector chart `0.178.2`, contrib image `0.163.2`.
This extends existing Tempo/Prometheus; it does not replace existing logging.

Run inside primary WSL as root:

```bash
python3 /mnt/q/src/tools/HostCapabilities/deployment/otel/manage.py install
python3 /mnt/q/src/tools/HostCapabilities/deployment/otel/verify.py
python3 /mnt/q/src/tools/HostCapabilities/deployment/otel/dashboard.py
```

Explicit leaf renewal uses `install --renew-leaves`. It replaces only this
component's server/client leaves (48-hour validity), preserves the CA, republishes
client material, and rolls only this Collector using a public certificate hash.
Restart/reload the host exporter after updating its pinned certificate.

The installer verifies the chart archive checksum and uses an externally prepared
TLS Secret. Private CA/client state remains root-only under
`/home/nibeauli/kagent-lab/host-observability`; Windows client material is protected
for the signed-in user, SYSTEM, and local administrators under
`%LOCALAPPDATA%\HostCapabilities\telemetry`.

Host endpoint: `https://localhost:14318`. Standard systemd socket activation and
`systemd-socket-proxyd` forward raw TLS to the Collector's ClusterIP. Unlike a
long-lived `kubectl port-forward`, this does not depend on a Kubernetes API stream
or a selected Pod remaining alive. Reinstall resolves the stable Service address
and updates source-IP policy from primary's current interfaces.

The receiver requires a client certificate. No Windows root-store changes,
public ingress, NodePort, or credential-bearing HTTP listener is installed.
The NetworkPolicy grants host OTLP only from primary's exact interface addresses.
Internal actor OTLP gRPC has a separate allowlist for Substrate egress.

Traces have allowlisted metadata; payload attributes, status messages, and all
span events are removed. Metric labels have a bounded allowlist. Logs are not
accepted. Tempo receives traces and Prometheus scrapes metrics through a
release-labelled ServiceMonitor. Do not send private session exports to this
pipeline.

Verified at `2026-11-03T23:33:27Z`: authenticated ingestion, anonymous rejection,
Tempo delivery, Prometheus delivery, and removal of synthetic payload markers.
Protected report: `host-observability/verification.json`.

The host exporter uses the explicit `pinned-private` profile: validate private
root, DNS, validity, server EKU, and exact SHA-256 leaf pin. The short-lived pilot
issuer has no public CRL; this does not disable certificate validation or pretend
online revocation succeeded. The online profile remains strict by default.

The existing Grafana sidecar provisions the owned ConfigMap dashboard at
`http://localhost:13000/d/host-capabilities`. It uses the existing `prometheus`
datasource and `exported_job="host-capabilities"` service label. Panels cover
active requests/streams/MCP logical sessions, capability readiness, outer/nested
tool calls, known downstream codes, and completed-request latency. MCP sessions
are not Kagent conversations; token usage/cost is not inferred. The intentionally
disabled native ADO provider is excluded from compatibility-module readiness.
