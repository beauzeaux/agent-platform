using System.Net;
using System.Diagnostics;
using System.Security.Cryptography.X509Certificates;
using System.Security.Authentication;
using HostCapabilities.NativeAdo;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.AspNetCore.Server.Kestrel.Https;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace NativeAdo.Tests;

[TestClass]
public sealed class Ed25519SvidTests
{
    [TestMethod]
    public void ExplicitPrivateSvidPkixValidatesEd25519SignaturesAndAllLeafConstraints()
    {
        using var certificates = new Ed25519IssuerCertificates();
        using var stranger = new Ed25519IssuerCertificates();
        using var online = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root]);
        using var trust = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root],
            shortLivedSpiffePolicy: new() { MaxLeafLifetime = TimeSpan.FromHours(2) });
        Assert.AreEqual("1.3.101.112", certificates.Root.PublicKey.Oid.Value);
        Assert.AreEqual("1.3.101.112", certificates.Gateway.SignatureAlgorithm.Value);
        Assert.AreEqual("1.2.840.10045.2.1", certificates.Gateway.PublicKey.Oid.Value);
        Assert.IsFalse(online.IsTrusted(certificates.Gateway));
        Assert.IsTrue(trust.IsTrusted(certificates.Gateway));
        foreach (var invalid in new[]
        {
            stranger.Gateway,
            certificates.Issue(TestCertificates.ActorId),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromHours(3)),
            certificates.Issue(TestCertificates.GatewayId, expired: true),
            certificates.Issue(TestCertificates.GatewayId, wrongUsage: true),
            certificates.Issue(TestCertificates.GatewayId, isCa: true)
        })
        {
            Assert.IsFalse(trust.IsTrusted(invalid));
        }
    }

    [TestMethod]
    public async Task WindowsSchannelBlocksEd25519SignedEcdsaBeforeCustomValidationOnTls12AndTls13()
    {
        using var serverCertificates = new TestCertificates();
        using var clientCertificates = new Ed25519IssuerCertificates();
        using var trust = new GatewayCertificateTrust(TestCertificates.GatewayId, [clientCertificates.Root, serverCertificates.Root],
            shortLivedSpiffePolicy: new());
        var calls = 0;
        var tlsValidations = 0;
        var supportedClient = serverCertificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromHours(1), exportable: true);
        Assert.IsTrue(trust.IsTrusted(clientCertificates.Gateway));
        var builder = WebApplication.CreateEmptyBuilder(new WebApplicationOptions());
        builder.WebHost.UseKestrel(k => k.Listen(IPAddress.Loopback, 0, listener =>
        {
            listener.Protocols = HttpProtocols.Http2;
            listener.UseHttps(https =>
            {
                https.ServerCertificate = serverCertificates.Server;
                https.SslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13;
                https.ClientCertificateMode = ClientCertificateMode.RequireCertificate;
                https.ClientCertificateValidation = (certificate, chain, _) =>
                {
                    Interlocked.Increment(ref tlsValidations);
                    return trust.IsTrusted(certificate, chain);
                };
            });
        }));
        builder.Services.AddNativeAdo(new(), trust, (_, _, _) => ValueTask.FromResult<CredentialGrant?>(new("fake-host-identity")),
            (_, _) =>
            {
                Interlocked.Increment(ref calls);
                return ValueTask.FromResult("fake.private-svid.token");
            });
        await using var app = builder.Build();
        app.MapNativeAdo();
        await app.StartAsync();
        try
        {
            var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
            foreach (var protocol in new[] { "12", "13" })
            {
                var accepted = await RunOpenSslTlsProbeAsync(
                    new Uri(address).Port, supportedClient, serverCertificates.Root, protocol);
                Assert.AreEqual("TLS_OK", accepted);
                Assert.IsTrue(tlsValidations > 0);
                var validationsBefore = tlsValidations;
                var rejected = await RunOpenSslTlsProbeAsync(
                    new Uri(address).Port, clientCertificates.Gateway, serverCertificates.Root, protocol);
                Assert.AreEqual("TLS_UNSUPPORTED_CERTIFICATE", rejected);
                Assert.AreEqual(validationsBefore, tlsValidations);
            }
            Assert.AreEqual(0, calls);
        }
        finally
        {
            await app.StopAsync();
        }
    }

    private static async Task<string> RunOpenSslTlsProbeAsync(int serverPort,
        X509Certificate2 certificate, X509Certificate2 serverRoot, string protocol)
    {
        var directory = Path.Combine(AppContext.BaseDirectory, "owned-openssl-fixture-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        Process? process = null;
        try
        {
            var certPath = Path.Combine(directory, "fake-client.pem");
            var keyPath = Path.Combine(directory, "fake-client-key.pem");
            var caPath = Path.Combine(directory, "fake-server-root.pem");
            using var key = certificate.GetECDsaPrivateKey()!;
            await File.WriteAllTextAsync(certPath, certificate.ExportCertificatePem());
            await File.WriteAllTextAsync(keyPath, key.ExportPkcs8PrivateKeyPem());
            await File.WriteAllTextAsync(caPath, serverRoot.ExportCertificatePem());
            var start = new ProcessStartInfo("python")
            {
                RedirectStandardError = true,
                RedirectStandardOutput = true
            };
            foreach (var argument in new[]
            {
                Path.Combine(AppContext.BaseDirectory, "Fixtures", "openssl_svid_tls_probe.py"),
                serverPort.ToString(System.Globalization.CultureInfo.InvariantCulture), caPath, certPath, keyPath, protocol
            })
            {
                start.ArgumentList.Add(argument);
            }
            process = Process.Start(start)!;
            var output = process.StandardOutput.ReadToEndAsync();
            await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15));
            var classification = (await output).Trim();
            return classification is "TLS_OK" or "TLS_UNSUPPORTED_CERTIFICATE" ? classification : "TLS_FAILURE";
        }
        finally
        {
            if (process is not null)
            {
                if (!process.HasExited) { process.Kill(entireProcessTree: true); }
                await process.WaitForExitAsync();
                process.Dispose();
            }
            Directory.Delete(directory, recursive: true);
        }
    }
}
