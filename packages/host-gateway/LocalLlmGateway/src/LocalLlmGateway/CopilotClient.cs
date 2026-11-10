using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

namespace LocalLlmGateway;

public sealed class CopilotClient(HttpClient http, CopilotTokenProvider tokens, string accountType)
{
    private readonly Uri origin = UpstreamProtocol.CopilotOrigin(accountType);

    public async Task<HttpResponseMessage> SendAsync(HttpMethod method, string resource, HttpContent? content,
        CancellationToken cancellationToken, bool agentContinuation = false)
    {
        if (resource is not ("models" or "responses" or "chat/completions"))
            throw new ArgumentException("Unsupported upstream resource.", nameof(resource));
        var token = await tokens.GetAsync(cancellationToken);
        using var request = new HttpRequestMessage(method, new Uri(origin, resource)) { Content = content };
        UpstreamProtocol.AddHeaders(request);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (resource is "responses" or "chat/completions")
        {
            request.Headers.Add("openai-intent", "conversation-agent");
            request.Headers.Add("x-initiator", agentContinuation ? "agent" : "user");
        }
        var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        if (response.StatusCode == HttpStatusCode.Unauthorized)
            tokens.Invalidate(token);
        return response;
    }

    public async Task<JsonDocument> GetModelsAsync(CancellationToken cancellationToken)
    {
        using var response = await SendAsync(HttpMethod.Get, "models", null, cancellationToken);
        UpstreamProtocol.EnsureSuccess(response, "Copilot model discovery");
        return await UpstreamProtocol.ReadJsonAsync(response.Content, cancellationToken);
    }
}
