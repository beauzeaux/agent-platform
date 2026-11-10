#!/usr/bin/env bash
set -euo pipefail
backup=/var/snap/microk8s/common/kagent-lab-backup-20261031
test -s "$backup/datastore.tar.gz"
python3 - <<'PY'
import os
from pathlib import Path
import tempfile

root = Path("/var/snap/microk8s/current/args")
gates = {
    "ClusterTrustBundle": "true",
    "ClusterTrustBundleProjection": "true",
    "PodCertificateRequest": "true",
}
for component in ("kube-apiserver", "kubelet", "kube-controller-manager"):
    path = root / component
    lines = path.read_text().splitlines()
    settings = {"feature-gates": gates}
    if component == "kube-apiserver":
        settings["runtime-config"] = {"certificates.k8s.io/v1beta1": "true"}
    for flag, additions in settings.items():
        prefix = f"--{flag}="
        indexes = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if len(indexes) > 1:
            raise RuntimeError(f"duplicate {flag} in {component}")
        values = {}
        if indexes:
            for item in lines[indexes[0]][len(prefix):].split(","):
                key, value = item.split("=", 1)
                values[key] = value
        values.update(additions)
        line = prefix + ",".join(f"{key}={value}" for key, value in values.items())
        if indexes:
            lines[indexes[0]] = line
        else:
            lines.append(line)
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as output:
        output.write("\n".join(lines) + "\n")
        name = output.name
    os.chmod(name, path.stat().st_mode & 0o777)
    os.replace(name, path)
    print(f"Configured certificate features in {component}")
PY
systemctl restart snap.microk8s.daemon-kubelite
for attempt in $(seq 1 90); do
    if /snap/bin/microk8s kubectl get --raw /readyz >/dev/null 2>&1; then
        /snap/bin/microk8s kubectl wait --for=condition=Ready node/"$(hostname | tr '[:upper:]' '[:lower:]')" --timeout=120s
        /snap/bin/microk8s kubectl api-resources --api-group=certificates.k8s.io
        printf 'PASS: certificate APIs and node readiness on %s\n' "$(hostname)"
        exit 0
    fi
    sleep 2
done
journalctl -u snap.microk8s.daemon-kubelite -n 30 --no-pager
exit 1
