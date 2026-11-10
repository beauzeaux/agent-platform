using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Hosting;

namespace HostCapabilities.Tray;

internal static class TrayControlChannel
{
    internal static string Name(string stateDirectory) => "HostCapabilities-" + Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(stateDirectory).TrimEnd('\\').ToUpperInvariant())))[..24];

    internal static async Task RunAsync(string stateDirectory, IHostApplicationLifetime lifetime)
    {
        while (!lifetime.ApplicationStopping.IsCancellationRequested)
        {
            try
            {
                await using var pipe = new NamedPipeServerStream(Name(stateDirectory), PipeDirection.In, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(lifetime.ApplicationStopping);
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(lifetime.ApplicationStopping);
                deadline.CancelAfter(TimeSpan.FromSeconds(2));
                var command = new byte[5];
                await pipe.ReadExactlyAsync(command, deadline.Token);
                if (command.AsSpan().SequenceEqual("stop\n"u8))
                    lifetime.StopApplication();
            }
            catch (OperationCanceledException) when (lifetime.ApplicationStopping.IsCancellationRequested) { break; }
            catch (Exception ex) when (ex is IOException or OperationCanceledException) { }
        }
    }
}
