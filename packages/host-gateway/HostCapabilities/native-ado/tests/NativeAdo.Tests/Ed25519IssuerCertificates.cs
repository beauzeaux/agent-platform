using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Org.BouncyCastle.Asn1;
using Org.BouncyCastle.Asn1.X509;
using Org.BouncyCastle.Crypto;
using Org.BouncyCastle.Crypto.Generators;
using Org.BouncyCastle.Crypto.Operators;
using Org.BouncyCastle.Crypto.Parameters;
using Org.BouncyCastle.Math;
using Org.BouncyCastle.Security;
using Org.BouncyCastle.X509;

namespace NativeAdo.Tests;

internal sealed class Ed25519IssuerCertificates : IDisposable
{
    private readonly List<X509Certificate2> owned = [];
    private readonly AsymmetricCipherKeyPair issuer;
    private readonly SecureRandom random = new();
    private readonly X509Name issuerName = new("CN=offline-ed25519-issuer");
    internal X509Certificate2 Root { get; }
    internal X509Certificate2 Gateway { get; }

    internal Ed25519IssuerCertificates()
    {
        var keys = new Ed25519KeyPairGenerator();
        keys.Init(new Ed25519KeyGenerationParameters(random));
        issuer = keys.GenerateKeyPair();
        var generator = Generator(issuerName, issuer.Public, DateTime.UtcNow.AddDays(-2), DateTime.UtcNow.AddDays(2));
        generator.AddExtension(X509Extensions.BasicConstraints, true, new BasicConstraints(true));
        generator.AddExtension(X509Extensions.KeyUsage, true, new KeyUsage(KeyUsage.KeyCertSign | KeyUsage.CrlSign));
        var root = generator.Generate(new Asn1SignatureFactory("Ed25519", issuer.Private, random));
        Root = X509CertificateLoader.LoadCertificate(root.GetEncoded());
        owned.Add(Root);
        Gateway = Issue(TestCertificates.GatewayId);
    }

    internal X509Certificate2 Issue(string identity, TimeSpan? lifetime = null, bool expired = false,
        bool wrongUsage = false, bool isCa = false)
    {
        using var key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var notBefore = DateTime.UtcNow.AddMinutes(-1);
        var notAfter = notBefore.Add(lifetime ?? TimeSpan.FromHours(1));
        if (expired)
        {
            notAfter = DateTime.UtcNow.AddMinutes(-1);
            notBefore = notAfter.Subtract(lifetime ?? TimeSpan.FromHours(1));
        }
        var generator = Generator(new X509Name("CN=offline-ecdsa-client"),
            PublicKeyFactory.CreateKey(key.ExportSubjectPublicKeyInfo()), notBefore, notAfter);
        generator.AddExtension(X509Extensions.BasicConstraints, true, new BasicConstraints(isCa));
        generator.AddExtension(X509Extensions.KeyUsage, true, new KeyUsage(KeyUsage.DigitalSignature));
        generator.AddExtension(X509Extensions.ExtendedKeyUsage, false,
            new ExtendedKeyUsage(new[] { new DerObjectIdentifier(wrongUsage ? "1.3.6.1.5.5.7.3.1" : "1.3.6.1.5.5.7.3.2") }));
        generator.AddExtension(X509Extensions.SubjectAlternativeName, false,
            new GeneralNames(new GeneralName(GeneralName.UniformResourceIdentifier, identity)));
        var signed = generator.Generate(new Asn1SignatureFactory("Ed25519", issuer.Private, random));
        using var publicLeaf = X509CertificateLoader.LoadCertificate(signed.GetEncoded());
        using var withKey = publicLeaf.CopyWithPrivateKey(key);
        var pkcs12 = withKey.Export(X509ContentType.Pkcs12);
        X509Certificate2 certificate;
        try
        {
            certificate = X509CertificateLoader.LoadPkcs12(pkcs12, null, X509KeyStorageFlags.Exportable);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(pkcs12);
        }
        owned.Add(certificate);
        return certificate;
    }

    private X509V3CertificateGenerator Generator(X509Name subject, AsymmetricKeyParameter publicKey,
        DateTime notBefore, DateTime notAfter)
    {
        var generator = new X509V3CertificateGenerator();
        generator.SetSerialNumber(new BigInteger(1, RandomNumberGenerator.GetBytes(16)));
        generator.SetIssuerDN(issuerName);
        generator.SetSubjectDN(subject);
        generator.SetNotBefore(notBefore);
        generator.SetNotAfter(notAfter);
        generator.SetPublicKey(publicKey);
        return generator;
    }

    public void Dispose()
    {
        foreach (var certificate in owned)
        {
            certificate.Dispose();
        }
    }
}
