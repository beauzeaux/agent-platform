using System.Diagnostics;
using System.Net.Http;

namespace HostCapabilities.Tray;

internal sealed class TrayApplicationContext : ApplicationContext
{
    private readonly IReadOnlyList<TrayCapabilityDefinition> definitions;
    private readonly HostLifecycle lifecycle;
    private readonly HttpClient client = new() { Timeout = TimeSpan.FromSeconds(5) };
    private readonly NotifyIcon notifyIcon;
    private readonly ToolStripMenuItem overallItem = new();
    private readonly Dictionary<string, ToolStripMenuItem> capabilityItems = [];
    private readonly ToolStripMenuItem restartItem = new("Restart host");
    private readonly ToolStripMenuItem exitItem = new("Exit");
    private readonly System.Windows.Forms.Timer pollTimer = new() { Interval = 2000 };
    private bool polling;
    private bool exiting;
    private TraySnapshot snapshot;

    internal TrayApplicationContext(HostConfiguration configuration)
    {
        definitions = TrayStatus.Definitions(configuration);
        lifecycle = new HostLifecycle(configuration);
        snapshot = TrayStatus.Lifecycle(TrayStatusKind.Starting, definitions);
        var menu = BuildMenu(configuration.ControlPort);
        notifyIcon = new NotifyIcon
        {
            ContextMenuStrip = menu,
            Text = "Host Capabilities — Starting",
            Visible = true
        };
        notifyIcon.Icon = TrayIcon.Create(TrayStatusKind.Starting);
        pollTimer.Tick += PollAsync;
        UpdateUi(snapshot);
        pollTimer.Start();
        _ = StartAsync();
    }

    private ContextMenuStrip BuildMenu(int port)
    {
        var menu = new ContextMenuStrip { ShowImageMargin = false };
        overallItem.Enabled = false;
        menu.Items.Add(overallItem);
        foreach (var definition in definitions)
        {
            var item = new ToolStripMenuItem { Enabled = false };
            capabilityItems[definition.Name] = item;
            menu.Items.Add(item);
        }
        menu.Items.Add(new ToolStripSeparator());
        var diagnostics = new ToolStripMenuItem("Open diagnostics");
        diagnostics.Click += (_, _) => OpenDiagnostics(port);
        menu.Items.Add(diagnostics);
        restartItem.Click += RestartAsync;
        menu.Items.Add(restartItem);
        menu.Items.Add(new ToolStripSeparator());
        exitItem.Click += ExitAsync;
        menu.Items.Add(exitItem);
        return menu;
    }

    private async Task StartAsync()
    {
        UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Starting, definitions));
        try
        {
            await lifecycle.StartAsync();
        }
        catch (Exception)
        {
            if (exiting)
                return;
            UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Unavailable, definitions));
            Program.ShowError("The host could not start. It remains in the tray for restart or exit.");
        }
    }

    private async void RestartAsync(object? sender, EventArgs e)
    {
        if (exiting)
            return;
        restartItem.Enabled = false;
        UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Stopping, definitions));
        try
        {
            await lifecycle.RestartAsync();
            UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Starting, definitions));
        }
        catch (Exception)
        {
            UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Unavailable, definitions));
            Program.ShowError("The host could not restart. You can try again or exit.");
        }
        finally
        {
            restartItem.Enabled = true;
        }
    }

    private async void ExitAsync(object? sender, EventArgs e)
    {
        if (exiting)
            return;
        exiting = true;
        pollTimer.Stop();
        exitItem.Enabled = false;
        restartItem.Enabled = false;
        UpdateUi(TrayStatus.Lifecycle(TrayStatusKind.Stopping, definitions));
        try
        {
            await lifecycle.StopAsync();
        }
        catch (Exception)
        {
            Program.ShowError("The host could not complete shutdown cleanly. The tray application will exit.");
        }
        ExitThread();
    }

    private async void PollAsync(object? sender, EventArgs e)
    {
        if (polling || exiting)
            return;
        polling = true;
        try
        {
            var next = await lifecycle.ProbeAsync(definitions, client, CancellationToken.None);
            UpdateUi(next);
        }
        catch (Exception) when (!exiting)
        {
            var unavailable = TrayStatus.Lifecycle(TrayStatusKind.Unavailable, definitions);
            if (snapshot.Overall is not (TrayStatusKind.Starting or TrayStatusKind.Stopping))
                UpdateUi(unavailable);
        }
        finally
        {
            polling = false;
        }
    }

    private static void OpenDiagnostics(int port)
    {
        try
        {
            Process.Start(new ProcessStartInfo($"http://127.0.0.1:{port}/health/ready")
            {
                UseShellExecute = true
            });
        }
        catch (Exception)
        {
            Program.ShowError("The diagnostics page could not be opened.");
        }
    }

    private void UpdateUi(TraySnapshot next)
    {
        snapshot = next;
        overallItem.Text = $"Host: {next.Summary}";
        foreach (var capability in next.Capabilities)
            capabilityItems[capability.Definition.Name].Text =
                $"{capability.Definition.DisplayName}: {TrayStatus.Label(capability.Kind)}";
        var oldIcon = notifyIcon.Icon;
        notifyIcon.Icon = TrayIcon.Create(next.Overall);
        oldIcon?.Dispose();
        notifyIcon.Text = $"Host Capabilities — {next.Summary}";
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            pollTimer.Dispose();
            notifyIcon.Dispose();
            client.Dispose();
        }
        base.Dispose(disposing);
    }
}
