using System.Collections.Concurrent;
using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using LocalLlmGateway;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Logging;

namespace HostCapabilities.Tests;

internal sealed class OwnedDirectory : IDisposable
{
    public string Path { get; } = System.IO.Path.Combine(Environment.CurrentDirectory, ".state", "test-" + Guid.NewGuid().ToString("N"));
    public OwnedDirectory() => Directory.CreateDirectory(Path);
    public string Write(string name, string content)
    {
        var path = System.IO.Path.Combine(Path, name);
        File.WriteAllText(path, content);
        return path;
    }
    public void Dispose() => Directory.Delete(Path, true);
}

internal sealed class TelemetryCapture : IDisposable
{
    private readonly ActivityListener activities;
    private readonly MeterListener meter = new();
    public ConcurrentQueue<Activity> Spans { get; } = new();
    public ConcurrentQueue<(string Name, long Value, Dictionary<string, object?> Tags)> Measurements { get; } = new();

    public TelemetryCapture()
    {
        activities = new()
        {
            ShouldListenTo = source => source.Name == HostTelemetry.SourceName,
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = activity => Spans.Enqueue(activity)
        };
        ActivitySource.AddActivityListener(activities);
        meter.InstrumentPublished = (instrument, listener) =>
        {
            if (instrument.Meter.Name == HostTelemetry.SourceName)
                listener.EnableMeasurementEvents(instrument);
        };
        meter.SetMeasurementEventCallback<long>((instrument, value, tags, _) =>
            Measurements.Enqueue((instrument.Name, value, tags.ToArray().ToDictionary(tag => tag.Key, tag => tag.Value))));
        meter.Start();
    }

    public void Dispose()
    {
        meter.Dispose();
        activities.Dispose();
    }
}

internal sealed class LlmFixture : IAsyncDisposable
{
    internal const string Key = "test-only-distinct-gateway-key-32-chars";
    private readonly HostTelemetry telemetry;
    private readonly HttpClient upstream;
    private readonly CopilotTokenProvider tokens;
    internal WebApplication App { get; }
    internal HttpClient Client { get; }
    internal int Inferences;

    private LlmFixture(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond,
        ActorJwtConfiguration? actorJwt = null, int timeoutSeconds = 10)
    {
        var configuration = new HostConfiguration { ActorJwt = actorJwt ?? new() };
        telemetry = new(new());
        upstream = new(new FakeHandler(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.Host == "api.github.com")
                return Json(new { token = "private-inference-token", expires_at = DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds(), refresh_in = 1800 });
            if (request.RequestUri.AbsolutePath == "/models")
                return Json(new { data = new[] { new { id = "test-model", supported_endpoints = new[] { "/responses", "/chat/completions" } } } });
            Interlocked.Increment(ref Inferences);
            return await respond(request, cancellationToken);
        }));
        tokens = new(upstream, "private-host-token");
        var module = new LlmModule(configuration, new(configuration.ActorJwt), telemetry);
        App = GatewayServer.Create(new GatewayOptions { Port = 0, RequestTimeoutSeconds = timeoutSeconds }, Key,
            new(upstream, tokens, "individual"), app => module.Configure(app, Key));
        Client = new(new SocketsHttpHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(15) };
        Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", Key);
    }

    internal static async Task<LlmFixture> StartAsync(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond,
        ActorJwtConfiguration? actorJwt = null, int timeoutSeconds = 10)
    {
        var fixture = new LlmFixture(respond, actorJwt, timeoutSeconds);
        await fixture.App.StartAsync();
        fixture.Client.BaseAddress = HostApplication.Address(fixture.App);
        return fixture;
    }

    internal Task<HttpResponseMessage> RespondAsync(bool streaming = true, bool chatCompletions = false, string? path = null) => Client.SendAsync(
        new HttpRequestMessage(HttpMethod.Post,
            (path ?? (chatCompletions ? "/v1/chat/completions" : "/v1/responses")) + "?private-query=never-export")
        {
            Content = new StringContent(chatCompletions ? JsonSerializer.Serialize(new
            {
                model = "test-model",
                stream = streaming,
                messages = new[] { new { role = "user", content = "private-prompt-never-export" } }
            }) : JsonSerializer.Serialize(new
            {
                model = "test-model",
                stream = streaming,
                input = "private-prompt-never-export"
            }), Encoding.UTF8, "application/json")
        }, HttpCompletionOption.ResponseHeadersRead);

    internal static HttpResponseMessage Json(object body, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json") };

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await App.StopAsync();
        await App.DisposeAsync();
        tokens.Dispose();
        upstream.Dispose();
        telemetry.Dispose();
    }
}

internal sealed class FakeHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
        send(request, cancellationToken);
}
