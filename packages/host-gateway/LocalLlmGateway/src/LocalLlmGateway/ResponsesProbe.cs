using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace LocalLlmGateway;

public static class ResponsesProbe
{
    public static async Task RunAsync(CopilotClient client, string? model, CancellationToken cancellationToken)
    {
        try
        {
            await VerifyAsync(client, model, cancellationToken);
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException)
        {
            throw new GatewayException("The Responses probe received an invalid upstream protocol response.");
        }
    }

    private static async Task VerifyAsync(CopilotClient client, string? model, CancellationToken cancellationToken)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromMinutes(10));
        cancellationToken = deadline.Token;
        using var catalog = await client.GetModelsAsync(cancellationToken);
        if (!catalog.RootElement.TryGetProperty("data", out var models) || models.ValueKind != JsonValueKind.Array)
            throw new GatewayException("Model discovery returned an invalid catalog.");
        if (model is null)
        {
            foreach (var entry in models.EnumerateArray())
                Console.WriteLine(entry.GetProperty("id").GetString());
            return;
        }
        if (!models.EnumerateArray().Any(entry => entry.GetProperty("id").GetString() == model))
            throw new GatewayException("The selected model is not in the host subscription catalog.");

        using (var response = await client.SendAsync(HttpMethod.Post, "responses", JsonContent.Create(new
        {
            model,
            input = "Reply with exactly GATEWAY_OK.",
            store = false,
            stream = false
        }), cancellationToken))
        {
            UpstreamProtocol.EnsureSuccess(response, "Responses JSON probe");
            using var json = await UpstreamProtocol.ReadJsonAsync(response.Content, cancellationToken);
            if (!json.RootElement.TryGetProperty("output", out var output) || output.ValueKind != JsonValueKind.Array
                || !output.EnumerateArray().Any(item => item.TryGetProperty("content", out var content)
                    && content.EnumerateArray().Any(part => part.TryGetProperty("text", out var text)
                        && text.GetString()?.Contains("GATEWAY_OK", StringComparison.Ordinal) == true)))
                throw new GatewayException("Responses JSON probe did not return the expected output.");
        }
        Console.WriteLine("Native Responses JSON: passed.");

        await VerifyStreamAsync(client, JsonContent.Create(new
        {
            model,
            input = "Reply with exactly GATEWAY_STREAM_OK.",
            store = false,
            stream = true
        }), "GATEWAY_STREAM_OK", cancellationToken);
        Console.WriteLine("Native Responses SSE: passed.");

        var toolPayload = new
        {
            model,
            input = "Call gateway_probe once with value GATEWAY_TOOL_OK. After its result, reply with that result.",
            store = false,
            stream = false,
            tools = new[]
            {
                new
                {
                    type = "function", name = "gateway_probe", description = "Echo a test value.",
                    parameters = new
                    {
                        type = "object",
                        properties = new { value = new { type = "string" } },
                        required = new[] { "value" },
                        additionalProperties = false
                    },
                    strict = true
                }
            },
            tool_choice = new { type = "function", name = "gateway_probe" }
        };
        using var toolResponse = await client.SendAsync(HttpMethod.Post, "responses", JsonContent.Create(toolPayload), cancellationToken);
        UpstreamProtocol.EnsureSuccess(toolResponse, "Responses tool probe");
        using var toolJson = await UpstreamProtocol.ReadJsonAsync(toolResponse.Content, cancellationToken);
        var items = toolJson.RootElement.GetProperty("output");
        var calls = items.EnumerateArray().Where(item => item.GetProperty("type").GetString() == "function_call").ToArray();
        if (calls.Length != 1 || calls[0].GetProperty("name").GetString() != "gateway_probe")
            throw new GatewayException("Native Responses did not produce the expected tool call.");
        using var arguments = JsonDocument.Parse(calls[0].GetProperty("arguments").GetString()!);
        if (arguments.RootElement.GetProperty("value").GetString() != "GATEWAY_TOOL_OK")
            throw new GatewayException("Native Responses tool arguments were not preserved.");

        var continuation = JsonSerializer.SerializeToNode(toolPayload)!.AsObject();
        var input = new JsonArray
        {
            new JsonObject { ["role"] = "user", ["content"] = toolPayload.input }
        };
        foreach (var item in items.EnumerateArray())
            input.Add(JsonNode.Parse(item.GetRawText()));
        input.Add(new JsonObject
        {
            ["type"] = "function_call_output",
            ["call_id"] = calls[0].GetProperty("call_id").GetString(),
            ["output"] = "GATEWAY_TOOL_OK"
        });
        continuation["input"] = input;
        continuation["stream"] = true;
        continuation["tool_choice"] = "none";
        await VerifyStreamAsync(client, JsonContent.Create(continuation), "GATEWAY_TOOL_OK", cancellationToken, agentContinuation: true);
        Console.WriteLine("Native Responses tool-call/result continuation: passed.");
    }

    private static async Task VerifyStreamAsync(CopilotClient client, HttpContent payload, string marker,
        CancellationToken cancellationToken, bool agentContinuation = false)
    {
        using var response = await client.SendAsync(HttpMethod.Post, "responses", payload, cancellationToken, agentContinuation);
        UpstreamProtocol.EnsureSuccess(response, "Responses SSE probe");
        if (response.Content.Headers.ContentType?.MediaType != "text/event-stream")
            throw new GatewayException("Responses streaming probe did not return SSE.");
        using var reader = new StreamReader(await response.Content.ReadAsStreamAsync(cancellationToken));
        var text = new System.Text.StringBuilder();
        var completed = false;
        while (await reader.ReadLineAsync(cancellationToken) is { } line)
        {
            if (!line.StartsWith("data:", StringComparison.Ordinal))
                continue;
            var data = line[5..].Trim();
            if (data == "[DONE]")
                continue;
            using var json = JsonDocument.Parse(data);
            var type = json.RootElement.GetProperty("type").GetString();
            if (type is "error" or "response.failed" or "response.incomplete")
                throw new GatewayException("Native Responses stream reported a failure.");
            if (type == "response.output_text.delta")
                text.Append(json.RootElement.GetProperty("delta").GetString());
            if (type == "response.completed")
                completed = true;
        }
        if (!completed || !text.ToString().Contains(marker, StringComparison.Ordinal))
            throw new GatewayException("Native Responses stream did not complete with the expected output.");
    }
}
