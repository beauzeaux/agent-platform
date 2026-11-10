using HostCapabilities.Tray;

namespace HostCapabilities.Tray;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        if (args.Length != 2 || args[0] != "--config" || !Path.IsPathFullyQualified(args[1]))
        {
            ShowError("Usage: HostCapabilities.Tray.exe --config <absolute JSON path>");
            return 2;
        }

        HostConfiguration configuration;
        try
        {
            configuration = HostConfiguration.Load(args[1]);
        }
        catch (Exception)
        {
            ShowError("The host configuration is invalid or unavailable. Check the JSON path and settings.");
            return 1;
        }

        using var context = new TrayApplicationContext(configuration);
        Application.Run(context);
        return 0;
    }

    internal static void ShowError(string message) =>
        MessageBox.Show(message, "Host Capabilities", MessageBoxButtons.OK, MessageBoxIcon.Error);
}
