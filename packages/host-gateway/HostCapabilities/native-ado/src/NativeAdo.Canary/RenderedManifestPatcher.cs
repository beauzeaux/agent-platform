using System.Globalization;
using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace HostCapabilities.NativeAdo.Canary;

public sealed record CanaryPatchOptions(
    string ConfigMapName, string DeploymentName, string Namespace, string Endpoint, string RootConfigMapName);

public static partial class RenderedManifestPatcher
{
    private const string VolumeName = "windows-wam-provider-ca";
    private const string RootPath = "/run/windows-wam-provider-ca/ca.crt";
    private const string ClientBundle = "/run/podidentity.podcert.ate.dev/credential-bundle.pem";

    public static string Patch(string renderedManifest, CanaryPatchOptions options)
    {
        var host = Validate(options);
        var manifest = Load(renderedManifest);
        var configMap = Find(manifest, "ConfigMap", options.ConfigMapName, options.Namespace);
        var data = Map(configMap, "data");
        var config = Load(Text(data, "config.yaml"));
        Require(config.Documents.Count == 1);
        var configRoot = (YamlMappingNode)config.Documents[0].RootNode;
        var bind = Sequence(configRoot, "binds").Children.OfType<YamlMappingNode>()
            .Single(b => Text(b, "mode") == "internal" && Text(b, "protocol") == "AUTO");
        var listeners = Sequence(bind, "listeners").Children.Cast<YamlMappingNode>().ToArray();
        foreach (var protocol in new[] { "HTTP", "HTTPS" })
        {
            var listener = listeners.Single(l => Text(l, "protocol") == protocol);
            var routes = Sequence(listener, "routes");
            Require(routes.Children.Count == 1);
            var policy = Map(Map((YamlMappingNode)routes.Children[0], "policies"), "substrateEgress");
            var providers = Sequence(policy, "credentialProviders");
            var entries = providers.Children.Cast<YamlMappingNode>().ToArray();
            Require(entries.Select(p => Text(p, "uriAuthority")).Distinct(StringComparer.Ordinal).Count() == entries.Length);
            Require(entries.Count(p => Text(p, "uriAuthority") == "k8s.io") == 1);
            var provider = new YamlMappingNode
            {
                { "uriAuthority", "windows-wam" },
                { "target", new YamlMappingNode
                    {
                        { "host", options.Endpoint },
                        { "policies", new YamlMappingNode
                            {
                                { "backendTLS", new YamlMappingNode
                                    {
                                        { "hostname", host },
                                        { "cert", ClientBundle },
                                        { "key", ClientBundle },
                                        { "root", RootPath }
                                    }
                                }
                            }
                        }
                    }
                }
            };
            AddExact(providers, "uriAuthority", "windows-wam", provider);
        }

        var deployment = Find(manifest, "Deployment", options.DeploymentName, options.Namespace);
        var pod = Map(Map(Map(deployment, "spec"), "template"), "spec");
        var container = Sequence(pod, "containers").Children.Cast<YamlMappingNode>()
            .Single(c => Text(c, "name") == "agentgateway");
        Require(Text(container, "image") ==
            "cr.agentgateway.dev/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b");
        Require(!Sequence(container, "volumeMounts").Children.Cast<YamlMappingNode>().Any(m =>
            Text(m, "mountPath") == "/run/windows-wam-provider-ca" && Text(m, "name") != VolumeName));
        AddExact(Sequence(container, "volumeMounts"), "name", VolumeName, new YamlMappingNode
        {
            { "name", VolumeName },
            { "mountPath", "/run/windows-wam-provider-ca" },
            { "readOnly", "true" }
        });
        AddExact(Sequence(pod, "volumes"), "name", VolumeName, new YamlMappingNode
        {
            { "name", VolumeName },
            { "configMap", new YamlMappingNode
                {
                    { "name", options.RootConfigMapName },
                    { "items", new YamlSequenceNode(new YamlMappingNode { { "key", "ca.crt" }, { "path", "ca.crt" } }) }
                }
            }
        });
        data.Children[new YamlScalarNode("config.yaml")] = new YamlScalarNode(Save(config))
        {
            Style = YamlDotNet.Core.ScalarStyle.Literal
        };
        return Save(manifest);
    }

    private static string Validate(CanaryPatchOptions options)
    {
        foreach (var name in new[] { options.ConfigMapName, options.DeploymentName, options.Namespace, options.RootConfigMapName })
        {
            Require(name.Length <= 253 && DnsName().IsMatch(name));
        }
        var parts = options.Endpoint.Split(':');
        Require(parts.Length == 2 && parts[0].Length <= 253 && DnsName().IsMatch(parts[0]) &&
            ushort.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out var port) && port > 0);
        return parts[0];
    }

    private static void AddExact(YamlSequenceNode sequence, string key, string value, YamlMappingNode addition)
    {
        var existing = sequence.Children.Cast<YamlMappingNode>().Where(n => Text(n, key) == value).ToArray();
        Require(existing.Length <= 1);
        if (existing.Length == 1)
        {
            Require(existing[0].Equals(addition));
        }
        else
        {
            sequence.Add(addition);
        }
    }

    private static YamlMappingNode Find(YamlStream stream, string kind, string name, string ns) =>
        stream.Documents.Select(d => d.RootNode).OfType<YamlMappingNode>()
            .Single(m => Text(m, "kind") == kind && Text(Map(m, "metadata"), "name") == name &&
                Text(Map(m, "metadata"), "namespace") == ns);

    private static YamlMappingNode Map(YamlMappingNode node, string key) => (YamlMappingNode)node.Children[new YamlScalarNode(key)];
    private static YamlSequenceNode Sequence(YamlMappingNode node, string key) => (YamlSequenceNode)node.Children[new YamlScalarNode(key)];
    private static string Text(YamlMappingNode node, string key) => ((YamlScalarNode)node.Children[new YamlScalarNode(key)]).Value ?? "";

    private static YamlStream Load(string yaml)
    {
        Require(yaml.Length <= 8 * 1024 * 1024);
        var stream = new YamlStream();
        stream.Load(new StringReader(yaml));
        return stream;
    }

    private static string Save(YamlStream stream)
    {
        using var writer = new StringWriter(CultureInfo.InvariantCulture);
        stream.Save(writer, false);
        return writer.ToString();
    }

    private static void Require(bool condition)
    {
        if (!condition)
        {
            throw new InvalidOperationException("Unsupported canary manifest.");
        }
    }

    [GeneratedRegex(@"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")]
    private static partial Regex DnsName();
}
