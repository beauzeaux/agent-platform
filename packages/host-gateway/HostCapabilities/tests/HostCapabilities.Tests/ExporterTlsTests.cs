using System.Net;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Server.Kestrel.Https;
using Microsoft.Extensions.Logging;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class ExporterTlsTests
{
    [TestMethod]
    public async Task PinnedPrivateExportsActualOtlpWithMtlsWithoutInstallingGlobalTrustOrRequiringCrl()
    {
        await using var fixture = await TlsCollectorFixture.StartAsync();
        using (var telemetry = new HostTelemetry(fixture.Configuration()))
        {
            using var activity = telemetry.Activities.StartActivity("test.export");
            activity?.SetTag("host.capability", "test");
        }
        Assert.IsTrue(fixture.TraceRequests > 0);
        Assert.IsTrue(fixture.ClientAuthenticated);
    }

    [TestMethod]
    public async Task PinRemovalOrReplacementRevokesSubsequentExportsEvenWithTheSameHttpClient()
    {
        await using var fixture = await TlsCollectorFixture.StartAsync();
        using var transport = new ExporterTls(fixture.Configuration());
        using var client = transport.CreateClient();
        using var first = await client.GetAsync(fixture.Endpoint);
        Assert.AreEqual(HttpStatusCode.OK, first.StatusCode);
        File.Delete(fixture.PinFile);
        await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(fixture.Endpoint));
        using (var unknown = fixture.Leaf())
            File.WriteAllText(fixture.PinFile, unknown.ExportCertificatePem());
        await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(fixture.Endpoint));
        File.WriteAllText(fixture.PinFile, fixture.Server.ExportCertificatePem());
        using var restored = await client.GetAsync(fixture.Endpoint);
        Assert.AreEqual(HttpStatusCode.OK, restored.StatusCode);
    }

    [TestMethod]
    public async Task LeafPinCannotBypassDnsIssuerOrOnlineRevocationPolicy()
    {
        await using var fixture = await TlsCollectorFixture.StartAsync();
        using (var transport = new ExporterTls(fixture.Configuration()))
        using (var client = transport.CreateClient())
        {
            var wrongName = new UriBuilder(fixture.Endpoint) { Host = "127.0.0.1" }.Uri;
            await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(wrongName));
        }
        using (var otherRoot = TlsCollectorFixture.Root())
        {
            var rootFile = fixture.Directory.Write("wrong-ca.crt", otherRoot.ExportCertificatePem());
            using var transport = new ExporterTls(fixture.Configuration(rootFile: rootFile));
            using var client = transport.CreateClient();
            await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(fixture.Endpoint));
        }
        using (var online = new ExporterTls(fixture.Configuration(trustMode: "online")))
        using (var client = online.CreateClient())
            await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(fixture.Endpoint));
    }

    [TestMethod]
    public async Task ExactPinStillRequiresCurrentValidityAndServerAuthenticationEku()
    {
        await using var fixture = await TlsCollectorFixture.StartAsync();
        using var transport = new ExporterTls(fixture.Configuration());
        foreach (var invalid in new[] { fixture.Leaf(expired: true), fixture.Leaf(serverEku: false) })
        {
            using (invalid)
            {
                File.WriteAllText(fixture.PinFile, invalid.ExportCertificatePem());
                Assert.IsFalse(transport.ValidateCertificate(invalid, null, SslPolicyErrors.RemoteCertificateChainErrors));
            }
        }
    }
}

internal sealed class TlsCollectorFixture : IAsyncDisposable
{
    internal OwnedDirectory Directory { get; } = new();
    private readonly X509Certificate2 issuer = Root();
    private readonly X509Certificate2 client;
    internal X509Certificate2 Server { get; }
    internal string PinFile { get; }
    private readonly string rootFile;
    private readonly string clientFile;
    private readonly WebApplication application;
    internal Uri Endpoint { get; private set; } = null!;
    internal int TraceRequests;
    internal bool ClientAuthenticated;

    private TlsCollectorFixture()
    {
        Server = Leaf();
        client = Leaf(serverEku: false);
        rootFile = Directory.Write("ca.crt", issuer.ExportCertificatePem());
        PinFile = Directory.Write("server.crt", Server.ExportCertificatePem());
        clientFile = Path.Combine(Directory.Path, "client.pfx");
        var bytes = client.Export(X509ContentType.Pfx);
        try { File.WriteAllBytes(clientFile, bytes); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
        var builder = WebApplication.CreateSlimBuilder();
        builder.Configuration.Sources.Clear();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(server =>
        {
            server.Limits.MaxRequestBodySize = 1024 * 1024;
            server.Listen(IPAddress.Loopback, 0, listen => listen.UseHttps(https =>
            {
                https.ServerCertificate = Server;
                https.ClientCertificateMode = ClientCertificateMode.RequireCertificate;
                https.ClientCertificateValidation = (certificate, _, _) =>
                {
                    using var chain = new X509Chain();
                    chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                    chain.ChainPolicy.CustomTrustStore.Add(issuer);
                    chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                    chain.ChainPolicy.ApplicationPolicy.Add(new("1.3.6.1.5.5.7.3.2"));
                    ClientAuthenticated = chain.Build(certificate);
                    return ClientAuthenticated;
                };
            }));
        });
        application = builder.Build();
        application.MapGet("/", () => Results.Ok());
        application.MapPost("/v1/traces", async (HttpContext context) =>
        {
            Assert.AreEqual("application/x-protobuf", context.Request.ContentType);
            Assert.IsNotNull(context.Connection.ClientCertificate);
            await context.Request.Body.CopyToAsync(Stream.Null);
            Interlocked.Increment(ref TraceRequests);
            context.Response.ContentType = "application/x-protobuf";
        });
        application.MapPost("/v1/metrics", async (HttpContext context) =>
        {
            await context.Request.Body.CopyToAsync(Stream.Null);
            context.Response.ContentType = "application/x-protobuf";
        });
    }

    internal TelemetryConfiguration Configuration(string trustMode = "pinned-private", string? rootFile = null) => new()
    {
        OtlpEndpoint = Endpoint.ToString(),
        CaCertificateFile = rootFile ?? this.rootFile,
        ClientCertificateFile = clientFile,
        ServerCertificateFile = PinFile,
        TrustMode = trustMode
    };

    internal static async Task<TlsCollectorFixture> StartAsync()
    {
        var fixture = new TlsCollectorFixture();
        await fixture.application.StartAsync();
        fixture.Endpoint = new UriBuilder(HostApplication.Address(fixture.application)) { Host = "localhost" }.Uri;
        return fixture;
    }

    internal static X509Certificate2 Root()
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=HostCapabilities test " + Guid.NewGuid().ToString("N"),
            key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.KeyCertSign, true));
        request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, false));
        return request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(2));
    }

    internal X509Certificate2 Leaf(bool expired = false, bool serverEku = true)
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=localhost", key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, true));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
            new OidCollection { new(serverEku ? "1.3.6.1.5.5.7.3.1" : "1.3.6.1.5.5.7.3.2") }, true));
        var names = new SubjectAlternativeNameBuilder();
        names.AddDnsName("localhost");
        request.CertificateExtensions.Add(names.Build());
        using var issued = request.Create(issuer, DateTimeOffset.UtcNow.AddHours(expired ? -2 : -1),
            DateTimeOffset.UtcNow.AddHours(expired ? -1 : 1), RandomNumberGenerator.GetBytes(16));
        using var keyed = issued.CopyWithPrivateKey(key);
        var bytes = keyed.Export(X509ContentType.Pfx);
        try { return X509CertificateLoader.LoadPkcs12(bytes, null, X509KeyStorageFlags.UserKeySet | X509KeyStorageFlags.Exportable); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }

    public async ValueTask DisposeAsync()
    {
        await application.StopAsync();
        await application.DisposeAsync();
        client.Dispose();
        Server.Dispose();
        issuer.Dispose();
        Directory.Dispose();
    }
}
