using System.Net;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;

namespace HostCapabilities;

public static class HostApplication
{
    public static WebApplication Create(HostConfiguration configuration,
        Func<HostConfiguration, ActorAdmission, HostTelemetry, IEnumerable<ICapabilityModule>>? modulesFactory = null)
    {
        var admission = new ActorAdmission(configuration.ActorJwt);
        var modules = new List<ICapabilityModule>();
        var telemetry = new HostTelemetry(configuration.Telemetry, modules);
        modules.AddRange(modulesFactory?.Invoke(configuration, admission, telemetry) ??
        [new AdoModule(configuration, telemetry), new AgencyModule(configuration, admission, telemetry),
            new LlmModule(configuration, admission, telemetry), new NativeAdoCapability(configuration, telemetry)]);
        var builder = CreateBuilder("127.0.0.1", configuration.ControlPort, 1024);
        builder.Services.AddSingleton(telemetry);
        builder.Services.AddSingleton<IHostedService>(services => new CapabilitySupervisor(modules,
            services.GetRequiredService<ILogger<CapabilitySupervisor>>()));
        builder.Services.Configure<HostOptions>(options => options.ShutdownTimeout = TimeSpan.FromSeconds(configuration.ShutdownSeconds));
        var app = builder.Build();
        app.Lifetime.ApplicationStopped.Register(telemetry.Dispose);
        app.MapGet("/health/live", () => Results.Json(new { status = "live" }));
        app.MapGet("/health/ready", () => Results.Json(
            modules.ToDictionary(module => module.Name, module => module.State.Health),
            statusCode: modules.Where(module => module.Enabled).All(module => module.State.IsReady) ? 200 : 503));
        app.MapGet("/health/{capability}/ready", (string capability) =>
        {
            var module = modules.FirstOrDefault(module => module.Name == capability);
            return module is null ? Results.NotFound() : Results.Json(module.State.Health,
                statusCode: module.State.IsReady ? 200 : 503);
        });
        // Only loopback control health is exposed; sign-in and token-returning APIs are intentionally absent.
        return app;
    }

    internal static WebApplication CreateListener(string address, int port, long maxRequestBytes = 1024) =>
        CreateBuilder(address, port, maxRequestBytes).Build();

    private static WebApplicationBuilder CreateBuilder(string address, int port, long maxRequestBytes)
    {
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = [],
            ApplicationName = typeof(HostApplication).Assembly.FullName,
            ContentRootPath = AppContext.BaseDirectory
        });
        builder.Configuration.Sources.Clear();
        builder.Logging.ClearProviders();
        builder.Logging.AddSimpleConsole(options => options.SingleLine = true);
        builder.Logging.AddFilter((category, level) => category?.StartsWith("HostCapabilities.", StringComparison.Ordinal) == true
            && level >= LogLevel.Information);
        builder.WebHost.ConfigureKestrel(server =>
        {
            server.Listen(IPAddress.Parse(address), port);
            server.Limits.MaxRequestBodySize = maxRequestBytes;
            server.Limits.MaxConcurrentConnections = 128;
            server.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(15);
        });
        return builder;
    }

    internal static Uri Address(WebApplication application) => new(application.Services.GetRequiredService<IServer>()
        .Features.Get<IServerAddressesFeature>()!.Addresses.Single());
}
