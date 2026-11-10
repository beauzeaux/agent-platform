namespace LocalLlmGateway;

public static class GatewayApplication
{
    public static async Task ExecuteAsync(string command, GatewayOptions options, string? model, CancellationToken cancellationToken)
    {
        using var http = UpstreamProtocol.CreateHttpClient();
        var store = new CredentialStore(options.DataDir);
        try
        {
            if (command == "login")
            {
                var login = new GitHubLogin(http);
                var token = await login.AuthorizeAsync(
                    (uri, code) => Console.WriteLine($"On this host, authorize at {uri} using code: {code}"), cancellationToken);
                using var verify = new CopilotTokenProvider(http, token);
                await verify.GetAsync(cancellationToken);
                await store.WriteAsync(token, cancellationToken);
                Console.WriteLine("Host login saved. Credentials are plaintext in the user configuration directory.");
                return;
            }

            var gatewayKey = command == "serve"
                ? await GatewayConfiguration.LoadGatewayKeyAsync(options, cancellationToken) : null;
            var githubToken = await store.ReadAsync(cancellationToken);
            using var tokens = new CopilotTokenProvider(http, githubToken);
            var client = new CopilotClient(http, tokens, options.AccountType);
            if (command == "probe")
            {
                await ResponsesProbe.RunAsync(client, model, cancellationToken);
                return;
            }
            if (gatewayKey == githubToken)
                throw new GatewayException("The gateway key must not be the GitHub credential.");
            await using var app = GatewayServer.Create(options, gatewayKey!, client);
            using var startupDeadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            startupDeadline.CancelAfter(TimeSpan.FromSeconds(60));
            await app.Services.GetRequiredService<ModelCatalog>().GetAsync(startupDeadline.Token);
            using var started = app.Lifetime.ApplicationStarted.Register(() =>
                Console.WriteLine($"Gateway listening at {options.ListenUri}. GitHub credentials remain on this host."));
            await app.RunAsync(cancellationToken);
        }
        catch (UpstreamException ex)
        {
            throw new GatewayException($"{ex.Message} Verify host login, subscription entitlement, and account type.");
        }
    }
}
