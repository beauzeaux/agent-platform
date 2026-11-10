using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class ImdsEndpointTests
{
    [TestMethod]
    public async Task AllowedActorReceivesAzureDevOpsImdsResponse()
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("audit-docs-contributor"), tokens, telemetry);
        var context = Context();

        await endpoint.HandleAsync(context);

        using var response = await ReadAsync(context);
        Assert.AreEqual(200, context.Response.StatusCode);
        Assert.AreEqual(tokens.Token, response.RootElement.GetProperty("access_token").GetString());
        Assert.AreEqual(ImdsEndpoint.AzureDevOpsResource, response.RootElement.GetProperty("resource").GetString());
        Assert.AreEqual(tokens.ClientId, response.RootElement.GetProperty("client_id").GetString());
        Assert.AreEqual(ImdsEndpoint.AzureDevOpsResource, tokens.Resource);
        Assert.AreEqual(1, tokens.Calls);
    }

    [TestMethod]
    public async Task EngsysActorReceivesAzureManagementToken()
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("engsys-contributor"), tokens, telemetry);
        var context = Context();
        context.Request.QueryString = new(
            "?api-version=2019-08-01&resource=https%3A%2F%2Fmanagement.azure.com%2F");

        await endpoint.HandleAsync(context);

        using var response = await ReadAsync(context);
        Assert.AreEqual(200, context.Response.StatusCode);
        Assert.AreEqual(ImdsEndpoint.AzureManagementResource, response.RootElement.GetProperty("resource").GetString());
        Assert.AreEqual(ImdsEndpoint.AzureManagementResource, tokens.Resource);
    }

    [TestMethod]
    public async Task UnapprovedProfileIsDeniedBeforeTokenAcquisition()
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("engsys-reader"), tokens, telemetry);
        var context = Context();

        await endpoint.HandleAsync(context);

        Assert.AreEqual(403, context.Response.StatusCode);
        Assert.AreEqual(0, tokens.Calls);
    }

    [TestMethod]
    [DataRow("?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F")]
    [DataRow("?api-version=2018-02-01&resource=11111111-1111-1111-1111-111111111111")]
    [DataRow("?api-version=2018-02-01&resource=499b84ac-1321-427f-aa17-267ca6975798&client_id=other")]
    public async Task UnsupportedResourceOrIdentitySelectorIsDenied(string query)
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("audit-docs-contributor"), tokens, telemetry);
        var context = Context();
        context.Request.QueryString = new(query);

        await endpoint.HandleAsync(context);

        Assert.AreEqual(400, context.Response.StatusCode);
        Assert.AreEqual(0, tokens.Calls);
    }

    [TestMethod]
    public async Task MissingMetadataHeaderIsDenied()
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("audit-docs-contributor"), tokens, telemetry);
        var context = Context();
        context.Request.Headers.Remove("Metadata");

        await endpoint.HandleAsync(context);

        Assert.AreEqual(400, context.Response.StatusCode);
        Assert.AreEqual(0, tokens.Calls);
    }

    [TestMethod]
    public async Task AppServiceIdentityHeaderAllowsMetadataOmission()
    {
        var tokens = new FakeTokens();
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("audit-docs-contributor"), tokens, telemetry);
        var context = Context();
        context.Request.Headers.Remove("Metadata");
        context.Request.Headers[ImdsEndpoint.IdentityHeader] = "Bearer actor-token";
        context.Request.QueryString = new(
            "?api-version=2019-08-01&resource=499b84ac-1321-427f-aa17-267ca6975798");

        await endpoint.HandleAsync(context);

        Assert.AreEqual(200, context.Response.StatusCode);
        Assert.AreEqual(1, tokens.Calls);
    }

    [TestMethod]
    public async Task ExpiringTokenIsNotReturned()
    {
        var tokens = new FakeTokens(DateTimeOffset.UtcNow.AddMinutes(1));
        using var telemetry = new HostTelemetry(new());
        var endpoint = new ImdsEndpoint(Configuration(), new FakeActors("audit-docs-contributor"), tokens, telemetry);
        var context = Context();

        await endpoint.HandleAsync(context);

        using var response = await ReadAsync(context);
        Assert.AreEqual(503, context.Response.StatusCode);
        Assert.IsFalse(response.RootElement.TryGetProperty("access_token", out _));
    }

    private static ImdsConfiguration Configuration() => new()
    {
        ResourcesByProfile = new()
        {
            ["audit-docs-contributor"] = [ImdsEndpoint.AzureDevOpsResource],
            ["engsys-contributor"] = [ImdsEndpoint.AzureDevOpsResource, ImdsEndpoint.AzureManagementResource]
        }
    };

    private static DefaultHttpContext Context()
    {
        var context = new DefaultHttpContext();
        context.Connection.RemoteIpAddress = IPAddress.Loopback;
        context.Request.Headers["Metadata"] = "true";
        context.Request.QueryString = new(
            "?api-version=2018-02-01&resource=499b84ac-1321-427f-aa17-267ca6975798");
        context.Response.Body = new MemoryStream();
        return context;
    }

    private static async Task<JsonDocument> ReadAsync(DefaultHttpContext context)
    {
        context.Response.Body.Position = 0;
        return await JsonDocument.ParseAsync(context.Response.Body);
    }

    private sealed class FakeActors(string profile) : IImdsActorAuthorizer
    {
        public Task<ActorIdentity?> AuthorizeAsync(HttpContext context) =>
            Task.FromResult<ActorIdentity?>(new("uid", "actor/baseline/session-test", profile));
    }

    private sealed class FakeTokens(DateTimeOffset? expiresOn = null) : IImdsTokenProvider
    {
        public string ClientId { get; } = "04b07795-8ddb-461a-bbee-02f9e1bf7b46";
        public int Calls { get; private set; }
        public string Resource { get; private set; } = "";
        public string Token { get; private set; } = "";

        public Task<string> GetTokenAsync(string resource, CancellationToken cancellationToken)
        {
            Calls++;
            Resource = resource;
            var header = Base64Url("""{"alg":"none"}""");
            var payload = Base64Url(JsonSerializer.Serialize(new
            {
                exp = (expiresOn ?? DateTimeOffset.UtcNow.AddHours(1)).ToUnixTimeSeconds()
            }));
            Token = $"{header}.{payload}.test-only-token";
            return Task.FromResult(Token);
        }

        private static string Base64Url(string value) => Convert.ToBase64String(Encoding.UTF8.GetBytes(value))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
}
