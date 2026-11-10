using System.Formats.Asn1;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Org.BouncyCastle.Asn1;
using Org.BouncyCastle.Pkix;
using Org.BouncyCastle.Security.Certificates;
using Org.BouncyCastle.Utilities.Collections;
using Org.BouncyCastle.X509;
using Org.BouncyCastle.X509.Store;

namespace HostCapabilities.NativeAdo;

public sealed class GatewayCertificateTrust : IDisposable
{
    private readonly X509Certificate2Collection roots = [];
    private readonly X509Certificate2Collection intermediates = [];
    private readonly string gatewaySpiffeId;
    private readonly X509RevocationMode revocationMode;
    private readonly TimeSpan? maxLeafLifetime;

    public GatewayCertificateTrust(string gatewaySpiffeId, IEnumerable<X509Certificate2> roots,
        IEnumerable<X509Certificate2>? intermediates = null, X509RevocationMode revocationMode = X509RevocationMode.Online,
        ShortLivedSpiffeTrustPolicy? shortLivedSpiffePolicy = null)
    {
        if (!IdentitySyntax.IsSpiffeId(gatewaySpiffeId))
        {
            throw new ArgumentException("An exact gateway SPIFFE ID is required.");
        }
        shortLivedSpiffePolicy?.Validate();
        if (shortLivedSpiffePolicy is not null && revocationMode != X509RevocationMode.Online)
        {
            throw new ArgumentException("Select the explicit SPIFFE policy without a separate revocation override.");
        }

        this.gatewaySpiffeId = gatewaySpiffeId;
        this.revocationMode = shortLivedSpiffePolicy is null ? revocationMode : X509RevocationMode.NoCheck;
        maxLeafLifetime = shortLivedSpiffePolicy?.MaxLeafLifetime;
        foreach (var root in roots)
        {
            this.roots.Add(X509CertificateLoader.LoadCertificate(root.RawData));
        }
        foreach (var intermediate in intermediates ?? [])
        {
            this.intermediates.Add(X509CertificateLoader.LoadCertificate(intermediate.RawData));
        }
        if (this.roots.Count == 0)
        {
            throw new ArgumentException("A gateway trust bundle is required.");
        }
    }

    public bool IsTrusted(X509Certificate2? certificate, X509Chain? presentedChain = null)
    {
        if (certificate is null)
        {
            return false;
        }
        try
        {
            var now = DateTime.UtcNow;
            if (maxLeafLifetime is { } maximum &&
                (certificate.NotBefore.ToUniversalTime() > now || certificate.NotAfter.ToUniversalTime() <= now ||
                 certificate.NotAfter.ToUniversalTime() - certificate.NotBefore.ToUniversalTime() > maximum ||
                 certificate.Extensions.OfType<X509BasicConstraintsExtension>().Count(e => !e.CertificateAuthority) != 1))
            {
                return false;
            }
            if (!HasExactUriSan(certificate) ||
                !certificate.Extensions.OfType<X509EnhancedKeyUsageExtension>().Any(e =>
                    e.EnhancedKeyUsages.Cast<Oid>().Any(o => o.Value == "1.3.6.1.5.5.7.3.2")) ||
                certificate.Extensions.OfType<X509BasicConstraintsExtension>().Any(e => e.CertificateAuthority))
            {
                return false;
            }
            if (maxLeafLifetime is not null)
            {
                return certificate.PublicKey.Oid.Value is "1.2.840.10045.2.1" or "1.2.840.113549.1.1.1" &&
                    HasPinnedPrivateChain(certificate, now);
            }

            using var chain = new X509Chain();
            chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
            chain.ChainPolicy.CustomTrustStore.AddRange(roots);
            chain.ChainPolicy.ExtraStore.AddRange(intermediates);
            if (presentedChain is not null)
            {
                foreach (var element in presentedChain.ChainElements)
                {
                    chain.ChainPolicy.ExtraStore.Add(element.Certificate);
                }
            }
            chain.ChainPolicy.ApplicationPolicy.Add(new Oid("1.3.6.1.5.5.7.3.2"));
            chain.ChainPolicy.DisableCertificateDownloads = true;
            chain.ChainPolicy.VerificationTime = now;
            chain.ChainPolicy.RevocationMode = revocationMode;
            chain.ChainPolicy.RevocationFlag = X509RevocationFlag.ExcludeRoot;
            chain.ChainPolicy.UrlRetrievalTimeout = TimeSpan.FromSeconds(2);
            return chain.Build(certificate);
        }
        catch (CryptographicException)
        {
            return false;
        }
        catch (AsnContentException)
        {
            return false;
        }
        catch (PkixCertPathBuilderException)
        {
            return false;
        }
        catch (CertificateException)
        {
            return false;
        }
        catch (Org.BouncyCastle.Security.GeneralSecurityException)
        {
            return false;
        }
        catch (IOException)
        {
            return false;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }

    private bool HasPinnedPrivateChain(X509Certificate2 certificate, DateTime now)
    {
        var parser = new X509CertificateParser();
        var pinnedRoots = roots.Cast<X509Certificate2>().Select(c => parser.ReadCertificate(c.RawData)).ToArray();
        var pinnedIntermediates = intermediates.Cast<X509Certificate2>().Select(c => parser.ReadCertificate(c.RawData)).ToArray();
        var authorities = pinnedRoots.Concat(pinnedIntermediates).ToArray();
        var leaf = parser.ReadCertificate(certificate.RawData);
        var parameters = new PkixBuilderParameters(pinnedRoots.Select(c => new TrustAnchor(c, null)).ToHashSet(),
            new X509CertStoreSelector { Certificate = leaf })
        {
            Date = now,
            IsRevocationEnabled = false,
            MaxPathLength = Math.Min(pinnedIntermediates.Length, 8)
        };
        parameters.AddStoreCert(CollectionUtilities.CreateStore(authorities.Append(leaf)));
        var result = new PkixCertPathBuilder().Build(parameters);
        var path = result.CertPath.Certificates.Append(result.TrustAnchor.TrustedCert).ToArray();
        foreach (var item in path)
        {
            item.CheckValidity(now);
            var usage = item.GetExtendedKeyUsage();
            if (usage is not null && !usage.Contains(new DerObjectIdentifier("1.3.6.1.5.5.7.3.2")) &&
                !usage.Contains(new DerObjectIdentifier("2.5.29.37.0")))
            {
                return false;
            }
        }
        return path.Skip(1).All(item => authorities.Any(pinned => pinned.Equals(item)));
    }

    private bool HasExactUriSan(X509Certificate2 certificate)
    {
        var extensions = certificate.Extensions.Cast<X509Extension>().Where(e => e.Oid?.Value == "2.5.29.17").ToArray();
        if (extensions.Length != 1)
        {
            return false;
        }
        var reader = new AsnReader(extensions[0].RawData, AsnEncodingRules.DER);
        var names = reader.ReadSequence();
        reader.ThrowIfNotEmpty();
        var uriTag = new Asn1Tag(TagClass.ContextSpecific, 6);
        string? identity = null;
        while (names.HasData)
        {
            if (names.PeekTag().HasSameClassAndValue(uriTag))
            {
                if (identity is not null)
                {
                    return false;
                }
                identity = names.ReadCharacterString(UniversalTagNumber.IA5String, uriTag);
            }
            else
            {
                names.ReadEncodedValue();
            }
        }
        return string.Equals(identity, gatewaySpiffeId, StringComparison.Ordinal);
    }

    public void Dispose()
    {
        foreach (var certificate in roots.Cast<X509Certificate2>().Concat(intermediates.Cast<X509Certificate2>()))
        {
            certificate.Dispose();
        }
    }
}
