"""Enroll baseline in the existing factory controller/UI; never start model work."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess

import yaml

KUBE = ["/snap/bin/microk8s", "kubectl", "--request-timeout=30s"]
NAMESPACE = "baseline"
TARGETS = ["baseline", "cluster-management"]
FACTORY = "factory-executions"
OWNER = "host-capabilities-baseline"
STATE = Path("/home/nibeauli/kagent-lab/baseline")
VALUES = Path("/home/nibeauli/kagent-lab/factory-coding/controller-values.json")
CHART = "/home/nibeauli/kagent-lab/artifacts/kagent-1.0.0-rc1.tgz"


def watch_namespaces(values):
    watched = values.get("kubernetes", {}).get("watchNamespaces")
    if (not isinstance(watched, list) or not watched or FACTORY not in watched
            or any(not isinstance(name, str) or not name or name == "*" for name in watched)):
        raise ValueError("An explicit existing factory watch list is required.")
    return list(dict.fromkeys([*watched, *TARGETS]))


def updated_config(config):
    result = copy.deepcopy(config)
    result["kubernetes"]["watchNamespaces"] = watch_namespaces(config)
    return result


def baseline_rbac(documents):
    allowed = {"factory-getter-role", "factory-writer-role",
               "factory-getter-rolebinding", "factory-writer-rolebinding"}
    result = []
    for document in documents:
        if not document or document.get("metadata", {}).get("namespace") not in TARGETS:
            continue
        if document["kind"] not in {"Role", "RoleBinding"} or document["metadata"]["name"] not in allowed:
            continue
        if document["kind"] == "RoleBinding" and document.get("subjects") != [
                {"kind": "ServiceAccount", "name": "factory-controller", "namespace": FACTORY}]:
            raise ValueError("Namespace bindings must reference only the existing factory controller.")
        document["metadata"].setdefault("labels", {})["app.kubernetes.io/managed-by"] = "factory-coding"
        document["metadata"].setdefault("annotations", {})["host-capabilities/namespace-enrollment"] = OWNER
        result.append(document)
    if (len(result) != 8 or {(value["metadata"]["namespace"], value["metadata"]["name"]) for value in result}
            != {(namespace, name) for namespace in TARGETS for name in allowed}):
        raise ValueError("Pinned chart did not render the expected namespace-only RBAC.")
    return result


def kube(*args, body=None):
    result = subprocess.run([*KUBE, *args], input=None if body is None else json.dumps(body).encode(),
                            capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Namespace registration Kubernetes operation failed; inspect protected state.")
    return json.loads(result.stdout) if result.stdout.strip() else None


def main(apply=False):
    namespace = kube("get", "namespace", NAMESPACE, "-o", "json")
    if namespace["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("Baseline namespace ownership changed.")
    controller = kube("get", "configmap", "factory-controller", "-n", FACTORY, "-o", "json")
    if controller["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "factory-coding":
        raise RuntimeError("Factory controller ownership changed.")
    config = yaml.safe_load(controller["data"]["controller.yaml"])
    updated = updated_config(config)
    values = json.loads(VALUES.read_text())
    values.setdefault("global", {})["watchNamespaces"] = watch_namespaces(config)
    if not apply:
        print(json.dumps({"dryRun": True, "controller": "factory-controller",
                          "watchNamespaces": values["global"]["watchNamespaces"],
                          "newController": False, "modelWorkStarted": False}))
        return
    if os.geteuid() != 0:
        raise RuntimeError("Apply as primary WSL root.")
    os.umask(0o077)
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (STATE / ("controller-before-" + stamp + ".json")).write_text(json.dumps(controller))
    (STATE / ("values-before-" + stamp + ".json")).write_bytes(VALUES.read_bytes())
    rendered_values = STATE / "factory-namespace-values.json"
    rendered_values.write_text(json.dumps(values))
    raw = subprocess.check_output(["/snap/bin/microk8s", "helm3", "template", "factory", CHART,
                                   "-n", FACTORY, "-f", str(rendered_values)], timeout=120)
    roles = baseline_rbac(list(yaml.safe_load_all(raw)))
    for document in roles:
        prior = kube("get", document["kind"], document["metadata"]["name"], "-n", document["metadata"]["namespace"],
                     "--ignore-not-found", "-o", "json")
        if prior and prior["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "factory-coding":
            raise RuntimeError("Refusing to overwrite an unowned baseline role.")
    payload = {"apiVersion": "v1", "kind": "List", "items": roles}
    kube("apply", "--dry-run=server", "-f", "-", "-o", "json", body=payload)
    kube("apply", "-f", "-", "-o", "json", body=payload)
    if updated != config:
        kube("patch", "configmap", "factory-controller", "-n", FACTORY, "--type=json", "-p",
             json.dumps([{"op": "test", "path": "/metadata/resourceVersion",
                          "value": controller["metadata"]["resourceVersion"]},
                         {"op": "replace", "path": "/data/controller.yaml",
                          "value": yaml.safe_dump(updated, sort_keys=False)}]), "-o", "json")
    temporary = VALUES.with_suffix(".namespace-partial")
    temporary.write_text(json.dumps(values))
    temporary.chmod(0o600)
    temporary.replace(VALUES)
    if updated != config:
        subprocess.run([*KUBE, "rollout", "restart", "deployment/factory-controller", "-n", FACTORY],
                       check=True, capture_output=True, timeout=60)
    subprocess.run([*KUBE, "rollout", "status", "deployment/factory-controller", "-n", FACTORY,
                    "--timeout=180s"], check=True, timeout=210)
    observed = yaml.safe_load(kube("get", "configmap", "factory-controller", "-n", FACTORY,
                                  "-o", "json")["data"]["controller.yaml"])
    if observed != updated:
        raise RuntimeError("Controller configuration changed during enrollment.")
    print(json.dumps({"registered": True, "controller": "factory-controller",
                      "ui": "http://kagent.internal", "watchNamespaces": watch_namespaces(observed),
                      "namespaceRbac": 8, "newController": False, "modelWorkStarted": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
