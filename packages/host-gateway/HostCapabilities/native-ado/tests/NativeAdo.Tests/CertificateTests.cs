using System.Security.Cryptography.X509Certificates;
using HostCapabilities.NativeAdo;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace NativeAdo.Tests;

[TestClass]
public sealed class CertificateTests
{
    [TestMethod]
    public void OnlyExactGatewayUriSanWithTrustedValidClientChainIsAccepted()
    {
        using var certificates = new TestCertificates();
        using var stranger = new TestCertificates();
        using var trust = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root],
            revocationMode: X509RevocationMode.NoCheck);
        Assert.IsTrue(trust.IsTrusted(certificates.Gateway));
        foreach (var certificate in new X509Certificate2?[]
        {
            null,
            stranger.Gateway,
            certificates.Issue(null, commonName: TestCertificates.GatewayId),
            certificates.Issue(TestCertificates.ActorId),
            certificates.Issue(TestCertificates.GatewayId + "-other"),
            certificates.Issue(TestCertificates.GatewayId, duplicateUri: true),
            certificates.Issue(TestCertificates.GatewayId, expired: true),
            certificates.Issue(TestCertificates.GatewayId, malformedSan: true),
            certificates.Issue(TestCertificates.GatewayId, wrongUsage: true)
        })
        {
            Assert.IsFalse(trust.IsTrusted(certificate));
        }
    }

    [TestMethod]
    public void ShortLivedPolicyIsExplicitAndRejectsWrongIdentityIssuerUsageAndLifetime()
    {
        using var certificates = new TestCertificates();
        using var stranger = new TestCertificates();
        using var online = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root]);
        using var shortLived = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root],
            shortLivedSpiffePolicy: new() { MaxLeafLifetime = TimeSpan.FromHours(1) });
        var valid = certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30));
        Assert.IsFalse(online.IsTrusted(valid));
        Assert.IsTrue(shortLived.IsTrusted(valid));
        foreach (var invalid in new[]
        {
            stranger.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30)),
            certificates.Issue(TestCertificates.ActorId, lifetime: TimeSpan.FromMinutes(30)),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromHours(2)),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30), expired: true),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30), notYetValid: true),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30), wrongUsage: true),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30), omitClientUsage: true),
            certificates.Issue(TestCertificates.GatewayId, lifetime: TimeSpan.FromMinutes(30), isCa: true)
        })
        {
            Assert.IsFalse(shortLived.IsTrusted(invalid));
        }
        Assert.ThrowsException<ArgumentException>(() => new GatewayCertificateTrust(
            TestCertificates.GatewayId, [certificates.Root], shortLivedSpiffePolicy: new() { MaxLeafLifetime = TimeSpan.Zero }));
        Assert.ThrowsException<ArgumentException>(() => new GatewayCertificateTrust(
            TestCertificates.GatewayId, [certificates.Root], shortLivedSpiffePolicy: new() { MaxLeafLifetime = TimeSpan.FromHours(25) }));
    }

    [TestMethod]
    public void ShortLivedPolicyPinsEveryIntermediateAndIgnoresUnpinnedPresentedChains()
    {
        using var certificates = new TestCertificates();
        var issuer = certificates.Issue(null, commonName: "pinned-intermediate", isCa: true,
            omitClientUsage: true, lifetime: TimeSpan.FromHours(12));
        var otherIssuer = certificates.Issue(null, commonName: "unpinned-intermediate", isCa: true,
            omitClientUsage: true, lifetime: TimeSpan.FromHours(12));
        var valid = certificates.Issue(TestCertificates.GatewayId, issuer: issuer, lifetime: TimeSpan.FromHours(1));
        var unpinned = certificates.Issue(TestCertificates.GatewayId, issuer: otherIssuer, lifetime: TimeSpan.FromHours(1));
        using var trust = new GatewayCertificateTrust(TestCertificates.GatewayId, [certificates.Root], [issuer],
            shortLivedSpiffePolicy: new());
        Assert.IsTrue(trust.IsTrusted(valid));
        using var presented = new X509Chain();
        presented.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
        presented.ChainPolicy.CustomTrustStore.Add(certificates.Root);
        presented.ChainPolicy.ExtraStore.Add(otherIssuer);
        presented.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
        presented.ChainPolicy.DisableCertificateDownloads = true;
        Assert.IsTrue(presented.Build(unpinned));
        Assert.IsFalse(trust.IsTrusted(unpinned, presented));
    }
}
