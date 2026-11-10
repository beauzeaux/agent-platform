using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Collections.Concurrent;
using Microsoft.Extensions.Options;
using System.Diagnostics;

namespace AgencyCoder;

public sealed class HarnessOrchestrator
{
    private readonly HarnessOptions options;
    private readonly NativeHarnessConfig native;
    private readonly TaskJournal journal;
    private readonly WorkspaceManager? workspace;
    private readonly AgencyPluginResolver? plugins;
    private readonly CopilotExecutor? executor;
    private readonly ConcurrentDictionary<string, CodingTask> tasks;
    private readonly SemaphoreSlim admission = new(1, 1);
    private readonly ConcurrentDictionary<string, CancellationTokenSource> active = new(StringComparer.Ordinal);
    private readonly Func<CodingTask, bool, CancellationToken, Task<string>>? executeTask;
    private string? busy;

    public HarnessOrchestrator(IOptions<HarnessOptions> options, NativeHarnessConfig native, TaskJournal journal,
        WorkspaceManager workspace, AgencyPluginResolver plugins, CopilotExecutor executor)
        : this(options.Value, native, journal, workspace, plugins, executor, new Dictionary<string, CodingTask>(), null)
    {
    }

    internal HarnessOrchestrator(HarnessOptions options, NativeHarnessConfig native, TaskJournal journal,
        WorkspaceManager? workspace, AgencyPluginResolver? plugins, CopilotExecutor? executor,
        IReadOnlyDictionary<string, CodingTask> recovered,
        Func<CodingTask, bool, CancellationToken, Task<string>>? execute)
    {
        this.options = options;
        this.native = native;
        this.journal = journal;
        this.workspace = workspace;
        this.plugins = plugins;
        this.executor = executor;
        executeTask = execute;
        tasks = new(recovered, StringComparer.Ordinal);
    }

    public async Task InitializeAsync(CancellationToken cancellationToken)
    {
        foreach (var (id, task) in await journal.RecoverAsync(cancellationToken))
            tasks[id] = task;
    }

    public CodingTask[] Snapshot() => tasks.Values.ToArray();
    public CodingTask? Get(string id) => tasks.TryGetValue(id, out var task) ? task : null;
    public object Status() => new
    {
        model = native.Model,
        wireApi = native.WireApi,
        activeTask = busy,
        tasks = tasks.Values.Select(task => new { task.Id, task.Status })
    };

    public async Task<TaskSubmissionResult> SubmitAsync(CodingTaskRequest request, bool refreshPlugins,
        CancellationToken cancellationToken)
    {
        var inputError = AdmissionError(request);
        if (inputError is not null)
            return new(400, null, inputError);
        var approvals = request.NativeToolApprovals ?? [];
        var digest = RequestDigest(request, refreshPlugins, native.ConfigSha256);
        if (tasks.TryGetValue(request.Id, out var existing))
            return existing.RequestSha256 == digest
                ? new(existing.Status is "accepted" or "working" ? 202 : 200, existing, null)
                : new(409, null, "task_identity_conflict");
        await admission.WaitAsync(cancellationToken);
        try
        {
            if (tasks.TryGetValue(request.Id, out existing))
                return existing.RequestSha256 == digest
                    ? new(existing.Status is "accepted" or "working" ? 202 : 200, existing, null)
                    : new(409, null, "task_identity_conflict");
            if (busy is not null)
                return new(409, null, "agent_busy");
            var sessionId = Environment.GetEnvironmentVariable("AGENT_SESSION_ID");
            if (sessionId is not { Length: > 0 } || !Guid.TryParse(sessionId, out var session))
                return new(503, null, "native_session_identity_required");
            var workingDirectory = WorkspaceManager.NormalizeWorkingDirectory(request.WorkingDirectory);
            var task = new CodingTask
            {
                Id = request.Id,
                Prompt = request.Prompt,
                RequestSha256 = digest,
                SessionId = sessionId,
                AllowEdits = request.AllowEdits,
                DryRun = request.DryRun,
                BaseBranch = request.BaseBranch ?? "main",
                WorkBranch = request.WorkBranch,
                WorkingDirectory = workingDirectory == "." ? null : workingDirectory,
                NativeToolApprovals = approvals,
                AcceptedAt = DateTimeOffset.UtcNow,
            };
            tasks[task.Id] = task;
            busy = task.Id;
            try
            {
                await journal.SaveAsync(task, cancellationToken);
            }
            catch
            {
                tasks.TryRemove(task.Id, out _);
                busy = null;
                throw;
            }
            var execution = new CancellationTokenSource();
            active[task.Id] = execution;
            var parent = Activity.Current?.Context;
            var baggage = Activity.Current?.Baggage.ToDictionary(pair => pair.Key, pair => pair.Value,
                StringComparer.Ordinal) ?? [];
            _ = RunAsync(task, refreshPlugins, parent, baggage, execution.Token);
            return new(202, task, null);
        }
        finally
        {
            admission.Release();
        }
    }

    public async Task CancelAsync(string id, CancellationToken cancellationToken)
    {
        if (!tasks.TryGetValue(id, out var task) || busy != id)
            return;
        if (active.TryGetValue(id, out var cancellation))
            await cancellation.CancelAsync();
        task.Status = task.Status is "completed" ? "completed" : "canceled";
        task.Error = task.Status == "canceled" ? "Cancellation requested; recovery will not replay uncertain work." : null;
        await journal.SaveAsync(task, cancellationToken);
    }

    private async Task RunAsync(CodingTask task, bool refreshPlugins, ActivityContext? parent,
        IReadOnlyDictionary<string, string?> baggage, CancellationToken outerToken)
    {
        using var activity = parent is { TraceId: var trace } && trace != default
            ? AgencyCoderTracing.Source.StartActivity("agency-coder.task", ActivityKind.Internal, parent.Value)
            : AgencyCoderTracing.Source.StartActivity("agency-coder.task");
        foreach (var (key, value) in baggage)
            activity?.AddBaggage(key, value);
        activity?.SetTag("agency_coder.task_id", task.Id);
        activity?.SetTag("kagent.session_id", task.SessionId);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(outerToken);
        timeout.CancelAfter(options.TaskTimeout);
        try
        {
            task.Status = "working";
            await journal.SaveAsync(task, timeout.Token);
            using (AgencyCoderTracing.Source.StartActivity("agency-coder.execute"))
                task.Answer = executeTask is null
                    ? await ExecuteAsync(task, refreshPlugins, timeout.Token)
                    : await executeTask(task, refreshPlugins, timeout.Token);
            if (task.Status != "canceled")
                task.Status = "completed";
        }
        catch (OperationCanceledException)
        {
            task.Status = "canceled";
            task.Error = "Task canceled; external effects are not rolled back and are never replayed.";
        }
        catch (InvalidOperationException ex)
        {
            task.Status = "failed";
            task.Error = NativeHarnessConfig.Redact(ex.Message, Credentials());
        }
        catch (Exception ex) when (ex is IOException or HttpRequestException or JsonException or UnauthorizedAccessException)
        {
            task.Status = "failed";
            task.Error = "task_execution_failed";
        }
        finally
        {
            try
            {
                task.FinishedAt = DateTimeOffset.UtcNow;
                try
                {
                    task.ArchivePath = await TaskArchive.WriteAsync(options.DurableDirectory, task, Credentials(),
                        CancellationToken.None);
                }
                catch
                {
                    task.Error = task.Error is null ? "archive_failed" : task.Error;
                }
                await journal.SaveAsync(task, CancellationToken.None);
            }
            finally
            {
                busy = null;
                if (active.TryRemove(task.Id, out var execution))
                    execution.Dispose();
            }
        }
    }

    private async Task<string> ExecuteAsync(CodingTask task, bool refreshPlugins, CancellationToken cancellationToken)
    {
        var checkoutRoot = await workspace!.PrepareAsync(task, cancellationToken);
        var executionDirectory = WorkspaceManager.ResolveExecutionDirectory(checkoutRoot, task.WorkingDirectory);
        var prepared = await plugins!.ResolveAsync(checkoutRoot, executionDirectory, refreshPlugins, cancellationToken);
        if (task.DryRun)
            return JsonSerializer.Serialize(new
            {
                dryRun = true,
                policyAndPluginsAuthorized = true,
                prepared.PolicySha256,
                workingDirectory = task.WorkingDirectory ?? ".",
                prepared.ConfigSourceCount,
                pluginBundleCount = prepared.Directories.Length,
                inferenceRequested = false,
            }, TaskJournal.Json);
        return await executor!.ExecuteAsync(task, checkoutRoot, executionDirectory, prepared, cancellationToken);
    }

    private string? AdmissionError(CodingTaskRequest request)
    {
        if (request.DryRun && (request.AllowEdits || request.WorkBranch is not null))
            return "dry_run_must_be_read_only";
        if (string.IsNullOrWhiteSpace(request.Id) || request.Id.Length > 64
            || request.Id.Any(character => !char.IsAsciiLetterOrDigit(character) && character != '-')
            || string.IsNullOrWhiteSpace(request.Prompt) || request.Prompt.Length > 16_000)
            return "invalid_task";
        if (request.BaseBranch is { } branch && !WorkspaceManager.SafeBranch(branch))
            return "invalid_base_branch";
        if (request.WorkBranch is { } work && !WorkspaceManager.SafeWorkBranch(work))
            return "invalid_work_branch";
        if (!WorkspaceManager.SafeWorkingDirectory(request.WorkingDirectory))
            return "invalid_working_directory";
        foreach (var approval in request.NativeToolApprovals ?? [])
        {
            var server = native.McpServers.SingleOrDefault(item => item.Name == approval.ServerName);
            if (server is null || !server.RequireApproval || !server.Tools.Contains(approval.ToolName, StringComparer.Ordinal))
                return "invalid_native_approval";
        }
        return null;
    }

    public static string RequestDigest(CodingTaskRequest request, bool refreshPlugins, string nativeConfigSha256) =>
        Hash(JsonSerializer.SerializeToUtf8Bytes(new
        {
            request.Id,
            request.Prompt,
            request.AllowEdits,
            request.BaseBranch,
            request.WorkBranch,
            workingDirectory = NormalizeDigestWorkingDirectory(request.WorkingDirectory),
            refreshPlugins,
            approvals = request.NativeToolApprovals ?? [],
            nativeConfigSha256,
            dryRun = request.DryRun ? (bool?)true : null,
        }, TaskJournal.Json));

    internal static string[] Credentials() => Environment.GetEnvironmentVariables().Keys.Cast<object>()
        .Select(key => key.ToString()!)
        .Where(key => key.Contains("TOKEN", StringComparison.OrdinalIgnoreCase)
            || key.Contains("SECRET", StringComparison.OrdinalIgnoreCase)
            || key.Contains("PASSWORD", StringComparison.OrdinalIgnoreCase)
            || key.Contains("CREDENTIAL", StringComparison.OrdinalIgnoreCase)
            || key.Contains("API_KEY", StringComparison.OrdinalIgnoreCase))
        .Select(key => Environment.GetEnvironmentVariable(key))
        .Where(value => value is { Length: >= 6 })
        .Cast<string>()
        .ToArray();

    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static string? NormalizeDigestWorkingDirectory(string? workingDirectory)
    {
        var normalized = WorkspaceManager.NormalizeWorkingDirectory(workingDirectory);
        return normalized == "." ? null : normalized;
    }
}
