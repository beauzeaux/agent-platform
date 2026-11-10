# Test swarm preparation - operator only

Prepared 2026-11-04; extended 2026-11-05. **This business experiment is planning
only; none of its agents, sessions, campaigns, bindings, timers or application
environments have been created.** The separate [disposable A2A sanity fixture](../../A2A-SANITY.md)
has been live-tested and cleaned up.

## What is prepared

- Twelve role definitions, complete common/role instructions, model selections,
  desired tool permissions, review gates, and independent testing lanes.
- An agent-visible business brief and roadmap preserving product intent without
  exposing the existing implementation or its development history.
- A new ADO base, `users/nibeauli/test/base`, pushed without force. Commit
  `5625d63bb645a0ae69101b7574d73194ecb50993` has **no parents and no files**;
  its tree is `4b825dc642cb6eb9a060e54bf8d69288fbee4904`. It is not a branch
  containing a deletion of engsys. Its message includes `[skip ci]`.
- Model discovery through the existing authenticated host gateway advertised
  both `mai-code-2` and `gpt-5.6-sol`. No inference was requested. Discovery
  does not establish a successful inference/tool round trip.

The base is in the existing engsys repository, project Engineering Fundamentals,
organization msasg:

`https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys?version=GBusers%2Fnibeauli%2Ftest%2Fbase`

Only the empty base was pushed. The planning package remains local.
The source checkout and its unrelated dirty/untracked files were not changed.
`PACKAGE-LOCK.json` pins the exact five agent-visible files; it is operator
metadata and must not be added to the delivered inputs.

## Package boundary

**Only `agent-input` is eligible for delivery to agents.** Do not mount, clone,
index, or attach this directory's parent, the environment-map repository, this
operator record, the original specification files, or this conversation.

| File in agent-input | Purpose |
|---|---|
| `BRIEF.md` | Curated business requirements, invariants, unresolved decisions |
| `ROADMAP.md` | Outcome sequence, seed work packages, dependencies, scope fences |
| `AGENTS.md` | Common policy plus twelve role-specific system instructions |
| `DOCUMENTATION.md` | User/maintainer/auditor docs, Markdown/Mermaid flows and native TM7 lifecycle |
| `swarm-plan.json` | Non-executable preparation contract: roster, tools, gates |

This JSON is deliberately **not** a SendMessage payload, Kagent resource, Helm
values file, or configuration accepted by the current dispatcher. It cannot
start a swarm. Do not copy it into a live service configuration and imply that
the permissions it describes have been enforced.

## Source authority and intent extraction

Remote ADO and the local specification ref agreed on commit
`2a06b43a9086a517efd0317971983b02cba62991`,
`users/nibeauli/audit-ui/specs`.

Read only requirements/governance material for the product brief:

- `src\MAI.EngSys.AuditUI\UAR CM Tool BRD - MAI V2.md`
- `src\MAI.EngSys.AuditUI\roadmap.md`
- `src\MAI.EngSys.AuditUI\.specify\memory\constitution.md`, version 1.3.0
- `src\MAI.EngSys.AuditUI\specs\104-asset-owner-scoping\spec.md`, to
  distinguish narrow prior delivery decisions from business outcomes.

No product implementation code, migrations, tests, deployment configuration,
or old feature plans/tasks were used to seed a solution. In particular, the
prior five-operation API, entity layout, sign-off state representation, and
specific existing project names were **not** copied into the agent packet.
No original screenshots, PR URLs, work item IDs, original roadmap IDs,
completion claims, review comments, author identities, or source links are in it.

### Intent traceability - do not deliver this table to agents

| Test requirement | Original requirement basis | Roadmap coverage |
|---|---|---|
| T-01 | GF-001-004; Campaign object | 101, 102, 110, 112 |
| T-02 | Stage 1 source, snapshot, cutoff; INT-002 | 103, 104 |
| T-03 | Stage 1 decisions, justification, evidence, sign-off | 104, 107-109 |
| T-04 | Stage 1 net-new assets and questionnaire | 150-151 |
| T-05 | S2-001-006, S2-019-020, S2-022; INT-001-002 | 115-116 |
| T-06 | S2-004, S2-019; Stage 2 upload controls | 117-118 |
| T-07 | S2-007-008, S2-017-018 | 121-122 |
| T-08 | S2-009-012, S2-015-016 | 123-124 |
| T-09 | Stage 2 high-density table, bulk actions; NFR | 108, 124 |
| T-10 | GF-005, GF-008; S2-013-014 | 113-114, 125-126, 149 |
| T-11 | GF-007-008; AUD-001-002; INT-002 | 101, 111, 133 |
| T-12 | GF-006; AUD-003-004; retention assumption | 135-140 |
| T-13 | Stage 3 overview | 127-130 |
| T-14 | Stage 4 overview; S4-REM; INT-004 | 131-134 |
| T-15 | GF-009; Stage 5 overview; S5 metrics | 141-142 |
| T-16 | CM scope TBD; Stage 4 CM overview | 143-146 |
| T-17 | AI assumption; deferred delivery horizon | 147-148 |
| T-18 | Constitution I, II; roadmap architecture constraints | 101, 105 |
| T-19 | Constitution III, VI | Cross-cutting |
| T-20 | Constitution IV, V; GF-008; AUD-001-003 | 106, 111; cross-cutting |
| T-21 | Constitution VII; assignment/review seed clarification | 121-126 |
| T-22 | NFR; explicit non-production experiment boundary | 106; cross-cutting |

Original roadmap 101-118 and 121-151 are accounted for; absent/retired numbers
are not invented. Infrastructure enablers are preserved as engineering outcomes,
not inferred completed product features.

### Conflicts and deliberate choices

- **Per-item progress wins over a blanket admin launch gate.** The current
  constitution explicitly supersedes older campaign-wide approval language:
  authorized, eligible, otherwise valid items progress independently; missing
  ownership or SoD blocks affected items. The packet states that rule directly.
- The roadmap scopes ingestion to receipt of DataIngest-published outputs.
  The broader BRD's connector farming language is not a request to rebuild
  upstream ingestion inside this project.
- Explicit SQL/.NET/React/read-only upstream boundaries are product constraints,
  not secrets of the old implementation. They remain, with neutral names.
  Cluster PostgreSQL is infrastructure, **not** the application's audit store.
- Existing shipped/in-progress statuses are discarded. Every test outcome starts
  unimplemented. Demo, later, separate-CM, and deferred-AI horizons remain.
- Business ambiguities are retained, especially upload format/size, identity
  linkage, assignment precedence, delegation limits, notification cadence, bulk
  denominator, evidence applicability, retention disposition, and CM ownership.
  A synthetic fixture choice cannot quietly settle a business policy.
- SoD and trail specialists start with the first slice, even though full
  evidence packaging and closure arrive later. This does not pull all later
  product features into the first milestone.

## Proposed team

Three production-code authors use **mai-code-2**, as requested. At the user's
2026-11-05 correction, SDETs must use diverse model families: the proposed
API/SQL tester is **gpt-6.1-terra**, and the browser/journey tester is **grok-4.7**.
Both exact IDs were advertised by authenticated model discovery without inference.
These are two fixed, distinct assignments, not a new model-rotation scheduler.
Their tool-capable runtime paths still need the authorized preflight; catalog
discovery alone is not proof. Test receipts record the actual model ID.
The user's later reviewer-diversity direction supersedes the all-Sol assignment:
the architect and trail reviewer use **gpt-6.1-sol**, while the SoD reviewer
uses **gemini-4-argon**. Reviewers must retain distinct high-capability families.
Sol is advertised; **Argon is now advertised by the production gateway as Chat-only**.
The delegated 2026-11-05 investigation found upstream `/models` returning HTTP 200
with 40 models, including enabled `gemini-4-argon` with `tool_calls=true`, but only
`/chat/completions` support. At diagnosis, the running gateway returned 30 compatible
models and implemented only native `/responses`; that filter was deliberate.
The source implementation and deployed Windows host now support both protocols
following the user's approved restart. Do not publish a misleading alias or fall back.
Metadata does not prove actual inference, streaming or tool execution.
`gpt-5.6-sol` remains proposed for the PM and three documentation roles.
No model fallback is allowed.

The initial diagnostic agent made no changes or inference requests. Its original
18-test result and formatting baseline were superseded by the implementation
validation below. Metadata does not establish live Argon inference/tool behavior.

The three new roles are user-documentation, developer-documentation, and
flows-threat-model. The last owns the canonical Markdown/Mermaid flow catalog
and a genuine `.tm7` model; developer docs link those diagrams and explain their
code/control paths rather than creating duplicate flow sources.
See [tooling research](DOCUMENTATION-TOOLING-RESEARCH.md) for verified reusable
tooling, source pins, native Windows constraints and remaining proof work.

The PM is a planning agent, **not** a privileged scheduler. It emits work
packages and routing requests; a trusted coordinator retains dispatch,
authorization, task identities, cancellation, and receipt persistence.
Architect, SoD, trail, and SDET gates are independent and cannot be bypassed
by the PM or a code author.

Use the existing factory Kagent/Substrate lifecycle, unified model gateway,
private archives, and metadata-only telemetry. Create fresh test-specific
Agent/creator/session identities and snapshots. Existing business-role sessions,
memories, golden images containing a checkout, and workspaces are not reusable
inputs. Native memory remains off until fresh namespace isolation is proven.

## Chat Completions implementation - deployed to the Windows host

The retained infrastructure agent implemented authenticated
`POST /v1/chat/completions` to the native upstream `/chat/completions` route,
alongside unchanged Responses routing. Discovery includes either supported
protocol and preserves `supported_endpoints`; incompatible model/endpoint
requests are refused before inference. Host actor-JWT admission and bounded
Chat stream telemetry cover the new endpoint. No format translation or fallback
was introduced.

Gateway Release tests: **92 passed**. Targeted host LLM/actor-admission tests:
**24 passed**. The composed Release build and touched-file formatting/diff
checks passed. The full gateway formatter still reports **354 pre-existing
line-ending diagnostics in three untouched test files**.

The parent published, verified and promoted this immutable runtime:

`Q:\src\tools\HostCapabilities\.artifacts\runtime-chat-20261105-375cb3d8`

Its published lifecycle check passed, including PID/creation-time ownership,
graceful stop and an in-memory mocked logon action; that check did not change a real task.
A separate loopback-only, LLM-only published canary was started, inspected and
gracefully stopped. It exposed exact `gemini-4-argon` with only
`/chat/completions`, with LLM readiness passing and **zero inference requests**.
Its catalog had 31 entries at that observation; upstream catalog/counts can vary
and are not readiness invariants. Temporary canary configuration/key/state was
removed.

The user subsequently approved the idle shared-service restart on 2026-11-05.
Owner 6832 stopped gracefully and the new runtime became owner **46504**.
The controlled cutover completed in **14.29 seconds**, with the original
configuration hash, listener addresses and enabled startup state preserved.
The existing logon action now targets the promoted runtime through the explicit
per-user .NET host. Production LLM/ADO/MCP readiness passed; native ADO remains
intentionally disabled. All five expected compatibility listeners belong to
the new owner.

Production discovery exposed exact Argon with only `/chat/completions`; anonymous
Chat requests were rejected with 401. There were 34 models at the observation,
not a fixed expected count. Prior payload, startup XML, owner record and logs
are retained for rollback under:

`%LOCALAPPDATA%\HostCapabilities\rollbacks\chat-20261105T195836Z-1d033467`

No inference, accepted-work replay or swarm/session start occurred during that
gateway restart.

The subsequent disposable sanity fixture enabled and tested native bridge Chat
routing, and real Sol/Argon official-ADK tasks passed independent MCP challenge
checks. All 14 fixture objects and three sessions were removed; no session actor
remained. This proves an official-ADK Argon path, not every custom SDK runtime.
The existing business `native-config.mjs` still rejects non-Responses format.
Select the demonstrated ADK path for applicable read-only reviewers or implement
and test a separate SDK Chat mapping before using Argon there. Business-specific
permissions, model/runtime choices and canaries remain separate gates.

## Why the current swarm is not an activation target yet

Inspected the existing runtime rather than assuming it is generic:

| Existing file | Relevant limitation |
|---|---|
| `Q:\tools\kagent-lab\factory-coding\swarm_roles.py` | SlopMop role prompts, agent identities, bases and profile names |
| `factory-coding\swarm_manager.py` | SlopMop discovery/planning workflow, not this twelve-role delivery graph |
| `factory-coding\gateway_service.py` | SlopMop work-branch validation |
| `engsys-agency-coder\repo-tools.mjs` | Hard-coded engsys origin/base allowlist and SlopMop test command |
| `engsys-agency-coder\agent.mjs` | Engsys system policy and repository-plugin loading; optional original `audit_ui` helper |
| `agency-broker\profiles.json`, `broker.py` | Broad read/search tools; branch checks on PR writes do not isolate repository reads |

A branch restriction in a prompt, shallow clone, or PR target rule is **not**
an implementation-knowledge boundary. The ADO proxy's organization allowlist is
not repository/ref isolation. An unrestricted shell plus that proxy would also
defeat an otherwise scoped MCP profile.

Do not grant existing `engsys-reader`, `engsys-contributor`,
`slopmop-contributor`, Bluebird, WorkIQ, EngHub, wiki, history search,
`audit_ui`, or repository-supplied plugins to this experiment.

## Isolation design and launch gates

The recommended first run is **offline source delivery**: a trusted operator
exports only the five reviewed input files plus the parentless test lineage into
fresh actors. Actors have no ADO Git/REST transport or broad search tools.
They return owned patches/commits as artifacts. The trusted operator/coordinator
performs any approved remote writes and independently checks the exact target
and ancestry. Do not give a task-supplied command networked credentials just
because it is called a test.

For the first run, have the trusted operator restore approved dependencies from
1ES feeds into reviewed source-free caches; actor validation uses those caches
offline. Do not expose the general ADO proxy to actors merely for package
restore: its current organization-wide Git support would undermine source
isolation. A later package-only route needs separate enforcement and denial proof.

An alternate dedicated repository with deny-by-default repository-scoped
transport is reasonable later. Do not claim a new repo alone fixes broad search
or a Windows-user-wide Git proxy. Do not build a second orchestrator merely
to route these roles.

Before any start, require all of these:

1. Approve this packet, role/model choices, first milestone, and a bounded run
   budget. Limits in the manifest are proposals, not spending guarantees.
2. Bind reusable Kagent runtime/config and existing tools to test-only inputs;
   replace the hard-coded source/bootstrap/validator policy without changing
   the active SlopMop deployment. If reuse cannot meet the boundary, report the
   gap before introducing a new runtime or coordinator.
3. Enforce file/role permissions, no broad MCP discovery or source transport,
   no original product helper/plugins, and offline dependency caches.
   Prove negative source-access cases, not just prompt obedience.
4. Prove a blank actor/snapshot and fresh memory/creator scope. Inspect every
   delivered input, environment variable, plugin bundle and mounted volume.
5. Bind reviewed, sandboxed .NET, frontend, SQL integration and browser test
   commands. Existing `repo_validate` runs SlopMop; it must not be used unchanged.
   Existing `repo_write` also needs a pre-existing parent directory: reviewed
   scaffolding must support the empty-project bootstrap without a general shell.
   The manifest's new validation capability names are desired bindings, not
   callable tools that already exist.
6. Prepare SDET copies from an immutable candidate commit and owned synthetic
   SQL/app environments. Establish separate identities and evidence access;
   use no live Graph, email, Kusto writes, user datasets or production resource.
7. Prove exact target/source/ancestry rules for future writes. Keep the empty
   base immutable; create any integration and work refs as descendants only.
   No merge to main, source/spec branches, deployment, or automatic PR completion.
8. Configure task/checkpoint receipts and independent gates in the trusted
   coordinator. Test duplicate request IDs, uncertain delivery, failure and
   bounded rework without blindly replaying accepted work.
9. Run a separately authorized minimal model/tool canary and verify the actual
   model ID, including both selected SDET models. Prove distinct SDET families
   and scoped tool support; stop rather than silently collapse to one model.
   Separately establish the missing Argon route and prove distinct high-capability
   reviewer families. Catalog discovery alone does not satisfy this gate.
   No canary or inference has been authorized by this preparation.
10. Bind reviewed documentation/Mermaid/TM7 tools, prove portable update semantics
    and native Windows compatibility, and obtain a dedicated authorized desktop
    or explicit takeover permission for interactive TMT automation. A headless
    cluster cannot substitute XML parsing for native open/save/reopen evidence.

Until then, **packet prepared, swarm activation blocked** is the honest status.
No dormant deployment is represented as tested or launchable.

## Preparation verification

Original preparation passed its nine-role structural checks. The extended packet
passed checks for twelve unique roles, exactly three production authors pinned
to mai-code-2, two SDETs assigned distinct model families, three code/control
reviewers assigned distinct high-capability Sol/Argon families, matching
instruction anchors, known tool identifiers, independent gate owners, all activation flags
false, 22 unique requirements and twelve acyclic seed work packages.
All five agent inputs were checked for original project/branch/source names,
source URLs, broad-search provider names, private-key/password patterns and
non-ASCII content; none were found. The lock records their reviewed hashes.

ADO independently returned the new base SHA; Git remote lookup matched it.
Object inspection proved one root commit, the canonical empty tree, no parents
and no files. The factory agent-name list before/after was identical, and no
test role was registered. No campaign/session/inference mutation was issued;
existing source-checkout dirty/untracked paths were unchanged. These checks
verify preparation, not enforcement of a future runtime or live tool execution.

## Independent testing copies

At a review checkpoint, hand SDET-contract and SDET-journey the same immutable
candidate commit plus approved requirements/contracts, not a developer's dirty
workspace. Give each a separate writable test overlay, fresh disposable SQL
database, synthetic identities, local email sink and application instance.
Use gpt-6.1-terra and grok-4.7 respectively, with independently derived coverage
before findings are exchanged; do not treat different model names as sufficient
isolation or their agreement as evidence that behavior passed.
The system under test remains pinned; SDETs cannot patch production code to
make tests pass. A bug fix creates a new candidate and invalidates affected
receipts. Keep regression tests and observations even when a candidate fails.

Use current telemetry and private session archives for operational debugging,
but never call OTel an audit ledger or proof that an application operation
committed. Read-only audit assertions use test application exports and separately
scoped test-database access, not cluster-admin or infrastructure database access.

## Stop / rollback

There is no new swarm runtime to stop. Do not reset the existing swarm, restart its
timer, pause unrelated campaigns, or alter the cluster to undo this preparation.
Discarding this plan is a local documentation operation and does not roll back
the independently approved shared-gateway upgrade. Removing the remote
empty base, if later requested, must verify its current SHA first and must not
delete any work descendants. Do not force-reset any integration branch.
