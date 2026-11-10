# Offline placeholders and remaining policy parity

These are inert **fixture candidates**, not approved actor deployment configuration.
Only the old proxy at baseline `20ee732` remains accepted for actual ADO access.
No real token belongs in a client config, argument, environment projection, actor file, or Kubernetes Secret.
The literal `native-ado-placeholder` has no authentication value.

## Client configuration fixtures

All paths below are under `tests\NativeAdo.Tests\Fixtures\clients`.

| Client / fixture | What is established offline | What is not established; required gate |
|---|---|---|
| Git / `git.config` | Stock `git config --file … --get-urlmatch` selects the documented `http.<url>.extraHeader` only for `https://dev.azure.com/msasg/`; credentials helper is cleared and redirects disabled. Canonical Git URLs are not rewritten. | Actual Git fetch/clone **and push** must carry the inert Authorization header through AGW on HTTPS. Test discovery/pack/write requests, allowed vs denied org/repo/path/method, challenge handling, CA trust and no write replay. Config parsing is not wire proof. |
| npm / `.npmrc` | Explicit 1ES registry only; inert `_authToken` is scoped to the approved feed registry/parent paths. No public-registry fallback. | Pin stock npm version and prove real metadata/tarball/audit placeholder emission, including credential/path scoping. Do not invent `always-auth` or custom-header switches. Lockfile/resolved resource/CDN destinations need their own approved capability checks. |
| NuGet / `NuGet.Config` | `<clear/>` leaves one private 1ES source; username/password are inert placeholders; documented `ValidAuthenticationTypes=basic` is explicit. | Basic credentials do **not** prove preemptive Authorization. Verify first unauthenticated request, 401 challenge, retry, V3 index, registration, flat-container and download behavior with stock NuGet. AGW missing-header behavior can prevent injection. No assumption that service-index credentials propagate to every resource. |

The npm and NuGet tests parse the candidate configurations; they intentionally do not claim header generation.
Git testing proves URL config matching only. **All three wire/header checks remain gates.**
Use isolated client homes, no inherited helpers/credential-provider plugins, no real token on the actor,
and no package install/restore against these fixtures during routine offline tests.

## Behavioral parity / canary fixture matrix

Parent can turn each row into a baseline-vs-canary request fixture after reviewing the exact legacy policy.
No allow/deny outcome is inferred from host/origin membership alone.

| Scenario | Offline evidence here | Required live / parent parity evidence |
|---|---|---|
| Trusted gateway, approved actor, exact credential URI | Real loopback mTLS + generated FetchSecret returns fake bytes. | Actual podidentity chain/SPIFFE, provider DNS/server SAN, private transport, actor attestation and actor revision. |
| Missing/wrong/actor client certificate, CN-only identity, wrong root/EKU/expiry, multiple URI SANs | Denied; no token delegate invocation. Spoofed forwarded certificates/identity do not authenticate. | Cluster actors cannot reach provider directly; rotated/revoked gateway certificates fail as intended. |
| Unknown actor/profile or URI, malformed/cross-provider URI | Denied before token acquisition. | Exact deployed profile/actor grants, session fork/restore boundaries and revocation lag. |
| Provider overload/outage, token or policy exception | Fixed safe statuses, bounded work, no secret logs/trailers. | AGW's actual 1.7.0 timeout/retry behavior; independent host-capability readiness. |
| Client cancellation, provider deadline, delegate ignores cancellation | Tested locally; no late policy-to-token transition or release of capacity while work still runs. | Gateway disconnect/cancellation and shutdown behavior; no unbounded retry load. |
| HTTP + HTTPS provider registration | Both lists patched; existing `k8s.io`/policies/TLS routes retained; CA mount supplied; patch is idempotent. | Isolated canary AGW validates the config and real provider over mTLS. HTTP registration is not permission to send production bearer tokens over HTTP. |
| Placeholder absent / present | Documented AGW requirement only; no fabricated insertion. | On AGW 1.7.0, absent header never obtains/leaks a token; present inert header is replaced only for the approved actor/rule/destination. |
| Cache actor/URI isolation, token refresh/expiry/401 | No new module cache; late work cannot respond. | Five-minute AGW cache isolation and lifetime/skew guarantee; expiry/revocation lag; no assumed FetchSecret expiry field or force-refresh signal. |
| Organization / feed restrictions | Actor+URI allowlist is a credential grant, not request-level policy. | Same accepted/rejected organizations and feeds as old proxy. Other paths on the same origin must remain denied. |
| Git path / HTTP method / push | No Git network or write attempted, no retries implemented. | Successful canonical fetch/push plus denied paths/methods; never blindly retry an accepted/uncertain write. |
| npm metadata / tarballs / audit | Inert private registry configuration parsed. | Each baseline-approved resource/method, audit behavior, feed scoping and lockfile URLs; deny publishing and public fallback. |
| NuGet V3 resources / package restore | Inert single-source credentials parsed. | Enforce baseline service-index/resource rules and complete approved restore. An origin allowlist does not constrain resource URLs. |
| Signed CDN links / redirects / bearer stripping | No native replacement is claimed. | Match exact signed capability including path/query/expiry, forbid broad shard grants, strip auth on content hosts/cross-origin redirects, and deny unsigned or substituted resources. |
| Compiler external credential URI | Not implemented: ordinary KagentEgressPolicy only models K8sSecret / actor JWT. | Reviewed declarative/compiler bridge to `effects.replace_headers`; no unmanaged live-actor edits. |

Acceptance means **prototype only** until these gates pass. If parity needs a larger custom policy engine than
the existing proxy, retaining the old module is an acceptable outcome. Short-lived tokens in authenticated trusted
egress are accepted; tokens available to actors and weaker method/path/feed/CDN policy are not.
