# Agency harness base image

This is the shared OCI foundation, not a repository convention or deployment
framework. Repositories own their derived images, registry publishing and
Kagent/Helm delivery through their own CI/CD.

The base contains the native Kagent A2A bridge, Agency, its pinned Copilot
executable, Git Credential Manager, the Azure Artifacts Credential Provider,
the disconnected legacy host credential transport, .NET 10 SDK, Node 24/npm,
Git and system CA certificates. Both initial coding profiles need .NET 10;
including the SDK here avoids duplicating that toolchain in each image.

It contains **no repository checkout or URL, coding adapter, factory controller,
legacy JavaScript coding agent, host credentials or deployment certificates**.
The base is extended rather than run as a complete agent by itself.

## Build inputs

Use reviewed, architecture-matching release artifacts in a private build context:

```text
<runtime-context>/
  native/harness       native Kagent bridge
  agency/agency        Agency executable
  copilot/             pinned Linux Copilot package, including copilot
  gcm/                  reviewed Linux Git Credential Manager release
  nuget/plugins/        reviewed Azure Artifacts Credential Provider plugins
  transport/*.js       retained legacy host credential transport modules
```

The bridge source is currently maintained in the lab's `factory-coding` and
`byo-harness` projects; source relocation is not part of this image split.
Obtain its tested release binary, rather than copying `/controller` or
inheriting the old factory image. The bridge must support
`AGENCY_AGENT_EXECUTABLE` and `AGENCY_AGENT_ENTRYPOINT`. Existing
`AGENCY_CODER_*` names remain supported only for previously deployed images.

The Agency/Copilot/transport inputs can be extracted from the reviewed existing
integration image during migration. Acquire GCM and the Azure Artifacts
Credential Provider through the owning release pipeline and pin their reviewed
release digests. Only those executable assets belong in the context; do not
copy environment, filesystem, certificates, keys, operator configuration,
credential caches or repository workspaces. Runtime compatibility is
Linux/amd64 for the currently pinned Node and .NET images; other architectures
require matching artifacts and base image digests.

The image sets `AGENCY_GLOBAL_CONFIG_PATH` to the **directory**
`/opt/agency/global-config`. That directory is created intentionally empty.
Agency appends `agency.toml` or `agency.yaml` when reading this global scope;
pointing the variable at a TOML file would not isolate global configuration.
Repository-local Agency discovery remains cwd-based, and the coding adapter's
trusted host supplies only authoritative, policy-checked repository layers.
The operator's personal global Agency config and credentials are never copied
into the image.

From the HostCapabilities checkout, using Docker or another compatible builder:

```powershell
$Base = 'registry.example.org/team/agency-harness:<build-id>'
docker build -f src\AgencyHarness\Containerfile -t $Base $RuntimeContext
docker push $Base
docker buildx imagetools inspect $Base
```

The Containerfile pins the tested .NET and Node image manifests. Their build
arguments can be updated by the owning release pipeline as toolchains advance.
Use the pushed **manifest digest** in subsequent `FROM` references, not a
Docker image/config ID. Podman also accepts `-f ...\Containerfile`; the filename
is a build recipe convention, not a separate OCI standard.

## Extend it

For the coding implementation, see
[AgencyCoder](../AgencyCoder/README.md). A different agent can instead copy in
its own published adapter and select it directly:

```text
ARG AGENCY_HARNESS_IMAGE
FROM ${AGENCY_HARNESS_IMAGE}
COPY publish/ /app/my-agent/
ENV AGENCY_AGENT_EXECUTABLE=/usr/bin/dotnet \
    AGENCY_AGENT_ENTRYPOINT=/app/my-agent/MyAgent.dll
```

This is executable inheritance, not a generic plugin/configuration framework.
The derived implementation must honor the bridge's loopback task contract:
`GET /readyz`, `POST /tasks`, `GET /tasks/{id}` and
`POST /tasks/{id}/cancel`. Kagent still owns native A2A, model/tool configuration,
identity, session/task authority and telemetry context. The bridge injects
W3C trace context/baggage and the durable workspace/session environment into
the adapter. Do not start a second Kagent controller.

The image entrypoint is `/app/harness`; no `/controller` is packaged or needed.
The legacy host credential transport remains in the image for bounded rollback
but is disconnected by default. It starts only when both of its existing
`AGENCY_TRANSPORT_*` variables are explicitly injected. New deployments use
the WAM-backed IMDS endpoint, GCM managed-identity mode, and the Azure Artifacts
Credential Provider instead of proxy variables or localhost interception.

## Initial coding layers

```text
AgencyHarness base
  -> AgencyCoder (.NET coding adapter)
     -> engsys coder (repository-specific image defaults)
     -> Audit coder  (repository-specific image defaults)
```

The examples under `deployment/agency-coder/engsys` and `audit` are buildable
layers, not a required location in either product repository. No Helm chart,
repository layout or shared CI/CD convention is introduced here.
