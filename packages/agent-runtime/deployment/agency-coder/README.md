# Deploy AgencyCoder from outside the cluster

This installs **workload configuration**, not another Kagent controller.
Image inheritance is `AgencyHarness -> AgencyCoder -> engsys/Audit`.
Each repository owns where its derived recipe lives, image hosting and CI/CD,
including any Helm chart. The examples here do not prescribe a repo convention.
Use the shared namespace-scoped controller/UI. The installed contract is
`api.kagent.dev/v1alpha3`: `Agent -> Harness + AgentTemplate -> ModelConfig`.
AgencyCoder is **not** an A2A server: `/app/harness` is the native Go A2A
bridge and starts `dotnet /app/agency-coder/AgencyCoder.dll` on loopback.
Do not replace the shared controller, worker pool, database, registry or CRDs.

## Prerequisites

- An operator-provided kubeconfig/context, `kubectl`, an OCI client, and registry
  push/pull access. Run from any workstation/CI runner; neither WSL, root,
  cluster-local build directories nor the lab checkout is required.
- A compatible Kagent installation, enrolled workload namespace, explicit
  controller watch/RBAC scope, runtime space/access policy, namespace-local
  worker pool, snapshot location and registry reachability from every worker.
  Namespace creation alone does not provision these substrate dependencies.
- A namespace-local **Responses** `ModelConfig` with projected credentials,
  strict TLS and admitted egress. Preserve existing HTTP MCP tools/approvals
  by adding them to the template before applying. Chat, SSE, native memory
  and remote archive export are unsupported: do not remove such settings to
  make an incompatible migration appear healthy.
- Host-prepared Agency policy/plugins endpoints and their identity enrollment.
  They must accept the runtime's projected credential, not an operator token.
  The approved WAM-backed IMDS endpoint, Git Credential Manager, Azure Artifacts
  Credential Provider and pinned Agency executable are required for coding. A
  repository Agency file is optional: native discovery
  uses the selected `workingDirectory` and its ancestor TOML/YAML layers, while
  the host still enforces base policy when no file exists.

## Required identity enrollment

A model gateway key is **not** an Agency actor credential. Use `model.yaml`
with native `actorJwt` injection for the shared host origin, not `apiKeySecret`.
Add `MODEL_ID`, `MODEL_BASE_URL` (ending `/v1`) and `ACTOR_TOKEN_AUDIENCE`
to the parameter map below; the audience must exactly match the host validator.
Native egress replaces the placeholder with a short-lived signed actor JWT;
never obtain or copy a host gateway key, signing key or inference token.
The model and Agency endpoints must share that admitted origin; a different
Agency origin needs its own explicit native credential binding, not a copied key.

On the existing HostCapabilities host, preserve the current `actorJwt.space`,
issuer/audience/JWKS/profile settings and explicitly add this workload namespace
to `actorJwt.additionalSpaces` (maximum 16 exact identifiers, no wildcards).
Deploy that host configuration with its normal validated, idle/drained lifecycle;
do not overwrite a running payload. Registration remains mandatory: permitting
a space does not authorize an unregistered actor or caller-selected profile.
The native host frontend must also have a valid, hostname-matching leaf chained
to the existing trusted CA. Use the existing certificate provisioner to renew
an expired owned leaf; preserve CA/key/SAN ownership and never bypass validation.

For **each fresh native session**, create it through the authenticated Kagent
API/UI, then obtain the actual substrate actor UID before sending any coding task:

```powershell
$Actor = kubectl-ate --kubeconfig $Kubeconfig --context $KubeContext --preview `
  get actor "session-$SessionId" -a $Namespace -o json | ConvertFrom-Json
if ($Actor.metadata.atespace -ne $Namespace -or
    $Actor.metadata.name -ne "session-$SessionId") { throw 'Identity mismatch' }
$Subject = "actor/$Namespace/session-$SessionId"
```

The trusted host operator invokes the **existing installed Agency registrar**
(`$Registrar`, the host's approved `configure.py` artifact), not an API exposed
to the actor:

```powershell
python $Registrar --actor-uid $Actor.metadata.uid --subject $Subject `
  --profile $ApprovedProfile
```

Select an already approved profile, for example the existing scoped contributor
profile for a coding workload; do not invent or broaden tool permissions. The
registrar atomically updates the protected UID/subject/profile registry. It does
not send its bridge credential to the actor or cluster. Revoked UIDs cannot be
silently re-enrolled. The host checks both signed namespace/name claims and
the current exact registry binding on every request.

Git and NuGet authenticate through their official managed-identity paths. The
Harness points MSAL clients at the WAM-backed IMDS endpoint, configures GCM for
Azure Repos managed identity, disables interactive prompts and persistent
credential stores, and disables both Azure Artifacts provider disk caches.
The retained `/opt/proxy/client.js` code is not started and no HTTP proxy
variables are set. Reconnecting that transport is rollback work requiring an
explicit deployment change, not an automatic fallback.

After terminal cleanup, revoke only that session's binding with
`python $Registrar --actor-uid $Actor.metadata.uid --revoke`, update the public
UID projection if used, and delete the session through native lifecycle APIs.
Do not enroll a golden/template actor in place of a session actor, reuse an old
session identity, or make anonymous policy access permissive.

## Build without putting feed credentials into Docker

Use `Containerfile` build recipes and normal OCI image tooling. First build the
shared [Agency harness base](../../src/AgencyHarness/README.md) from approved
native bridge, Agency, Copilot and transport release artifacts:

```powershell
$Registry = 'registry.example.org/team'
$BuildId = '<unique-build-id>'
$Base = "$Registry/agency-harness:$BuildId"
docker build -f src\AgencyHarness\Containerfile -t $Base $RuntimeContext
docker push $Base
```

`$RuntimeContext` has only the documented executable inputs, no repository,
deployment certificates or credentials. The new base does not inherit the
factory coder/controller image. It supplies .NET 10 SDK, Node 24/npm and Git;
both initial coding profiles use that common toolchain.

Record the **registry manifest digest** reported by the push, not the Docker
image/config ID, and use that digest as `$BaseImage` below.

Publish the coding adapter using the pinned .NET 10 SDK and repository
`NuGet.Config`. Authenticate
the existing 1ES feeds using the approved credential provider (or CI
`NuGetAuthenticate@1` with `EngSysPublisher`). Never copy NuGet credential files,
tokens or the repository root into an image context. From the HostCapabilities
root, in PowerShell:

```powershell
dotnet test tests\AgencyCoder.Tests\AgencyCoder.Tests.csproj
$Context = Join-Path $PWD (".artifacts\agency-oci-" + [guid]::NewGuid())
dotnet publish src\AgencyCoder\AgencyCoder.csproj -c Release -r linux-x64 `
  --self-contained false -p:UseAppHost=false -o "$Context\publish"
$BaseImage = "$Registry/agency-harness@sha256:<published-base-manifest-digest>"
$Coder = "$Registry/agency-coder:$BuildId"
docker build --build-arg "AGENCY_HARNESS_IMAGE=$BaseImage" `
  -f src\AgencyCoder\Containerfile -t $Coder $Context
docker push $Coder
```

The coding layer selects the .NET adapter through `AGENCY_AGENT_EXECUTABLE`
and `AGENCY_AGENT_ENTRYPOINT`; the bridge still owns A2A, session identity,
admission, cancellation and trace propagation. Its release binary must support
those variables. Old `AGENCY_CODER_*` variables remain compatibility inputs for
previously deployed sessions; they are not needed by these images.

Build the repository-specific layers using the coding image's pushed digest:

```powershell
$CoderImage = "$Registry/agency-coder@sha256:<published-coder-manifest-digest>"
foreach ($Profile in @('engsys', 'audit')) {
  docker build --build-arg "AGENCY_CODER_IMAGE=$CoderImage" `
    -f "deployment\agency-coder\$Profile\Containerfile" `
    -t "$Registry/$Profile-coder:$BuildId" "deployment\agency-coder\$Profile"
  docker push "$Registry/$Profile-coder:$BuildId"
}
```

Only the derived images choose a repository; no checkout is baked into them.
Add extra tools in the relevant derived Containerfile, not runtime switches in
the common harness. Both initial layers have .NET 10 SDK and Node 24/npm.
engsys's `global.json` requests 10.0.111 with `latestFeature`; Audit's main
at `5a47276c3eff412327a706428cad3158f43ce82c` requests 10.0.114 with
`latestFeature`, and its SPA uses npm/Vite. The tested SDK 10.0.403 satisfies
both SDK selectors. This does not claim that Windows-only engsys workloads
or either repository's full builds have been tested in these Linux images.

The corrected source supports repositories with no Agency file, including Audit
main at that commit. That source-only fix is not a deployment or authorization
grant, and the live baseline image remains unchanged. Building an Audit image
does not grant Audit permissions or prove a coding/PR task works. Do not copy
engsys plugin declarations into Audit or skip policy checks. Audit remains
unvalidated pending its own deployment, host authorization, model and PR tests.

Deploy the selected profile as `registry/repository@sha256:...`. Use matching
RID/base/bridge artifacts for ARM64. Runtime keys, trust material and actor
identities are injected by the existing platform, never baked into these
images. Keep private build contexts only under the owning artifact-retention
policy.

## Render, validate, apply

The template deliberately references an existing ModelConfig rather than
creating credentials or inventing a provider. Set these deployment parameters:

```powershell
$Kubeconfig = 'C:\operator\cluster.kubeconfig'
$KubeContext = 'approved-context'
$Namespace = 'coding'
$Parameters = @{
  NAMESPACE = $Namespace; AGENT_NAME = 'coder'; WORKER_POOL = 'kagent-default'
  HARNESS_IMAGE = "$Registry/audit-coder@sha256:<published-manifest-digest>"
  SNAPSHOT_LOCATION = 'gs://approved-snapshots/coding/agency-coder/'
  AGENCY_BASE_URL = 'https://approved-agency.example.org'
  MODEL_CONFIG_NAME = 'coding-responses'
  MODEL_ID = 'mai-code-2'
  MODEL_BASE_URL = 'https://approved-agency.example.org/v1'
  ACTOR_TOKEN_AUDIENCE = 'https://approved-agency.example.org/session'
}
$Manifest = Get-Content deployment\agency-coder\native.yaml -Raw
$ModelManifest = Get-Content deployment\agency-coder\model.yaml -Raw
foreach ($Key in $Parameters.Keys) {
  $Manifest = $Manifest.Replace('${' + $Key + '}', $Parameters[$Key])
  $ModelManifest = $ModelManifest.Replace('${' + $Key + '}', $Parameters[$Key])
}
if ($Manifest.Contains('${') -or $ModelManifest.Contains('${')) {
  throw 'Unresolved deployment parameter'
}
$K = @('--kubeconfig', $Kubeconfig, '--context', $KubeContext, '-n', $Namespace)
$ModelManifest | kubectl @K apply --dry-run=server -f -
$Manifest | kubectl @K apply --dry-run=server -f -
```

Parameter values must be trusted operator configuration without YAML
metacharacters/newlines; inspect the rendered manifest before applying.
`HARNESS_IMAGE` must select the desired derived repository image (for example
`audit-coder`), not the repository-neutral coding layer. `AGENCY_BASE_URL` is
the port-8445 model/Agency origin. `AGENCY_IMDS_ORIGIN` is the separate
port-8446 managed-identity origin. The companion `EgressPolicy` injects a
short-lived actor JWT into the appropriate credential header for each origin;
the host revalidates the actor UID, subject, profile and registry binding before
calling WAM. The token response is limited to the Azure DevOps audience. Do not
set `MSI_ENDPOINT` or `AZURE_POD_IDENTITY_AUTHORITY_HOST`. The manifest sets
`IDENTITY_ENDPOINT` and `IDENTITY_HEADER` for the App Service managed-identity
contract consumed by GCM/MSAL; the header placeholder must include the
configured `Bearer ` prefix so Kagent can replace it. Global gateway trust
remains supplied by Kagent.
Do not declare reserved global trust variables such as `SSL_CERT_FILE` in the
Harness: doing so conflicts with the controller-owned gateway trust.
No lab hostname, service CIDR, transport credential or CA is inherited from
the new base image. The retained MITM transport code is disconnected: the
manifest sets no session/data URL, gateway CA or proxy variables.
For a **new** Agent, apply the rendered manifest:

```powershell
$ModelManifest | kubectl @K apply -f -
$Manifest | kubectl @K apply -f -
kubectl @K wait agent/coder --for=condition=Ready --timeout=300s
kubectl @K get agent/coder -o yaml
```

For an **existing** Agent, first back up its exact UID/spec, Harness/template
and native session/revision references. Apply only the new Harness/template,
then JSON-patch the existing Agent with UID/resourceVersion tests and replace
only its harness/template references. Preserve annotations, labels, egress
references, pinned sessions and all unrelated settings. Never force-apply a
generic Agent document over an owned production Agent.

Readiness must match the new observed generation, `desiredRevision ==
latestSuccessfulRevision` and immutable `workloadImage`. Native golden
readiness does not prove that the lazily started .NET adapter or plugins work.
Verify a fresh disposable native session's A2A card/protocol plus an invalid
empty-prompt request: this starts the .NET adapter, which must return 400 before
repository/plugin/inference work. Verify loopback `/readyz` and `/status`,
then delete **only that session** through the native lifecycle and confirm
its actor is absent. Never retry an uncertain SendMessage or replay old tasks.

Separately verify identity-authorized policy/plugin preparation without model
generation by sending one fresh native A2A task whose text is:

```json
{"prompt":"Verify repository and Agency plugin preflight only.","allowEdits":false,"baseBranch":"main","dryRun":true}
```

`dryRun` prepares the configured repository and hash-verifies authorized policy
and plugin bundles, then returns before constructing a Copilot session or model
request. It refuses editing/work-branch options and is part of the durable
request digest, so a dry run cannot be replayed as generation under the same ID.
Add a checkout-relative `workingDirectory` only when a monorepo subdirectory
should select the task cwd and its ancestor Agency layers. Omitted and `"."`
retain the root compatibility identity.
Expect completed status and `policyAndPluginsAuthorized: true`,
`inferenceRequested: false` in its answer. A 401 is a real enrollment failure;
never retry inference or disable TLS to hide it.

A plugin 403 can be a separate executable/archive policy refusal after identity
authorization succeeds. Use the trusted host's bounded policy error codes and
existing artifact-review/provisioning process; do not strip plugins, loosen
hook restrictions or approve arbitrary new hashes. The lab follow-up retained
both original hook pins and added only a reviewed new manifest hash for the
same executable bytes; future plugin updates still require their own review.

## Retirement and rollback

After replacement readiness and reference checks, delete only obsolete custom
BYO Harness definitions using Kubernetes DELETE `preconditions.uid` (and
resourceVersion). Retain session-owned revisions, snapshots and image blobs
needed for recoverability. Never delete official ADK harnesses or broad
namespaces. If an old Harness was retired, first recreate its exact reviewed
specification from the protected backup under a new server-assigned UID, after
checking the name is still absent. Then guarded-restore the backed-up Agent
references; never repoint/replay existing sessions. No scheduler or recurring model work is
installed by this manifest.
