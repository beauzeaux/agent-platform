using System.IO.Compression;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed record PreparedPlugins(
    string Identity,
    string[] Directories,
    string PolicySha256,
    int ConfigSourceCount);

public sealed class AgencyPluginResolver
{
    private readonly HarnessOptions options;
    private readonly HttpClient client;
    private readonly string root;

    public AgencyPluginResolver(IOptions<HarnessOptions> options, HttpClient client)
    {
        this.options = options.Value;
        this.client = client;
        root = Path.Combine(options.Value.DurableDirectory, "agency", "bundles");
        Directory.CreateDirectory(root);
    }

    public async Task<PreparedPlugins> ResolveAsync(string checkoutRoot, string executionDirectory, bool refresh,
        CancellationToken cancellationToken)
    {
        if (!WorkspaceManager.Confined(checkoutRoot, executionDirectory) || !Directory.Exists(executionDirectory))
            throw new InvalidOperationException("invalid_working_directory");
        var workingDirectory = Path.GetRelativePath(checkoutRoot, executionDirectory)
            .Replace(Path.DirectorySeparatorChar, '/');
        workingDirectory = WorkspaceManager.NormalizeWorkingDirectory(workingDirectory);
        var commit = (await WorkspaceManager.RunAsync(options.GitExecutable, checkoutRoot,
            ["-c", "credential.helper=", "-C", checkoutRoot, "rev-parse", "HEAD"], cancellationToken)).Trim();
        using var policyResponse = await PostAsync(options.AgencyPolicyEndpoint, "{}", cancellationToken);
        using var policy = JsonDocument.Parse(await ReadBoundedAsync(policyResponse, 128 * 1024, cancellationToken));
        var policyHash = policy.RootElement.GetProperty("profileSha256").GetString()
            ?? throw new InvalidOperationException("invalid_agency_policy");
        var body = JsonSerializer.SerializeToUtf8Bytes(new { commit, workingDirectory });
        using var preparedResponse = await PostAsync(options.AgencyPluginsEndpoint, body, cancellationToken);
        using var prepared = JsonDocument.Parse(await ReadBoundedAsync(preparedResponse, 12 * 1024 * 1024, cancellationToken));
        var manifest = prepared.RootElement.GetProperty("manifest");
        if (manifest.GetProperty("profileSha256").GetString() != policyHash
            || manifest.GetProperty("repositoryCommit").GetString() != commit
            || manifest.GetProperty("configResolution").GetString() != "native-cwd-ancestor-v1"
            || manifest.GetProperty("workingDirectory").GetString() != workingDirectory
            || manifest.GetProperty("configSourceCommit").GetString() != commit)
            throw new InvalidOperationException("plugin_manifest_mismatch");
        ValidateConfigSources(manifest, commit, workingDirectory);
        var directories = new List<string>();
        foreach (var bundle in prepared.RootElement.GetProperty("bundles").EnumerateArray())
        {
            var expected = bundle.GetProperty("sha256").GetString()
                ?? throw new InvalidOperationException("plugin_hash_required");
            var content = Convert.FromBase64String(bundle.GetProperty("archive").GetString()!);
            if (Hash(content) != expected || content.Length > 8 * 1024 * 1024)
                throw new InvalidOperationException("plugin_content_mismatch");
            var destination = Path.Combine(root, expected);
            if (refresh && Directory.Exists(destination))
                Directory.Delete(destination, recursive: true);
            if (!Directory.Exists(destination))
                Extract(content, destination);
            directories.Add(destination);
        }
        var identity = manifest.GetProperty("identity").GetString()
            ?? throw new InvalidOperationException("plugin_identity_required");
        if (!IsSha256(identity))
            throw new InvalidOperationException("plugin_identity_mismatch");
        return new(identity, [.. directories], policyHash, manifest.GetProperty("configSources").GetArrayLength());
    }

    private static void ValidateConfigSources(JsonElement manifest, string commit, string workingDirectory)
    {
        var effectiveHash = manifest.GetProperty("effectiveConfigSha256").GetString();
        if (effectiveHash is null || !IsSha256(effectiveHash))
            throw new InvalidOperationException("plugin_manifest_mismatch");
        var sources = manifest.GetProperty("configSources").EnumerateArray().ToArray();
        if (sources.Length > 32)
            throw new InvalidOperationException("plugin_manifest_mismatch");
        var ancestors = Ancestors(workingDirectory);
        var localPaths = new Dictionary<string, int>(StringComparer.Ordinal);
        var lastAncestor = -1;
        for (var index = 0; index < sources.Length; index++)
        {
            var source = sources[index];
            if (source.GetProperty("type").GetString() != "file")
                continue;
            var path = source.GetProperty("path").GetString()
                ?? throw new InvalidOperationException("plugin_manifest_mismatch");
            var separator = path.LastIndexOf('/');
            var directory = separator < 0 ? "" : path[..separator];
            var filename = separator < 0 ? path : path[(separator + 1)..];
            var ancestor = Array.IndexOf(ancestors, directory);
            var format = source.GetProperty("format").GetString();
            if (!IsCanonicalSourcePath(path) || !localPaths.TryAdd(path, index)
                || ancestor <= lastAncestor
                || format != (filename == "agency.toml" ? "toml" :
                    filename == "agency.yaml" ? "yaml" : null)
                || source.GetProperty("commit").GetString() != commit
                || source.GetProperty("sha256").GetString() is not { Length: 64 } hash || !IsSha256(hash))
                throw new InvalidOperationException("plugin_manifest_mismatch");
            lastAncestor = ancestor;
        }
        for (var index = 0; index < sources.Length; index++)
        {
            var source = sources[index];
            var type = source.GetProperty("type").GetString();
            if (type == "remote_config")
                ValidateRemoteConfigSource(source, index, localPaths);
            else if (type != "file")
                throw new InvalidOperationException("plugin_manifest_mismatch");
        }
    }

    private static void ValidateRemoteConfigSource(JsonElement source, int index,
        IReadOnlyDictionary<string, int> localPaths)
    {
        var declaredBy = source.GetProperty("declaredBy").GetString();
        var git = source.GetProperty("git").GetString();
        var path = source.GetProperty("path").GetString();
        var resolvedRef = source.GetProperty("resolvedRef").GetString();
        var format = source.GetProperty("format").GetString();
        var filename = path?.TrimStart('/').Split('/').LastOrDefault();
        var hasRequestedRef = source.TryGetProperty("requestedRef", out var requestedRef);
        var hasRefType = source.TryGetProperty("refType", out var refType);
        if (declaredBy is null || !localPaths.TryGetValue(declaredBy, out var localIndex) || localIndex <= index
            || git is null || !IsBoundedText(git, 2048)
            || path is null || !IsRemotePath(path)
            || resolvedRef is null || !IsBoundedText(resolvedRef, 512)
            || format != (filename == "agency.toml" ? "toml" :
                filename is "agency.yaml" or "agency.yml" ? "yaml" : null)
            || hasRequestedRef != hasRefType
            || hasRequestedRef && (!IsBoundedText(requestedRef.GetString(), 512)
                || refType.GetString() is not ("branch" or "tag" or "rev"))
            || source.GetProperty("sha256").GetString() is not { Length: 64 } hash || !IsSha256(hash))
            throw new InvalidOperationException("plugin_manifest_mismatch");
    }

    private static string[] Ancestors(string workingDirectory)
    {
        if (workingDirectory == ".")
            return [""];
        var segments = workingDirectory.Split('/');
        return [.. Enumerable.Range(0, segments.Length + 1)
            .Select(length => string.Join('/', segments.Take(length)))];
    }

    private static bool IsCanonicalSourcePath(string path) =>
        IsBoundedText(path, 512) && path == path.Replace('\\', '/')
        && path.Split('/').All(segment => segment.Length > 0 && segment != "." && segment != "..");

    private static bool IsRemotePath(string path) =>
        IsBoundedText(path, 512) && !path.Contains('\\')
        && path.Split('/').All(segment => segment != "..");

    private static bool IsBoundedText(string? value, int maximum) =>
        value is { Length: > 0 } && value.Length <= maximum && !value.Any(char.IsControl);

    private static bool IsSha256(string value) =>
        value.Length == 64 && value.All(character => char.IsAsciiHexDigit(character) && !char.IsUpper(character));

    private async Task<HttpResponseMessage> PostAsync(Uri uri, object body, CancellationToken cancellationToken)
    {
        var content = body is string text ? new StringContent(text, Encoding.UTF8, "application/json")
            : new ByteArrayContent((byte[])body);
        using var request = new HttpRequestMessage(HttpMethod.Post, uri) { Content = content };
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", "kagent-credential-injected");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        var response = await client.SendAsync(request, cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            var status = (int)response.StatusCode;
            response.Dispose();
            var operation = uri == options.AgencyPolicyEndpoint ? "policy" : "plugins";
            throw new InvalidOperationException($"host_{operation}_preparation_http_{status}");
        }
        return response;
    }

    internal static void Extract(byte[] content, string destination)
    {
        var staging = destination + ".partial-" + Guid.NewGuid().ToString("N");
        Directory.CreateDirectory(staging);
        try
        {
            using var archive = new ZipArchive(new MemoryStream(content), ZipArchiveMode.Read, leaveOpen: false);
            long bytes = 0;
            foreach (var entry in archive.Entries)
            {
                var normalized = entry.FullName.Replace('\\', '/');
                var segments = normalized.Split('/', StringSplitOptions.RemoveEmptyEntries);
                var hasSymlink = (entry.ExternalAttributes >> 16 & 0xF000) == 0xA000;
                if (hasSymlink || entry.Length < 0 || entry.Length > 8 * 1024 * 1024
                    || normalized.StartsWith('/') || segments.Any(part => part is "." or ".."))
                    throw new InvalidOperationException("unsafe_plugin_path");
                bytes += entry.Length;
                if (bytes > 8 * 1024 * 1024)
                    throw new InvalidOperationException("plugin_content_limit");
            }
            archive.ExtractToDirectory(staging, overwriteFiles: false);
            Directory.Move(staging, destination);
        }
        finally
        {
            if (Directory.Exists(staging))
                Directory.Delete(staging, recursive: true);
        }
    }

    public static string Hash(byte[] content) => Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant();

    private static async Task<byte[]> ReadBoundedAsync(HttpResponseMessage response, int maximum,
        CancellationToken cancellationToken)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var buffer = new MemoryStream();
        var block = new byte[16 * 1024];
        while (true)
        {
            var count = await stream.ReadAsync(block, cancellationToken);
            if (count == 0)
                return buffer.ToArray();
            buffer.Write(block, 0, count);
            if (buffer.Length > maximum)
                throw new InvalidOperationException("host_response_limit");
        }
    }
}
