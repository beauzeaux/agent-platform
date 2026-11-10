using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed class TaskJournal
{
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
    };
    private readonly string directory;
    private readonly SemaphoreSlim gate = new(1, 1);

    public TaskJournal(IOptions<HarnessOptions> options)
    {
        directory = Path.Combine(options.Value.DurableDirectory, "tasks");
        Directory.CreateDirectory(directory);
    }

    public async Task<IReadOnlyDictionary<string, CodingTask>> RecoverAsync(CancellationToken cancellationToken)
    {
        var tasks = new Dictionary<string, CodingTask>(StringComparer.Ordinal);
        foreach (var path in Directory.EnumerateFiles(directory, "*.json"))
        {
            CodingTask task;
            await using (var input = File.OpenRead(path))
                task = await JsonSerializer.DeserializeAsync<CodingTask>(input, Json, cancellationToken)
                    ?? throw new InvalidDataException("invalid_task_journal");
            if (string.IsNullOrEmpty(task.Id)
                || task.Id.Length > 64
                || task.Id.Any(character => !char.IsAsciiLetterOrDigit(character) && character != '-')
                || task.WorkingDirectory is not null && !WorkspaceManager.SafeWorkingDirectory(task.WorkingDirectory)
                || task.Status is not ("accepted" or "working" or "completed" or "failed" or "canceled" or "interrupted"))
                throw new InvalidDataException("invalid_task_journal");
            if (task.Status is "accepted" or "working")
            {
                task.Status = "interrupted";
                task.Error = "Previous execution interrupted; not automatically replayed.";
                await SaveAsync(task, cancellationToken);
            }
            tasks.Add(task.Id, task);
        }
        return tasks;
    }

    public async Task SaveAsync(CodingTask task, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            var path = Path.Combine(directory, task.Id + ".json");
            var temporary = path + "." + Guid.NewGuid().ToString("N") + ".partial";
            try
            {
                await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                    4096, FileOptions.WriteThrough | FileOptions.Asynchronous))
                {
                    await JsonSerializer.SerializeAsync(stream, task, Json, cancellationToken);
                    await stream.FlushAsync(cancellationToken);
                    stream.Flush(flushToDisk: true);
                }
                File.Move(temporary, path, overwrite: true);
            }
            finally
            {
                File.Delete(temporary);
            }
        }
        finally
        {
            gate.Release();
        }
    }
}
