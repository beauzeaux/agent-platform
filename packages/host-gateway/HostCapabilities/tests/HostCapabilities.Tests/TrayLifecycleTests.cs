using System.IO.Pipes;
using System.Text.Json;
using HostCapabilities.Tray;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class TrayLifecycleTests
{
    [TestMethod]
    public async Task ProbeBeforeStartIsRejected()
    {
        var lifecycle = new HostLifecycle(new HostConfiguration());
        using var client = new HttpClient();

        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
            lifecycle.ProbeAsync([], client, CancellationToken.None));
    }

    [TestMethod]
    public async Task StopBeforeStartIsIdempotent()
    {
        var lifecycle = new HostLifecycle(new HostConfiguration());

        await lifecycle.StopAsync();
        await lifecycle.StopAsync();
    }

    [TestMethod]
    public async Task ExternalStopCleansOwnerAndSerializesWithSubsequentStops()
    {
        using var directory = new OwnedDirectory();
        var lifecycle = new HostLifecycle(Configuration(directory.Path));

        await lifecycle.StartAsync();
        var ownerPath = Path.Combine(directory.Path, "owner.json");
        Assert.IsTrue(File.Exists(ownerPath));
        using (var owner = JsonDocument.Parse(await File.ReadAllTextAsync(ownerPath)))
        {
            var root = owner.RootElement;
            Assert.AreEqual(Environment.ProcessId, root.GetProperty("pid").GetInt32());
            Assert.IsTrue(root.GetProperty("startTimeUtcTicks").GetInt64() > 0);
            Assert.AreEqual(Environment.ProcessPath, root.GetProperty("executable").GetString());
        }

        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await using (var pipe = new NamedPipeClientStream(
            ".",
            TrayControlChannel.Name(directory.Path),
            PipeDirection.Out,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly))
        {
            await pipe.ConnectAsync(timeout.Token);
            await pipe.WriteAsync("stop\n"u8.ToArray(), timeout.Token);
            await pipe.FlushAsync(timeout.Token);
        }

        await WaitUntilAsync(() => !File.Exists(ownerPath), timeout.Token);
        await Task.WhenAll(lifecycle.StopAsync(), lifecycle.StopAsync()).WaitAsync(TimeSpan.FromSeconds(5));
        Assert.IsFalse(File.Exists(ownerPath));

        await using var lockFile = new FileStream(
            Path.Combine(directory.Path, "host.lock"),
            FileMode.Open,
            FileAccess.ReadWrite,
            FileShare.None);
        using var client = new HttpClient();
        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
            lifecycle.ProbeAsync([], client, CancellationToken.None));
    }

    private static HostConfiguration Configuration(string stateDirectory) => new()
    {
        ControlPort = 0,
        StateDirectory = stateDirectory,
        Llm = new() { Enabled = false },
        Ado = new() { Enabled = false },
        Agency = new() { Enabled = false },
        NativeAdo = new() { Enabled = false }
    };

    private static async Task WaitUntilAsync(Func<bool> condition, CancellationToken cancellationToken)
    {
        while (!condition())
            await Task.Delay(20, cancellationToken);
    }
}
