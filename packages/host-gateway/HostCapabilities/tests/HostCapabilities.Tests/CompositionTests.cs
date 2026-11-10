using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class CompositionTests
{
    [TestMethod]
    public void PinnedPrivateExporterRequiresItsLeafPinWhileOnlineRemainsDefault()
    {
        using var directory = new OwnedDirectory();
        HostConfiguration Configuration(TelemetryConfiguration telemetry) => new()
        {
            StateDirectory = directory.Path,
            Telemetry = telemetry,
            Llm = new() { Enabled = false },
            Ado = new() { Enabled = false },
            Agency = new() { Enabled = false }
        };
        Configuration(new()
        {
            OtlpEndpoint = "https://localhost:14318/",
            CaCertificateFile = Path.Combine(directory.Path, "ca.crt"),
            ClientCertificateFile = Path.Combine(directory.Path, "client.pfx")
        }).Validate();
        Assert.ThrowsExactly<InvalidOperationException>(() => Configuration(new()
        {
            OtlpEndpoint = "https://localhost:14318/",
            CaCertificateFile = Path.Combine(directory.Path, "ca.crt"),
            ClientCertificateFile = Path.Combine(directory.Path, "client.pfx"),
            TrustMode = "pinned-private"
        }).Validate());
    }

    [TestMethod]
    public void ModelAndMcpJwtAdmissionCannotUseDifferentActorRegistries()
    {
        using var jwt = new JwtFixture();
        var directory = Path.GetDirectoryName(jwt.Configuration.ActorRegistryFile)!;
        HostConfiguration Configuration(string registry) => new()
        {
            StateDirectory = directory,
            ActorJwt = jwt.Configuration,
            Llm = new() { Enabled = false },
            Ado = new() { Enabled = false },
            Agency = new()
            {
                PythonExecutable = Path.Combine(directory, "python.exe"),
                BrokerScript = Path.Combine(directory, "broker.py"),
                BrokerConfigurationFile = registry,
                StateDirectory = Path.Combine(directory, "owned-broker"),
                ProfilesFile = Path.Combine(directory, "profiles.json"),
                AgencyExecutable = Path.Combine(directory, "agency.exe"),
                GlobalConfigurationFile = Path.Combine(directory, "empty.toml")
            }
        };
        Assert.ThrowsExactly<InvalidOperationException>(() => Configuration(Path.Combine(directory, "other-actors.json")).Validate());
        Configuration(jwt.Configuration.ActorRegistryFile).Validate();
    }

    [TestMethod]
    public async Task FailedModuleDoesNotStopHealthyModuleOrProcessLivenessAndShutdownDrainsIt()
    {
        var failed = new FakeModule("failed", true);
        var healthy = new FakeModule("healthy", false);
        await using var application = HostApplication.Create(new()
        {
            ControlPort = 0,
            ShutdownSeconds = 2,
            Llm = new() { Enabled = false },
            Agency = new() { Enabled = false },
            Ado = new() { Enabled = false }
        }, (_, _, _) => [failed, healthy]);
        await application.StartAsync();
        try
        {
            using var client = new HttpClient(new SocketsHttpHandler { UseProxy = false })
            { BaseAddress = HostApplication.Address(application) };
            await healthy.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
            await failed.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
            using var live = await client.GetAsync("/health/live");
            using var ready = await client.GetAsync("/health/ready");
            using var healthyReady = await client.GetAsync("/health/healthy/ready");
            using var failedReady = await client.GetAsync("/health/failed/ready");
            Assert.AreEqual(HttpStatusCode.OK, live.StatusCode);
            Assert.AreEqual(HttpStatusCode.ServiceUnavailable, ready.StatusCode);
            Assert.AreEqual(HttpStatusCode.OK, healthyReady.StatusCode);
            Assert.AreEqual(HttpStatusCode.ServiceUnavailable, failedReady.StatusCode);
            Assert.IsFalse((await failedReady.Content.ReadAsStringAsync()).Contains("private", StringComparison.Ordinal));
        }
        finally { await application.StopAsync(); }
        Assert.IsTrue(healthy.Stopped);
        Assert.AreEqual("stopping", healthy.State.Health.Status);
    }

    [TestMethod]
    public void ConfigurationRejectsUnknownSecretsRelativePathsPortCollisionsAndNonTlsExporters()
    {
        using var directory = new OwnedDirectory();
        var baseline = new HostConfiguration
        {
            StateDirectory = directory.Path,
            Ado = new() { Enabled = false },
            Agency = new() { Enabled = false },
            Llm = new() { Enabled = false }
        };
        var path = directory.Write("host.json", JsonSerializer.Serialize(baseline));
        HostConfiguration.Load(path);
        directory.Write("host.json", JsonSerializer.Serialize(baseline).Replace("\"ControlPort\":14540", "\"credential\":\"secret\",\"ControlPort\":14540", StringComparison.Ordinal));
        Assert.ThrowsExactly<JsonException>(() => HostConfiguration.Load(path));
        Assert.ThrowsExactly<InvalidOperationException>(() => new HostConfiguration
        {
            StateDirectory = "relative",
            Ado = new() { Enabled = false },
            Agency = new() { Enabled = false },
            Llm = new() { Enabled = false }
        }.Validate());
        Assert.ThrowsExactly<InvalidOperationException>(() => new HostConfiguration
        {
            StateDirectory = directory.Path,
            ControlPort = 4142,
            Llm = new() { Options = new() { DataDir = directory.Path, GatewayKeyFile = path, Port = 4142 } },
            Ado = new() { Enabled = false },
            Agency = new() { Enabled = false }
        }.Validate());
        Assert.ThrowsExactly<InvalidOperationException>(() => new HostConfiguration
        {
            StateDirectory = directory.Path,
            Telemetry = new() { OtlpEndpoint = "http://localhost:14318/" },
            Ado = new() { Enabled = false },
            Agency = new() { Enabled = false },
            Llm = new() { Enabled = false }
        }.Validate());
    }

    private sealed class FakeModule(string name, bool fail) : ICapabilityModule
    {
        public string Name => name;
        public bool Enabled => true;
        public CapabilityState State { get; } = new();
        internal TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal bool Stopped;
        public async Task RunAsync(CancellationToken cancellationToken)
        {
            if (fail)
            {
                Started.TrySetResult();
                throw new InvalidOperationException("private credential details");
            }
            State.Ready();
            Started.TrySetResult();
            try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken); }
            finally { Stopped = true; }
        }
    }
}
