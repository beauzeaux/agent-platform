using Microsoft.Identity.Client;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using Titanium.Web.Proxy.Models;

namespace AdoDevProxy.Tests;

[TestClass]
public sealed class TransportTests
{
    private const string PackageUrl = "https://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/package";

    [TestMethod]
    public async Task InterceptionStripsCredentialsAndStreamsBeyondTheBufferLimit()
    {
        var payload = new byte[6 * 1024 * 1024];
        RandomNumberGenerator.Fill(payload);
        await using var fixture = new Fixture(_ => new(200, payload));
        using var client = fixture.Client();
        using var request = new HttpRequestMessage(HttpMethod.Get, PackageUrl);
        request.Headers.Add("Authorization", "Basic client-secret");
        request.Headers.Add("Cookie", "session=client-secret");
        request.Headers.Add("X-Forwarded-For", "1.2.3.4");
        using var response = await client.SendAsync(request);
        Assert.AreEqual(HttpStatusCode.OK, response.StatusCode);
        CollectionAssert.AreEqual(SHA256.HashData(payload), SHA256.HashData(await response.Content.ReadAsByteArrayAsync()));
        var observed = fixture.Upstream.Requests.Single();
        Assert.AreEqual("Bearer test-token", observed.Headers.GetValueOrDefault("Authorization"));
        Assert.IsFalse(observed.Headers.ContainsKey("Cookie"));
        Assert.IsFalse(observed.Headers.ContainsKey("X-Forwarded-For"));
        Assert.IsFalse(response.Headers.Contains("Set-Cookie"));
    }

    [TestMethod]
    public async Task FailedSafeReadRefreshesOnceButPushIsNeverReplayed()
    {
        await using (var fixture = new Fixture(r => new(r.Number == 1 ? 401 : 200, [])))
        {
            using var client = fixture.Client();
            using var response = await client.GetAsync(PackageUrl);
            Assert.AreEqual(HttpStatusCode.OK, response.StatusCode);
            Assert.AreEqual(2, fixture.Upstream.Requests.Count);
            Assert.AreEqual(1, fixture.Tokens.Refreshes);
            Assert.AreEqual("Bearer refreshed-test-token", fixture.Upstream.Requests.Last().Headers["Authorization"]);
        }
        await using (var fixture = new Fixture(_ => new(401, [])))
        {
            using var client = fixture.Client();
            using var response = await client.PostAsync("https://dev.azure.com/msasg/p/_git/r/git-receive-pack", new ByteArrayContent([1, 2]));
            Assert.AreEqual(HttpStatusCode.ServiceUnavailable, response.StatusCode);
            Assert.AreEqual(1, fixture.Upstream.Requests.Count);
            Assert.AreEqual(0, fixture.Tokens.Refreshes);
        }
    }

    [TestMethod]
    public async Task RedirectedContentGetsNoBearerAndOnlyTheExactCapabilityIsAllowed()
    {
        const string location = "https://content.example.com/package?sig=test";
        await using var fixture = new Fixture(r => r.Host == "content.example.com"
            ? new(200, "artifact"u8.ToArray()) : new(303, [], location));
        using var client = fixture.Client();
        using var response = await client.GetAsync(PackageUrl);
        Assert.AreEqual("artifact", await response.Content.ReadAsStringAsync());
        Assert.IsFalse(fixture.Upstream.Requests.Last().Headers.ContainsKey("Authorization"));
        using var denied = await client.GetAsync(location + "different");
        Assert.AreEqual(HttpStatusCode.Forbidden, denied.StatusCode);
        Assert.AreEqual(2, fixture.Upstream.Requests.Count);
    }

    [TestMethod]
    public async Task MissingTrustAndInvalidUpstreamCertificatesFailClosed()
    {
        await using (var fixture = new Fixture(_ => new(200, [])))
        {
            using var client = fixture.Client(trustRoot: false);
            await Assert.ThrowsExactlyAsync<HttpRequestException>(() => client.GetAsync(PackageUrl));
            Assert.AreEqual(0, fixture.Upstream.Requests.Count);
        }
        await using (var fixture = new Fixture(_ => new(200, []), trustUpstream: false))
        {
            using var client = fixture.Client();
            try
            {
                using var response = await client.GetAsync(PackageUrl);
                Assert.IsFalse(response.IsSuccessStatusCode);
                Assert.IsFalse((await response.Content.ReadAsStringAsync()).Contains("test-token", StringComparison.Ordinal));
            }
            catch (HttpRequestException) { }
            Assert.AreEqual(0, fixture.Upstream.Requests.Count);
        }
    }

    [TestMethod]
    public async Task PlaintextAndMalformedTlsTunnelCannotBecomeAnOpaqueRelay()
    {
        await using var fixture = new Fixture(_ => new(200, []));
        using (var client = fixture.Client())
        {
            using var plain = await client.GetAsync("http://pkgs.dev.azure.com/msasg/project/_packaging/feed/npm/registry/package");
            Assert.AreEqual(HttpStatusCode.Forbidden, plain.StatusCode);
        }
        foreach (var useTls in new[] { false, true })
        {
            using var tcp = new TcpClient();
            await tcp.ConnectAsync(IPAddress.Loopback, fixture.Port);
            using var network = tcp.GetStream();
            await network.WriteAsync("CONNECT pkgs.dev.azure.com:443 HTTP/1.1\r\nHost: pkgs.dev.azure.com:443\r\n\r\n"u8.ToArray());
            _ = await Headers(network);
            Stream stream = network;
            if (useTls)
            {
                var tls = new SslStream(network, true, (_, cert, _, errors) => fixture.Trust(cert, errors));
                await tls.AuthenticateAsClientAsync("pkgs.dev.azure.com");
                stream = tls;
            }
            await stream.WriteAsync("NOTHTTP\r\n\r\n"u8.ToArray());
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            try { _ = await stream.ReadAsync(new byte[128], timeout.Token); }
            catch (IOException) { }
            finally { if (stream != network) await stream.DisposeAsync(); }
        }
        Assert.AreEqual(0, fixture.Upstream.Connections);
    }

    [TestMethod]
    public async Task SniAndHttpAuthorityMustMatchConnect()
    {
        await using var fixture = new Fixture(_ => new(200, []));
        using (var tcp = new TcpClient())
        {
            await tcp.ConnectAsync(IPAddress.Loopback, fixture.Port);
            using var network = tcp.GetStream();
            await network.WriteAsync("CONNECT pkgs.dev.azure.com:443 HTTP/1.1\r\nHost: pkgs.dev.azure.com:443\r\n\r\n"u8.ToArray());
            _ = await Headers(network);
            using var tls = new SslStream(network, false, (_, cert, _, errors) => fixture.Trust(cert, errors));
            try
            {
                await tls.AuthenticateAsClientAsync("different.example.com");
                Assert.Fail("Mismatched SNI was accepted.");
            }
            catch (AuthenticationException) { }
            catch (IOException) { }
        }
        using (var tcp = new TcpClient())
        {
            await tcp.ConnectAsync(IPAddress.Loopback, fixture.Port);
            using var network = tcp.GetStream();
            await network.WriteAsync("CONNECT pkgs.dev.azure.com:443 HTTP/1.1\r\nHost: pkgs.dev.azure.com:443\r\n\r\n"u8.ToArray());
            _ = await Headers(network);
            using var tls = new SslStream(network, false, (_, cert, _, errors) => fixture.Trust(cert, errors));
            await tls.AuthenticateAsClientAsync("pkgs.dev.azure.com");
            await tls.WriteAsync("GET https://dev.azure.com/msasg/p/_git/r/info/refs?service=git-upload-pack HTTP/1.1\r\nHost: dev.azure.com\r\nConnection: close\r\n\r\n"u8.ToArray());
            var headers = await Headers(tls);
            StringAssert.Contains(headers, "403");
        }
        Assert.AreEqual(0, fixture.Upstream.Connections);
    }

    [TestMethod]
    public async Task InteractionRequiredDoesNotReachUpstreamOrExposeMsalDetails()
    {
        await using var fixture = new Fixture(_ => new(200, []));
        fixture.Tokens.Fail = true;
        using var client = fixture.Client();
        using var response = await client.GetAsync(PackageUrl);
        Assert.AreEqual(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.AreEqual("host_sign_in_required", await response.Content.ReadAsStringAsync());
        Assert.AreEqual(0, fixture.Upstream.Connections);
    }

    [TestMethod]
    public async Task RangeValidatorsAndCompressedBytesArePreserved()
    {
        var payload = "unmodified artifact metadata"u8.ToArray();
        using var buffer = new MemoryStream();
        using (var gzip = new System.IO.Compression.GZipStream(buffer, System.IO.Compression.CompressionLevel.Fastest, true))
            gzip.Write(payload);
        var compressed = buffer.ToArray();
        await using var fixture = new Fixture(r => r.Number == 1
            ? new(206, [1, 2, 3, 4], null, "Content-Range: bytes 1-4/10\r\nETag: \"artifact\"\r\n")
            : new(200, compressed, null, "Content-Encoding: gzip\r\n"));
        using var client = fixture.Client();
        using var request = new HttpRequestMessage(HttpMethod.Get, PackageUrl);
        request.Headers.Range = new(1, 4);
        using var range = await client.SendAsync(request);
        Assert.AreEqual(HttpStatusCode.PartialContent, range.StatusCode);
        Assert.AreEqual("bytes=1-4", fixture.Upstream.Requests[0].Headers["Range"]);
        Assert.AreEqual("\"artifact\"", range.Headers.ETag?.Tag);
        Assert.AreEqual("bytes 1-4/10", range.Content.Headers.ContentRange?.ToString());
        CollectionAssert.AreEqual(new byte[] { 1, 2, 3, 4 }, await range.Content.ReadAsByteArrayAsync());
        using var response = await client.GetAsync(PackageUrl);
        Assert.IsTrue(response.Content.Headers.ContentEncoding.Contains("gzip"));
        CollectionAssert.AreEqual(compressed, await response.Content.ReadAsByteArrayAsync());
    }

    [TestMethod]
    public async Task AmbiguousBodyFramingIsRejectedBeforeAnOriginConnection()
    {
        await using var fixture = new Fixture(_ => new(200, []));
        foreach (var framing in new[]
        {
            "Content-Length: 1\r\nContent-Length: 2\r\n",
            "Content-Length: 1, 2\r\n"
        })
        {
            using var tcp = new TcpClient();
            await tcp.ConnectAsync(IPAddress.Loopback, fixture.Port);
            using var network = tcp.GetStream();
            await network.WriteAsync("CONNECT dev.azure.com:443 HTTP/1.1\r\nHost: dev.azure.com:443\r\n\r\n"u8.ToArray());
            _ = await Headers(network);
            using var tls = new SslStream(network, false, (_, cert, _, errors) => fixture.Trust(cert, errors));
            await tls.AuthenticateAsClientAsync("dev.azure.com");
            await tls.WriteAsync(Encoding.ASCII.GetBytes(
                "POST /msasg/p/_git/r/git-receive-pack HTTP/1.1\r\nHost: dev.azure.com\r\n" + framing + "\r\n0\r\n\r\n"));
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            try
            {
                var bytes = new byte[1024];
                var length = await tls.ReadAsync(bytes, timeout.Token);
                Assert.IsFalse(Encoding.ASCII.GetString(bytes, 0, length).StartsWith("HTTP/1.1 2", StringComparison.Ordinal));
            }
            catch (IOException) { }
        }
        Assert.AreEqual(0, fixture.Upstream.Connections);
    }

    [TestMethod]
    public async Task ChunkedFramingRemovesContentLengthBeforeForwarding()
    {
        await using var fixture = new Fixture(_ => new(200, []));
        using var tcp = new TcpClient();
        await tcp.ConnectAsync(IPAddress.Loopback, fixture.Port);
        using var network = tcp.GetStream();
        await network.WriteAsync("CONNECT dev.azure.com:443 HTTP/1.1\r\nHost: dev.azure.com:443\r\n\r\n"u8.ToArray());
        _ = await Headers(network);
        using var tls = new SslStream(network, false, (_, cert, _, errors) => fixture.Trust(cert, errors));
        await tls.AuthenticateAsClientAsync("dev.azure.com");
        await tls.WriteAsync(
            "POST /msasg/p/_git/r/git-receive-pack HTTP/1.1\r\nHost: dev.azure.com\r\nContent-Length: 7\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n"u8.ToArray());
        StringAssert.Contains(await Headers(tls), "200");
        Assert.AreEqual(1, fixture.Upstream.Requests.Count);
        Assert.IsFalse(fixture.Upstream.Requests[0].Headers.ContainsKey("Content-Length"));
        Assert.AreEqual("chunked", fixture.Upstream.Requests[0].Headers["Transfer-Encoding"]);
    }

    [TestMethod]
    public void RootKeyIsProtectedAndRotationDoesNotTrustTheHost()
    {
        var path = Path.Combine(Path.GetTempPath(), "AdoDevProxy-Test-" + Guid.NewGuid().ToString("N"));
        try
        {
            var state = new UserState(path);
            using var root = state.InitializeCertificate(false);
            using var loaded = state.LoadCertificate();
            Assert.AreEqual(root.Thumbprint, loaded.Thumbprint);
            Assert.IsTrue(loaded.HasPrivateKey);
            Assert.ThrowsExactly<InvalidOperationException>(() => state.InitializeCertificate(false));
            using var store = new X509Store(StoreName.Root, StoreLocation.CurrentUser);
            store.Open(OpenFlags.ReadOnly);
            Assert.AreEqual(0, store.Certificates.Find(X509FindType.FindByThumbprint, root.Thumbprint, false).Count);
            using var rotated = state.InitializeCertificate(true);
            Assert.AreNotEqual(root.Thumbprint, rotated.Thumbprint);
            using var exported = X509CertificateLoader.LoadCertificateFromFile(state.PublicCertificatePath);
            Assert.IsFalse(exported.HasPrivateKey);
            Assert.AreEqual(rotated.Thumbprint, exported.Thumbprint);
            Assert.IsFalse(Directory.EnumerateFiles(path).Any(f => f.EndsWith(".pfx", StringComparison.OrdinalIgnoreCase)));
        }
        finally { Directory.Delete(path, true); }
    }

    internal static async Task<string> Headers(Stream stream)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var bytes = new List<byte>();
        var buffer = new byte[1];
        while (bytes.Count < 65536)
        {
            if (await stream.ReadAsync(buffer, timeout.Token) == 0) throw new IOException("Connection closed.");
            bytes.Add(buffer[0]);
            if (bytes.Count >= 4 && bytes.TakeLast(4).SequenceEqual("\r\n\r\n"u8.ToArray()))
                return Encoding.ASCII.GetString(bytes.ToArray());
        }
        throw new IOException("Header limit exceeded.");
    }

    private sealed class Fixture : IAsyncDisposable
    {
        private readonly CancellationTokenSource cancellation = new();
        private readonly X509Certificate2 root;
        private readonly ProxyHost proxy;
        internal int Port { get; }
        internal FakeUpstream Upstream { get; }
        internal FakeTokens Tokens { get; } = new();

        internal Fixture(Func<ObservedRequest, Reply> reply, bool trustUpstream = true)
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop();
            root = Certificate("proxy-root", true);
            Upstream = new(reply);
            proxy = new(PolicyTests.Settings(Port), Tokens, root, cancellation.Token);
            proxy.Server.UpStreamHttpsProxy = new ExternalProxy("127.0.0.1", Upstream.Port);
            proxy.Server.BlockLoopbackDestinations = false;
            proxy.Server.BlockPrivateNetworkDestinations = false;
            if (trustUpstream)
                proxy.Server.ServerCertificateValidationCallback += (_, e) =>
                {
                    e.IsValid = e.Certificate?.GetCertHashString() == Upstream.Certificate.Thumbprint &&
                        (e.SslPolicyErrors & SslPolicyErrors.RemoteCertificateNameMismatch) == 0;
                    return Task.CompletedTask;
                };
            proxy.Start();
        }

        internal HttpClient Client(bool trustRoot = true)
        {
            var handler = new HttpClientHandler
            {
                Proxy = new WebProxy($"http://127.0.0.1:{Port}"),
                UseProxy = true,
                UseCookies = false
            };
            if (trustRoot) handler.ServerCertificateCustomValidationCallback = (_, cert, _, errors) => Trust(cert, errors);
            return new(handler) { Timeout = TimeSpan.FromSeconds(15) };
        }

        internal bool Trust(X509Certificate? certificate, SslPolicyErrors errors)
        {
            if (certificate is null || (errors & SslPolicyErrors.RemoteCertificateNameMismatch) != 0) return false;
            using var leaf = X509CertificateLoader.LoadCertificate(certificate.GetRawCertData());
            using var chain = new X509Chain();
            chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
            chain.ChainPolicy.CustomTrustStore.Add(root);
            chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
            return chain.Build(leaf);
        }

        public async ValueTask DisposeAsync()
        {
            await cancellation.CancelAsync();
            await proxy.Server.StopAsync(TimeSpan.FromSeconds(2));
            proxy.Dispose();
            await Upstream.DisposeAsync();
            root.Dispose();
            cancellation.Dispose();
        }
    }

    private sealed class FakeTokens : ITokenProvider
    {
        internal int Refreshes;
        internal bool Fail;
        private string token = "test-token";
        public Task<string> GetTokenAsync(string identity, bool forceRefresh, CancellationToken cancellation)
        {
            if (Fail) throw new MsalUiRequiredException("interaction_required", "sensitive detail must not escape");
            if (forceRefresh)
            {
                Interlocked.Increment(ref Refreshes);
                token = "refreshed-test-token";
            }
            return Task.FromResult(token);
        }
    }

    private sealed record Reply(int Status, byte[] Body, string? Location = null, string Headers = "");
    private sealed record ObservedRequest(int Number, string Host, Dictionary<string, string> Headers);

    private sealed class FakeUpstream : IAsyncDisposable
    {
        private readonly TcpListener listener = new(IPAddress.Loopback, 0);
        private readonly CancellationTokenSource cancellation = new();
        private readonly Task loop;
        private readonly List<Task> clients = [];
        private readonly Func<ObservedRequest, Reply> reply;
        internal X509Certificate2 Certificate { get; } = TransportTests.Certificate("upstream", false);
        internal int Port { get; }
        internal int Connections;
        internal List<ObservedRequest> Requests { get; } = [];

        internal FakeUpstream(Func<ObservedRequest, Reply> reply)
        {
            this.reply = reply;
            listener.Start();
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            loop = Loop();
        }

        private async Task Loop()
        {
            try
            {
                while (!cancellation.IsCancellationRequested)
                {
                    var client = await listener.AcceptTcpClientAsync(cancellation.Token);
                    Interlocked.Increment(ref Connections);
                    clients.Add(Handle(client));
                }
            }
            catch (OperationCanceledException) { }
        }

        private async Task Handle(TcpClient client)
        {
            using (client)
            {
                try
                {
                    using var stream = client.GetStream();
                    _ = await Headers(stream);
                    await stream.WriteAsync("HTTP/1.1 200 Connection Established\r\n\r\n"u8.ToArray(), cancellation.Token);
                    using var tls = new SslStream(stream, true);
                    await tls.AuthenticateAsServerAsync(Certificate, false, SslProtocols.Tls12 | SslProtocols.Tls13, false);
                    var text = await Headers(tls);
                    var headers = text.Split("\r\n").Skip(1).Where(s => s.Contains(':')).ToDictionary(
                        s => s[..s.IndexOf(':')], s => s[(s.IndexOf(':') + 1)..].Trim(), StringComparer.OrdinalIgnoreCase);
                    if (headers.TryGetValue("Content-Length", out var length) && int.Parse(length) > 0)
                        await tls.ReadExactlyAsync(new byte[int.Parse(length)], cancellation.Token);
                    if (headers.TryGetValue("Transfer-Encoding", out var encoding) && encoding == "chunked")
                    {
                        var end = new byte[5];
                        await tls.ReadExactlyAsync(end, cancellation.Token);
                        if (!end.AsSpan().SequenceEqual("0\r\n\r\n"u8))
                            throw new IOException("Fixture expects an empty chunked request.");
                    }
                    ObservedRequest request;
                    lock (Requests)
                    {
                        request = new(Requests.Count + 1, headers["Host"].Split(':')[0], headers);
                        Requests.Add(request);
                    }
                    var response = reply(request);
                    var location = response.Location is null ? "" : "Location: " + response.Location + "\r\n";
                    await tls.WriteAsync(Encoding.ASCII.GetBytes(
                        $"HTTP/1.1 {response.Status} Test\r\nContent-Length: {response.Body.Length}\r\n{location}{response.Headers}Set-Cookie: ignored=test\r\nConnection: close\r\n\r\n"), cancellation.Token);
                    await tls.WriteAsync(response.Body, cancellation.Token);
                }
                catch (Exception ex) when (ex is IOException or AuthenticationException or OperationCanceledException) { }
            }
        }

        public async ValueTask DisposeAsync()
        {
            await cancellation.CancelAsync();
            listener.Stop();
            await loop;
            await Task.WhenAll(clients);
            cancellation.Dispose();
            Certificate.Dispose();
        }
    }

    private static X509Certificate2 Certificate(string name, bool ca)
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=" + name + "-" + Guid.NewGuid().ToString("N"),
            key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(ca, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(ca ? X509KeyUsageFlags.KeyCertSign : X509KeyUsageFlags.DigitalSignature, true));
        request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, false));
        if (!ca)
        {
            var san = new SubjectAlternativeNameBuilder();
            foreach (var host in new[] { "pkgs.dev.azure.com", "dev.azure.com", "content.example.com" }) san.AddDnsName(host);
            request.CertificateExtensions.Add(san.Build());
        }
        using var certificate = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddDays(5));
        var pfx = certificate.Export(X509ContentType.Pfx);
        // Windows SslStream's server fixture needs a temporary OS-backed key.
        try { return X509CertificateLoader.LoadPkcs12(pfx, null, X509KeyStorageFlags.Exportable); }
        finally { CryptographicOperations.ZeroMemory(pfx); }
    }
}
