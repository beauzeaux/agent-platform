using System.Security.Cryptography.X509Certificates;
using Grpc.Core;
using HostCapabilities.NativeAdo;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace NativeAdo.Tests;

[TestClass]
public sealed class BrokerTests
{
    internal const string CredentialUri = "ate-secret://windows-wam/canary/ado";
    private static readonly CredentialGrant Grant = new("test-host-identity");

    [TestMethod]
    public async Task GatewayActorAndCredentialMustAllBeAuthorizedBeforeTokenAcquisition()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        var calls = 0;
        using var broker = new NativeAdoBroker(new(), trust,
            (actor, uri, _) => ValueTask.FromResult(actor == TestCertificates.ActorId && uri == CredentialUri ? Grant : null),
            (identity, _) =>
            {
                Assert.AreEqual(Grant.Identity, identity);
                Interlocked.Increment(ref calls);
                return ValueTask.FromResult("fake.offline.token");
            });
        await Reject(StatusCode.Unauthenticated, () => broker.FetchAsync(null, CredentialUri, TestCertificates.ActorId, default));
        await Reject(StatusCode.Unauthenticated, () => broker.FetchAsync(
            certificates.Issue(TestCertificates.ActorId), CredentialUri, TestCertificates.ActorId, default));
        await Reject(StatusCode.PermissionDenied, () => broker.FetchAsync(
            certificates.Gateway, CredentialUri, TestCertificates.ActorId + "-other", default));
        foreach (var uri in new[] { "ate-secret://windows-wam/canary/other", "ate-secret://k8s.io/secret",
            "ate-secret://windows-wam/canary/../ado", "ate-secret://windows-wam/canary/%61do", CredentialUri + "?query=x" })
        {
            await Reject(StatusCode.PermissionDenied, () => broker.FetchAsync(certificates.Gateway, uri, TestCertificates.ActorId, default));
        }
        Assert.AreEqual(0, calls);
        var response = await broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default);
        Assert.AreEqual("fake.offline.token", response.OpaqueBytes.ToStringUtf8());
        Assert.AreEqual(1, calls);
    }

    [TestMethod]
    public async Task HostAndPolicyExceptionsNeverLeakTheirMessageOrTrailers()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        using var tokenFailure = new NativeAdoBroker(new(), trust, (_, _, _) => ValueTask.FromResult<CredentialGrant?>(Grant),
            (_, _) => throw new RpcException(new Status(StatusCode.Internal, "fake-sensitive-message"),
                new Metadata { { "secret", "fake-sensitive-message" } }));
        using var policyFailure = new NativeAdoBroker(new(), trust,
            (_, _, _) => throw new InvalidOperationException("fake-sensitive-message"),
            (_, _) => throw new AssertFailedException("Token acquisition must not run."));
        foreach (var broker in new[] { tokenFailure, policyFailure })
        {
            var exception = await Reject(StatusCode.Unavailable,
                () => broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default));
            Assert.AreEqual("Credential request failed.", exception.Status.Detail);
            Assert.AreEqual(0, exception.Trailers.Count);
        }
    }

    [TestMethod]
    public async Task NonCooperativeTokenWorkTimesOutButKeepsItsConcurrencyPermitUntilItFinishes()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var calls = 0;
        using var broker = new NativeAdoBroker(new() { MaxConcurrency = 1, RequestTimeout = TimeSpan.FromMilliseconds(200) },
            trust, (_, _, _) => ValueTask.FromResult<CredentialGrant?>(Grant),
            (_, _) =>
            {
                Interlocked.Increment(ref calls);
                entered.TrySetResult();
                return new ValueTask<string>(release.Task);
            });
        var first = broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await Reject(StatusCode.ResourceExhausted, () => broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default));
        await Reject(StatusCode.DeadlineExceeded, () => first);
        await Reject(StatusCode.ResourceExhausted, () => broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default));
        Assert.AreEqual(1, calls);
        release.SetResult("fake.late.token");
        await AssertEventuallySucceeds(broker, certificates);
    }

    [TestMethod]
    public async Task CancellationReachesTokenDelegateAndCapacityRecovers()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        using var cancellation = new CancellationTokenSource();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var observed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var first = true;
        using var broker = new NativeAdoBroker(new() { MaxConcurrency = 1 }, trust,
            (_, _, _) => ValueTask.FromResult<CredentialGrant?>(Grant),
            async (_, ct) =>
            {
                if (first)
                {
                    first = false;
                    entered.SetResult();
                    try { await Task.Delay(Timeout.InfiniteTimeSpan, ct); }
                    finally { observed.SetResult(); }
                }
                return "fake.offline.token";
            });
        var request = broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, cancellation.Token);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();
        await Reject(StatusCode.Cancelled, () => request);
        await observed.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await AssertEventuallySucceeds(broker, certificates);
    }

    [TestMethod]
    public async Task CancelledAuthorizationCannotAcquireAToken()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        using var cancellation = new CancellationTokenSource();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var calls = 0;
        using var broker = new NativeAdoBroker(new(), trust, async (_, _, ct) =>
        {
            entered.SetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return Grant;
        }, (_, _) => { Interlocked.Increment(ref calls); return ValueTask.FromResult("fake.token"); });
        var request = broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, cancellation.Token);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();
        await Reject(StatusCode.Cancelled, () => request);
        Assert.AreEqual(0, calls);
    }

    [TestMethod]
    public async Task LateAuthorizationAfterTimeoutCannotAcquireATokenAndDisposalClosesAdmission()
    {
        using var certificates = new TestCertificates();
        using var trust = Trust(certificates);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<CredentialGrant?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var calls = 0;
        var authorizations = 0;
        using var broker = new NativeAdoBroker(new() { MaxConcurrency = 1, RequestTimeout = TimeSpan.FromMilliseconds(200) },
            trust, (_, _, _) =>
            {
                if (Interlocked.Increment(ref authorizations) == 1)
                {
                    entered.SetResult();
                    return new ValueTask<CredentialGrant?>(release.Task);
                }
                return ValueTask.FromResult<CredentialGrant?>(Grant);
            }, (_, _) => { Interlocked.Increment(ref calls); return ValueTask.FromResult("fake.token"); });
        var request = broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await Reject(StatusCode.DeadlineExceeded, () => request);
        release.SetResult(Grant);
        await AssertEventuallySucceeds(broker, certificates);
        Assert.AreEqual(1, calls);
        broker.Dispose();
        await Reject(StatusCode.Unavailable, () =>
            broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default));
        Assert.AreEqual(1, calls);
    }

    internal static GatewayCertificateTrust Trust(TestCertificates certificates) =>
        new(TestCertificates.GatewayId, [certificates.Root], revocationMode: X509RevocationMode.NoCheck);

    internal static async Task<RpcException> Reject(StatusCode code, Func<Task> operation)
    {
        var exception = await Assert.ThrowsExceptionAsync<RpcException>(operation);
        Assert.AreEqual(code, exception.StatusCode);
        return exception;
    }

    private static async Task AssertEventuallySucceeds(NativeAdoBroker broker, TestCertificates certificates)
    {
        for (var i = 0; i < 100; i++)
        {
            try
            {
                await broker.FetchAsync(certificates.Gateway, CredentialUri, TestCertificates.ActorId, default);
                return;
            }
            catch (RpcException e) when (e.StatusCode == StatusCode.ResourceExhausted)
            {
                await Task.Delay(10);
            }
        }
        Assert.Fail("Capacity was not released.");
    }
}
