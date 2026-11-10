using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace HostCapabilities;

public sealed record ActorIdentity(string Uid, string Subject, string Profile);

public sealed class ActorAdmission(ActorJwtConfiguration configuration)
{
    private readonly JsonWebTokenHandler handler = new() { MaximumTokenSizeInBytes = 16 * 1024 };

    public async Task<ActorIdentity?> ValidateAsync(string token)
    {
        if (!configuration.Enabled || token.Length > 16 * 1024)
            return null;
        try
        {
            var keys = new JsonWebKeySet(await File.ReadAllTextAsync(configuration.JwksFile));
            var signingKeys = keys.GetSigningKeys().Where(key =>
                key is RsaSecurityKey or ECDsaSecurityKey
                || key is JsonWebKey jwk && jwk.Kty is "RSA" or "EC").ToArray();
            var result = await handler.ValidateTokenAsync(token, new TokenValidationParameters
            {
                ClockSkew = TimeSpan.Zero,
                IssuerSigningKeys = signingKeys,
                RequireExpirationTime = true,
                RequireSignedTokens = true,
                TryAllIssuerSigningKeys = false,
                ValidateAudience = true,
                ValidateIssuer = true,
                ValidateIssuerSigningKey = true,
                ValidateLifetime = true,
                ValidAlgorithms = [SecurityAlgorithms.EcdsaSha256, SecurityAlgorithms.RsaSha256],
                ValidAudience = configuration.Audience,
                ValidIssuer = configuration.Issuer
            });
            if (!result.IsValid || result.SecurityToken is not JsonWebToken jwt)
                return null;
            using var document = JsonDocument.Parse(Base64UrlEncoder.DecodeBytes(jwt.EncodedPayload));
            var payload = document.RootElement;
            if (payload.EnumerateObject().GroupBy(property => property.Name, StringComparer.Ordinal).Any(group => group.Count() != 1))
                return null;
            var subject = String(payload, "sub");
            if (subject is null || subject.Length > 512)
                return null;
            var parts = subject.Split('/');
            if (parts.Length != 3 || parts[0] != "actor"
                || parts[1] != configuration.Space && !configuration.AdditionalSpaces.Contains(parts[1], StringComparer.Ordinal)
                || !Identifier(parts[1], false) || !Identifier(parts[2], true))
                return null;
            if (payload.TryGetProperty("iat", out var issuedAt)
                && (!issuedAt.TryGetInt64(out var issued) || issued > DateTimeOffset.UtcNow.ToUnixTimeSeconds()))
                return null;
            string? uid;
            if (configuration.ClaimFormat == "substrate")
            {
                if (!payload.TryGetProperty(configuration.ActorClaim, out var identity) || identity.ValueKind != JsonValueKind.Object
                    || identity.EnumerateObject().GroupBy(property => property.Name, StringComparer.Ordinal).Any(group => group.Count() != 1)
                    || String(identity, "atespace") != parts[1] || String(identity, "actorName") != parts[2]
                    || payload.TryGetProperty("atespace", out var space) && (space.ValueKind != JsonValueKind.String
                        || space.GetString() != parts[1]))
                    return null;
                uid = String(identity, "actorUID");
            }
            else if (configuration.ClaimFormat == "custom")
            {
                if (String(payload, configuration.SpaceClaim) != parts[1])
                    return null;
                uid = String(payload, configuration.ActorUidClaim);
            }
            else return null;
            if (string.IsNullOrWhiteSpace(uid) || uid.Length > 256 || uid.Any(char.IsControl))
                return null;
            using var registry = JsonDocument.Parse(await File.ReadAllTextAsync(configuration.ActorRegistryFile));
            var actors = registry.RootElement.GetProperty("actors");
            if (actors.ValueKind != JsonValueKind.Object || !actors.TryGetProperty(uid, out var actor)
                || String(actor, "subject") != subject || String(actor, "profile") is not { } profile
                || !configuration.Profiles.Contains(profile, StringComparer.Ordinal)
                || configuration.ClaimFormat == "custom" && String(payload, configuration.ProfileClaim) != profile)
                return null;
            return new(uid, subject, profile);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException
            or InvalidOperationException or SecurityTokenException or JsonException or KeyNotFoundException)
        {
            return null;
        }
    }

    private static string? String(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static bool Identifier(string value, bool allowDot) => value.Length > 0
        && value.All(character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_' || allowDot && character == '.');

    internal static bool MatchesSecret(string actual, string expected) =>
        CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(actual)),
            SHA256.HashData(Encoding.UTF8.GetBytes(expected)));
}

internal sealed record BrokerActor(string Subject, string Profile);

internal sealed class BrokerCredentials
{
    public string BridgeKey { get; }
    public IReadOnlyDictionary<string, BrokerActor> Actors { get; }

    public BrokerCredentials(string path)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        BridgeKey = document.RootElement.GetProperty("bridgeKey").GetString()
            ?? throw new InvalidOperationException("Missing Agency bridge credential.");
        if (BridgeKey.Length < 32 || BridgeKey.Any(char.IsWhiteSpace) || BridgeKey.Any(char.IsControl))
            throw new InvalidOperationException("Agency bridge credential is invalid.");
        Actors = document.RootElement.GetProperty("actors").EnumerateObject().ToDictionary(actor => actor.Name,
            actor => new BrokerActor(actor.Value.GetProperty("subject").GetString()!,
                actor.Value.GetProperty("profile").GetString()!), StringComparer.Ordinal);
    }

    public bool Matches(ActorIdentity identity) => Actors.TryGetValue(identity.Uid, out var actor)
        && actor.Subject == identity.Subject && actor.Profile == identity.Profile;
}
