using System.Net;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace NativeAdo.Tests;

internal sealed class TestCertificates : IDisposable
{
    internal const string GatewayId = "spiffe://cluster.local/ns/canary/sa/canary-atenet-egress";
    internal const string ActorId = "spiffe://cluster.local/atespace/team-a/actor/actor-a";
    private readonly List<X509Certificate2> owned = [];
    internal X509Certificate2 Root { get; }
    internal X509Certificate2 Gateway { get; }
    internal X509Certificate2 Server { get; }

    internal TestCertificates()
    {
        using var key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var request = new CertificateRequest("CN=offline-test-ca", key, HashAlgorithmName.SHA256);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(
            X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        Root = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-2), DateTimeOffset.UtcNow.AddDays(2));
        owned.Add(Root);
        Gateway = Issue(GatewayId);
        Server = Issue(null, server: true);
    }

    internal X509Certificate2 Issue(string? uri, bool server = false, bool expired = false,
        string? commonName = null, bool duplicateUri = false, bool wrongUsage = false, bool malformedSan = false,
        TimeSpan? lifetime = null, bool notYetValid = false, bool isCa = false, bool omitClientUsage = false,
        X509Certificate2? issuer = null, bool exportable = false)
    {
        using var key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var request = new CertificateRequest($"CN={commonName ?? "offline-test-leaf"}", key, HashAlgorithmName.SHA256);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(isCa, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(
            isCa ? X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.KeyCertSign : X509KeyUsageFlags.DigitalSignature, true));
        if (!omitClientUsage)
        {
            request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
                [new Oid(server || wrongUsage ? "1.3.6.1.5.5.7.3.1" : "1.3.6.1.5.5.7.3.2")], true));
        }
        if (malformedSan)
        {
            request.CertificateExtensions.Add(new X509Extension("2.5.29.17", [0x30, 0x03, 0x86, 0x02, 0x61], false));
        }
        else
        {
            var san = new SubjectAlternativeNameBuilder();
            san.AddDnsName("localhost");
            san.AddIpAddress(IPAddress.Loopback);
            if (uri is not null)
            {
                san.AddUri(new Uri(uri));
                if (duplicateUri)
                {
                    san.AddUri(new Uri(ActorId));
                }
            }
            request.CertificateExtensions.Add(san.Build());
        }
        var now = DateTimeOffset.UtcNow;
        var notBefore = lifetime is null ? now.AddDays(-1) : now.AddMinutes(-1);
        var notAfter = lifetime is null ? now.AddDays(1) : notBefore.Add(lifetime.Value);
        if (expired)
        {
            notAfter = now.AddHours(-1);
            notBefore = lifetime is null ? now.AddDays(-1) : notAfter.Subtract(lifetime.Value);
        }
        if (notYetValid)
        {
            notBefore = now.AddMinutes(1);
            notAfter = notBefore.Add(lifetime ?? TimeSpan.FromHours(1));
        }
        using var unsigned = request.Create(issuer ?? Root, notBefore, notAfter, RandomNumberGenerator.GetBytes(16));
        using var withKey = unsigned.CopyWithPrivateKey(key);
        var pkcs12 = withKey.Export(X509ContentType.Pkcs12);
        X509Certificate2 certificate;
        try
        {
            // Schannel needs a transient key container; disposal deletes it, without installing a certificate.
            certificate = X509CertificateLoader.LoadPkcs12(pkcs12, null,
                exportable ? X509KeyStorageFlags.Exportable : X509KeyStorageFlags.DefaultKeySet);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(pkcs12);
        }
        owned.Add(certificate);
        return certificate;
    }

    public void Dispose()
    {
        foreach (var certificate in owned)
        {
            certificate.Dispose();
        }
    }
}
