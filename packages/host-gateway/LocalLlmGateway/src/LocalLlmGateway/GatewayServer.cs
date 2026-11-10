using System.Buffers;
using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http.Features;

namespace LocalLlmGateway;

public static class GatewayServer
{
    public static WebApplication Create(GatewayOptions options, string key, CopilotClient client,
        Action<WebApplication>? configure = null)
    {
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = [],
            ApplicationName = typeof(GatewayServer).Assembly.FullName
        });
        builder.Configuration.Sources.Clear();
        builder.Configuration.AddInMemoryCollection();
        builder.Logging.ClearProviders();
        builder.Logging.AddSimpleConsole(console => console.SingleLine = true);
        builder.Logging.AddFilter((category, level) =>
            category?.StartsWith("LocalLlmGateway.", StringComparison.Ordinal) == true && level >= LogLevel.Information);
        builder.WebHost.UseUrls(options.ListenUri.ToString());
        builder.WebHost.ConfigureKestrel(server => server.Limits.MaxRequestBodySize = options.MaxRequestBytes);
        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton(client);
        builder.Services.AddSingleton<ModelCatalog>();
        builder.Services.AddSingleton(new InferenceCapacity(options.MaxConcurrentRequests));
        var app = builder.Build();
        configure?.Invoke(app);
        var keyHash = SHA256.HashData(Encoding.UTF8.GetBytes(key));
        var logger = app.Services.GetRequiredService<ILoggerFactory>().CreateLogger("LocalLlmGateway.Requests");

        app.Use(async (context, next) =>
        {
            var timer = Stopwatch.StartNew();
            var route = GetRoute(context.Request.Path) ?? "<unmapped>";
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
            deadline.CancelAfter(TimeSpan.FromSeconds(options.RequestTimeoutSeconds));
            try
            {
                context.Items[CancellationKey] = deadline.Token;
                context.Response.Headers.CacheControl = "no-store";
                context.Response.Headers.XContentTypeOptions = "nosniff";
                if (route is "/v1/models" or "/v1/responses" or "/v1/chat/completions" && !IsAuthorized(context, keyHash))
                {
                    context.Response.Headers.WWWAuthenticate = "Bearer";
                    await ErrorAsync(context, 401, "invalid_gateway_key", "A valid gateway key is required.", deadline.Token);
                    return;
                }
                await next(context);
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
            {
                context.Items["gatewayOutcome"] = "cancelled";
                logger.LogInformation("Request {RequestId} canceled by client.", context.TraceIdentifier);
                context.Abort();
            }
            catch (OperationCanceledException)
            {
                context.Items["gatewayOutcome"] = "timeout";
                await FailAsync(context, 504, "upstream_timeout", "The upstream request timed out.", logger);
            }
            catch (UpstreamException ex)
            {
                context.Items["gatewayUpstreamStatus"] = (int)ex.StatusCode;
                if (ex.RetryAfter is { } retry)
                    context.Response.Headers.RetryAfter = Math.Max(0, (long)retry.TotalSeconds).ToString(System.Globalization.CultureInfo.InvariantCulture);
                await FailAsync(context, 503, "host_upstream_unavailable",
                    $"Host authentication or model discovery failed (upstream HTTP {(int)ex.StatusCode}).", logger);
            }
            catch (GatewayException ex)
            {
                await FailAsync(context, ex.StatusCode, "gateway_unavailable", ex.Message, logger);
            }
            catch (BadHttpRequestException ex)
            {
                await FailAsync(context, ex.StatusCode, "invalid_request", "The request could not be read.", logger);
            }
            catch (Exception ex) when (ex is HttpRequestException or IOException)
            {
                context.Items["gatewayOutcome"] = "transport_error";
                await FailAsync(context, 502, "upstream_connection_failed", "The upstream connection failed.", logger);
            }
            catch (Exception ex)
            {
                logger.LogError("Request {RequestId} failed with {ErrorType}.", context.TraceIdentifier, ex.GetType().Name);
                await FailAsync(context, 500, "internal_error", "The gateway could not complete the request.", logger);
            }
            finally
            {
                logger.LogInformation("Request {RequestId} route {Route} model {Model} status {Status} duration_ms {Duration}",
                    context.TraceIdentifier, route, context.Items[ModelKey] ?? "-", context.Response.StatusCode, timer.ElapsedMilliseconds);
            }
        });

        app.MapGet("/health", () => Results.Json(new { status = "ok" }));
        app.MapGet("/v1/models", async (HttpContext context, ModelCatalog catalog) =>
        {
            var models = await catalog.GetAsync(Token(context));
            await context.Response.WriteAsJsonAsync(new { @object = "list", data = models.Values }, Token(context));
        });
        app.MapPost("/v1/responses", RelayAsync);
        app.MapPost("/v1/chat/completions", RelayAsync);
        return app;
    }

    private const string CancellationKey = "gatewayCancellation";
    private const string ModelKey = "gatewayModel";
    private static CancellationToken Token(HttpContext context) => (CancellationToken)context.Items[CancellationKey]!;

    public static string? GetRoute(PathString path)
    {
        // Match ASP.NET routing's case-insensitivity and optional trailing slash before authentication.
        var route = path.Value?.TrimEnd('/').ToLowerInvariant();
        return route is "/health" or "/v1/models" or "/v1/responses" or "/v1/chat/completions" ? route : null;
    }

    private static bool IsAuthorized(HttpContext context, byte[] keyHash)
    {
        if (!AuthenticationHeaderValue.TryParse(context.Request.Headers.Authorization, out var authorization)
            || !authorization.Scheme.Equals("Bearer", StringComparison.OrdinalIgnoreCase)
            || string.IsNullOrEmpty(authorization.Parameter))
            return false;
        return CryptographicOperations.FixedTimeEquals(keyHash, SHA256.HashData(Encoding.UTF8.GetBytes(authorization.Parameter)));
    }

    private static async Task RelayAsync(HttpContext context, GatewayOptions options, CopilotClient client,
        ModelCatalog catalog, InferenceCapacity capacity)
    {
        var cancellationToken = Token(context);
        var chatCompletions = GetRoute(context.Request.Path) == "/v1/chat/completions";
        var endpoint = chatCompletions ? "/chat/completions" : "/responses";
        if (!await capacity.Slots.WaitAsync(0, cancellationToken))
        {
            context.Response.Headers.RetryAfter = "1";
            await ErrorAsync(context, 429, "gateway_busy", "All inference slots are occupied.", cancellationToken);
            return;
        }
        try
        {
            if (!MediaTypeHeaderValue.TryParse(context.Request.ContentType, out var contentType)
                || contentType.MediaType != "application/json")
            {
                await ErrorAsync(context, 415, "unsupported_media_type", "Send an application/json request.", cancellationToken);
                return;
            }
            if (context.Request.ContentLength > options.MaxRequestBytes)
            {
                await ErrorAsync(context, 413, "request_too_large", "The request exceeds the configured body limit.", cancellationToken);
                return;
            }
            var body = await ReadBodyAsync(context.Request.Body, options.MaxRequestBytes, cancellationToken);
            JsonDocument document;
            try
            {
                document = JsonDocument.Parse(body);
            }
            catch (JsonException)
            {
                await ErrorAsync(context, 400, "invalid_json", "The request must contain valid JSON.", cancellationToken);
                return;
            }
            using (document)
            {
                var root = document.RootElement;
                if (root.ValueKind != JsonValueKind.Object
                    || !root.TryGetProperty("model", out var model) || model.ValueKind != JsonValueKind.String
                    || string.IsNullOrWhiteSpace(model.GetString())
                    || (root.TryGetProperty("stream", out var stream) && stream.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    || (chatCompletions
                        ? !root.TryGetProperty("messages", out var messages) || messages.ValueKind != JsonValueKind.Array
                            || messages.GetArrayLength() == 0
                        : root.TryGetProperty("input", out var input) && input.ValueKind is not (JsonValueKind.String or JsonValueKind.Array)))
                {
                    await ErrorAsync(context, 400, "invalid_request", chatCompletions
                        ? "Provide a model, a boolean stream flag, and a nonempty messages array."
                        : "Provide a model, a boolean stream flag, and a string or array input when present.", cancellationToken);
                    return;
                }
                var models = await catalog.GetAsync(cancellationToken);
                if (!models.TryGetValue(model.GetString()!, out var selected))
                {
                    await ErrorAsync(context, 400, "unsupported_model", "Select a model from /v1/models.", cancellationToken);
                    return;
                }
                if (!ModelCatalog.SupportsEndpoint(selected, endpoint))
                {
                    await ErrorAsync(context, 400, "unsupported_model_endpoint",
                        $"The selected model does not support {endpoint}. Check supported_endpoints in /v1/models.", cancellationToken);
                    return;
                }
                context.Items[ModelKey] = model.GetString();
                var streaming = stream.ValueKind == JsonValueKind.True;
                var continuation = IsContinuation(root, chatCompletions);
                using var payload = new ByteArrayContent(body);
                payload.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                using var upstream = await client.SendAsync(HttpMethod.Post, endpoint[1..], payload, cancellationToken, continuation);
                context.Items["gatewayUpstreamStatus"] = (int)upstream.StatusCode;
                if (!upstream.IsSuccessStatusCode)
                {
                    if (upstream.Headers.RetryAfter is { } retryAfter)
                        context.Response.Headers.RetryAfter = retryAfter.ToString();
                    var status = upstream.StatusCode == HttpStatusCode.Unauthorized ? 503 : (int)upstream.StatusCode;
                    // Redirects are never followed or exposed as successful inference.
                    if (status is >= 300 and < 400)
                        status = 502;
                    await ErrorAsync(context, status, "upstream_error",
                        $"Copilot inference failed (upstream HTTP {(int)upstream.StatusCode}).", cancellationToken);
                    return;
                }
                var mediaType = upstream.Content.Headers.ContentType?.MediaType;
                if ((streaming && mediaType != "text/event-stream") || (!streaming && mediaType != "application/json"))
                    throw new GatewayException("Copilot returned an unexpected response content type.", 502);
                context.Response.StatusCode = (int)upstream.StatusCode;
                context.Response.ContentType = streaming ? "text/event-stream" : "application/json";
                context.Features.Get<IHttpResponseBodyFeature>()?.DisableBuffering();
                await using var source = await upstream.Content.ReadAsStreamAsync(cancellationToken);
                var buffer = ArrayPool<byte>.Shared.Rent(16 * 1024);
                try
                {
                    int count;
                    while ((count = await source.ReadAsync(buffer, cancellationToken)) != 0)
                    {
                        await context.Response.Body.WriteAsync(buffer.AsMemory(0, count), cancellationToken);
                        await context.Response.Body.FlushAsync(cancellationToken);
                    }
                }
                finally { ArrayPool<byte>.Shared.Return(buffer, clearArray: true); }
            }
        }
        finally { capacity.Slots.Release(); }
    }

    private static bool IsContinuation(JsonElement root, bool chatCompletions)
    {
        if (chatCompletions)
            return root.GetProperty("messages").EnumerateArray().Any(message => message.ValueKind == JsonValueKind.Object
                && message.TryGetProperty("role", out var role) && role.ValueKind == JsonValueKind.String
                && role.GetString() is "assistant" or "tool");
        if (root.TryGetProperty("previous_response_id", out var previous) && previous.ValueKind == JsonValueKind.String
            && !string.IsNullOrEmpty(previous.GetString()))
            return true;
        return root.TryGetProperty("input", out var input) && input.ValueKind == JsonValueKind.Array
            && input.EnumerateArray().Any(item => item.ValueKind == JsonValueKind.Object
                && ((item.TryGetProperty("type", out var type) && type.ValueKind == JsonValueKind.String
                        && type.GetString() == "function_call_output")
                    || (item.TryGetProperty("role", out var role) && role.ValueKind == JsonValueKind.String
                        && role.GetString() == "assistant")));
    }

    private static async Task<byte[]> ReadBodyAsync(Stream source, long maximumBytes, CancellationToken cancellationToken)
    {
        using var target = new MemoryStream();
        var buffer = ArrayPool<byte>.Shared.Rent(16 * 1024);
        try
        {
            int count;
            while ((count = await source.ReadAsync(buffer, cancellationToken)) != 0)
            {
                if (target.Length + count > maximumBytes)
                    throw new BadHttpRequestException("Request body limit exceeded.", 413);
                target.Write(buffer, 0, count);
            }
            return target.ToArray();
        }
        finally { ArrayPool<byte>.Shared.Return(buffer, clearArray: true); }
    }

    private static async Task ErrorAsync(HttpContext context, int status, string code, string message, CancellationToken cancellationToken)
    {
        context.Response.StatusCode = status;
        await context.Response.WriteAsJsonAsync(new { error = new { message, type = "gateway_error", code } }, cancellationToken);
    }

    private static async Task FailAsync(HttpContext context, int status, string code, string message, ILogger logger)
    {
        if (!context.Items.ContainsKey("gatewayOutcome"))
            context.Items["gatewayOutcome"] = "gateway_error";
        logger.LogWarning("Request {RequestId} failed: {Code}.", context.TraceIdentifier, code);
        if (context.Response.HasStarted)
        {
            context.Abort();
            return;
        }
        await ErrorAsync(context, status, code, message, context.RequestAborted);
    }

    private sealed class InferenceCapacity(int count) : IDisposable
    {
        public SemaphoreSlim Slots { get; } = new(count, count);
        public void Dispose() => Slots.Dispose();
    }
}
