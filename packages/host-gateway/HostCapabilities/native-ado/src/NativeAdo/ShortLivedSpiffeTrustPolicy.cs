namespace HostCapabilities.NativeAdo;

public sealed record ShortLivedSpiffeTrustPolicy
{
    public TimeSpan MaxLeafLifetime { get; init; } = TimeSpan.FromHours(24);

    internal void Validate()
    {
        if (MaxLeafLifetime <= TimeSpan.Zero || MaxLeafLifetime > TimeSpan.FromHours(24))
        {
            throw new ArgumentException("SPIFFE leaf lifetime must be positive and no greater than 24 hours.");
        }
    }
}
