# Public-data-only native integration canary

This is a **new image only**, extending the exact published factory manifest
`sha256:b8b03c7e4145b0af095713e2c1fd00b0dadf6c9d48b0fe75d794cc05b886dfe8`.
It does not modify production harness sources, images, services, agents, or deployments.
The image replaces `/app/coder/agent.mjs` with `public-probe.mjs` and uses the existing
`/app/harness` managed Go binary. There is no actor administrative/exec API.

## Public-content boundary

- The only SDK inference prompt is **`Reply exactly NATIVE_HOST_OK.`**, under the fixed
  public system message in `probe-worker.mjs`. Compiled instructions and incoming text
  are never forwarded. Only compiled model/provider/placeholder headers are used.
- `CopilotClient` uses empty mode, offline mode, no logged-in user, a fresh public HOME
  and empty work directory, empty tools/MCP/plugin/skill/hook sets, and reject-all permissions.
  The stock pinned CLI is used directly, not the Agency/private plugin wrapper.
- No checkout clone, repository inspection, private plugin preparation, Git identity,
  credential-store read, real `agent.mjs` import, or private task prompt is allowed.
  Existing `engsys`, `agency`, or other nonpublic durable directories cause startup refusal.
- SDK child environment is allowlisted; inherited Git/GitHub/Agency settings, tokens and
  native identity variables are excluded. Node's global environment is also replaced
  before importing the SDK, so SDK environment merging cannot re-add them. SDK session IDs
  are independent public random identifiers, not native identity hashes. Both SDK telemetry
  and shared cross-session search store remain disabled.
- Stock CLI startup uses its actual supported flags and a validated native
  offline-provider environment, not the Agency-only `--no-config-plugins` or
  `--no-default-mcps` flags. Provider URL/model/key come only from compiled native
  configuration; the key is the inert egress placeholder. SDK loader scratch
  files stay in the separate public temp directory, not the empty model workspace.
- Standard Node/CLI certificate validation uses system/bundled roots plus the projected
  egress bundle. Node 24+ `tls.setDefaultCACertificates` is required. No TLS bypass exists.
- Native destinations must come from compiled configuration and match the native host
  HTTPS origin on port **8445**. Header values other than the compiled bearer placeholder
  are rejected. No loopback model/MCP adapter or `/session` token exchange is started.

## Offline validation and parent-only packaging

From this directory:

```powershell
node --test test-probe.mjs
python -B -m unittest -v test_build test_invoke test_resources
```

Tests use a fake SDK/client/transport and the real session archive module from the
existing workspace. They never call a model, embedding endpoint, Docker, or Kubernetes.
`verify-sdk-start.mjs` is an additional parent-owned integration fixture: run it
inside the cached image with `--network=none`. It starts the actual stock CLI/SDK,
creates an empty-tools session and fetches documented events without sending any
prompt. Mount that script and `probe-worker.mjs` read-only into the same directory;
it requires no downloads and cleans only its own unique temporary directory.

Parent build commands, run only after reviewing the source:

```powershell
wsl.exe -d Ubuntu-26.04 --user root --exec sh -c 'cd /mnt/q/src/tools/HostCapabilities/deployment/native-canary && python3 -B build.py'
# Explicit private publication and primary-node cache import, still no deployment:
wsl.exe -d Ubuntu-26.04 --user root --exec sh -c 'cd /mnt/q/src/tools/HostCapabilities/deployment/native-canary && python3 -B build.py --publish'
```

Prerequisites: the pinned factory OCI blobs still exist in
`/home/nibeauli/kagent-lab/factory-coding/oci`, and the cached factory image's
rootfs, runtime configuration, OS and architecture match those pinned blobs.
Modern Docker may report a manifest ID rather than the OCI config digest; the
builder validates actual image contents rather than assuming those IDs coincide.
The builder refuses another base; it does not pull.
WSL Docker is invoked through `sudo -n`. Build uses `--pull=false --network=none`;
there are no npm, pip, apt, or Go restores/downloads.

The existing private OCI layer helpers and publisher are reused. Publication is only
to repository `kagent-lab/native-public-canary`; cache import targets only the primary
node, not XL/secondary/business nodes. No Helm/kubectl apply, rollout, service restart,
or production tag change occurs. The existing private registry port-forward on port
15000 must be available. Generated `.build/source.json` records base manifest/config,
each shipped file hash, source fingerprint and local image ID. `.build/image.json`
records the resulting immutable OCI digest; publication also writes `.build/values.json`.
Image labels and worker `/status` expose the public base/source fingerprints.

## Parent Agent/session setup

Create **two distinct new Kagent Agents**, e.g. `public-native-canary-a` and
`public-native-canary-b`, in `factory-executions`, using this image digest, BYO command
`/app/harness`, and fresh durable sessions. Do not fork/reuse a business snapshot.
Use a public compiled instruction such as `Public integration canary.`; the worker
deliberately ignores even that instruction for external inference.

`resources.py` renders the exact pinned v1alpha3 native ModelConfig, RemoteMCPServer,
AgentTemplate, private-embedding EgressPolicy, and **two distinct Harness/Agent pairs**.
It writes only `.build/resources.json`, with ownership labels, and never calls Kubernetes:

```bash
# CANARY_IMAGE = published .build/values.json repository + @ + digest.
# RUN_ID is one public UUID shared by A/B; MODEL is the host-advertised public model.
# SNAPSHOT_PREFIX is a new approved private snapshot prefix, never a business snapshot.
python3 -B resources.py --image "$CANARY_IMAGE" --run-id "$RUN_ID" --model "$MODEL" --snapshot-prefix "$SNAPSHOT_PREFIX" --worker-pool kagent-default --allow-public-inference
```

`--allow-public-inference` grants A's prospective gate only; B remains disabled.
Omitting it leaves both disabled. Parent alone, after controller/archive readiness and
checking that all same-named objects are absent or owned:

```bash
/snap/bin/microk8s kubectl apply --dry-run=server -f .build/resources.json
/snap/bin/microk8s kubectl apply -f .build/resources.json
```

No controller Deployment, worker pool, business Agent, Secret, or existing Service is
rendered or patched. Ready/EgressPoliciesApplied and actual image pinning are parent gates.

Reuse the accepted native ModelConfig and one HTTP RemoteMCPServer:

```text
model base URL: https://host-capabilities.proxy-access.svc.cluster.local:8445/v1
MCP URL:        https://host-capabilities.proxy-access.svc.cluster.local:8445/agency/mcp
```

The MCP selected tools must exist in the host profile; only initialization, initialized
notification, tools/list and DELETE are called, never call_tool. No MCP result or
description is provided to the model. The actual Windows ES256/P-256 JWT/profile
admission, issuer/audience, origin TLS and Substrate credential replacement must already
be ready. Register only these new actor identities in the parent-owned host registry.
The identity projection is `session-<UUID>`; Go's `AGENT_SESSION_ID` is the UUID.

Required Agent environment (image defaults never enable paid inference):

```text
AGENT_NATIVE_CONFIG=true
OPENAI_API_KEY=kagent-credential-injected          # normally compiler-emitted
NATIVE_MEMORY_ENABLED=true                       # Go localhost bridge 18082
SESSION_ARCHIVE_EXPORT_ENABLED=true               # Go localhost bridge 18083
PUBLIC_CANARY_INFERENCE=false                     # A: true only for the explicit model proof; B: false
PUBLIC_CANARY_RUN_ID=<same public UUID for A and B>
NATIVE_MEMORY_CONFIG_JSON=<public JSON below>
```

Go supplies `AGENT_DATA`, `AGENT_SESSION_ID`, `AGENT_RUNTIME_IDENTITY_PATH`,
`AGENT_COMPILED_CONFIG_PATH`, name and namespace. Optional `AGENT_KAGENT_REVISION`
correlates the full revision. Do not override projected trust variables.

Example memory JSON, passed as an environment value, never a secret or a prompt:

```json
{
  "enabled": true,
  "embedding": {
    "baseUrl": "http://local-embeddings.factory-executions.svc.cluster.local:8080/v1",
    "model": "BAAI/bge-base-en-v1.5",
    "dimensions": 768
  },
  "localBridgeUrl": "http://127.0.0.1:18082",
  "privateHttpOrigins": ["http://local-embeddings.factory-executions.svc.cluster.local:8080"]
}
```

The parent must explicitly grant this exact private embedding origin through native
egress and have the native MemoryService/pgvector/embedding prerequisites ready.
Memory scope is server-derived; there is no user/Agent selector supplied by Node.
The existing `memory_propose({content})` and `memory_search({query})` handlers are invoked
directly by trusted fixed operator actions, **not registered as SDK/model tools**.

## Parent-only native API commands

Do not use the business factory gateway: it is bound to the business Agent and can
add repository fields. Use the existing authenticated controller Session/A2A APIs.
Parent owns opening/reusing the existing loopback forward:

```text
/snap/bin/microk8s kubectl -n factory-executions port-forward service/factory-controller 18093:8083 --address=127.0.0.1
```

In another parent-owned root WSL shell, from this directory, set `TOKEN_FILE` to the
existing protected **controller token file path**, never the token value. Choose
stable public UUIDs for `CREATE_A`, `CREATE_B`, and each `MESSAGE_ID`; retain them in
the parent's protected acceptance record. These are identifiers, not credentials.

```bash
python3 -B invoke.py --agent public-native-canary-a --token-file "$TOKEN_FILE" create --request-id "$CREATE_A"
python3 -B invoke.py --agent public-native-canary-b --token-file "$TOKEN_FILE" create --request-id "$CREATE_B"
# Save returned sessionId values as SESSION_A and SESSION_B, then authorize their actor registry entries.
python3 -B invoke.py --agent public-native-canary-a --token-file "$TOKEN_FILE" send --session "$SESSION_A" --message-id "$MESSAGE_ID" --code metadata
# Poll the returned taskId, never blindly resend an uncertain SendMessage:
python3 -B invoke.py --agent public-native-canary-a --token-file "$TOKEN_FILE" get --session "$SESSION_A" --task "$TASK_ID"
```

Use a **new stable message UUID for each distinct action**. Require metadata's
`archive.status:"exported"` and no pending error before proceeding, not merely terminal status:

```bash
python3 -B invoke.py --agent public-native-canary-a --token-file "$TOKEN_FILE" send --session "$SESSION_A" --message-id "$SEED_MESSAGE_ID" --code seed
python3 -B invoke.py --agent public-native-canary-b --token-file "$TOKEN_FILE" send --session "$SESSION_B" --message-id "$ISOLATE_MESSAGE_ID" --code isolate
python3 -B invoke.py --agent public-native-canary-a --token-file "$TOKEN_FILE" send --session "$SESSION_A" --message-id "$MODEL_MESSAGE_ID" --code model --allow-public-inference
```

Run/poll these **in sequence**, not as a blind batch:

1. A `--code seed`: proposes only `Paris is the capital of France. PUBLIC_CANARY_<run UUID>.`,
   then searches and requires its own returned memory ID to be visible.
2. B `--code isolate`: only searches the same public fixture; requires **zero rows**.
   Any returned row fails isolation without recording its content or sending it to a model.
3. A `--code model --allow-public-inference`: requires A's predeclared
   `PUBLIC_CANARY_INFERENCE=true` as a second gate. This is the one paid public SDK turn.
   The worker commits an archive preflight before either model inference or a memory
   proposal; inactive archive RPC prevents those operations. A second model task ID after
   any model work attempt is rejected, including after uncertain/outage recovery.
   Do not repoint an active pinned session to enable it; declare A's gate prospectively.
4. Poll each task through `get` and inspect the private archive index/object using
   parent-owned storage tooling. This worker never receives RustFS/PG credentials.

`invoke.py` rejects non-canary Agent names, verifies Session provenance using the existing
prepared session client, and makes exactly one SendMessage with no transport retry loop.
On uncertain send admission, use the same parent native ListTasks/receipt reconciliation;
do not submit another message ID as a retry.

## HTTP/receipt/archive contract

Managed Go uses loopback `/readyz`, `/status`, POST `/tasks`, GET `/tasks/<native A2A task ID>`.
POST accepts only `{id, prompt:<metadata|model|seed|isolate>, allowEdits:false}`.
Arbitrary text, edits, repo/branch/plugin options and admin routes are rejected. POST
`model` with a disabled gate returns 403 before creating a journal or starting an SDK.

The canary-only config projection replaces compiled instruction text with its public
system prompt before using the pinned native mapper. It handles the CRD's
`terminateOnClose:true` compiled default by implementing explicit MCP DELETE itself;
false or ambiguous close settings fail closed. No frozen production mapper is modified,
and TLS/auth/provider validation remains the pinned mapper's responsibility.

Each action uses its own public SDK session and the documented SDK `session.on` and
`getEvents`, real `SessionArchive.beginTask/finishTask/close/upload`, and existing Go export
bridge. Listener attachment precedes the only possible model send. SDK events preserve
their actual origins; fixed operator events have `origin:"operator-canary"` and event
types `operator.canary.*`. Their `ephemeral:true` means **not part of SDK persisted
history**, not fabricated SDK emissions. No synthetic SDK messages/terminal events exist.
Operator-only actions honestly have partial SDK-turn completeness (no assistant turn);
the model action must provide the real assistant message/terminal for full completeness.

Only after local JSONL/manifest/outbox persistence and the export attempt settle can a
receipt become `completed`. Export outages may yield `completed` business work with
`archive.status:"pending"` and a retained durable outbox. They never repeat inference,
memory proposals or MCP requests. Local archive durability failure leaves an explicit
failed/uncertain receipt with `businessOutcome` retained, also never replayed.
Accepted/working journals recover as `interrupted`, without automatically executing work.
Repeated native task IDs return the prior receipt; changing its action conflicts.

Receipts expose public result counts/IDs, SDK/archive paths, source fingerprint and
pending export status. Source files, task prompts, credentials, returned MCP descriptions
and returned memory contents are never printed. `businessWorkAttempted`, `businessOutcome`,
`failureStage` and `probeError` distinguish uncertain accepted work from archive settlement.
No live SQLite database is copied/read.
Canary session disk files remain sensitive operational state even though inputs are public.

**Not performed by this artifact's author:** image build/publication/deployment, actual
SDK/provider inference, actual embedding/memory writes, actor registry mutation, or live
archive upload. Passing fixtures are not a claim that the real native path succeeded.
