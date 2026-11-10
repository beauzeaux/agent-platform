using System.Net.Http.Json;
using System.Text.Json;

namespace LocalLlmGateway;

public sealed class GitHubLogin(HttpClient http, TimeProvider? clock = null,
    Func<TimeSpan, CancellationToken, Task>? delay = null)
{
    private readonly TimeProvider clock = clock ?? TimeProvider.System;
    private readonly Func<TimeSpan, CancellationToken, Task> delay = delay ?? Task.Delay;

    public async Task<string> AuthorizeAsync(Action<string, string> displayCode, CancellationToken cancellationToken)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromMinutes(20));
        cancellationToken = deadline.Token;
        using var request = new HttpRequestMessage(HttpMethod.Post, "https://github.com/login/device/code")
        {
            Content = JsonContent.Create(new { client_id = UpstreamProtocol.OAuthClientId, scope = "read:user" })
        };
        UpstreamProtocol.AddHeaders(request);
        using var response = await http.SendAsync(request, cancellationToken);
        UpstreamProtocol.EnsureSuccess(response, "GitHub device authorization");
        using var device = await UpstreamProtocol.ReadJsonAsync(response.Content, cancellationToken);
        try
        {
            var root = device.RootElement;
            var deviceCode = root.GetProperty("device_code").GetString()!;
            var code = root.GetProperty("user_code").GetString()!;
            var uri = root.GetProperty("verification_uri").GetString()!;
            var interval = root.GetProperty("interval").GetInt32();
            var lifetime = root.GetProperty("expires_in").GetInt32();
            if (string.IsNullOrWhiteSpace(deviceCode) || string.IsNullOrWhiteSpace(code)
                || uri != "https://github.com/login/device" || interval < 1 || lifetime < 1)
                throw new GatewayException("Invalid GitHub device authorization response.");
            var expires = clock.GetUtcNow().AddSeconds(lifetime);
            displayCode(uri, code);
            while (clock.GetUtcNow() < expires)
            {
                var remaining = expires - clock.GetUtcNow();
                await delay(TimeSpan.FromSeconds(Math.Min(interval, remaining.TotalSeconds)), cancellationToken);
                if (clock.GetUtcNow() >= expires)
                    break;
                using var poll = new HttpRequestMessage(HttpMethod.Post, "https://github.com/login/oauth/access_token")
                {
                    Content = JsonContent.Create(new
                    {
                        client_id = UpstreamProtocol.OAuthClientId,
                        device_code = deviceCode,
                        grant_type = "urn:ietf:params:oauth:grant-type:device_code"
                    })
                };
                UpstreamProtocol.AddHeaders(poll);
                using var pollResponse = await http.SendAsync(poll, cancellationToken);
                UpstreamProtocol.EnsureSuccess(pollResponse, "GitHub device authorization polling");
                using var json = await UpstreamProtocol.ReadJsonAsync(pollResponse.Content, cancellationToken);
                if (json.RootElement.TryGetProperty("access_token", out var accessToken)
                    && accessToken.GetString() is { Length: > 0 } token)
                    return token;
                var error = json.RootElement.GetProperty("error").GetString();
                switch (error)
                {
                    case "authorization_pending": break;
                    case "slow_down": interval = checked(interval + 5); break;
                    case "access_denied": throw new GatewayException("GitHub authorization was denied.");
                    case "expired_token": throw new GatewayException("GitHub device authorization expired. Run login again.");
                    default: throw new GatewayException("GitHub device authorization failed. Run login again.");
                }
            }
            throw new GatewayException("GitHub device authorization expired. Run login again.");
        }
        catch (Exception ex) when (ex is KeyNotFoundException or InvalidOperationException or FormatException or OverflowException)
        {
            throw new GatewayException("Invalid GitHub device authorization response.");
        }
    }
}
