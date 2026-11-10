using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace LocalLlmGateway.Tests;

public sealed class ChatCompletionsTests
{
    [Theory]
    [InlineData("/V1/CHAT/COMPLETIONS")]
    [InlineData("/v1/chat/completions/")]
    public async Task EquivalentRouteSpellingsCannotBypassAuthenticationOrChangeProtocol(string path)
    {
        await using var fixture = await GatewayFixture.StartAsync((request, _) =>
        {
            Assert.Equal("/chat/completions", request.RequestUri!.AbsolutePath);
            return Task.FromResult(TestHandler.Json(new { }));
        });
        using var content = new StringContent(
            $$"""{"model":"{{GatewayFixture.ChatModel}}","messages":[{"role":"user","content":"test"}]}""",
            Encoding.UTF8, "application/json");
        fixture.Http.DefaultRequestHeaders.Authorization = null;
        using var denied = await fixture.Http.PostAsync(path, content);
        Assert.Equal(HttpStatusCode.Unauthorized, denied.StatusCode);
        Assert.Equal(0, fixture.CatalogCount);
        fixture.Http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", GatewayFixture.Key);
        using var accepted = await fixture.Http.PostAsync(path, content);
        Assert.Equal(HttpStatusCode.OK, accepted.StatusCode);
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(GatewayFixture.ChatModel, false)]
    [InlineData(GatewayFixture.Model, true)]
    [InlineData("metadata-absent", true)]
    public async Task RejectsModelsOnAnUnadvertisedEndpointWithoutInference(string model, bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync(catalog: () => TestHandler.Json(new
        {
            data = new object[]
            {
                new { id = GatewayFixture.ChatModel, supported_endpoints = new[] { "/chat/completions" } },
                new { id = GatewayFixture.Model, supported_endpoints = new[] { "/responses" } },
                new { id = "metadata-absent" }
            }
        }));
        using var response = await fixture.RespondAsync(
            $$"""{"model":"{{model}}","input":"test","messages":[{"role":"user","content":"test"}]}""",
            chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        using var error = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("unsupported_model_endpoint", error.RootElement.GetProperty("error").GetProperty("code").GetString());
        Assert.Equal(0, fixture.InferenceCount);
    }

    [Theory]
    [InlineData("/responses", false)]
    [InlineData("/chat/completions", true)]
    public async Task DualEndpointModelsRouteNativelyWithoutFallback(string endpoint, bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync((request, _) =>
        {
            Assert.Equal(endpoint, request.RequestUri!.AbsolutePath);
            return Task.FromResult(TestHandler.Json(new { }));
        }, catalog: () => TestHandler.Json(new
        {
            data = new[] { new { id = "dual-model", supported_endpoints = new[] { "/responses", "/chat/completions" } } }
        }));
        using var response = await fixture.RespondAsync(
            """{"model":"dual-model","messages":[{"role":"user","content":"test"}],"input":"test"}""",
            chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Fact]
    public async Task MissingEndpointMetadataPreservesLegacyResponsesAdmission()
    {
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(TestHandler.Json(new { })),
            catalog: () => TestHandler.Json(new { data = new[] { new { id = GatewayFixture.Model } } }));
        using var response = await fixture.RespondAsync($$"""{"model":"{{GatewayFixture.Model}}","input":"test"}""");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData("user", "user")]
    [InlineData("assistant", "agent")]
    [InlineData("tool", "agent")]
    public async Task PreservesNativeChatJsonToolFieldsAndContinuationHeaders(string role, string initiator)
    {
        var input = $$$"""
            { "model": "{{{GatewayFixture.ChatModel}}}", "stream": false,
              "messages": [{"role":"{{{role}}}","content":"test-result","tool_call_id":"c1"}],
              "tools": [{"type":"function","function":{"name":"test","parameters":{"type":"object"} }}],
              "tool_choice":{"type":"function","function":{"name":"test"}},"future_field":{"x":1} }
            """;
        const string output = """{"id":"chat1","choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"id":"c1","type":"function","function":{"name":"test","arguments":"{\"x\":1}"}}]},"finish_reason":"tool_calls"}],"usage":{"total_tokens":42}}""";
        await using var fixture = await GatewayFixture.StartAsync(async (request, ct) =>
        {
            Assert.Equal("/chat/completions", request.RequestUri!.AbsolutePath);
            Assert.Equal(input, await request.Content!.ReadAsStringAsync(ct));
            Assert.Equal("conversation-agent", request.Headers.GetValues("openai-intent").Single());
            Assert.Equal(initiator, request.Headers.GetValues("x-initiator").Single());
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(output, Encoding.UTF8, "application/json")
            };
        });
        fixture.Http.DefaultRequestHeaders.Add("X-Client-Secret", "do-not-forward");
        using var response = await fixture.RespondAsync(input, chatCompletions: true);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(output, await response.Content.ReadAsStringAsync());
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Fact]
    public async Task StreamsNativeToolCallDeltasAndDoneBeforeUpstreamFinishes()
    {
        const string first = "data: {\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\",\"type\":\"function\",\"function\":{\"name\":\"test\",\"arguments\":\"{\\\"x\\\":\"}}]},\"finish_reason\":null}]}\r\n\r\n";
        const string last = "data: {\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"1}\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\ndata: [DONE]\n\n";
        using var source = new GatedStream(first, last);
        await using var fixture = await GatewayFixture.StartAsync((request, _) =>
        {
            Assert.Equal("/chat/completions", request.RequestUri!.AbsolutePath);
            return Task.FromResult(GatewayFixture.Sse(source));
        });
        using var response = await fixture.RespondAsync(chatCompletions: true);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("text/event-stream", response.Content.Headers.ContentType!.MediaType);
        await using var body = await response.Content.ReadAsStreamAsync();
        var initial = new byte[Encoding.UTF8.GetByteCount(first)];
        await body.ReadExactlyAsync(initial).AsTask().WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(first, Encoding.UTF8.GetString(initial));
        Assert.False(source.Release.Task.IsCompleted);
        source.Release.SetResult();
        using var reader = new StreamReader(body);
        Assert.Equal(last, await reader.ReadToEndAsync());
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData("{")]
    [InlineData("[]")]
    [InlineData("""{"model":"gemini-4-argon","input":"not-chat"}""")]
    [InlineData("""{"model":"gemini-4-argon","messages":"wrong"}""")]
    [InlineData("""{"model":"gemini-4-argon","messages":[]}""")]
    [InlineData("""{"model":"gemini-4-argon","messages":[{"role":"user","content":"test"}],"stream":"true"}""")]
    public async Task RejectsInvalidChatPayloadsWithoutInference(string input)
    {
        await using var fixture = await GatewayFixture.StartAsync();
        using var response = await fixture.RespondAsync(input, chatCompletions: true);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(0, fixture.InferenceCount);
    }
}
