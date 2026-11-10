using AdoDevProxy;
using Microsoft.Identity.Client;
using System.Net.Sockets;
using System.Security.Cryptography;

var command = args.FirstOrDefault() ?? "help";
if (command == "help")
{
    Console.WriteLine("AdoDevProxy: init-ca | rotate-ca | login | serve [--sign-in] | doctor | relay");
    Console.WriteLine("Options: --config <file> --state <directory>. Default configuration: proxy.example.json.");
    return;
}

string Option(string name, string fallback)
{
    var index = Array.IndexOf(args, name);
    if (index < 0) return fallback;
    if (index + 1 == args.Length || args[index + 1].StartsWith("--")) throw new ArgumentException("Missing option value.");
    return args[index + 1];
}

try
{
    if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Run on the signed-in Windows host.");
    var settings = ProxySettings.Load(Option("--config", "proxy.example.json"));
    if (command == "relay")
    {
        using var socket = new TcpClient();
        await socket.ConnectAsync("127.0.0.1", settings.Port);
        await using var stream = socket.GetStream();
        var upstream = Console.OpenStandardInput().CopyToAsync(stream);
        var downstream = stream.CopyToAsync(Console.OpenStandardOutput());
        await Task.WhenAny(upstream, downstream);
        return;
    }
    var state = new UserState(Option("--state", Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AdoDevProxy")));
    using var cancellation = new CancellationTokenSource();
    Console.CancelKeyPress += (_, e) => { e.Cancel = true; cancellation.Cancel(); };
    if (command == "doctor")
    {
        using var certificate = System.Security.Cryptography.X509Certificates.X509CertificateLoader.LoadCertificateFromFile(state.PublicCertificatePath);
        Console.WriteLine($"Configuration valid; {settings.Identities.Length} identities, {settings.Feeds.Length} feeds.");
        Console.WriteLine($"Data listener: {settings.ListenAddress}:{settings.Port}; host control: 127.0.0.1:{settings.ControlPort}");
        Console.WriteLine("CA SHA256: " + Convert.ToHexString(SHA256.HashData(certificate.RawData)));
        Console.WriteLine("Containers need proxy settings and this public CA; no token endpoint is exposed.");
        return;
    }
    using var processLock = state.AcquireLock();
    if (command is "init-ca" or "rotate-ca")
    {
        using var certificate = state.InitializeCertificate(command == "rotate-ca");
        Console.WriteLine(state.PublicCertificatePath);
        Console.WriteLine("SHA256: " + Convert.ToHexString(SHA256.HashData(certificate.RawData)));
        return;
    }
    using var tokens = new WamTokenProvider(settings, state);
    if (command == "login" || args.Contains("--sign-in"))
        await tokens.SignInAsync(cancellation.Token, args.Contains("--interactive"));
    if (command == "login") return;
    if (command != "serve") throw new ArgumentException("Unknown command.");
    using var root = state.LoadCertificate();
    using var proxy = new ProxyHost(settings, tokens, root, cancellation.Token);
    await proxy.PrepareFeedsAsync();
    var builder = WebApplication.CreateSlimBuilder();
    builder.Logging.ClearProviders();
    builder.WebHost.ConfigureKestrel(server => server.Listen(System.Net.IPAddress.Loopback, settings.ControlPort));
    await using var control = builder.Build();
    control.MapGet("/health", (HttpContext context) =>
        context.Request.Headers.ContainsKey("Origin") ? Results.StatusCode(403) : Results.Json(new { status = "ready" }));
    await control.StartAsync(cancellation.Token);
    proxy.Start();
    Console.WriteLine($"Proxy ready: {settings.ListenAddress}:{settings.Port}. Tokens and CA signing keys stay on Windows.");
    await control.WaitForShutdownAsync(cancellation.Token);
}
catch (OperationCanceledException) { }
catch (MsalException ex)
{
    Console.Error.WriteLine($"WAM authentication failed ({ex.ErrorCode}); restart with --sign-in on the Windows host.");
    Environment.ExitCode = 1;
}
catch (Exception ex) when (ex is IOException or InvalidOperationException or ArgumentException or CryptographicException or PlatformNotSupportedException or System.Text.Json.JsonException)
{
    Console.Error.WriteLine(ex is IOException ? "I/O failure or another process is using this state/listener." : ex.Message);
    Environment.ExitCode = 1;
}
