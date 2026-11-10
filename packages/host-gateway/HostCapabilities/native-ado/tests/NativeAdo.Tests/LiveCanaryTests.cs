using System.Text.Json.Nodes;
using HostCapabilities.NativeAdo.Canary;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using YamlDotNet.RepresentationModel;

namespace NativeAdo.Tests;

[TestClass]
public sealed class LiveCanaryTests
{
    [TestMethod]
    public void GuardedPatchUsesNewConfigMapPreservesProxyRootsAndRollsBackExactly()
    {
        var snapshot = Snapshot();
        var original = snapshot["deployment"]!.DeepClone();
        var artifacts = LiveCanaryRenderer.Prepare(snapshot.ToJsonString());
        var forward = JsonNode.Parse(artifacts["deployment.forward.patch.json"])!.AsArray();
        var rollback = JsonNode.Parse(artifacts["deployment.rollback.patch.json"])!.AsArray();
        var deployment = snapshot["deployment"]!.DeepClone();
        Apply(deployment, forward);
        var pod = deployment["spec"]!["template"]!["spec"]!;
        Assert.AreEqual(LiveCanaryRenderer.NewConfigMapName, pod["volumes"]![0]!["configMap"]!["name"]!.GetValue<string>());
        Assert.AreEqual("agent-proxies-egress-roots", pod["volumes"]![1]!["configMap"]!["name"]!.GetValue<string>());
        var newConfigMap = JsonNode.Parse(artifacts["provider-configmap.json"])!;
        var yaml = newConfigMap["data"]!["config.yaml"]!.GetValue<string>();
        Assert.IsTrue(yaml.Contains("/run/proxy-access-ca/ca-bundle.pem", StringComparison.Ordinal));
        Assert.IsTrue(yaml.Contains(LiveCanaryRenderer.ProviderEndpoint, StringComparison.Ordinal));
        Apply(deployment, rollback);
        Assert.IsTrue(JsonNode.DeepEquals(original, deployment));
        Assert.IsTrue(JsonNode.DeepEquals(original, snapshot["deployment"]));
        Assert.AreEqual("atenet-egress-agentgateway-config", snapshot["configMap"]!["metadata"]!["name"]!.GetValue<string>());
    }

    [TestMethod]
    public void ForwardAndRollbackRejectConcurrentDeploymentDrift()
    {
        var snapshot = Snapshot();
        var artifacts = LiveCanaryRenderer.Prepare(snapshot.ToJsonString());
        var forward = JsonNode.Parse(artifacts["deployment.forward.patch.json"])!.AsArray();
        var rollback = JsonNode.Parse(artifacts["deployment.rollback.patch.json"])!.AsArray();
        var changed = snapshot["deployment"]!.DeepClone();
        changed["metadata"]!["resourceVersion"] = "18";
        Assert.ThrowsException<InvalidOperationException>(() => Apply(changed, forward));
        var deployment = snapshot["deployment"]!.DeepClone();
        Apply(deployment, forward);
        deployment["spec"]!["template"]!["spec"]!["volumes"]!.AsArray().Add(new JsonObject { ["name"] = "another-owner" });
        Assert.ThrowsException<InvalidOperationException>(() => Apply(deployment, rollback));
    }

    private static JsonObject Snapshot()
    {
        var stream = new YamlStream();
        stream.Load(new StringReader(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "helm-egress.yaml"))));
        var cm = stream.Documents.Select(d => (YamlMappingNode)d.RootNode)
            .Single(d => ((YamlScalarNode)d.Children[new YamlScalarNode("kind")]).Value == "ConfigMap");
        var data = (YamlMappingNode)cm.Children[new YamlScalarNode("data")];
        var yaml = ((YamlScalarNode)data.Children[new YamlScalarNode("config.yaml")]).Value!
            .Replace("/etc/ssl/certs/ca-certificates.crt", "/run/proxy-access-ca/ca-bundle.pem", StringComparison.Ordinal);
        return new JsonObject
        {
            ["configMap"] = new JsonObject
            {
                ["apiVersion"] = "v1",
                ["kind"] = "ConfigMap",
                ["metadata"] = new JsonObject
                {
                    ["name"] = "atenet-egress-agentgateway-config",
                    ["namespace"] = "ate-system",
                    ["uid"] = "test-config-uid",
                    ["resourceVersion"] = "11"
                },
                ["data"] = new JsonObject { ["config.yaml"] = yaml }
            },
            ["deployment"] = JsonNode.Parse("""
                {
                  "apiVersion": "apps/v1", "kind": "Deployment",
                  "metadata": {"name": "atenet-egress", "namespace": "ate-system", "uid": "test-deployment-uid", "resourceVersion": "17"},
                  "spec": {"template": {"spec": {
                    "serviceAccountName": "atenet-egress",
                    "containers": [{
                      "name": "agentgateway",
                      "image": "cr.agentgateway.dev/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b",
                      "volumeMounts": [
                        {"name": "config", "mountPath": "/etc/agentgateway", "readOnly": true},
                        {"name": "proxy-access-ca", "mountPath": "/run/proxy-access-ca", "readOnly": true}
                      ]
                    }],
                    "volumes": [
                      {"name": "config", "configMap": {"name": "atenet-egress-agentgateway-config"}},
                      {"name": "proxy-access-ca", "configMap": {"name": "agent-proxies-egress-roots"}}
                    ]
                  }}}
                }
                """)
        };
    }

    private static void Apply(JsonNode document, JsonArray operations)
    {
        foreach (var operation in operations)
        {
            var parts = operation!["path"]!.GetValue<string>().Split('/', StringSplitOptions.RemoveEmptyEntries);
            var parent = document;
            foreach (var part in parts[..^1])
            {
                parent = parent is JsonArray array ? array[int.Parse(part)]! : parent[part]!;
            }
            var key = parts[^1];
            var value = operation["value"]!;
            if (operation["op"]!.GetValue<string>() == "test")
            {
                var existing = parent is JsonArray array ? array[int.Parse(key)] : parent[key];
                if (!JsonNode.DeepEquals(existing, value))
                {
                    throw new InvalidOperationException("Patch precondition failed.");
                }
            }
            else if (parent is JsonArray array)
            {
                if (key == "-") { array.Add(value.DeepClone()); }
                else { array[int.Parse(key)] = value.DeepClone(); }
            }
            else
            {
                parent[key] = value.DeepClone();
            }
        }
    }
}
