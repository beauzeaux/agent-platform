using Org.BouncyCastle.Asn1.X509;
using Org.BouncyCastle.Crypto.Operators;
using Org.BouncyCastle.Crypto.Parameters;
using Org.BouncyCastle.Math;
using Org.BouncyCastle.Security;
using Org.BouncyCastle.X509;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class NativeAdoConfigurationTests
{
    [TestMethod]
    public async Task UnsupportedPodidentityIssuerCannotReportNativeReadyOrAccessWamState()
    {
        using var directory = new OwnedDirectory();
        var random = new SecureRandom();
        var key = new Ed25519PrivateKeyParameters(random);
        var generator = new X509V3CertificateGenerator();
        generator.SetSerialNumber(BigInteger.One);
        generator.SetIssuerDN(new X509Name("CN=HostCapabilities test Ed25519 issuer"));
        generator.SetSubjectDN(new X509Name("CN=HostCapabilities test Ed25519 issuer"));
        generator.SetNotBefore(DateTime.UtcNow.AddMinutes(-5));
        generator.SetNotAfter(DateTime.UtcNow.AddHours(1));
        generator.SetPublicKey(key.GeneratePublicKey());
        generator.AddExtension(X509Extensions.BasicConstraints, true, new BasicConstraints(true));
        var certificate = generator.Generate(new Asn1SignatureFactory("Ed25519", key, random));
        var rootFile = Path.Combine(directory.Path, "issuer.crt");
        await File.WriteAllBytesAsync(rootFile, certificate.GetEncoded());
        var state = Path.Combine(directory.Path, "wam-never-created");
        using var telemetry = new HostTelemetry(new());
        var module = new NativeAdoCapability(new()
        {
            NativeAdo = new() { Enabled = true, TrustMode = "short-lived-spiffe", GatewayRootFiles = [rootFile], StateDirectory = state }
        }, telemetry);
        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() => module.RunAsync(CancellationToken.None));
        Assert.IsFalse(module.State.IsReady);
        Assert.AreEqual("unsupported_gateway_tls_issuer", module.State.Health.Reason);
        Assert.IsFalse(Directory.Exists(state));
    }

    [TestMethod]
    public void NativeCredentialGrantsNeverDeriveHostIdentityFromCallerText()
    {
        var configuration = new NativeAdoConfiguration
        {
            Grants = [new()
            {
                ActorSpiffeId = "spiffe://cluster.local/actors/canary",
                CredentialUri = "ate-secret://windows-wam/canary/ado",
                Identity = "approved-work-account", Profile = "engsys-reader"
            }]
        };
        Assert.AreEqual("approved-work-account", configuration.Grant(
            "spiffe://cluster.local/actors/canary", "ate-secret://windows-wam/canary/ado")?.Identity);
        Assert.IsNull(configuration.Grant("spiffe://cluster.local/actors/other", "ate-secret://windows-wam/canary/ado"));
        Assert.IsNull(configuration.Grant("spiffe://cluster.local/actors/canary", "ate-secret://windows-wam/canary/ado?scope=other"));
        Assert.IsFalse(new HostConfiguration().NativeAdo.Enabled);
        Assert.ThrowsExactly<InvalidOperationException>(() => new NativeAdoConfiguration { Enabled = true }.Validate());
    }
}
