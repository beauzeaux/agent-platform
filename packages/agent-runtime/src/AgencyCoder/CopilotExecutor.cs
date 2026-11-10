using GitHub.Copilot;
using GitHub.Copilot.Rpc;
using System.Collections;
using Microsoft.Extensions.Options;

namespace AgencyCoder;

public sealed class CopilotExecutor
{
    private readonly HarnessOptions options;
    private readonly NativeHarnessConfig native;

    public CopilotExecutor(IOptions<HarnessOptions> options, NativeHarnessConfig native)
    {
        this.options = options.Value;
        this.native = native;
    }

    public async Task<string> ExecuteAsync(CodingTask task, string checkoutRoot, string executionDirectory,
        PreparedPlugins plugins, CancellationToken cancellationToken)
    {
        var environment = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
            environment[(string)entry.Key] = (string?)entry.Value ?? "";
        foreach (var key in environment.Keys.Where(key => key.StartsWith("COPILOT_PROVIDER_", StringComparison.Ordinal)).ToArray())
            environment[key] = "";
        environment["COPILOT_OFFLINE"] = "true";
        environment["COPILOT_MODEL"] = native.Model;
        environment["COPILOT_PROVIDER_TYPE"] = native.ProviderType;
        environment["COPILOT_PROVIDER_BASE_URL"] = native.BaseUrl.AbsoluteUri;
        environment["COPILOT_PROVIDER_WIRE_API"] = native.WireApi;
        environment["COPILOT_PROVIDER_API_KEY"] = Environment.GetEnvironmentVariable("OPENAI_API_KEY") ?? "";
        environment["COPILOT_HOME"] = Path.Combine(options.DurableDirectory, "copilot");
        Directory.CreateDirectory(environment["COPILOT_HOME"]);
        var tools = new RepositoryTools(checkoutRoot, task.AllowEdits).Create();
        var toolNames = tools.Select(tool => tool.Name).ToList();
        var mcp = new Dictionary<string, McpServerConfig>();
        foreach (var server in native.McpServers)
        {
            mcp.Add(server.Name, new McpHttpServerConfig
            {
                Url = server.Url.AbsoluteUri,
                Headers = server.Headers.ToDictionary(),
                Timeout = server.TimeoutMilliseconds,
                Tools = server.Tools,
            });
            toolNames.AddRange(server.Tools.Select(name => "mcp:" + server.Name + "-" + name));
        }
        var availableTools = new List<string>(toolNames)
        {
            "bash",
            "read_bash",
            "stop_bash",
            "list_bash",
        };
        await using var client = new CopilotClient(new()
        {
            Connection = RuntimeConnection.ForStdio(options.CopilotExecutable, [
                "copilot",
                "--no-aec",
                "--no-config-plugins",
                "--no-default-mcps",
                "--disable-builtin-mcps",
                "--no-auto-update",
                "--no-ask-user",
                "--no-color",
                .. plugins.Directories.SelectMany(directory => new[] { "--plugin-dir", directory }),
            ]),
            Mode = CopilotClientMode.Empty,
            UseLoggedInUser = false,
            LogLevel = CopilotLogLevel.None,
            BaseDirectory = environment["COPILOT_HOME"],
            WorkingDirectory = executionDirectory,
            Environment = environment,
        });
        await client.StartAsync(cancellationToken);
        try
        {
            var configuration = new SessionConfig
            {
                SessionId = task.SessionId,
                Model = native.Model,
                Provider = new()
                {
                    Type = native.ProviderType,
                    BaseUrl = native.BaseUrl.AbsoluteUri,
                    WireApi = native.WireApi,
                    ApiKey = environment["COPILOT_PROVIDER_API_KEY"],
                    Headers = native.Headers.ToDictionary(),
                },
                WorkingDirectory = executionDirectory,
                PluginDirectories = plugins.Directories,
                SkillDirectories = plugins.Directories.Select(directory => Path.Combine(directory, "skills")).ToList(),
                McpServers = mcp,
                Tools = [.. tools],
                AvailableTools = availableTools,
                EnableSkills = true,
                Streaming = true,
                EnableSessionStore = true,
                EnableSessionTelemetry = false,
                EnableHostGitOperations = false,
                SkipCustomInstructions = true,
                SystemMessage = new()
                {
                    Mode = SystemMessageMode.Replace,
                    Content = native.Instruction +
                        "\n\nUse only the supplied repository tools, approved sandboxed shell commands, " +
                        "and approved native MCP tools. Do not access files outside the workspace. " +
                        "Read repository instructions before editing. No credentials are available to the model.",
                },
#pragma warning disable GHCP001
                OnPermissionRequest = (request, _) =>
                {
                    if (request is PermissionRequestShell shell)
                    {
                        var reason = ShellDenialReason(shell, task, checkoutRoot, executionDirectory);
                        return Task.FromResult(reason is null ? PermissionDecision.ApproveOnce()
                            : PermissionDecision.Reject("The host rejected the shell operation: " + reason));
                    }
                    return Task.FromResult(Permit(request, task, checkoutRoot, toolNames)
                        ? PermissionDecision.ApproveOnce()
                        : PermissionDecision.Reject("The host did not approve this operation."));
                },
#pragma warning restore GHCP001
            };
            await using var session = await client.CreateSessionAsync(configuration, cancellationToken);
            using var registration = cancellationToken.Register(() =>
                _ = session.AbortAsync(CancellationToken.None));
            var answer = await session.SendAndWaitAsync(new MessageOptions { Prompt = task.Prompt }, options.TaskTimeout,
                cancellationToken);
            if (answer?.Data?.Content is not { Length: > 0 } content)
                throw new InvalidOperationException("missing_assistant_reply");
            foreach (var server in native.McpServers)
                try { await session.Rpc.Mcp.StopServerAsync(server.Name, cancellationToken); }
                catch (OperationCanceledException) { throw; }
                catch { }
            return content;
        }
        finally
        {
            await client.StopAsync();
        }
    }

#pragma warning disable GHCP001
    private bool Permit(PermissionRequest request, CodingTask task, string workspace, IReadOnlyList<string> toolNames) => request switch
    {
        PermissionRequestCustomTool custom => toolNames.Contains(custom.ToolName, StringComparer.Ordinal)
            && custom.ManagedApprovalRequired != true,
        PermissionRequestMcp mcp when mcp.ManagedApprovalRequired != true => native.McpServers.Any(server =>
            server.Name == mcp.ServerName && server.Tools.Any(tool => mcp.ToolName == tool
                || mcp.ToolName == server.Name + "-" + tool)
            && (!server.RequireApproval || task.NativeToolApprovals.Any(item => item.ServerName == server.Name
                && (item.ToolName == mcp.ToolName || mcp.ToolName == server.Name + "-" + item.ToolName)))),
        PermissionRequestRead read => read.ResolvedPath is { } path && WorkspaceManager.Confined(workspace, path),
        PermissionRequestWrite write when task.AllowEdits => write.ResolvedPath is { } path
            && WorkspaceManager.Confined(workspace, path),
        _ => false,
    };

    internal static bool PermitShell(PermissionRequestShell shell, CodingTask task, string workspace,
        string? defaultWorkingDirectory = null) =>
        ShellDenialReason(shell, task, workspace, defaultWorkingDirectory) is null;

    internal static string? ShellDenialReason(PermissionRequestShell shell, CodingTask task, string workspace,
        string? defaultWorkingDirectory = null)
    {
        if (shell.ManagedApprovalRequired == true)
            return "managed approval is required";
        if (shell.RequestSandboxBypass == true)
            return "sandbox bypass was requested";
        if (shell.RequestSandboxPermissive == true)
            return "permissive sandbox mode was requested";
        if (shell.SandboxPathGrant is not null)
            return "an additional sandbox path grant was requested";
        var workingDirectory = shell.ResolvedWorkingDirectory ?? defaultWorkingDirectory;
        if (workingDirectory is null)
            return "the working directory was not resolved";
        if (!WorkspaceManager.Confined(workspace, workingDirectory))
            return "the working directory is outside the workspace";
        if (shell.ResolvedPaths is { } resolvedPaths
            && resolvedPaths.Values.Any(path => !WorkspaceManager.Confined(workspace, path)))
            return "a resolved path is outside the workspace";
        if (shell.Commands is not { Length: > 0 } commands)
            return "the command was not parsed";
        if (!task.AllowEdits && (shell.HasWriteFileRedirection || commands.Any(command => !command.ReadOnly)))
            return "the task is read-only";
        return null;
    }
#pragma warning restore GHCP001
}
