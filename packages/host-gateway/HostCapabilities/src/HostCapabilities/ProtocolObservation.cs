using System.Text;
using System.Text.Json;

namespace HostCapabilities;

internal sealed class ProtocolObservation(bool streaming, int maximumBytes = 64 * 1024, bool chatCompletions = false)
{
    private readonly MemoryStream buffer = new();
    private bool oversized;
    private bool terminal;
    private bool chatFinished;
    private string chatOutcome = "success";
    public string Outcome { get; private set; } = "unknown";

    public void Observe(ReadOnlySpan<byte> bytes)
    {
        if (!streaming)
        {
            if (!oversized && buffer.Length + bytes.Length <= maximumBytes)
                buffer.Write(bytes);
            else
            {
                oversized = true;
                buffer.SetLength(0);
            }
            return;
        }
        foreach (var value in bytes)
        {
            if (value == (byte)'\n')
            {
                if (!oversized)
                    InspectLine(buffer.GetBuffer().AsSpan(0, (int)buffer.Length));
                buffer.SetLength(0);
                oversized = false;
            }
            else if (!oversized && buffer.Length < maximumBytes)
                buffer.WriteByte(value);
            else
            {
                oversized = true;
                buffer.SetLength(0);
            }
        }
    }

    public string Complete()
    {
        if (streaming)
        {
            if (!oversized && buffer.Length > 0)
                InspectLine(buffer.GetBuffer().AsSpan(0, (int)buffer.Length));
            return terminal ? Outcome : "incomplete";
        }
        if (oversized || buffer.Length == 0)
            return "unobserved";
        try
        {
            using var document = JsonDocument.Parse(buffer.GetBuffer().AsMemory(0, (int)buffer.Length));
            return chatCompletions ? ClassifyChat(document.RootElement) : Classify(document.RootElement);
        }
        catch (JsonException) { return "invalid_json"; }
    }

    private void InspectLine(ReadOnlySpan<byte> line)
    {
        if (!chatCompletions && line.StartsWith("event:"u8))
            ObserveType(Encoding.UTF8.GetString(line[6..]).Trim());
        else if (line.StartsWith("data:"u8))
        {
            if (chatCompletions && Encoding.UTF8.GetString(line[5..]).Trim() == "[DONE]")
            {
                if (chatFinished && Outcome != "protocol_error")
                {
                    Outcome = chatOutcome;
                    terminal = true;
                }
                return;
            }
            try
            {
                using var document = JsonDocument.Parse(line[5..].ToArray());
                if (chatCompletions)
                {
                    var outcome = ClassifyChat(document.RootElement);
                    if (outcome == "protocol_error")
                    {
                        Outcome = outcome;
                        terminal = true;
                    }
                    else if (outcome is "success" or "incomplete")
                    {
                        chatFinished = true;
                        if (outcome == "incomplete") chatOutcome = outcome;
                    }
                    return;
                }
                if (document.RootElement.ValueKind == JsonValueKind.Object
                    && document.RootElement.TryGetProperty("type", out var type) && type.ValueKind == JsonValueKind.String)
                    ObserveType(type.GetString()!);
            }
            catch (JsonException) { }
        }
    }

    private static string ClassifyChat(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object) return "invalid_json";
        if (root.TryGetProperty("error", out var error) && error.ValueKind != JsonValueKind.Null) return "protocol_error";
        if (!root.TryGetProperty("choices", out var choices) || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0)
            return "unobserved";
        var outcome = "success";
        foreach (var choice in choices.EnumerateArray())
        {
            if (choice.ValueKind != JsonValueKind.Object || !choice.TryGetProperty("finish_reason", out var reason)
                || reason.ValueKind != JsonValueKind.String)
                return "unobserved";
            if (reason.GetString() is not ("stop" or "tool_calls" or "function_call")) outcome = "incomplete";
        }
        return outcome;
    }

    private void ObserveType(string type)
    {
        if (type is "error" or "response.failed" or "response.incomplete")
        {
            Outcome = type == "response.incomplete" ? "incomplete" : "protocol_error";
            terminal = true;
        }
        else if (type == "response.completed" && Outcome is not ("protocol_error" or "incomplete"))
        {
            Outcome = "success";
            terminal = true;
        }
    }

    internal static string Classify(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
            return "invalid_json";
        if (root.TryGetProperty("error", out var error) && error.ValueKind != JsonValueKind.Null)
            return "rpc_error";
        if (root.TryGetProperty("status", out var status) && status.ValueKind == JsonValueKind.String
            && status.GetString() is "failed" or "incomplete" or "cancelled")
            return "protocol_error";
        if (root.TryGetProperty("isError", out var isError) && isError.ValueKind == JsonValueKind.True)
            return "tool_error";
        if (root.TryGetProperty("result", out var result) && result.ValueKind == JsonValueKind.Object)
            return Classify(result);
        return "success";
    }
}

internal sealed class ObservedWriteStream(Stream inner, Action<ReadOnlyMemory<byte>> observe) : Stream
{
    public override bool CanRead => false;
    public override bool CanSeek => false;
    public override bool CanWrite => true;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
    public override void Flush() => inner.Flush();
    public override Task FlushAsync(CancellationToken cancellationToken) => inner.FlushAsync(cancellationToken);
    public override void Write(byte[] buffer, int offset, int count)
    {
        inner.Write(buffer, offset, count);
        observe(buffer.AsMemory(offset, count));
    }
    public override async ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellationToken = default)
    {
        await inner.WriteAsync(buffer, cancellationToken);
        observe(buffer);
    }
    public override Task WriteAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
        WriteAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
    public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
}
