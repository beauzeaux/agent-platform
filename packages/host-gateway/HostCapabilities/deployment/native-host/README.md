# Optional native host frontend

This adds a TLS frontend to the **existing** WSL `kagent-proxy-access` bridge process.
It is disabled when `native_frontend` is absent or `enabled` is false. No new daemon,
RAM lease, token-vending endpoint, model adapter, or Kubernetes proxy pod is created.
The existing model/ADO/Agency mTLS listeners and their certificate/key/CA inputs remain unchanged.

`native-host.yaml` defines only an owned ClusterIP Service and manual EndpointSlice:

```text
https://host-capabilities.proxy-access.svc.cluster.local:8445
  -> primary WSL 10.82.0.2:15445
  -> configured Windows model host:4141 or existing Windows Agency stdio relay

https://host-capabilities.proxy-access.svc.cluster.local:8446
  -> primary WSL 10.82.0.2:15445
  -> Windows HostCapabilities loopback:15442 managed-identity endpoint
```

Only GET `/v1/models`, POST `/v1/responses`, POST `/agency/mcp`, DELETE `/agency/mcp`,
POST `/agency/policy`, POST `/agency/plugins`, and GET
`/metadata/identity/oauth2/token` are admitted. The token route requires exactly
`api-version=2019-08-01` and Azure DevOps resource
`499b84ac-1321-427f-aa17-267ca6975798`. Other query strings, absolute proxy request
targets, CONNECT, alternate origins, cookies, identity selectors, and caller-supplied
Agency identity/bridge-key headers are never forwarded. Model request/response entity
bytes are relayed without JSON/SSE translation, inference retries, or protocol fallback.
Agency and managed identity use the existing per-RPC Windows Python interop path to
fixed loopback port 15442.

The native TLS listener intentionally does **not** request client certificates.
Port 8445 forwards one syntactically valid `Authorization` bearer; port 8446 forwards
one syntactically valid `X-IDENTITY-HEADER` bearer. Windows, not this relay,
must verify the actual ES256/P-256 signature, issuer `https://idp.ate-system.svc`,
audience `https://host-capabilities.proxy-access.svc/session`, expiry, actor identity,
and authoritative broker registry/profile in `factory-executions`.
Unverified JWT claims and `X-Agency-*` input headers never authorize requests here.
The native Windows relay path does not read or forward the legacy bridge key.

Parent-owned cutover, in order:

1. Finish Windows native JWT/profile admission and its rejection tests on both capabilities.
2. Issue a **new** leaf/key under the existing protected proxy CA, with serverAuth and SAN
   `host-capabilities.proxy-access.svc.cluster.local`. Keep existing trust and keys unchanged.
   The existing Substrate origin-root overlay already trusts this proxy CA.
3. Review/copy the tested `proxy-access/host_bridge.py` into protected WSL state and record
   both hashes. Validate the existing config extension in a root WSL shell:

   ```text
   cd /mnt/q/src/tools/HostCapabilities/deployment/native-host
   python3 configure_native_host.py --certificate /home/nibeauli/kagent-lab/proxy-access/certificates/native-host.crt --key /home/nibeauli/kagent-lab/proxy-access/certificates/native-host.key --enable --dry-run
   ```

   Remove `--dry-run` only for an intentional config change. The helper is fixed to the
   existing root-owned private `proxy-access/bridge.json`, replaces only its owned
   `native_frontend` entry, validates the new leaf/key and hostname/expiry, and never
   restarts a service, creates keys, changes a CA, or applies Kubernetes resources.
4. Parent alone restarts the existing bridge and applies the Service/EndpointSlice after
   verifying ownership of any same-named resources. No business-pod restart is required.
5. Use new pinned-revision canary sessions. Configure native model base URL
   `https://host-capabilities.proxy-access.svc.cluster.local:8445/v1` and MCP URL
   `https://host-capabilities.proxy-access.svc.cluster.local:8445/agency/mcp`, with the
   compiled inert credential/actor-JWT rules. Native inferred HTTPS egress is port **8445**,
   not the legacy factory compiler's TLS passthrough port 8444.
6. Prove real origin/server TLS validation, actual Substrate actor JWT replacement, native
   model discovery/Responses and MCP, profile/foreign actor denials, cancellation, and
   recovery without accepted-request replay. Check proxy bypass/routing for these native
   destinations rather than sending them through the legacy package CONNECT adapter.

Node policy/plugin bootstrap can remain on the existing loopback legacy route initially;
do not claim that every lease/adapter dependency has been removed. Reverting only this
owned `native_frontend.enabled` flag disables the frontend on the next parent-owned
bridge restart. Preserve existing config and certificates for rollback.

The credential-injecting ADO frontend is independently controlled by
`legacy_ado_frontend_enabled`. Set it to `false` after managed-identity
acceptance to retain the rollback code/configuration without binding port 15480.

Bounds: at most 8 native connections by default (hard maximum 32), 30 MB model requests,
8 MB Agency requests/results, 128 MB streamed model responses, 60-second request
deadline, 1,800-second model response deadline, and the existing 200-second Agency interop
deadline. Ambiguous content length/transfer encoding and trailers fail closed.
Responses interrupted after headers terminate the connection, not a second request.

Focused, offline tests (no credentials or live inference):

```text
proxy-access: python -m unittest -v test_host_bridge.BridgeTests test_native_host_bridge
agency-broker: python -m unittest -v test_windows_relay
deployment/native-host: python -m unittest -v test_native_host_config
```
