using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using Grpc.Core;
using Grpc.Net.Client;
using HostCapabilities.NativeAdo;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Substrate.Credprovider.V1;

namespace NativeAdo.Tests;

[TestClass]
public sealed class GrpcTests
{
    [TestMethod]
    public async Task RealHttp2MtlsAllowsOnlyGatewayAndNeverTrustsForwardedCertificates()
    {
        using var certificates = new TestCertificates();
        using var stranger = new TestCertificates();
        using var trust = BrokerTests.Trust(certificates);
        var acquired = 0;
        var logs = new CapturedLogs();
        var builder = WebApplication.CreateEmptyBuilder(new WebApplicationOptions());
        builder.Logging.AddProvider(logs);
        builder.WebHost.UseKestrel(k => k.ListenNativeAdo(IPAddress.Loopback, 0, certificates.Server, trust));
        builder.Services.AddNativeAdo(new(), trust,
            (actor, uri, _) => ValueTask.FromResult<CredentialGrant?>(
                actor == TestCertificates.ActorId && uri == BrokerTests.CredentialUri ? new("host-identity") : null),
            (_, _) =>
            {
                if (Interlocked.Increment(ref acquired) > 1)
                {
                    throw new InvalidOperationException("fake-sensitive-host-error");
                }
                return ValueTask.FromResult("fake.offline.token");
            });
        await using var app = builder.Build();
        app.MapNativeAdo();
        await app.StartAsync();
        try
        {
            var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
            using var valid = Channel(address, certificates.Gateway, certificates.Root);
            var client = new CredentialProvider.CredentialProviderClient(valid);
            var request = new FetchSecretRequest { Uri = BrokerTests.CredentialUri, ActorSpiffeId = TestCertificates.ActorId };
            var response = await client.FetchSecretAsync(request, deadline: DateTime.UtcNow.AddSeconds(10));
            Assert.AreEqual("fake.offline.token", response.OpaqueBytes.ToStringUtf8());
            var failure = await BrokerTests.Reject(StatusCode.Unavailable, async () => await client.FetchSecretAsync(request));
            Assert.AreEqual("Credential request failed.", failure.Status.Detail);
            await BrokerTests.Reject(StatusCode.PermissionDenied, async () =>
                await client.FetchSecretAsync(new FetchSecretRequest { Uri = request.Uri, ActorSpiffeId = TestCertificates.ActorId + "-denied" }));
            foreach (var certificate in new X509Certificate2?[] { null, certificates.Issue(TestCertificates.ActorId), stranger.Gateway })
            {
                using var denied = Channel(address, certificate, certificates.Root);
                var deniedClient = new CredentialProvider.CredentialProviderClient(denied);
                await Assert.ThrowsExceptionAsync<RpcException>(async () => await deniedClient.FetchSecretAsync(request,
                    new Metadata
                    {
                        { "x-forwarded-client-cert", certificates.Gateway.GetRawCertDataString() },
                        { "x-actor-spiffe-id", TestCertificates.GatewayId }
                    }, deadline: DateTime.UtcNow.AddSeconds(5)));
            }
            Assert.AreEqual(2, acquired);
        }
        finally
        {
            await app.StopAsync();
        }
        Assert.IsFalse(logs.Text.Contains("fake.offline.token", StringComparison.Ordinal));
        Assert.IsFalse(logs.Text.Contains("fake-sensitive-host-error", StringComparison.Ordinal));
        Assert.IsFalse(logs.Text.Contains(BrokerTests.CredentialUri, StringComparison.Ordinal));
    }

    [TestMethod]
    public async Task GrpcCancellationPropagatesToHostTokenDelegate()
    {
        using var certificates = new TestCertificates();
        using var trust = BrokerTests.Trust(certificates);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var builder = WebApplication.CreateEmptyBuilder(new WebApplicationOptions());
        builder.WebHost.UseKestrel(k => k.ListenNativeAdo(IPAddress.Loopback, 0, certificates.Server, trust));
        builder.Services.AddNativeAdo(new(), trust, (_, _, _) => ValueTask.FromResult<CredentialGrant?>(new("identity")),
            async (_, ct) =>
            {
                entered.SetResult();
                try { await Task.Delay(Timeout.InfiniteTimeSpan, ct); }
                finally { cancelled.SetResult(); }
                return "fake.unreachable";
            });
        await using var app = builder.Build();
        app.MapNativeAdo();
        await app.StartAsync();
        try
        {
            var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
            using var channel = Channel(address, certificates.Gateway, certificates.Root);
            using var cancellation = new CancellationTokenSource();
            var call = new CredentialProvider.CredentialProviderClient(channel).FetchSecretAsync(
                new FetchSecretRequest { Uri = BrokerTests.CredentialUri, ActorSpiffeId = TestCertificates.ActorId },
                cancellationToken: cancellation.Token);
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(10));
            cancellation.Cancel();
            await BrokerTests.Reject(StatusCode.Cancelled, async () => await call.ResponseAsync);
            await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(5));
        }
        finally
        {
            await app.StopAsync();
        }
    }

    private static GrpcChannel Channel(string address, X509Certificate2? certificate, X509Certificate2 root)
    {
        var handler = new HttpClientHandler();
        if (certificate is not null)
        {
            handler.ClientCertificates.Add(certificate);
        }
        handler.ServerCertificateCustomValidationCallback = (_, server, _, errors) =>
        {
            if (server is null || (errors & (SslPolicyErrors.RemoteCertificateNameMismatch |
                SslPolicyErrors.RemoteCertificateNotAvailable)) != 0)
            {
                return false;
            }
            using var chain = new X509Chain();
            chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
            chain.ChainPolicy.CustomTrustStore.Add(root);
            chain.ChainPolicy.ApplicationPolicy.Add(new("1.3.6.1.5.5.7.3.1"));
            chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
            chain.ChainPolicy.DisableCertificateDownloads = true;
            return chain.Build(server);
        };
        return GrpcChannel.ForAddress(address, new GrpcChannelOptions { HttpHandler = handler });
    }

    private sealed class CapturedLogs : ILoggerProvider
    {
        private readonly System.Collections.Concurrent.ConcurrentQueue<string> messages = new();
        internal string Text => string.Join("\n", messages);
        public ILogger CreateLogger(string categoryName) => new Sink(messages);
        public void Dispose() { }

        private sealed class Sink(System.Collections.Concurrent.ConcurrentQueue<string> messages) : ILogger
        {
            public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
            public bool IsEnabled(LogLevel logLevel) => true;
            public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
                Func<TState, Exception?, string> formatter) => messages.Enqueue(formatter(state, exception));
        }
    }
}
