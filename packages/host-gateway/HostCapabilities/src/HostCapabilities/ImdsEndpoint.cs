using System.Globalization;
using System.Net.Http.Headers;
using AdoDevProxy;
using Microsoft.IdentityModel.JsonWebTokens;

namespace HostCapabilities;

internal interface IImdsTokenProvider
{
    string ClientId { get; }
    Task<string> GetTokenAsync(string resource, CancellationToken cancellationToken);
}

internal interface IImdsActorAuthorizer
{
    Task<ActorIdentity?> AuthorizeAsync(HttpContext context);
}

internal sealed class WamImdsTokenProvider : IImdsTokenProvider, IDisposable
{
    private readonly string identity;
    private readonly WamTokenProvider tokens;

    internal WamImdsTokenProvider(ImdsConfiguration configuration)
    {
        var settings = ProxySettings.Load(configuration.ConfigurationFile);
        var selected = settings.Identity(configuration.Identity);
        ClientId = selected.ClientId;
        identity = selected.Name;
        tokens = new WamTokenProvider(settings, new UserState(configuration.StateDirectory));
    }

    public string ClientId { get; }

    public Task<string> GetTokenAsync(string resource, CancellationToken cancellationToken) =>
        tokens.GetTokenAsync(identity, resource, false, cancellationToken);

    public void Dispose() => tokens.Dispose();
}

internal sealed class ImdsActorAuthorizer(ActorAdmission admission, string registryFile) : IImdsActorAuthorizer
{
    public async Task<ActorIdentity?> AuthorizeAsync(HttpContext context)
    {
        var value = context.Request.Headers.Authorization.ToString();
        if (value.Length == 0)
            value = context.Request.Headers[ImdsEndpoint.IdentityHeader].ToString();
        if (!AuthenticationHeaderValue.TryParse(value, out var header)
            || !header.Scheme.Equals("Bearer", StringComparison.OrdinalIgnoreCase) || header.Parameter is not { } bearer)
            return null;
        var identity = await admission.ValidateAsync(bearer);
        return identity is not null && new BrokerCredentials(registryFile).Matches(identity) ? identity : null;
    }
}

internal sealed class ImdsEndpoint(
    ImdsConfiguration configuration,
    IImdsActorAuthorizer actors,
    IImdsTokenProvider tokens,
    HostTelemetry telemetry)
{
    internal const string AzureDevOpsResource = "499b84ac-1321-427f-aa17-267ca6975798";
    internal const string AzureManagementResource = "https://management.azure.com/";
    internal const string IdentityHeader = "X-IDENTITY-HEADER";
    private static readonly HashSet<string> SupportedResources =
        [AzureDevOpsResource, AzureManagementResource];

    internal async Task HandleAsync(HttpContext context)
    {
        context.Response.Headers.CacheControl = "no-store";
        context.Response.Headers.Pragma = "no-cache";
        using var observation = telemetry.Begin("imds", "token", context);
        try
        {
            if (context.Request.Headers.ContainsKey("Origin")
                || context.Request.Headers.ContainsKey("X-Forwarded-For")
                || !string.Equals(context.Request.Headers["Metadata"], "true", StringComparison.OrdinalIgnoreCase)
                    && !context.Request.Headers.ContainsKey(IdentityHeader))
            {
                observation.Outcome = "invalid_request";
                await ErrorAsync(context, 400, "invalid_request",
                    "Metadata: true or the managed identity endpoint header is required.");
                return;
            }

            var query = context.Request.Query;
            var resource = query["resource"].ToString();
            if (query.Any(pair => pair.Value.Count != 1 || pair.Key is not ("api-version" or "resource"))
                || !DateOnly.TryParseExact(query["api-version"], "yyyy-MM-dd", CultureInfo.InvariantCulture,
                    DateTimeStyles.None, out var version)
                || version < new DateOnly(2018, 2, 1) || version > DateOnly.FromDateTime(DateTime.UtcNow)
                || !SupportedResources.Contains(resource))
            {
                observation.Outcome = "target_denied";
                await ErrorAsync(context, 400, "invalid_resource", "The requested resource is not supported.");
                return;
            }

            var identity = await actors.AuthorizeAsync(context);
            if (identity is null || !configuration.Allows(identity.Profile, resource))
            {
                observation.Outcome = "actor_denied";
                await ErrorAsync(context, 403, "actor_denied", "Actor is not allowed.");
                return;
            }

            observation.Actor(identity);
            var token = await tokens.GetTokenAsync(resource, context.RequestAborted);
            var jwt = new JsonWebTokenHandler().ReadJsonWebToken(token);
            var expiresOn = new DateTimeOffset(jwt.ValidTo.ToUniversalTime());
            var now = DateTimeOffset.UtcNow;
            if (expiresOn <= now.AddMinutes(5))
            {
                observation.Outcome = "token_unavailable";
                await ErrorAsync(context, 503, "temporarily_unavailable", "Token acquisition is temporarily unavailable.");
                return;
            }

            observation.Outcome = "success";
            await context.Response.WriteAsJsonAsync(new
            {
                access_token = token,
                client_id = tokens.ClientId,
                expires_in = ((long)(expiresOn - now).TotalSeconds).ToString(CultureInfo.InvariantCulture),
                expires_on = expiresOn.ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture),
                not_before = now.ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture),
                resource,
                token_type = "Bearer"
            }, context.RequestAborted);
        }
        catch (Microsoft.Identity.Client.MsalUiRequiredException)
        {
            observation.Outcome = "interaction_required";
            await ErrorAsync(context, 401, "interaction_required", "Local Windows sign-in is required.");
        }
        catch (Microsoft.Identity.Client.MsalException)
        {
            observation.Outcome = "token_unavailable";
            await ErrorAsync(context, 503, "temporarily_unavailable", "Token acquisition is temporarily unavailable.");
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            observation.Outcome = "token_unavailable";
            await ErrorAsync(context, 503, "temporarily_unavailable", "Token acquisition is temporarily unavailable.");
        }
        finally
        {
            observation.GatewayStatus = context.Response.StatusCode;
        }
    }

    internal Task WarmAsync(CancellationToken cancellationToken) =>
        tokens.GetTokenAsync(AzureDevOpsResource, cancellationToken);

    private static Task ErrorAsync(HttpContext context, int status, string error, string description)
    {
        context.Response.StatusCode = status;
        return context.Response.WriteAsJsonAsync(new { error, error_description = description }, context.RequestAborted);
    }
}
