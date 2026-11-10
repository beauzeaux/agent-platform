import json
import unittest

from resources import resources, HOST, AUDIENCE


class PublicResourceTests(unittest.TestCase):
    def test_distinct_agent_scopes_use_native_actor_jwt_and_only_public_runtime_config(self):
        document = resources("10.152.183.131:5000/kagent-lab/native-public-canary@sha256:" + "1" * 64,
            "11111111-2222-4333-8444-555555555555", "gpt-5.6-sol", "kagent-default",
            "gs://ate-snapshots/public-native-canary/", True)
        agents = [row for row in document["items"] if row["kind"] == "Agent"]
        self.assertEqual([row["metadata"]["name"] for row in agents], ["public-native-canary-a", "public-native-canary-b"])
        model = document["items"][0]["spec"]
        self.assertEqual(model["openAI"]["baseUrl"], HOST + "/v1")
        self.assertEqual(model["actorJwt"]["audiences"], [AUDIENCE])
        harnesses = [row for row in document["items"] if row["kind"] == "Harness"]
        environment = [{item["name"]: item["value"] for item in row["spec"]["env"]} for row in harnesses]
        self.assertEqual(environment[0]["PUBLIC_CANARY_INFERENCE"], "true")
        self.assertEqual(environment[1]["PUBLIC_CANARY_INFERENCE"], "false")
        self.assertEqual(json.loads(environment[0]["NATIVE_MEMORY_CONFIG_JSON"])["embedding"]["dimensions"], 768)
        self.assertTrue(all(row["metadata"]["namespace"] == "factory-executions" for row in document["items"]))

    def test_inference_stays_disabled_without_explicit_parent_enablement(self):
        document = resources("10.152.183.131:5000/kagent-lab/native-public-canary@sha256:" + "1" * 64,
            "11111111-2222-4333-8444-555555555555", "gpt-5.6-sol", "kagent-default",
            "gs://ate-snapshots/public-native-canary/")
        for row in document["items"]:
            if row["kind"] == "Harness":
                self.assertEqual(next(item["value"] for item in row["spec"]["env"]
                                      if item["name"] == "PUBLIC_CANARY_INFERENCE"), "false")


if __name__ == "__main__":
    unittest.main()
