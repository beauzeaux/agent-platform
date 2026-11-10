using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace LocalLlmGateway.Tests;

public sealed class ResponsesTests
{
    [Theory]
    [InlineData(null, false)]
    [InlineData(null, true)]
    [InlineData("Bearer wrong-key", false)]
    [InlineData("Bearer wrong-key", true)]
    [InlineData("Basic private-key", false)]
    [InlineData("Basic private-key", true)]
    public async Task RejectsInvalidGatewayKeysWithoutContactingUpstream(string? authorization, bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync();
        fixture.Http.DefaultRequestHeaders.Authorization = null;
        if (authorization is not null)
            fixture.Http.DefaultRequestHeaders.TryAddWithoutValidation("Authorization", authorization);
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal(0, fixture.ExchangeCount);
        Assert.Equal(0, fixture.InferenceCount);
        Assert.Equal(0, fixture.CatalogCount);
    }

    [Fact]
    public async Task ExposesOnlyHealthModelsAndNativeInferenceEndpoints()
    {
        await using var fixture = await GatewayFixture.StartAsync();
        fixture.Http.DefaultRequestHeaders.Authorization = null;
        using var health = await fixture.Http.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, health.StatusCode);
        Assert.DoesNotContain("token", await health.Content.ReadAsStringAsync());
        fixture.Http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", GatewayFixture.Key);
        foreach (var path in new[] { "/token", "/usage", "/", "/v1/completions", "/login" })
        {
            using var response = await fixture.Http.GetAsync(path);
            Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        }
    }

    [Fact]
    public async Task ModelsPreserveCapabilitiesAndOmitKnownIncompatibleEntries()
    {
        await using var fixture = await GatewayFixture.StartAsync(catalog: () => TestHandler.Json(new
        {
            data = new object[]
            {
                new { id = GatewayFixture.Model, supported_endpoints = new[] { "/responses" },
                    capabilities = new { supports = new { tool_calls = true }, custom_metadata = "preserved" } },
                new { id = GatewayFixture.ChatModel, supported_endpoints = new[] { "/chat/completions" },
                    capabilities = new { supports = new { tool_calls = true } } },
                new { id = "legacy-only", supported_endpoints = new[] { "/completions" } },
                new { id = "disabled", policy = new { state = "disabled" } },
                new { id = "no-tools", capabilities = new { supports = new { tool_calls = false } } }
            }
        }));
        using var response = await fixture.Http.GetAsync("/v1/models");
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(2, json.RootElement.GetProperty("data").GetArrayLength());
        Assert.Equal("preserved", json.RootElement.GetProperty("data")[0].GetProperty("capabilities").GetProperty("custom_metadata").GetString());
        var chatModel = json.RootElement.GetProperty("data")[1];
        Assert.Equal(GatewayFixture.ChatModel, chatModel.GetProperty("id").GetString());
        Assert.Equal("/chat/completions", Assert.Single(chatModel.GetProperty("supported_endpoints").EnumerateArray()).GetString());
        using var again = await fixture.Http.GetAsync("/v1/models");
        Assert.Equal(1, fixture.CatalogCount);
    }

    [Fact]
    public async Task PreservesRawJsonAndUsesOnlyHostAuthorization()
    {
        const string input = $$$"""
            { "model": "{{{GatewayFixture.Model}}}", "stream": false,
              "reasoning": {"effort":"high"}, "unknown_future_field": {"x": 1},
              "tools": [{"type":"function","name":"test","parameters":{"type":"object"}}],
              "input": [{"type":"function_call_output","call_id":"c1","output":"test-result"}] }
            """;
        const string output = """{"id":"r1","output":[{"type":"reasoning","encrypted_content":"opaque"}],"usage":{"total_tokens":42}}""";
        await using var fixture = await GatewayFixture.StartAsync(async (request, ct) =>
        {
            Assert.Equal(input, await request.Content!.ReadAsStringAsync(ct));
            Assert.Equal("agent", request.Headers.GetValues("x-initiator").Single());
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(output, Encoding.UTF8, "application/json")
            };
        });
        fixture.Http.DefaultRequestHeaders.Add("X-Client-Secret", "do-not-forward");
        using var response = await fixture.RespondAsync(input);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(output, await response.Content.ReadAsStringAsync());
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData("{", HttpStatusCode.BadRequest)]
    [InlineData("[]", HttpStatusCode.BadRequest)]
    [InlineData("""{"input":"test"}""", HttpStatusCode.BadRequest)]
    [InlineData("""{"model":"gpt-5-test","stream":"true"}""", HttpStatusCode.BadRequest)]
    [InlineData("""{"model":"gpt-5-test","input":1}""", HttpStatusCode.BadRequest)]
    [InlineData("""{"model":"not-in-catalog"}""", HttpStatusCode.BadRequest)]
    public async Task RejectsInvalidRequestsWithoutInference(string input, HttpStatusCode expected)
    {
        await using var fixture = await GatewayFixture.StartAsync();
        using var response = await fixture.RespondAsync(input);
        Assert.Equal(expected, response.StatusCode);
        Assert.Equal(0, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task EnforcesRequestBodyAndMediaTypeLimits(bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync(options: new GatewayOptions { MaxRequestBytes = 100 });
        using var oversized = await fixture.RespondAsync(new string('x', 101), chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.RequestEntityTooLarge, oversized.StatusCode);
        using var text = await fixture.Http.PostAsync(chatCompletions ? "/v1/chat/completions" : "/v1/responses", new StringContent("not-json"));
        Assert.Equal(HttpStatusCode.UnsupportedMediaType, text.StatusCode);
        Assert.Equal(0, fixture.InferenceCount);
    }

    [Fact]
    public async Task ForwardsSseBeforeUpstreamFinishesWithoutRewritingEvents()
    {
        const string first = "event: response.function_call_arguments.delta\nid: item-1\ndata: {\"delta\":\"{\\\"x\\\":\"}\n\n";
        const string last = "event: response.function_call_arguments.delta\ndata: {\"delta\":\"1}\"}\n\nevent: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"usage\":{\"total_tokens\":3}}}\n\n";
        using var source = new GatedStream(first, last);
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(GatewayFixture.Sse(source)));
        using var response = await fixture.RespondAsync();
        Assert.Equal("text/event-stream", response.Content.Headers.ContentType!.MediaType);
        await using var body = await response.Content.ReadAsStreamAsync();
        var initial = new byte[Encoding.UTF8.GetByteCount(first)];
        await body.ReadExactlyAsync(initial).AsTask().WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(first, Encoding.UTF8.GetString(initial));
        Assert.False(source.Release.Task.IsCompleted);
        source.Release.SetResult();
        using var reader = new StreamReader(body);
        Assert.Equal(last, await reader.ReadToEndAsync());
    }

    [Theory]
    [InlineData(HttpStatusCode.Unauthorized, HttpStatusCode.ServiceUnavailable, false)]
    [InlineData(HttpStatusCode.Unauthorized, HttpStatusCode.ServiceUnavailable, true)]
    [InlineData(HttpStatusCode.Forbidden, HttpStatusCode.Forbidden, false)]
    [InlineData(HttpStatusCode.Forbidden, HttpStatusCode.Forbidden, true)]
    [InlineData(HttpStatusCode.TooManyRequests, HttpStatusCode.TooManyRequests, false)]
    [InlineData(HttpStatusCode.TooManyRequests, HttpStatusCode.TooManyRequests, true)]
    [InlineData(HttpStatusCode.InternalServerError, HttpStatusCode.InternalServerError, false)]
    [InlineData(HttpStatusCode.InternalServerError, HttpStatusCode.InternalServerError, true)]
    [InlineData(HttpStatusCode.Redirect, HttpStatusCode.BadGateway, false)]
    [InlineData(HttpStatusCode.Redirect, HttpStatusCode.BadGateway, true)]
    public async Task PreservesRelevantErrorsWithoutBodiesOrInferenceReplay(HttpStatusCode upstream, HttpStatusCode expected,
        bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync((_, _) =>
        {
            var response = TestHandler.Json(new { error = "inference-secret private-host-token private-prompt" }, upstream);
            response.Headers.RetryAfter = new RetryConditionHeaderValue(TimeSpan.FromSeconds(12));
            response.Headers.Location = new Uri("https://example.com/never-follow");
            return Task.FromResult(response);
        });
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(expected, response.StatusCode);
        Assert.Equal(TimeSpan.FromSeconds(12), response.Headers.RetryAfter!.Delta);
        var error = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain("secret", error);
        Assert.DoesNotContain("private-", error);
        Assert.False(response.Headers.Contains("Location"));
        Assert.Equal(1, fixture.InferenceCount);
        Assert.Equal(1, fixture.ExchangeCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task UpstreamUnauthorizedInvalidatesTokenForNextRequestWithoutReplaying(bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync((_, _) =>
            Task.FromResult(TestHandler.Json(new { error = "private" }, HttpStatusCode.Unauthorized)));
        using var first = await fixture.RespondAsync(chatCompletions: chatCompletions);
        using var second = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, first.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, second.StatusCode);
        Assert.Equal(2, fixture.ExchangeCount);
        Assert.Equal(2, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task FourStreamsAreIndependentAndTheFifthIsRejectedUntilCancellationReleasesCapacity(bool chatCompletions)
    {
        var streams = new ConcurrentQueue<GatedStream>();
        await using var fixture = await GatewayFixture.StartAsync((_, _) =>
        {
            var source = new GatedStream("data: {\"type\":\"response.created\"}\n\n", "data: {\"type\":\"response.completed\"}\n\n");
            streams.Enqueue(source);
            return Task.FromResult(GatewayFixture.Sse(source));
        });
        var responses = await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => fixture.RespondAsync(chatCompletions: chatCompletions)));
        try
        {
            Assert.All(responses, response => Assert.Equal(HttpStatusCode.OK, response.StatusCode));
            Assert.Equal(4, fixture.InferenceCount);
            using var rejected = await fixture.RespondAsync(chatCompletions: !chatCompletions);
            Assert.Equal(HttpStatusCode.TooManyRequests, rejected.StatusCode);
            using var health = await fixture.Http.GetAsync("/health");
            using var models = await fixture.Http.GetAsync("/v1/models");
            Assert.Equal(HttpStatusCode.OK, health.StatusCode);
            Assert.Equal(HttpStatusCode.OK, models.StatusCode);

            // Each downstream stream may be assigned to any one of the concurrent upstreams.
            responses[0].Dispose();
            await Task.WhenAny(streams.Select(source => source.Canceled.Task)).WaitAsync(TimeSpan.FromSeconds(5));
            HttpResponseMessage? replacement = null;
            for (var attempt = 0; attempt < 50; attempt++)
            {
                replacement = await fixture.RespondAsync(chatCompletions: !chatCompletions);
                if (replacement.StatusCode == HttpStatusCode.OK)
                    break;
                replacement.Dispose();
                await Task.Delay(20);
            }
            Assert.NotNull(replacement);
            using (replacement)
                Assert.Equal(HttpStatusCode.OK, replacement.StatusCode);
            Assert.Equal(5, fixture.InferenceCount);
        }
        finally
        {
            foreach (var source in streams)
                source.Release.TrySetResult();
            foreach (var response in responses)
                response.Dispose();
        }
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task UpstreamStreamFailureAbortsRatherThanFabricatingSuccess(bool chatCompletions)
    {
        using var source = new GatedStream("data: {\"type\":\"response.created\"}\n\n", failAfterFirst: true);
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(GatewayFixture.Sse(source)));
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        source.Release.SetResult();
        var error = await Assert.ThrowsAnyAsync<HttpRequestException>(() => response.Content.ReadAsStringAsync());
        Assert.DoesNotContain("private-upstream-detail", error.Message);
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task TimeoutCancelsUpstreamAndReleasesCapacity(bool chatCompletions)
    {
        using var source = new GatedStream("data: {\"type\":\"response.created\"}\n\n");
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(GatewayFixture.Sse(source)),
            new GatewayOptions { MaxConcurrentRequests = 1, RequestTimeoutSeconds = 1 });
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        await source.Canceled.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await Assert.ThrowsAnyAsync<HttpRequestException>(() => response.Content.ReadAsStringAsync());
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task RejectsUnexpectedUpstreamContentType(bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(TestHandler.Json(new { output = "not SSE" })));
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.BadGateway, response.StatusCode);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task RejectsChunkedBodiesAboveTheLimit(bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync(options: new GatewayOptions { MaxRequestBytes = 50 });
        using var source = new GatedStream(new string('x', 100));
        source.Release.SetResult();
        using var content = new StreamContent(source);
        content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        using var response = await fixture.Http.PostAsync(chatCompletions ? "/v1/chat/completions" : "/v1/responses", content);
        Assert.Equal(HttpStatusCode.RequestEntityTooLarge, response.StatusCode);
        Assert.Equal(0, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task ConnectionFailuresAreSanitizedAndNotReplayed(bool chatCompletions)
    {
        await using var fixture = await GatewayFixture.StartAsync((_, _) =>
            Task.FromException<HttpResponseMessage>(new HttpRequestException("private-network-details")));
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.BadGateway, response.StatusCode);
        Assert.DoesNotContain("private", await response.Content.ReadAsStringAsync());
        Assert.Equal(1, fixture.InferenceCount);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task TimeoutBeforeHeadersReturns504(bool chatCompletions)
    {
        var canceled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var fixture = await GatewayFixture.StartAsync(async (_, ct) =>
        {
            try { await Task.Delay(Timeout.Infinite, ct); }
            catch (OperationCanceledException) { canceled.SetResult(); throw; }
            return TestHandler.Json(new { });
        }, new GatewayOptions { RequestTimeoutSeconds = 1 });
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.Equal(HttpStatusCode.GatewayTimeout, response.StatusCode);
        Assert.True(canceled.Task.IsCompleted);
    }

    [Fact]
    public async Task LogsDoNotContainCredentialsPromptsResponsesOrUnknownPaths()
    {
        using var logs = new LogCapture();
        await using var fixture = await GatewayFixture.StartAsync((_, _) => Task.FromResult(
            TestHandler.Json(new { error = "private-response" }, HttpStatusCode.Forbidden)));
        fixture.App.Services.GetRequiredService<ILoggerFactory>().AddProvider(logs);
        using var response = await fixture.RespondAsync($$"""{"model":"{{GatewayFixture.Model}}","input":"private-prompt"}""");
        using var unknown = await fixture.Http.GetAsync("/private-path?key=private-query");
        await fixture.App.StopAsync();
        var text = string.Join("\n", logs.Messages);
        Assert.Contains("/v1/responses", text);
        foreach (var value in new[] { GatewayFixture.Key, "inference-secret", "host-only-test-credential",
            "private-response", "private-prompt", "private-path", "private-query" })
            Assert.DoesNotContain(value, text);
    }

    private sealed class LogCapture : ILoggerProvider, ILogger
    {
        public ConcurrentQueue<string> Messages { get; } = new();
        public ILogger CreateLogger(string categoryName) => this;
        public void Dispose() { }
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter) =>
            Messages.Enqueue(formatter(state, exception));
    }
}
