"""Deploy five idle agents into workload namespaces on the existing factory instance."""
import argparse
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "management"))
import manage

KUBE = ["/snap/bin/microk8s", "kubectl", "--request-timeout=30s"]
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
STATE = Path("/home/nibeauli/kagent-lab/baseline")
OWNER = "host-capabilities-baseline"
NAMESPACES = ("baseline", "cluster-management")
IMAGE = ("10.152.183.131:5000/kagent-lab/a2a-sanity@sha256:"
         "82321e3ca831eb95b23103b9047205613fa38f757de18e4ed63d3ac556e658da")
CLI = ["/home/nibeauli/kagent-lab/artifacts/kubectl-ate", "--preview"]
MEMBERS = ["user:spiffe://cluster.local/ns/factory-executions/sa/factory-controller",
           "user:system:serviceaccount:ate-system:ate-client"]


def fixture_module():
    spec = importlib.util.spec_from_file_location("baseline_fixture_resources", HERE.parent / "a2a-sanity" / "resources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kube(*args, body=None):
    encoded = None if body is None else json.dumps(body).encode()
    for attempt in range(20):
        result = subprocess.run([*KUBE, *args], env=ENV, capture_output=True, timeout=300, input=encoded)
        if result.returncode == 0 or b"database is locked" not in result.stderr or attempt == 19:
            break
        time.sleep(0.5)
    if result.returncode:
        if STATE.exists():
            path = STATE / "fleet-platform-error.log"
            path.write_bytes(result.stderr)
            path.chmod(0o600)
        raise RuntimeError("Fleet Kubernetes operation failed; inspect protected fleet-platform-error.log.")
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(value, indent=2))
    partial.chmod(0o600)
    partial.replace(path)


def owned_current(document, receipts):
    meta = document["metadata"]
    current = kube("get", document["kind"], meta["name"], "-n", meta["namespace"], "-o", "json", "--ignore-not-found")
    key = "/".join((meta["namespace"], document["kind"], meta["name"]))
    expected = receipts.get(key)
    if expected and (not current or current["metadata"]["uid"] != expected):
        raise RuntimeError("Fleet resource UID changed; refusing adoption.")
    if current:
        observed = current["metadata"]
        if observed.get("labels", {}).get("app.kubernetes.io/managed-by") != meta["labels"]["app.kubernetes.io/managed-by"]:
            raise RuntimeError("Refusing to overwrite an unowned fleet resource.")
        run = meta["labels"].get("sanity.host-capabilities/run")
        if run and observed.get("labels", {}).get("sanity.host-capabilities/run") != run:
            raise RuntimeError("Refusing to overwrite a different fixture run.")
    return current


def runtime_documents(namespace, pool):
    labels = {"app.kubernetes.io/managed-by": OWNER}
    spec = copy.deepcopy(pool["spec"])
    spec["replicas"] = 1
    return [
        {"apiVersion": "v1", "kind": "ServiceAccount",
         "metadata": {"name": "kagent-default", "namespace": namespace, "labels": labels}},
        {"apiVersion": "ate.dev/v1", "kind": "WorkerPool",
         "metadata": {"name": "kagent-default", "namespace": namespace,
                      "labels": {**labels, "kagent.dev/worker-pool-name": "kagent-default",
                                 "kagent.dev/worker-pool-namespace": namespace}}, "spec": spec},
    ]


def credential_policy(policy):
    result = copy.deepcopy(policy)
    entries = result.setdefault("policies", [])
    for namespace, owner, access in (
        ("baseline", OWNER, "baseline-model"),
        ("cluster-management", manage.OWNER, "management-model"),
        ("cluster-management", manage.OWNER, "management-mcp"),
    ):
        grant = {"atespace": namespace, "allowedNamespaces": [namespace],
                 "secretSelector": {"matchLabels": {"app.kubernetes.io/managed-by": owner,
                                                    "host-capabilities/access": access}}}
        if grant not in entries:
            entries.append(grant)
    return result


def runtime_space(namespace):
    marker = STATE / ("space-" + namespace + ".json")
    current = subprocess.run([*CLI, "get", "atespace", namespace, "-o", "json"],
                             env=ENV, capture_output=True, timeout=120)
    if current.returncode:
        if b"NotFound" not in current.stderr or marker.exists():
            raise RuntimeError("Cannot inspect or replace owned runtime space.")
        current = subprocess.run([*CLI, "create", "atespace", namespace, "-o", "json"],
                                 env=ENV, capture_output=True, check=True, timeout=120)
        save(marker, json.loads(current.stdout))
    elif not marker.exists() or json.loads(marker.read_text())["metadata"]["uid"] != json.loads(current.stdout)["metadata"]["uid"]:
        raise RuntimeError("Refusing to adopt an unowned or replaced runtime space.")
    policy = {"bindings": [{"role": "owner", "members": MEMBERS}]}
    result = subprocess.run([*CLI, "get", "access-policy", "-a", namespace, "-o", "json"],
                            env=ENV, capture_output=True, check=True, timeout=120)
    if not result.stdout.strip():
        subprocess.run([*CLI, "create", "access-policy", "-a", namespace, "-f", "-"], env=ENV,
                       input=json.dumps(policy).encode(), capture_output=True, check=True, timeout=120)
    elif json.loads(result.stdout).get("bindings") != policy["bindings"]:
        raise RuntimeError("Runtime access policy differs from the exact factory/ate-client scope.")


def secret(name, namespace, data, access, owner):
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "data": data,
            "metadata": {"name": name, "namespace": namespace,
                         "labels": {"app.kubernetes.io/managed-by": owner, "host-capabilities/access": access}}}


def main(apply=False):
    if not apply:
        print(json.dumps({"dryRun": True, "namespaces": NAMESPACES, "controller": "factory-controller",
                          "agents": 5, "workerPoolReplicasPerNamespace": 1, "modelWorkStarted": False}))
        return
    if os.geteuid() != 0:
        raise RuntimeError("Apply as primary WSL root.")
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = STATE / "fleet-owned.json"
    fleet = json.loads(marker.read_text()) if marker.exists() else {
        "runId": str(uuid.uuid4()), "namespaces": {}, "uids": {}, "phase": "preparing"}
    namespaces = [kube("get", "namespace", name, "-o", "json") for name in NAMESPACES]
    for namespace in namespaces:
        meta = namespace["metadata"]
        if meta["name"] == "baseline" and meta.get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
            raise RuntimeError("Baseline namespace ownership changed.")
        prior = fleet["namespaces"].get(meta["name"])
        if prior and prior != meta["uid"]:
            raise RuntimeError("Workload namespace UID changed.")
        fleet["namespaces"][meta["name"]] = meta["uid"]
    save(marker, fleet)
    pool = kube("get", "workerpool", "kagent-default", "-n", "factory-executions", "-o", "json")
    if pool["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "factory-coding":
        raise RuntimeError("Reference factory worker pool ownership changed.")
    fixture = fixture_module()
    documents = [item for name in NAMESPACES for item in runtime_documents(name, pool)]
    source_key = kube("get", "secret", "cluster-management-llm", "-n", "factory-executions", "-o", "json")
    source_auth = kube("get", "secret", "pilot-grafana-mcp-auth", "-n", "kagent", "-o", "json")
    if (source_key["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != manage.OWNER
            or source_auth["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "host-capabilities-pilot-observability"):
        raise RuntimeError("Existing admission Secret ownership changed.")
    documents += [
        secret("baseline-llm", "baseline", {"gateway-key": source_key["data"]["gateway-key"]}, "baseline-model", OWNER),
        secret("cluster-management-llm", "cluster-management", {"gateway-key": source_key["data"]["gateway-key"]},
               "management-model", manage.OWNER),
        secret("cluster-grafana-mcp-auth", "cluster-management", {"authorization": source_auth["data"]["authorization"]},
               "management-mcp", manage.OWNER),
        *manage.documents(), *fixture.documents(fleet["runId"], IMAGE, live=False, namespace="baseline"),
    ]
    before = [current for document in documents if (current := owned_current(document, fleet["uids"]))]
    config = kube("get", "configmap", "k8s-credential-provider-namespace-policy", "-n", "ate-system", "-o", "json")
    if config["metadata"].get("annotations", {}).get("meta.helm.sh/release-name") != "substrate":
        raise RuntimeError("Credential policy ownership changed.")
    grafana = kube("get", "networkpolicy", "pilot-grafana-mcp", "-n", "kagent", "-o", "json")
    if grafana["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "host-capabilities-pilot-observability":
        raise RuntimeError("Grafana NetworkPolicy ownership changed.")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    save(STATE / ("fleet-before-" + stamp + ".json"), [*namespaces, config, grafana, *before])
    for namespace in namespaces:
        meta = namespace["metadata"]
        kube("patch", "namespace", meta["name"], "--type=json", "-p", json.dumps([
            {"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
            {"op": "add", "path": "/metadata/labels/ate.dev~1api-access", "value": "true"},
        ]), "-o", "json")
        runtime_space(meta["name"])
    old_policy = yaml.safe_load(config["data"]["namespace-policy.yaml"])
    policy = credential_policy(old_policy)
    if policy != old_policy:
        kube("patch", "configmap", config["metadata"]["name"], "-n", "ate-system", "--type=json", "-p", json.dumps([
            {"op": "test", "path": "/metadata/resourceVersion", "value": config["metadata"]["resourceVersion"]},
            {"op": "replace", "path": "/data/namespace-policy.yaml", "value": yaml.safe_dump(policy)},
        ]), "-o", "json")
        overlay = STATE / "substrate-fleet-credential.values.yaml"
        overlay.write_text(yaml.safe_dump({"credentialProvider": {"namespacePolicies": policy["policies"]}}))
        overlay.chmod(0o600)
        subprocess.run([*KUBE, "rollout", "restart", "deployment/k8s-credential-provider", "-n", "ate-system"],
                       env=ENV, capture_output=True, check=True, timeout=60)
        subprocess.run([*KUBE, "rollout", "status", "deployment/k8s-credential-provider", "-n", "ate-system",
                        "--timeout=120s"], env=ENV, check=True, timeout=150)
    factory_peer = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "factory-executions"}},
                    "podSelector": {"matchLabels": {"app.kubernetes.io/component": "controller",
                                                   "app.kubernetes.io/instance": "factory"}}}
    if factory_peer not in grafana["spec"]["ingress"][0]["from"]:
        kube("patch", "networkpolicy", "pilot-grafana-mcp", "-n", "kagent", "--type=json", "-p", json.dumps([
            {"op": "test", "path": "/metadata/resourceVersion", "value": grafana["metadata"]["resourceVersion"]},
            {"op": "add", "path": "/spec/ingress/0/from/-", "value": factory_peer},
        ]), "-o", "json")
    fixture_root = Path("/home/nibeauli/kagent-lab/a2a-sanity")
    pointer = fixture_root / "current.json"
    if pointer.exists():
        previous = json.loads(pointer.read_text())["runId"]
        prior = json.loads((fixture_root / previous / "state.json").read_text())
        if prior["phase"] != "cleaned" and previous != fleet["runId"]:
            raise RuntimeError("Another current fixture is still present.")
    payload = {"apiVersion": "v1", "kind": "List", "items": documents}
    kube("apply", "--dry-run=server", "-f", "-", "-o", "json", body=payload)
    try:
        kube("apply", "-f", "-", "-o", "json", body=payload)
    finally:
        for document in documents:
            value = owned_current(document, fleet["uids"])
            if value:
                meta = value["metadata"]
                fleet["uids"]["/".join((meta["namespace"], value["kind"], meta["name"]))] = meta["uid"]
        save(marker, fleet)
    fixture_receipt = fixture_root / fleet["runId"] / "state.json"
    state = json.loads(fixture_receipt.read_text()) if fixture_receipt.exists() else {
        "runId": fleet["runId"], "namespace": "baseline", "live": False, "image": IMAGE,
        "models": {role: {"id": model, "apiFormat": wire} for role, (model, wire) in fixture.MODELS.items()},
        "tasks": {}, "sessions": {}, "results": {}}
    state["uids"] = {key.removeprefix("baseline/"): value for key, value in fleet["uids"].items()
                     if key.startswith("baseline/") and key.removeprefix("baseline/") in {
                         item["kind"] + "/" + item["metadata"]["name"]
                         for item in documents if item["metadata"]["labels"]["app.kubernetes.io/managed-by"] == fixture.OWNER}}
    state["phase"] = "setting-up"
    save(fixture_receipt, state)
    save(pointer, {"runId": fleet["runId"]})
    for namespace in NAMESPACES:
        subprocess.run([*KUBE, "rollout", "status", "deployment/kagent-default", "-n", namespace, "--timeout=240s"],
                       env=ENV, check=True, timeout=270)
        subprocess.run([*KUBE, "wait", "agents.api.kagent.dev", "--all", "-n", namespace,
                        "--for=condition=Ready", "--timeout=240s"], env=ENV, check=True, timeout=270)
    fleet["phase"] = state["phase"] = "ready"
    save(marker, fleet)
    save(fixture_receipt, state)
    print(json.dumps({"ready": True, "runId": fleet["runId"], "namespaces": NAMESPACES,
                      "agents": 5, "newController": False, "modelWorkStarted": False,
                      "grafana": "existing official read-only MCP; no Grafana credential copied"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
