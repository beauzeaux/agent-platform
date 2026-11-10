import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import promote_harness as promotion


class PromotionBoundaryTests(unittest.TestCase):
    def test_status_only_resource_version_change_is_refreshed_but_changed_spec_is_rejected(self):
        before = {"metadata": {"name": "owned", "uid": "same", "resourceVersion": "1"},
                  "spec": {"existing": "keep"}}
        current = copy.deepcopy(before)
        current["metadata"]["resourceVersion"] = "2"
        with patch.object(promotion, "get", return_value=current), \
             patch.object(promotion.subprocess, "run") as command:
            command.return_value.returncode = 0
            promotion.patch("agent", before, [{"op": "add", "path": "/spec/new", "value": "enabled"}])
            args = command.call_args.args[0]
            self.assertEqual(json.loads(args[-1])[0]["value"], "2")
        current["spec"]["existing"] = "changed"
        with patch.object(promotion, "get", return_value=current), \
             patch.object(promotion.subprocess, "run") as command:
            with self.assertRaises(RuntimeError):
                promotion.patch("agent", before, [])
            command.assert_not_called()

    def proof(self, directory):
        receipt = {"state": "TASK_STATE_COMPLETED", "receipt": {
            "archive": {"status": "exported"}, "result": {"visibleRows": 0}}}
        value = {"tasks": {phase: {"result": copy.deepcopy(receipt)} for phase in
                          ("bound-metadata", "seed", "isolate", "model", "seed-recurrence")}}
        file = Path(directory) / "proof.json"
        file.write_text(json.dumps(value))
        return file, value

    def resources(self, kind, name):
        value = {"metadata": {"name": name, "resourceVersion": "1", "generation": 1,
                              "labels": {"app.kubernetes.io/managed-by": "factory-coding"}},
                 "spec": {}}
        if kind == "harness":
            value["spec"] = {"workload": {"image": "original"}, "env": [{"name": "existing", "value": "keep"}],
                             "substrate": {"snapshotPolicy": {"location": "original-snapshot"}}}
        elif kind == "agent":
            value["spec"] = {"template": {"ref": {"name": name}}, "egressPolicyRefs": [{"name": "existing"}]}
        elif kind == "agenttemplate":
            value["spec"] = {"systemPrompt": "existing instructions"}
        else:
            return None
        return value

    def test_rejects_unowned_or_changed_declarations_before_any_mutation(self):
        image = "10.152.183.131:5000/kagent-lab/factory-coder@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            file, _ = self.proof(directory)
            for altered in ("owner", "model"):
                def get(kind, name):
                    value = self.resources(kind, name)
                    if kind == "agenttemplate":
                        if altered == "owner":
                            value["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "someone-else"
                        else:
                            value["spec"]["modelConfig"] = {"name": "existing-model"}
                    return value
                with patch.object(promotion, "get", side_effect=get), \
                     patch.object(promotion, "patch") as write, \
                     patch.object(promotion.subprocess, "run") as command:
                    with self.assertRaises(RuntimeError):
                        promotion.promote(image, file, apply=True)
                    write.assert_not_called()
                    command.assert_not_called()

    def test_failed_archive_or_foreign_memory_gate_prevents_changes(self):
        image = "10.152.183.131:5000/kagent-lab/factory-coder@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            file, value = self.proof(directory)
            value["tasks"]["model"]["result"]["receipt"]["archive"]["status"] = "pending"
            file.write_text(json.dumps(value))
            with patch.object(promotion, "get") as read:
                with self.assertRaises(RuntimeError):
                    promotion.promote(image, file, apply=True)
                read.assert_not_called()
            value["tasks"]["model"]["result"]["receipt"]["archive"]["status"] = "exported"
            value["tasks"]["isolate"]["result"]["receipt"]["result"]["visibleRows"] = 1
            file.write_text(json.dumps(value))
            with patch.object(promotion, "get") as read:
                with self.assertRaises(RuntimeError):
                    promotion.promote(image, file, apply=True)
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
