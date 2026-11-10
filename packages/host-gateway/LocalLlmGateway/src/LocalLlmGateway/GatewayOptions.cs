using System.CommandLine;
using System.CommandLine.Parsing;
using System.Net;

namespace LocalLlmGateway;

public sealed class GatewayOptions
{
    public string Host { get; set; } = "127.0.0.1";
    public int Port { get; set; } = 4141;
    public int MaxConcurrentRequests { get; set; } = 4;
    public int RequestTimeoutSeconds { get; set; } = 1800;
    public long MaxRequestBytes { get; set; } = 30_000_000;
    public string AccountType { get; set; } = "individual";
    public string DataDir { get; set; } = DefaultDataDir;
    public string? GatewayKeyFile { get; set; }

    public static string DefaultDataDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "local-llm-gateway");

    public Uri ListenUri => new UriBuilder("http", Host, Port).Uri;

    public void Validate()
    {
        if (Host != "localhost" && !IPAddress.TryParse(Host, out _))
            throw new GatewayException("Host must be localhost or an IP address.");
        if (Port is < 1 or > 65535)
            throw new GatewayException("Port must be between 1 and 65535.");
        if (MaxConcurrentRequests < 1 || RequestTimeoutSeconds is < 1 or > 4_294_967
            || MaxRequestBytes is < 1 or > int.MaxValue)
            throw new GatewayException("Concurrency must be positive, timeout must be 1-4294967 seconds, and request size must be 1-2147483647 bytes.");
        if (AccountType is not ("individual" or "business" or "enterprise"))
            throw new GatewayException("Account type must be individual, business, or enterprise.");
        if (string.IsNullOrWhiteSpace(DataDir))
            throw new GatewayException("Data directory must not be empty.");
        DataDir = Path.GetFullPath(DataDir);
        if (GatewayKeyFile is not null)
            GatewayKeyFile = Path.GetFullPath(GatewayKeyFile);
    }
}

public sealed class GatewayConfiguration
{
    private readonly Option<string?> config = new("--config") { Description = "JSON configuration file." };
    private readonly (string Property, string Environment, Option<string?> Option)[] settings =
    [
        ("Host", "HOST", new("--host") { Description = "Listen IP address (default: 127.0.0.1)." }),
        ("Port", "PORT", new("--port") { Description = "Listen port (default: 4141)." }),
        ("MaxConcurrentRequests", "MAX_CONCURRENT_REQUESTS", new("--max-concurrent-requests") { Description = "Active inference limit (default: 4)." }),
        ("RequestTimeoutSeconds", "REQUEST_TIMEOUT_SECONDS", new("--request-timeout-seconds") { Description = "Inference deadline (default: 1800 seconds)." }),
        ("MaxRequestBytes", "MAX_REQUEST_BYTES", new("--max-request-bytes") { Description = "Maximum request body size (default: 30000000)." }),
        ("AccountType", "ACCOUNT_TYPE", new("--account-type") { Description = "individual, business, or enterprise." }),
        ("DataDir", "DATA_DIR", new("--data-dir") { Description = "Host credential directory." }),
        ("GatewayKeyFile", "KEY_FILE", new("--gateway-key-file") { Description = "File containing the gateway key; never a GitHub token." })
    ];

    public void AddTo(RootCommand root)
    {
        config.Recursive = true;
        root.Options.Add(config);
        foreach (var setting in settings)
        {
            setting.Option.Recursive = true;
            root.Options.Add(setting.Option);
        }
    }

    public GatewayOptions Load(ParseResult result, Func<string, string?>? environment = null, string? defaultConfig = null)
    {
        environment ??= Environment.GetEnvironmentVariable;
        var selectedFile = result.GetValue(config) ?? environment("LOCAL_LLM_GATEWAY_CONFIG");
        var configPath = Path.GetFullPath(selectedFile ?? defaultConfig
            ?? Path.Combine(GatewayOptions.DefaultDataDir, "config.json"));
        try
        {
            var builder = new ConfigurationBuilder()
                .AddJsonFile(configPath, optional: selectedFile is null, reloadOnChange: false);
            foreach (var setting in settings)
            {
                var value = result.GetValue(setting.Option) ?? environment("LOCAL_LLM_GATEWAY_" + setting.Environment);
                if (value is not null)
                    builder.AddInMemoryCollection([new(setting.Property, value)]);
            }
            var options = builder.Build().Get<GatewayOptions>() ?? new GatewayOptions();
            options.Validate();
            return options;
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or FormatException or ArgumentException)
        {
            throw new GatewayException("Cannot load configuration. Check the file, setting names, types, and paths.");
        }
    }

    public static async Task<string> LoadGatewayKeyAsync(GatewayOptions options, CancellationToken cancellationToken,
        Func<string, string?>? environment = null)
    {
        environment ??= Environment.GetEnvironmentVariable;
        var key = environment("LOCAL_LLM_GATEWAY_KEY");
        if (key is not null && options.GatewayKeyFile is not null)
            throw new GatewayException("Set either LOCAL_LLM_GATEWAY_KEY or a gateway key file, not both.");
        if (options.GatewayKeyFile is not null)
        {
            try
            {
                key = (await File.ReadAllTextAsync(options.GatewayKeyFile, cancellationToken)).Trim();
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                throw new GatewayException("Cannot read the gateway key file.");
            }
        }
        if (string.IsNullOrWhiteSpace(key) || key.Length < 32 || key.Any(char.IsWhiteSpace) || key.Any(char.IsControl))
            throw new GatewayException("Provide a gateway key of at least 32 non-whitespace characters via LOCAL_LLM_GATEWAY_KEY or --gateway-key-file.");
        return key;
    }
}
