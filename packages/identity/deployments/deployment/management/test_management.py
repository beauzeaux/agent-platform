import unittest
from manage import documents, ADK, K8S_TOOLS, PROM_TOOLS, GRAFANA_TOOLS


class NativeManagementTests(unittest.TestCase):
    def test_native_agents_reference_only_the_readonly_tool_surface_and_no_actor_secret(self):
        values = documents()
        harness = next(x for x in values if x["kind"] == "Harness")
        self.assertIn("kagent", harness["spec"])
        self.assertNotIn("byo", harness["spec"])
        self.assertEqual(harness["spec"]["workload"]["image"], ADK)
        self.assertNotIn("env", harness["spec"])
        agents = [x for x in values if x["kind"] == "Agent"]
        self.assertEqual({x["metadata"]["name"] for x in agents},
                         {"cluster-manager", "cluster-observability"})
        self.assertTrue(all(x["metadata"]["namespace"] == "cluster-management" for x in values))
        for template in (x for x in values if x["kind"] == "AgentTemplate"):
            tools = [name for binding in template["spec"]["tools"] for name in binding["mcp"]["tools"]]
            self.assertTrue(set(tools) <= set(K8S_TOOLS + PROM_TOOLS + GRAFANA_TOOLS))
            self.assertFalse(any(word in name for name in tools
                                 for word in ("delete", "apply", "patch", "exec", "scale", "secret")))

    def test_only_observability_inherits_existing_grafana_loki_tools(self):
        values = documents()
        templates = {x["metadata"]["name"]: x["spec"] for x in values if x["kind"] == "AgentTemplate"}
        self.assertEqual(len(templates["cluster-manager"]["tools"]), 1)
        grafana = templates["cluster-observability"]["tools"][1]["mcp"]
        self.assertEqual(set(grafana["tools"]), set(GRAFANA_TOOLS))
        remote = next(x for x in values if x["metadata"]["name"] == "cluster-grafana")
        self.assertEqual(remote["spec"]["headersFrom"][0]["valueFrom"]["name"], "cluster-grafana-mcp-auth")


if __name__ == "__main__":
    unittest.main()
