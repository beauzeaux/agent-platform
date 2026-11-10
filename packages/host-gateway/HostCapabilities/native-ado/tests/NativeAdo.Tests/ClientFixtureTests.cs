using System.Diagnostics;
using System.Xml.Linq;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace NativeAdo.Tests;

[TestClass]
public sealed class ClientFixtureTests
{
    [TestMethod]
    public async Task GitScopesInertHeaderToApprovedCanonicalOrgAndDisablesRedirectFollowing()
    {
        var approved = await Git("http.extraHeader", "https://dev.azure.com/msasg/project/_git/repo");
        Assert.AreEqual(0, approved.Exit);
        Assert.AreEqual("Authorization: native-ado-placeholder", approved.Output.Trim());
        var otherOrg = await Git("http.extraHeader", "https://dev.azure.com/other/project/_git/repo");
        Assert.AreEqual(1, otherOrg.Exit);
        Assert.AreEqual("", otherOrg.Output);
        var redirects = await Git("http.followRedirects", "https://dev.azure.com/msasg/project/_git/repo");
        Assert.AreEqual("false", redirects.Output.Trim());
    }

    [TestMethod]
    public void PackageFixturesHaveOnlyPrivateFeedAndInertChallengeCredentials()
    {
        var config = XDocument.Load(Path.Combine(Clients, "NuGet.Config"));
        var sources = config.Root!.Element("packageSources")!;
        Assert.AreEqual("clear", sources.Elements().First().Name.LocalName);
        Assert.AreEqual(1, sources.Elements("add").Count());
        var uri = new Uri(sources.Element("add")!.Attribute("value")!.Value);
        Assert.AreEqual("pkgs.dev.azure.com", uri.Host);
        Assert.IsTrue(uri.AbsolutePath.StartsWith("/msasg/", StringComparison.Ordinal));
        var credentials = config.Root.Element("packageSourceCredentials")!.Element("Canary1ES")!;
        Assert.AreEqual("native-ado-placeholder", credentials.Elements("add").Single(
            e => e.Attribute("key")!.Value == "ClearTextPassword").Attribute("value")!.Value);
        var npm = File.ReadAllLines(Path.Combine(Clients, ".npmrc"));
        Assert.AreEqual(3, npm.Length);
        foreach (var line in npm)
        {
            Assert.IsTrue(line.Contains("pkgs.dev.azure.com/msasg/", StringComparison.Ordinal));
        }
        Assert.IsTrue(npm.Skip(1).All(line => line.EndsWith(":_authToken=native-ado-placeholder", StringComparison.Ordinal)));
    }

    private static string Clients => Path.Combine(AppContext.BaseDirectory, "Fixtures", "clients");

    private static async Task<(int Exit, string Output)> Git(string setting, string url)
    {
        var start = new ProcessStartInfo("git") { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var argument in new[] { "config", "--file", Path.Combine(Clients, "git.config"), "--get-urlmatch", setting, url })
        {
            start.ArgumentList.Add(argument);
        }
        using var process = Process.Start(start)!;
        var output = await process.StandardOutput.ReadToEndAsync();
        await process.WaitForExitAsync();
        return (process.ExitCode, output);
    }
}
