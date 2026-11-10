using System.Net;
using System.Text.Json;
using System.Text.Json.Serialization;
using AdoDevProxy;
using LocalLlmGateway;

namespace HostCapabilities;

public sealed class HostConfiguration
{
    public int ControlPort { get; init; } = 14540;
    public string StateDirectory { get; init; } = "";
    public int ShutdownSeconds { get; init; } = 20;
    public LlmConfiguration Llm { get; init; } = new();
    public AdoConfiguration Ado { get; init; } = new();
    public AgencyConfiguration Agency { get; init; } = new();
    public TelemetryConfiguration Telemetry { get; init; } = new();
    public ActorJwtConfiguration ActorJwt { get; init; } = new();
    public NativeAdoConfiguration NativeAdo { get; init; } = new();
    public ImdsConfiguration Imds { get; init; } = new();

    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        RespectNullableAnnotations = true,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };

    public static HostConfiguration Load(string path)
    {
        RequirePath(path);
        var configuration = JsonSerializer.Deserialize<HostConfiguration>(File.ReadAllText(path), JsonOptions)
            ?? throw new InvalidOperationException("Configuration is empty.");
        configuration.Validate();
        return configuration;
    }

    public void Validate()
    {
        RequirePath(StateDirectory);
        if (ShutdownSeconds is < 1 or > 120)
            throw new InvalidOperationException("Shutdown deadline must be between 1 and 120 seconds.");
        var ports = new List<int> { ControlPort };
        if (Llm.Enabled)
        {
            RequirePath(Llm.Options.DataDir);
            RequirePath(Llm.Options.GatewayKeyFile ?? "");
            Llm.Options.Validate();
            if (Llm.Options.MaxConcurrentRequests > 256)
                throw new InvalidOperationException("Model concurrency exceeds the host bound.");
            ports.Add(Llm.Options.Port);
        }
        if (Ado.Enabled)
        {
            RequirePath(Ado.ConfigurationFile);
            RequirePath(Ado.StateDirectory);
            if (Ado.Mode is not ("delegate" or "embedded"))
                throw new InvalidOperationException("ADO mode must be delegate or embedded.");
            var settings = ProxySettings.Load(Ado.ConfigurationFile);
            ports.AddRange([settings.Port, settings.ControlPort]);
            if (Ado.Mode == "delegate" && (Ado.DelegatePort is < 1 or > 65535
                || Ado.DelegateControlPort is < 1 or > 65535
                || ports.Contains(Ado.DelegatePort) || ports.Contains(Ado.DelegateControlPort)))
                throw new InvalidOperationException("ADO delegation must use separate valid loopback ports.");
        }
        if (Agency.Enabled)
        {
            foreach (var path in new[] { Agency.PythonExecutable, Agency.BrokerScript, Agency.BrokerConfigurationFile,
                Agency.StateDirectory, Agency.ProfilesFile, Agency.AgencyExecutable, Agency.GlobalConfigurationFile })
                RequirePath(path);
            if (!IPAddress.TryParse(Agency.ListenAddress, out var address) || address.Equals(IPAddress.Any)
                || address.Equals(IPAddress.IPv6Any))
                throw new InvalidOperationException("Choose an explicit MCP listener IP.");
            ports.AddRange([Agency.Port, Agency.InternalPort]);
            if (Agency.MaxConcurrentRequests is < 1 or > 64 || Agency.MaxConcurrentPlugins is < 1 or > 8
                || Agency.RequestTimeoutSeconds is < 1 or > 2100)
                throw new InvalidOperationException("Agency concurrency or deadline exceeds the host bound.");
            if (Path.TrimEndingDirectorySeparator(Path.GetFullPath(Agency.StateDirectory)).Equals(
                Path.TrimEndingDirectorySeparator(Path.Combine(Path.GetDirectoryName(Agency.BrokerScript)!, "state")),
                StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Use separate Agency state for a composed host.");
        }
        if (NativeAdo.Enabled)
        {
            NativeAdo.Validate();
            ports.Add(NativeAdo.Port);
        }
        if (Imds.Enabled)
        {
            if (!Agency.Enabled || !ActorJwt.Enabled)
                throw new InvalidOperationException("IMDS requires the actor-authenticated Agency front end.");
            Imds.Validate();
        }
        if (ports.Any(port => port is < 1 or > 65535) || ports.Count != ports.Distinct().Count())
            throw new InvalidOperationException("All enabled listeners must use distinct valid ports.");
        if (Ado.Enabled && Ado.Mode == "delegate"
            && (ports.Contains(Ado.DelegatePort) || ports.Contains(Ado.DelegateControlPort)))
            throw new InvalidOperationException("Delegated ADO ports cannot overlap any composed listener.");
        if (Telemetry.OtlpEndpoint is { } endpoint
            && (!Uri.TryCreate(endpoint, UriKind.Absolute, out var uri) || uri.Scheme != "https"
                || uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0
                || uri.AbsolutePath != "/"))
            throw new InvalidOperationException("OTLP requires a TLS origin without user information, path or query.");
        if (Telemetry.OtlpEndpoint is not null)
        {
            RequirePath(Telemetry.CaCertificateFile ?? "");
            RequirePath(Telemetry.ClientCertificateFile ?? "");
            if (Telemetry.ClientCertificatePasswordFile is not null)
                RequirePath(Telemetry.ClientCertificatePasswordFile);
            if (Telemetry.TrustMode == "pinned-private")
                RequirePath(Telemetry.ServerCertificateFile ?? "");
        }
        if (Telemetry.TrustMode is not ("online" or "pinned-private"))
            throw new InvalidOperationException("Exporter trust mode must be online or pinned-private.");
        ActorJwt.Validate();
        if (ActorJwt.Enabled && Agency.Enabled && !Path.GetFullPath(ActorJwt.ActorRegistryFile).Equals(
            Path.GetFullPath(Agency.BrokerConfigurationFile), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("JWT admission must use the authoritative Agency broker actor registry.");
    }

    internal static void RequirePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path))
            throw new InvalidOperationException("Host-owned paths must be explicit absolute paths.");
    }
}

public sealed class ImdsConfiguration
{
    public bool Enabled { get; init; }
    public string ConfigurationFile { get; init; } = "";
    public string StateDirectory { get; init; } = "";
    public string Identity { get; init; } = "";
    public string[] AllowedProfiles { get; init; } = [];
    public Dictionary<string, string[]> ResourcesByProfile { get; init; } = [];

    internal void Validate()
    {
        HostConfiguration.RequirePath(ConfigurationFile);
        HostConfiguration.RequirePath(StateDirectory);
        var settings = ProxySettings.Load(ConfigurationFile);
        _ = settings.Identity(Identity);
        if (ResourcesByProfile.Count == 0
            && (AllowedProfiles.Length == 0 || AllowedProfiles.Any(string.IsNullOrWhiteSpace)
                || AllowedProfiles.Distinct(StringComparer.Ordinal).Count() != AllowedProfiles.Length))
            throw new InvalidOperationException("IMDS requires exact allowed actor profiles.");
        if (ResourcesByProfile.Any(pair => string.IsNullOrWhiteSpace(pair.Key) || pair.Value.Length == 0
                || pair.Value.Any(resource => resource is not (ImdsEndpoint.AzureDevOpsResource
                    or ImdsEndpoint.AzureManagementResource))
                || pair.Value.Distinct(StringComparer.Ordinal).Count() != pair.Value.Length))
            throw new InvalidOperationException("IMDS resources must be exact supported values grouped by profile.");
    }

    internal bool Allows(string profile, string resource) =>
        ResourcesByProfile.TryGetValue(profile, out var resources)
            ? resources.Contains(resource, StringComparer.Ordinal)
            : resource == ImdsEndpoint.AzureDevOpsResource
                && AllowedProfiles.Contains(profile, StringComparer.Ordinal);
}

public sealed class NativeAdoConfiguration
{
    public bool Enabled { get; init; }
    public string ListenAddress { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 5443;
    public string ConfigurationFile { get; init; } = "";
    public string StateDirectory { get; init; } = "";
    public string ServingCertificateFile { get; init; } = "";
    public string? ServingCertificatePasswordFile { get; init; }
    public string GatewaySpiffeId { get; init; } = "";
    public string[] GatewayRootFiles { get; init; } = [];
    public string[] GatewayIntermediateFiles { get; init; } = [];
    public string TrustMode { get; init; } = "online";
    public int MaxLeafLifetimeHours { get; init; } = 24;
    public int MaxConcurrency { get; init; } = 4;
    public int RequestTimeoutSeconds { get; init; } = 15;
    public int MinimumTokenLifetimeSeconds { get; init; } = 480;
    public NativeAdoGrant[] Grants { get; init; } = [];

    internal void Validate()
    {
        foreach (var path in new[] { ConfigurationFile, StateDirectory, ServingCertificateFile }
            .Concat(GatewayRootFiles).Concat(GatewayIntermediateFiles))
            HostConfiguration.RequirePath(path);
        if (ServingCertificatePasswordFile is not null) HostConfiguration.RequirePath(ServingCertificatePasswordFile);
        if (!IPAddress.TryParse(ListenAddress, out var address) || address.Equals(IPAddress.Any)
            || address.Equals(IPAddress.IPv6Any) || !IsSpiffe(GatewaySpiffeId)
            || GatewayRootFiles.Length == 0 || Grants.Length == 0
            || TrustMode is not ("online" or "short-lived-spiffe") || MaxLeafLifetimeHours is < 1 or > 24
            || MaxConcurrency is < 1 or > 64 || RequestTimeoutSeconds is < 1 or > 120
            || MinimumTokenLifetimeSeconds is < 360 or > 3600)
            throw new InvalidOperationException("Native ADO requires explicit mTLS, actor grants and bounded token lifetime.");
        var settings = ProxySettings.Load(ConfigurationFile);
        var pairs = new HashSet<(string Actor, string Uri)>();
        foreach (var grant in Grants)
        {
            if (!IsSpiffe(grant.ActorSpiffeId)
                || !grant.CredentialUri.StartsWith("ate-secret://windows-wam/", StringComparison.Ordinal)
                || !grant.CredentialUri["ate-secret://windows-wam/".Length..].Split('/').All(segment =>
                    segment.Length > 0 && segment is not "." and not ".."
                    && segment.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.'))
                || string.IsNullOrWhiteSpace(grant.Profile) || !pairs.Add((grant.ActorSpiffeId, grant.CredentialUri)))
                throw new InvalidOperationException("Native ADO grants must pin unique exact actor/profile/credential URI mappings.");
            _ = settings.Identity(grant.Identity);
        }
    }

    internal NativeAdoGrant? Grant(string actor, string uri) =>
        Grants.SingleOrDefault(grant => grant.ActorSpiffeId == actor && grant.CredentialUri == uri);

    private static bool IsSpiffe(string value) => value.StartsWith("spiffe://", StringComparison.Ordinal)
        && !value.Any(c => c <= 32 || c > 126 || c is '%' or '\\' or '?' or '#')
        && Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.UserInfo.Length == 0 && uri.Authority == uri.Host && uri.AbsoluteUri == value && uri.AbsolutePath.Length > 1;
}

public sealed class NativeAdoGrant
{
    public string ActorSpiffeId { get; init; } = "";
    public string CredentialUri { get; init; } = "";
    public string Identity { get; init; } = "";
    public string Profile { get; init; } = "";
}
public sealed class LlmConfiguration
{
    public bool Enabled { get; init; } = true;
    public GatewayOptions Options { get; init; } = new() { Port = 4142 };
}

public sealed class AdoConfiguration
{
    public bool Enabled { get; init; } = true;
    public string Mode { get; init; } = "delegate";
    public string ConfigurationFile { get; init; } = "";
    public string StateDirectory { get; init; } = "";
    public int DelegatePort { get; init; } = 5480;
    public int DelegateControlPort { get; init; } = 5481;
}

public sealed class AgencyConfiguration
{
    public bool Enabled { get; init; } = true;
    public string ListenAddress { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 15443;
    public int InternalPort { get; init; } = 15444;
    public string PythonExecutable { get; init; } = "";
    public string BrokerScript { get; init; } = "";
    public string BrokerConfigurationFile { get; init; } = "";
    public string StateDirectory { get; init; } = "";
    public string ProfilesFile { get; init; } = "";
    public string AgencyExecutable { get; init; } = "";
    public string GlobalConfigurationFile { get; init; } = "";
    public int MaxConcurrentRequests { get; init; } = 4;
    public int MaxConcurrentPlugins { get; init; } = 1;
    public int RequestTimeoutSeconds { get; init; } = 2000;
}

public sealed class TelemetryConfiguration
{
    public string? OtlpEndpoint { get; init; }
    public string ServiceName { get; init; } = "host-capabilities";
    public string? CaCertificateFile { get; init; }
    public string? ClientCertificateFile { get; init; }
    public string? ClientCertificatePasswordFile { get; init; }
    public string TrustMode { get; init; } = "online";
    public string? ServerCertificateFile { get; init; }
}

public sealed class ActorJwtConfiguration
{
    public bool Enabled { get; init; }
    public bool AllowLegacyKeys { get; init; } = true;
    public string Issuer { get; init; } = "";
    public string Audience { get; init; } = "";
    public string JwksFile { get; init; } = "";
    public string Space { get; init; } = "";
    public string[] AdditionalSpaces { get; init; } = [];
    public string[] Profiles { get; init; } = [];
    public string ClaimFormat { get; init; } = "substrate";
    public string ActorClaim { get; init; } = "actor.ate.dev";
    public string ActorRegistryFile { get; init; } = "";
    public string ActorUidClaim { get; init; } = "actor_uid";
    public string ProfileClaim { get; init; } = "profile";
    public string SpaceClaim { get; init; } = "space";

    internal void Validate()
    {
        if (!Enabled)
        {
            if (!AllowLegacyKeys)
                throw new InvalidOperationException("At least one admission mode must be enabled.");
            return;
        }
        HostConfiguration.RequirePath(JwksFile);
        HostConfiguration.RequirePath(ActorRegistryFile);
        if (!Uri.TryCreate(Issuer, UriKind.Absolute, out var issuer) || issuer.Scheme != "https"
            || issuer.UserInfo.Length != 0 || issuer.Query.Length != 0 || issuer.Fragment.Length != 0
            || string.IsNullOrWhiteSpace(Audience) || string.IsNullOrWhiteSpace(Space) || Profiles.Length == 0
            || Profiles.Any(string.IsNullOrWhiteSpace) || ClaimFormat is not ("custom" or "substrate")
            || AdditionalSpaces.Length > 16
            || AdditionalSpaces.Any(space => string.IsNullOrWhiteSpace(space)
                || space.Any(character => !char.IsAsciiLetterOrDigit(character) && character is not '-' and not '_'))
            || AdditionalSpaces.Distinct(StringComparer.Ordinal).Count() != AdditionalSpaces.Length
            || AdditionalSpaces.Contains(Space, StringComparer.Ordinal)
            || string.IsNullOrWhiteSpace(ActorClaim)
            || new[] { ActorUidClaim, ProfileClaim, SpaceClaim }.Any(string.IsNullOrWhiteSpace))
            throw new InvalidOperationException("Pin JWT issuer, audience, JWKS, space, profiles and identity claim names.");
    }
}
