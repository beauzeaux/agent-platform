# Primary native AGW canary wiring — prepared, not applied

Read-only inspection: 2026-11-03. No apply, patch, restart, port forward, WAM acquisition, or private
certificate/key read was performed. All generated runtime material is under ignored `artifacts\live-canary`.
The existing ADO proxy remains authoritative; this wiring does not establish policy parity.

## Confirmed active resources

| Item | Observed value |
|---|---|
| Gateway Deployment / namespace | `atenet-egress` / `ate-system` |
| Deployment UID / generation / captured resourceVersion | `406f4c92-67d6-490c-baa2-ef9740b4c277` / `7` / `259636` |
| Running Pod / UID | `atenet-egress-6478dc46b7-mnlh5` / `0350100d-c4fd-41a8-be2d-f922c043fe02` |
| ServiceAccount | `atenet-egress` |
| Required exact gateway URI SAN | `spiffe://cluster.local/ns/ate-system/sa/atenet-egress` |
| Active ConfigMap / captured resourceVersion | `atenet-egress-agentgateway-config` / `250034` |
| Gateway image | `cr.agentgateway.dev/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b` |

Identity is corroborated by the existing `k8s-credential-provider` Deployment's exact
`--injector-identity=spiffe://cluster.local/ns/ate-system/sa/atenet-egress` argument, its
`--client-ca-file=/run/podidentity.podcert.ate.dev/trust-bundle.pem`, and the gateway's actual ServiceAccount
and podidentity signer projection. No client leaf certificate was read to obtain this evidence.

The active HTTPS dynamic backend uses `/run/proxy-access-ca/ca-bundle.pem`, supplied by ConfigMap
`agent-proxies-egress-roots` through volume `proxy-access-ca`. **Preserve it.**
A fresh stock Helm render would use a different backend trust path. The current-config renderer operates
on the captured active ConfigMap, not that stock render.

## Actual public client issuer

The gateway podidentity projection selects:

```yaml
podCertificate:
  signerName: podidentity.podcert.ate.dev/identity
  keyType: ECDSAP256
  credentialBundlePath: credential-bundle.pem
clusterTrustBundle:
  signerName: podidentity.podcert.ate.dev/identity
  labelSelector:
    matchLabels:
      podcert.ate.dev/canarying: live
  path: trust-bundle.pem
```

Public trust anchor:

- ClusterTrustBundle: `podidentity.podcert.ate.dev:identity:primary-bundle`, resourceVersion `153279`.
- Subject/issuer: `CN=YZFVWBWD5ZWBYM4CXY2XR7XED5`.
- SHA-256: `1A603736D718C0B6FDF900126F15EDC0E7D6760908A8FCFC89E5BAD796337667`.
- Public key and signature OID: `1.3.101.112` (**Ed25519**).
- Validity: 2026-10-31 13:45:56 UTC through 2027-10-31 13:45:56 UTC.
- Public PEM: `artifacts\live-canary\podidentity-public-roots.pem`.

**Trust blocker:** certificate-only loading succeeds, but a custom-root, offline **root-only** chain build
on this Windows platform throws a cryptographic chain-building error. This was an offline diagnostic
using `NoCheck` for a public trust anchor only, not a production revocation-policy change.
Actual gateway-leaf verification was not attempted or proven. A subsequent **Pod-spec-only** query confirms
the running gateway's projected signer is `podidentity.podcert.ate.dev/identity` and leaf key type is
**ECDSAP256**; no leaf or private bundle was extracted.

The explicit `ShortLivedSpiffeTrustPolicy` now validates pinned private SVID chains without CRLs using
standard BouncyCastle PKIX, with exact identity/EKU/non-CA/current-validity checks and a maximum leaf lifetime
hard-capped at 24 hours. The default Online/platform validation path remains unchanged. Fake Ed25519
issuer/ECDSA client certificates pass PKIX, while wrong signatures/issuer/identity, missing or wrong EKU,
CA leaves, overlong/expired/future leaves and unpinned intermediates are rejected.

**Remaining handshake blocker, not merely a root-chain inference:** a bounded local OpenSSL client test
using generated fake certificates confirms that this Windows Schannel server returns
`unsupported_certificate` for an Ed25519-signed ECDSA client on **both TLS 1.2 and 1.3, before the custom
certificate-validation callback executes**. Supported ECDSA-issuer controls complete authenticated TLS
and HTTP/2 setup with the same test harness. No FetchSecret/WAM request is sent by that TLS test.
A Windows .NET test client separately fails while constructing its local Ed25519-signed certificate
context; the OpenSSL control avoids mistaking that client-side problem for server evidence.

Therefore the PKIX implementation does **not** make direct AGW-to-Schannel ingress deployment-ready.
Do not ignore native handshake errors, substitute an unrelated issuer, relax EKUs/lifetimes, or extract
the gateway private key. A standard **Linux TLS-terminating bridge** is the recommended bounded next
design if this Windows crypto stack cannot support the issuer: verify the original SVID with its pinned
issuer and exact SPIFFE constraints there, then use a separately authenticated, pinned and encrypted
backend hop to Windows with an explicitly authorized infrastructure identity. An L4 byte forwarder alone
cannot fix this; forwarded identity headers are not an authentication substitute. That bridge requires
parent security/integration review and is **not implemented or deployed** here.

The public root contains no AIA/CRL-distribution-point extension. This does not establish what the
uninspected leaf contains; leaf revocation behavior remains a separate gate.

## Proposed transport and server certificate

```text
AGW provider target: windows-wam.proxy-access.svc.cluster.local:5443
  -> selectorless Service / EndpointSlice: 10.82.0.2:15443
  -> parent's primary WSL L4 forward: Windows 172.24.160.1:5443
```

Server DNS SAN: **`windows-wam.proxy-access.svc.cluster.local`**, with server-auth EKU and a private key
owned by the parent host. `backendTLS.hostname` is pinned to that DNS name, independent of the L4 IPs.
The transport manifest is `canary\transport.proposed.yaml`; service and EndpointSlice did not exist
at inspection. It includes no selector and no TLS termination. Parent owns forwarding, firewall checks,
endpoint readiness, host startup, and all later deployment actions. None was performed here.

## Exact local artifacts

Refresh/read/render only, from this module:

```powershell
.\scripts\Capture-ActiveCanary.ps1
dotnet run --project src\NativeAdo.Canary --no-build -- `
  --prepare-live artifacts\live-canary\active-snapshot.json artifacts\live-canary\prepared
```

The capture script performs only fixed `kubectl get` operations. It reads ConfigMaps, Deployment specs,
and public ClusterTrustBundles, never Secrets or mounted credential bundles. Runtime snapshots and
public roots are ignored by git, not intended for source control.

Generated files:

| File under `artifacts\live-canary\prepared` | Purpose |
|---|---|
| `provider-configmap.json` | **New** ConfigMap `atenet-egress-agentgateway-config-windows-wam-canary` in `ate-system`, copying current config and adding the provider on both HTTP/HTTPS routes. Original ConfigMap remains untouched. |
| `deployment.forward.patch.json` | JSON Patch with exact Deployment UID/resourceVersion tests, adds root volume/mount and changes only the config-volume ConfigMap reference. |
| `deployment.rollback.patch.json` | UID and exact expected volume/mount array tests; restores the original ConfigMap reference and arrays, including proxy-access CA wiring. Rejects concurrent array changes. |

Other source artifacts:

- `canary\provider-root-configmap.template.yaml`: ConfigMap **`windows-wam-provider-ca`** in **`ate-system`**,
  key **`ca.crt`**. Parent must replace the deliberately invalid placeholder with its **public serving CA PEM**.
  The public podidentity root authenticates gateway clients on Windows; it is **not** automatically the
  Windows serving CA. A valid serving CA was not supplied, so this template is deliberately **not deployable yet**.
- New gateway read-only root mount: `/run/windows-wam-provider-ca`, file `ca.crt`.
- Existing provider client cert **and key path references**, unchanged:
  `/run/podidentity.podcert.ate.dev/credential-bundle.pem`. Those files were **not read or copied**.
- Existing `k8s.io` target stays `k8s-credential-provider.ate-system.svc:50051`, with its existing
  `/run/servicedns.podcert.ate.dev/trust-bundle.pem` server trust.
- New provider target is `windows-wam.proxy-access.svc.cluster.local:5443`,
  `uriAuthority: windows-wam`, `backendTLS.hostname: windows-wam.proxy-access.svc.cluster.local`,
  and `backendTLS.root: /run/windows-wam-provider-ca/ca.crt`.

No chart/schema block prevents **registration**: the active schema matches the existing additive renderer.
The chart still hardcodes `k8s.io`; this local guarded patch bridges that deployment gap without modifying
the upstream checkout. A later Helm reconciliation can overwrite this canary Deployment reference;
parent must coordinate the release owner rather than assuming permanent Helm integration.

### Effects needed — separate, not an actor-policy update

`canary\replace-headers.effects.json` is the exact protobuf field-name form of `HttpRuleEffects`.
Attach it only to an already approved canary HTTPS rule at `rules[].https.effects`:

```json
{
  "replace_headers": [{
    "header": "authorization",
    "prefix": "Bearer ",
    "credential_uri": "ate-secret://windows-wam/canary/ado"
  }]
}
```

Source contract: pinned `api\ateapi\v1\ateapi.proto:647–845`.
The corresponding `HttpsRule` uses `hostnames` and `ports: {"numbers":[443]}`. This effect is not a
Kubernetes resource and not a grant. Do not replace existing effects/rules blindly or broaden destinations.
Ordinary KagentEgressPolicy cannot express this external URI; parent still needs the reviewed native
policy/compiler bridge and exact actor/profile grant. There is no actor ID assumed or silently approved here.
Grant only the approved `(actor SPIFFE ID, ate-secret://windows-wam/canary/ado)` pair at the host.
An **existing** Authorization placeholder is required; absent headers are not injected.
Do not add production plaintext-HTTP bearer injection. No signed-CDN wildcard, public feed, or method/path
relaxation is included. This alone cannot replicate the old ADO proxy's policy.

## Minimal token-safe gRPC probe

Project: `src\NativeAdo.Probe\NativeAdo.Probe.csproj` (`net10.0`, portable client only).
It links the pinned proto locally, uses official gRPC packages, makes one FetchSecret RPC with a ten-second
deadline, disables redirects, and validates server DNS, custom-root chain, server usage and online revocation.
It never prints response bytes, exception messages, trailers, identities, or certificate contents.
The only result line is one of:

```text
success=true length=123 status=OK
success=false length=0 status=PermissionDenied
```

Other failures are finite classified statuses. Exit codes: `0` success, `1` failure.
No actual endpoint request was made while preparing this artifact.

For **later parent-approved execution in trusted gateway infrastructure only**, not inside an actor:

```text
dotnet NativeAdo.Probe.dll
  --endpoint https://windows-wam.proxy-access.svc.cluster.local:5443
  --client-bundle /run/podidentity.podcert.ate.dev/credential-bundle.pem
  --server-ca /run/windows-wam-provider-ca/ca.crt
  --actor <APPROVED_EXACT_ACTOR_SPIFFE_ID>
  --uri ate-secret://windows-wam/canary/ado
```

Run as one command; paths above are the **existing trusted caller's local mounts**, never exported
credentials. AGW itself is not assumed to contain a .NET runtime or shell; preparing a trusted diagnostic
runtime is parent-owned and was not done here. Do not create an actor or hand it the gateway ServiceAccount
or certificate to run this probe. A successful real probe can invoke the host's WAM delegate, so do not
execute until parent explicitly opens that gate. An intentionally denied actor/URI can first validate
the authenticated transport and return `PermissionDenied` without token acquisition.

Optional later portable publish, still using only the configured 1ES feed:

```powershell
dotnet publish src\NativeAdo.Probe -c Release -r linux-x64 --self-contained true `
  -o artifacts\live-canary\probe-linux-x64
```

## Parent preconditions and rollback checks

Before any later change:

1. Re-read Deployment UID/resourceVersion and original ConfigMap UID/resourceVersion/data hash. Compare to
   `public-evidence.json` and the generated ConfigMap source annotations. Regenerate on **any** drift;
   never remove JSON Patch tests to make a stale patch apply.
2. Resolve the confirmed Schannel issuer handshake blocker; prove actual gateway mTLS and the selected
   explicit private-SVID or default Online trust policy.
   Supply the public Windows serving CA, matching DNS SAN, explicit approved actor/URI grant and host expiry margin.
3. Independently validate the parent's L4 path and selectorless Service/EndpointSlice. No provider readiness
   or forwarding readiness is inferred from this rendering.
4. Ensure the original ConfigMap remains unchanged and present; coordinate Helm ownership/reconciliation.
   Establish root ConfigMap before a deployment could reference it. This step list is **not authorization
   to apply** the prepared files.

After a future approved canary, compare Pod image/ServiceAccount, both providers, proxy-access root path,
actor resolution, HTTPS interception, TLS passthrough, and existing `k8s.io` behavior. Use only sanitized
probe status/length and metadata. The five-minute actor/URI cache and missing-placeholder behavior remain
AGW-version-specific gates.

For rollback, stop new canary work and restore actor canary effects/grants through the same reviewed owner.
The generated rollback patch restores the baseline config reference and original volume/mount arrays only
if their expected canary state still matches. If it rejects, stop and re-inspect; do not clobber another
owner's changes. Parent must verify rollout/readiness, new Pods using `atenet-egress-agentgateway-config`,
baseline `k8s.io`, old proxy operation, and absence of native WAM requests before removing canary-only
ConfigMaps/Service/EndpointSlice/forwarder. Do not delete shared proxy roots or existing proxy resources.
Cached credentials may persist in surviving AGW processes for five minutes; config rollback is not evidence
of immediate credential revocation.
