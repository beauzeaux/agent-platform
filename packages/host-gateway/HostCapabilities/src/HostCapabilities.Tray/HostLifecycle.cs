using System.Diagnostics;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Hosting;

namespace HostCapabilities.Tray;

internal sealed class HostLifecycle(HostConfiguration configuration)
{
    private readonly SemaphoreSlim commands = new(1, 1);
    private WebApplication? application;
    private FileStream? processLock;
    private bool ownsSession;
    private bool running;
    private long generation;
    private Task? controlTask;
    private Task? sessionTask;

    internal async Task StartAsync()
    {
        await ExecuteAsync(StartCoreAsync);
    }

    internal async Task RestartAsync()
    {
        Task? previousSession = null;
        await ExecuteAsync(async () =>
        {
            previousSession = sessionTask;
            await StopCoreAsync();
            await StartCoreAsync();
        });
        if (previousSession is not null)
            await previousSession;
    }

    internal async Task StopAsync()
    {
        Task? currentSession = null;
        await ExecuteAsync(() =>
        {
            currentSession = sessionTask;
            return StopCoreAsync();
        });
        if (currentSession is not null)
            await currentSession;
    }

    internal async Task<TraySnapshot> ProbeAsync(
        IReadOnlyList<TrayCapabilityDefinition> definitions,
        HttpClient client,
        CancellationToken cancellationToken)
    {
        if (!Volatile.Read(ref running))
            throw new InvalidOperationException("The host is not running.");
        var probeGeneration = Volatile.Read(ref generation);
        var uri = new Uri($"http://127.0.0.1:{configuration.ControlPort}/health/ready");
        using var response = await client.GetAsync(uri, cancellationToken);
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        var health = await JsonSerializer.DeserializeAsync<Dictionary<string, CapabilityHealth>>(
            stream,
            new JsonSerializerOptions(JsonSerializerDefaults.Web),
            cancellationToken);
        // Ignore a response belonging to a host generation that a restart or exit has already replaced.
        if (!Volatile.Read(ref running) || probeGeneration != Volatile.Read(ref generation))
            throw new InvalidOperationException("The host generation changed.");
        return health is null
            ? TrayStatus.Lifecycle(TrayStatusKind.Unavailable, definitions)
            : TrayStatus.FromHealth(health, definitions);
    }

    private async Task ExecuteAsync(Func<Task> action)
    {
        await commands.WaitAsync();
        try { await action(); }
        finally { commands.Release(); }
    }

    private async Task StartCoreAsync()
    {
        Directory.CreateDirectory(configuration.StateDirectory);
        processLock = new FileStream(
            Path.Combine(configuration.StateDirectory, "host.lock"),
            FileMode.OpenOrCreate,
            FileAccess.ReadWrite,
            FileShare.None);
        ownsSession = true;
        var started = false;
        WebApplication? pending = null;
        try
        {
            pending = HostApplication.Create(configuration);
            WriteOwner();
            await pending.StartAsync();
            application = pending;
            var currentGeneration = Interlocked.Increment(ref generation);
            Volatile.Write(ref running, true);
            controlTask = TrayControlChannel.RunAsync(configuration.StateDirectory, pending.Lifetime);
            sessionTask = ObserveSessionAsync(pending, currentGeneration, controlTask);
            started = true;
        }
        finally
        {
            if (!started)
            {
                try
                {
                    if (pending is not null)
                        await pending.DisposeAsync();
                }
                finally { ReleaseSessionFiles(); }
            }
        }
    }

    private async Task StopCoreAsync()
    {
        var current = application;
        application = null;
        Volatile.Write(ref running, false);
        Interlocked.Increment(ref generation);
        try
        {
            if (current is not null)
                await current.StopAsync();
            if (controlTask is not null)
                await controlTask;
        }
        finally
        {
            try
            {
                if (current is not null)
                    await current.DisposeAsync();
            }
            finally
            {
                controlTask = null;
                ReleaseSessionFiles();
            }
        }
    }

    private async Task ObserveSessionAsync(WebApplication current, long currentGeneration, Task channel)
    {
        await Task.WhenAll(current.WaitForShutdownAsync(), channel);
        await ExecuteAsync(async () =>
        {
            if (currentGeneration == Volatile.Read(ref generation) && ReferenceEquals(current, application))
                await StopCoreAsync();
        });
    }

    private void WriteOwner()
    {
        var value = new
        {
            pid = Environment.ProcessId,
            startTimeUtcTicks = Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks,
            executable = Environment.ProcessPath
        };
        File.WriteAllText(
            Path.Combine(configuration.StateDirectory, "owner.json"),
            JsonSerializer.Serialize(value));
    }

    private void ReleaseSessionFiles()
    {
        if (!ownsSession)
            return;
        var ownerPath = Path.Combine(configuration.StateDirectory, "owner.json");
        try { File.Delete(ownerPath); }
        finally
        {
            processLock?.Dispose();
            processLock = null;
            ownsSession = false;
        }
    }
}
