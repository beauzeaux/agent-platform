using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using Grpc.Core;
using Grpc.Net.Client;
using HostCapabilities.NativeAdo.Probe;
using Substrate.Credprovider.V1;

try
{
    return await ProbeRunner.RunAsync(async ct =>
    {
        if (args.Length != 10)
        {
            throw new ArgumentException();
        }
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var i = 0; i < args.Length; i += 2)
        {
            values.Add(args[i], args[i + 1]);
        }
        var endpoint = new Uri(values["--endpoint"], UriKind.Absolute);
        if (endpoint.Scheme != "https" || endpoint.UserInfo.Length != 0 ||
            endpoint.AbsolutePath != "/" || endpoint.Query.Length != 0 || endpoint.Fragment.Length != 0)
        {
            throw new ArgumentException();
        }
        using var certificate = X509Certificate2.CreateFromPemFile(values["--client-bundle"], values["--client-bundle"]);
        var roots = new X509Certificate2Collection();
        roots.ImportFromPem(await File.ReadAllTextAsync(values["--server-ca"], ct));
        if (roots.Count == 0)
        {
            throw new ArgumentException();
        }
        try
        {
            using var handler = new HttpClientHandler { AllowAutoRedirect = false };
            handler.ClientCertificates.Add(certificate);
            handler.ServerCertificateCustomValidationCallback = (_, server, _, errors) =>
            {
                if (server is null || (errors & (SslPolicyErrors.RemoteCertificateNameMismatch |
                    SslPolicyErrors.RemoteCertificateNotAvailable)) != 0)
                {
                    return false;
                }
                using var chain = new X509Chain();
                chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                chain.ChainPolicy.CustomTrustStore.AddRange(roots);
                chain.ChainPolicy.ApplicationPolicy.Add(new("1.3.6.1.5.5.7.3.1"));
                chain.ChainPolicy.DisableCertificateDownloads = true;
                chain.ChainPolicy.RevocationMode = X509RevocationMode.Online;
                chain.ChainPolicy.UrlRetrievalTimeout = TimeSpan.FromSeconds(2);
                return chain.Build(server);
            };
            using var channel = GrpcChannel.ForAddress(endpoint,
                new GrpcChannelOptions { HttpHandler = handler, MaxReceiveMessageSize = 32768 });
            using var call = new CredentialProvider.CredentialProviderClient(channel).FetchSecretAsync(
                new FetchSecretRequest { ActorSpiffeId = values["--actor"], Uri = values["--uri"] },
                deadline: DateTime.UtcNow.AddSeconds(10), cancellationToken: ct);
            var response = await call.ResponseAsync;
            return response.OpaqueBytes.Length;
        }
        finally
        {
            foreach (var root in roots)
            {
                root.Dispose();
            }
        }
    }, Console.Out, TimeSpan.FromSeconds(10));
}
catch (RpcException)
{
    return await ProbeRunner.FailureAsync(Console.Out, "Internal");
}
