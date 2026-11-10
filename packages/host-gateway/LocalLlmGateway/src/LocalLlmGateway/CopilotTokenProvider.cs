using System.Net.Http.Headers;

namespace LocalLlmGateway;

public sealed class CopilotTokenProvider(HttpClient http, string githubToken, TimeProvider? clock = null) : IDisposable
{
    private readonly TimeProvider clock = clock ?? TimeProvider.System;
    private readonly SemaphoreSlim refreshLock = new(1, 1);
    private TokenState? current;
    private DateTimeOffset retryAt;

    public async Task<string> GetAsync(CancellationToken cancellationToken)
    {
        var snapshot = Volatile.Read(ref current);
        if (snapshot is not null && clock.GetUtcNow() < snapshot.RefreshAt)
            return snapshot.Value;
        await refreshLock.WaitAsync(cancellationToken);
        try
        {
            snapshot = current;
            var now = clock.GetUtcNow();
            if (snapshot is not null && now < snapshot.ExpiresAt && (now < snapshot.RefreshAt || now < retryAt))
                return snapshot.Value;
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            deadline.CancelAfter(TimeSpan.FromSeconds(60));
            try
            {
                for (var attempt = 0; ; attempt++)
                {
                    try
                    {
                        using var request = new HttpRequestMessage(HttpMethod.Get, "https://api.github.com/copilot_internal/v2/token");
                        UpstreamProtocol.AddHeaders(request);
                        request.Headers.Authorization = new AuthenticationHeaderValue("token", githubToken);
                        using var response = await http.SendAsync(request, deadline.Token);
                        UpstreamProtocol.EnsureSuccess(response, "Copilot token exchange");
                        using var json = await UpstreamProtocol.ReadJsonAsync(response.Content, deadline.Token);
                        var root = json.RootElement;
                        var value = root.GetProperty("token").GetString();
                        var expiry = DateTimeOffset.FromUnixTimeSeconds(root.GetProperty("expires_at").GetInt64());
                        var refreshSeconds = root.GetProperty("refresh_in").GetInt32();
                        now = clock.GetUtcNow();
                        if (string.IsNullOrWhiteSpace(value) || expiry <= now || refreshSeconds < 1)
                            throw new GatewayException("Copilot returned an invalid or expired inference token.");
                        var lifetime = (expiry - now).TotalSeconds;
                        var refreshDelay = Math.Max(1, Math.Min(refreshSeconds, lifetime) - Math.Min(60, lifetime / 2));
                        Volatile.Write(ref current, new TokenState(value, expiry, now.AddSeconds(refreshDelay)));
                        retryAt = default;
                        return value;
                    }
                    catch (UpstreamException ex) when (ex.IsTransient && attempt < 2
                        && (ex.RetryAfter is null || ex.RetryAfter <= TimeSpan.FromSeconds(5)))
                    {
                        var wait = ex.RetryAfter ?? TimeSpan.FromSeconds(attempt + 1);
                        await Task.Delay(wait > TimeSpan.Zero ? wait : TimeSpan.Zero, deadline.Token);
                    }
                }
            }
            catch (Exception ex) when ((ex is UpstreamException { IsTransient: true } or HttpRequestException)
                && snapshot is not null && clock.GetUtcNow() < snapshot.ExpiresAt)
            {
                Console.Error.WriteLine("Copilot token refresh temporarily failed; using the still-valid cached token.");
                retryAt = clock.GetUtcNow().AddSeconds(5);
                return snapshot.Value;
            }
            catch
            {
                Volatile.Write(ref current, null);
                throw;
            }
        }
        catch (Exception ex) when (ex is KeyNotFoundException or InvalidOperationException or ArgumentOutOfRangeException or FormatException)
        {
            throw new GatewayException("Copilot returned an invalid inference token response.");
        }
        finally { refreshLock.Release(); }
    }

    public void Invalidate(string rejectedToken)
    {
        var snapshot = Volatile.Read(ref current);
        if (snapshot?.Value == rejectedToken)
            Interlocked.CompareExchange(ref current, null, snapshot);
    }

    public void Dispose() => refreshLock.Dispose();

    private sealed class TokenState(string value, DateTimeOffset expiresAt, DateTimeOffset refreshAt)
    {
        public string Value { get; } = value;
        public DateTimeOffset ExpiresAt { get; } = expiresAt;
        public DateTimeOffset RefreshAt { get; } = refreshAt;
    }
}
