using Microsoft.VisualStudio.TestTools.UnitTesting;
using System.Text.Json;

[assembly: DoNotParallelize]

namespace AdoDevProxy.Tests;

[TestClass]
public sealed class PolicyTests
{
    internal static ProxySettings Settings(int port = 5480) => new()
    {
        Port = port,
        Identities = [new() { Name = "work", TenantId = "72f988bf-86f1-41af-91ab-2d7cd011db47" }],
        Organizations = [new() { Name = "msasg", Identity = "work" }],
        Feeds = [new()
        {
            Name = "feed", Organization = "msasg", Identity = "work",
            NpmRegistry = "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/",
            NugetIndex = "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/nuget/v3/index.json"
        }],
        DownloadHosts = ["content.example.com"]
    };

    [TestMethod]
    [DataRow("GET", "https://dev.azure.com/msasg/project/_git/repo/info/refs?service=git-upload-pack")]
    [DataRow("POST", "https://dev.azure.com/msasg/project/_git/repo/git-receive-pack")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/@scope%2fname")]
    [DataRow("POST", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/-/npm/v1/security/advisories/bulk")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/nuget/v3/flat2/package/1/package.1.nupkg")]
    public void SupportedOperationsResolveTheirIdentity(string method, string url) =>
        Assert.AreEqual("work", new RequestPolicy(Settings()).Evaluate(method, url)?.Identity);

    [TestMethod]
    [DataRow("GET", "https://dev.azure.com/other/project/_git/repo/info/refs?service=git-upload-pack")]
    [DataRow("GET", "https://dev.azure.com/msasg/_apis/projects")]
    [DataRow("GET", "https://dev.azure.com/msasg/project/_git/repo/info/refs?service=git-upload-pack&service=git-receive-pack")]
    [DataRow("POST", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/nuget/v3/index.json")]
    [DataRow("PUT", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/package")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/other/npm/registry/package")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/../admin")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/%2e%2e/admin")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/foo%2f..%2fadmin")]
    [DataRow("GET", "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/%252e%252e/admin")]
    [DataRow("GET", "https://127.0.0.1/msasg/project/_packaging/feed/npm/registry/package")]
    [DataRow("GET", "https://pkgs.dev.azure.com:444/msasg/project/_packaging/feed/npm/registry/package")]
    public void UnapprovedOrAmbiguousRequestsAreDenied(string method, string url) =>
        Assert.IsNull(new RequestPolicy(Settings()).Evaluate(method, url));

    [TestMethod]
    public void RedirectCapabilitiesAreExactReadOnlyAndNeverAttachAnIdentity()
    {
        var policy = new RequestPolicy(Settings());
        var target = new Uri("https://content.example.com/package?sig=test");
        Assert.IsFalse(policy.CanConnect(new("https://content.example.com")));
        Assert.IsFalse(policy.AllowRedirect(new("work", "nuget-read"), new("https://unknown.example.com/package")));
        Assert.IsTrue(policy.AllowRedirect(new("work", "nuget-read"), target));
        Assert.IsTrue(policy.AllowRedirect(new("work", "nuget-read"), target));
        Assert.IsTrue(policy.CanConnect(new("https://content.example.com")));
        Assert.AreEqual("download", policy.Evaluate("GET", target.AbsoluteUri)?.Operation);
        Assert.IsNull(policy.Evaluate("GET", target.AbsoluteUri)?.Identity);
        Assert.IsNull(policy.Evaluate("POST", target.AbsoluteUri));
        Assert.IsNull(policy.Evaluate("GET", target.AbsoluteUri + "other"));
        Assert.IsFalse(policy.CanConnect(new("https://content.example.com/path")));
    }

    [TestMethod]
    public void NugetDiscoveryCannotAuthorizeAnotherFeed()
    {
        var policy = new RequestPolicy(Settings());
        using var index = JsonDocument.Parse("""
            {"resources":[
              {"@type":"VssFeedId","@id":"urn:uuid:11111111-1111-1111-1111-111111111111"},
              {"@type":"AzureDevOpsProjectId","@id":"urn:uuid:22222222-2222-2222-2222-222222222222"},
              {"@type":"PackageBaseAddress/3.0.0","@id":"https://pkgs.dev.azure.com/msasg/22222222-2222-2222-2222-222222222222/_packaging/11111111-1111-1111-1111-111111111111/nuget/v3/flat2/"}
            ]}
            """);
        policy.AddNugetResources(Settings().Feeds[0], index.RootElement);
        Assert.IsNotNull(policy.Evaluate("GET",
            "https://pkgs.dev.azure.com/msasg/22222222-2222-2222-2222-222222222222/_packaging/11111111-1111-1111-1111-111111111111/nuget/v3/flat2/package/index.json"));
        using var invalid = JsonDocument.Parse(index.RootElement.GetRawText().Replace(
            "_packaging/11111111-1111-1111-1111-111111111111", "_packaging/33333333-3333-3333-3333-333333333333"));
        Assert.ThrowsExactly<InvalidOperationException>(() => policy.AddNugetResources(Settings().Feeds[0], invalid.RootElement));
    }

    [TestMethod]
    public void MultipleOrganizationsUseExplicitIdentities()
    {
        var settings = new ProxySettings
        {
            Identities = [new() { Name = "first", TenantId = Guid.NewGuid().ToString() },
                new() { Name = "second", TenantId = Guid.NewGuid().ToString() }],
            Organizations = [new() { Name = "one", Identity = "first" }, new() { Name = "two", Identity = "second" }]
        };
        settings.Validate();
        var policy = new RequestPolicy(settings);
        Assert.AreEqual("first", policy.Evaluate("POST", "https://dev.azure.com/one/p/_git/r/git-upload-pack")?.Identity);
        Assert.AreEqual("second", policy.Evaluate("POST", "https://dev.azure.com/two/p/_git/r/git-upload-pack")?.Identity);
    }
}
