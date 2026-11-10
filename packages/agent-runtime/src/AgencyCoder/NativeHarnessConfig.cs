using System.Globalization;
using System.Security.Cryptography;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed record NativeMcpServer(string Name, Uri Url, IReadOnlyDictionary<string, string> Headers,
    string[] Tools, int TimeoutMilliseconds, bool RequireApproval);

public sealed record NativeHarnessConfig(string Model, string ProviderType, Uri BaseUrl, string WireApi,
    IReadOnlyDictionary<string, string> Headers, string Instruction, NativeMcpServer[] McpServers, string ConfigSha256)
{
    public static NativeHarnessConfig Create(NativeAgentOptions agent, string credentialPlaceholder,
        IConfiguration configuration)
    {
        var servers = agent.HttpTools.Select((server, index) =>
            MapServer(server, index, credentialPlaceholder)).ToArray();
        var path = configuration["AGENT_COMPILED_CONFIG_PATH"]
            ?? throw new InvalidOperationException("missing_agent_compiled_config_path");
        var hash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
        return new(agent.Model.Model, "openai", ThrowIfUnapprovedUri(agent.Model.OpenAi.BaseUrl), "responses",
            ThrowIfUnapprovedHeaders(agent.Model.Headers, credentialPlaceholder), agent.Instruction, servers, hash);
    }

    private static NativeMcpServer MapServer(NativeToolOptions server, int index, string credentialPlaceholder) =>
        new($"native-http-{index}", ThrowIfUnapprovedUri(server.Params.Url),
            ThrowIfUnapprovedHeaders(server.Params.Headers, credentialPlaceholder), [.. server.Tools],
            ParseTimeout(server.Params.Timeout), server.RequireApproval);

    public static string Redact(string value, IEnumerable<string> credentials)
    {
        foreach (var credential in credentials.Where(item => item.Length >= 6))
            value = value.Replace(credential, "[REDACTED]", StringComparison.Ordinal);
        return value;
    }

    public static Uri ThrowIfUnapprovedUri(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttps
            || uri.Host.Length == 0 || uri.UserInfo.Length != 0 || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment))
            throw new InvalidOperationException("native_https_required");
        return uri;
    }

    public static int ParseTimeout(string? value)
    {
        if (value is null)
            return 30_000;
        if (!value.EndsWith('s') || !decimal.TryParse(value[..^1], NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture, out var seconds) || seconds <= 0 || seconds > 2_147_483.647m
            || decimal.Round(seconds * 1000) != seconds * 1000)
            throw new InvalidOperationException("invalid_native_timeout");
        return (int)(seconds * 1000);
    }

    public static IReadOnlyDictionary<string, string> ThrowIfUnapprovedHeaders(
        Dictionary<string, string>? headers, string credentialPlaceholder)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var (name, content) in headers ?? [])
        {
            if (name.Contains('\r') || name.Contains('\n') || content.Contains('\r') || content.Contains('\n')
                || content.Contains("__KAGENT_ENV[", StringComparison.Ordinal)
                || name is "Cookie" or "Set-Cookie" or "Proxy-Authorization")
                throw new InvalidOperationException("invalid_native_header");
            var sensitive = name.Equals("Authorization", StringComparison.OrdinalIgnoreCase)
                || name.EndsWith("api-key", StringComparison.OrdinalIgnoreCase)
                || name.EndsWith("token", StringComparison.OrdinalIgnoreCase);
            if (sensitive && content != credentialPlaceholder && content != "Bearer " + credentialPlaceholder)
                throw new InvalidOperationException("native_credential_not_projected");
            result.Add(name, content);
        }
        return result;
    }
}

public sealed class NativeAgentOptions
{
    [ConfigurationKeyName("name")]
    public string? Name { get; init; }
    [ConfigurationKeyName("description")]
    public string? Description { get; init; }
    [ConfigurationKeyName("model")]
    public required NativeModelOptions Model { get; init; }
    [ConfigurationKeyName("instruction")]
    public required string Instruction { get; init; }
    [ConfigurationKeyName("stream")]
    public bool Stream { get; init; } = true;
    [ConfigurationKeyName("session_db_url")]
    public string? SessionDbUrl { get; init; }
    [ConfigurationKeyName("http_tools")]
    public List<NativeToolOptions> HttpTools { get; init; } = [];
    [ConfigurationKeyName("sse_tools")]
    public List<NativeToolOptions> SseTools { get; init; } = [];
}

public class NativeTransportOptions
{
    [ConfigurationKeyName("tls_insecure_skip_verify")]
    public bool TlsInsecureSkipVerify { get; init; }
    [ConfigurationKeyName("tls_disable_system_cas")]
    public bool TlsDisableSystemCas { get; init; }
    [ConfigurationKeyName("tls_ca_cert_path")]
    public string? TlsCaCertPath { get; init; }
    [ConfigurationKeyName("api_key_passthrough")]
    public bool ApiKeyPassthrough { get; init; }
}

public sealed class NativeModelOptions : NativeTransportOptions
{
    [ConfigurationKeyName("model")]
    public required string Model { get; init; }
    [ConfigurationKeyName("openai")]
    public required NativeOpenAiOptions OpenAi { get; init; }
    [ConfigurationKeyName("headers")]
    public Dictionary<string, string>? Headers { get; init; }
}

public sealed class NativeOpenAiOptions
{
    [ConfigurationKeyName("base_url")]
    public required string BaseUrl { get; init; }
    [ConfigurationKeyName("api_format")]
    public required string ApiFormat { get; init; }
}

public sealed class NativeToolOptions
{
    [ConfigurationKeyName("params")]
    public required NativeToolParamsOptions Params { get; init; }
    [ConfigurationKeyName("tools")]
    public required List<string> Tools { get; init; }
    [ConfigurationKeyName("require_approval")]
    public bool RequireApproval { get; init; }
    [ConfigurationKeyName("token_passthrough")]
    public bool TokenPassthrough { get; init; }
    [ConfigurationKeyName("allowed_headers")]
    public List<string> AllowedHeaders { get; init; } = [];
}

public sealed class NativeToolParamsOptions : NativeTransportOptions
{
    [ConfigurationKeyName("url")]
    public required string Url { get; init; }
    [ConfigurationKeyName("headers")]
    public Dictionary<string, string>? Headers { get; init; }
    [ConfigurationKeyName("timeout")]
    public string? Timeout { get; init; }
    [ConfigurationKeyName("terminate_on_close")]
    public bool? TerminateOnClose { get; init; }
}

public sealed class NativeOptionsValidator : IValidateOptions<NativeAgentOptions>
{
    private readonly IConfiguration configuration;

    public NativeOptionsValidator(IConfiguration configuration) => this.configuration = configuration;

    public ValidateOptionsResult Validate(string? name, NativeAgentOptions agent)
    {
        var failures = new List<string>();
        AddUnknownKeyFailures(configuration.GetSection("agent"), [
            "name", "description", "model", "instruction", "stream", "session_db_url", "http_tools", "sse_tools",
        ], failures);
        AddUnknownKeyFailures(configuration.GetSection("agent:model"), [
            "model", "headers", "openai", "tls_insecure_skip_verify", "tls_disable_system_cas", "tls_ca_cert_path",
            "api_key_passthrough",
        ], failures);
        AddUnknownKeyFailures(configuration.GetSection("agent:model:openai"), ["base_url", "api_format"], failures);
        if (agent.Model is null)
        {
            failures.Add("Native model configuration is required.");
            return ValidateOptionsResult.Fail(failures);
        }
        var model = agent.Model;
        AddTransportFailures(model, failures);
        if (model.OpenAi is null)
            failures.Add("Native OpenAI configuration is required.");
        else if (model.OpenAi.ApiFormat != "responses")
            failures.Add("Only Responses API format is supported.");
        if (!agent.Stream || string.IsNullOrWhiteSpace(agent.Instruction) || string.IsNullOrWhiteSpace(model.Model))
            failures.Add("Streaming, instruction and model are required.");
        if (agent.SessionDbUrl is not null && agent.SessionDbUrl != "sqlite+aiosqlite:////data/sessions.db")
            failures.Add("Unsupported native session store.");
        if (agent.SseTools?.Count != 0)
            failures.Add("Explicit SSE transport is unsupported by the pinned .NET SDK.");
        foreach (var server in agent.HttpTools ?? [])
        {
            if (server.Params is null)
            {
                failures.Add("Native MCP params are required.");
                continue;
            }
            AddTransportFailures(server.Params, failures);
            if (server.TokenPassthrough || server.AllowedHeaders.Count != 0)
                failures.Add("Token passthrough and allowed_headers are unsupported.");
            if (server.Params.TerminateOnClose == false)
                failures.Add("HTTP MCP termination must remain enabled.");
            if (server.Tools is null || server.Tools.Count == 0
                || server.Tools.Distinct(StringComparer.Ordinal).Count() != server.Tools.Count
                || server.Tools.Any(tool => tool.Length is < 1 or > 128 || tool.Any(character =>
                    !char.IsAsciiLetterOrDigit(character) && character is not '_' and not '-' and not '.')))
                failures.Add("Native MCP tools must be bounded, unique identifiers.");
        }
        foreach (var server in configuration.GetSection("agent:http_tools").GetChildren())
        {
            AddUnknownKeyFailures(server, ["params", "tools", "require_approval", "token_passthrough", "allowed_headers"],
                failures);
            AddUnknownKeyFailures(server.GetSection("params"), [
                "url", "headers", "timeout", "terminate_on_close", "tls_insecure_skip_verify",
                "tls_disable_system_cas", "tls_ca_cert_path",
            ], failures);
        }
        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }

    private static void AddTransportFailures(NativeTransportOptions transport, List<string> failures)
    {
        if (transport.TlsInsecureSkipVerify || transport.TlsDisableSystemCas || transport.TlsCaCertPath is not null
            || transport.ApiKeyPassthrough)
            failures.Add("TLS and credential overrides are unsupported.");
    }

    private static void AddUnknownKeyFailures(IConfigurationSection section, string[] allowed, List<string> failures)
    {
        foreach (var child in section.GetChildren())
            if (!allowed.Contains(child.Key, StringComparer.OrdinalIgnoreCase))
                failures.Add($"Unsupported native setting '{section.Path}:{child.Key}'.");
    }
}
