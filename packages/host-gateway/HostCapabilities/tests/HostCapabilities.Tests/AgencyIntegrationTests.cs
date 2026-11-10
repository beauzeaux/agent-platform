using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class AgencyIntegrationTests
{
    [TestMethod]
    public async Task McpTimeoutCancelsOnlyItsAcceptedRequestWithoutReplayingTheTool()
    {
        var toolCalls = 0;
        var cancellations = 0;
        await using var fixture = await AgencyFixture.StartAsync(async (request, cancellationToken) =>
        {
            using var document = JsonDocument.Parse(await request.Content!.ReadAsByteArrayAsync(cancellationToken));
            if (document.RootElement.GetProperty("method").GetString() == "notifications/cancelled")
            {
                Interlocked.Increment(ref cancellations);
                Assert.AreEqual(7, document.RootElement.GetProperty("params").GetProperty("requestId").GetInt32());
                Assert.AreEqual("owned-session", request.Headers.GetValues("Mcp-Session-Id").Single());
                Assert.AreEqual("1", request.Headers.GetValues("X-Host-Cancellation").Single());
                Assert.IsTrue(request.Content.Headers.ContentLength > 0);
                return new HttpResponseMessage(HttpStatusCode.Accepted);
            }
            Interlocked.Increment(ref toolCalls);
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("unreachable");
        }, timeoutSeconds: 1);
        fixture.Client.DefaultRequestHeaders.Add("Mcp-Session-Id", "owned-session");
        using var response = await fixture.CallAsync("""{"method":"tools/call","id":7,"params":{"name":"search_tools","arguments":{}}}""");
        Assert.AreEqual(HttpStatusCode.GatewayTimeout, response.StatusCode);
        Assert.AreEqual(1, toolCalls);
        Assert.AreEqual(1, cancellations);
    }

    [TestMethod]
    public async Task RpcErrorUnderHttp200IsAFailureAndApprovedNestedToolsHaveDistinctBoundedMetrics()
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await AgencyFixture.StartAsync((request, _) =>
        {
            Assert.AreEqual(AgencyFixture.BridgeKey, request.Headers.GetValues("X-Agency-Bridge-Key").Single());
            Assert.AreEqual("a", request.Headers.GetValues("X-Agency-Actor-Uid").Single());
            Assert.IsTrue(request.Headers.Contains("traceparent"));
            return Task.FromResult(LlmFixture.Json(new
            {
                jsonrpc = "2.0",
                id = 1,
                error = new { code = -32001, message = "private-policy-error" }
            }));
        });
        const string input = """{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"call_tool","arguments":{"toolset_id":"ado::orgName=msasg","tool_name":"repo_file","arguments":{"private":"never-export"}}}}""";
        using var response = await fixture.CallAsync(input);
        Assert.AreEqual(HttpStatusCode.OK, response.StatusCode);
        Assert.IsTrue((await response.Content.ReadAsStringAsync()).Contains("private-policy-error", StringComparison.Ordinal));
        Assert.AreEqual("rpc_error", capture.Spans.Single(span => span.OperationName == "agency.mcp").GetTagItem("host.outcome"));
        var tools = capture.Measurements.Where(value => value.Name == "host.tool.calls").ToArray();
        Assert.AreEqual(2, tools.Length);
        Assert.IsTrue(tools.Any(value => Equals(value.Tags["layer"], "outer") && Equals(value.Tags["tool"], "call_tool")));
        Assert.IsTrue(tools.Any(value => Equals(value.Tags["layer"], "nested")
            && Equals(value.Tags["toolset"], "ado") && Equals(value.Tags["tool"], "repo_file")));
        Assert.IsTrue(tools.All(value => Equals(value.Tags["outcome"], "rpc_error")));
        Assert.IsFalse(string.Join(" ", capture.Spans.SelectMany(span => span.Tags)).Contains("private", StringComparison.Ordinal));
        using var unknown = await fixture.CallAsync(input.Replace("repo_file", "caller-invented-private-tool", StringComparison.Ordinal));
        _ = await unknown.Content.ReadAsStringAsync();
        Assert.IsFalse(capture.Measurements.Any(value => value.Tags.Values.Contains("caller-invented-private-tool")));
    }

    [TestMethod]
    public async Task ToolIsErrorIsNotSuccessfulAndRevocationIsRecheckedForEveryRequest()
    {
        using var capture = new TelemetryCapture();
        var calls = 0;
        await using var fixture = await AgencyFixture.StartAsync((_, _) =>
        {
            Interlocked.Increment(ref calls);
            return Task.FromResult(LlmFixture.Json(new { jsonrpc = "2.0", id = 1, result = new { isError = true } }));
        });
        using var first = await fixture.CallAsync("""{"method":"tools/call","id":1,"params":{"name":"search_tools","arguments":{}}}""");
        _ = await first.Content.ReadAsStringAsync();
        Assert.AreEqual("tool_error", capture.Spans.Single(span => span.OperationName == "agency.mcp").GetTagItem("host.outcome"));
        fixture.RevokeActor();
        using var second = await fixture.CallAsync("""{"method":"tools/list","id":2}""");
        Assert.AreEqual(HttpStatusCode.Unauthorized, second.StatusCode);
        Assert.AreEqual(1, calls);
    }

    [TestMethod]
    public async Task JwtActorCannotSpoofBridgeHeadersOrEscapeCurrentBrokerProfileBinding()
    {
        using var jwt = new JwtFixture();
        var calls = 0;
        await using var fixture = await AgencyFixture.StartAsync((request, _) =>
        {
            Interlocked.Increment(ref calls);
            Assert.AreEqual("a", request.Headers.GetValues("X-Agency-Actor-Uid").Single());
            Assert.AreEqual("actor/factory-executions/a", request.Headers.GetValues("X-Agency-Actor").Single());
            return Task.FromResult(LlmFixture.Json(new { jsonrpc = "2.0", id = 1, result = new { } }));
        }, jwt.Configuration);
        fixture.Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", jwt.Token());
        fixture.Client.DefaultRequestHeaders.Remove("X-Agency-Actor-Uid");
        fixture.Client.DefaultRequestHeaders.Add("X-Agency-Actor-Uid", "spoofed");
        using var accepted = await fixture.CallAsync("""{"method":"ping","id":1}""");
        Assert.AreEqual(HttpStatusCode.OK, accepted.StatusCode);
        fixture.RebindProfile("writer");
        using var denied = await fixture.CallAsync("""{"method":"ping","id":2}""");
        Assert.AreEqual(HttpStatusCode.Unauthorized, denied.StatusCode);
        Assert.AreEqual(1, calls);
    }

    [TestMethod]
    public async Task BoundedMcpAdmissionDoesNotBlockIndependentPluginCapacity()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var fixture = await AgencyFixture.StartAsync(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.AbsolutePath == "/agency/mcp")
            {
                if ((await request.Content!.ReadAsStringAsync(cancellationToken)).Contains("notifications/cancelled", StringComparison.Ordinal))
                {
                    Assert.AreEqual("1", request.Headers.GetValues("X-Host-Cancellation").Single());
                    return new HttpResponseMessage(HttpStatusCode.Accepted);
                }
                entered.TrySetResult();
                await release.Task.WaitAsync(cancellationToken);
            }
            return LlmFixture.Json(new { result = new { } });
        });
        var first = fixture.CallAsync("""{"method":"ping","id":1}""");
        try
        {
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
            using var busy = await fixture.CallAsync("""{"method":"ping","id":2}""");
            Assert.AreEqual((HttpStatusCode)429, busy.StatusCode);
            using var cancelled = await fixture.CallAsync("""{"method":"notifications/cancelled","params":{"requestId":1}}""");
            Assert.AreEqual(HttpStatusCode.Accepted, cancelled.StatusCode);
            using var plugin = await fixture.Client.PostAsync("/agency/plugins", new StringContent("{}", Encoding.UTF8, "application/json"));
            Assert.AreEqual(HttpStatusCode.OK, plugin.StatusCode);
        }
        finally
        {
            release.TrySetResult();
            using var response = await first;
        }
    }
}

internal sealed class AgencyFixture : IAsyncDisposable
{
    internal const string BridgeKey = "test-only-private-bridge-key-32-chars";
    private readonly OwnedDirectory directory = new();
    private readonly HostTelemetry telemetry = new(new());
    private readonly HttpClient upstream;
    private readonly WebApplication application;
    private readonly string brokerConfig;
    internal HttpClient Client { get; }

    private AgencyFixture(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send,
        ActorJwtConfiguration? actorJwt, int timeoutSeconds)
    {
        brokerConfig = directory.Write("broker.json", Credentials("reader"));
        var profiles = directory.Write("profiles.json", """{"reader":{"toolsets":{"ado":{"tools":["repo_file"]}}}}""");
        var options = new AgencyConfiguration
        {
            Port = 0,
            ProfilesFile = profiles,
            BrokerConfigurationFile = brokerConfig,
            MaxConcurrentRequests = 1,
            RequestTimeoutSeconds = timeoutSeconds
        };
        var configuration = new HostConfiguration { Agency = options, ActorJwt = actorJwt ?? new() };
        var module = new AgencyModule(configuration, new(configuration.ActorJwt), telemetry);
        upstream = new(new FakeHandler(send)) { BaseAddress = new Uri("http://127.0.0.1:1") };
        application = module.CreateFront(options, upstream);
        Client = new(new SocketsHttpHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(10) };
        Client.DefaultRequestHeaders.Add("X-Agency-Bridge-Key", BridgeKey);
        Client.DefaultRequestHeaders.Add("X-Agency-Actor-Uid", "a");
        Client.DefaultRequestHeaders.Add("X-Agency-Actor", "actor/factory-executions/a");
    }

    internal static async Task<AgencyFixture> StartAsync(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send,
        ActorJwtConfiguration? actorJwt = null, int timeoutSeconds = 5)
    {
        var fixture = new AgencyFixture(send, actorJwt, timeoutSeconds);
        await fixture.application.StartAsync();
        fixture.Client.BaseAddress = HostApplication.Address(fixture.application);
        return fixture;
    }
    internal Task<HttpResponseMessage> CallAsync(string json) =>
        Client.PostAsync("/agency/mcp", new StringContent(json, Encoding.UTF8, "application/json"));
    internal void RevokeActor() => File.WriteAllText(brokerConfig, JsonSerializer.Serialize(new { bridgeKey = BridgeKey, actors = new { } }));
    internal void RebindProfile(string profile) => File.WriteAllText(brokerConfig, Credentials(profile));
    private static string Credentials(string profile) => JsonSerializer.Serialize(new
    {
        bridgeKey = BridgeKey,
        actors = new Dictionary<string, object>
        {
            ["a"] = new { subject = "actor/factory-executions/a", profile }
        }
    });
    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await application.StopAsync();
        await application.DisposeAsync();
        upstream.Dispose();
        telemetry.Dispose();
        directory.Dispose();
    }
}
