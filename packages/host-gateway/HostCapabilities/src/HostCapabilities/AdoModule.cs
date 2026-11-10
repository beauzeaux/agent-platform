using System.Net;
using System.Net.Sockets;
using AdoDevProxy;

namespace HostCapabilities;

internal sealed class AdoModule(HostConfiguration configuration, HostTelemetry telemetry) : ICapabilityModule
{
    public string Name => "ado";
    public bool Enabled => configuration.Ado.Enabled;
    public CapabilityState State { get; } = new();

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var options = configuration.Ado;
        var settings = ProxySettings.Load(options.ConfigurationFile);
        await using var control = HostApplication.CreateListener("127.0.0.1", settings.ControlPort);
        control.MapGet("/health", () => Results.Json(State.Health, statusCode: State.IsReady ? 200 : 503));
        control.MapGet("/health/live", () => Results.Json(new { status = "live" }));
        control.MapGet("/health/ready", () => Results.Json(State.Health, statusCode: State.IsReady ? 200 : 503));
        await control.StartAsync(cancellationToken);
        try
        {
            if (options.Mode == "delegate")
                await DelegateAsync(settings, cancellationToken);
            else
                await EmbeddedAsync(settings, cancellationToken);
        }
        finally
        {
            State.Stopping();
            using var drain = new CancellationTokenSource(TimeSpan.FromSeconds(configuration.ShutdownSeconds));
            await control.StopAsync(drain.Token);
        }
    }

    private async Task EmbeddedAsync(ProxySettings settings, CancellationToken cancellationToken)
    {
        var state = new UserState(configuration.Ado.StateDirectory);
        using var processLock = state.AcquireLock();
        using var root = state.LoadCertificate();
        using var tokens = new WamTokenProvider(settings, state);
        using var proxy = new ProxyHost(settings, tokens, root, cancellationToken, telemetry.Ado);
        await proxy.PrepareFeedsAsync();
        proxy.Start();
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    foreach (var identity in settings.Identities)
                        _ = await tokens.GetTokenAsync(identity.Name, false, cancellationToken);
                    State.Ready();
                }
                catch (Exception ex) when (ex is Microsoft.Identity.Client.MsalException or InvalidOperationException
                    or OperationCanceledException)
                {
                    State.Unavailable("host_sign_in_required");
                }
                await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken);
            }
        }
        finally { await proxy.Server.StopAsync(TimeSpan.FromSeconds(configuration.ShutdownSeconds)); }
    }

    private async Task DelegateAsync(ProxySettings settings, CancellationToken cancellationToken)
    {
        using var shutdown = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var clients = settings.AllowedClients.Select(ClientSubnet.Parse).ToArray();
        using var listener = new TcpListener(IPAddress.Parse(settings.ListenAddress), settings.Port);
        using var slots = new SemaphoreSlim(64);
        var connections = new HashSet<Task>();
        listener.Start(64);
        var health = ProbeDelegateAsync(shutdown.Token);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var client = await listener.AcceptTcpClientAsync(cancellationToken);
                connections.RemoveWhere(task => task.IsCompleted);
                if (!clients.Any(subnet => subnet.Contains(((IPEndPoint)client.Client.RemoteEndPoint!).Address))
                    || !await slots.WaitAsync(0, cancellationToken))
                {
                    client.Dispose();
                    continue;
                }
                connections.Add(RelayAsync(client, slots, shutdown.Token));
            }
        }
        finally
        {
            shutdown.Cancel();
            listener.Stop();
            await Task.WhenAll(connections.Append(health));
        }
    }

    private async Task ProbeDelegateAsync(CancellationToken cancellationToken)
    {
        using var http = new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false })
        { Timeout = TimeSpan.FromSeconds(3) };
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    using var response = await http.GetAsync(
                        $"http://127.0.0.1:{configuration.Ado.DelegateControlPort}/health", cancellationToken);
                    if (response.IsSuccessStatusCode) State.Ready();
                    else State.Unavailable("delegate_not_ready");
                }
                catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException)
                {
                    State.Unavailable("delegate_unavailable");
                }
                await Task.Delay(TimeSpan.FromSeconds(5), cancellationToken);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
    }

    private async Task RelayAsync(TcpClient client, SemaphoreSlim slots, CancellationToken cancellationToken)
    {
        using (client)
        using (var upstream = new TcpClient())
        using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
        using (var observation = telemetry.Begin("ado", "delegated_transport"))
        {
            deadline.CancelAfter(TimeSpan.FromMinutes(30));
            try
            {
                await upstream.ConnectAsync(IPAddress.Loopback, configuration.Ado.DelegatePort, deadline.Token);
                await using var local = client.GetStream();
                await using var remote = upstream.GetStream();
                var send = local.CopyToAsync(remote, deadline.Token);
                var receive = remote.CopyToAsync(local, deadline.Token);
                await Task.WhenAny(send, receive);
                deadline.Cancel();
                try { await Task.WhenAll(send, receive); }
                catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { }
                observation.Outcome = "unobserved";
            }
            catch (OperationCanceledException) { observation.Outcome = "cancelled"; }
            catch (Exception ex) when (ex is IOException or SocketException) { observation.Outcome = "transport_error"; }
            finally { slots.Release(); }
        }
    }
}
