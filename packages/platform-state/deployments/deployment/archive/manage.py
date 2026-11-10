"""Prepare private archive storage and enable only the existing factory controller."""
import argparse
import base64
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.parse

import yaml

SOURCE = Path(__file__).resolve().parent
LAB = Path("/home/nibeauli/kagent-lab")
STATE = LAB / "factory-coding/archive"
KUBE = ["/snap/bin/microk8s", "kubectl"]
OWNER = "host-capabilities"


def run(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def kube(*arguments, body=None):
    result = run([*KUBE, *arguments], input=json.dumps(body).encode() if body is not None else None,
                 capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def protected(path, value):
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))
    path.chmod(0o600)


@contextmanager
def object_forward():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = STATE / "bootstrap-forward.log"
    with log_path.open("wb") as log:
        log_path.chmod(0o600)
        process = subprocess.Popen(
            ["/snap/microk8s/current/kubectl", "-n", "ate-system", "port-forward",
             "service/rustfs", f"{port}:9000", "--address=127.0.0.1"],
            env=dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config"),
            stdout=log, stderr=subprocess.STDOUT)
        try:
            for attempt in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Owned object-store bootstrap forward exited.")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if attempt == 59:
                        raise RuntimeError("Object-store bootstrap access did not become ready.")
                    time.sleep(0.5)
            yield "http://127.0.0.1:" + str(port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def prepare():
    if os.geteuid() != 0:
        raise RuntimeError("Run as root in the existing primary WSL distribution.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    secret = kube("get", "secret", "factory-postgres-readwrite", "-n", "factory-executions", "-o", "json")
    dsn = base64.b64decode(secret["data"]["readWriteConnectionString"]).decode()
    credentials = yaml.safe_load((LAB / "protected/rustfs-values.yaml").read_text())["rustfs"]
    protected(STATE / "database-url", dsn)
    protected(STATE / "access-key", credentials["accessKey"])
    protected(STATE / "secret-key", credentials["secretKey"])
    config = {"databaseUrlFile": "/run/factory-archive/database-url", "databaseRole": "factory_readwrite",
              "endpoint": "http://rustfs.ate-system.svc.cluster.local:9000", "bucket": "bespoke-session-archives",
              "accessKeyFile": "/run/factory-archive/access-key", "secretKeyFile": "/run/factory-archive/secret-key"}
    protected(STATE / "config.json", config)
    parsed = urllib.parse.urlsplit(dsn)
    query = urllib.parse.parse_qs(parsed.query)
    query["sslrootcert"] = [str(LAB / "factory-coding/postgres-ca.pem")]
    root_dsn = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))
    protected(STATE / "bootstrap-database-url", root_dsn)
    bootstrap = {**config, "databaseUrlFile": str(STATE / "bootstrap-database-url"),
                 "accessKeyFile": str(STATE / "access-key"),
                 "secretKeyFile": str(STATE / "secret-key")}
    with object_forward() as origin:
        protected(STATE / "bootstrap.json", {**bootstrap, "endpoint": origin})
        run([str(LAB / "factory-coding/controller"), "--archive-prepare", str(STATE / "bootstrap.json")], timeout=60)
    print("Protected archive configuration and separate private bucket prepared.")


def apply(image):
    if not re.fullmatch(r"10\.152\.183\.\d+:5000/kagent-lab/factory-coder@sha256:[a-f0-9]{64}", image):
        raise RuntimeError("Use the verified immutable private factory image.")
    if not (STATE / "config.json").exists():
        raise RuntimeError("Prepare protected archive configuration first.")
    deployment = kube("get", "deployment", "factory-controller", "-n", "factory-executions", "-o", "json")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    protected(STATE / f"controller-before-{stamp}.json", deployment)
    backup = STATE / f"database-before-archive-{stamp}.dump"
    with backup.open("wb") as stream:
        backup.chmod(0o600)
        run([*KUBE, "exec", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql", "--",
             "pg_dump", "--username=postgres", "--format=custom", "--no-owner", "--no-acl", "--dbname=kagent"],
            stdout=stream, stderr=subprocess.PIPE, timeout=180)
    with backup.open("rb") as stream:
        run([*KUBE, "exec", "-i", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql", "--",
             "sh", "-c",
             'd=$(mktemp -d /tmp/host-archive-verify.XXXXXXXX); '
             'trap \'rm -f -- "$d/dump"; rmdir -- "$d"\' EXIT; '
             'cat >"$d/dump" && pg_restore --list "$d/dump" >/dev/null'],
            stdin=stream, capture_output=True, timeout=60)
    digest = hashlib.sha256()
    with backup.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    protected(STATE / "schema-backup.json", {"file": backup.name, "bytes": backup.stat().st_size,
              "sha256": digest.hexdigest(), "kind": "logical data backup; owner/ACL excluded"})
    run([*KUBE, "exec", "-i", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql", "--",
         "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--single-transaction", "--username=postgres",
         "--dbname=kagent"], input=(SOURCE / "schema.sql").read_bytes(), capture_output=True, timeout=60)
    name = "factory-session-archive"
    observed = subprocess.run([*KUBE, "get", "secret", name, "-n", "factory-executions", "-o", "json"],
                              capture_output=True, check=False)
    if observed.returncode and b"NotFound" not in observed.stderr:
        raise RuntimeError("Cannot inspect existing archive configuration ownership.")
    if observed.stdout and json.loads(observed.stdout)["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("Refusing to replace unowned archive storage configuration.")
    document = {"apiVersion": "v1", "kind": "Secret",
                "metadata": {"name": name, "namespace": "factory-executions",
                             "labels": {"app.kubernetes.io/managed-by": OWNER}},
                "type": "Opaque", "stringData": {
                    item: (STATE / item).read_text() for item in ("database-url", "access-key", "secret-key", "config.json")}}
    kube("apply", "-f", "-", "-o", "json", body=document)
    run([*KUBE, "apply", "-f", str(SOURCE / "resources.yaml")])
    deployment = kube("get", "deployment", "factory-controller", "-n", "factory-executions", "-o", "json")
    pod = deployment["spec"]["template"]["spec"]
    container = next(item for item in pod["containers"] if item["name"] == "controller")
    index = pod["containers"].index(container)
    volumes = [item for item in pod.get("volumes", []) if item["name"] != name]
    volumes.append({"name": name, "secret": {"secretName": name, "defaultMode": 292}})
    mounts = [item for item in container.get("volumeMounts", []) if item["name"] != name]
    mounts.append({"name": name, "mountPath": "/run/factory-archive", "readOnly": True})
    environment = [item for item in container.get("env", []) if item["name"] != "ARCHIVE_CONFIG"]
    environment.append({"name": "ARCHIVE_CONFIG", "value": "/run/factory-archive/config.json"})
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": deployment["metadata"]["resourceVersion"]},
             {"op": "add", "path": "/spec/template/spec/volumes", "value": volumes},
             {"op": "add", "path": f"/spec/template/spec/containers/{index}/volumeMounts", "value": mounts},
             {"op": "add", "path": f"/spec/template/spec/containers/{index}/env", "value": environment},
             {"op": "replace", "path": f"/spec/template/spec/containers/{index}/image", "value": image}]
    patched = subprocess.run(
        [*KUBE, "patch", "deployment", "factory-controller", "-n", "factory-executions", "--type=json",
         "-p", json.dumps(patch)], capture_output=True)
    if patched.returncode:
        raise RuntimeError("Factory controller patch failed: " + patched.stderr.decode(errors="replace"))
    run([*KUBE, "rollout", "status", "deployment/factory-controller", "-n", "factory-executions", "--timeout=180s"])
    protected(STATE / "deployment.json", {"image": image, "enabled": True, "backup": backup.name})
    print("Factory archive capability enabled; existing session/runtime images remain pinned.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "apply"])
    parser.add_argument("--image")
    arguments = parser.parse_args()
    if arguments.action == "prepare":
        prepare()
    elif arguments.image:
        apply(arguments.image)
    else:
        parser.error("apply requires --image")
