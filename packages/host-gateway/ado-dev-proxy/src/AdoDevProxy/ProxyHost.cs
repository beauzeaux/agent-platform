using Microsoft.Identity.Client;
using System.Diagnostics;
using System.Net;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Titanium.Web.Proxy;
using Titanium.Web.Proxy.EventArguments;
using Titanium.Web.Proxy.Models;
using Titanium.Web.Proxy.Network;
using Titanium.Web.Proxy.Options;

namespace AdoDevProxy;

internal sealed class ProxyHost : IDisposable
{
    private readonly ProxySettings settings;
    private readonly ITokenProvider tokens;
    private readonly CancellationToken shutdown;
    private readonly RequestPolicy policy;
    private readonly ClientSubnet[] clients;
    private readonly Action<ProxyObservation>? observe;
    internal ProxyServer Server { get; }

    internal ProxyHost(ProxySettings settings, ITokenProvider tokens, X509Certificate2 root, CancellationToken shutdown,
        Action<ProxyObservation>? observe = null)
    {
        this.settings = settings;
        this.tokens = tokens;
        this.shutdown = shutdown;
        this.observe = observe;
        policy = new(settings);
        clients = settings.AllowedClients.Select(ClientSubnet.Parse).ToArray();
        Server = new ProxyServer(false, false, false)
        {
            EnableHttp2 = false,
            EnableDecryptFailureBypass = false,
            IgnoreServerCertificateErrors = false,
            ForwardToUpstreamGateway = false,
            EnableWinAuth = false,
            EnableTcpServerConnectionPrefetch = false,
            NetworkFailureRetryAttempts = 0,
            BlockPrivateNetworkDestinations = true,
            BlockLoopbackDestinations = true,
            PolicyModes = ProxyPolicyModes.AllEnforce,
            MaxConcurrentClientConnections = 64,
            MaxBufferedBodyBytes = 4 * 1024 * 1024,
            MaxDecodedHeaderListBytes = 64 * 1024,
            ConnectTimeOutSeconds = 30,
            ClientHeaderTimeoutSeconds = 30,
            ResponseHeaderTimeoutSeconds = 120,
            IdleReadTimeoutSeconds = 120,
            IdleWriteTimeoutSeconds = 120,
            SupportedSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13,
            SupportedServerSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13
        };
#pragma warning disable TWP001
        Server.EnableHttp3 = false;
#pragma warning restore TWP001
        Server.Logging.Enabled = false;
        Server.ResourceLimits = ProxyResourceLimits.Default.WithCertificateCacheBounds(128, 128);
        CertificateManager.SuppressInteractiveRootStoreMutations = true;
        Server.CertificateManager.RootCertificate = root;
        Server.CertificateManager.SaveFakeCertificates = false;
        Server.CertificateManager.DisableWildCardCertificates = true;
        Server.CertificateManager.CertificateValidDays = 2;
        Server.CertificateManager.CertificateGraceDays = 0;
        Server.CertificateManager.CertificateCacheTimeOutMinutes = 10;
        Server.CertificateManager.StorageFlag = X509KeyStorageFlags.EphemeralKeySet;
        // The engine has raw fallback paths beyond its learned TLS-bypass feature.
        Server.GetCustomUpStreamProxyFunc = e =>
        {
            if (e is not SessionEventArgs || e.UserData is not ForwardState || !e.IsHttps ||
                policy.Evaluate(e.HttpClient.Request.Method, e.HttpClient.Request.Url) is null)
                throw new IOException("Unapproved upstream connection.");
            return Task.FromResult<IExternalProxy?>(null);
        };
        var endpoint = new ExplicitProxyEndPoint(IPAddress.Parse(settings.ListenAddress), settings.Port, true);
        endpoint.BeforeTunnelConnectRequest += BeforeConnect;
        endpoint.BeforeTunnelConnectResponse += AfterClientHello;
        Server.BeforeRequest += BeforeRequest;
        Server.BeforeResponse += BeforeResponse;
        Server.AfterResponse += (_, e) =>
        {
            if (e.UserData is ForwardState state)
            {
                Console.WriteLine($"{state.Rule.Operation} {e.HttpClient.Response.StatusCode}");
                observe?.Invoke(new(state.Rule.Operation, e.HttpClient.Response.StatusCode, state.UpstreamStatus,
                    e.HttpClient.Response.StatusCode >= 400 ? "gateway_error" : "success",
                    Stopwatch.GetElapsedTime(state.Started).TotalSeconds));
            }
            return Task.CompletedTask;
        };
        Server.AddEndPoint(endpoint);
    }

    internal async Task PrepareFeedsAsync()
    {
        using var client = new HttpClient(new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false })
        {
            Timeout = TimeSpan.FromSeconds(45)
        };
        foreach (var feed in settings.Feeds.Where(f => f.NugetIndex is not null))
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, feed.NugetIndex);
            request.Headers.Authorization = new("Bearer", await tokens.GetTokenAsync(feed.Identity, false, shutdown));
            using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, shutdown);
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"Feed discovery failed ({(int)response.StatusCode}).");
            await response.Content.LoadIntoBufferAsync(1024 * 1024, shutdown);
            using var document = JsonDocument.Parse(await response.Content.ReadAsByteArrayAsync(shutdown));
            policy.AddNugetResources(feed, document.RootElement);
        }
    }

    internal void Start() => Server.Start(false);

    private Task BeforeConnect(object sender, TunnelConnectSessionEventArgs e)
    {
        var request = e.HttpClient.Request;
        e.DecryptSsl = true;
        e.EstablishServerConnectionBeforeResponse = false;
        if (!RequestPolicy.TryUri("https://" + request.RequestUriString, out var uri) ||
            !request.RequestUriString.Equals(uri.Host + ":443", StringComparison.OrdinalIgnoreCase))
        {
            e.DenyConnect = true;
            return Task.CompletedTask;
        }
        e.DenyConnect = !clients.Any(c => c.Contains(e.ClientRemoteEndPoint.Address)) ||
            !policy.CanConnect(uri) || request.Headers.HeaderExists("Origin") ||
            request.Url.Any(char.IsControl) || request.Url.Contains('@') || !MatchesHost(request.Headers, uri);
        if (e.DenyConnect)
        {
            Console.WriteLine($"connect_denied host={uri.Host}");
            observe?.Invoke(new("connect", 403, 0, "denied"));
        }
        if (!e.DenyConnect)
        {
            e.ClientUserData = uri.Authority;
            foreach (var header in new[] { "Authorization", "Proxy-Authorization", "Cookie", "Forwarded" })
                request.Headers.RemoveHeader(header);
        }
        return Task.CompletedTask;
    }

    private static Task AfterClientHello(object sender, TunnelConnectSessionEventArgs e)
    {
        var hello = e.HttpClient.ConnectRequest?.ClientHelloInfo;
        var authority = e.ClientUserData as string;
        if (hello is null || authority is null)
        {
            e.TerminateSession();
            return Task.CompletedTask;
        }
        if (hello.Extensions is not null && hello.Extensions.TryGetValue("server_name", out var sni) &&
            !sni.Data.Equals(new Uri("https://" + authority).Host, StringComparison.OrdinalIgnoreCase))
            e.TerminateSession();
        return Task.CompletedTask;
    }

    private async Task BeforeRequest(object sender, SessionEventArgs e)
    {
        var request = e.HttpClient.Request;
        var rule = policy.Evaluate(request.Method, request.Url);
        if (!e.IsHttps || e.ClientUserData is not string authority ||
            !request.RequestUri.Authority.Equals(authority, StringComparison.OrdinalIgnoreCase) ||
            !MatchesHost(request.Headers, request.RequestUri) ||
            HasAmbiguousFraming(request.Headers) ||
            request.Headers.HeaderExists("Origin") || request.Headers.HeaderExists("Sec-Fetch-Site") ||
            request.UpgradeToWebSocket || rule is null ||
            !clients.Any(c => c.Contains(e.ClientRemoteEndPoint.Address)))
        {
            e.GenericResponse("proxy_policy_denied", HttpStatusCode.Forbidden);
            observe?.Invoke(new("policy", 403, 0, "denied"));
            return;
        }
        foreach (var header in request.Headers.GetAllHeaders().Where(h =>
            h.Name.StartsWith("X-Forwarded-", StringComparison.OrdinalIgnoreCase)).Select(h => h.Name).ToArray())
            request.Headers.RemoveHeader(header);
        foreach (var header in new[] { "Authorization", "Proxy-Authorization", "Cookie", "Forwarded" })
            request.Headers.RemoveHeader(header);
        try
        {
            if (rule.Identity is not null)
                request.Headers.AddHeader("Authorization", "Bearer " + await tokens.GetTokenAsync(rule.Identity, false, shutdown));
            e.UserData = new ForwardState(rule, (e.UserData as ForwardState)?.Retries ?? 0,
                Started: (e.UserData as ForwardState)?.Started ?? Stopwatch.GetTimestamp());
            e.NetworkFailureRetryAttempts = 0;
        }
        catch (Exception ex) when (ex is MsalException or InvalidOperationException)
        {
            e.GenericResponse("host_sign_in_required", HttpStatusCode.ServiceUnavailable);
            observe?.Invoke(new(rule.Operation, 503, 0, "authentication_unavailable"));
        }
        catch (OperationCanceledException) when (!shutdown.IsCancellationRequested)
        {
            e.GenericResponse("host_authentication_unavailable", HttpStatusCode.ServiceUnavailable);
            observe?.Invoke(new(rule.Operation, 503, 0, "authentication_unavailable"));
        }
    }

    private async Task BeforeResponse(object sender, SessionEventArgs e)
    {
        if (e.UserData is not ForwardState state) return;
        var response = e.HttpClient.Response;
        e.UserData = state with { UpstreamStatus = response.StatusCode };
        response.Headers.RemoveHeader("Set-Cookie");
        response.Headers.RemoveHeader("Proxy-Authenticate");
        if (response.StatusCode == 401 && state.Rule.Identity is not null)
        {
            if (state.Retries == 0 && e.HttpClient.Request.Method is "GET" or "HEAD")
            {
                try
                {
                    _ = await tokens.GetTokenAsync(state.Rule.Identity, true, shutdown);
                    e.UserData = state with { Retries = 1, UpstreamStatus = response.StatusCode };
                    e.ReRequest = true;
                    return;
                }
                catch (Exception ex) when (ex is MsalException or InvalidOperationException) { }
            }
            e.GenericResponse("host_sign_in_required", HttpStatusCode.ServiceUnavailable);
        }
        if (response.StatusCode is >= 300 and < 400 && response.Headers.GetFirstHeader("Location") is { } location)
        {
            if (!Uri.TryCreate(e.HttpClient.Request.RequestUri, location.Value, out var target) ||
                !policy.AllowRedirect(state.Rule, target))
            {
                Console.WriteLine($"redirect_denied host={target?.Host ?? "invalid"}");
                e.GenericResponse("proxy_redirect_denied", HttpStatusCode.BadGateway);
            }
        }
    }

    public void Dispose() => Server.Dispose();

    private static bool MatchesHost(Titanium.Web.Proxy.Http.HeaderCollection headers, Uri uri)
    {
        var hosts = headers.GetAllHeaders().Where(h => h.Name.Equals("Host", StringComparison.OrdinalIgnoreCase)).ToArray();
        return hosts.Length == 1 && RequestPolicy.TryUri("https://" + hosts[0].Value, out var host) &&
            host.AbsolutePath == "/" && host.Query.Length == 0 &&
            host.Authority.Equals(uri.Authority, StringComparison.OrdinalIgnoreCase);
    }

    private static bool HasAmbiguousFraming(Titanium.Web.Proxy.Http.HeaderCollection headers)
    {
        var all = headers.GetAllHeaders().ToArray();
        var lengths = all.Where(h => h.Name.Equals("Content-Length", StringComparison.OrdinalIgnoreCase)).ToArray();
        var encodings = all.Where(h => h.Name.Equals("Transfer-Encoding", StringComparison.OrdinalIgnoreCase)).ToArray();
        return lengths.Length > 1 || encodings.Length > 1 || (lengths.Length > 0 && encodings.Length > 0) ||
            (lengths.Length == 1 && !long.TryParse(lengths[0].Value, System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture, out _)) ||
            (encodings.Length == 1 && !encodings[0].Value.Equals("chunked", StringComparison.OrdinalIgnoreCase));
    }

    private sealed record ForwardState(RequestRule Rule, int Retries, int UpstreamStatus = 0, long Started = 0);
}

internal sealed record ProxyObservation(string Operation, int GatewayStatus, int UpstreamStatus, string Outcome,
    double DurationSeconds = 0);
