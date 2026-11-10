import datetime
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
import hmac
import http.client
import urllib.error
import xml.etree.ElementTree as ET

import yaml

from lab_transport import BASE, ENV, port_forward

K = ["/snap/bin/microk8s", "kubectl"]
PG_IMAGE = "postgres:18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
S3_IMAGE = "rustfs/rustfs:1.0.3@sha256:9ca9559533b537d10482fe923288b676ad08f92a7069d9a17327fbae3cab688b"
REGISTRY_IMAGE = "registry:3@sha256:d4032d4cc323c1731c707931ecada07a53076eac38e3b61ae57533f170f2c6c3"


def kube(*args, input=None):
    return subprocess.check_output([*K, *args], input=input, env=ENV,
                                   stderr=subprocess.PIPE, timeout=300)


def scale(namespace, name, replicas):
    for attempt in range(3):
        try:
            kube("scale", f"deployment/{name}", "-n", namespace, f"--replicas={replicas}")
            break
        except subprocess.CalledProcessError as error:
            if b"database is locked" not in error.stderr or attempt == 2:
                raise
            time.sleep(5)
    if replicas:
        kube("rollout", "status", f"deployment/{name}", "-n", namespace, "--timeout=240s")
    else:
        for attempt in range(90):
            value = json.loads(kube("get", "deployment", name, "-n", namespace, "-o", "json"))
            if not value.get("status", {}).get("replicas", 0):
                return
            time.sleep(1)
        raise RuntimeError(f"{namespace}/{name} did not stop")


def sql(query, namespace="kagent", target="deployment/kagent-postgresql"):
    container = "postgresql" if namespace == "kagent" else "recovery-postgres"
    return kube("exec", "-n", namespace, target, "-c", container, "--",
                "psql", "-U", "postgres", "-d", "kagent", "-At", "-c", query).decode().strip()


def table_hashes(namespace="kagent", target="deployment/kagent-postgresql"):
    tables = sql("SELECT schemaname||'.'||tablename FROM pg_tables "
                 "WHERE schemaname IN ('kagent','substrate') ORDER BY 1", namespace, target).splitlines()
    result = {}
    for table in tables:
        schema, name = table.split(".")
        quoted = '"' + schema.replace('"', '""') + '"."' + name.replace('"', '""') + '"'
        result[table] = sql(
            f"SELECT count(*)||':'||coalesce(md5(string_agg(row_to_json(t)::text,"
            f" E'\\n' ORDER BY row_to_json(t)::text)),'empty') FROM {quoted} t",
            namespace, target,
        )
    return result


class S3:
    def __init__(self, origin, credentials):
        self.origin = origin
        self.access = credentials["accessKey"]
        self.secret = credentials["secretKey"]

    def get(self, path="/", query=None):
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        host = urllib.parse.urlparse(self.origin).netloc
        uri = urllib.parse.quote(path, safe="/-_.~")
        query = urllib.parse.urlencode(sorted((query or {}).items()), quote_via=urllib.parse.quote)
        payload = hashlib.sha256(b"").hexdigest()
        headers = f"host:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{stamp}\n"
        signed = "host;x-amz-content-sha256;x-amz-date"
        canonical = f"GET\n{uri}\n{query}\n{headers}\n{signed}\n{payload}"
        scope = f"{day}/us-east-1/s3/aws4_request"
        string = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        key = ("AWS4" + self.secret).encode()
        for item in (day, "us-east-1", "s3", "aws4_request"):
            key = hmac.new(key, item.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, string.encode(), hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.origin + uri + ("?" + query if query else ""), headers={
            "x-amz-date": stamp, "x-amz-content-sha256": payload,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={self.access}/{scope}, SignedHeaders={signed}, Signature={signature}",
        })
        for attempt in range(8):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 7:
                    raise
            except (http.client.HTTPException, urllib.error.URLError, TimeoutError):
                if attempt == 7:
                    raise
            time.sleep(5)

    def hashes(self):
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        buckets = ET.fromstring(self.get()).findall(".//s:Bucket/s:Name", ns)
        result = {}
        for bucket in buckets:
            continuation = None
            while True:
                query = {"list-type": "2"}
                if continuation:
                    query["continuation-token"] = continuation
                listing = ET.fromstring(self.get("/" + bucket.text, query))
                for key in listing.findall("s:Contents/s:Key", ns):
                    path = f"/{bucket.text}/{key.text}"
                    result[path] = hashlib.sha256(self.get(path)).hexdigest()
                continuation = listing.findtext("s:NextContinuationToken", namespaces=ns)
                if not continuation:
                    break
        assert result, "No snapshot objects were backed up"
        return result


def manifest(namespace, pg_name, s3_name, data, registry_data, credentials):
    resources = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "deny-ingress", "namespace": namespace},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}},
    ]
    for name, image, port, env, volume in [
        (pg_name, PG_IMAGE, 5432, [{"name": "POSTGRES_HOST_AUTH_METHOD", "value": "trust"}],
         {"emptyDir": {}}),
        (s3_name, S3_IMAGE, 9000, [
            {"name": "RUSTFS_ACCESS_KEY", "value": credentials["accessKey"]},
            {"name": "RUSTFS_SECRET_KEY", "value": credentials["secretKey"]},
            {"name": "RUSTFS_CONSOLE_ENABLE", "value": "false"},
        ], {"hostPath": {"path": str(data), "type": "Directory"}}),
        ("recovery-registry", REGISTRY_IMAGE, 5000, [],
         {"hostPath": {"path": str(registry_data), "type": "Directory"}}),
    ]:
        container = {
            "name": name, "image": image, "env": env,
            "ports": [{"containerPort": port}],
            "volumeMounts": [{"name": "data", "mountPath": {
                9000: "/data", 5432: "/var/lib/postgresql", 5000: "/var/lib/registry",
            }[port]}],
            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                          "limits": {"cpu": "1", "memory": "1Gi"}},
            "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 2},
        }
        if port == 9000:
            container["args"] = ["/data"]
            container["securityContext"] = {"runAsUser": 10001, "runAsGroup": 10001}
        resources.extend([
            {"apiVersion": "apps/v1", "kind": "Deployment",
             "metadata": {"name": name, "namespace": namespace},
             "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                      "template": {"metadata": {"labels": {"app": name}},
                                   "spec": {"nodeSelector": {"devbox-name": "primary-devbox"},
                                            "containers": [container],
                                            "volumes": [{"name": "data", **volume}]}}}},
            {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": namespace},
             "spec": {"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]}},
        ])
    return {"apiVersion": "v1", "kind": "List", "items": resources}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-report", choices=[
        "byo-acceptance.json", "byo-lifecycle-remainder.json",
    ], default="byo-acceptance.json")
    arguments = parser.parse_args()
    os.umask(0o077)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/mnt/microk8s-hostpath/kagent-lab/backups") / stamp
    backup.mkdir(parents=True)
    namespace = "kagent-lab-recovery"
    credentials = yaml.safe_load((BASE / "protected/rustfs-values.yaml").read_text())["rustfs"]
    stopped = []
    report = {"backup": str(backup), "result": "FAIL",
              "acceptance_boundary": arguments.boundary_report}
    try:
        # Refuse to freeze at an uncommitted or active managed-session boundary.
        acceptance = json.loads((BASE / "protected" / arguments.boundary_report).read_text())
        assert acceptance["result"] == "PASS"
        actors = json.loads(subprocess.check_output([
            str(BASE / "artifacts/kubectl-ate"), "get", "actors",
            "-a", "kagent", "-o", "json",
        ], env=ENV, stderr=subprocess.DEVNULL))["actors"]
        names = {actor["metadata"]["name"] for actor in actors}
        assert all(f"session-{session}" in names for session in acceptance["sessions"])
        assert all(actor["status"]["state"] == "ACTOR_STATE_SUSPENDED" for actor in actors), \
            "Refusing backup while any pilot actor is active or lifecycle work is unresolved"
        assert sql("SELECT count(*) FROM kagent.session_task WHERE state NOT IN "
                   "('TASK_STATE_COMPLETED','TASK_STATE_CANCELED','TASK_STATE_FAILED','TASK_STATE_REJECTED')") == "0", \
            "Refusing backup with unresolved task state"
        for ns in ("kagent", "ate-system", "podcertificate-controller-system"):
            (backup / f"{ns}-resources.json").write_bytes(kube(
                "get", "secrets,configmaps,deployments,services,pvc,serviceaccounts", "-n", ns, "-o", "json",
            ))
        (backup / "trust-bundles.json").write_bytes(kube("get", "clustertrustbundles", "-o", "json"))
        (backup / "agent-resources.json").write_bytes(kube(
            "get", "agents.api.kagent.dev,agenttemplates.api.kagent.dev,harnesses.api.kagent.dev,workerpools.ate.dev",
            "-n", "kagent", "-o", "json",
        ))
        image = json.loads(kube("get", "harness", "deterministic-byo", "-n", "kagent", "-o", "json"))["spec"]["workload"]["image"]
        registry_digest = image.split("@")[1]
        report["pinned_byo_image"] = image
        (backup / "rustfs-values.yaml").write_bytes((BASE / "protected/rustfs-values.yaml").read_bytes())
        for ns, name in [
            ("kagent", "kagent-controller"), ("ate-system", "ate-controller"),
            ("ate-system", "ate-api-server"),
        ]:
            replicas = json.loads(kube("get", "deployment", name, "-n", ns, "-o", "json"))["spec"]["replicas"]
            stopped.append((ns, name, replicas))
            scale(ns, name, 0)
        with port_forward("ate-system", "rustfs", 19000, 9000) as origin:
            objects = S3(origin, credentials).hashes()
        database = table_hashes()
        (backup / "roles.sql").write_bytes(kube(
            "exec", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql", "--",
            "pg_dumpall", "-U", "postgres", "--roles-only", "--no-role-passwords",
        ))
        with (backup / "kagent.dump").open("wb") as output:
            subprocess.run([*K, "exec", "-n", "kagent", "deployment/kagent-postgresql", "-c", "postgresql", "--",
                            "pg_dump", "-U", "postgres", "-d", "kagent", "-Fc", "--create"],
                           env=ENV, stdout=output, check=True, timeout=300)
        for ns, name in [("ate-system", "rustfs"), ("kagent", "kagent-lab-registry")]:
            replicas = json.loads(kube("get", "deployment", name, "-n", ns, "-o", "json"))["spec"]["replicas"]
            stopped.append((ns, name, replicas))
            scale(ns, name, 0)
        for ns, claim, archive in [
            ("ate-system", "rustfs-data", "rustfs.tar"),
            ("kagent", "kagent-lab-registry", "registry.tar"),
        ]:
            pv = json.loads(kube("get", "pvc", claim, "-n", ns, "-o", "json"))["spec"]["volumeName"]
            source = json.loads(kube("get", "pv", pv, "-o", "json"))["spec"]["hostPath"]["path"]
            assert source.startswith(f"/mnt/microk8s-hostpath/{ns}-{claim}-pvc-")
            with (backup / archive).open("wb") as output:
                subprocess.run([
                    "sudo", "-u", "nibeauli", "-H", "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "10.82.0.3",
                    "sudo", "-n", "tar", "-C", source, "-cf", "-", ".",
                ], stdout=output, check=True, timeout=600)
        report.update(database=database, objects=objects)
    finally:
        restore_errors = []
        for ns, name, replicas in reversed(stopped):
            try:
                scale(ns, name, replicas)
            except Exception as error:
                restore_errors.append(f"{ns}/{name}: {error}")
        (backup / "report.json").write_text(json.dumps(report, indent=2))
        if restore_errors:
            raise RuntimeError("Failed to restore original replicas: " + "; ".join(restore_errors))
    data = backup / "recovered-rustfs"
    data.mkdir()
    with tarfile.open(backup / "rustfs.tar") as archive:
        archive.extractall(data, filter="data")
    os.chown(data, 10001, 10001)
    for root, directories, files in os.walk(data, followlinks=False):
        for name in directories + files:
            os.chown(Path(root) / name, 10001, 10001, follow_symlinks=False)
    registry_data = backup / "recovered-registry"
    registry_data.mkdir()
    with tarfile.open(backup / "registry.tar") as archive:
        archive.extractall(registry_data, filter="data")
    existing = subprocess.run([*K, "get", "namespace", namespace], capture_output=True, env=ENV)
    assert existing.returncode != 0 and b"NotFound" in existing.stderr, \
        "Recovery namespace absence could not be established"
    try:
        kube("apply", "-f", "-", input=json.dumps(manifest(
            namespace, "recovery-postgres", "recovery-rustfs", data, registry_data, credentials,
        )).encode())
        for name in ("recovery-postgres", "recovery-rustfs", "recovery-registry"):
            kube("rollout", "status", f"deployment/{name}", "-n", namespace, "--timeout=240s")
        roles = (backup / "roles.sql").read_bytes().replace(b"CREATE ROLE postgres;\n", b"")
        kube("exec", "-i", "-n", namespace, "deployment/recovery-postgres", "--",
             "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input=roles)
        with (backup / "kagent.dump").open("rb") as source:
            subprocess.run([*K, "exec", "-i", "-n", namespace, "deployment/recovery-postgres", "--",
                            "pg_restore", "-U", "postgres", "-d", "postgres", "--create", "--exit-on-error"],
                           stdin=source, env=ENV, check=True, capture_output=True, timeout=300)
        recovered = table_hashes(namespace, "deployment/recovery-postgres")
        assert recovered == database, "Restored PostgreSQL table content differs"
        report["restored_tables"] = len(database)
        with port_forward(namespace, "recovery-rustfs", 19000, 9000) as origin:
            recovered_objects = S3(origin, credentials).hashes()
        assert recovered_objects == objects, "Restored snapshot objects differ through S3"
        report["restored_snapshot_objects"] = len(objects)
        with port_forward(namespace, "recovery-registry", 15000, 5000) as origin:
            request = urllib.request.Request(
                origin + "/v2/kagent-lab/deterministic-byo/manifests/" + registry_digest,
                headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                restored_manifest = response.read()
            assert "sha256:" + hashlib.sha256(restored_manifest).hexdigest() == registry_digest
        report.update(result="PASS", restored_tables=len(database),
                      restored_snapshot_objects=len(objects),
                      restored_byo_manifest_digest=registry_digest,
                      scope="Isolated PostgreSQL logical restore and S3 object read/hash validation; not full control-plane disaster recovery")
    finally:
        if report["result"] != "PASS":
            try:
                (backup / "recovery-pods.json").write_bytes(kube("get", "pods", "-n", namespace, "-o", "json"))
                (backup / "recovery-rustfs.log").write_bytes(kube(
                    "logs", "deployment/recovery-rustfs", "-n", namespace, "--tail=100",
                ))
            except Exception:
                pass
        kube("delete", "namespace", namespace, "--wait=true", "--timeout=180s")
        (backup / "report.json").write_text(json.dumps(report, indent=2))
        (BASE / "protected/recovery-report.json").write_text(json.dumps(report, indent=2))
        if report["result"] == "PASS":
            assert data.name == "recovered-rustfs" and data.parent == backup
            shutil.rmtree(data)
            assert registry_data.name == "recovered-registry" and registry_data.parent == backup
            shutil.rmtree(registry_data)
    print(json.dumps({"result": report["result"], "backup": str(backup),
                      "restored_tables": report.get("restored_tables"),
                      "restored_snapshot_objects": report.get("restored_snapshot_objects")}))
