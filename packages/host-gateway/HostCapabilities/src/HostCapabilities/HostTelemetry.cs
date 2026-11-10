using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using AdoDevProxy;

namespace HostCapabilities;

public sealed class HostTelemetry : IDisposable
{
    public const string SourceName = "HostCapabilities";
    public ActivitySource Activities { get; } = new(SourceName);
    private readonly Meter meter = new(SourceName);
    private readonly Counter<long> requests;
    private readonly Counter<long> tools;
    private readonly Counter<long> sessionEvents;
    private readonly UpDownCounter<long> active;
    private readonly UpDownCounter<long> streams;
    private readonly Histogram<double> durations;
    private readonly Histogram<double> firstBytes;
    private long activeSessions;
    private readonly TracerProvider? traces;
    private readonly MeterProvider? metrics;
    private readonly ExporterTls? tls;

    public HostTelemetry(TelemetryConfiguration configuration, IEnumerable<ICapabilityModule>? modules = null)
    {
        requests = meter.CreateCounter<long>("host.requests");
        tools = meter.CreateCounter<long>("host.tool.calls");
        sessionEvents = meter.CreateCounter<long>("host.mcp.session.events");
        active = meter.CreateUpDownCounter<long>("host.requests.active");
        streams = meter.CreateUpDownCounter<long>("host.streams.active");
        durations = meter.CreateHistogram<double>("host.request.duration", "s");
        firstBytes = meter.CreateHistogram<double>("host.response.first_byte", "s");
        meter.CreateObservableGauge("host.mcp.sessions.active", () => Interlocked.Read(ref activeSessions));
        if (modules is not null)
            meter.CreateObservableGauge("host.capability.ready", () => modules.Select(module =>
                new Measurement<int>(module.State.IsReady ? 1 : 0, new KeyValuePair<string, object?>("module", module.Name))));
        if (configuration.OtlpEndpoint is null)
            return;
        tls = new(configuration);
        var resource = ResourceBuilder.CreateEmpty().AddService(configuration.ServiceName);
        void Configure(OtlpExporterOptions options, string path)
        {
            options.Endpoint = new Uri(new Uri(configuration.OtlpEndpoint), path);
            options.Protocol = OtlpExportProtocol.HttpProtobuf;
            options.Headers = "";
            options.TimeoutMilliseconds = 5000;
            options.HttpClientFactory = tls.CreateClient;
        }
        traces = Sdk.CreateTracerProviderBuilder().SetResourceBuilder(resource).AddSource(SourceName)
            .SetSampler(new ParentBasedSampler(new AlwaysOnSampler()))
            .AddOtlpExporter(options =>
            {
                Configure(options, "v1/traces");
                var processor = options.BatchExportProcessorOptions;
                processor.MaxQueueSize = 2048;
                processor.MaxExportBatchSize = 256;
                processor.ScheduledDelayMilliseconds = 5000;
                processor.ExporterTimeoutMilliseconds = 5000;
            }).Build();
        metrics = Sdk.CreateMeterProviderBuilder().SetResourceBuilder(resource).AddMeter(SourceName)
            .AddOtlpExporter((options, reader) =>
            {
                Configure(options, "v1/metrics");
                reader.PeriodicExportingMetricReaderOptions.ExportIntervalMilliseconds = 30000;
                reader.PeriodicExportingMetricReaderOptions.ExportTimeoutMilliseconds = 5000;
            }).Build();
    }

    internal RequestObservation Begin(string module, string operation, HttpContext? context = null)
    {
        ActivityContext parent = default;
        if (context is not null)
            ActivityContext.TryParse(context.Request.Headers["traceparent"], context.Request.Headers["tracestate"], out parent);
        var activity = Activities.StartActivity($"{module}.{operation}", ActivityKind.Server, parent);
        activity?.SetTag("host.capability", module);
        activity?.SetTag("host.operation", operation);
        active.Add(1, new("module", module), new("operation", operation));
        return new(this, module, operation, activity);
    }

    internal void Tool(string layer, string toolset, string tool, string outcome)
    {
        tools.Add(1, new("layer", layer), new("toolset", toolset), new("tool", tool), new("outcome", outcome));
        using var activity = Activities.StartActivity($"agency.{layer}.{tool}", ActivityKind.Internal);
        activity?.SetTag("host.capability", "agency");
        activity?.SetTag("host.mcp.toolset", toolset);
        activity?.SetTag("host.mcp.tool", tool);
        activity?.SetTag("host.outcome", outcome);
        activity?.SetStatus(outcome == "success" ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
    }

    internal void Session(string outcome) => sessionEvents.Add(1, new KeyValuePair<string, object?>("outcome", outcome));
    internal void Sessions(int count) => Interlocked.Exchange(ref activeSessions, count);
    internal void Ado(ProxyObservation observation)
    {
        var tags = new TagList
        {
            { "module", "ado" }, { "operation", observation.Operation }, { "outcome", observation.Outcome },
            { "status_code", observation.GatewayStatus }, { "upstream_status_code", observation.UpstreamStatus }
        };
        requests.Add(1, tags);
        durations.Record(observation.DurationSeconds, tags);
        using var activity = Activities.StartActivity("ado." + observation.Operation, ActivityKind.Server,
            default(ActivityContext), startTime: DateTimeOffset.UtcNow.AddSeconds(-observation.DurationSeconds));
        activity?.SetTag("host.capability", "ado");
        activity?.SetTag("host.operation", observation.Operation);
        activity?.SetTag("http.response.status_code", observation.GatewayStatus);
        activity?.SetTag("host.upstream.status_code", observation.UpstreamStatus);
        activity?.SetTag("host.outcome", observation.Outcome);
        activity?.SetStatus(observation.Outcome == "success" ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
    }

    public void Dispose()
    {
        metrics?.Dispose();
        traces?.Dispose();
        tls?.Dispose();
        meter.Dispose();
        Activities.Dispose();
    }

    internal sealed class RequestObservation(HostTelemetry owner, string module, string operation, Activity? activity)
        : IDisposable
    {
        private readonly long started = Stopwatch.GetTimestamp();
        private bool streaming;
        private bool firstByte;
        private string? model;
        public int GatewayStatus { get; set; }
        public int UpstreamStatus { get; set; }
        public string Outcome { get; set; } = "unavailable";
        public void Model(string name)
        {
            model = name;
            activity?.SetTag("gen_ai.request.model", name);
        }
        public void Actor(ActorIdentity identity)
        {
            activity?.SetTag("host.actor.id", identity.Uid);
            activity?.SetTag("host.actor.profile", identity.Profile);
        }
        public void Stream()
        {
            if (streaming) return;
            streaming = true;
            owner.streams.Add(1, new KeyValuePair<string, object?>("module", module));
        }
        public void FirstByte()
        {
            if (firstByte) return;
            firstByte = true;
            owner.firstBytes.Record(Stopwatch.GetElapsedTime(started).TotalSeconds,
                new("module", module), new("operation", operation));
        }
        public void Dispose()
        {
            var elapsed = Stopwatch.GetElapsedTime(started).TotalSeconds;
            var tags = new TagList
            {
                { "module", module }, { "operation", operation }, { "outcome", Outcome },
                { "status_code", GatewayStatus }, { "upstream_status_code", UpstreamStatus }
            };
            if (model is not null) tags.Add("model", model);
            owner.requests.Add(1, tags);
            owner.durations.Record(elapsed, tags);
            owner.active.Add(-1, new("module", module), new("operation", operation));
            if (streaming) owner.streams.Add(-1, new KeyValuePair<string, object?>("module", module));
            activity?.SetTag("http.response.status_code", GatewayStatus);
            activity?.SetTag("host.upstream.status_code", UpstreamStatus);
            activity?.SetTag("host.outcome", Outcome);
            activity?.SetStatus(Outcome == "success" ? ActivityStatusCode.Ok
                : Outcome == "unobserved" ? ActivityStatusCode.Unset : ActivityStatusCode.Error);
            activity?.Dispose();
        }
    }
}

internal sealed class ExporterTls : IDisposable
{
    private readonly X509Certificate2 root;
    private readonly X509Certificate2 client;
    private readonly bool pinnedPrivate;
    private readonly string? serverCertificateFile;

    public ExporterTls(TelemetryConfiguration configuration)
    {
        if (configuration.TrustMode is not ("online" or "pinned-private"))
            throw new InvalidOperationException("Unknown exporter trust mode.");
        pinnedPrivate = configuration.TrustMode == "pinned-private";
        serverCertificateFile = configuration.ServerCertificateFile;
        if (pinnedPrivate) HostConfiguration.RequirePath(serverCertificateFile ?? "");
        root = X509CertificateLoader.LoadCertificateFromFile(configuration.CaCertificateFile!);
        try { client = LoadClient(configuration); }
        catch { root.Dispose(); throw; }
    }

    public HttpClient CreateClient()
    {
        var handler = new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false };
        handler.SslOptions.ClientCertificates = new X509CertificateCollection { client };
        handler.SslOptions.CertificateRevocationCheckMode = pinnedPrivate ? X509RevocationMode.NoCheck : X509RevocationMode.Online;
        // A fresh handshake for every export makes pin removal effective even with an existing HttpClient.
        if (pinnedPrivate) handler.PooledConnectionLifetime = TimeSpan.Zero;
        handler.SslOptions.RemoteCertificateValidationCallback = (_, certificate, chain, errors) =>
            ValidateCertificate(certificate, chain, errors);
        return new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(5) };
    }

    internal bool ValidateCertificate(X509Certificate? certificate, X509Chain? presentedChain, SslPolicyErrors errors)
    {
        if (certificate is null || (errors & (SslPolicyErrors.RemoteCertificateNameMismatch
            | SslPolicyErrors.RemoteCertificateNotAvailable)) != 0)
            return false;
        try
        {
            using var remote = X509CertificateLoader.LoadCertificate(certificate.GetRawCertData());
            if (!remote.Extensions.OfType<X509EnhancedKeyUsageExtension>().Any(extension =>
                    extension.EnhancedKeyUsages.Cast<Oid>().Any(oid => oid.Value == "1.3.6.1.5.5.7.3.1"))
                || remote.Extensions.OfType<X509BasicConstraintsExtension>().Any(extension => extension.CertificateAuthority))
                return false;
            if (pinnedPrivate)
            {
                using var pin = X509CertificateLoader.LoadCertificateFromFile(serverCertificateFile!);
                if (!CryptographicOperations.FixedTimeEquals(SHA256.HashData(pin.RawData), SHA256.HashData(remote.RawData)))
                    return false;
            }
            using var custom = new X509Chain();
            custom.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
            custom.ChainPolicy.CustomTrustStore.Add(root);
            custom.ChainPolicy.RevocationMode = pinnedPrivate ? X509RevocationMode.NoCheck : X509RevocationMode.Online;
            custom.ChainPolicy.RevocationFlag = X509RevocationFlag.ExcludeRoot;
            custom.ChainPolicy.DisableCertificateDownloads = true;
            custom.ChainPolicy.UrlRetrievalTimeout = TimeSpan.FromSeconds(2);
            custom.ChainPolicy.ApplicationPolicy.Add(new("1.3.6.1.5.5.7.3.1"));
            if (presentedChain is not null)
                foreach (var element in presentedChain.ChainElements)
                    custom.ChainPolicy.ExtraStore.Add(element.Certificate);
            return custom.Build(remote);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or CryptographicException)
        {
            return false;
        }
    }

    private static X509Certificate2 LoadClient(TelemetryConfiguration configuration)
    {
        var password = configuration.ClientCertificatePasswordFile is { } path ? File.ReadAllText(path).TrimEnd('\r', '\n') : null;
        var certificate = X509CertificateLoader.LoadPkcs12FromFile(configuration.ClientCertificateFile!, password,
            X509KeyStorageFlags.UserKeySet);
        if (!certificate.HasPrivateKey)
        {
            certificate.Dispose();
            throw new InvalidOperationException("The OTLP client certificate must contain its private key.");
        }
        return certificate;
    }

    public void Dispose()
    {
        client.Dispose();
        root.Dispose();
    }
}
