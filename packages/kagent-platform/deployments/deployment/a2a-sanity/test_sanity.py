import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

import sanity
from resources import documents

RUN = "18cd2612-0fc0-4e8c-ae73-3f5b25c56e7c"
IMAGE = "registry.invalid:5000/kagent-lab/a2a-sanity@sha256:" + "a" * 64


def task(value):
    return {"status": {"message": {"parts": [{"text": json.dumps(value)}]}}}


class SanityTests(unittest.TestCase):
    def test_native_model_contract_and_prospective_live_gate(self):
        values = documents(RUN, IMAGE)
        models = [value["spec"] for value in values if value["kind"] == "ModelConfig"]
        self.assertEqual(len({value["model"] for value in models}), 3)
        argon = next(value for value in models if value["model"] == "gemini-4-argon")
        self.assertEqual(argon["openAI"]["apiFormat"], "chatCompletions")
        harness = next(value["spec"] for value in values
                       if value["kind"] == "Harness" and "byo" in value["spec"])
        self.assertIn({"name": "A2A_SANITY_LIVE", "value": "false"}, harness["env"])
        self.assertFalse(any(value["kind"] in {"Namespace", "Secret", "Deployment"} for value in values))

    def test_coder_result_requires_actual_tool_and_artifact_evidence(self):
        source = "public fixture"
        value = {"publicDataOnly": True, "status": "completed", "tools": ["repo_write", "fixture_test"],
                 "result": {"source": source, "sourceSha256": hashlib.sha256(source.encode()).hexdigest(),
                            "testsPassed": 7}}
        accepted = sanity.check_coder(task({"detail": json.dumps(value)}))
        self.assertEqual(accepted["source"], source)
        self.assertEqual(accepted["observedTools"], ["repo_write", "fixture_test"])
        value["tools"] = []
        with self.assertRaises(RuntimeError):
            sanity.check_coder(task(value))
        value["tools"] = ["repo_write", "fixture_test"]
        value["result"]["source"] = "tampered"
        with self.assertRaises(RuntimeError):
            sanity.check_coder(task(value))

    def test_review_cannot_pass_without_fresh_mcp_nonce_and_same_artifact(self):
        value = {"verdict": "pass", "nonce": "actual-tool-value", "sourceSha256": "abc"}
        self.assertTrue(sanity.check_review(task(value), "actual-tool-value", "abc")["mcpChallengeVerified"])
        for nonce, digest in [("guessed", "abc"), ("actual-tool-value", "different")]:
            with self.assertRaises(RuntimeError):
                sanity.check_review(task(value), nonce, digest)

    def test_foreign_or_replaced_resource_is_never_accepted_for_cleanup(self):
        document = documents(RUN, IMAGE)[0]
        state = {"runId": RUN, "uids": {}}
        foreign = {"metadata": {"name": document["metadata"]["name"], "uid": "one", "labels": {}}}
        with patch.object(sanity, "kube", return_value=foreign):
            with self.assertRaises(RuntimeError):
                sanity.existing(document, state)
        foreign["metadata"]["labels"] = document["metadata"]["labels"]
        state["uids"][document["kind"] + "/" + document["metadata"]["name"]] = "different"
        with patch.object(sanity, "kube", return_value=foreign):
            with self.assertRaises(RuntimeError):
                sanity.existing(document, state)

    def test_uncertain_delivery_is_observed_not_resent(self):
        state = {"runId": RUN, "sessions": {"coder": str(uuid.uuid4())},
                 "tasks": {"coder": {"attempted": True, "messageId": str(uuid.uuid4())}}}
        class Client:
            origin = "http://127.0.0.1:1234"
            token_file = None

            def __init__(self):
                self.methods = []

            def rpc(self, agent, method, params):
                self.methods.append(method)
                return {"tasks": []}
        client = Client()
        with patch.object(sanity, "session_rpc", return_value=state["sessions"]["coder"]):
            with self.assertRaisesRegex(RuntimeError, "Uncertain"):
                sanity.role_task(state, client, "coder", "sanity-code")
        self.assertEqual(client.methods, ["ListTasks"])

    def test_cleanup_retries_do_not_delete_an_already_deleted_session_again(self):
        session = str(uuid.uuid4())
        state = {"runId": RUN, "phase": "failed", "image": IMAGE, "live": True,
                 "sessions": {"coder": session}, "deletedSessions": [session], "uids": {}}
        class Client:
            origin = "unused"
            token_file = None
        with patch.object(sanity, "session_rpc") as rpc, patch.object(sanity, "existing", return_value=None), \
                patch.object(sanity, "save"), patch.object(sanity, "wait_actor_absent") as absent:
            result = sanity.remove(state, Client(), True)
        rpc.assert_not_called()
        absent.assert_called_once_with(session, "factory-executions")
        self.assertEqual(result["cleanup"]["sessionsAbsent"], 1)

    def test_actor_transport_failure_is_not_mistaken_for_cleanup_success(self):
        result = type("Result", (), {"returncode": 1, "stderr": b"connection refused"})()
        with patch.object(sanity.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "absence could not"):
                sanity.wait_actor_absent(str(uuid.uuid4()))
        result.stderr = b"rpc error: code = NotFound desc = actor not found"
        with patch.object(sanity.subprocess, "run", return_value=result):
            sanity.wait_actor_absent(str(uuid.uuid4()))

    def test_baseline_resources_use_their_own_namespace_secret_and_stable_names(self):
        values = documents(RUN, IMAGE, namespace="baseline")
        self.assertTrue(all(value["metadata"]["namespace"] == "baseline" for value in values))
        models = [value["spec"] for value in values if value["kind"] == "ModelConfig"]
        self.assertTrue(all(value["apiKeySecret"] == "baseline-llm" for value in models))
        self.assertEqual({value["metadata"]["name"] for value in values if value["kind"] == "Agent"},
                         {"baseline-coder", "baseline-sol", "baseline-argon"})
        templates = [value["spec"] for value in values if value["kind"] == "AgentTemplate"]
        self.assertTrue(all("factory-executions" not in value["systemPrompt"] for value in templates))


if __name__ == "__main__":
    unittest.main()
