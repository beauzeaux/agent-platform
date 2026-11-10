using System.CommandLine;
using LocalLlmGateway;

return await GatewayCommands.Create().Parse(args).InvokeAsync();

namespace LocalLlmGateway
{
    public static class GatewayCommands
    {
        public static RootCommand Create()
        {
            var root = new RootCommand("Host-only GitHub Copilot subscription gateway (unofficial).");
            var configuration = new GatewayConfiguration();
            configuration.AddTo(root);
            var login = new Command("login", "Authorize GitHub on this host only.");
            var serve = new Command("serve", "Run the authenticated BYOK endpoint.");
            var probe = new Command("probe", "List models or verify native GPT-5 Responses against GitHub (uses quota).");
            var model = new Option<string?>("--model") { Description = "An entitled model to verify; omit to list available models." };
            probe.Options.Add(model);

            foreach (var command in new[] { login, serve, probe })
            {
                root.Subcommands.Add(command);
                command.SetAction(async (result, cancellationToken) =>
                {
                    try
                    {
                        var options = configuration.Load(result);
                        await GatewayApplication.ExecuteAsync(command.Name, options, result.GetValue(model), cancellationToken);
                        return 0;
                    }
                    catch (GatewayException ex)
                    {
                        Console.Error.WriteLine(ex.Message);
                        return 1;
                    }
                    catch (OperationCanceledException)
                    {
                        Console.Error.WriteLine("Operation canceled or timed out.");
                        return 1;
                    }
                    catch (Exception ex) when (ex is HttpRequestException or IOException or UnauthorizedAccessException)
                    {
                        Console.Error.WriteLine("Operation failed. Check host connectivity, credentials, and filesystem permissions.");
                        return 1;
                    }
                });
            }
            return root;
        }
    }
}
