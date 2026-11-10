using System.Text.Json;

namespace LocalLlmGateway;

public sealed class ModelCatalog(CopilotClient client, TimeProvider? clock = null) : IDisposable
{
    private readonly TimeProvider clock = clock ?? TimeProvider.System;
    private readonly SemaphoreSlim refreshLock = new(1, 1);
    private Snapshot? snapshot;

    public async Task<IReadOnlyDictionary<string, JsonElement>> GetAsync(CancellationToken cancellationToken)
    {
        var current = Volatile.Read(ref snapshot);
        if (current is not null && clock.GetUtcNow() < current.ExpiresAt)
            return current.Models;
        await refreshLock.WaitAsync(cancellationToken);
        try
        {
            current = snapshot;
            if (current is not null && clock.GetUtcNow() < current.ExpiresAt)
                return current.Models;
            using var response = await client.GetModelsAsync(cancellationToken);
            if (response.RootElement.ValueKind != JsonValueKind.Object
                || !response.RootElement.TryGetProperty("data", out var entries) || entries.ValueKind != JsonValueKind.Array)
                throw new GatewayException("Copilot returned an invalid model catalog.", 502);
            var models = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
            foreach (var entry in entries.EnumerateArray())
            {
                if (entry.ValueKind != JsonValueKind.Object
                    || !entry.TryGetProperty("id", out var id) || id.ValueKind != JsonValueKind.String
                    || string.IsNullOrWhiteSpace(id.GetString()))
                    throw new GatewayException("Copilot returned an invalid model entry.", 502);
                if (IsCompatible(entry))
                    models[id.GetString()!] = entry.Clone();
            }
            current = new Snapshot(models, clock.GetUtcNow().AddMinutes(5));
            Volatile.Write(ref snapshot, current);
            return current.Models;
        }
        finally { refreshLock.Release(); }
    }

    private static bool IsCompatible(JsonElement entry)
    {
        if (entry.TryGetProperty("policy", out var policy) && policy.ValueKind == JsonValueKind.Object
            && policy.TryGetProperty("state", out var state) && state.GetString() == "disabled")
            return false;
        if (!SupportsEndpoint(entry, "/responses") && !SupportsEndpoint(entry, "/chat/completions"))
            return false;
        if (entry.TryGetProperty("capabilities", out var capabilities) && capabilities.ValueKind == JsonValueKind.Object)
        {
            if (capabilities.TryGetProperty("type", out var type) && type.GetString() == "embeddings")
                return false;
            if (capabilities.TryGetProperty("supports", out var supports) && supports.ValueKind == JsonValueKind.Object
                && supports.TryGetProperty("tool_calls", out var tools) && tools.ValueKind == JsonValueKind.False)
                return false;
        }
        // Missing metadata is not proof of incompatibility; the live probe remains authoritative.
        return true;
    }

    public static bool SupportsEndpoint(JsonElement entry, string endpoint)
    {
        if (endpoint is not ("/responses" or "/chat/completions"))
            return false;
        if (!entry.TryGetProperty("supported_endpoints", out var endpoints))
            return endpoint == "/responses"; // Preserve legacy Responses admission, without inferring Chat support.
        return endpoints.ValueKind == JsonValueKind.Array
            && endpoints.EnumerateArray().Any(value => value.ValueKind == JsonValueKind.String && value.GetString() == endpoint);
    }

    public void Dispose() => refreshLock.Dispose();

    private sealed record Snapshot(IReadOnlyDictionary<string, JsonElement> Models, DateTimeOffset ExpiresAt);
}
