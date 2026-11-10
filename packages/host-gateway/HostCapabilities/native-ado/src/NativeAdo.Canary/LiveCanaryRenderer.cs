using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using YamlDotNet.RepresentationModel;

namespace HostCapabilities.NativeAdo.Canary;

public static class LiveCanaryRenderer
{
    public const string ProviderEndpoint = "windows-wam.proxy-access.svc.cluster.local:5443";
    public const string NewConfigMapName = "atenet-egress-agentgateway-config-windows-wam-canary";
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public static IReadOnlyDictionary<string, string> Prepare(string snapshotJson)
    {
        Require(snapshotJson.Length <= 8 * 1024 * 1024);
        var snapshot = JsonNode.Parse(snapshotJson)!.AsObject();
        var configMap = snapshot["configMap"]!.AsObject();
        var deployment = snapshot["deployment"]!.AsObject();
        Require(configMap["metadata"]!["name"]!.GetValue<string>() == "atenet-egress-agentgateway-config");
        Require(deployment["spec"]!["template"]!["spec"]!["serviceAccountName"]!.GetValue<string>() == "atenet-egress");
        var options = new CanaryPatchOptions("atenet-egress-agentgateway-config", "atenet-egress", "ate-system",
            ProviderEndpoint, "windows-wam-provider-ca");
        var rendered = RenderedManifestPatcher.Patch(configMap.ToJsonString() + "\n---\n" + deployment.ToJsonString(), options);
        var stream = new YamlStream();
        stream.Load(new StringReader(rendered));
        var changedConfigMap = stream.Documents.Select(d => (YamlMappingNode)d.RootNode)
            .Single(d => Scalar(d, "kind") == "ConfigMap");
        var changedDeployment = stream.Documents.Select(d => (YamlMappingNode)d.RootNode)
            .Single(d => Scalar(d, "kind") == "Deployment");
        var changedPod = Map(Map(Map(changedDeployment, "spec"), "template"), "spec");
        var oldPod = deployment["spec"]!["template"]!["spec"]!;
        var oldVolumes = oldPod["volumes"]!.AsArray();
        var oldContainers = oldPod["containers"]!.AsArray();
        var containerIndex = oldContainers.Select((c, i) => (c, i)).Single(x => x.c!["name"]!.GetValue<string>() == "agentgateway").i;
        var volumeIndex = oldVolumes.Select((v, i) => (v, i)).Single(x =>
            x.v!["name"]!.GetValue<string>() == "config" &&
            x.v["configMap"]!["name"]!.GetValue<string>() == "atenet-egress-agentgateway-config").i;
        var volumePath = $"/spec/template/spec/volumes/{volumeIndex}/configMap/name";
        var mountPath = $"/spec/template/spec/containers/{containerIndex}/volumeMounts";
        var addedVolume = new JsonObject
        {
            ["name"] = "windows-wam-provider-ca",
            ["configMap"] = new JsonObject
            {
                ["name"] = "windows-wam-provider-ca",
                ["items"] = new JsonArray(new JsonObject { ["key"] = "ca.crt", ["path"] = "ca.crt" })
            }
        };
        var addedMount = new JsonObject
        {
            ["name"] = "windows-wam-provider-ca",
            ["mountPath"] = "/run/windows-wam-provider-ca",
            ["readOnly"] = true
        };
        var uid = deployment["metadata"]!["uid"]!.GetValue<string>();
        var resourceVersion = deployment["metadata"]!["resourceVersion"]!.GetValue<string>();
        var patchedVolumes = oldVolumes.DeepClone().AsArray();
        patchedVolumes[volumeIndex]!["configMap"]!["name"] = NewConfigMapName;
        patchedVolumes.Add(addedVolume.DeepClone());
        var oldMounts = oldContainers[containerIndex]!["volumeMounts"]!.AsArray();
        var patchedMounts = oldMounts.DeepClone().AsArray();
        patchedMounts.Add(addedMount.DeepClone());
        Require(!oldVolumes.Any(v => v!["name"]!.GetValue<string>() == "windows-wam-provider-ca"));
        Require(Sequence(changedPod, "volumes").Children.Count == patchedVolumes.Count);

        var forward = new JsonArray(
            Op("test", "/metadata/uid", JsonValue.Create(uid)!),
            Op("test", "/metadata/resourceVersion", JsonValue.Create(resourceVersion)!),
            Op("test", volumePath, JsonValue.Create("atenet-egress-agentgateway-config")!),
            Op("add", "/spec/template/spec/volumes/-", addedVolume),
            Op("add", mountPath + "/-", addedMount),
            Op("replace", volumePath, JsonValue.Create(NewConfigMapName)!));
        var rollback = new JsonArray(
            Op("test", "/metadata/uid", JsonValue.Create(uid)!),
            Op("test", "/spec/template/spec/volumes", patchedVolumes),
            Op("test", mountPath, patchedMounts),
            Op("replace", "/spec/template/spec/volumes", oldVolumes.DeepClone()),
            Op("replace", mountPath, oldMounts.DeepClone()));
        var data = Map(changedConfigMap, "data");
        var newConfigMap = new JsonObject
        {
            ["apiVersion"] = "v1",
            ["kind"] = "ConfigMap",
            ["metadata"] = new JsonObject
            {
                ["name"] = NewConfigMapName,
                ["namespace"] = "ate-system",
                ["annotations"] = new JsonObject
                {
                    ["hostcapabilities/source-configmap-uid"] = configMap["metadata"]!["uid"]!.DeepClone(),
                    ["hostcapabilities/source-configmap-resource-version"] = configMap["metadata"]!["resourceVersion"]!.DeepClone(),
                    ["hostcapabilities/source-config-sha256"] = Convert.ToHexString(SHA256.HashData(
                        Encoding.UTF8.GetBytes(configMap["data"]!["config.yaml"]!.GetValue<string>())))
                }
            },
            ["data"] = new JsonObject(data.Children.Select(pair =>
                new KeyValuePair<string, JsonNode?>(((YamlScalarNode)pair.Key).Value!, JsonValue.Create(((YamlScalarNode)pair.Value).Value))))
        };
        return new SortedDictionary<string, string>(StringComparer.Ordinal)
        {
            ["deployment.forward.patch.json"] = forward.ToJsonString(JsonOptions),
            ["deployment.rollback.patch.json"] = rollback.ToJsonString(JsonOptions),
            ["provider-configmap.json"] = newConfigMap.ToJsonString(JsonOptions)
        };
    }

    private static JsonObject Op(string operation, string path, JsonNode value) =>
        new() { ["op"] = operation, ["path"] = path, ["value"] = value };

    private static YamlMappingNode Map(YamlMappingNode node, string key) => (YamlMappingNode)node.Children[new YamlScalarNode(key)];
    private static YamlSequenceNode Sequence(YamlMappingNode node, string key) => (YamlSequenceNode)node.Children[new YamlScalarNode(key)];
    private static string Scalar(YamlMappingNode node, string key) => ((YamlScalarNode)node.Children[new YamlScalarNode(key)]).Value!;

    private static void Require(bool condition)
    {
        if (!condition)
        {
            throw new InvalidOperationException("Unsupported active gateway snapshot.");
        }
    }
}
