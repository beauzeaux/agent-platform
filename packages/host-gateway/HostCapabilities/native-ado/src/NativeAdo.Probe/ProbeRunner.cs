using System.Globalization;
using System.Security.Cryptography;
using Grpc.Core;

namespace HostCapabilities.NativeAdo.Probe;

public static class ProbeRunner
{
    public static async Task<int> RunAsync(Func<CancellationToken, Task<int>> fetchLength,
        TextWriter output, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        using var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        limit.CancelAfter(timeout);
        try
        {
            var length = await fetchLength(limit.Token).WaitAsync(limit.Token);
            await output.WriteLineAsync(string.Create(CultureInfo.InvariantCulture, $"success=true length={length} status=OK"));
            return 0;
        }
        catch (RpcException exception)
        {
            return await FailureAsync(output, Enum.IsDefined(exception.StatusCode) ? exception.StatusCode.ToString() : "Unknown");
        }
        catch (OperationCanceledException)
        {
            return await FailureAsync(output, cancellationToken.IsCancellationRequested ? "Cancelled" : "DeadlineExceeded");
        }
        catch (Exception exception) when (exception is ArgumentException or CryptographicException or
            InvalidOperationException or UnauthorizedAccessException or IOException)
        {
            return await FailureAsync(output, "InvalidConfiguration");
        }
        catch (HttpRequestException)
        {
            return await FailureAsync(output, "Unavailable");
        }
        catch (Exception)
        {
            throw new RpcException(new Status(StatusCode.Internal, "Probe failed."));
        }
    }

    public static async Task<int> FailureAsync(TextWriter output, string status)
    {
        var classified = status is "Cancelled" or "DeadlineExceeded" or "Internal" or "InvalidConfiguration" or "Unavailable"
            ? status : Enum.TryParse<StatusCode>(status, out var code) && Enum.IsDefined(code) ? code.ToString() : "Unknown";
        await output.WriteLineAsync($"success=false length=0 status={classified}");
        return 1;
    }
}
