using System.Text.Json;

namespace LocalLlmGateway;

public sealed class CredentialStore(string directory)
{
    public string FilePath { get; } = Path.Combine(directory, "credentials.json");

    public async Task<string> ReadAsync(CancellationToken cancellationToken)
    {
        try
        {
            var json = await File.ReadAllTextAsync(FilePath, cancellationToken);
            using var document = JsonDocument.Parse(json);
            var token = document.RootElement.GetProperty("githubToken").GetString();
            if (string.IsNullOrWhiteSpace(token) || token.Any(char.IsWhiteSpace))
                throw new GatewayException("Saved host login is invalid. Run login again on this host.");
            return token;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException
            or KeyNotFoundException or InvalidOperationException)
        {
            throw new GatewayException("Cannot read saved host login. Run login on this host or check --data-dir.");
        }
    }

    public async Task WriteAsync(string token, CancellationToken cancellationToken)
    {
        var directory = Path.GetDirectoryName(FilePath)!;
        if (OperatingSystem.IsWindows())
            Directory.CreateDirectory(directory);
        else
            Directory.CreateDirectory(directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        var temporary = Path.Combine(directory, $".credentials-{Guid.NewGuid():N}.tmp");
        try
        {
            var options = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write, Share = FileShare.None };
            if (!OperatingSystem.IsWindows())
                options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            await using (var stream = new FileStream(temporary, options))
            {
                await JsonSerializer.SerializeAsync(stream, new { githubToken = token }, cancellationToken: cancellationToken);
                await stream.FlushAsync(cancellationToken);
            }
            File.Move(temporary, FilePath, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary))
                File.Delete(temporary);
        }
    }
}
