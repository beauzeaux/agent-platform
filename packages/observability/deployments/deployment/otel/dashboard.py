"""Provision a metadata-only view using the existing Grafana/Prometheus stack."""
import json
import subprocess

KUBE = ["/snap/bin/microk8s", "kubectl"]
NAME = "host-capabilities-dashboard"
OWNER = "host-capabilities"


def dashboard():
    selector = 'exported_job="host-capabilities"'
    definitions = [
        ("Requests by capability and outcome", f"sum by (module,outcome) (rate(host_requests_total{{{selector}}}[5m]))", "{{module}} {{outcome}}"),
        ("Active requests", f"sum by (module,operation) (host_requests_active{{{selector}}})", "{{module}} {{operation}}"),
        ("Active model streams", f"sum by (module) (host_streams_active{{{selector}}})", "{{module}}"),
        ("MCP logical sessions (not conversations)", f"host_mcp_sessions_active{{{selector}}}", "MCP sessions"),
        ("Approved outer and nested tool calls", f"sum by (layer,toolset,tool,outcome) (rate(host_tool_calls_total{{{selector}}}[5m]))", "{{layer}} {{toolset}} {{tool}} {{outcome}}"),
        ("Completed request latency p95", f"histogram_quantile(0.95, sum by (le,module) (rate(host_request_duration_seconds_bucket{{{selector}}}[5m])))", "{{module}}"),
        ("Known downstream HTTP result codes", f'sum by (module,upstream_status_code) (rate(host_requests_total{{{selector},upstream_status_code!="0"}}[5m]))', "{{module}} {{upstream_status_code}}"),
        ("Enabled compatibility capability readiness", f'host_capability_ready{{{selector},module=~"llm|ado|agency"}}', "{{module}}"),
    ]
    panels = []
    for index, (title, expression, legend) in enumerate(definitions):
        panels.append({"id": index + 1, "type": "timeseries", "title": title,
                       "datasource": {"type": "prometheus", "uid": "prometheus"},
                       "gridPos": {"x": (index % 2) * 12, "y": (index // 2) * 8, "w": 12, "h": 8},
                       "targets": [{"refId": "A", "expr": expression, "legendFormat": legend}],
                       "fieldConfig": {"defaults": {}, "overrides": []},
                       "options": {"legend": {"displayMode": "list", "placement": "bottom"}}})
    panels.append({"id": 20, "type": "text", "title": "Signal boundaries",
                   "gridPos": {"x": 0, "y": 32, "w": 24, "h": 4},
                   "options": {"mode": "markdown", "content":
                       "Metadata only: no prompts, tool bodies, credentials, or signed URLs. "
                       "MCP sessions are logical transport sessions, not Kagent conversations. "
                       "Unknown downstream status is not success; status 0 is excluded from that panel. "
                       "Token usage/cost is not inferred. Native ADO credential mode is intentionally disabled; "
                       "the compatible Git/package policy module remains active. "
                       "For traces, open Tempo Explore and filter service.name = host-capabilities."}})
    return {"uid": "host-capabilities", "title": "Host Capabilities", "schemaVersion": 39, "version": 1,
            "tags": ["host-capabilities", "kagent", "metadata-only"], "timezone": "browser",
            "time": {"from": "now-1h", "to": "now"}, "refresh": "30s", "panels": panels}


if __name__ == "__main__":
    current = subprocess.run([*KUBE, "get", "configmap", NAME, "-n", "observability", "-o", "json", "--ignore-not-found"],
                             capture_output=True, check=True)
    if current.stdout and json.loads(current.stdout)["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("Refusing to overwrite an unowned dashboard.")
    value = {"apiVersion": "v1", "kind": "ConfigMap",
             "metadata": {"name": NAME, "namespace": "observability",
                          "labels": {"grafana_dashboard": "1", "app.kubernetes.io/managed-by": OWNER}},
             "data": {"host-capabilities.json": json.dumps(dashboard())}}
    subprocess.run([*KUBE, "apply", "-f", "-"], input=json.dumps(value).encode(), check=True, capture_output=True)
    print("Host Capabilities dashboard provisioned: http://localhost:13000/d/host-capabilities")
