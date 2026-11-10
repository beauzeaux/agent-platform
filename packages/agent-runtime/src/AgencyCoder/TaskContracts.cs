using System.Text.Json.Serialization;

namespace AgencyCoder;

public sealed record CodingTaskRequest(
    string Id,
    string Prompt,
    bool AllowEdits = true,
    string? BaseBranch = null,
    string? WorkBranch = null,
    string? WorkingDirectory = null,
    bool RefreshPlugins = false,
    NativeToolApproval[]? NativeToolApprovals = null,
    bool DryRun = false);

public sealed record NativeToolApproval(string ServerName, string ToolName);

public sealed class CodingTask
{
    public required string Id { get; init; }
    public required string Prompt { get; init; }
    public required string RequestSha256 { get; init; }
    public required string SessionId { get; init; }
    public bool AllowEdits { get; init; }
    public bool DryRun { get; init; }
    public required string BaseBranch { get; init; }
    public string? WorkBranch { get; init; }
    public string? WorkingDirectory { get; init; }
    public NativeToolApproval[] NativeToolApprovals { get; init; } = [];
    public string Status { get; set; } = "accepted";
    public string? Error { get; set; }
    public string? Answer { get; set; }
    public string? ArchivePath { get; set; }
    public DateTimeOffset AcceptedAt { get; init; }
    public DateTimeOffset? FinishedAt { get; set; }
}

public sealed record TaskSubmissionResult(int StatusCode, CodingTask? Task, string? Error);
