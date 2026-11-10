using System.Security.Cryptography.X509Certificates;
using Google.Protobuf;
using Grpc.Core;
using Substrate.Credprovider.V1;

namespace HostCapabilities.NativeAdo;

public sealed class NativeAdoBroker : IDisposable
{
    private readonly AcquireAdoTokenAsync acquireToken;
    private readonly AuthorizeCredentialAsync authorize;
    private readonly GatewayCertificateTrust trust;
    private readonly object admission = new();
    private readonly int maxConcurrency;
    private readonly TimeSpan requestTimeout;
    private int active;
    private bool disposed;

    public NativeAdoBroker(NativeAdoOptions options, GatewayCertificateTrust trust,
        AuthorizeCredentialAsync authorize, AcquireAdoTokenAsync acquireToken)
    {
        options.Validate();
        this.trust = trust;
        this.authorize = authorize;
        this.acquireToken = acquireToken;
        maxConcurrency = options.MaxConcurrency;
        requestTimeout = options.RequestTimeout;
    }

    public async Task<FetchSecretResponse> FetchAsync(X509Certificate2? clientCertificate, string credentialUri,
        string actorSpiffeId, CancellationToken cancellationToken)
    {
        if (!trust.IsTrusted(clientCertificate))
        {
            throw Failure(StatusCode.Unauthenticated);
        }
        if (!IdentitySyntax.IsSpiffeId(actorSpiffeId) || !IdentitySyntax.IsCredentialUri(credentialUri))
        {
            throw Failure(StatusCode.PermissionDenied);
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(requestTimeout);
        try
        {
            timeout.Token.ThrowIfCancellationRequested();
            lock (admission)
            {
                if (disposed)
                {
                    throw Failure(StatusCode.Unavailable);
                }
                if (active == maxConcurrency)
                {
                    throw Failure(StatusCode.ResourceExhausted);
                }
                active++;
            }

            // Work owns its permit even if a non-cooperative host delegate outlives the RPC deadline.
            var work = Task.Run(() => ExecuteOwnedAsync(actorSpiffeId, credentialUri, timeout.Token), CancellationToken.None);
            _ = work.ContinueWith(static t => { _ = t.Exception; }, CancellationToken.None,
                TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
            return await work.WaitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            throw Failure(cancellationToken.IsCancellationRequested ? StatusCode.Cancelled :
                timeout.IsCancellationRequested ? StatusCode.DeadlineExceeded : StatusCode.Unavailable);
        }
    }

    private async Task<FetchSecretResponse> ExecuteOwnedAsync(string actor, string uri, CancellationToken cancellationToken)
    {
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            var grant = await authorize(actor, uri, cancellationToken);
            if (grant is null || string.IsNullOrWhiteSpace(grant.Identity))
            {
                throw new CredentialDeniedException();
            }
            cancellationToken.ThrowIfCancellationRequested();
            var token = await acquireToken(grant.Identity, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (string.IsNullOrEmpty(token) || token.Length > 16384 ||
                token.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not '-' and not '_' and not '.' and not '~' and not '+' and not '/' and not '='))
            {
                throw Failure(StatusCode.Unavailable);
            }
            return new FetchSecretResponse { OpaqueBytes = ByteString.CopyFromUtf8(token) };
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (CredentialDeniedException)
        {
            throw Failure(StatusCode.PermissionDenied);
        }
        catch (Exception)
        {
            // Never propagate host exception messages, including exceptions containing credential material.
            throw Failure(StatusCode.Unavailable);
        }
        finally
        {
            lock (admission)
            {
                active--;
            }
        }
    }

    private static RpcException Failure(StatusCode code) => new(new Status(code, "Credential request failed."));

    private sealed class CredentialDeniedException : Exception;

    public void Dispose()
    {
        lock (admission)
        {
            disposed = true;
        }
    }
}
