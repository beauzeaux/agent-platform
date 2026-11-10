import unittest

from pilot import documents, GRAFANA_TOOLS, KUBERNETES_TOOLS


class PilotObservabilityTests(unittest.TestCase):
    def test_pilot_reuses_native_harness_and_enforced_readonly_official_tools(self):
        values = documents()
        harness = next(x for x in values if x["kind"] == "Harness")
        self.assertIn("kagent", harness["spec"])
        self.assertNotIn("byo", harness["spec"])
        self.assertNotIn("env", harness["spec"])
        template = next(x for x in values if x["kind"] == "AgentTemplate")
        selected = {name for tool in template["spec"]["tools"] for name in tool["mcp"]["tools"]}
        self.assertEqual(selected, set(GRAFANA_TOOLS + KUBERNETES_TOOLS))
        self.assertFalse(any(word in name for name in selected
                             for word in ("write", "update", "create", "delete", "exec", "patch")))
        workload = next(x for x in values if x["kind"] == "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(workload["automountServiceAccountToken"])
        server = workload["containers"][0]
        self.assertIn("--disable-write", server["args"])
        self.assertIn("--usage-stats=disabled", server["args"])
        self.assertNotIn("--enable-query", server["args"])
        self.assertTrue(server["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(any(x["name"] in ("GRAFANA_PASSWORD", "GRAFANA_SERVICE_ACCOUNT_TOKEN")
                             for x in server["env"]))

    def test_mcp_auth_stays_in_native_egress_and_network_access_is_scoped(self):
        values = documents()
        remote = next(x for x in values if x["kind"] == "RemoteMCPServer"
                      and x["metadata"]["name"] == "pilot-grafana")
        header = remote["spec"]["headersFrom"][0]
        self.assertEqual(header["name"], "Authorization")
        self.assertEqual(header["valueFrom"]["type"], "Secret")
        policy = next(x for x in values if x["kind"] == "NetworkPolicy"
                      and x["metadata"]["namespace"] == "kagent")["spec"]
        self.assertEqual(set(policy["policyTypes"]), {"Ingress", "Egress"})
        self.assertTrue(all("namespaceSelector" in peer and "podSelector" in peer
                            for rule in policy["ingress"] for peer in rule["from"]))
        destinations = [peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                        for rule in policy["egress"] for peer in rule["to"]]
        self.assertEqual(set(destinations), {"kube-system", "observability"})
        self.assertTrue(all(x["metadata"]["namespace"] in ("kagent", "cluster-management")
                            for x in values))


if __name__ == "__main__":
    unittest.main()
