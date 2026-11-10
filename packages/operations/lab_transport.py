from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import time

BASE = Path("/home/nibeauli/kagent-lab")
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")


@contextmanager
def port_forward(namespace, service, local_port, remote_port):
    path = BASE / "protected" / f"forward-{service}-{local_port}.log"
    with path.open("w") as log:
        path.chmod(0o600)
        process = subprocess.Popen(
            ["/snap/bin/microk8s", "kubectl", "port-forward", "-n", namespace, f"service/{service}", f"{local_port}:{remote_port}", "--address=127.0.0.1"],
            env=ENV, stdout=log, stderr=log,
        )
        try:
            for attempt in range(90):
                if process.poll() is not None:
                    raise RuntimeError(f"Port-forward exited; inspect {path}")
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError(f"Port-forward was not responsive; inspect {path}")
            yield f"http://127.0.0.1:{local_port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
