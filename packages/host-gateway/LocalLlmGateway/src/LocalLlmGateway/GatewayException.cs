namespace LocalLlmGateway;

// Messages are safe to print; never include upstream bodies, headers, or inner exceptions.
public sealed class GatewayException(string message, int statusCode = 503) : Exception(message)
{
    public int StatusCode { get; } = statusCode;
}
