using System.Net.Http.Headers;
using LocalLlmGateway;

namespace HostCapabilities;

internal sealed class LlmModule(HostConfiguration configuration, ActorAdmission admission, HostTelemetry telemetry)
    : ICapabilityModule
{
    public string Name => "llm";
    public bool Enabled => configuration.Llm.Enabled;
    public CapabilityState State { get; } = new();

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var options = configuration.Llm.Options;
        using var http = UpstreamProtocol.CreateHttpClient();
        var key = await GatewayConfiguration.LoadGatewayKeyAsync(options, cancellationToken, _ => null);
        var credential = await new CredentialStore(options.DataDir).ReadAsync(cancellationToken);
        if (ActorAdmission.MatchesSecret(key, credential))
            throw new InvalidOperationException("The gateway key must be separate from the host credential.");
        using var tokens = new CopilotTokenProvider(http, credential);
        var client = new CopilotClient(http, tokens, options.AccountType);
        await using var app = GatewayServer.Create(options, key, client, gateway => Configure(gateway, key));
        await app.StartAsync(cancellationToken);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                    deadline.CancelAfter(TimeSpan.FromSeconds(60));
                    await app.Services.GetRequiredService<ModelCatalog>().GetAsync(deadline.Token);
                    State.Ready();
                }
                catch (Exception ex) when (ex is GatewayException or HttpRequestException or IOException or OperationCanceledException)
                {
                    State.Unavailable("authorization_or_catalog");
                }
                await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken);
            }
        }
        finally
        {
            State.Stopping();
            using var drain = new CancellationTokenSource(TimeSpan.FromSeconds(configuration.ShutdownSeconds));
            await app.StopAsync(drain.Token);
        }
    }

    internal void Configure(WebApplication app, string key)
    {
        app.MapGet("/health/live", () => Results.Json(new { status = "live" }));
        app.MapGet("/health/ready", () => Results.Json(State.Health, statusCode: State.IsReady ? 200 : 503));
        app.Use(async (context, next) =>
        {
            var operation = GatewayServer.GetRoute(context.Request.Path) switch
            {
                "/v1/models" => "models",
                "/v1/chat/completions" => "chat_completions",
                "/v1/responses" => "responses",
                _ => "other"
            };
            if (operation == "other")
            {
                await next(context);
                return;
            }
            using var observation = telemetry.Begin("llm", operation, context);
            if (!await AdmitAsync(context, key, observation))
            {
                context.Response.StatusCode = 401;
                context.Response.Headers.WWWAuthenticate = "Bearer";
                observation.GatewayStatus = 401;
                observation.Outcome = "denied";
                return;
            }
            ProtocolObservation? protocol = null;
            var original = context.Response.Body;
            context.Response.Body = new ObservedWriteStream(original, bytes =>
            {
                observation.FirstByte();
                var streaming = context.Response.ContentType?.StartsWith("text/event-stream", StringComparison.Ordinal) == true;
                if (streaming) observation.Stream();
                protocol ??= new(streaming, chatCompletions: operation == "chat_completions");
                protocol.Observe(bytes.Span);
            });
            try
            {
                await next(context);
                observation.Outcome = context.RequestAborted.IsCancellationRequested ? "cancelled"
                    : context.Response.StatusCode >= 400 ? "gateway_error"
                    : protocol?.Complete() ?? "unobserved";
                observation.UpstreamStatus = context.Items.TryGetValue("gatewayUpstreamStatus", out var status) ? (int)status! : 0;
            }
            catch (OperationCanceledException)
            {
                observation.Outcome = context.RequestAborted.IsCancellationRequested ? "cancelled" : "timeout";
                throw;
            }
            finally
            {
                if (context.Items.TryGetValue("gatewayModel", out var model) && model is string selected)
                    observation.Model(selected);
                if (context.Items.TryGetValue("gatewayOutcome", out var outcome))
                    observation.Outcome = (string)outcome!;
                observation.GatewayStatus = context.Response.StatusCode;
                context.Response.Body = original;
            }
        });
    }

    private async Task<bool> AdmitAsync(HttpContext context, string key, HostTelemetry.RequestObservation observation)
    {
        if (!AuthenticationHeaderValue.TryParse(context.Request.Headers.Authorization, out var header)
            || !header.Scheme.Equals("Bearer", StringComparison.OrdinalIgnoreCase) || header.Parameter is not { } credential)
            return false;
        if (configuration.ActorJwt.AllowLegacyKeys && ActorAdmission.MatchesSecret(credential, key))
            return true;
        var identity = await admission.ValidateAsync(credential);
        if (identity is null) return false;
        observation.Actor(identity);
        // The reused gateway still authenticates its own private, distinct gateway credential.
        context.Request.Headers.Authorization = "Bearer " + key;
        return true;
    }
}
