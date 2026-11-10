#!/usr/bin/env bash
set -euo pipefail
base=/home/nibeauli/kagent-lab
export PATH="$base/bin:$PATH"
export KUBECONFIG=/var/snap/microk8s/current/credentials/client.config
install -d "$base/bin" "$base/config"
install -d -m 700 "$base/protected"
for file in helm kubectl post-render post_render.py test_post_render.py; do
    install -m 755 "/mnt/q/tools/kagent-lab/bin/$file" "$base/bin/$file"
    sed -i 's/\r$//' "$base/bin/$file"
done
python3 "$base/bin/test_post_render.py"
for file in kagent-values.yaml substrate-values.yaml substrate-bootstrap-values.yaml; do
    install -m 600 "/mnt/q/tools/kagent-lab/$file" "$base/config/$file"
done
python3 - <<'PY'
from pathlib import Path
import secrets
import yaml

base = Path("/home/nibeauli/kagent-lab")
private = base / "protected/rustfs-values.yaml"
if not private.exists():
    with private.open("x") as output:
        yaml.safe_dump({"rustfs": {"accessKey": secrets.token_hex(16), "secretKey": secrets.token_hex(32)}}, output)
    private.chmod(0o600)
PY
test -x "$base/artifacts/bootstrap-platform"
chart() {
    local release=$1 namespace=$2
    shift 2
    local log="$base/protected/helm-$release.log"
    for attempt in 1 2 3; do
        if helm upgrade --install "$release" "$base/artifacts/$release-1.0.0-rc1.tgz" \
            --namespace "$namespace" --create-namespace --wait --wait-for-jobs \
            --history-max 5 --timeout 5m "$@" >"$log" 2>&1; then
            chmod 600 "$log"
            printf 'PASS: release %s is deployed\n' "$release"
            return
        fi
        chmod 600 "$log"
        if [ "$attempt" -lt 3 ] && grep -q 'database is locked' "$log"; then
            sleep 5
        else
            printf 'Helm failed for %s; diagnostics are protected at %s\n' "$release" "$log" >&2
            return 1
        fi
    done
}
chart kagent-crds kagent
chart substrate-crds ate-system
"$base/artifacts/bootstrap-platform" identities
chart substrate-podcert podcertificate-controller-system \
    --set-string 'postgresClients[0]=kagent/kagent-controller=kagent_owner_user' \
    --set-string 'postgresClients[1]=kagent/kagent-controller=kagent_readwrite_user'
"$base/artifacts/bootstrap-platform" postgres
chart substrate ate-system -f "$base/config/substrate-bootstrap-values.yaml" \
    -f "$base/config/substrate-values.yaml" -f "$base/protected/rustfs-values.yaml"
chart kagent kagent -f "$base/config/kagent-values.yaml"
for attempt in $(seq 1 60); do
    if kubectl -n kagent get deployment/kagent-default >/dev/null 2>&1; then break; fi
    sleep 1
done
kubectl apply --server-side --field-manager=kagent-lab-placement -f /mnt/q/tools/kagent-lab/worker-placement.yaml
kubectl -n kagent rollout status deployment/kagent-default --timeout=300s
kubectl -n kagent get pods,workerpools,pvc -o wide
kubectl -n ate-system get pods,pvc -o wide
