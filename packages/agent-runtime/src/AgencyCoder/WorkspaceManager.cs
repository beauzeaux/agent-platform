using System.Diagnostics;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed partial class WorkspaceManager
{
    private static readonly TimeSpan CommandTimeout = TimeSpan.FromMinutes(5);
    private readonly HarnessOptions options;

    public WorkspaceManager(IOptions<HarnessOptions> options) => this.options = options.Value;

    public async Task<string> PrepareAsync(CodingTask task, CancellationToken cancellationToken)
    {
        if (!SafeBranch(task.BaseBranch))
            throw new InvalidOperationException("invalid_base_branch");
        var root = Path.GetFullPath(Path.Combine(options.DurableDirectory, options.WorkspaceName));
        Directory.CreateDirectory(options.DurableDirectory);
        if (!Directory.Exists(Path.Combine(root, ".git")))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(root)!);
            await GitAsync(Path.GetDirectoryName(root)!, ["-c",
                "credential.helper=manager",
                "clone",
                "--depth=1",
                "--single-branch",
                "--branch",
                task.BaseBranch,
                "--",
                options.RepositoryUrl,
                root], cancellationToken);
        }
        await GitAsync(root, ["remote", "get-url", "origin"], cancellationToken, check: output =>
            output.Trim().Equals(options.RepositoryUrl, StringComparison.Ordinal));
        await GitAsync(root, ["config", "credential.helper", ""], cancellationToken);
        await GitAsync(root, ["config", "core.hooksPath", OperatingSystem.IsWindows() ? "NUL" : "/dev/null"],
            cancellationToken);
        var status = await GitAsync(root, ["status", "--porcelain"], cancellationToken);
        if (status.Length != 0)
            throw new InvalidOperationException("workspace_not_clean");
        var work = task.WorkBranch;
        if (work is not null)
        {
            if (!SafeWorkBranch(work))
                throw new InvalidOperationException("invalid_work_branch");
            await GitAsync(root, ["switch", "-C", work, task.BaseBranch], cancellationToken);
        }
        return root;
    }

    public static string ResolveExecutionDirectory(string root, string? workingDirectory)
    {
        var normalized = NormalizeWorkingDirectory(workingDirectory);
        var current = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        if (!Directory.Exists(current) || (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("invalid_working_directory");
        var scopeSegments = normalized == "." ? Array.Empty<string>() : normalized.Split('/');
        foreach (var segment in scopeSegments)
        {
            var match = Directory.EnumerateDirectories(current)
                .FirstOrDefault(path => Path.GetFileName(path) == segment);
            if (match is null)
                throw new InvalidOperationException("invalid_working_directory");
            current = match;
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("working_directory_symlink_denied");
        }
        if (!Confined(root, current))
            throw new InvalidOperationException("invalid_working_directory");
        return current;
    }

    public static string NormalizeWorkingDirectory(string? workingDirectory)
    {
        if (workingDirectory is null or ".")
            return ".";
        if (workingDirectory.Length is 0 or > 512 || workingDirectory.StartsWith("/", StringComparison.Ordinal)
            || workingDirectory.Contains('\\') || workingDirectory.Contains(':')
            || workingDirectory.Any(char.IsControl))
            throw new InvalidOperationException("invalid_working_directory");
        var segments = workingDirectory.Split('/');
        if (segments.Length > 31 || segments.Any(segment => segment is "" or "." or ".."
                || segment.EndsWith('.') || segment.EndsWith(' ')))
            throw new InvalidOperationException("invalid_working_directory");
        return string.Join('/', segments);
    }

    public static bool SafeWorkingDirectory(string? workingDirectory)
    {
        try
        {
            _ = NormalizeWorkingDirectory(workingDirectory);
            return true;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    public static bool Confined(string root, string path)
    {
        var fullRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        var fullPath = Path.GetFullPath(Path.Combine(fullRoot, path));
        var relative = Path.GetRelativePath(fullRoot, fullPath);
        return relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal)
            && !relative.Split(Path.DirectorySeparatorChar).Contains(".git", StringComparer.OrdinalIgnoreCase);
    }

    public static bool SafeBranch(string value) => BranchPattern().IsMatch(value);
    public static bool SafeWorkBranch(string value) => WorkBranchPattern().IsMatch(value);

    private async Task<string> GitAsync(string workingDirectory, string[] arguments, CancellationToken cancellationToken,
        Func<string, bool>? check = null)
    {
        return await RunAsync(options.GitExecutable, workingDirectory, PrepareGitArguments(arguments), cancellationToken,
            check);
    }

    internal static async Task<string> RunAsync(string executable, string workingDirectory, string[] arguments,
        CancellationToken cancellationToken, Func<string, bool>? check = null)
    {
        using var process = new Process
        {
            StartInfo = new(executable)
            {
                WorkingDirectory = workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            },
        };
        foreach (var argument in arguments)
            process.StartInfo.ArgumentList.Add(argument);
        process.StartInfo.Environment["GIT_TERMINAL_PROMPT"] = "0";
        process.StartInfo.Environment["GIT_LFS_SKIP_SMUDGE"] = "1";
        process.Start();
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(CommandTimeout);
        var outputTask = process.StandardOutput.ReadToEndAsync();
        var errorTask = process.StandardError.ReadToEndAsync();
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
            }
            await process.WaitForExitAsync(CancellationToken.None);
            await Task.WhenAll(outputTask, errorTask);
            throw;
        }
        var output = await outputTask;
        await errorTask;
        if (process.ExitCode != 0 || check is not null && !check(output))
            throw new InvalidOperationException("repository_command_failed");
        return output;
    }

    private static string[] PrepareGitArguments(string[] arguments) =>
        ["-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=" + (OperatingSystem.IsWindows() ? "NUL" : "/dev/null"),
            .. arguments];

    [GeneratedRegex(@"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")]
    private static partial Regex BranchPattern();

    [GeneratedRegex(@"^agent/[A-Za-z0-9-]{1,80}$|^users/[A-Za-z0-9._-]+/[A-Za-z0-9-]{1,80}$")]
    private static partial Regex WorkBranchPattern();
}
