using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class ActorAdmissionTests
{
    [TestMethod]
    public async Task ExplicitAdditionalSpaceStillRequiresRegisteredUidSubjectProfileAndMatchingClaims()
    {
        using var fixture = new JwtFixture(additionalSpaces: ["baseline"]);
        var admission = new ActorAdmission(fixture.Configuration);
        var baseline = fixture.Token(space: "baseline", subject: "actor/baseline/a");
        Assert.IsNull(await admission.ValidateAsync(baseline));
        fixture.SetRegisteredSubject("actor/baseline/a");
        Assert.AreEqual(new ActorIdentity("a", "actor/baseline/a", "reader"), await admission.ValidateAsync(baseline));
        Assert.IsNull(await admission.ValidateAsync(fixture.Token(space: "unmanaged", subject: "actor/unmanaged/a")));
        Assert.IsNull(await admission.ValidateAsync(fixture.Token(space: "baseline", subject: "actor/baseline/a", uid: "other")));
        Assert.IsNull(await admission.ValidateAsync(fixture.Token(space: "factory-executions", subject: "actor/baseline/a")));
        fixture.RevokeActor();
        Assert.IsNull(await admission.ValidateAsync(baseline));
    }

    [TestMethod]
    public async Task ValidatesPinnedSignatureIssuerAudienceLifetimeSpaceProfileAndRequiredActorIdentity()
    {
        using var fixture = new JwtFixture();
        var admission = new ActorAdmission(fixture.Configuration);
        Assert.AreEqual(new ActorIdentity("a", "actor/factory-executions/a", "reader"), await admission.ValidateAsync(fixture.Token()));
        foreach (var token in new[]
        {
            fixture.Token(issuer: "https://other.invalid"), fixture.Token(audience: "other"),
            fixture.Token(space: "other"), fixture.Token(actorName: "other"), fixture.Token(uid: ""),
            fixture.Token(expired: true), fixture.Token().Replace(".", ".tampered", StringComparison.Ordinal)
        })
            Assert.IsNull(await admission.ValidateAsync(token));
        fixture.ClearKeys();
        Assert.IsNull(await admission.ValidateAsync(fixture.Token()));
    }

    [TestMethod]
    public async Task NativeActorClaimUsesCurrentRegistryProfileAndRejectsUidSubjectSpaceMismatchAndRevocation()
    {
        using var fixture = new JwtFixture();
        var admission = new ActorAdmission(fixture.Configuration);
        Assert.AreEqual("reader", (await admission.ValidateAsync(fixture.Token(profile: "invented-admin")))?.Profile);
        foreach (var token in new[]
        {
            fixture.Token(uid: "unknown"), fixture.Token(actorSpace: "other"),
            fixture.Token(topAtespace: "other"),
            fixture.Token(subject: "actor/factory-executions/other", actorName: "other"),
            fixture.Token(subject: "actor/other/a", actorSpace: "other")
        })
            Assert.IsNull(await admission.ValidateAsync(token));
        fixture.SetProfile("writer");
        Assert.IsNull(await admission.ValidateAsync(fixture.Token()));
        fixture.SetProfile("reader");
        Assert.IsNotNull(await admission.ValidateAsync(fixture.Token()));
        fixture.RevokeActor();
        Assert.IsNull(await admission.ValidateAsync(fixture.Token()));
    }

    [TestMethod]
    public async Task NativeStructuredActorClaimSupportsPinnedEs256SigningKeys()
    {
        using var fixture = new JwtFixture(useEc: true);
        Assert.AreEqual(new ActorIdentity("a", "actor/factory-executions/a", "reader"),
            await new ActorAdmission(fixture.Configuration).ValidateAsync(fixture.Token()));
    }

    [TestMethod]
    public async Task CustomClaimsRequireExplicitModeAndRemainBoundToTheCurrentRegistry()
    {
        using var native = new JwtFixture();
        Assert.IsNull(await new ActorAdmission(native.Configuration).ValidateAsync(native.Token(custom: true)));
        using var custom = new JwtFixture(custom: true);
        var admission = new ActorAdmission(custom.Configuration);
        Assert.AreEqual("reader", (await admission.ValidateAsync(custom.Token(custom: true)))?.Profile);
        Assert.IsNull(await admission.ValidateAsync(custom.Token(custom: true, profile: "writer")));
        custom.RevokeActor();
        Assert.IsNull(await admission.ValidateAsync(custom.Token(custom: true)));
    }

    [TestMethod]
    [DataRow(false, "/v1/responses")]
    [DataRow(false, "/V1/RESPONSES/")]
    [DataRow(true, "/v1/chat/completions")]
    [DataRow(true, "/V1/CHAT/COMPLETIONS/")]
    public async Task JwtOnlyModelAdmissionRejectsLegacyKeyAndUsesPrivateGatewayCredentialInternally(bool chatCompletions, string path)
    {
        using var jwt = new JwtFixture();
        await using var fixture = await LlmFixture.StartAsync((request, _) =>
        {
            Assert.AreEqual("Bearer private-inference-token", request.Headers.Authorization?.ToString());
            return Task.FromResult(LlmFixture.Json(new { status = "completed", output = Array.Empty<object>() }));
        }, jwt.Configuration);
        using var denied = await fixture.RespondAsync(false, chatCompletions, path);
        Assert.AreEqual(HttpStatusCode.Unauthorized, denied.StatusCode);
        Assert.AreEqual(0, fixture.Inferences);
        fixture.Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", jwt.Token());
        using var accepted = await fixture.RespondAsync(false, chatCompletions, path);
        Assert.AreEqual(HttpStatusCode.OK, accepted.StatusCode);
        Assert.AreEqual(1, fixture.Inferences);
        jwt.RevokeActor();
        using var revoked = await fixture.RespondAsync(false, chatCompletions, path);
        Assert.AreEqual(HttpStatusCode.Unauthorized, revoked.StatusCode);
        Assert.AreEqual(1, fixture.Inferences);
    }
}

internal sealed class JwtFixture : IDisposable
{
    private readonly OwnedDirectory directory = new();
    private readonly RSA rsa = RSA.Create(2048);
    private readonly ECDsa? ec;
    internal ActorJwtConfiguration Configuration { get; }
    internal JwtFixture(bool custom = false, bool useEc = false, string[]? additionalSpaces = null)
    {
        var parameters = rsa.ExportParameters(false);
        var file = directory.Write("jwks.json", JsonSerializer.Serialize(new
        {
            keys = new[] { new { kty = "RSA", kid = "fixture", use = "sig", alg = "RS256",
                n = Base64UrlEncoder.Encode(parameters.Modulus!), e = Base64UrlEncoder.Encode(parameters.Exponent!) } }
        }));
        if (useEc)
        {
            ec = ECDsa.Create(ECCurve.NamedCurves.nistP256);
            var curve = ec.ExportParameters(false);
            File.WriteAllText(file, JsonSerializer.Serialize(new
            {
                keys = new[] { new { kty = "EC", crv = "P-256", kid = "fixture", use = "sig", alg = "ES256",
                    x = Base64UrlEncoder.Encode(curve.Q.X!), y = Base64UrlEncoder.Encode(curve.Q.Y!) } }
            }));
        }
        var registry = directory.Write("actors.json", Registry("reader"));
        Configuration = new()
        {
            Enabled = true,
            AllowLegacyKeys = false,
            Audience = "host-capabilities",
            Issuer = "https://issuer.invalid",
            JwksFile = file,
            Space = "factory-executions",
            AdditionalSpaces = additionalSpaces ?? [],
            Profiles = ["reader"],
            ActorRegistryFile = registry,
            ClaimFormat = custom ? "custom" : "substrate"
        };
    }
    internal string Token(string issuer = "https://issuer.invalid", string audience = "host-capabilities",
        string space = "factory-executions", string? profile = null, string uid = "a", bool expired = false,
        string actorName = "a", string? actorSpace = null, string? subject = null, string? topAtespace = null, bool custom = false)
    {
        var claims = new Dictionary<string, object> { ["sub"] = subject ?? "actor/factory-executions/a" };
        if (custom)
        {
            claims["actor_uid"] = uid;
            claims["space"] = space;
            claims["profile"] = profile ?? "reader";
        }
        else
        {
            claims["actor.ate.dev"] = new Dictionary<string, object>
            {
                ["atespace"] = actorSpace ?? space,
                ["actorName"] = actorName,
                ["actorUID"] = uid
            };
            if (topAtespace is not null) claims["atespace"] = topAtespace;
            if (profile is not null) claims["profile"] = profile;
        }
        return new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Audience = audience,
            Issuer = issuer,
            Expires = DateTime.UtcNow.AddMinutes(expired ? -5 : 5),
            IssuedAt = DateTime.UtcNow.AddMinutes(-10),
            NotBefore = DateTime.UtcNow.AddMinutes(-10),
            Claims = claims,
            SigningCredentials = ec is null
                ? new(new RsaSecurityKey(rsa) { KeyId = "fixture" }, SecurityAlgorithms.RsaSha256)
                : new(new ECDsaSecurityKey(ec) { KeyId = "fixture" }, SecurityAlgorithms.EcdsaSha256)
        });
    }
    internal void RevokeActor() => File.WriteAllText(Configuration.ActorRegistryFile, """{"actors":{}}""");
    internal void SetRegisteredSubject(string subject) => File.WriteAllText(Configuration.ActorRegistryFile,
        JsonSerializer.Serialize(new { actors = new Dictionary<string, object> { ["a"] = new { subject, profile = "reader" } } }));
    internal void SetProfile(string profile) => File.WriteAllText(Configuration.ActorRegistryFile, Registry(profile));
    private static string Registry(string profile) => JsonSerializer.Serialize(new
    {
        actors = new Dictionary<string, object> { ["a"] = new { subject = "actor/factory-executions/a", profile } }
    });
    internal void ClearKeys() => File.WriteAllText(Configuration.JwksFile, """{"keys":[]}""");
    public void Dispose()
    {
        rsa.Dispose();
        ec?.Dispose();
        directory.Dispose();
    }
}
