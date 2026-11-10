using System.Diagnostics;
using System.IO.Pipes;
using System.Net;
using System.Net.Sockets;
using Microsoft.Extensions.Hosting;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class LifecycleTests
{
    [TestMethod]
    public async Task CurrentUserControlPipeGracefullyStopsOnlyItsHostLifetime()
    {
        using var directory = new OwnedDirectory();
        await using var application = HostApplication.Create(new()
        {
            ControlPort = 0,
            Llm = new() { Enabled = false },
            Agency = new() { Enabled = false },
            Ado = new() { Enabled = false }
        }, (_, _, _) => []);
        await application.StartAsync();
        var channel = ControlChannel.RunAsync(directory.Path, application.Lifetime);
        var stopped = application.WaitForShutdownAsync();
        await using (var pipe = new NamedPipeClientStream(".", ControlChannel.Name(directory.Path), PipeDirection.Out,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly))
        {
            await pipe.ConnectAsync(3000);
            await pipe.WriteAsync("stop\n"u8.ToArray());
            await pipe.FlushAsync();
        }
        await Task.WhenAll(channel, stopped).WaitAsync(TimeSpan.FromSeconds(5));
        Assert.IsTrue(application.Lifetime.ApplicationStopped.IsCancellationRequested);
    }

    [TestMethod]
    public async Task SupervisedFakeBrokerUsesSelectedPortAndStateAndDoesNotOutliveItsModule()
    {
        var python = Environment.GetEnvironmentVariable("HOST_CAPABILITIES_TEST_PYTHON");
        if (string.IsNullOrEmpty(python))
            Assert.Inconclusive("Set HOST_CAPABILITIES_TEST_PYTHON to the absolute Python executable for the process-lifecycle test.");
        using var directory = new OwnedDirectory();
        var profiles = directory.Write("profiles.json", """{"reader":{"toolsets":{}}}""");
        var config = directory.Write("broker.json", """{"bridgeKey":"test-only-distinct-bridge-credential-32","actors":{}}""");
        var options = new AgencyConfiguration
        {
            Port = 0,
            InternalPort = FreePort(),
            StateDirectory = directory.Path,
            PythonExecutable = python,
            BrokerScript = Path.Combine(AppContext.BaseDirectory, "Fixtures", "fake_broker.py"),
            BrokerConfigurationFile = config,
            ProfilesFile = profiles
        };
        var configuration = new HostConfiguration { Agency = options, ShutdownSeconds = 2 };
        using var telemetry = new HostTelemetry(new());
        var module = new AgencyModule(configuration, new(new()), telemetry);
        using var cancellation = new CancellationTokenSource();
        var running = module.RunAsync(cancellation.Token);
        int pid = 0;
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (!module.State.IsReady && DateTime.UtcNow < deadline)
                await Task.Delay(50);
            Assert.IsTrue(module.State.IsReady);
            pid = int.Parse(await File.ReadAllTextAsync(Path.Combine(directory.Path, "owned-child.pid")));
            using var child = Process.GetProcessById(pid);
            Assert.IsFalse(child.HasExited);
        }
        finally
        {
            await cancellation.CancelAsync();
            try { await running.WaitAsync(TimeSpan.FromSeconds(10)); }
            catch (OperationCanceledException) { }
        }
        Assert.AreEqual("stopping", module.State.Health.Status);
        Assert.ThrowsExactly<ArgumentException>(() => Process.GetProcessById(pid));
    }

    private static int FreePort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }
}
