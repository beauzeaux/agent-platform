using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

namespace HostCapabilities.Tray;

internal static class TrayIcon
{
    internal static Icon Create(TrayStatusKind status)
    {
        using var bitmap = new Bitmap(32, 32);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.Clear(Color.Transparent);
        var color = TrayStatus.ColorFor(status);
        using var brush = new SolidBrush(color);
        graphics.FillEllipse(brush, 3, 3, 26, 26);
        using var pen = new Pen(Color.White, 2);
        graphics.DrawEllipse(pen, 3, 3, 26, 26);
        var handle = bitmap.GetHicon();
        try { return (Icon)Icon.FromHandle(handle).Clone(); }
        finally { DestroyIcon(handle); }
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(nint handle);
}
