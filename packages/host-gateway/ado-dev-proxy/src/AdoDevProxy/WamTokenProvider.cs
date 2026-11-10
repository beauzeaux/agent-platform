using Microsoft.Identity.Client;
using Microsoft.Identity.Client.Broker;
using Microsoft.Identity.Client.Extensibility;
using System.Runtime.InteropServices;

namespace AdoDevProxy;

internal interface ITokenProvider
{
    Task<string> GetTokenAsync(string identity, bool forceRefresh, CancellationToken cancellation);
}

internal interface IWamClient
{
    Task<IEnumerable<IAccount>> AccountsAsync();
    Task<AuthenticationResult> SilentAsync(IAccount account, string[] scopes, bool forceRefresh,
        CancellationToken cancellation);
    Task<AuthenticationResult> InteractiveAsync(string[] scopes, CancellationToken cancellation);
}

internal sealed class WamTokenProvider : ITokenProvider, IDisposable
{
    internal const string AzureDevOpsResource = "499b84ac-1321-427f-aa17-267ca6975798";
    private readonly Dictionary<string, Context> contexts;
    private readonly UserState state;

    internal WamTokenProvider(ProxySettings settings, UserState state, Func<IdentitySettings, IWamClient>? createClient = null)
    {
        this.state = state;
        contexts = settings.Identities.ToDictionary(i => i.Name,
            i => new Context(i, (createClient ?? (identity => new WamClient(identity)))(i)), StringComparer.OrdinalIgnoreCase);
    }

    private sealed class WamClient : IWamClient
    {
        private readonly IPublicClientApplication app;
        internal WamClient(IdentitySettings identity)
        {
            app = PublicClientApplicationBuilder.Create(identity.ClientId)
                .WithAuthority(AzureCloudInstance.AzurePublic, identity.TenantId)
                .WithRedirectUri($"ms-appx-web://microsoft.aad.brokerplugin/{identity.ClientId}")
                .WithParentActivityOrWindow(GetParentWindow)
                .WithBroker(new BrokerOptions(BrokerOptions.OperatingSystems.Windows)
                {
                    Title = "AdoDevProxy host sign-in",
                    ListOperatingSystemAccounts = true
                })
                .Build();
            if (app is not PublicClientApplication publicApp || !publicApp.IsBrokerAvailable())
                throw new InvalidOperationException("WAM is unavailable; browser/device-code fallback is disabled.");
        }

        public Task<IEnumerable<IAccount>> AccountsAsync() => app.GetAccountsAsync();
        public Task<AuthenticationResult> SilentAsync(IAccount account, string[] scopes, bool forceRefresh,
            CancellationToken cancellation) =>
            app.AcquireTokenSilent(scopes, account).WithForceRefresh(forceRefresh).ExecuteAsync(cancellation);
        public Task<AuthenticationResult> InteractiveAsync(string[] scopes, CancellationToken cancellation) =>
            app.AcquireTokenInteractive(scopes).WithAccount(PublicClientApplication.OperatingSystemAccount)
                .WithCustomWebUi(new BrokerOnlyWebUi()).ExecuteAsync(cancellation);
    }

    private sealed class BrokerOnlyWebUi : ICustomWebUi
    {
        public Task<Uri> AcquireAuthorizationCodeAsync(Uri authorizationUri, Uri redirectUri, CancellationToken cancellationToken) =>
            throw new InvalidOperationException("WAM is unavailable; browser/device-code fallback is disabled.");
    }

    public Task<string> GetTokenAsync(string identity, bool forceRefresh, CancellationToken cancellation) =>
        GetTokenAsync(identity, AzureDevOpsResource, forceRefresh, cancellation);

    internal async Task<string> GetTokenAsync(string identity, string resource, bool forceRefresh,
        CancellationToken cancellation)
    {
        var context = contexts[identity];
        var observed = context.Tokens.GetValueOrDefault(resource);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(TimeSpan.FromSeconds(60));
        await context.Gate.WaitAsync(timeout.Token);
        try
        {
            if ((!forceRefresh || context.Tokens.GetValueOrDefault(resource) != observed)
                && context.Tokens.GetValueOrDefault(resource) is { } cached &&
                cached.ExpiresOn > DateTimeOffset.UtcNow.AddMinutes(5))
                return cached.AccessToken;
            var account = await ResolveAccount(context);
            var result = await context.App.SilentAsync(account, Scopes(resource), forceRefresh, timeout.Token);
            Accept(context, resource, result);
            return result.AccessToken;
        }
        finally { context.Gate.Release(); }
    }

    internal async Task SignInAsync(CancellationToken cancellation, bool interactive = false)
    {
        foreach (var context in contexts.Values)
        {
            try
            {
                if (interactive) throw new MsalUiRequiredException("explicit_sign_in", "Explicit host sign-in.");
                _ = await GetTokenAsync(context.Settings.Name, false, cancellation);
            }

            catch (MsalUiRequiredException)
            {
                Console.WriteLine($"Host sign-in required for {context.Settings.Name}.");
                var result = await context.App.InteractiveAsync(Scopes(AzureDevOpsResource), cancellation);
                Accept(context, AzureDevOpsResource, result);
            }
        }
    }

    internal async Task<string> GetTokenWithMinimumLifetimeAsync(string identity, TimeSpan minimumLifetime,
        CancellationToken cancellation)
    {
        var token = await GetTokenAsync(identity, false, cancellation);
        bool Sufficient() => contexts[identity].Tokens.GetValueOrDefault(AzureDevOpsResource) is { } result
            && result.AccessToken == token
            && result.ExpiresOn > DateTimeOffset.UtcNow.Add(minimumLifetime);
        if (Sufficient()) return token;
        token = await GetTokenAsync(identity, true, cancellation);
        if (!Sufficient())
            throw new InvalidOperationException("The delegated token has insufficient remaining lifetime.");
        return token;
    }

    private async Task<IAccount> ResolveAccount(Context context)
    {
        var selected = state.Account(context.Settings.Name);
        if (context.Account is not null) return context.Account;
        if (selected is null) return PublicClientApplication.OperatingSystemAccount;
        return (await context.App.AccountsAsync()).FirstOrDefault(a => a.HomeAccountId.Identifier == selected)
            ?? throw new MsalUiRequiredException("selected_account_missing", "Explicit host sign-in required.");
    }

    private void Accept(Context context, string resource, AuthenticationResult result)
    {
        if (!result.TenantId.Equals(context.Settings.TenantId, StringComparison.OrdinalIgnoreCase) ||
            result.ExpiresOn <= DateTimeOffset.UtcNow)
            throw new InvalidOperationException("Token has an unexpected tenant or expiry.");
        context.Account = result.Account;
        context.Tokens[resource] = result;
        state.SaveAccount(context.Settings.Name, result.Account.HomeAccountId.Identifier);
    }

    private static string[] Scopes(string resource) => [$"{resource.TrimEnd('/')}/.default"];

    public void Dispose()
    {
        foreach (var context in contexts.Values) context.Gate.Dispose();
    }

    private sealed class Context(IdentitySettings settings, IWamClient app)
    {
        internal IdentitySettings Settings { get; } = settings;
        internal IWamClient App { get; } = app;
        internal SemaphoreSlim Gate { get; } = new(1, 1);
        internal IAccount? Account { get; set; }
        internal Dictionary<string, AuthenticationResult> Tokens { get; } = new(StringComparer.Ordinal);
    }

    private static IntPtr GetParentWindow()
    {
        var console = GetConsoleWindow();
        var root = GetAncestor(console, 3);
        return root == IntPtr.Zero ? console : root;
    }

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll", ExactSpelling = true)]
    private static extern IntPtr GetAncestor(IntPtr window, uint flags);
}
