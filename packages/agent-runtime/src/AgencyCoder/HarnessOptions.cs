using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed class HarnessOptions
{
    [ConfigurationKeyName("AGENCY_POLICY_URL")]
    public Uri AgencyPolicyEndpoint { get; init; } = null!;
    [ConfigurationKeyName("AGENCY_PLUGINS_URL")]
    public Uri AgencyPluginsEndpoint { get; init; } = null!;
    [ConfigurationKeyName("AGENT_DATA")]
    public string DurableDirectory { get; init; } = "";
    [ConfigurationKeyName("AGENCY_REPOSITORY_URL")]
    public string RepositoryUrl { get; init; } = "";
    [ConfigurationKeyName("AGENCY_WORKSPACE_NAME")]
    public string WorkspaceName { get; init; } = "repository";
    [ConfigurationKeyName("AGENCY_COPILOT_EXECUTABLE")]
    public string CopilotExecutable { get; init; } = "/opt/agency/agency";
    [ConfigurationKeyName("AGENCY_GIT_EXECUTABLE")]
    public string GitExecutable { get; init; } = "git";
    [ConfigurationKeyName("AGENT_PORT")]
    public int Port { get; init; } = 18080;
    [ConfigurationKeyName("AGENCY_TASK_TIMEOUT")]
    public TimeSpan TaskTimeout { get; init; } = TimeSpan.FromMinutes(30);
}

public sealed class HarnessOptionsValidator : IValidateOptions<HarnessOptions>
{
    public ValidateOptionsResult Validate(string? name, HarnessOptions options)
    {
        var failures = new List<string>();
        AddEndpointFailures(options.AgencyPolicyEndpoint, "AgencyPolicyEndpoint");
        AddEndpointFailures(options.AgencyPluginsEndpoint, "AgencyPluginsEndpoint");
        if (string.IsNullOrWhiteSpace(options.DurableDirectory))
            failures.Add("DurableDirectory is required.");
        if (string.IsNullOrWhiteSpace(options.RepositoryUrl))
            failures.Add("RepositoryUrl is required.");
        if (options.Port is < 1 or > 65535)
            failures.Add("Port must be between 1 and 65535.");
        if (options.TaskTimeout <= TimeSpan.Zero)
            failures.Add("TaskTimeout must be positive.");
        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);

        void AddEndpointFailures(Uri? endpoint, string property)
        {
            if (endpoint is null || endpoint.Host.Length == 0 || endpoint.UserInfo.Length != 0
                || !string.IsNullOrEmpty(endpoint.Query) || !string.IsNullOrEmpty(endpoint.Fragment)
                || endpoint.Scheme != Uri.UriSchemeHttps
                    && !(endpoint.Scheme == Uri.UriSchemeHttp && endpoint.IsLoopback))
                failures.Add(property + " must be HTTPS, or loopback HTTP.");
        }
    }
}
