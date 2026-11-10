"""Add one native, label-scoped trusted-egress grant; preserve all existing grants."""
import datetime
import json
from pathlib import Path
import subprocess

import yaml

KUBE = ["/snap/bin/microk8s", "kubectl", "--request-timeout=15s"]
GRANT = {"atespace": "factory-executions", "allowedNamespaces": ["factory-executions"],
         "secretSelector": {"matchLabels": {"app.kubernetes.io/managed-by": "host-capabilities-management",
                                          "host-capabilities/access": "management-model"}}}


def main():
    name = "k8s-credential-provider-namespace-policy"
    current = json.loads(subprocess.check_output([*KUBE, "get", "configmap", name, "-n", "ate-system", "-o", "json"]))
    if current["metadata"].get("annotations", {}).get("meta.helm.sh/release-name") != "substrate":
        raise RuntimeError("Credential policy ownership changed.")
    policy = yaml.safe_load(current["data"]["namespace-policy.yaml"])
    policies = policy.setdefault("policies", [])
    if GRANT in policies:
        print("Exact management-model grant already present.")
        return
    state = Path("/home/nibeauli/kagent-lab/cluster-management")
    backup = state / ("credential-policy-before-" +
                      datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    backup.write_text(json.dumps(current, indent=2))
    backup.chmod(0o600)
    policies.append(GRANT)
    overlay = state / "substrate-management-grant.values.yaml"
    overlay.write_text(yaml.safe_dump({"credentialProvider": {"namespacePolicies": policies}}))
    overlay.chmod(0o600)
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
             {"op": "replace", "path": "/data/namespace-policy.yaml", "value": yaml.safe_dump(policy)}]
    subprocess.run([*KUBE, "patch", "configmap", name, "-n", "ate-system", "--type=json", "-p", json.dumps(patch)],
                   capture_output=True, check=True)
    subprocess.run([*KUBE, "rollout", "restart", "deployment/k8s-credential-provider", "-n", "ate-system"],
                   capture_output=True, check=True)
    subprocess.run([*KUBE, "rollout", "status", "deployment/k8s-credential-provider", "-n", "ate-system",
                    "--timeout=120s"], check=True)
    print(json.dumps({"nativeGrantAdded": True, "scope": "only management-model-labelled Secrets",
                      "allExistingGrantsPreserved": True,
                      "futureSubstrateUpgradeValues": str(overlay), "backup": backup.name}))


if __name__ == "__main__":
    main()
