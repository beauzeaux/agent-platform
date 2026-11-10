"""Reversible, single-field CA extension for Substrate's HTTPS egress origin trust."""

import json
import subprocess

from manage import KUBE, STATE, run


NAMESPACE = "ate-system"
DEPLOYMENT = "atenet-egress"
CONFIG = "atenet-egress-agentgateway-config"
ROOT_CONFIG = "agent-proxies-egress-roots"
MOUNT = "proxy-access-ca"
OLD_ROOT = "/etc/ssl/certs/ca-certificates.crt"
NEW_ROOT = "/run/proxy-access-ca/ca-bundle.pem"
OWNER = "kagent-proxy-access"


def get(kind, name):
    data = run([*KUBE, "get", kind, name, "-n", NAMESPACE, "-o", "json",
                "--ignore-not-found"], capture_output=True).stdout
    return json.loads(data) if data.strip() else None


def replace_config(old, new):
    resource = get("configmap", CONFIG)
    content = resource["data"]["config.yaml"]
    if content.count(new) == 1 and old not in content:
        return
    if content.count(old) != 1 or new in content:
        raise RuntimeError("Shared gateway root configuration differs; inspect before changing it.")
    if old == OLD_ROOT:
        path = STATE / "egress-original-config.yaml"
        if not path.exists():
            path.write_text(content)
            path.chmod(0o600)
    resource["data"]["config.yaml"] = content.replace(old, new)
    run([*KUBE, "replace", "-f", "-"], input=json.dumps(resource).encode(), capture_output=True)


def volume(add):
    deployment = get("deployment", DEPLOYMENT)
    spec = deployment["spec"]["template"]["spec"]
    index = next(i for i, c in enumerate(spec["containers"]) if c["name"] == "agentgateway")
    mounts = spec["containers"][index].get("volumeMounts", [])
    volumes = spec.get("volumes", [])
    existing = next((v for v in volumes if v["name"] == MOUNT), None)
    if existing and existing.get("configMap", {}).get("name") != ROOT_CONFIG:
        raise RuntimeError("Refusing to modify an unowned CA volume.")
    mounted = next((m for m in mounts if m["name"] == MOUNT), None)
    if mounted and mounted.get("mountPath") != "/run/proxy-access-ca":
        raise RuntimeError("Refusing to modify an unexpected CA mount.")
    operations = [{"op": "test", "path": "/metadata/resourceVersion",
                   "value": deployment["metadata"]["resourceVersion"]}]
    if add:
        if not existing:
            operations.append({"op": "add", "path": "/spec/template/spec/volumes/-",
                               "value": {"name": MOUNT, "configMap": {"name": ROOT_CONFIG}}})
        if not mounted:
            operations.append({"op": "add", "path": f"/spec/template/spec/containers/{index}/volumeMounts/-",
                               "value": {"name": MOUNT, "mountPath": "/run/proxy-access-ca", "readOnly": True}})
    else:
        if mounted:
            operations.append({"op": "remove",
                               "path": f"/spec/template/spec/containers/{index}/volumeMounts/{mounts.index(mounted)}"})
        if existing:
            operations.append({"op": "remove", "path": f"/spec/template/spec/volumes/{volumes.index(existing)}"})
    if len(operations) > 1:
        run([*KUBE, "patch", "deployment", DEPLOYMENT, "-n", NAMESPACE,
             "--type=json", "-p", json.dumps(operations)], capture_output=True)
        run([*KUBE, "rollout", "status", "deployment/" + DEPLOYMENT, "-n", NAMESPACE, "--timeout=180s"])


def apply():
    roots = STATE / "egress-system-roots.pem"
    if not roots.exists():
        image = get("deployment", DEPLOYMENT)["spec"]["template"]["spec"]["containers"][0]["image"]
        container = run(["docker", "create", image], capture_output=True).stdout.decode().strip()
        try:
            run(["docker", "cp", container + ":" + OLD_ROOT, str(roots)], capture_output=True)
        finally:
            run(["docker", "rm", container], capture_output=True)
    bundle = roots.read_text() + "\n" + (STATE / "certificates/ca.crt").read_text()
    existing = get("configmap", ROOT_CONFIG)
    if existing and existing["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("Refusing to replace an unowned egress CA ConfigMap.")
    manifest = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": ROOT_CONFIG, "namespace": NAMESPACE,
                     "labels": {"app.kubernetes.io/managed-by": OWNER}},
        "data": {"ca-bundle.pem": bundle},
    }
    run([*KUBE, "apply", "-f", "-"], input=json.dumps(manifest).encode(), capture_output=True)
    volume(True)
    replace_config(OLD_ROOT, NEW_ROOT)
    run([*KUBE, "rollout", "restart", "deployment/" + DEPLOYMENT, "-n", NAMESPACE], capture_output=True)
    run([*KUBE, "rollout", "status", "deployment/" + DEPLOYMENT, "-n", NAMESPACE, "--timeout=180s"])
    print("Extended only HTTPS origin trust; existing system roots and all egress policy routes preserved.")


def remove():
    resource = get("configmap", CONFIG)
    if NEW_ROOT in resource["data"]["config.yaml"]:
        replace_config(NEW_ROOT, OLD_ROOT)
    volume(False)
    existing = get("configmap", ROOT_CONFIG)
    if existing:
        if existing["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
            raise RuntimeError("Refusing to delete an unowned egress CA ConfigMap.")
        run([*KUBE, "delete", "configmap", ROOT_CONFIG, "-n", NAMESPACE], capture_output=True)
    print("Removed owned origin-trust overlay without restoring unrelated configuration.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2 or sys.argv[1] not in {"apply", "remove"}:
        raise SystemExit("Usage: egress_overlay.py apply|remove")
    globals()[sys.argv[1]]()
