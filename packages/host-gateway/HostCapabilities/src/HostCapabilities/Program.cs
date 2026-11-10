using HostCapabilities;
using System.Diagnostics;
using System.Text.Json;

if (args.Length == 0 || args.SequenceEqual(["--help"]))
{
    Console.WriteLine("HostCapabilities serve|doctor --config <absolute JSON path>");
    Console.WriteLine("No credentials on the command line. Host login remains an explicit operation of the original providers.");
    return;
}

if (args.Length != 3 || args[0] is not ("serve" or "doctor") || args[1] != "--config")
{
    Console.Error.WriteLine("Usage: HostCapabilities serve|doctor --config <absolute JSON path>");
    Environment.ExitCode = 2;
    return;
}

try
{
    var configuration = HostConfiguration.Load(args[2]);
    if (args[0] == "doctor")
    {
        Console.WriteLine("Configuration valid. No listeners started, tokens requested, state locks acquired or certificates changed.");
        return;
    }
    if (!OperatingSystem.IsWindows())
        throw new PlatformNotSupportedException();
    Directory.CreateDirectory(configuration.StateDirectory);
    using var processLock = new FileStream(Path.Combine(configuration.StateDirectory, "host.lock"),
        FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    await using var application = HostApplication.Create(configuration);
    var ownerFile = Path.Combine(configuration.StateDirectory, "owner.json");
    File.WriteAllText(ownerFile, JsonSerializer.Serialize(new
    {
        pid = Environment.ProcessId,
        startTimeUtcTicks = Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks,
        executable = Environment.ProcessPath
    }));
    try
    {
        await application.StartAsync();
        await Task.WhenAll(application.WaitForShutdownAsync(),
            ControlChannel.RunAsync(configuration.StateDirectory, application.Lifetime));
    }
    finally { File.Delete(ownerFile); }
}
catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException
    or ArgumentException or System.Text.Json.JsonException or PlatformNotSupportedException
    or System.Security.Cryptography.CryptographicException)
{
    Console.Error.WriteLine("Host configuration, state, or listener is unavailable. Check host-owned paths and module readiness.");
    Environment.ExitCode = 1;
}
