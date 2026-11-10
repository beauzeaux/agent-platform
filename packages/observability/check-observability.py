import json
import time
import urllib.parse
import urllib.request

from lab_transport import BASE, port_forward

with port_forward("observability", "kube-prom-stack-kube-prome-prometheus", 19090, 9090) as origin:
    for attempt in range(12):
        with urllib.request.urlopen(origin + "/api/v1/targets", timeout=10) as response:
            targets = json.load(response)["data"]["activeTargets"]
        selected = [
            target for target in targets
            if target["labels"].get("namespace") in {"kagent", "ate-system"}
        ]
        if len(selected) >= 6 and all(target["health"] == "up" for target in selected):
            break
        time.sleep(10)
    else:
        raise RuntimeError(json.dumps([
            {"job": target["labels"].get("job"), "health": target["health"], "error": target["lastError"]}
            for target in selected
        ]))
    report = [{"job": target["labels"].get("job"), "health": target["health"], "scrapeUrl": target["scrapeUrl"]} for target in selected]
    path = BASE / "protected/observability-report.json"
    path.write_text(json.dumps(report, indent=2))
    path.chmod(0o600)
with port_forward("observability", "tempo", 13200, 3200) as origin:
    with urllib.request.urlopen(origin + "/api/search?limit=100", timeout=30) as response:
        traces = json.load(response)["traces"]
    pilot_traces = [
        trace for trace in traces
        if trace["rootServiceName"] in {"atelet", "ateapi", "atecontroller", "kagent-controller"}
    ]
    assert pilot_traces, "Tempo has no actual pilot traces"

with port_forward("observability", "loki", 13100, 3100) as origin:
    query = urllib.parse.urlencode({
        "query": '{namespace="kagent",pod=~"kagent-controller-.*"}',
        "limit": 5, "start": str(int((time.time() - 7200) * 1e9)),
        "end": str(int(time.time() * 1e9)),
    })
    with urllib.request.urlopen(origin + "/loki/api/v1/query_range?" + query, timeout=30) as response:
        streams = json.load(response)["data"]["result"]
    assert streams and any(stream["values"] for stream in streams), "Loki has no controller logs"

telemetry = {
    "result": "PASS", "healthy_kagent_substrate_metrics_targets": len(report),
    "tempo_trace_ids": [trace["traceID"] for trace in pilot_traces],
    "loki_controller_streams": len(streams),
}
path = BASE / "protected/telemetry-report.json"
path.write_text(json.dumps(telemetry, indent=2))
path.chmod(0o600)
print(json.dumps(telemetry))
