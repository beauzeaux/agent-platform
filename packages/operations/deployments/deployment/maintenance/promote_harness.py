"""Promote only the owned factory harness/templates after native canary gates."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import time

KUBE = ["/snap/bin/microk8s", "kubectl", "--request-timeout=15s"]
SPACE = "factory-executions"
OWNER = "host-capabilities"
STATE = Path("/home/nibeauli/kagent-lab/host-maintenance")
HOST = "https://host-capabilities.proxy-access.svc.cluster.local:8445"
EMBEDDING = "http://local-embeddings.factory-executions.svc.cluster.local:8080"
NAMES = ["factory-coder", "slopmop-ado-triage", "slopmop-documentation-steward",
         "slopmop-implementation-reviewer", "slopmop-opportunity-scout",
         "slopmop-plan-reviewer", "slopmop-planner", "slopmop-pr-pattern-miner"]


def get(kind, name):
    result = subprocess.run([*KUBE, "get", kind, name, "-n", SPACE, "-o", "json", "--ignore-not-found"],
                            check=True, capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def patch(kind, before, operations):
    current = get(kind, before["metadata"]["name"])
    if current["metadata"]["uid"] != before["metadata"]["uid"] or current["spec"] != before["spec"]:
        raise RuntimeError("Owned resource specification changed; preserve it.")
    operations.insert(0, {"op": "test", "path": "/metadata/resourceVersion",
                          "value": current["metadata"]["resourceVersion"]})
    result = subprocess.run([*KUBE, "patch", kind, before["metadata"]["name"], "-n", SPACE,
                             "--type=json", "-p", json.dumps(operations)], capture_output=True)
    if result.returncode:
        raise RuntimeError("Owned configuration patch failed: " + result.stderr.decode(errors="replace"))


def resource(kind, name, spec):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": kind,
            "metadata": {"name": name, "namespace": SPACE,
                         "labels": {"app.kubernetes.io/managed-by": OWNER}}, "spec": spec}


def promote(image, evidence, apply=False):
    if not re.fullmatch(r"10\.152\.183\.\d+:5000/kagent-lab/factory-coder@sha256:[a-f0-9]{64}", image):
        raise ValueError("Verified immutable private factory image required.")
    proof = json.loads(evidence.read_text())
    for phase in ("bound-metadata", "seed", "isolate", "model", "seed-recurrence"):
        result = proof["tasks"][phase]["result"]
        if result["state"] != "TASK_STATE_COMPLETED" or result["receipt"]["archive"]["status"] != "exported":
            raise RuntimeError("Native model/MCP/memory/archive/recurrence gates must pass first.")
    if proof["tasks"]["isolate"]["result"]["receipt"]["result"]["visibleRows"] != 0:
        raise RuntimeError("Foreign Agent memory was visible.")
    harness = get("harness", "factory-coding")
    agents = [get("agent", name) for name in NAMES]
    templates = [get("agenttemplate", agent["spec"]["template"]["ref"]["name"]) for agent in agents]
    for value in [harness, *agents, *templates]:
        if not value or value["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "factory-coding":
            raise RuntimeError("Refusing to change an unowned factory resource.")
    jwt = {"audiences": ["https://host-capabilities.proxy-access.svc/session"], "expirationSeconds": 300}
    documents = [
        resource("ModelConfig", "host-capabilities-llm", {"provider": "OpenAI", "model": "gpt-5.6-sol",
                 "stream": True, "openAI": {"baseUrl": HOST + "/v1", "apiFormat": "responses"}, "actorJwt": jwt}),
        resource("RemoteMCPServer", "host-capabilities-agency", {"description": "Existing profile-enforced Agency gateway",
                 "protocol": "STREAMABLE_HTTP", "url": HOST + "/agency/mcp", "actorJwt": jwt,
                 "timeout": "1900s", "terminateOnClose": True}),
        resource("EgressPolicy", "host-capabilities-memory", {"destinations": [{"origin": EMBEDDING}]}),
    ]
    documents[1]["metadata"]["labels"]["kagent.dev/discovery"] = "disabled"
    tools = [{"mcp": {"server": {"kind": "RemoteMCPServer", "name": "host-capabilities-agency"},
                     "tools": ["search_tools", "list_toolsets", "load_toolset", "get_tool_schema", "call_tool"],
                     "requireApproval": False}}]
    for template in templates:
        if template["spec"].get("modelConfig") not in (None, {"name": "host-capabilities-llm"}):
            raise RuntimeError("Existing model declaration changed; preserve it.")
        if template["spec"].get("tools") not in (None, tools):
            raise RuntimeError("Existing native tools changed; preserve them.")
    for value in documents:
        existing = get(value["kind"], value["metadata"]["name"])
        if existing and existing["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
            raise RuntimeError("Refusing to replace an unowned native configuration.")
    if not apply:
        print(json.dumps({"dryRun": True, "image": image, "agents": NAMES, "model": "gpt-5.6-sol",
                          "snapshotPolicyPreserved": True, "existingSessionPinsPreserved": True}))
        return
    if not json.loads((STATE / "admission.json").read_text()).get("paused"):
        raise RuntimeError("Pause owned factory admission before applying, with an EXIT-trap restore.")
    backup = STATE / ("native-before-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    backup.write_text(json.dumps({"harness": harness, "agents": agents, "templates": templates}, indent=2))
    backup.chmod(0o600)
    subprocess.run([*KUBE, "apply", "-f", "-"], input=json.dumps(
        {"apiVersion": "v1", "kind": "List", "items": documents}).encode(), check=True, capture_output=True)
    memory = json.dumps({"enabled": True, "embedding": {"baseUrl": EMBEDDING + "/v1",
                        "model": "BAAI/bge-base-en-v1.5", "dimensions": 768},
                        "localBridgeUrl": "http://127.0.0.1:18082", "privateHttpOrigins": [EMBEDDING]})
    owned = {"AGENT_NATIVE_CONFIG": "true", "NATIVE_MEMORY_ENABLED": "true",
             "NATIVE_MEMORY_CONFIG_JSON": memory, "AGENT_SESSION_ARCHIVE": "true",
             "SESSION_ARCHIVE_EXPORT_ENABLED": "true"}
    env = [item for item in harness["spec"].get("env", []) if item["name"] not in owned]
    env.extend({"name": key, "value": value} for key, value in owned.items())
    patch("harness", harness, [{"op": "replace", "path": "/spec/workload/image", "value": image},
                              {"op": "add", "path": "/spec/env", "value": env}])
    for template in templates:
        patch("agenttemplate", template, [
            {"op": "add", "path": "/spec/modelConfig", "value": {"name": "host-capabilities-llm"}},
            {"op": "add", "path": "/spec/tools", "value": tools}])
    for agent in agents:
        refs = agent["spec"].get("egressPolicyRefs", [])
        if {"name": "host-capabilities-memory"} not in refs:
            patch("agent", agent, [{"op": "add", "path": "/spec/egressPolicyRefs",
                                   "value": [*refs, {"name": "host-capabilities-memory"}]}])
    end = time.monotonic() + 240
    while True:
        current = [get("agent", name) for name in NAMES]
        if all(value["status"].get("workloadImage") == image and
               any(c["type"] == "Ready" and c["status"] == "True" and
                   c.get("observedGeneration") == value["metadata"]["generation"]
                   for c in value["status"]["conditions"])
               for value in current):
            break
        if time.monotonic() >= end:
            raise RuntimeError("New native revision readiness timed out; preserved backups, no replay.")
        time.sleep(5)
    print(json.dumps({"promoted": True, "agents": NAMES, "image": image, "backup": backup.name,
                      "existingSessionsRepointed": False, "businessWorkReplayed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    promote(args.image, args.evidence, args.apply)
