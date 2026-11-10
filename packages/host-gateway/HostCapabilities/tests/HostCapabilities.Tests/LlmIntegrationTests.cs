using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Diagnostics;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class LlmIntegrationTests
{
    [TestMethod]
    public async Task ComposedGatewayPreservesSseBytesAndCorrelatesMetadataWithoutContent()
    {
        const string bytes = "event: response.output_text.delta\ndata: {\"delta\":\"private-output-never-export\"}\r\n\r\n"
            + "event: response.completed\ndata: {\"type\":\"response.completed\"}\n\n";
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        { Content = new StringContent(bytes, Encoding.UTF8, "text/event-stream") }));
        fixture.Client.DefaultRequestHeaders.Add("traceparent", "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01");
        using var response = await fixture.RespondAsync();
        Assert.AreEqual(bytes, await response.Content.ReadAsStringAsync());
        Assert.AreEqual(1, fixture.Inferences);
        var span = capture.Spans.Single(activity => activity.OperationName == "llm.responses");
        Assert.AreEqual("0123456789abcdef0123456789abcdef", span.TraceId.ToString());
        Assert.AreEqual("success", span.GetTagItem("host.outcome"));
        Assert.AreEqual(200, span.GetTagItem("host.upstream.status_code"));
        var exported = string.Join(" ", capture.Spans.SelectMany(activity => activity.TagObjects).Select(tag => $"{tag.Key}={tag.Value}"));
        foreach (var secret in new[] { "private-output", "private-prompt", "private-query", "private-inference", LlmFixture.Key })
            Assert.IsFalse(exported.Contains(secret, StringComparison.Ordinal));
        var streams = capture.Measurements.Where(value => value.Name == "host.streams.active").ToArray();
        Assert.IsTrue(streams.Any(value => value.Value == 1));
        Assert.AreEqual(0L, streams.Sum(value => value.Value));
    }

    [TestMethod]
    [DataRow("event: response.failed\ndata: {\"type\":\"response.failed\",\"error\":\"private-error\"}\n\n", "protocol_error")]
    [DataRow("data: {\"type\":\"response.incomplete\"}\n\n", "incomplete")]
    [DataRow("data: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\n\n", "incomplete")]
    public async Task Http200DoesNotHideSseTerminalFailureOrTruncation(string bytes, string outcome)
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        { Content = new StringContent(bytes, Encoding.UTF8, "text/event-stream") }));
        using var response = await fixture.RespondAsync();
        Assert.AreEqual(HttpStatusCode.OK, response.StatusCode);
        Assert.AreEqual(bytes, await response.Content.ReadAsStringAsync());
        Assert.AreEqual(outcome, capture.Spans.Single(activity => activity.OperationName == "llm.responses").GetTagItem("host.outcome"));
        Assert.AreEqual(1, fixture.Inferences);
    }

    [TestMethod]
    [DataRow(false)]
    [DataRow(true)]
    public async Task InvalidKeyNeverContactsUpstreamAndInference401IsNotReplayed(bool chatCompletions)
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((_, _) => Task.FromResult(LlmFixture.Json(new { error = "private-error" },
            HttpStatusCode.Unauthorized)));
        fixture.Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "invalid");
        using var denied = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.AreEqual(HttpStatusCode.Unauthorized, denied.StatusCode);
        Assert.AreEqual(0, fixture.Inferences);
        fixture.Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", LlmFixture.Key);
        using var response = await fixture.RespondAsync(chatCompletions: chatCompletions);
        Assert.AreEqual(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.IsFalse((await response.Content.ReadAsStringAsync()).Contains("private-error", StringComparison.Ordinal));
        Assert.AreEqual(1, fixture.Inferences);
        Assert.IsTrue(capture.Spans.Any(span => Equals(span.GetTagItem("host.upstream.status_code"), 401)));
    }

    [TestMethod]
    [DataRow("data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"function\":{\"arguments\":\"private-output\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\ndata: [DONE]\n\n", "success")]
    [DataRow("data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"length\"}]}\n\ndata: [DONE]\n\n", "incomplete")]
    [DataRow("data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"tool_calls\"}]}\n\n", "incomplete")]
    [DataRow("data: {\"error\":{\"message\":\"private-error\"}}\n\ndata: [DONE]\n\n", "protocol_error")]
    [DataRow("data: [DONE]\n\n", "incomplete")]
    public async Task ChatStreamsPreserveBytesAndReportTerminalOutcomeWithoutExportingContent(string bytes, string outcome)
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((request, _) =>
        {
            Assert.AreEqual("/chat/completions", request.RequestUri!.AbsolutePath);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(bytes, Encoding.UTF8, "text/event-stream")
            });
        });
        using var response = await fixture.RespondAsync(chatCompletions: true);
        Assert.AreEqual(HttpStatusCode.OK, response.StatusCode);
        Assert.AreEqual(bytes, await response.Content.ReadAsStringAsync());
        var span = capture.Spans.Single(activity => activity.OperationName == "llm.chat_completions");
        Assert.AreEqual(outcome, span.GetTagItem("host.outcome"));
        Assert.AreEqual("test-model", span.GetTagItem("gen_ai.request.model"));
        Assert.AreEqual(200, span.GetTagItem("host.upstream.status_code"));
        var exported = string.Join(" ", span.TagObjects.Select(tag => $"{tag.Key}={tag.Value}"));
        foreach (var secret in new[] { "private-output", "private-error", "private-prompt", "private-query", "private-inference", LlmFixture.Key })
            Assert.IsFalse(exported.Contains(secret, StringComparison.Ordinal));
        Assert.AreEqual(1, fixture.Inferences);
    }

    [TestMethod]
    [DataRow("stop", "success")]
    [DataRow("tool_calls", "success")]
    [DataRow("content_filter", "incomplete")]
    public async Task ChatJsonUsesItsOwnCompletionSemantics(string reason, string outcome)
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((_, _) => Task.FromResult(LlmFixture.Json(new
        {
            choices = new[] { new { finish_reason = reason, message = new { role = "assistant", content = "private-output" } } }
        })));
        using var response = await fixture.RespondAsync(streaming: false, chatCompletions: true);
        _ = await response.Content.ReadAsStringAsync();
        Assert.AreEqual(outcome, capture.Spans.Single(activity => activity.OperationName == "llm.chat_completions").GetTagItem("host.outcome"));
    }

    [TestMethod]
    public void ProtocolInspectionIsBoundedAndDoesNotConvertOversizedContentIntoSuccess()
    {
        var observer = new ProtocolObservation(true, 64);
        observer.Observe(Encoding.UTF8.GetBytes("data: " + new string('x', 1_000_000) + "\n"));
        Assert.AreEqual("incomplete", observer.Complete());
        observer.Observe("event: response.failed\n\n"u8);
        Assert.AreEqual("protocol_error", observer.Complete());
    }

    [TestMethod]
    public async Task ValidatedModelIsCorrelatedWithoutTreatingUnobservedPayloadAsSuccess()
    {
        using var capture = new TelemetryCapture();
        await using var fixture = await LlmFixture.StartAsync((_, _) => Task.FromResult(LlmFixture.Json(
            new { output = new string('x', 200_000) })));
        using var response = await fixture.RespondAsync(streaming: false);
        _ = await response.Content.ReadAsStringAsync();
        var span = capture.Spans.Single(activity => activity.OperationName == "llm.responses");
        Assert.AreEqual("test-model", span.GetTagItem("gen_ai.request.model"));
        Assert.AreEqual("unobserved", span.GetTagItem("host.outcome"));
        Assert.AreEqual(ActivityStatusCode.Unset, span.Status);
        var requests = capture.Measurements.Where(value => value.Name == "host.requests"
            && Equals(value.Tags["module"], "llm")).ToArray();
        Assert.IsTrue(requests.Length > 0);
        Assert.IsTrue(requests.All(value => Equals(value.Tags["model"], "test-model")));
    }
}
