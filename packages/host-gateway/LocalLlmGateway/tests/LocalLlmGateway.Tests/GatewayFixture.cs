using System.Net;
using System.Net.Http.Headers;
using System.Text;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;

namespace LocalLlmGateway.Tests;

internal sealed class GatewayFixture : IAsyncDisposable
{
    public const string Key = "gateway-key-for-tests-not-a-github-token";
    public const string Model = "gpt-5-test";
    public const string ChatModel = "gemini-4-argon";
    private readonly HttpClient upstream;
    private readonly CopilotTokenProvider tokens;
    public WebApplication App { get; }
    public HttpClient Http { get; private set; } = null!;
    public int ExchangeCount;
    public int CatalogCount;
    public int InferenceCount;

    private GatewayFixture(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> inference,
        GatewayOptions? options, Func<HttpResponseMessage>? catalog)
    {
        var clock = new TestClock();
        upstream = new HttpClient(new TestHandler((request, ct) =>
        {
            if (request.RequestUri!.Host == "api.github.com")
            {
                Interlocked.Increment(ref ExchangeCount);
                return Task.FromResult(AuthenticationTests.TokenResponse(clock));
            }
            Assert.Equal("Bearer inference-secret", request.Headers.Authorization!.ToString());
            Assert.Equal("api.githubcopilot.com", request.RequestUri.Host);
            Assert.DoesNotContain(request.Headers, header => header.Key == "X-Client-Secret");
            if (request.RequestUri.AbsolutePath == "/models")
            {
                Interlocked.Increment(ref CatalogCount);
                return Task.FromResult(catalog?.Invoke() ?? DefaultCatalog());
            }
            Assert.Contains(request.RequestUri.AbsolutePath, new[] { "/responses", "/chat/completions" });
            Interlocked.Increment(ref InferenceCount);
            return inference(request, ct);
        }));
        tokens = new CopilotTokenProvider(upstream, "host-only-test-credential", clock);
        var client = new CopilotClient(upstream, tokens, "individual");
        options ??= new GatewayOptions();
        options.Port = 0; // Kestrel assigns an isolated loopback port for each integration test.
        App = GatewayServer.Create(options, Key, client);
    }

    public static async Task<GatewayFixture> StartAsync(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>? inference = null,
        GatewayOptions? options = null, Func<HttpResponseMessage>? catalog = null)
    {
        var fixture = new GatewayFixture(inference ?? ((_, _) => Task.FromResult(TestHandler.Json(new { output = new object[0] }))),
            options, catalog);
        try
        {
            await fixture.App.StartAsync();
            fixture.Http = new HttpClient(new SocketsHttpHandler { MaxResponseDrainSize = 0 })
            {
                BaseAddress = new Uri(fixture.App.Urls.Single()),
                Timeout = TimeSpan.FromSeconds(15)
            };
            fixture.Http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", Key);
            return fixture;
        }
        catch
        {
            await fixture.DisposeAsync();
            throw;
        }
    }

    public Task<HttpResponseMessage> RespondAsync(string? payload = null, CancellationToken cancellationToken = default,
        bool chatCompletions = false)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, chatCompletions ? "/v1/chat/completions" : "/v1/responses")
        {
            Content = new StringContent(payload ?? (chatCompletions
                ? $$"""{"model":"{{ChatModel}}","messages":[{"role":"user","content":"test"}],"stream":true}"""
                : $$"""{"model":"{{Model}}","input":"test","stream":true}"""),
                Encoding.UTF8, "application/json")
        };
        return SendAndDisposeRequestAsync(request, cancellationToken);
    }

    private async Task<HttpResponseMessage> SendAndDisposeRequestAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        using (request)
            return await Http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
    }

    public static HttpResponseMessage DefaultCatalog() => TestHandler.Json(new
    {
        data = new[]
        {
            new { id = Model, supported_endpoints = new[] { "/responses" },
                capabilities = new { supports = new { tool_calls = true }, limits = new { max_context_window_tokens = 128000 } } },
            new { id = ChatModel, supported_endpoints = new[] { "/chat/completions" },
                capabilities = new { supports = new { tool_calls = true }, limits = new { max_context_window_tokens = 128000 } } }
        }
    });

    public static HttpResponseMessage Sse(Stream body) => new(HttpStatusCode.OK)
    {
        Content = new StreamContent(body) { Headers = { ContentType = new MediaTypeHeaderValue("text/event-stream") } }
    };

    public async ValueTask DisposeAsync()
    {
        Http?.Dispose();
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await App.StopAsync(deadline.Token);
        await App.DisposeAsync();
        tokens.Dispose();
        upstream.Dispose();
    }
}

internal sealed class GatedStream(string first, string last = "", bool failAfterFirst = false) : Stream
{
    private readonly byte[] firstBytes = Encoding.UTF8.GetBytes(first);
    private readonly byte[] lastBytes = Encoding.UTF8.GetBytes(last);
    private int firstPosition;
    private int lastPosition;
    public TaskCompletionSource Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource Canceled { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        if (firstPosition < firstBytes.Length)
        {
            var count = Math.Min(buffer.Length, firstBytes.Length - firstPosition);
            firstBytes.AsMemory(firstPosition, count).CopyTo(buffer);
            firstPosition += count;
            return count;
        }
        try { await Release.Task.WaitAsync(cancellationToken); }
        catch (OperationCanceledException) { Canceled.TrySetResult(); throw; }
        if (failAfterFirst)
            throw new IOException("private-upstream-detail");
        var remaining = Math.Min(buffer.Length, lastBytes.Length - lastPosition);
        lastBytes.AsMemory(lastPosition, remaining).CopyTo(buffer);
        lastPosition += remaining;
        return remaining;
    }

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
        ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
    public override bool CanRead => true;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
    public override void Flush() => throw new NotSupportedException();
    public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
}
