using System.Collections.Concurrent;
using System.Net;
using System.Text.Json;

namespace AdoDevProxy;

internal sealed record RequestRule(string? Identity, string Operation);

internal sealed class RequestPolicy(ProxySettings settings)
{
    private readonly List<(Uri Root, string Identity)> nugetRoots = [];
    private readonly ConcurrentDictionary<string, DateTimeOffset> downloads = new(StringComparer.Ordinal);

    internal bool CanConnect(Uri uri) =>
        uri.Scheme == "https" && uri.Port == 443 && uri.UserInfo.Length == 0 && uri.AbsolutePath == "/" &&
        uri.Query.Length == 0 && uri.Fragment.Length == 0 && ProxySettings.IsDnsName(uri.Host) &&
        (uri.Host == "dev.azure.com" || uri.Host == "pkgs.dev.azure.com" ||
         downloads.Any(d => d.Value > DateTimeOffset.UtcNow && new Uri(d.Key).Host == uri.Host));

    internal RequestRule? Evaluate(string method, string rawUrl)
    {
        if (!TryUri(rawUrl, out var uri)) return null;
        var read = method is "GET" or "HEAD";
        if (downloads.TryGetValue(uri.AbsoluteUri, out var expiry) && expiry > DateTimeOffset.UtcNow)
            return read ? new(null, "download") : null;
        if (uri.Host == "dev.azure.com")
        {
            var segments = Uri.UnescapeDataString(uri.AbsolutePath).Trim('/').Split('/');
            var org = settings.Organizations.FirstOrDefault(o => o.Name.Equals(segments[0], StringComparison.OrdinalIgnoreCase));
            var git = Array.FindIndex(segments, s => s.Equals("_git", StringComparison.OrdinalIgnoreCase));
            if (org is null || git is not (1 or 2) || segments.Length < git + 3) return null;
            var endpoint = string.Join('/', segments.Skip(git + 2));
            if (read && endpoint == "info/refs" &&
                uri.Query is "?service=git-upload-pack" or "?service=git-receive-pack")
                return new(org.Identity, "git-discovery");
            if (method == "POST" && uri.Query.Length == 0 && endpoint is "git-upload-pack" or "git-receive-pack")
                return new(org.Identity, endpoint);
            return null;
        }
        foreach (var feed in settings.Feeds)
        {
            if (feed.NpmRegistry is not null && Below(uri, new Uri(feed.NpmRegistry)))
            {
                if (read) return new(feed.Identity, "npm-read");
                var suffix = uri.AbsolutePath[new Uri(feed.NpmRegistry).AbsolutePath.Length..];
                if (method == "POST" && uri.Query.Length == 0 &&
                    suffix is "-/npm/v1/security/advisories/bulk" or "-/npm/v1/security/audits/quick")
                    return new(feed.Identity, "npm-audit");
            }
            if (feed.NugetIndex is not null && Below(uri, new Uri(new Uri(feed.NugetIndex), "./")) && read)
                return new(feed.Identity, "nuget-read");
        }
        foreach (var root in nugetRoots)
            if (read && Below(uri, root.Root)) return new(root.Identity, "nuget-read");
        return null;
    }

    internal void AddNugetResources(FeedSettings feed, JsonElement index)
    {
        var resources = index.GetProperty("resources").EnumerateArray().ToArray();
        string? Identifier(string type) => resources.FirstOrDefault(r => r.GetProperty("@type").GetString() == type)
            is { ValueKind: JsonValueKind.Object } resource ? resource.GetProperty("@id").GetString() : null;
        var feedId = Identifier("VssFeedId");
        var projectId = Identifier("AzureDevOpsProjectId");
        if (feedId is null || !Guid.TryParse(feedId.Replace("urn:uuid:", "", StringComparison.Ordinal), out var feedGuid))
            throw new InvalidOperationException("Feed index does not identify its Azure Artifacts feed.");
        var projectSegment = "";
        if (projectId is not null)
        {
            if (!Guid.TryParse(projectId.Replace("urn:uuid:", "", StringComparison.Ordinal), out var projectGuid))
                throw new InvalidOperationException("Invalid feed project identity.");
            projectSegment = projectGuid + "/";
        }
        var expected = $"/{feed.Organization}/{projectSegment}_packaging/{feedGuid}/nuget/v3/";
        foreach (var resource in index.GetProperty("resources").EnumerateArray())
        {
            var type = resource.GetProperty("@type").GetString() ?? "";
            if (!type.StartsWith("RegistrationsBaseUrl", StringComparison.Ordinal) &&
                !type.StartsWith("PackageBaseAddress", StringComparison.Ordinal) &&
                !type.StartsWith("SearchQueryService", StringComparison.Ordinal) &&
                !type.StartsWith("SearchAutocompleteService", StringComparison.Ordinal) &&
                !type.StartsWith("RepositorySignatures", StringComparison.Ordinal) &&
                !type.StartsWith("VulnerabilityInfo", StringComparison.Ordinal)) continue;
            if (!TryUri(resource.GetProperty("@id").GetString() ?? "", out var uri) ||
                uri.Host != "pkgs.dev.azure.com" || uri.Query.Length != 0 ||
                !uri.AbsolutePath.StartsWith(expected, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Feed advertised an unsupported resource; configure/inspect it before use.");
            if (nugetRoots.Any(r => r.Root == uri && r.Identity != feed.Identity))
                throw new InvalidOperationException("Resource belongs to conflicting identities.");
            nugetRoots.Add((uri, feed.Identity));
        }
    }

    internal bool AllowRedirect(RequestRule source, Uri location)
    {
        if (!TryUri(location.AbsoluteUri, out var uri)) return false;
        if (Evaluate("GET", uri.AbsoluteUri) is { } target)
            return target.Identity is null || target.Identity == source.Identity;
        if (!settings.DownloadHosts.Contains(uri.Host, StringComparer.OrdinalIgnoreCase)) return false;
        foreach (var expired in downloads.Where(d => d.Value <= DateTimeOffset.UtcNow))
            downloads.TryRemove(expired.Key, out _);
        if (downloads.Count >= 512) return false;
        downloads[uri.AbsoluteUri] = DateTimeOffset.UtcNow.AddMinutes(10);
        return true;
    }

    private static bool Below(Uri uri, Uri root) =>
        uri.Host == root.Host && uri.Port == root.Port &&
        (root.AbsolutePath.EndsWith('/')
            ? uri.AbsolutePath.StartsWith(root.AbsolutePath, StringComparison.OrdinalIgnoreCase)
            : uri.AbsolutePath.Equals(root.AbsolutePath, StringComparison.OrdinalIgnoreCase));

    internal static bool TryUri(string raw, out Uri uri)
    {
        uri = null!;
        if (!raw.StartsWith("https://", StringComparison.OrdinalIgnoreCase) ||
            raw.Any(c => c is '\\' or '#' || char.IsControl(c)) ||
            !Uri.TryCreate(raw, UriKind.Absolute, out var parsed) || parsed.Port != 443 ||
            parsed.UserInfo.Length != 0 || !ProxySettings.IsDnsName(parsed.Host)) return false;
        var pathStart = raw.IndexOf('/', 8);
        var path = pathStart < 0 ? "" : raw[pathStart..].Split('?')[0];
        foreach (var segment in path.Split('/'))
        {
            var decoded = Uri.UnescapeDataString(segment);
            if (decoded.Split('/').Any(s => s is "." or "..") ||
                decoded.Contains('%') || decoded.Contains('\\') || decoded.Any(char.IsControl))
                return false;
        }
        uri = parsed;
        return true;
    }
}
