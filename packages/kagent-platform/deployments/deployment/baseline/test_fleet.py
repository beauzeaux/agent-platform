import copy
import subprocess
import unittest
from unittest.mock import patch

from fleet import credential_policy, kube, runtime_documents


class FleetTests(unittest.TestCase):
    def test_only_transient_cluster_database_contention_is_retried(self):
        locked = subprocess.CompletedProcess([], 1, b"", b"database is locked")
        ready = subprocess.CompletedProcess([], 0, b'{"ready":true}', b"")
        with patch("fleet.subprocess.run", side_effect=[locked, ready]) as run, patch("fleet.time.sleep"):
            self.assertEqual(kube("get", "namespace", "baseline"), {"ready": True})
            self.assertEqual(run.call_count, 2)
        refused = subprocess.CompletedProcess([], 1, b"", b"Forbidden")
        with patch("fleet.subprocess.run", return_value=refused) as run, patch("fleet.STATE") as state:
            state.exists.return_value = False
            with self.assertRaises(RuntimeError):
                kube("get", "namespace", "baseline")
            self.assertEqual(run.call_count, 1)

    def test_scoped_grants_preserve_existing_and_are_repeat_safe(self):
        old = {"policies": [{"atespace": "kagent", "allowedNamespaces": ["kagent"]}]}
        original = copy.deepcopy(old)
        updated = credential_policy(old)
        self.assertEqual(old, original)
        self.assertEqual(updated["policies"][0], old["policies"][0])
        self.assertEqual(credential_policy(updated), updated)
        for grant in updated["policies"][1:]:
            self.assertEqual(grant["allowedNamespaces"], [grant["atespace"]])
            self.assertEqual(set(grant["secretSelector"]["matchLabels"]),
                             {"app.kubernetes.io/managed-by", "host-capabilities/access"})
        self.assertEqual({x["atespace"] for x in updated["policies"][1:]}, {"baseline", "cluster-management"})

    def test_namespace_local_pool_preserves_platform_contract_without_rbac(self):
        pool = {"spec": {"replicas": 2, "workerImage": "pinned-worker", "sandboxClasses": [
            {"name": "gvisor", "configRef": {"name": "gvisor-default"}}],
            "template": {"serviceAccountName": "kagent-default", "resources": {"requests": {"cpu": "1"}}}}}
        original = copy.deepcopy(pool)
        values = runtime_documents("baseline", pool)
        self.assertEqual(pool, original)
        self.assertEqual({x["kind"] for x in values}, {"ServiceAccount", "WorkerPool"})
        worker = values[1]
        self.assertEqual(worker["metadata"]["namespace"], "baseline")
        self.assertEqual(worker["spec"], {**pool["spec"], "replicas": 1})


if __name__ == "__main__":
    unittest.main()
