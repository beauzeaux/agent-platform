import json
import http.client
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = Path("/home/nibeauli/kagent-lab")
ATE = str(BASE / "artifacts/kubectl-ate")
SPACE = "kagent-lab-runtime"
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
KUBECTL = ["/snap/bin/microk8s", "kubectl"]


def ate(*arguments):
    result = subprocess.run([ATE, "--preview", *arguments], env=ENV, text=True, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def actor_state(name):
    return json.loads(ate("get", "actor", name, "-a", SPACE, "-o", "json"))


def workers():
    return json.loads(ate("get", "workers", "-o", "json"))["workers"]


def fetch(name):
    with (BASE / "protected/golden-proxy.log").open("w") as log:
        proxy = subprocess.Popen([ATE, "proxy", "actor", name, "18080:80", "-a", SPACE], env=ENV, stdout=log, stderr=log)
        try:
            for attempt in range(180):
                if proxy.poll() is not None:
                    raise RuntimeError("Golden actor proxy exited; inspect protected/golden-proxy.log")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18080/readyz", timeout=2) as response:
                        assert response.status == 200
                    break
                except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError):
                    time.sleep(1)
            else:
                raise RuntimeError("Golden actor did not become responsive")
            data = {}
            for key in ("boots", "instance", "note"):
                with urllib.request.urlopen(f"http://127.0.0.1:18080/{key}", timeout=10) as response:
                    data[key] = response.read().decode().strip()
            return data
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()


if __name__ == "__main__":
    name = "golden-" + uuid.uuid4().hex[:12]
    try:
        ate("get", "atespace", SPACE)
    except RuntimeError as error:
        if "not found" not in str(error).lower() and "notfound" not in str(error).lower():
            raise
        ate("create", "atespace", SPACE)
    try:
        ate("get", "actor-template", "golden-volumes-v2", "-a", SPACE)
    except RuntimeError as error:
        if "not found" not in str(error).lower() and "notfound" not in str(error).lower():
            raise
        ate("create", "actor-template", "-f", "/mnt/q/tools/kagent-lab/golden-template.yaml")
    ate("create", "actor", name, "-a", SPACE, "--template", "golden-volumes-v2")
    cordoned = None
    try:
        first = fetch(name)
        assert first["boots"] == "1", first
        assert first["note"] == "durable-volume-proof", first
        first_node = actor_state(name)["status"]["assignedNode"]
        ate("suspend", "actor", name, "-a", SPACE, "--mode", "sync")
        suspended = actor_state(name)
        if "--cross-node" in sys.argv:
            candidates = [
                worker for worker in workers()
                if worker.get("workerPool") == "kagent-default"
                and worker["status"]["state"] == "WORKER_STATE_ACTIVE"
            ]
            assert len({worker["nodeName"] for worker in candidates}) == 3, candidates
            old = next(worker for worker in candidates if worker["nodeName"] == first_node)
            subprocess.run([*KUBECTL, "cordon", first_node], check=True)
            cordoned = first_node
            subprocess.run([*KUBECTL, "delete", "pod", old["workerPod"], "-n", "kagent", "--wait=true"], check=True, timeout=120)
            for attempt in range(60):
                if not any(
                    worker["metadata"]["name"] == old["metadata"]["name"]
                    and worker["status"]["state"] == "WORKER_STATE_ACTIVE"
                    for worker in workers()
                ):
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Deleted worker did not retire from the runtime scheduler")
        second = fetch(name)
        assert second["boots"] == "2", second
        assert second["instance"] != first["instance"], (first, second)
        assert second["note"] == first["note"], (first, second)
        second_node = actor_state(name)["status"]["assignedNode"]
        if "--cross-node" in sys.argv:
            assert second_node != first_node, (first_node, second_node)
        ate("suspend", "actor", name, "-a", SPACE, "--mode", "sync")
        report = {"actor": name, "first": first, "restored": second, "suspended": suspended, "nodes": [first_node, second_node]}
        path = BASE / ("protected/golden-cross-node-report.json" if "--cross-node" in sys.argv else "protected/golden-runtime-report.json")
        path.write_text(json.dumps(report, indent=2))
        path.chmod(0o600)
        print(json.dumps({"result": "PASS", "actor": name, "cold_boots": [1, 2], "durable_note_preserved": True, "nodes": [first_node, second_node]}))
    finally:
        if cordoned:
            subprocess.run([*KUBECTL, "uncordon", cordoned], check=True)
        ate("delete", "actor", name, "-a", SPACE)
