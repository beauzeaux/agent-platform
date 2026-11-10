"""Configure the existing Kagent images; the front door must strip the public prefix."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

TARGETS = (("factory-executions", "factory-ui", "/kagent"),)
STATE = Path("/home/nibeauli/kagent-lab/host-service-access/ui-subpaths")


def kubectl(*args):
    return subprocess.check_output(
        ["/snap/bin/microk8s", "kubectl", "--request-timeout=30s", *args])


def configure(apply=False, state=STATE, run=kubectl):
    snapshots = []
    for namespace, name, prefix in TARGETS:
        deployment = json.loads(run("-n", namespace, "get", "deployment", name, "-o", "json"))
        config = json.loads(run("-n", namespace, "get", "configmap", name, "-o", "json"))
        pod = deployment["spec"]["template"]["spec"]
        nginx = next(v for v in pod["volumes"] if v["name"] == "ui-config")
        kind = "secret" if "secret" in nginx else "configmap"
        config_name = nginx["secret" if kind == "secret" else "configMap"][
            "secretName" if kind == "secret" else "name"]
        nginx_config = json.loads(run("-n", namespace, "get", kind, config_name, "-o", "json"))
        snapshots.append((namespace, name, prefix, deployment, config, nginx_config))
    if not apply:
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    backup = state / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ.json")
    with backup.open("x", encoding="utf-8") as output:
        os.chmod(backup, 0o600)
        json.dump(snapshots, output)
    for namespace, name, prefix, _, _, _ in snapshots:
        run("-n", namespace, "patch", "configmap", name, "--type=merge", "-p",
            json.dumps({"data": {"KAGENT_UI_BASE_PATH": prefix}}))
        run("-n", namespace, "rollout", "restart", "deployment", name)
        run("-n", namespace, "rollout", "status", "deployment", name, "--timeout=180s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    configure(parser.parse_args().apply)
