namespace HostCapabilities.NativeAdo;

public sealed record CredentialGrant(string Identity);

public delegate ValueTask<CredentialGrant?> AuthorizeCredentialAsync(
    string actorSpiffeId, string credentialUri, CancellationToken cancellationToken);

public delegate ValueTask<string> AcquireAdoTokenAsync(string identity, CancellationToken cancellationToken);

public sealed record NativeAdoOptions
{
    public int MaxConcurrency { get; init; } = 4;
    public TimeSpan RequestTimeout { get; init; } = TimeSpan.FromSeconds(15);

    internal void Validate()
    {
        if (MaxConcurrency is < 1 or > 64 || RequestTimeout <= TimeSpan.Zero ||
            RequestTimeout > TimeSpan.FromMinutes(2))
        {
            throw new ArgumentException("Invalid credential-provider limits.");
        }
    }
}

internal static class IdentitySyntax
{
    internal static bool IsSpiffeId(string value)
    {
        if (value.Length is < 12 or > 2048 || !value.StartsWith("spiffe://", StringComparison.Ordinal) ||
            value.Any(c => c > 126 || c <= 32 || c is '%' or '\\' or '?' or '#') ||
            !Uri.TryCreate(value, UriKind.Absolute, out var uri))
        {
            return false;
        }

        return uri.UserInfo.Length == 0 && uri.Authority == uri.Host &&
            uri.AbsoluteUri == value && uri.AbsolutePath.Length > 1 &&
            !uri.AbsolutePath.Contains("//", StringComparison.Ordinal);
    }

    internal static bool IsCredentialUri(string value)
    {
        const string prefix = "ate-secret://windows-wam/";
        return value.StartsWith(prefix, StringComparison.Ordinal) && value.Length is > 25 and <= 2048 &&
            value[prefix.Length..].Split('/').All(segment =>
                segment.Length > 0 && segment is not "." and not ".." &&
                segment.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.'));
    }
}
