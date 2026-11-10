using System.Text.Json;

namespace AgencyCoder;

public sealed class TaskArchive
{
    public static async Task<string> WriteAsync(string durableDirectory, CodingTask task, IEnumerable<string> credentials,
        CancellationToken cancellationToken)
    {
        var directory = Path.Combine(durableDirectory, "archive", task.Id);
        Directory.CreateDirectory(directory);
        var manifest = JsonSerializer.SerializeToElement(task, TaskJournal.Json).GetRawText();
        manifest = NativeHarnessConfig.Redact(manifest, credentials);
        await File.WriteAllTextAsync(Path.Combine(directory, "manifest.v1.json"), manifest, cancellationToken);
        return directory;
    }
}
