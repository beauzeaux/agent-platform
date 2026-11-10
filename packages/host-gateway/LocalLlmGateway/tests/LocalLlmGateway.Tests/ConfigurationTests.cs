using System.CommandLine;

namespace LocalLlmGateway.Tests;

public sealed class ConfigurationTests
{
    private static (RootCommand Root, GatewayConfiguration Config) Create()
    {
        var root = new RootCommand();
        var config = new GatewayConfiguration();
        config.AddTo(root);
        root.Subcommands.Add(new Command("serve"));
        return (root, config);
    }

    [Fact]
    public void DefaultsAreLoopbackAndFourRequests()
    {
        var (root, config) = Create();
        var options = config.Load(root.Parse("serve"), _ => null, Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".json"));
        Assert.Equal("127.0.0.1", options.Host);
        Assert.Equal(4141, options.Port);
        Assert.Equal(4, options.MaxConcurrentRequests);
    }

    [Fact]
    public void CliOverridesEnvironmentWhichOverridesJson()
    {
        var path = Path.GetTempFileName();
        try
        {
            File.WriteAllText(path, """{"Port":1234,"MaxConcurrentRequests":2,"Host":"localhost"}""");
            var (root, config) = Create();
            var options = config.Load(root.Parse($"serve --config \"{path}\" --port 4321"),
                name => name switch
                {
                    "LOCAL_LLM_GATEWAY_PORT" => "2345",
                    "LOCAL_LLM_GATEWAY_MAX_CONCURRENT_REQUESTS" => "3",
                    _ => null
                });
            Assert.Equal(4321, options.Port);
            Assert.Equal(3, options.MaxConcurrentRequests);
            Assert.Equal("localhost", options.Host);
        }
        finally { File.Delete(path); }
    }

    [Theory]
    [InlineData("--port 0")]
    [InlineData("--port 70000")]
    [InlineData("--port not-a-number")]
    [InlineData("--host https://example.com")]
    [InlineData("--account-type other")]
    [InlineData("--max-concurrent-requests 0")]
    [InlineData("--request-timeout-seconds -1")]
    [InlineData("--request-timeout-seconds 4294968")]
    [InlineData("--max-request-bytes 0")]
    [InlineData("--max-request-bytes 2147483648")]
    public void RejectsInvalidSettings(string arguments)
    {
        var (root, config) = Create();
        Assert.Throws<GatewayException>(() => config.Load(root.Parse("serve " + arguments), _ => null));
    }

    [Fact]
    public void MissingExplicitConfigIsAnError()
    {
        var (root, config) = Create();
        Assert.Throws<GatewayException>(() => config.Load(root.Parse($"serve --config {Guid.NewGuid()}.json"), _ => null));
    }

    [Fact]
    public void UnknownCliOptionsAreRejected()
    {
        Assert.NotEmpty(GatewayCommands.Create().Parse("serve --gateway-key secret").Errors);
    }

    [Fact]
    public async Task AmbiguousOrMissingKeysAreRejected()
    {
        await Assert.ThrowsAsync<GatewayException>(() => GatewayConfiguration.LoadGatewayKeyAsync(
            new GatewayOptions(), default, _ => null));
        await Assert.ThrowsAsync<GatewayException>(() => GatewayConfiguration.LoadGatewayKeyAsync(
            new GatewayOptions { GatewayKeyFile = "key" }, default, _ => new string('a', 32)));
    }

    [Fact]
    public async Task ReadsGatewayKeyFromFileOrEnvironmentWithoutRewritingIt()
    {
        using var directory = new TemporaryDirectory();
        var path = Path.Combine(directory.Path, "gateway-key");
        var key = new string('x', 40);
        await File.WriteAllTextAsync(path, key + "\n");
        Assert.Equal(key, await GatewayConfiguration.LoadGatewayKeyAsync(
            new GatewayOptions { GatewayKeyFile = path }, default, _ => null));
        Assert.Equal(key, await GatewayConfiguration.LoadGatewayKeyAsync(new GatewayOptions(), default, _ => key));
    }
}
