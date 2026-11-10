using GitHub.Copilot;

namespace AgencyCoder.Tests;

#pragma warning disable GHCP001
[TestClass]
public sealed class CopilotExecutorTests
{
    [TestMethod]
    public void ShellPermissionAllowsSandboxedCommandsForEditingTasks()
    {
        var workspace = Path.GetFullPath(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N")));
        var request = Request(workspace, readOnly: false);

        Assert.IsTrue(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));

        request.ResolvedPaths = null;
        Assert.IsTrue(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));

        request.ResolvedWorkingDirectory = null;
        Assert.IsTrue(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace, workspace));
    }

    [TestMethod]
    public void ShellPermissionAllowsOnlyReadOnlyCommandsForReadOnlyTasks()
    {
        var workspace = Path.GetFullPath(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N")));
        var request = Request(workspace, readOnly: true);

        Assert.IsTrue(CopilotExecutor.PermitShell(request, Task(allowEdits: false), workspace));

        request.Commands = [new() { Identifier = "dotnet", ReadOnly = false }];
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: false), workspace));

        request.Commands = [new() { Identifier = "type", ReadOnly = true }];
        request.HasWriteFileRedirection = true;
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: false), workspace));
    }

    [TestMethod]
    public void ShellPermissionRejectsSandboxEscalationAndPathsOutsideWorkspace()
    {
        var workspace = Path.GetFullPath(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N")));
        var request = Request(workspace, readOnly: false);

        request.ManagedApprovalRequired = true;
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));
        request.ManagedApprovalRequired = false;

        request.RequestSandboxBypass = true;
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));
        request.RequestSandboxBypass = false;

        request.RequestSandboxPermissive = true;
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));
        request.RequestSandboxPermissive = false;

        request.ResolvedWorkingDirectory = Path.GetDirectoryName(workspace)!;
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));
        request.ResolvedWorkingDirectory = workspace;

        request.ResolvedPaths = new Dictionary<string, string>
        {
            ["argument"] = Path.Combine(Path.GetDirectoryName(workspace)!, "outside.txt"),
        };
        Assert.IsFalse(CopilotExecutor.PermitShell(request, Task(allowEdits: true), workspace));
    }

    private static PermissionRequestShell Request(string workspace, bool readOnly) => new()
    {
        CanOfferSessionApproval = false,
        Commands = [new() { Identifier = "test", ReadOnly = readOnly }],
        CommandSegments = [],
        FullCommandText = "test",
        HasWriteFileRedirection = false,
        Intention = "test",
        PossiblePaths = [],
        PossibleUrls = [],
        ResolvedPaths = new Dictionary<string, string>(),
        ResolvedWorkingDirectory = workspace,
        ToolCallId = "test",
    };

    private static CodingTask Task(bool allowEdits) => new()
    {
        Id = "permission-test",
        Prompt = "test",
        RequestSha256 = "test",
        SessionId = "test",
        AllowEdits = allowEdits,
        BaseBranch = "main",
    };
}
#pragma warning restore GHCP001
