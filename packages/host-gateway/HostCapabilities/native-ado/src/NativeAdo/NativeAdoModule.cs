using System.Net;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using Grpc.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.AspNetCore.Server.Kestrel.Https;
using Microsoft.Extensions.DependencyInjection;
using Substrate.Credprovider.V1;

namespace HostCapabilities.NativeAdo;

public static class NativeAdoModule
{
    public static IServiceCollection AddNativeAdo(this IServiceCollection services, NativeAdoOptions options,
        GatewayCertificateTrust trust, AuthorizeCredentialAsync authorize, AcquireAdoTokenAsync acquireToken)
    {
        options.Validate();
        services.AddSingleton(_ => new NativeAdoBroker(options, trust, authorize, acquireToken));
        services.AddGrpc().AddServiceOptions<FetchSecretService>(o =>
        {
            o.EnableDetailedErrors = false;
            o.MaxReceiveMessageSize = 8192;
            o.MaxSendMessageSize = 32768;
        });
        return services;
    }

    public static void ListenNativeAdo(this KestrelServerOptions kestrel, IPAddress address, int port,
        X509Certificate2 servingCertificate, GatewayCertificateTrust trust)
    {
        kestrel.Listen(address, port, listener =>
        {
            listener.Protocols = HttpProtocols.Http2;
            listener.UseHttps(https =>
            {
                https.ServerCertificate = servingCertificate;
                https.SslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13;
                https.ClientCertificateMode = ClientCertificateMode.RequireCertificate;
                https.HandshakeTimeout = TimeSpan.FromSeconds(10);
                https.ClientCertificateValidation = (certificate, chain, _) => trust.IsTrusted(certificate, chain);
            });
        });
    }

    public static IEndpointConventionBuilder MapNativeAdo(this IEndpointRouteBuilder endpoints) =>
        endpoints.MapGrpcService<FetchSecretService>();
}

public sealed class FetchSecretService(NativeAdoBroker broker) : CredentialProvider.CredentialProviderBase
{
    public override Task<FetchSecretResponse> FetchSecret(FetchSecretRequest request, ServerCallContext context)
    {
        var http = context.GetHttpContext();
        return broker.FetchAsync(http.Request.IsHttps ? http.Connection.ClientCertificate : null,
            request.Uri, request.ActorSpiffeId, context.CancellationToken);
    }
}
