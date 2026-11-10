using System.Net;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace AdoDevProxy;

internal sealed class ProxySettings
{
    public string ListenAddress { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 5480;
    public int ControlPort { get; init; } = 5481;
    public string[] AllowedClients { get; init; } = ["127.0.0.1/32", "::1/128"];
    public IdentitySettings[] Identities { get; init; } = [];
    public OrganizationSettings[] Organizations { get; init; } = [];
    public FeedSettings[] Feeds { get; init; } = [];
    public string[] DownloadHosts { get; init; } = [];

    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };

    internal static ProxySettings Load(string path)
    {
        var settings = JsonSerializer.Deserialize<ProxySettings>(File.ReadAllText(path), JsonOptions)
            ?? throw new InvalidOperationException("Configuration is empty.");
        settings.Validate();
        return settings;
    }

    internal void Validate()
    {
        if (!IPAddress.TryParse(ListenAddress, out var address) || address.Equals(IPAddress.Any) ||
            address.Equals(IPAddress.IPv6Any) || Port is < 1 or > 65535 || ControlPort is < 1 or > 65535 || Port == ControlPort)
            throw new InvalidOperationException("Choose explicit listener addresses and distinct valid ports.");
        if (AllowedClients.Length == 0 || Identities.Length == 0 || Organizations.Length == 0)
            throw new InvalidOperationException("Client, identity, and organization allowlists cannot be empty.");
        foreach (var client in AllowedClients) _ = ClientSubnet.Parse(client);
        RequireUnique(Identities.Select(i => i.Name));
        RequireUnique(Organizations.Select(o => o.Name));
        RequireUnique(Feeds.Select(f => f.Name));
        foreach (var identity in Identities)
            if (!Guid.TryParse(identity.TenantId, out _) || !Guid.TryParse(identity.ClientId, out _))
                throw new InvalidOperationException("Identity tenant and client IDs must be GUIDs.");
        foreach (var organization in Organizations)
        {
            if (!Regex.IsMatch(organization.Name, "^[a-zA-Z0-9][a-zA-Z0-9-]*$"))
                throw new InvalidOperationException("Invalid organization name.");
            _ = Identity(organization.Identity);
        }
        foreach (var feed in Feeds)
        {
            _ = Identity(feed.Identity);
            if (!Organizations.Any(o => o.Name.Equals(feed.Organization, StringComparison.OrdinalIgnoreCase)))
                throw new InvalidOperationException("Feed references an unknown organization.");
            if (feed.NpmRegistry is null && feed.NugetIndex is null)
                throw new InvalidOperationException("Configure at least one feed protocol.");
            foreach (var url in new[] { feed.NpmRegistry, feed.NugetIndex }.OfType<string>())
                if (!RequestPolicy.TryUri(url, out var uri) || uri.Host != "pkgs.dev.azure.com" ||
                    !uri.AbsolutePath.StartsWith($"/{feed.Organization}/", StringComparison.OrdinalIgnoreCase) ||
                    !uri.AbsolutePath.Contains("/_packaging/", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Feeds must be explicit Azure Artifacts URLs in their organization.");
            if (feed.NpmRegistry is not null && !feed.NpmRegistry.EndsWith("/npm/registry/", StringComparison.Ordinal))
                throw new InvalidOperationException("npm registry must end in /npm/registry/.");
            if (feed.NugetIndex is not null && !feed.NugetIndex.EndsWith("/nuget/v3/index.json", StringComparison.Ordinal))
                throw new InvalidOperationException("NuGet source must be a v3 index.");
        }
        foreach (var host in DownloadHosts)
            if (!IsDnsName(host) || host.Equals("dev.azure.com", StringComparison.OrdinalIgnoreCase) ||
                host.Equals("pkgs.dev.azure.com", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Download hosts must be exact separate DNS names, never wildcards.");
    }

    internal IdentitySettings Identity(string name) =>
        Identities.SingleOrDefault(i => i.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
        ?? throw new InvalidOperationException("Unknown identity.");

    internal static bool IsDnsName(string host) =>
        host.Length <= 253 && !host.EndsWith('.') && Uri.CheckHostName(host) == UriHostNameType.Dns &&
        host.Contains('.') && !host.Contains('*') && !IPAddress.TryParse(host, out _);

    private static void RequireUnique(IEnumerable<string> names)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in names)
            if (!Regex.IsMatch(name, "^[a-zA-Z0-9][a-zA-Z0-9-]*$") || !seen.Add(name))
                throw new InvalidOperationException("Names must be unique simple aliases.");
    }
}

internal sealed record IdentitySettings
{
    public string Name { get; init; } = "";
    public string TenantId { get; init; } = "";
    public string ClientId { get; init; } = "04b07795-8ddb-461a-bbee-02f9e1bf7b46";
}

internal sealed record OrganizationSettings
{
    public string Name { get; init; } = "";
    public string Identity { get; init; } = "";
}

internal sealed record FeedSettings
{
    public string Name { get; init; } = "";
    public string Organization { get; init; } = "";
    public string Identity { get; init; } = "";
    public string? NpmRegistry { get; init; }
    public string? NugetIndex { get; init; }
}

internal sealed record ClientSubnet(IPAddress Address, int Prefix)
{
    internal static ClientSubnet Parse(string value)
    {
        var parts = value.Split('/');
        if (parts.Length != 2 || !IPAddress.TryParse(parts[0], out var ip) || !int.TryParse(parts[1], out var prefix) ||
            ip.IsIPv4MappedToIPv6 || prefix < 0 || prefix > ip.GetAddressBytes().Length * 8)
            throw new InvalidOperationException("Clients must be explicit IP CIDRs.");
        return new(ip, prefix);
    }

    internal bool Contains(IPAddress ip)
    {
        if (ip.IsIPv4MappedToIPv6) ip = ip.MapToIPv4();
        var actual = ip.GetAddressBytes();
        var expected = Address.GetAddressBytes();
        if (actual.Length != expected.Length) return false;
        for (var i = 0; i < Prefix; i++)
            if ((actual[i / 8] & (1 << (7 - i % 8))) != (expected[i / 8] & (1 << (7 - i % 8))))
                return false;
        return true;
    }
}
