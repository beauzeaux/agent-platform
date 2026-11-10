# AdoDevProxy

Windows-hosted, per-user HTTPS proxy for Linux coding-agent containers. Git smart HTTP,
Azure Artifacts npm consumption, and NuGet v3 restore retain their original HTTPS URLs.
MSAL.NET obtains Azure DevOps tokens through the Windows WAM broker and adds them only
to explicitly permitted upstream operations. Containers receive no Entra credentials.

## Build and host startup

Requires Windows 10/11 with WAM, an interactive signed-in user, and .NET 10 SDK.
`NuGet.Config` uses only the configured 1ES EngSys feed. Restore using your existing
host Azure Artifacts credential provider; do not add credentials to this repository.
Azure CLI is not used by the running proxy.

```powershell
dotnet restore AdoDevProxy.slnx
dotnet build AdoDevProxy.slnx -c Release --no-restore
dotnet test tests\AdoDevProxy.Tests\AdoDevProxy.Tests.csproj --no-restore
dotnet format AdoDevProxy.slnx --no-restore --verify-no-changes

.\scripts\run.ps1 init-ca
.\scripts\run.ps1 login
.\scripts\run.ps1 serve
```

The helper also finds the SDK at `%USERPROFILE%\.dotnet\dotnet.exe` when it is not on
PATH. Copy/edit `proxy.example.json` and pass `-Config` for another configuration.
Identity entries map explicit tenants/client IDs to organizations and feeds. The
initial Azure CLI public client ID follows the existing local `wam-imds-broker`
pattern; it is not a secret or a requirement to register a new Entra application.
Tenant policy can still restrict this client or require interactive MFA.

`login` first tries silent WAM acquisition and opens host UI only if necessary.
`login -Interactive` requests explicit sign-in. Requests never open UI; a persistent
401 becomes `503 host_sign_in_required`. Stop the proxy, run `login -Interactive`,
then restart. No browser/device-code/PAT fallback or token-returning API is provided.
Safe GET/HEAD requests have at most one forced refresh/retry; Git POSTs are never
replayed automatically.

Default data endpoint is `127.0.0.1:5480`; token-free health is
`http://127.0.0.1:5481/health`. `doctor` checks configuration/public CA while serving.
State defaults to `%LOCALAPPDATA%\AdoDevProxy`: a current-user ACL, DPAPI-encrypted
root PFX blob, public `root.crt`, and selected account IDs. Access tokens stay in
memory/WAM. A state lock prevents concurrent servers or CA rotation while serving.

## Linux containers on this host

The supported local setup is Docker Engine inside WSL2. A credential-free stdio
relay connects an **internal Docker bridge** to the Windows loopback proxy, avoiding
an otherwise blocked Windows VM-facing port. It has no signing key or WAM endpoint.
The bridge-scoped WSL INPUT rules reject clients arriving from other interfaces.

Install Docker Engine, `socat`, and `iptables` in the selected WSL distribution first.
Base-image/tool bootstrap uses separately approved connectivity, not this dev-plane
proxy. `preview` makes no changes; `status` is read-only, and `check` verifies the
owned rules and bridge-only relay listener.

```powershell
wsl -d Ubuntu-26.04 --user root -- bash /mnt/q/src/tools/ado-dev-proxy/scripts/container-network.sh preview
wsl -d Ubuntu-26.04 --user root -- bash /mnt/q/src/tools/ado-dev-proxy/scripts/container-network.sh setup
wsl -d Ubuntu-26.04 --user root -- docker build -f /mnt/q/src/tools/ado-dev-proxy/containers/Dockerfile -t ado-dev-proxy-agent:local /mnt/q/src/tools/ado-dev-proxy
```

In another host terminal, run the relay in the foreground:

```powershell
wsl -d Ubuntu-26.04 --user root --exec env `
  'ADO_PROXY_DOTNET=C:\Users\nibeauli\.dotnet\dotnet.exe' `
  'ADO_PROXY_APP=Q:\src\tools\ado-dev-proxy\src\AdoDevProxy\bin\Release\net10.0-windows\AdoDevProxy.dll' `
  'ADO_PROXY_CONFIG=Q:\src\tools\ado-dev-proxy\proxy.example.json' `
  bash /mnt/q/src/tools/ado-dev-proxy/scripts/container-network.sh relay
```

Adapt paths, distro, and SDK installation for other hosts. The relay port must match
the configuration (`ADO_PROXY_PORT` defaults to 5480). The setup script prints the
actual bridge gateway; use it, not container localhost.

Example launch **inside WSL**, substituting the printed gateway:

```sh
proxy=http://172.18.0.1:5480
docker run --rm -it --network ado-dev-proxy \
  -e HTTP_PROXY="$proxy" -e HTTPS_PROXY="$proxy" \
  -e http_proxy="$proxy" -e https_proxy="$proxy" \
  -e NO_PROXY=localhost,127.0.0.1 -e no_proxy=localhost,127.0.0.1 \
  -v /mnt/c/Users/nibeauli/AppData/Local/AdoDevProxy/root.crt:/usr/local/share/ca-certificates/ado-dev-proxy.crt:ro \
  ado-dev-proxy-agent:local bash
```

Only mount the **public certificate file**, never the state directory, DPAPI blob,
Windows profile, token cache, Docker socket, or a private key. Optionally set
`ADO_PROXY_CA_SHA256` to the fingerprint printed by `init-ca`/`doctor`.
The example image installs OS trust on startup and sets `NODE_EXTRA_CA_CERTS` for
Node/npm. Its entrypoint requires root to initialize CA trust; run your agent under
an unprivileged user afterward if desired. Do not disable TLS validation.
Git/libcurl and dotnet/NuGet use OS trust; proxy variables are container-scoped.

Use your project's existing canonical Git remotes, `.npmrc`, lockfiles, and
`NuGet.Config`. For projects without registry configuration, explicitly select the
approved 1ES registry; npm's public default is denied. Keep Azure DevOps hosts out of
`NO_PROXY`. Existing client authentication and cookies are ignored by the proxy,
but remove credential remnants from agent images/configuration rather than relying
on stripping alone.

The internal network also blocks ordinary direct internet routing in this example.
Agents needing model/general internet traffic require separately approved networking;
do not turn this proxy into a general tunnel. Proxy variables alone are not universal
egress enforcement, and this is not a sandbox for hostile code.

Docker Desktop is a different network topology: the WSL relay script deliberately
refuses a remote/Desktop daemon. If using Desktop, first prove host reachability,
bind an explicit VM-facing Windows IP, update `allowedClients`, and preview/apply
`scripts\firewall.ps1 -Action Install -LocalAddress <IP> -RemoteAddress <VM CIDR> -WhatIf`
from an elevated host shell. Remove `-WhatIf` only after inspecting the scope.
Do not expose control port 5481 or trust the CA on Windows. NAT can hide individual
container identities; a source-subnet rule is not per-container authentication.

For real-client smoke testing, mount this repository read-only and run
`containers/smoke/run.sh` inside the image. It uses isolated cold caches and checks
byte-identical lockfile/shrinkwrap `ci` runs, a scoped package download, and NuGet
restore. Set `ADO_PROXY_GIT_URL` for clone/fetch/pull; set `ADO_PROXY_GIT_PUSH=1` only
for a repository authorizing disposable branch creation/deletion. The script pushes
only a unique scratch branch, deletes it, and verifies cleanup; it never changes an
existing branch or the original working copy.

## Policy and limitations

Only HTTPS/443, matched CONNECT/SNI/Host authorities, configured Azure DevOps
organizations, and configured feeds are allowed. Git discovery/upload-pack/receive-pack
are supported; Azure DevOps REST administration, package publishing, public registries,
plain HTTP origins, IP targets, WebSocket upgrades, HTTP/2/3, and opaque tunnel fallback
are denied. SSH Git, Git LFS, certificate-pinned clients, and legacy/optimized Git
URL variants are not current acceptance targets.

Conflicting/invalid body lengths are rejected before authentication/origin creation.
The engine performs RFC 9112 normalization of otherwise valid
`Transfer-Encoding: chunked` plus `Content-Length`, removing the latter before
forwarding; it does not relay ambiguous framing to the origin.

NuGet service-index GUID resource aliases are discovered and validated at startup.
Feed metadata and artifact bodies are not rewritten. npm audit's two read-oriented
POST routes are permitted; upstream errors are preserved, never fabricated as a clean
audit result. Other feeds/identities require explicit configuration.

Signed content redirects require an exact approved `downloadHosts` entry and a
short-lived exact URL capability learned from an approved response. Content hosts
never receive a bearer token. Unknown download shards fail closed; inspect the
sanitized denied hostname and verify the feed's actual redirect before adding it.
Previously captured standalone signed CDN URLs are not authorized after restart.
Logs contain operation/status and denied hostnames, not tokens, bodies, or signed
queries. The example lists the five exact content shards observed from approved
feed redirects, not a wildcard destination allowance.

Reachable trusted local callers share the Windows user's permitted dev-plane
capability, including Git push. This is not caller authentication, DLP, or a permission
reduction layer. Compromise of the signing key is broader than the application's
allowlist: the synthetic CA can impersonate other TLS services in containers trusting it.

## Validated coverage

The Release build has zero warnings/errors; all 37 tests and the formatter pass.
Real Ubuntu-container acceptance passed canonical Git clone/fetch/pull and a disposable
branch push/delete, cold npm install/ci/shrinkwrap/scoped download with unchanged
lockfiles, and cold NuGet restore. Actual CA rotation rejected old trust and accepted
new trust. Scoped network rollback/recreation and unapproved-container rejection passed.

Live coverage is MSASG/EngSys through Docker Engine in WSL2, not Docker Desktop.
Multiple identities/organizations have fixture coverage. Live MFA recovery,
submodules, and every NuGet metadata resource were not separately forced.

## Rotation, startup, and rollback

Stop the proxy before `rotate-ca`. The root lasts one year; leaves last two days.
Rotation writes a new protected root/public certificate. Recreate or re-run trust
installation in selected containers and remove the old owned trust entry. Recreate
file-bind-mounted containers after atomic CA replacement to avoid retaining the old
mounted file. Old trust must fail rather than allowing passthrough. Compare the new
SHA256 fingerprint.
Never install the synthetic root into the Windows host's trust store.

Optional current-user logon startup, after building Release and signing in:

```powershell
.\scripts\startup.ps1 -Action Install -WhatIf
.\scripts\startup.ps1 -Action Install
.\scripts\startup.ps1 -Action Remove
```

This installs only a current-user Startup shortcut, not a privileged Windows service.
It does not automatically start the WSL relay or open sign-in UI.

For rollback, stop your relay/proxy processes and owned containers first, then:

```powershell
wsl -d Ubuntu-26.04 --user root -- bash /mnt/q/src/tools/ado-dev-proxy/scripts/container-network.sh remove
```

The script removes only the labeled owned network and its scoped INPUT chain. Remove
an explicitly installed Windows rule with `scripts\firewall.ps1 -Action Remove`
using the same address/subnet/port arguments. Delete the public CA trust entry only
from opted-in persistent containers, then run `update-ca-certificates`. Disposable
containers need only be removed. State may be retained for restart; remove its exact
owned files only when intentionally retiring the CA/account selection.
No shared URLs, lockfiles, Windows proxy settings, or host root trust are changed.
