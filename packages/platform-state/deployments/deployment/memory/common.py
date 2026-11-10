"""Bounded, owner-scoped operations for the isolated memory canary."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time

SOURCE = Path(__file__).resolve().parent
STATE = Path("/home/nibeauli/kagent-lab/memory-canary")
UPSTREAM = Path("/home/nibeauli/kagent-lab/source/kagent")
KUBE = ["/snap/bin/microk8s", "kubectl"]
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
OWNER = "host-capabilities-memory-canary"
NAMESPACE = "factory-executions"
PINS = json.loads((SOURCE / "pins.json").read_text())


def prepare_state():
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)


def run(command, *, data=None, timeout=180, name="command"):
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired as error:
        prepare_state()
        (STATE / (name + ".log")).write_bytes((error.stdout or b"") + (error.stderr or b""))
        raise RuntimeError(name + " timed out; inspect protected memory-canary diagnostics") from None
    if result.returncode:
        prepare_state()
        (STATE / (name + ".log")).write_bytes(result.stdout + result.stderr)
        raise RuntimeError(name + " failed; inspect protected memory-canary diagnostics")
    return result.stdout


def kube(*args, data=None, timeout=180):
    return run([*KUBE, *args], data=data, timeout=timeout, name="kubernetes")


def existing(kind, name, namespace=NAMESPACE):
    output = kube("get", kind, name, "-n", namespace, "--ignore-not-found", "-o", "json")
    return json.loads(output) if output.strip() else None


def require_owned(kind, name, namespace=NAMESPACE):
    current = existing(kind, name, namespace)
    if current and current["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("refusing to alter unowned " + kind + "/" + name)
    return current


def apply(document):
    meta = document["metadata"]
    require_owned(document["kind"], meta["name"], meta.get("namespace", NAMESPACE))
    meta.setdefault("labels", {})["app.kubernetes.io/managed-by"] = OWNER
    return json.loads(kube("apply", "--server-side", "--field-manager=" + OWNER,
                          "-f", "-", "-o", "json", data=json.dumps(document).encode()))


def remove(kind, name):
    if require_owned(kind, name):
        kube("delete", kind, name, "-n", NAMESPACE, "--wait=true", "--timeout=90s")


@contextmanager
def forward(namespace, service, remote_port):
    prepare_state()
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with (STATE / ("forward-" + service + ".log")).open("wb") as log:
        process = subprocess.Popen([*KUBE, "port-forward", "-n", namespace, "service/" + service,
                                    str(port) + ":" + str(remote_port), "--address=127.0.0.1"],
                                   stdout=log, stderr=log, env=ENV)
        try:
            for _ in range(45):
                if process.poll() is not None:
                    raise RuntimeError("private forward exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("private forward readiness timeout")
            yield "http://127.0.0.1:" + str(port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
