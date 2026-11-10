using Microsoft.Identity.Client;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace AdoDevProxy.Tests;

[TestClass]
public sealed class WamTests
{
    [TestMethod]
    public async Task CachedAcquisitionAndConcurrentForcedRefreshAreCoalesced()
    {
        using var fixture = new Fixture();
        using var provider = fixture.Provider();
        _ = await provider.GetTokenAsync("work", false, CancellationToken.None);
        _ = await provider.GetTokenAsync("work", false, CancellationToken.None);
        Assert.AreEqual(1, fixture.Client.Calls);
        fixture.Client.Delay = new(TaskCreationOptions.RunContinuationsAsynchronously);
        var first = provider.GetTokenAsync("work", true, CancellationToken.None);
        var second = provider.GetTokenAsync("work", true, CancellationToken.None);
        using var cancelled = new CancellationTokenSource();
        var waiting = provider.GetTokenAsync("work", true, cancelled.Token);
        await cancelled.CancelAsync();
        await Assert.ThrowsExactlyAsync<OperationCanceledException>(() => waiting);
        fixture.Client.Delay.SetResult();
        await Task.WhenAll(first, second);
        Assert.AreEqual(2, fixture.Client.Calls);
        Assert.AreEqual(1, fixture.Client.Refreshes);
    }

    [TestMethod]
    public async Task ExpiringTokensAreRenewedAndSelectedAccountSurvivesRestart()
    {
        using var fixture = new Fixture();
        fixture.Client.ExpiresOn = DateTimeOffset.UtcNow.AddMinutes(2);
        using (var provider = fixture.Provider())
        {
            _ = await provider.GetTokenAsync("work", false, CancellationToken.None);
            fixture.Client.ExpiresOn = DateTimeOffset.UtcNow.AddHours(1);
            _ = await provider.GetTokenAsync("work", false, CancellationToken.None);
        }
        Assert.AreEqual(2, fixture.Client.Calls);
        Assert.AreEqual(fixture.Client.Account.HomeAccountId.Identifier, fixture.State.Account("WORK"));
        using (var restarted = fixture.Provider())
            _ = await restarted.GetTokenAsync("work", false, CancellationToken.None);
        Assert.AreSame(fixture.Client.Account, fixture.Client.Selected);
        fixture.Client.Available = [];
        using var missing = fixture.Provider();
        await Assert.ThrowsExactlyAsync<MsalUiRequiredException>(() =>
            missing.GetTokenAsync("work", false, CancellationToken.None));
        Assert.AreEqual(0, fixture.Client.InteractiveCalls);
    }

    [TestMethod]
    public async Task DelegatedCredentialCacheLifetimeRequiresLongEnoughTokenEvenAfterOneForcedRefresh()
    {
        using var fixture = new Fixture();
        fixture.Client.ExpiresOn = DateTimeOffset.UtcNow.AddMinutes(6);
        using var provider = fixture.Provider();
        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
            provider.GetTokenWithMinimumLifetimeAsync("work", TimeSpan.FromMinutes(8), CancellationToken.None));
        Assert.AreEqual(2, fixture.Client.Calls);
        Assert.AreEqual(1, fixture.Client.Refreshes);
        fixture.Client.ExpiresOn = DateTimeOffset.UtcNow.AddHours(1);
        Assert.AreEqual("test-token", await provider.GetTokenWithMinimumLifetimeAsync(
            "work", TimeSpan.FromMinutes(8), CancellationToken.None));
        Assert.AreEqual(2, fixture.Client.Refreshes);
    }

    [TestMethod]
    public async Task RequestAcquisitionNeverOpensUiButExplicitSignInCanRecover()
    {
        using var fixture = new Fixture();
        fixture.Client.RequiresInteraction = true;
        using var provider = fixture.Provider();
        await Assert.ThrowsExactlyAsync<MsalUiRequiredException>(() =>
            provider.GetTokenAsync("work", false, CancellationToken.None));
        Assert.AreEqual(0, fixture.Client.InteractiveCalls);
        await provider.SignInAsync(CancellationToken.None);
        Assert.AreEqual(1, fixture.Client.InteractiveCalls);
        _ = await provider.GetTokenAsync("work", false, CancellationToken.None);
    }

    [TestMethod]
    [DataRow(true)]
    [DataRow(false)]
    public async Task IncorrectTenantOrExpiredTokenIsNotAccepted(bool wrongTenant)
    {
        using var fixture = new Fixture();
        if (wrongTenant) fixture.Client.Tenant = Guid.NewGuid().ToString();
        else fixture.Client.ExpiresOn = DateTimeOffset.UtcNow.AddMinutes(-1);
        using var provider = fixture.Provider();
        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
            provider.GetTokenAsync("work", false, CancellationToken.None));
        Assert.IsNull(fixture.State.Account("work"));
    }

    [TestMethod]
    public async Task IdentitiesHaveSeparateClientsAndConcurrentAccountSelectionIsPreserved()
    {
        using var fixture = new Fixture();
        var other = new FakeClient { Tenant = Guid.NewGuid().ToString() };
        var settings = new ProxySettings
        {
            Identities = [PolicyTests.Settings().Identities[0],
                new() { Name = "other", TenantId = other.Tenant, ClientId = Guid.NewGuid().ToString() }]
        };
        using var provider = new WamTokenProvider(settings, fixture.State,
            identity => identity.Name == "work" ? fixture.Client : other);
        await Task.WhenAll(provider.GetTokenAsync("work", false, CancellationToken.None),
            provider.GetTokenAsync("other", false, CancellationToken.None));
        Assert.AreEqual(1, fixture.Client.Calls);
        Assert.AreEqual(1, other.Calls);
        Assert.IsNotNull(fixture.State.Account("work"));
        Assert.IsNotNull(fixture.State.Account("other"));
    }

    [TestMethod]
    public async Task ResourceTokensAreScopedAndCachedSeparately()
    {
        using var fixture = new Fixture();
        using var provider = fixture.Provider();
        _ = await provider.GetTokenAsync("work", WamTokenProvider.AzureDevOpsResource, false, CancellationToken.None);
        _ = await provider.GetTokenAsync("work", "https://management.azure.com/", false, CancellationToken.None);
        _ = await provider.GetTokenAsync("work", "https://management.azure.com/", false, CancellationToken.None);

        Assert.AreEqual(2, fixture.Client.Calls);
        CollectionAssert.AreEquivalent(new[]
        {
            "499b84ac-1321-427f-aa17-267ca6975798/.default",
            "https://management.azure.com/.default"
        }, fixture.Client.Scopes.ToArray());
    }

    private sealed class Fixture : IDisposable
    {
        private readonly string path = Path.Combine(Path.GetTempPath(), "AdoDevProxy-WamTest-" + Guid.NewGuid().ToString("N"));
        internal UserState State { get; }
        internal FakeClient Client { get; } = new();
        internal Fixture() => State = new(path);
        internal WamTokenProvider Provider() => new(PolicyTests.Settings(), State, _ => Client);
        public void Dispose() => Directory.Delete(path, true);
    }

    private sealed class FakeClient : IWamClient
    {
        internal string Tenant = PolicyTests.Settings().Identities[0].TenantId;
        internal DateTimeOffset ExpiresOn = DateTimeOffset.UtcNow.AddHours(1);
        internal IAccount Account { get; } = PublicClientApplication.OperatingSystemAccount;
        internal IEnumerable<IAccount> Available { get; set; } = [PublicClientApplication.OperatingSystemAccount];
        internal IAccount? Selected;
        internal int Calls;
        internal int Refreshes;
        internal int InteractiveCalls;
        internal bool RequiresInteraction;
        internal TaskCompletionSource? Delay;
        internal List<string> Scopes { get; } = [];
        public Task<IEnumerable<IAccount>> AccountsAsync() => Task.FromResult(Available);
        public async Task<AuthenticationResult> SilentAsync(IAccount account, string[] scopes, bool forceRefresh,
            CancellationToken cancellation)
        {
            Selected = account;
            Scopes.Add(scopes.Single());
            Interlocked.Increment(ref Calls);
            if (forceRefresh) Interlocked.Increment(ref Refreshes);
            if (Delay is not null) await Delay.Task.WaitAsync(cancellation);
            if (RequiresInteraction) throw new MsalUiRequiredException("interaction_required", "test");
            return Result();
        }
        public Task<AuthenticationResult> InteractiveAsync(string[] scopes, CancellationToken cancellation)
        {
            Scopes.Add(scopes.Single());
            InteractiveCalls++;
            RequiresInteraction = false;
            return Task.FromResult(Result());
        }
        private AuthenticationResult Result() =>
            new("test-token", false, "test-id", ExpiresOn, ExpiresOn, Tenant, Account, "", [], Guid.NewGuid());
    }
}
