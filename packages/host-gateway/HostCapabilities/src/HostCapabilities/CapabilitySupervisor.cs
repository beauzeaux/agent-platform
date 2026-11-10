namespace HostCapabilities;

public sealed record CapabilityHealth(string Status, string Reason);

public sealed class CapabilityState
{
    private CapabilityHealth health = new("starting", "initializing");
    public CapabilityHealth Health => Volatile.Read(ref health);
    public bool IsReady => Health.Status == "ready";
    public void Ready() => Volatile.Write(ref health, new("ready", "none"));
    public void Unavailable(string reason) => Volatile.Write(ref health, new("unavailable", reason));
    public void Disabled() => Volatile.Write(ref health, new("disabled", "configuration"));
    public void Stopping() => Volatile.Write(ref health, new("stopping", "shutdown"));
}

public interface ICapabilityModule
{
    string Name { get; }
    bool Enabled { get; }
    CapabilityState State { get; }
    Task RunAsync(CancellationToken cancellationToken);
}

public sealed class CapabilitySupervisor(IEnumerable<ICapabilityModule> modules, ILogger<CapabilitySupervisor> logger)
    : BackgroundService
{
    public IReadOnlyList<ICapabilityModule> Modules { get; } = modules.ToArray();

    protected override Task ExecuteAsync(CancellationToken stoppingToken) =>
        Task.WhenAll(Modules.Select(module => SuperviseAsync(module, stoppingToken)));

    private async Task SuperviseAsync(ICapabilityModule module, CancellationToken cancellationToken)
    {
        if (!module.Enabled)
        {
            module.State.Disabled();
            return;
        }
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await module.RunAsync(cancellationToken);
                if (!cancellationToken.IsCancellationRequested)
                    module.State.Unavailable("dependency_stopped");
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { break; }
            catch (Exception)
            {
                // A failed capability must not stop unrelated capabilities; private exception details never leave here.
                module.State.Unavailable("dependency_unavailable");
                logger.LogWarning("Capability {Module} unavailable; retrying startup after backoff.", module.Name);
            }
            try { await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken); }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { break; }
        }
        module.State.Stopping();
    }
}
