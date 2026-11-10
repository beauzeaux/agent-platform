using System.Net;

namespace LocalLlmGateway.Tests;

public sealed class AuthenticationTests
{
    [Fact]
    public async Task SavesCredentialsAtomicallyAndReadsThemAfterRestart()
    {
        using var directory = new TemporaryDirectory();
        var store = new CredentialStore(directory.Path);
        await store.WriteAsync("test-host-token", default);
        Assert.Equal("test-host-token", await new CredentialStore(directory.Path).ReadAsync(default));
        await store.WriteAsync("replacement-host-token", default);
        Assert.Equal("replacement-host-token", await store.ReadAsync(default));
        Assert.Single(Directory.GetFiles(directory.Path));
        if (!OperatingSystem.IsWindows())
            Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite, File.GetUnixFileMode(store.FilePath));
    }

    [Fact]
    public async Task MissingAndInvalidCredentialsGiveSafeErrors()
    {
        using var directory = new TemporaryDirectory();
        var store = new CredentialStore(directory.Path);
        await Assert.ThrowsAsync<GatewayException>(() => store.ReadAsync(default));
        await File.WriteAllTextAsync(store.FilePath, """{"githubToken":12345}""");
        var error = await Assert.ThrowsAsync<GatewayException>(() => store.ReadAsync(default));
        Assert.DoesNotContain("12345", error.Message);
    }

    [Fact]
    public async Task DeviceLoginRespectsPendingSlowDownAndSuccess()
    {
        var clock = new TestClock();
        var polls = 0;
        var waits = new List<double>();
        using var http = new HttpClient(new TestHandler((request, _) =>
        {
            Assert.Equal("github.com", request.RequestUri!.Host);
            if (request.RequestUri.AbsolutePath == "/login/device/code")
                return Task.FromResult(TestHandler.Json(new
                {
                    device_code = "private-device-code",
                    user_code = "TEST-CODE",
                    verification_uri = "https://github.com/login/device",
                    interval = 2,
                    expires_in = 100
                }));
            return Task.FromResult(++polls switch
            {
                1 => TestHandler.Json(new { error = "authorization_pending" }),
                2 => TestHandler.Json(new { error = "slow_down" }),
                _ => TestHandler.Json(new { access_token = "private-host-token" })
            });
        }));
        var login = new GitHubLogin(http, clock, (wait, ct) =>
        {
            waits.Add(wait.TotalSeconds);
            return clock.Delay(wait, ct);
        });
        var token = await login.AuthorizeAsync((uri, code) =>
        {
            Assert.Equal("https://github.com/login/device", uri);
            Assert.Equal("TEST-CODE", code);
        }, default);
        Assert.Equal("private-host-token", token);
        Assert.Equal([2d, 2d, 7d], waits);
    }

    [Theory]
    [InlineData("access_denied")]
    [InlineData("expired_token")]
    [InlineData("unknown_error")]
    public async Task DeviceLoginStopsOnTerminalErrors(string error)
    {
        var clock = new TestClock();
        var count = 0;
        using var http = new HttpClient(new TestHandler((_, _) =>
            Task.FromResult(++count == 1 ? DeviceResponse() : TestHandler.Json(new { error }))));
        await Assert.ThrowsAsync<GatewayException>(() => new GitHubLogin(http, clock, clock.Delay)
            .AuthorizeAsync((_, _) => { }, default));
        Assert.Equal(2, count);
    }

    [Fact]
    public async Task DeviceLoginStopsAtExpiryWithoutPollingAgain()
    {
        var clock = new TestClock();
        var count = 0;
        using var http = new HttpClient(new TestHandler((_, _) =>
        {
            count++;
            return Task.FromResult(TestHandler.Json(new
            {
                device_code = "private",
                user_code = "TEST",
                verification_uri = "https://github.com/login/device",
                interval = 5,
                expires_in = 5
            }));
        }));
        await Assert.ThrowsAsync<GatewayException>(() => new GitHubLogin(http, clock, clock.Delay)
            .AuthorizeAsync((_, _) => { }, default));
        Assert.Equal(1, count);
    }

    [Fact]
    public async Task DeviceLoginCancellationIsPropagated()
    {
        using var cancellation = new CancellationTokenSource();
        using var http = new HttpClient(new TestHandler((_, _) => Task.FromResult(DeviceResponse())));
        var login = new GitHubLogin(http, delay: async (_, ct) =>
        {
            cancellation.Cancel();
            await Task.Delay(1, ct);
        });
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => login.AuthorizeAsync((_, _) => { }, cancellation.Token));
    }

    [Fact]
    public async Task ConcurrentTokenRequestsPerformOneExchange()
    {
        var clock = new TestClock();
        var calls = 0;
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using var http = new HttpClient(new TestHandler(async (request, ct) =>
        {
            Assert.Equal("token host-only-secret", request.Headers.Authorization!.ToString());
            Interlocked.Increment(ref calls);
            await release.Task.WaitAsync(ct);
            return TokenResponse(clock);
        }));
        using var provider = new CopilotTokenProvider(http, "host-only-secret", clock);
        var tasks = Enumerable.Range(0, 4).Select(_ => provider.GetAsync(default)).ToArray();
        release.SetResult();
        Assert.All(await Task.WhenAll(tasks), value => Assert.Equal("inference-secret", value));
        Assert.Equal(1, calls);
    }

    [Fact]
    public async Task RefreshesNearExpiryAndInvalidatesOnlyRejectedToken()
    {
        var clock = new TestClock();
        var calls = 0;
        using var http = new HttpClient(new TestHandler((_, _) =>
        {
            calls++;
            return Task.FromResult(TokenResponse(clock, "token-" + calls));
        }));
        using var provider = new CopilotTokenProvider(http, "host-only-secret", clock);
        Assert.Equal("token-1", await provider.GetAsync(default));
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.Equal("token-2", await provider.GetAsync(default));
        provider.Invalidate("token-1");
        Assert.Equal("token-2", await provider.GetAsync(default));
        provider.Invalidate("token-2");
        Assert.Equal("token-3", await provider.GetAsync(default));
    }

    [Fact]
    public async Task TransientRefreshFailureUsesOnlyUnexpiredCachedToken()
    {
        var clock = new TestClock();
        var calls = 0;
        using var http = new HttpClient(new TestHandler((_, _) =>
        {
            if (++calls == 1)
                return Task.FromResult(TokenResponse(clock));
            var response = TestHandler.Json(new { error = "private upstream details" }, HttpStatusCode.TooManyRequests);
            response.Headers.RetryAfter = new System.Net.Http.Headers.RetryConditionHeaderValue(TimeSpan.FromSeconds(10));
            return Task.FromResult(response);
        }));
        using var provider = new CopilotTokenProvider(http, "host-only-secret", clock);
        await provider.GetAsync(default);
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.Equal("inference-secret", await provider.GetAsync(default));
        Assert.Equal("inference-secret", await provider.GetAsync(default));
        Assert.Equal(2, calls);
        clock.Advance(TimeSpan.FromSeconds(60));
        var error = await Assert.ThrowsAsync<UpstreamException>(() => provider.GetAsync(default));
        Assert.DoesNotContain("private", error.Message);
    }

    [Fact]
    public async Task RevokedHostCredentialIsNotHiddenByCachedToken()
    {
        var clock = new TestClock();
        var calls = 0;
        using var http = new HttpClient(new TestHandler((_, _) => Task.FromResult(
            ++calls == 1 ? TokenResponse(clock) : TestHandler.Json(new { error = "private" }, HttpStatusCode.Unauthorized))));
        using var provider = new CopilotTokenProvider(http, "host-only-secret", clock);
        await provider.GetAsync(default);
        clock.Advance(TimeSpan.FromSeconds(61));
        await Assert.ThrowsAsync<UpstreamException>(() => provider.GetAsync(default));
    }

    [Fact]
    public async Task ExpiredTokensAreRejected()
    {
        var clock = new TestClock();
        using var http = new HttpClient(new TestHandler((_, _) => Task.FromResult(TestHandler.Json(new
        {
            token = "expired-secret",
            expires_at = clock.GetUtcNow().AddSeconds(-1).ToUnixTimeSeconds(),
            refresh_in = 120
        }))));
        using var provider = new CopilotTokenProvider(http, "host-only-secret", clock);
        await Assert.ThrowsAsync<GatewayException>(() => provider.GetAsync(default));
    }

    private static HttpResponseMessage DeviceResponse() => TestHandler.Json(new
    {
        device_code = "private",
        user_code = "TEST",
        verification_uri = "https://github.com/login/device",
        interval = 2,
        expires_in = 100
    });

    internal static HttpResponseMessage TokenResponse(TestClock clock, string token = "inference-secret") =>
        TestHandler.Json(new { token, expires_at = clock.GetUtcNow().AddSeconds(120).ToUnixTimeSeconds(), refresh_in = 120 });
}
