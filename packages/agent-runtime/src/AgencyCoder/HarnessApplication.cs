using Microsoft.AspNetCore.Http.Json;
using Microsoft.Extensions.Options;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenTelemetry;
using OpenTelemetry.Context.Propagation;
using System.Diagnostics;

namespace AgencyCoder;

public static class HarnessApplication
{
    public static WebApplication Build(string[] args)
    {
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = args,
            ContentRootPath = AppContext.BaseDirectory,
        });
        builder.Configuration.Sources.Clear();
        builder.Configuration.AddEnvironmentVariables();
        var nativePath = builder.Configuration["AGENT_COMPILED_CONFIG_PATH"]
            ?? throw new InvalidOperationException("missing_agent_compiled_config_path");
        builder.Configuration.AddJsonFile(nativePath, optional: false, reloadOnChange: false);
        builder.Configuration.AddEnvironmentVariables();
        builder.Services.AddOptions<HarnessOptions>()
            .Bind(builder.Configuration)
            .ValidateOnStart();
        builder.Services.AddSingleton<IValidateOptions<HarnessOptions>, HarnessOptionsValidator>();
        builder.Services.AddOptions<NativeAgentOptions>()
            .Bind(builder.Configuration.GetSection("agent"))
            .ValidateOnStart();
        builder.Services.AddSingleton<IValidateOptions<NativeAgentOptions>, NativeOptionsValidator>();
        builder.Services.Configure<JsonOptions>(options =>
        {
            options.SerializerOptions.PropertyNameCaseInsensitive = true;
            options.SerializerOptions.UnmappedMemberHandling =
                System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow;
        });
        builder.WebHost.ConfigureKestrel(server => server.Limits.MaxRequestBodySize = 20_000);
        builder.Services.AddSingleton(provider =>
        {
            ThrowIfUnsupportedNativeHostConfiguration(builder.Configuration);
            return NativeHarnessConfig.Create(
                provider.GetRequiredService<IOptions<NativeAgentOptions>>().Value,
                builder.Configuration["OPENAI_API_KEY"]!,
                builder.Configuration);
        });
        builder.Services.AddHttpClient<AgencyPluginResolver>(client => client.Timeout = TimeSpan.FromMinutes(4));
        builder.Services.AddSingleton<TaskJournal>();
        builder.Services.AddSingleton<WorkspaceManager>();
        builder.Services.AddSingleton<CopilotExecutor>();
        builder.Services.AddSingleton<HarnessOrchestrator>();
        builder.Services.AddHostedService<HarnessInitialization>();
        Sdk.SetDefaultTextMapPropagator(new CompositeTextMapPropagator(
            [new TraceContextPropagator(), new BaggagePropagator()]));
        builder.Services.AddOpenTelemetry()
            .ConfigureResource(resource => resource.AddService("agency-coder"))
            .WithTracing(tracing => tracing
                .AddSource(AgencyCoderTracing.Name)
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddOtlpExporter());
        var app = builder.Build();
        var options = app.Services.GetRequiredService<IOptions<HarnessOptions>>().Value;
        app.Urls.Add($"http://127.0.0.1:{options.Port}");
        Map(app);
        return app;
    }

    public static async Task<int> RunAsync(string[] args)
    {
        if (args is ["--help"])
        {
            Console.WriteLine("Generic native Kagent coding adapter. Configure AGENT_* and AGENCY_* environment values.");
            return 0;
        }
        await Build(args).RunAsync();
        return 0;
    }

    private static void Map(WebApplication app)
    {
        app.MapGet("/readyz", () => Results.Json(new { status = "ready" }));
        app.MapGet("/status", (HarnessOrchestrator orchestrator) => Results.Json(orchestrator.Status()));
        app.MapGet("/tasks/{id}", (string id, HarnessOrchestrator orchestrator) =>
            orchestrator.Get(id) is { } task ? Results.Json(task) : Results.NotFound(new { error = "task_not_found" }));
        app.MapPost("/tasks/{id}/cancel", async (string id, HarnessOrchestrator orchestrator,
            CancellationToken cancellationToken) =>
        {
            await orchestrator.CancelAsync(id, cancellationToken);
            return Results.Json(new { id, status = orchestrator.Get(id)?.Status }, statusCode: 202);
        });
        app.MapPost("/tasks", async (CodingTaskRequest request, HarnessOrchestrator orchestrator,
            CancellationToken cancellationToken) =>
        {
            var result = await orchestrator.SubmitAsync(request, request.RefreshPlugins, cancellationToken);
            return Results.Json(result.Task ?? (object)new { error = result.Error }, statusCode: result.StatusCode);
        });
    }

    private static void ThrowIfUnsupportedNativeHostConfiguration(IConfiguration configuration)
    {
        if (configuration["AGENT_NATIVE_CONFIG"] != "true")
            throw new InvalidOperationException("native_config_required");
        if (configuration["NODE_TLS_REJECT_UNAUTHORIZED"] == "0")
            throw new InvalidOperationException("tls_verification_disabled");
        if (configuration["NATIVE_MEMORY_ENABLED"] == "true"
            || configuration["NATIVE_MEMORY_CONFIG"] is { Length: > 0 }
            || configuration["NATIVE_MEMORY_CONFIG_JSON"] is { Length: > 0 })
            throw new InvalidOperationException("native_memory_requires_explicit_adapter");
        if (configuration["SESSION_ARCHIVE_EXPORT_ENABLED"] == "true")
            throw new InvalidOperationException("remote_archive_export_requires_explicit_adapter");
        var sessionId = configuration["AGENT_SESSION_ID"]
            ?? throw new InvalidOperationException("missing_agent_session_id");
        var identityPath = configuration["AGENT_RUNTIME_IDENTITY_PATH"];
        if (identityPath is not null
            && !File.ReadAllText(identityPath).Trim().Equals("session-" + sessionId, StringComparison.Ordinal))
            throw new InvalidOperationException("runtime_identity_mismatch");
        var placeholder = configuration["OPENAI_API_KEY"]
            ?? throw new InvalidOperationException("missing_openai_api_key");
        if (placeholder != "kagent-credential-injected")
            throw new InvalidOperationException("native_projected_credential_required");
    }

}

public static class AgencyCoderTracing
{
    public const string Name = "AgencyCoder";
    public static readonly ActivitySource Source = new(Name);
}

public sealed class HarnessInitialization(HarnessOrchestrator orchestrator) : IHostedService
{
    public Task StartAsync(CancellationToken cancellationToken) => orchestrator.InitializeAsync(cancellationToken);
    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
