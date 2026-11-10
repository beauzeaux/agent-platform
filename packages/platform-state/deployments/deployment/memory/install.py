"""Install only owned canary resources; never update the business database."""
import argparse
import json
import secrets
import subprocess
import tarfile
from common import (ENV, KUBE, NAMESPACE, OWNER, PINS, STATE, apply, existing,
                    kube, prepare_state, remove, require_owned, run)
from prepare import digest

PG = "memory-canary-postgres"
EMBED = "local-embeddings"
CACHE = "memory-canary-model"
LABELS = {"app.kubernetes.io/managed-by": OWNER}
NODE = {"kubernetes.io/hostname": "cpc-nibea-8t76e"}


def document(kind, name, spec, api="v1"):
    return {"apiVersion": api, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": dict(LABELS)}, "spec": spec}


def policy(name, selector, ingress):
    return document("NetworkPolicy", name, {
        "podSelector": {"matchLabels": selector}, "policyTypes": ["Ingress", "Egress"],
        "ingress": ingress, "egress": []}, "networking.k8s.io/v1")


def pod_spec(containers, volumes, group):
    return {"nodeSelector": NODE, "automountServiceAccountToken": False,
            "securityContext": {"fsGroup": group, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": containers, "volumes": volumes, "terminationGracePeriodSeconds": 30}


def deployment(name, spec):
    return document("Deployment", name, {
        "replicas": 1, "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": name}},
        "template": {"metadata": {"labels": {**LABELS, "app": name}}, "spec": spec}}, "apps/v1")


def diagnose():
    for kind, name in [("deployment", PG), ("deployment", EMBED), ("pod", "memory-canary-cache-loader")]:
        current = require_owned(kind, name)
        if current:
            (STATE / (name + "-describe.log")).write_bytes(kube("describe", kind, name, "-n", NAMESPACE))
            if kind == "deployment":
                result = subprocess.run([*KUBE, "logs", "-n", NAMESPACE, "deployment/" + name,
                                         "--all-containers", "--tail=100"], env=ENV, capture_output=True, timeout=30)
                (STATE / (name + "-runtime.log")).write_bytes(result.stdout + result.stderr)


def cleanup():
    for kind, name in [("deployment", EMBED), ("deployment", PG), ("pod", "memory-canary-cache-loader"),
                       ("service", EMBED), ("secret", PG + "-auth")]:
        remove(kind, name)


def install():
    prepare_state()
    for name, expected in PINS["modelFiles"].items():
        if digest(STATE / "model" / name) != expected:
            raise RuntimeError("pinned model SHA-256 mismatch before installation")
    image = json.loads((STATE / "image.json").read_text())
    tei = PINS["teiImage"]
    run(["/snap/bin/microk8s", "ctr", "--namespace", "k8s.io", "images", "pull", tei],
        timeout=600, name="tei-image-pull")
    labels = {"app.kubernetes.io/managed-by": OWNER}
    peer = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ate-system"}},
            "podSelector": {"matchLabels": {"app": "atenet-egress"}}}
    apply(policy("memory-canary-isolation", labels, []))
    apply(policy("memory-canary-embedding-ingress", {"app": EMBED},
                 [{"from": [peer], "ports": [{"port": 8080, "protocol": "TCP"}]}]))
    apply(document("PersistentVolumeClaim", CACHE, {
        "accessModes": ["ReadWriteOnce"], "storageClassName": "microk8s-hostpath-devdrive",
        "resources": {"requests": {"storage": "2Gi"}}}))
    try:
        loader = "memory-canary-cache-loader"
        remove("pod", loader)
        apply(document("Pod", loader, pod_spec([{
            "name": "loader", "image": tei, "imagePullPolicy": "IfNotPresent",
            "command": ["sleep", "900"],
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                          "limits": {"cpu": "500m", "memory": "256Mi"}},
            "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True,
                                "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            "volumeMounts": [{"name": "model", "mountPath": "/model"}]
        }], [{"name": "model", "persistentVolumeClaim": {"claimName": CACHE}}], 1000)))
        kube("wait", "pod/" + loader, "-n", NAMESPACE, "--for=condition=Ready", "--timeout=120s", timeout=140)
        archive = STATE / "model.tar"
        try:
            with tarfile.open(archive, "w") as tar:
                for name in PINS["modelFiles"]:
                    tar.add(STATE / "model" / name, arcname=name)
            with archive.open("rb") as stream:
                result = subprocess.run([*KUBE, "exec", "-i", "-n", NAMESPACE, loader, "--",
                                         "tar", "-xf", "-", "-C", "/model"], stdin=stream, capture_output=True,
                                        timeout=600, env=ENV)
            if result.returncode:
                (STATE / "cache-copy.log").write_bytes(result.stderr)
                raise RuntimeError("model cache transfer failed")
        finally:
            archive.unlink(missing_ok=True)
            remove("pod", loader)
        auth = require_owned("secret", PG + "-auth")
        if not auth:
            apply({"apiVersion": "v1", "kind": "Secret",
                   "metadata": {"name": PG + "-auth", "namespace": NAMESPACE},
                   "stringData": {"password": secrets.token_urlsafe(40)}})
        pg_container = {
            "name": "postgresql", "image": image["repository"] + "@" + image["digest"],
            "imagePullPolicy": "IfNotPresent",
            "env": [{"name": "POSTGRES_DB", "value": "memory_canary"},
                    {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
                    {"name": "POSTGRES_PASSWORD_FILE", "value": "/run/canary-auth/password"}],
            "securityContext": {"runAsUser": 70, "runAsGroup": 70, "runAsNonRoot": True,
                                "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                          "limits": {"cpu": "1", "memory": "1Gi"}},
            "readinessProbe": {"exec": {"command": ["pg_isready", "-U", "postgres", "-d", "memory_canary"]},
                               "periodSeconds": 5, "failureThreshold": 12},
            "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"},
                            {"name": "auth", "mountPath": "/run/canary-auth", "readOnly": True},
                            {"name": "work", "mountPath": "/work"}]}
        apply(deployment(PG, pod_spec([pg_container], [
            {"name": "data", "emptyDir": {"sizeLimit": "2Gi"}},
            {"name": "auth", "secret": {"secretName": PG + "-auth", "defaultMode": 0o440}},
            {"name": "work", "emptyDir": {"sizeLimit": "512Mi"}}], 70)))
        env = {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
               "HF_HOME": "/tmp/hf", "RAYON_NUM_THREADS": "2", "OMP_NUM_THREADS": "2",
               "MKL_NUM_THREADS": "2", "MKL_ENABLE_INSTRUCTIONS": "AVX2"}
        embed_container = {
            "name": "embeddings", "image": tei, "imagePullPolicy": "IfNotPresent",
            "args": ["--model-id", "/model", "--served-model-name", PINS["model"], "--port", "8080",
                     "--hostname", "0.0.0.0", "--dtype", "float32", "--pooling", "cls",
                     "--tokenization-workers", "2", "--max-concurrent-requests", "8",
                     "--max-client-batch-size", "8", "--max-batch-tokens", "1024", "--auto-truncate", "false",
                     "--json-output"],
            "env": [{"name": key, "value": value} for key, value in env.items()],
            "ports": [{"name": "http", "containerPort": 8080}],
            "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True,
                                "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "1", "memory": "1Gi"},
                          "limits": {"cpu": "2", "memory": "2Gi"}},
            "startupProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 5,
                             "failureThreshold": 24},
            "readinessProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 10},
            "livenessProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 30},
            "volumeMounts": [{"name": "model", "mountPath": "/model", "readOnly": True},
                            {"name": "tmp", "mountPath": "/tmp"}]}
        apply(deployment(EMBED, pod_spec([embed_container], [
            {"name": "model", "persistentVolumeClaim": {"claimName": CACHE, "readOnly": True}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}], 1000)))
        apply(document("Service", EMBED, {"type": "ClusterIP", "selector": {"app": EMBED},
                                         "ports": [{"port": 8080, "targetPort": "http", "name": "http"}]}))
        for name in [PG, EMBED]:
            kube("rollout", "status", "deployment/" + name, "-n", NAMESPACE, "--timeout=180s", timeout=200)
        from verify import verify
        verify()
        print("Healthy isolated memory canary installed; business PG/schema unchanged.")
    except Exception:
        diagnose()
        cleanup()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "remove"])
    args = parser.parse_args()
    prepare_state()
    install() if args.action == "install" else cleanup()
