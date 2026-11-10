using System.Net;
using System.Security.Cryptography.X509Certificates;
using AdoDevProxy;
using HostCapabilities.NativeAdo;
using Microsoft.AspNetCore.Http.Features;

namespace HostCapabilities;

internal sealed class NativeAdoCapability(HostConfiguration configuration, HostTelemetry telemetry) : ICapabilityModule
{
    public string Name => "native-ado";
    public bool Enabled => configuration.NativeAdo.Enabled;
    public CapabilityState State { get; } = new();

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var options = configuration.NativeAdo;
        var roots = new List<X509Certificate2>();
        var intermediates = new List<X509Certificate2>();
        try
        {
            foreach (var path in options.GatewayRootFiles) roots.Add(X509CertificateLoader.LoadCertificateFromFile(path));
            foreach (var path in options.GatewayIntermediateFiles) intermediates.Add(X509CertificateLoader.LoadCertificateFromFile(path));
            if (roots.Concat(intermediates).Any(certificate =>
                certificate.PublicKey.Oid.Value is "1.3.101.112" or "1.3.101.113"
                || certificate.SignatureAlgorithm.Value is "1.3.101.112" or "1.3.101.113"))
            {
                State.Unavailable("unsupported_gateway_tls_issuer");
                throw new InvalidOperationException("The gateway issuer is unsupported by Windows TLS.");
            }
            var settings = ProxySettings.Load(options.ConfigurationFile);
            var state = new UserState(options.StateDirectory);
            using var tokens = new WamTokenProvider(settings, state);
            using var serving = LoadServingCertificate(options);
            using var trust = new GatewayCertificateTrust(options.GatewaySpiffeId, roots, intermediates,
                shortLivedSpiffePolicy: options.TrustMode == "short-lived-spiffe"
                    ? new() { MaxLeafLifetime = TimeSpan.FromHours(options.MaxLeafLifetimeHours) } : null);
            var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
            {
                Args = [],
                ApplicationName = typeof(NativeAdoCapability).Assembly.FullName,
                ContentRootPath = AppContext.BaseDirectory
            });
            builder.Configuration.Sources.Clear();
            builder.Logging.ClearProviders();
            builder.WebHost.ConfigureKestrel(server =>
            {
                server.ListenNativeAdo(IPAddress.Parse(options.ListenAddress), options.Port, serving, trust);
                server.Limits.MaxConcurrentConnections = 32;
                server.Limits.Http2.MaxStreamsPerConnection = 16;
                server.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(15);
            });
            builder.Services.AddNativeAdo(new()
            {
                MaxConcurrency = options.MaxConcurrency,
                RequestTimeout = TimeSpan.FromSeconds(options.RequestTimeoutSeconds)
            }, trust, (actor, uri, _) => ValueTask.FromResult(
                options.Grant(actor, uri) is { } grant ? new CredentialGrant(grant.Identity) : null),
                async (identity, cancellation) => await tokens.GetTokenWithMinimumLifetimeAsync(identity,
                    TimeSpan.FromSeconds(options.MinimumTokenLifetimeSeconds), cancellation));
            await using var application = builder.Build();
            application.Use(async (context, next) =>
            {
                using var observation = telemetry.Begin("native-ado", "fetch_secret", context);
                try
                {
                    await next(context);
                    var trailers = context.Features.Get<IHttpResponseTrailersFeature>()?.Trailers;
                    var code = trailers is not null && int.TryParse(trailers["grpc-status"], out var grpcStatus) ? grpcStatus : -1;
                    observation.Outcome = code switch
                    {
                        0 => "success",
                        1 => "cancelled",
                        4 => "timeout",
                        7 or 16 => "denied",
                        8 => "busy",
                        _ => "protocol_error"
                    };
                }
                finally { observation.GatewayStatus = context.Response.StatusCode; }
            });
            application.MapNativeAdo();
            await application.StartAsync(cancellationToken);
            try
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    try
                    {
                        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                        deadline.CancelAfter(TimeSpan.FromSeconds(30));
                        foreach (var identity in options.Grants.Select(grant => grant.Identity).Distinct(StringComparer.OrdinalIgnoreCase))
                            _ = await tokens.GetTokenWithMinimumLifetimeAsync(identity,
                                TimeSpan.FromSeconds(options.MinimumTokenLifetimeSeconds), deadline.Token);
                        State.Ready();
                    }
                    catch (Exception ex) when (ex is Microsoft.Identity.Client.MsalException or InvalidOperationException
                        or OperationCanceledException)
                    {
                        State.Unavailable("host_sign_in_or_token_lifetime");
                    }
                    await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken);
                }
            }
            finally
            {
                State.Stopping();
                using var drain = new CancellationTokenSource(TimeSpan.FromSeconds(configuration.ShutdownSeconds));
                await application.StopAsync(drain.Token);
            }
        }
        finally
        {
            foreach (var certificate in roots.Concat(intermediates)) certificate.Dispose();
        }
    }

    private static X509Certificate2 LoadServingCertificate(NativeAdoConfiguration options)
    {
        var password = options.ServingCertificatePasswordFile is { } path ? File.ReadAllText(path).TrimEnd('\r', '\n') : null;
        var certificate = X509CertificateLoader.LoadPkcs12FromFile(options.ServingCertificateFile, password,
            X509KeyStorageFlags.UserKeySet);
        if (!certificate.HasPrivateKey || certificate.NotAfter.ToUniversalTime() <= DateTime.UtcNow
            || certificate.NotBefore.ToUniversalTime() > DateTime.UtcNow)
        {
            certificate.Dispose();
            throw new InvalidOperationException("The native ADO serving certificate is unusable.");
        }
        return certificate;
    }
}
