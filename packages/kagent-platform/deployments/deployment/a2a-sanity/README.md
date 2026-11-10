# Disposable mixed-model A2A sanity swarm

This is a finite **public/synthetic acceptance fixture**, not the prepared
business-project swarm and not a new long-running orchestrator.

| Native A2A role | Runtime / model | Required observable outcome |
|---|---|---|
| Coder | Existing managed harness + Copilot SDK; `mai-code-2`, Responses | Use confined repository tools to implement a tiny sum function; execute seven immutable checks |
| Reviewer | Official Go ADK; `gpt-6.1-sol`, Responses | Review the A2A coding artifact and read a fresh ConfigMap nonce through official read-only MCP |
| Tool verifier | Official Go ADK; `gemini-4-argon`, Chat Completions | Independently read a different fresh MCP nonce and verify the same artifact |

Nonces are synthetic challenge data, not credentials. They are never supplied
in the model prompts: exact nonce/hash matches prove actual MCP reads, rather
than trusting an assistant's assertion that it used a tool.

## One-command check and teardown

From Windows PowerShell:

```powershell
$sanity = 'Q:\src\tools\HostCapabilities\deployment\a2a-sanity\Invoke-Sanity.ps1'
& $sanity -Action Check                         # preview only
& $sanity -Action Check -Apply -Live             # setup, real model work, finally teardown
& $sanity -Action Status                        # retained result / report location
& $sanity -Action Remove                        # teardown preview
& $sanity -Action Remove -Apply                 # idempotent cleanup retry
```

For inspection between phases:

```powershell
& $sanity -Action Setup -Apply -Live
& $sanity -Action Run -Live
& $sanity -Action Status
& $sanity -Action Remove -Apply
```

The equivalent Linux CLI is `python3 -B sanity.py <setup|run|status|remove|check>`
in this directory as **primary WSL root**. Use `--apply` for infrastructure
writes and `--live` for model work. `--run-id <UUID>` selects an exact retained run.
`Check` allocates a fresh run and cleans it in `finally`, including partial setup.
It refuses to take over another active fixture. Setup/Run can deliberately leave
owned resources for inspection until Remove.

No scheduled prompt, timer, new daemon, external branch/PR, business checkout,
package restore, real account dataset, live email or production DB mutation is
part of this test. There are three bounded native A2A assignments; model/tool
continuations can produce more than three provider requests. There is no monetary
budget guarantee. Each coding session allows at most 16 exposed tool calls and
one model assignment, with fixed test commands and bounded task deadlines.

## Dependencies and reuse

- Existing factory controller, worker pool, authenticated native Session/A2A APIs
  and protected controller token file.
- Existing official read-only `cluster-management-tools` MCP service. Only
  `k8s_get_resource_yaml` is selected; its RBAC denies Secrets and mutations.
- Existing `cluster-management-llm` admission Secret and its trusted-egress grant.
  The fixture references it but never changes/deletes it or puts credentials in
  actor environments. Real GitHub/WAM credentials remain on Windows.
- The native host bridge must admit `/v1/chat/completions`; the legacy proxy
  need not be widened. Argon uses the official ADK's supported `chatCompletions`
  native format, not a bespoke Chat-to-Responses adapter.
- Cached, pinned public native-canary base and existing private OCI publishing/
  node-cache helpers. First setup builds offline and caches only the new fixture
  repository on the three workers. No public npm/pip/apt restore or model download.

Build output is under `HostCapabilities\.artifacts\a2a-sanity`, not source.
Changing shipped worker source invalidates the image fingerprint; never repoint
an existing session to a rebuilt image. Remove and create a fresh run.

## Evidence and recovery

Root-private state:
`/home/nibeauli/kagent-lab/a2a-sanity/<run UUID>/state.json`.
It records native session/task/message IDs, actual coding source/hash, observed
tool names/check results, model/wire assignments and MCP challenge dispositions.
The driver uses native A2A SendMessage/GetTask and passes the coder's actual
artifact to both reviewers. It is not three independent success prompts.

SendMessage is checkpointed **before** submission and never blindly resent after
an uncertain response. Poll the retained task/context or remove the fixture.
Re-running a completed Run returns retained evidence without inference.
Operation locking prevents two CLI mutations from racing; Status remains readable.
Failed or interrupted worker journals are not replayed after restore.

Expected PASS requires all seven code checks, both fresh nonce matches, the same
source hash throughout, and successful native task completions. A code-looking
assistant answer, model agreement or terminal status alone is not sufficient.
Missing model/endpoint support or MCP availability fails the check.

## Exact cleanup boundary

Only labeled, run-ID/UID-matched fixture Agents, templates, harnesses, ModelConfigs,
RemoteMCPServer and synthetic ConfigMaps are deleted. Native sessions are deleted
through the authenticated controller and checked absent, including their native
session actors. Transport errors are not accepted as absence. Foreign/replaced resources
are refused. No namespace, shared Secret/tool service, worker pool, host gateway,
other swarm, controller or database is reset or removed.

The private receipt, immutable image/cache and platform-managed snapshot/telemetry
retention are intentionally retained. Cleanup does not claim that metrics, traces
or native snapshot blobs were erased. The shared native Chat bridge improvement
is a tested platform capability and also remains after fixture teardown.

## Offline validation

```powershell
python -B -m unittest -v test_sanity
node --test test-worker.mjs
```

These use fixture transports/clients only and make no model or cluster requests.
The native bridge regression suite is in
`Q:\tools\kagent-lab\proxy-access\test_native_host_bridge.py`.

## Verified live result

On 2026-11-05, run `da6b1942-be01-4200-bff5-a157e83ec65d` passed all three
native A2A tasks: real SDK edits plus seven immutable checks, and independent
Sol/Argon MCP nonce/hash proofs. The run removed all 14 owned Kubernetes objects
and three sessions; an independent native lookup found zero remaining session
actors. A second Remove was harmless, and shared LLM/ADO/MCP readiness stayed good.

Worker image:
`sha256:82321e3ca831eb95b23103b9047205613fa38f757de18e4ed63d3ac556e658da`.
The code artifact SHA-256 was
`4e8fc3abb55da5c1fbe4afddd1833dca66e2dff87a9d30c13e7607a4c7388872`.
These identify that test, not constants required of future generated solutions.

This proves the native Go ADK's Argon Chat/MCP path, not Chat support in every
custom SDK harness. It does not test Windows Agency nested tools, remote Git/ADO
writes, actor-JWT profile issuance, memory isolation or archive retention. The twelve-role business experiment
remains inactive.
