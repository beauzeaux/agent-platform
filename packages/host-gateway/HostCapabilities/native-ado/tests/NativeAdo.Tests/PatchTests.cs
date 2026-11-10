using HostCapabilities.NativeAdo.Canary;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using YamlDotNet.RepresentationModel;

namespace NativeAdo.Tests;

[TestClass]
public sealed class PatchTests
{
    private static readonly CanaryPatchOptions Options = new(
        "native-ado-canary-atenet-egress-agentgateway-config", "native-ado-canary-atenet-egress", "native-ado-canary",
        "windows-wam.canary.invalid:5443", "windows-wam-provider-ca");

    [TestMethod]
    public void AddsBothRoutesAndPrivateRootMountWithoutChangingExistingProvidersOrPolicies()
    {
        var original = Fixture();
        var patched = RenderedManifestPatcher.Patch(original, Options);
        var originalDocuments = Parse(original);
        var patchedDocuments = Parse(patched);
        Assert.AreEqual(originalDocuments.Documents.Count, patchedDocuments.Documents.Count);
        var before = Config(originalDocuments);
        var after = Config(patchedDocuments);
        var oldListeners = Listeners(before);
        var newListeners = Listeners(after);
        foreach (var protocol in new[] { "HTTP", "HTTPS" })
        {
            var oldRoute = oldListeners.Single(l => Text(l, "protocol") == protocol);
            var newRoute = newListeners.Single(l => Text(l, "protocol") == protocol);
            var providers = Providers(newRoute);
            Assert.AreEqual(2, providers.Children.Count);
            Assert.AreEqual(Providers(oldRoute).Children[0], providers.Children[0]);
            var native = (YamlMappingNode)providers.Children[1];
            Assert.AreEqual("windows-wam", Text(native, "uriAuthority"));
            var tls = Map(Map(Map(native, "target"), "policies"), "backendTLS");
            Assert.AreEqual("windows-wam.canary.invalid", Text(tls, "hostname"));
            Assert.AreEqual("/run/podidentity.podcert.ate.dev/credential-bundle.pem", Text(tls, "cert"));
            Assert.AreEqual("/run/windows-wam-provider-ca/ca.crt", Text(tls, "root"));
            providers.Children.RemoveAt(1);
        }
        Assert.AreEqual(before, after);

        var beforeDeployment = Document(originalDocuments, "Deployment");
        var afterDeployment = Document(patchedDocuments, "Deployment");
        var pod = Map(Map(Map(afterDeployment, "spec"), "template"), "spec");
        var volumes = Seq(pod, "volumes");
        Assert.AreEqual("windows-wam-provider-ca", Text((YamlMappingNode)volumes.Children[^1], "name"));
        var mounts = Seq((YamlMappingNode)Seq(pod, "containers").Children[0], "volumeMounts");
        Assert.AreEqual("/run/windows-wam-provider-ca", Text((YamlMappingNode)mounts.Children[^1], "mountPath"));
        mounts.Children.RemoveAt(mounts.Children.Count - 1);
        volumes.Children.RemoveAt(volumes.Children.Count - 1);
        Assert.AreEqual(beforeDeployment, afterDeployment);
        foreach (var kind in new[] { "Service", "ServiceAccount" })
        {
            Assert.AreEqual(Document(originalDocuments, kind), Document(patchedDocuments, kind));
        }
    }

    [TestMethod]
    public void IsIdempotentAndRejectsMissingRouteConflictingProviderOrUnexpectedImage()
    {
        var patched = RenderedManifestPatcher.Patch(Fixture(), Options);
        Assert.AreEqual(patched, RenderedManifestPatcher.Patch(patched, Options));
        foreach (var input in new[]
        {
            Fixture().Replace("protocol: HTTP\n", "protocol: TCP\n", StringComparison.Ordinal),
            Fixture().Replace("uriAuthority: k8s.io", "uriAuthority: other", StringComparison.Ordinal),
            Fixture().Replace("agentgateway:v1.7.0@", "agentgateway:v2.0.0@", StringComparison.Ordinal),
            patched.Replace("windows-wam.canary.invalid:5443", "other.invalid:5443", StringComparison.Ordinal)
        })
        {
            Assert.ThrowsException<InvalidOperationException>(() => RenderedManifestPatcher.Patch(input, Options));
        }
        Assert.ThrowsException<InvalidOperationException>(() =>
            RenderedManifestPatcher.Patch(Fixture(), Options with { Endpoint = "http://host:80" }));
        Assert.ThrowsException<InvalidOperationException>(() =>
            RenderedManifestPatcher.Patch(Fixture(), Options with { ConfigMapName = "missing" }));
    }

    private static string Fixture() => File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "helm-egress.yaml"));
    private static YamlStream Parse(string yaml)
    {
        var stream = new YamlStream();
        stream.Load(new StringReader(yaml));
        return stream;
    }
    private static YamlMappingNode Document(YamlStream stream, string kind) =>
        stream.Documents.Select(d => (YamlMappingNode)d.RootNode).Single(d => Text(d, "kind") == kind);
    private static YamlMappingNode Config(YamlStream stream) =>
        (YamlMappingNode)Parse(Text(Map(Document(stream, "ConfigMap"), "data"), "config.yaml")).Documents[0].RootNode;
    private static YamlMappingNode Map(YamlMappingNode node, string key) => (YamlMappingNode)node.Children[new YamlScalarNode(key)];
    private static YamlSequenceNode Seq(YamlMappingNode node, string key) => (YamlSequenceNode)node.Children[new YamlScalarNode(key)];
    private static string Text(YamlMappingNode node, string key) => ((YamlScalarNode)node.Children[new YamlScalarNode(key)]).Value!;
    private static YamlMappingNode[] Listeners(YamlMappingNode config) =>
        Seq((YamlMappingNode)Seq(config, "binds").Children[0], "listeners").Children.Cast<YamlMappingNode>().ToArray();
    private static YamlSequenceNode Providers(YamlMappingNode listener) =>
        Seq(Map(Map((YamlMappingNode)Seq(listener, "routes").Children[0], "policies"), "substrateEgress"), "credentialProviders");
}
