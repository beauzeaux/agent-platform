"""Activate the verified same-base extension image without changing data paths."""
import datetime
import json
from pathlib import Path
import subprocess

import yaml

KUBE = ["/snap/bin/microk8s", "kubectl"]
STATE = Path("/home/nibeauli/kagent-lab/host-maintenance")
BASE = "postgres:18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
IMAGE = "10.152.183.131:5000/kagent-lab/memory-postgres@sha256:4172ce00709b7cece0e6390cae8db5c00ecef4120965b43ced14e3dc7f73e273"
BACKUP = Path("/home/nibeauli/kagent-lab/memory-canary/backups/offline-20261104T001407Z-156b42/report.json")


def run(*arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def kube(*arguments):
    return json.loads(run(*KUBE, *arguments, capture_output=True).stdout)


def main():
    proof = json.loads(BACKUP.read_text())
    if not proof.get("restoreProof", {}).get("rolesOwnershipAndAclsMatched") or not proof["restoreProof"].get("cleanShutdown"):
        raise RuntimeError("Verified physical restore proof is required.")
    deployment = kube("get", "deployment", "kagent-postgresql", "-n", "kagent", "-o", "json")
    if deployment["metadata"]["uid"] != proof["source"]["deploymentUid"]:
        raise RuntimeError("Database deployment identity changed after backup.")
    node = kube("get", "node", proof["source"]["node"], "-o", "json")
    if not any(item["type"] == "Ready" and item["status"] == "True" for item in node["status"]["conditions"]):
        raise RuntimeError("Database node is not Ready; refusing maintenance.")
    pod = deployment["spec"]["template"]["spec"]
    container = next(item for item in pod["containers"] if item["name"] == "postgresql")
    if container["image"] not in (BASE, IMAGE) or pod.get("securityContext", {}).get("fsGroup") != 70:
        raise RuntimeError("Database image or filesystem identity differs from the verified canary.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    original = STATE / f"postgres-before-vector-{stamp}.json"
    original.write_text(json.dumps(deployment))
    original.chmod(0o600)
    if container["image"] == BASE:
        index = pod["containers"].index(container)
        patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": deployment["metadata"]["resourceVersion"]},
                 {"op": "test", "path": f"/spec/template/spec/containers/{index}/image", "value": BASE},
                 {"op": "replace", "path": f"/spec/template/spec/containers/{index}/image", "value": IMAGE},
                 {"op": "add", "path": f"/spec/template/spec/containers/{index}/imagePullPolicy", "value": "Never"}]
        run(*KUBE, "patch", "deployment", "kagent-postgresql", "-n", "kagent", "--type=json",
            "-p", json.dumps(patch), capture_output=True)
    run(*KUBE, "rollout", "status", "deployment/kagent-postgresql", "-n", "kagent", "--timeout=180s")
    statement = """
BEGIN;
CREATE SCHEMA IF NOT EXISTS extensions;
REVOKE CREATE ON SCHEMA extensions FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
                 WHERE e.extname='vector' AND n.nspname='extensions') THEN
    RAISE EXCEPTION 'vector extension schema differs';
  END IF;
END $$;
GRANT USAGE ON SCHEMA extensions TO factory_owner,factory_readwrite;
GRANT USAGE ON TYPE extensions.vector TO factory_owner,factory_readwrite;
COMMIT;
SELECT e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector';
"""
    result = run(*KUBE, "exec", "-i", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql",
                 "--", "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--username=postgres", "--dbname=kagent",
                 "-At", input=statement.encode(), capture_output=True)
    if b"0.8.7|extensions" not in result.stdout:
        raise RuntimeError("Expected vector extension was not activated.")
    configmap = kube("get", "configmap", "factory-controller", "-n", "factory-executions", "-o", "json")
    before = STATE / f"factory-config-before-vector-{stamp}.json"
    before.write_text(json.dumps(configmap))
    before.chmod(0o600)
    candidates = []
    for key, value in configmap["data"].items():
        parsed = yaml.safe_load(value)
        if isinstance(parsed, dict) and isinstance(parsed.get("database"), dict):
            candidates.append((key, parsed))
    if len(candidates) != 1:
        raise RuntimeError("Cannot identify the native factory controller configuration.")
    key, config = candidates[0]
    config["database"]["vectorEnabled"] = True
    config["database"]["vectorSchema"] = "extensions"
    path = "/data/" + key.replace("~", "~0").replace("/", "~1")
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": configmap["metadata"]["resourceVersion"]},
             {"op": "replace", "path": path, "value": yaml.safe_dump(config, sort_keys=False)}]
    run(*KUBE, "patch", "configmap", "factory-controller", "-n", "factory-executions", "--type=json",
        "-p", json.dumps(patch), capture_output=True)
    report = STATE / "vector-activation.json"
    report.write_text(json.dumps({"image": IMAGE, "extension": "0.8.7", "schema": "extensions",
                                 "physicalBackup": str(BACKUP), "controllerMigrationPendingRollout": True}, indent=2))
    report.chmod(0o600)
    print("Verified same-base vector image and extension ready; factory configuration staged for its next rollout.")


if __name__ == "__main__":
    main()
