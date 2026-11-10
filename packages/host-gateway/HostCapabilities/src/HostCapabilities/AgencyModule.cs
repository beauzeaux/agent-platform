using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

namespace HostCapabilities;

internal sealed class AgencyModule(HostConfiguration configuration, ActorAdmission admission, HostTelemetry telemetry)
    : ICapabilityModule
{
    public string Name => "agency";
    public bool Enabled => configuration.Agency.Enabled;
    public CapabilityState State { get; } = new();

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var options = configuration.Agency;
        using var http = CreateBrokerClient(options);
        using var imdsTokens = configuration.Imds.Enabled ? new WamImdsTokenProvider(configuration.Imds) : null;
        var imds = imdsTokens is null ? null : new ImdsEndpoint(configuration.Imds,
            new ImdsActorAuthorizer(admission, options.BrokerConfigurationFile), imdsTokens, telemetry);
        await using var app = CreateFront(options, http, imds);
        using var child = StartBroker(options);
        var previousSessions = new int[3];
        try
        {
            await app.StartAsync(cancellationToken);
            while (!cancellationToken.IsCancellationRequested)
            {
                if (child.HasExited) throw new InvalidOperationException("The supervised Agency broker exited.");
                try
                {
                    using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                    deadline.CancelAfter(TimeSpan.FromSeconds(3));
                    using var health = await http.GetAsync("/health", deadline.Token);
                    using var document = JsonDocument.Parse(await health.Content.ReadAsByteArrayAsync(deadline.Token));
                    if (!health.IsSuccessStatusCode
                        || document.RootElement.GetProperty("brokerPid").GetInt32() != child.Id
                        || !document.RootElement.GetProperty("ready").GetBoolean())
                        State.Unavailable("broker_not_ready");
                    else
                    {
                        if (imds is not null)
                            await imds.WarmAsync(deadline.Token);
                        State.Ready();
                        telemetry.Sessions(document.RootElement.GetProperty("sessions").GetInt32());
                        var counters = new[] { "sessionsOpened", "sessionsClosed", "sessionsExpired" };
                        var outcomes = new[] { "opened", "closed", "expired" };
                        for (var index = 0; index < counters.Length; index++)
                        {
                            var current = document.RootElement.GetProperty(counters[index]).GetInt32();
                            for (var count = previousSessions[index]; count < current; count++)
                                telemetry.Session(outcomes[index]);
                            previousSessions[index] = current;
                        }
                    }
                }
                catch (Exception ex) when (ex is HttpRequestException or IOException or JsonException or OperationCanceledException)
                {
                    State.Unavailable("broker_unavailable");
                }
                await Task.Delay(TimeSpan.FromSeconds(3), cancellationToken);
            }
        }
        finally
        {
            State.Stopping();
            telemetry.Sessions(0);
            using var drain = new CancellationTokenSource(TimeSpan.FromSeconds(configuration.ShutdownSeconds));
            try { await app.StopAsync(drain.Token); }
            finally
            {
                if (!child.HasExited)
                {
                    child.Kill(entireProcessTree: true);
                    await child.WaitForExitAsync(CancellationToken.None);
                }
            }
        }
    }

    private static Process StartBroker(AgencyConfiguration options)
    {
        Directory.CreateDirectory(options.StateDirectory);
        var start = new ProcessStartInfo(options.PythonExecutable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = options.StateDirectory,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in new[]
        {
            options.BrokerScript, "--config", options.BrokerConfigurationFile, "--state", options.StateDirectory,
            "--listen-address", "127.0.0.1", "--port", options.InternalPort.ToString(System.Globalization.CultureInfo.InvariantCulture),
            "--profiles", options.ProfilesFile, "--agency", options.AgencyExecutable,
            "--global-config", options.GlobalConfigurationFile, "--parent-pid", Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture)
        })
            start.ArgumentList.Add(argument);
        var child = Process.Start(start) ?? throw new InvalidOperationException("Cannot start the Agency broker.");
        child.OutputDataReceived += (_, _) => { };
        child.ErrorDataReceived += (_, _) => { };
        child.BeginOutputReadLine();
        child.BeginErrorReadLine();
        return child;
    }

    internal static HttpClient CreateBrokerClient(AgencyConfiguration options) =>
        new(new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false, ActivityHeadersPropagator = null })
        {
            BaseAddress = new Uri($"http://127.0.0.1:{options.InternalPort}"),
            Timeout = Timeout.InfiniteTimeSpan
        };

    internal WebApplication CreateFront(AgencyConfiguration options, HttpClient http, ImdsEndpoint? imds = null)
    {
        var app = HostApplication.CreateListener(options.ListenAddress, options.Port, 8_000_000);
        var capacity = new SemaphoreSlim(options.MaxConcurrentRequests);
        var plugins = new SemaphoreSlim(options.MaxConcurrentPlugins);
        var cancellations = new SemaphoreSlim(4);
        var metadata = new ToolMetadata(options.ProfilesFile);
        app.Lifetime.ApplicationStopped.Register(() => { capacity.Dispose(); plugins.Dispose(); cancellations.Dispose(); });
        app.MapGet("/health/live", () => Results.Json(new { status = "live" }));
        app.MapGet("/health/ready", () => Results.Json(State.Health, statusCode: State.IsReady ? 200 : 503));
        if (imds is not null)
            app.MapGet("/metadata/identity/oauth2/token", imds.HandleAsync);
        app.MapMethods("/agency/{operation}", ["DELETE", "POST"], async (HttpContext context, string operation) =>
        {
            if (operation is not ("mcp" or "plugins" or "policy"))
            {
                context.Response.StatusCode = 404;
                return;
            }
            using var observation = telemetry.Begin("agency", operation, context);
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
            deadline.CancelAfter(TimeSpan.FromSeconds(options.RequestTimeoutSeconds));
            var slots = operation == "plugins" ? plugins : capacity;
            var acquired = false;
            ToolCall tool = default;
            HttpRequestMessage? cancelRequest = null;
            try
            {
                if (context.Request.Headers.ContainsKey("Origin"))
                {
                    context.Response.StatusCode = 403;
                    observation.Outcome = "denied";
                    return;
                }
                // Reload the authoritative actor binding for every request, including revocation and profile changes.
                var credentials = new BrokerCredentials(options.BrokerConfigurationFile);
                var identity = await AdmitAsync(context, credentials);
                if (identity is null)
                {
                    context.Response.StatusCode = 401;
                    observation.Outcome = "denied";
                    return;
                }
                observation.Actor(identity);
                byte[]? body = null;
                if (operation == "mcp" && context.Request.ContentLength is >= 0 and <= 4096)
                {
                    body = await ReadBoundedAsync(context.Request.Body, 4096, deadline.Token);
                    tool = metadata.Read(body);
                }
                var cancellationLane = operation == "mcp"
                    && (context.Request.Method == "DELETE" || tool.Method == "notifications/cancelled");
                if (cancellationLane) slots = cancellations;
                acquired = await slots.WaitAsync(0, deadline.Token);
                if (!acquired)
                {
                    context.Response.StatusCode = 429;
                    observation.Outcome = "busy";
                    return;
                }
                body ??= await ReadBoundedAsync(context.Request.Body, 8_000_000, deadline.Token);
                tool = operation == "mcp" ? metadata.Read(body) : default;
                cancelRequest = CreateCancellation(context, body, tool, credentials, identity);
                using var request = new HttpRequestMessage(new HttpMethod(context.Request.Method), "/agency/" + operation);
                request.Content = new ByteArrayContent(body);
                request.Content.Headers.ContentType = new("application/json");
                request.Headers.Add("X-Agency-Bridge-Key", credentials.BridgeKey);
                request.Headers.Add("X-Agency-Actor-Uid", identity.Uid);
                request.Headers.Add("X-Agency-Actor", identity.Subject);
                if (cancellationLane) request.Headers.Add("X-Host-Cancellation", "1");
                if (context.Request.Headers.TryGetValue("Mcp-Session-Id", out var session) && session.ToString().Length <= 256)
                    request.Headers.Add("Mcp-Session-Id", session.ToString());
                if (Activity.Current is { } activity)
                    request.Headers.Add("traceparent", activity.Id);
                using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
                observation.UpstreamStatus = (int)response.StatusCode;
                context.Response.StatusCode = (int)response.StatusCode;
                context.Response.ContentType = "application/json";
                context.Response.Headers.CacheControl = "no-store";
                context.Response.Headers.XContentTypeOptions = "nosniff";
                if (response.Headers.TryGetValues("Mcp-Session-Id", out var sessions))
                    context.Response.Headers["Mcp-Session-Id"] = sessions.First();
                var protocol = new ProtocolObservation(false, 1024 * 1024);
                await using var source = await response.Content.ReadAsStreamAsync(deadline.Token);
                var buffer = new byte[16 * 1024];
                int count;
                long total = 0;
                while ((count = await source.ReadAsync(buffer, deadline.Token)) > 0)
                {
                    total += count;
                    if (total > 16_000_000)
                        throw new IOException("Broker response exceeded its bound.");
                    observation.FirstByte();
                    protocol.Observe(buffer.AsSpan(0, count));
                    await context.Response.Body.WriteAsync(buffer.AsMemory(0, count), deadline.Token);
                    await context.Response.Body.FlushAsync(deadline.Token);
                }
                observation.Outcome = !response.IsSuccessStatusCode ? "gateway_error"
                    : response.StatusCode == HttpStatusCode.Accepted || context.Request.Method == "DELETE" ? "success"
                    : protocol.Complete();
            }
            catch (OperationCanceledException)
            {
                observation.Outcome = context.RequestAborted.IsCancellationRequested ? "cancelled" : "timeout";
                if (cancelRequest is not null)
                    await CancelBrokerAsync(http, cancelRequest);
                await SafeErrorAsync(context, 504);
            }
            catch (JsonException)
            {
                observation.Outcome = "invalid_json";
                await SafeErrorAsync(context, 400);
            }
            catch (BadHttpRequestException)
            {
                observation.Outcome = "request_limit";
                await SafeErrorAsync(context, 413);
            }
            catch (Exception ex) when (ex is HttpRequestException or IOException or InvalidOperationException
                or UnauthorizedAccessException or KeyNotFoundException)
            {
                observation.Outcome = "unavailable";
                await SafeErrorAsync(context, 503);
            }
            finally
            {
                observation.GatewayStatus = context.Response.StatusCode;
                cancelRequest?.Dispose();
                if (tool.Outer is not null)
                {
                    telemetry.Tool("outer", "gateway", tool.Outer, observation.Outcome);
                    if (tool.Tool is not null)
                        telemetry.Tool("nested", tool.Toolset!, tool.Tool, observation.Outcome);
                }
                if (acquired) slots.Release();
            }
        });
        return app;
    }

    private async Task<ActorIdentity?> AdmitAsync(HttpContext context, BrokerCredentials credentials)
    {
        if (AuthenticationHeaderValue.TryParse(context.Request.Headers.Authorization, out var header)
            && header.Scheme.Equals("Bearer", StringComparison.OrdinalIgnoreCase) && header.Parameter is { } bearer)
        {
            var identity = await admission.ValidateAsync(bearer);
            return identity is not null && credentials.Matches(identity) ? identity : null;
        }
        if (!configuration.ActorJwt.AllowLegacyKeys
            || !ActorAdmission.MatchesSecret(context.Request.Headers["X-Agency-Bridge-Key"].ToString(), credentials.BridgeKey))
            return null;
        var uid = context.Request.Headers["X-Agency-Actor-Uid"].ToString();
        return credentials.Actors.TryGetValue(uid, out var actor)
            && context.Request.Headers["X-Agency-Actor"].ToString() == actor.Subject
            ? new(uid, actor.Subject, actor.Profile) : null;
    }

    private static async Task<byte[]> ReadBoundedAsync(Stream stream, int maximum, CancellationToken cancellationToken)
    {
        using var buffer = new MemoryStream();
        var block = new byte[16 * 1024];
        int count;
        while ((count = await stream.ReadAsync(block, cancellationToken)) > 0)
        {
            if (buffer.Length + count > maximum)
                throw new BadHttpRequestException("Request exceeded its bound.", 413);
            buffer.Write(block, 0, count);
        }
        return buffer.ToArray();
    }

    private static HttpRequestMessage? CreateCancellation(HttpContext context, byte[] body, ToolCall tool,
            BrokerCredentials credentials, ActorIdentity identity)
    {
        if (tool.Method != "tools/call" || !context.Request.Headers.TryGetValue("Mcp-Session-Id", out var session)
            || session.ToString().Length is < 1 or > 256)
            return null;
        using var document = JsonDocument.Parse(body);
        if (!document.RootElement.TryGetProperty("id", out var id)
            || id.ValueKind is not (JsonValueKind.String or JsonValueKind.Number) || id.GetRawText().Length > 256)
            return null;
        var request = new HttpRequestMessage(HttpMethod.Post, "/agency/mcp")
        {
            Content = new ByteArrayContent(JsonSerializer.SerializeToUtf8Bytes(new
            { jsonrpc = "2.0", method = "notifications/cancelled", @params = new { requestId = id.Clone() } }))
        };
        request.Content.Headers.ContentType = new("application/json");
        request.Headers.Add("Mcp-Session-Id", session.ToString());
        request.Headers.Add("X-Agency-Bridge-Key", credentials.BridgeKey);
        request.Headers.Add("X-Agency-Actor-Uid", identity.Uid);
        request.Headers.Add("X-Agency-Actor", identity.Subject);
        request.Headers.Add("X-Host-Cancellation", "1");
        return request;
    }

    private static async Task CancelBrokerAsync(HttpClient client, HttpRequestMessage request)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        try { using var response = await client.SendAsync(request, deadline.Token); }
        catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException or IOException) { }
    }

    private static Task SafeErrorAsync(HttpContext context, int status)
    {
        if (context.Response.HasStarted || context.RequestAborted.IsCancellationRequested)
        {
            context.Abort();
            return Task.CompletedTask;
        }
        context.Response.StatusCode = status;
        return context.Response.WriteAsJsonAsync(new { error = "capability_unavailable" });
    }
}

internal readonly record struct ToolCall(string? Method, string? Outer, string? Toolset, string? Tool);

internal sealed class ToolMetadata
{
    private readonly HashSet<(string Toolset, string Tool)> approved = [];
    private static readonly HashSet<string> Outer = ["call_tool", "get_tool_schema", "get_toolset_info",
        "get_toolsets", "list_toolsets", "load_toolset", "search_tools"];

    public ToolMetadata(string profilesFile)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(profilesFile));
        foreach (var profile in document.RootElement.EnumerateObject())
            foreach (var toolset in profile.Value.GetProperty("toolsets").EnumerateObject())
                foreach (var tool in toolset.Value.GetProperty("tools").EnumerateArray())
                    approved.Add((toolset.Name, tool.GetString()!));
    }

    public ToolCall Read(byte[] body)
    {
        if (body.Length == 0) return default;
        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
            return default;
        var method = String(root, "method");
        if (method != "tools/call" || !root.TryGetProperty("params", out var parameters))
            return new(method, null, null, null);
        var outer = String(parameters, "name");
        outer = outer is not null && Outer.Contains(outer) ? outer : "other";
        if (outer != "call_tool" || !parameters.TryGetProperty("arguments", out var arguments))
            return new(method, outer, null, null);
        var toolset = String(arguments, "toolset_id")?.Split("::", 2)[0] ?? "other";
        var tool = String(arguments, "tool_name") ?? "other";
        return approved.Contains((toolset, tool))
            ? new(method, outer, toolset, tool) : new(method, outer, "other", "other");
    }

    private static string? String(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
}
