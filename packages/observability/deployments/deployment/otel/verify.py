"""Verify authenticated ingestion, metadata filtering, and backend delivery."""
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

STATE = Path("/home/nibeauli/kagent-lab/host-observability")
KUBE = ["/snap/bin/microk8s", "kubectl"]
MARKER = "HOST_OTEL_PAYLOAD_MUST_BE_REMOVED"


def service(name):
    value = subprocess.check_output([*KUBE, "-n", "observability", "get", "service", name, "-o", "json"])
    return json.loads(value)["spec"]["clusterIP"]


def opener(authenticated=True):
    context = ssl.create_default_context(cafile=str(STATE / "ca.crt"))
    if authenticated:
        context.load_cert_chain(str(STATE / "client.crt"), str(STATE / "client.key"))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))


def request(client, url, body=None):
    message = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    with client.open(message, timeout=8) as response:
        return json.load(response)


def wait_for(check, seconds=60):
    end = time.monotonic() + seconds
    while True:
        try:
            result = check()
            if result:
                return result
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        if time.monotonic() >= end:
            raise RuntimeError("Telemetry backend verification timed out.")
        time.sleep(2)


def main():
    client = opener()
    now = time.time_ns()
    trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
    span = {
        "traceId": trace_id, "spanId": span_id, "name": "host.canary", "kind": 2,
        "startTimeUnixNano": str(now - 1_000_000), "endTimeUnixNano": str(now),
        "attributes": [
            {"key": "host.capability", "value": {"stringValue": "canary"}},
            {"key": "host.outcome", "value": {"stringValue": "success"}},
            {"key": "host.operation", "value": {"stringValue": "canary-operation"}},
            {"key": "host.actor.profile", "value": {"stringValue": "canary-profile"}},
            {"key": "gen_ai.input.messages", "value": {"stringValue": MARKER}},
        ],
        "events": [{"name": "sensitive-event", "timeUnixNano": str(now),
                    "attributes": [{"key": "payload", "value": {"stringValue": MARKER}}]}],
    }
    resource = {"attributes": [{"key": "service.name", "value": {"stringValue": "host-capabilities-canary"}},
                                {"key": "unapproved", "value": {"stringValue": MARKER}}]}
    traces = {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": {"name": "host.canary"}, "spans": [span]}]}]}
    wait_for(lambda: request(client, "https://localhost:14318/v1/traces", traces) is not None, seconds=30)
    try:
        request(opener(False), "https://localhost:14318/v1/traces", traces)
    except (urllib.error.URLError, ssl.SSLError):
        pass
    else:
        raise RuntimeError("Collector incorrectly accepted a caller without its client certificate.")
    metrics = {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{
        "scope": {"name": "host.canary"},
        "metrics": [{"name": "host.canary.requests", "unit": "1", "sum": {
            "aggregationTemporality": 2, "isMonotonic": True,
            "dataPoints": [{"startTimeUnixNano": str(now - 1_000_000_000), "timeUnixNano": str(now), "asInt": "1",
                            "attributes": [{"key": "module", "value": {"stringValue": "canary"}},
                                           {"key": "layer", "value": {"stringValue": "canary-layer"}},
                                           {"key": "prompt", "value": {"stringValue": MARKER}}]}],
        }}],
    }]}]}
    request(client, "https://localhost:14318/v1/metrics", metrics)
    plain = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    tempo_url = "http://" + service("tempo") + ":3200/api/traces/" + trace_id
    trace = wait_for(lambda: request(plain, tempo_url))
    if MARKER in json.dumps(trace):
        raise RuntimeError("Collector retained disallowed payload data in a trace.")
    if not all(value in json.dumps(trace) for value in ("canary-operation", "canary-profile")):
        raise RuntimeError("Collector lost allowlisted operation/profile trace metadata.")
    prometheus_url = "http://" + service("kube-prom-stack-kube-prome-prometheus") + ":9090/api/v1/query?"
    query = urllib.parse.urlencode({"query": 'host_canary_requests_total{layer="canary-layer"}'})
    result = wait_for(lambda: request(plain, prometheus_url + query).get("data", {}).get("result"))
    if MARKER in json.dumps(result) or not any(
        item["metric"].get("module") == "canary" and item["metric"].get("layer") == "canary-layer"
        for item in result
    ):
        raise RuntimeError("Prometheus metadata filtering or delivery failed.")
    report = {"authenticatedIngestion": True, "anonymousRejected": True, "tempoDelivered": True,
              "prometheusDelivered": True, "payloadFiltered": True, "traceId": trace_id,
              "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = STATE / "verification.json"
    path.write_text(json.dumps(report, indent=2))
    path.chmod(0o600)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
