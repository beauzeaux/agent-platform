using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

namespace LocalLlmGateway;

public sealed class UpstreamException(HttpStatusCode statusCode, string operation, TimeSpan? retryAfter = null)
    : Exception($"{operation} failed (HTTP {(int)statusCode}).")
{
    public HttpStatusCode StatusCode { get; } = statusCode;
    public TimeSpan? RetryAfter { get; } = retryAfter;
    public bool IsTransient => StatusCode == HttpStatusCode.TooManyRequests || (int)StatusCode >= 500;
}

public static class UpstreamProtocol
{
    public const string OAuthClientId = "Iv1.b507a08c87ecfe98";

    public static HttpClient CreateHttpClient() => new(new SocketsHttpHandler
    {
        AllowAutoRedirect = false,
        UseCookies = false,
        ConnectTimeout = TimeSpan.FromSeconds(30)
    })
    {
        Timeout = Timeout.InfiniteTimeSpan
    };

    public static void AddHeaders(HttpRequestMessage request)
    {
        request.Headers.UserAgent.ParseAdd("local-llm-gateway/0.1");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.Add("x-github-api-version", "2025-04-01");
        // Compatibility headers adapted from copilot-api; not a claim of editor identity.
        request.Headers.Add("editor-version", "vscode/1.104.0");
        request.Headers.Add("editor-plugin-version", "copilot-chat/0.26.7");
        request.Headers.Add("copilot-integration-id", "vscode-chat");
        request.Headers.Add("x-request-id", Guid.NewGuid().ToString());
    }

    public static Uri CopilotOrigin(string accountType) => new(accountType switch
    {
        "individual" => "https://api.githubcopilot.com/",
        "business" => "https://api.business.githubcopilot.com/",
        "enterprise" => "https://api.enterprise.githubcopilot.com/",
        _ => throw new GatewayException("Invalid Copilot account type.")
    });

    public static void EnsureSuccess(HttpResponseMessage response, string operation)
    {
        if (!response.IsSuccessStatusCode)
        {
            var retryAfter = response.Headers.RetryAfter?.Delta;
            if (retryAfter is null && response.Headers.RetryAfter?.Date is { } date)
                retryAfter = date - DateTimeOffset.UtcNow;
            throw new UpstreamException(response.StatusCode, operation, retryAfter);
        }
    }

    public static async Task<JsonDocument> ReadJsonAsync(HttpContent content, CancellationToken cancellationToken)
    {
        try
        {
            await content.LoadIntoBufferAsync(4 * 1024 * 1024, cancellationToken);
            return JsonDocument.Parse(await content.ReadAsByteArrayAsync(cancellationToken));
        }
        catch (JsonException)
        {
            throw new GatewayException("Upstream returned invalid JSON.");
        }
    }
}
