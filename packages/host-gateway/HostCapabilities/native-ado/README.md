# Native ADO credential-provider prototype

**Bounded canary only; not retirement-ready.** The current ADO proxy remains authoritative.
No live cluster/configuration/service changes, WAM calls, ADO requests, or commits are part of this module.
All source and artifacts are isolated here; there is no reference to AdoDevProxy or another source checkout.

## Contracts and provenance

- Intended host: the parent's per-user Windows .NET 10 service, not LocalSystem.
- Existing ADO behavior baseline supplied for parity: `20ee732`; not modified or linked.
- Protocol and rendered chart baseline: Substrate `71b1f9ec80848f8fd46f5d5d7e6b84ba36a80966`.
  `src\NativeAdo\Protocol\credprovider.proto` is the pinned Apache-2.0 source, retaining its license header.
- Official Google.Protobuf and gRPC packages generate and serve
  `/substrate.credprovider.v1.CredentialProvider/FetchSecret`.
  Request: `uri`, `actor_spiffe_id`; response: **only** `opaque_bytes`, without expiry.
  Returned bytes are the token itself, without `Bearer `; the egress injection rule supplies that prefix.
- Active data plane is **AGW 1.7.0**, not the legacy Go router.
  No four-second provider timeout, automatic refresh/retry, or missing-header injection is assumed.

## Exact parent integration API

Reference `src\NativeAdo\NativeAdo.csproj` from the parent's Windows `net10.0-windows` project.
Published output contains the generated protocol and dependencies; upstream source is not required.

```csharp
using HostCapabilities.NativeAdo;

// Parent supplies certificates from its owned runtime store and the host token adapter.
using var trust = new GatewayCertificateTrust(
    gatewaySpiffeId: "spiffe://cluster.local/ns/native-ado-canary/sa/native-ado-canary-atenet-egress",
    roots: gatewayIssuerRoots,
    intermediates: gatewayIssuerIntermediates);

builder.WebHost.ConfigureKestrel(k =>
    k.ListenNativeAdo(privateHostAddress, 5443, servingCertificate, trust));

builder.Services.AddNativeAdo(
    new NativeAdoOptions { MaxConcurrency = 4, RequestTimeout = TimeSpan.FromSeconds(15) },
    trust,
    authorize: (actor, uri, ct) => ValueTask.FromResult<CredentialGrant?>(
        actor == approvedExactActorSpiffeId && uri == "ate-secret://windows-wam/canary/ado"
            ? new CredentialGrant(approvedHostIdentity)
            : null),
    acquireToken: async (identity, ct) => await hostTokenDelegate(identity, ct));

await using var app = builder.Build();
app.MapNativeAdo();
await app.RunAsync();
```

Required delegates:

```csharp
public delegate ValueTask<CredentialGrant?> AuthorizeCredentialAsync(
    string actorSpiffeId, string credentialUri, CancellationToken cancellationToken);
public delegate ValueTask<string> AcquireAdoTokenAsync(string identity, CancellationToken cancellationToken);
public sealed record CredentialGrant(string Identity);
```

The policy callback **must explicitly match the exact attested actor/profile and known credential URI**,
returning a host identity only for an approved pair; `null` denies. There is no permissive/default policy.
The URI is an opaque allowlisted credential locator, not a destination URL. Only conservative
`ate-secret://windows-wam/<segments>` syntax is accepted; no percent encoding, traversal, query, or fragment.
Do not derive the WAM account from actor-provided text.

The parent wraps its existing `WamTokenProvider.GetTokenAsync(identity, forceRefresh, ct)` using this delegate,
returning the token string. WAM cache, expiry checks, refresh synchronization, `forceRefresh` decisions,
and explicit sign-in remain host-owned. This library never forces refresh, retries, persists, or logs a token.
Do not replay Git writes after any error.

### Transport, authorization, and resource boundaries

- Dedicated listener: TLS 1.2/1.3, HTTP/2, required client certificate, ten-second handshake timeout.
  Firewall/private routing must expose it only to trusted infrastructure.
- Both TLS and each RPC validate a pinned custom-root chain, validity, client-auth EKU, non-CA leaf, and
  **exactly one matching URI SAN** using platform ASN.1 APIs. CN, DNS SAN, JWT, and forwarded headers cannot
  authenticate the gateway. A trusted actor certificate is still denied.
- Supply the **podidentity issuer** root/intermediate bundle, not the actor CA or servicedns server CA.
  Provision intermediates explicitly: RPC revalidation cannot depend on a TLS handshake's intermediate cache.
  Certificates and trust objects remain host-owned and alive through service shutdown.
- Default validation remains platform X509 with `Online` revocation; AIA downloads are disabled and unknown
  revocation fails closed. For bounded private SVIDs without CRLs, the explicit policy below is the supported
  opt-in; do not configure a bare `NoCheck` override in production.
- `FetchSecret` trusts the verified gateway's actor attestation, then calls the actor/URI policy.
  There is no actor-facing token API, JWT flow, or certificate/header impersonation escape hatch.
- Work admission is immediate, with no waiting queue. Default four concurrent operations, configurable 1–64;
  saturation returns `ResourceExhausted`. The deadline covers policy/token work, defaults to 15 seconds,
  and is configurable up to two minutes. TLS/chain validation precedes this work deadline.
- Cancellation/deadline is propagated to both delegates. A non-cooperative delegate cannot be killed:
  the RPC returns at its deadline but the work keeps its concurrency slot until it ends.
  Late policy success cannot start token acquisition; late token success cannot produce a response.
  Synchronous delegate work also runs behind bounded admission, off the RPC thread.
- `Dispose` closes admission without invalidating active work's capacity accounting.
  Parent stop must cancel RPCs and let cooperative work finish before disposing other host dependencies.
- Receive/send protobuf limits are 8/32 KiB for this service only; token text is capped at 16 KiB and must
  use bearer-token-safe characters. No reflection, debug endpoint, or additional HTTP route is registered.
  Parent owns connection/stream-rate limits and listener readiness; this is not a general DoS perimeter.
- Errors contain fixed details and no delegate exception, trailers, identities, URI, or token.
  The module does not log payloads. Parent must not enable request/response body logging, gRPC payload
  inspectors, or logging of generated protobuf objects (including `ToString()`).

### Explicit private short-lived SPIFFE policy

The constructor now has one appended optional parameter; existing calls keep the original Online path:

```csharp
public GatewayCertificateTrust(
    string gatewaySpiffeId,
    IEnumerable<X509Certificate2> roots,
    IEnumerable<X509Certificate2>? intermediates = null,
    X509RevocationMode revocationMode = X509RevocationMode.Online,
    ShortLivedSpiffeTrustPolicy? shortLivedSpiffePolicy = null);

public sealed record ShortLivedSpiffeTrustPolicy
{
    public TimeSpan MaxLeafLifetime { get; init; } = TimeSpan.FromHours(24);
}
```

Opt-in:

```csharp
using var trust = new GatewayCertificateTrust(
    "spiffe://cluster.local/ns/ate-system/sa/atenet-egress",
    gatewayIssuerRoots,
    gatewayIssuerIntermediates,
    shortLivedSpiffePolicy: new() { MaxLeafLifetime = TimeSpan.FromHours(24) });
```

`MaxLeafLifetime` must be positive and **at most 24 hours**. Supplying this policy together with a separate
non-Online `revocationMode` override is rejected. This mode uses **BouncyCastle.Cryptography 2.6.2 PKIX**
(restored solely from 1ES) instead of Windows chain building. It does not implement signature cryptography.
It disables CRL checking only inside this explicit private-SVID mode, retaining:

- Exactly one matching URI SAN, explicit client-auth EKU, mandatory non-CA basic constraints and ECDSA/RSA
  leaf key type. Ed25519 **issuer** signatures are supported; Ed25519 TLS leaf keys are not accepted here.
- Fresh current-time validity and total leaf-lifetime checks on every `IsTrusted` call, including each RPC.
- PKIX signature, CA/path/key-usage and validity checks anchored only in supplied pinned roots, with all
  intermediates supplied and pinned explicitly. No presented/unpinned intermediate, system trust, AIA fetch
  or global trust-store change is used. Chain depth is bounded to eight intermediates.
- Client-auth-compatible EKUs throughout the selected chain. Wrong issuer/signature, identity, usage,
  overlong or expired/future leaves fail closed.

**PKIX validation is not TLS interoperability.** The actual Pod spec declares ECDSAP256, but local fake
Ed25519-CA/ECDSA-client tests show this machine's Schannel sends `unsupported_certificate` on both TLS 1.2
and 1.3 **before the custom validation callback executes**. Matching supported-issuer controls complete TLS.
The new PKIX mode validates the fake Ed25519 chain correctly but cannot bypass that native handshake
rejection. Direct Windows ingress for the actual issuer remains blocked; see `LIVE-CANARY.md`.

## AGW canary artifact (render only, never apply)

`src\NativeAdo.Canary` is an offline rendered-manifest patcher. It reads YAML on stdin and produces YAML on
stdout only after all checks pass. It rejects missing/ambiguous routes, missing `k8s.io`, conflicts, or a
different pinned AGW image; it is idempotent and has an 8 MiB input bound.
It does **not** call Kubernetes, Helm upgrade, WAM, or ADO.

The fixture `tests\NativeAdo.Tests\Fixtures\helm-egress.yaml` was actually generated with Helm 3.19.0:

```powershell
helm template native-ado-canary <PINNED-SUBSTRATE-CHART-DIRECTORY> `
  --namespace native-ado-canary --show-only templates/atenet-egress.yaml
```

Pinned `charts\substrate\templates\atenet-egress.yaml:84–113` hardcodes `k8s.io`.
Unlike Helm, `cmd\ate-setup\internal\config\config.go:585–705` and
`steps\overlay.go:371–389` already render multiple named providers.
The patch adds a separate `windows-wam` entry to **both internal HTTP and HTTPS** credential-provider lists,
retaining existing `k8s.io`, actor resolution, CONNECT, dynamic destination/TLS validation, and opaque TLS routes.
This registers a provider; it does not authorize an origin or create an actor injection policy.

Example from this directory, after build (PowerShell 7):

```powershell
Get-Content tests\NativeAdo.Tests\Fixtures\helm-egress.yaml -Raw |
  dotnet src\NativeAdo.Canary\bin\Debug\net10.0-windows\NativeAdo.Canary.dll `
    --configmap native-ado-canary-atenet-egress-agentgateway-config `
    --deployment native-ado-canary-atenet-egress `
    --namespace native-ado-canary `
    --endpoint windows-wam.canary.invalid:5443 `
    --root-configmap windows-wam-provider-ca
```

`.invalid` is deliberately unroutable; replace it only in reviewed canary inputs.
No output here is an instruction to apply to the active release.
For a complete Helm render, the tool changes only the named ConfigMap and Deployment; unrelated YAML
documents are retained semantically, though formatting/comments are not preserved.
It is a rendered-manifest transform, not an installed Helm post-renderer wrapper.

Actual inspected mTLS paths:

| Purpose | Existing chart path / new artifact |
|---|---|
| Gateway's provider client certificate **and key** | `/run/podidentity.podcert.ate.dev/credential-bundle.pem` (existing projected mount) |
| Existing `k8s.io` provider server trust | `/run/servicedns.podcert.ate.dev/trust-bundle.pem` (unchanged) |
| Windows provider server trust | New read-only ConfigMap mount `/run/windows-wam-provider-ca/ca.crt` |
| Windows provider expected serving SAN | Explicit `backendTLS.hostname`, equal to endpoint DNS name |

The patch adds that public-CA ConfigMap volume/mount, referencing `--root-configmap`; it does not fabricate a CA,
create a private key, or assume Windows is issued a servicedns certificate.
Operator must provision the **public** CA as `ca.crt` in that ConfigMap in the isolated canary namespace.
Windows's serving certificate needs the configured DNS SAN, server-auth usage, private key accessible to
the signed-in host, and a chain verifiable by AGW. No global trust-store change or certificate bypass is included.
Pin the canary release/namespace, exact gateway SPIFFE ID, DNS/routing/firewall, trust rotation, and new Pod rollout
in the parent's deployment review. Source and fixtures contain no runtime certificates.

### Injection and cache limits

Pinned `docs\kubernetes-credential-provider.md` requires an **already-present placeholder header**.
Missing headers are forwarded unchanged, not magically inserted.
An approved Substrate rule needs `effects.replace_headers` with header `authorization`,
prefix `Bearer `, and `credential_uri: ate-secret://windows-wam/canary/ado`, on the appropriate
HTTP/HTTPS rule. Never attach credentials to plaintext production destinations.
Ordinary KagentEgressPolicy supports Kubernetes Secret / actor JWT, not this external URI; the reviewed
declarative/compiler bridge and exact actor revision are **not implemented** by this provider-registration patch.
Do not work around that by mutating live actor state.

AGW documents a **five-minute successful actor/URI cache**. This patch leaves it unchanged:
the inspected schema provides no proven configurable cache-TTL field, and this prototype invents none.
Policy revocation and token rotation can therefore lag five minutes at egress.
The protocol has no expiry response, no request destination/method/path, and no refresh/invalidation RPC.
The host delegate must guarantee sufficient remaining lifetime for that cache plus transport/skew,
or fail closed. Existing WAM refresh margins alone are not proof of that guarantee.
401 behavior, provider deadlines/retries, cache expiry/isolation, revocation, and certificate rotation on
**AGW 1.7.0** remain live gates; no legacy-router behavior is imported.

## Offline validation

Run from this directory; the SDK installed here is selected by `global.json`.
On this machine use `$env:USERPROFILE\.dotnet\dotnet.exe` if the system `dotnet` resolves to SDK 8.
All package sources and source mapping are restricted to the configured 1ES feed; no public fallback.
Package manifests are local and versioned centrally in `Directory.Packages.props`.

```powershell
dotnet restore NativeAdo.slnx --configfile NuGet.Config
dotnet test NativeAdo.slnx --no-restore
dotnet format NativeAdo.slnx --no-restore --verify-no-changes
dotnet test tests\NativeAdo.Tests --no-restore --filter "FullyQualifiedName~PatchTests|FullyQualifiedName~ClientFixtureTests"
dotnet run --project src\NativeAdo.Canary --no-build -- --help
```

Tests use fake tokens, test-owned ephemeral CA/leaf certificates, real loopback Kestrel mTLS/HTTP2,
and checked-in offline manifests/config. Nothing calls WAM, real ADO, or public feeds.
On Windows, Schannel cannot use `EphemeralKeySet` private keys: test-only PKCS#12 is imported in memory
into transient OS key containers, deleted when their certificate objects are disposed; no certificate
is installed into a trust store and import buffers are zeroed.
The issuer-interoperability test also requires `python` (Python 3.12/OpenSSL, present on this machine).
Its standard-library SSL client isolates server-side Schannel behavior instead of using a Windows client
that fails while constructing its local Ed25519-signed certificate context. Only freshly generated fake
ECDSA client key/cert PEMs are written inside a unique test-owned directory under the module's ignored
build output, deleted deterministically. That probe sends no FetchSecret RPC or token and prints only a
finite TLS classification. It exercises both TLS versions with accepted supported-issuer controls and
the confirmed Ed25519-issuer handshake blocker. No live private key or credential bundle is read.

## Acceptance and explicit blocks

Offline acceptance covers exact gateway identity/chain, actor/URI denial before token acquisition,
opaque fake-token round trip, safe errors/logs, saturation, deadline/cancellation and late completion,
and both-route additive patch preservation/idempotence/schema rejection.
Client fixtures prove inert configuration and Git URL-scoping, **not** package-client header emission.
See [the placeholder and policy-parity matrix](PARITY-MATRIX.md).

**Blocked for cutover:** stock-client headers/challenges, compiler/policy bridge, real AGW/Windows private mTLS
and revocation, cache/expiry/401 semantics, org/feed/path/method/resource restrictions, exact signed-CDN
capabilities, redirects/no bearer leakage, denied cases, successful Git fetch/push and package operations,
and no-write-replay recovery. Native origin allowlisting plus `FetchSecret(uri, actor)` cannot enforce
the existing proxy's full request policy. Keep that proxy until all gates pass; do not broaden policy,
enable public-feed fallback or publishing, or claim this prototype can replace it.

## Primary live-canary preparation

[LIVE-CANARY.md](LIVE-CANARY.md) records read-only primary gateway/issuer inspection, guarded current-config
artifacts, proposed transport, required effects, rollback checks, and the token-safe portable probe.
Nothing was applied or restarted and no real FetchSecret/WAM request was made.
The actual public podidentity root is Ed25519. The opt-in PKIX path validates equivalent fake chains, but
the controlled Schannel handshake test still blocks that issuer before application validation. TLS
interoperability remains an explicit blocker, not a reason to bypass certificate validation.
