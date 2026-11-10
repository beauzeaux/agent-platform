using System.Text;
using System.Diagnostics;
using Microsoft.Extensions.AI;

namespace AgencyCoder;

public sealed class RepositoryTools
{
    private readonly string root;
    private readonly bool allowEdits;
    private int calls;

    public RepositoryTools(string root, bool allowEdits)
    {
        this.root = Path.GetFullPath(root);
        this.allowEdits = allowEdits;
    }

    public IReadOnlyList<AIFunction> Create()
    {
        var tools = new List<AIFunction>
        {
            Function<string, string>(nameof(repo_list), "List repository paths below a relative prefix.", repo_list),
            Function<string, string>(nameof(repo_read), "Read one UTF-8 repository file, up to 64 KiB.", repo_read),
            Function(repo_status, "repo_status",
                "Confirm that the repository workspace is available."),
            Function(repo_diff, "repo_diff",
                "Read repository changes in the confined workspace."),
        };
        if (allowEdits)
        {
            tools.Add(Function<string, string, string>(nameof(repo_write),
                "Replace one UTF-8 file in the confined repository, up to 64 KiB.", repo_write));
            tools.Add(Function<string, string>(nameof(repo_delete), "Delete one confined regular repository file.",
                repo_delete));
        }
        return tools;
    }

    private string repo_list(string prefix)
    {
        ThrowIfToolBudgetExceeded();
        var directory = Resolve(prefix, directory: true);
        if (Directory.Exists(directory))
            return string.Join('\n', Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories)
                .Select(path => Path.GetRelativePath(root, path))
                .Where(path => WorkspaceManager.Confined(root, path))
                .Order().Take(200));
        throw new InvalidOperationException("repository_directory_not_found");
    }

    private string repo_read(string path)
    {
        ThrowIfToolBudgetExceeded();
        var file = Resolve(path);
        var info = new FileInfo(file);
        if (!info.Exists || info.Length > 64 * 1024)
            throw new InvalidOperationException("repository_file_limit");
        var bytes = File.ReadAllBytes(file);
        if (bytes.Contains((byte)0))
            throw new InvalidOperationException("binary_file_denied");
        return Encoding.UTF8.GetString(bytes);
    }

    private string repo_status() => Git(["status", "--short"]);

    private string repo_diff() => Git(["diff", "--no-ext-diff", "--no-textconv"], 64 * 1024);

    private string repo_write(string path, string content)
    {
        ThrowIfToolBudgetExceeded();
        if (!allowEdits || Encoding.UTF8.GetByteCount(content) > 64 * 1024)
            throw new InvalidOperationException("repository_write_denied");
        var file = Resolve(path, create: true);
        Directory.CreateDirectory(Path.GetDirectoryName(file)!);
        File.WriteAllText(file, content, new UTF8Encoding(false));
        return "File updated in the confined local checkout.";
    }

    private string repo_delete(string path)
    {
        ThrowIfToolBudgetExceeded();
        if (!allowEdits)
            throw new InvalidOperationException("repository_write_denied");
        var file = Resolve(path);
        if (!File.Exists(file))
            throw new InvalidOperationException("repository_file_required");
        File.Delete(file);
        return "File deleted in the confined local checkout.";
    }

    private string Resolve(string path, bool create = false, bool directory = false)
    {
        if (path.Contains('\\', StringComparison.Ordinal) && !OperatingSystem.IsWindows())
            throw new InvalidOperationException("invalid_repository_path");
        var candidate = Path.GetFullPath(Path.Combine(root, path));
        var relative = Path.GetRelativePath(root, candidate);
        if (relative == ".." || relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal)
            || relative.Split(Path.DirectorySeparatorChar).Contains(".git", StringComparer.OrdinalIgnoreCase))
            throw new InvalidOperationException("invalid_repository_path");
        if (!directory && Directory.Exists(candidate))
            throw new InvalidOperationException("repository_file_required");
        if (!create && !directory && !File.Exists(candidate))
            throw new InvalidOperationException("repository_file_not_found");
        ThrowIfReparsePoint(candidate);
        return candidate;
    }

    private void ThrowIfReparsePoint(string candidate)
    {
        var relative = Path.GetRelativePath(root, candidate);
        var current = root;
        foreach (var segment in relative.Split(Path.DirectorySeparatorChar))
        {
            current = Path.Combine(current, segment);
            if (!Path.Exists(current))
                break;
            var attributes = File.GetAttributes(current);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("repository_symlink_denied");
        }
    }

    private string Git(string[] arguments, int maximumBytes = 16 * 1024)
    {
        ThrowIfToolBudgetExceeded();
        var start = new ProcessStartInfo("git")
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in new[] { "-c", "credential.helper=", "-c", "core.hooksPath=" +
            (OperatingSystem.IsWindows() ? "NUL" : "/dev/null"), "-C", root }.Concat(arguments))
            start.ArgumentList.Add(argument);
        using var process = Process.Start(start)!;
        var output = process.StandardOutput.ReadToEnd();
        _ = process.StandardError.ReadToEnd();
        process.WaitForExit();
        if (process.ExitCode != 0 || output.Length > maximumBytes)
            throw new InvalidOperationException("repository_command_failed");
        return output;
    }

    private void ThrowIfToolBudgetExceeded()
    {
        if (++calls > 48)
            throw new InvalidOperationException("tool_budget_exceeded");
    }

    private static AIFunction Function(Func<string> handler, string name, string description) =>
        AIFunctionFactory.Create(handler, name, description);
    private static AIFunction Function<T, TResult>(string name, string description, Func<T, TResult> handler) =>
        AIFunctionFactory.Create(handler, name, description);
    private static AIFunction Function<T1, T2, TResult>(string name, string description, Func<T1, T2, TResult> handler) =>
        AIFunctionFactory.Create(handler, name, description);
}
