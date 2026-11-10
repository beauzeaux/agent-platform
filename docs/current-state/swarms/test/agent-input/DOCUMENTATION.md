# test - documentation, flows and native threat-model contract

This is an engineering-delivery contract, not additional business policy or an
instruction to start work. The three roles have distinct audiences and owned
artifacts. They read only the supplied test lineage and approved evidence.

## Ownership and expected outputs

Paths below are proposed project-relative locations to confirm with the architect.
They are not files already generated for an implemented application.

| Owner | Owned outputs | Independent acceptance |
|---|---|---|
| user-documentation | `docs/user/`: role/task guides, troubleshooting, limitations and links to synthetic screenshots | SDET-journey executes affected instructions against the candidate |
| developer-documentation | `docs/developer/` and `docs/audit/`: setup, project/contract map, maintainer code walkthroughs, control-to-code/test evidence index | SDET-contract checks commands/mappings; architect reviews technical accuracy |
| flows-threat-model | `docs/flows/`: catalog and per-flow Markdown/Mermaid sequences; `docs/security/`: model spec, `test.tm7`, Markdown security report and semantic change log | Architect reviews semantics; SoD/trail reviewers check affected controls; trusted Windows runner verifies native compatibility |

Documentation authors cannot edit production/test implementations, waive gates or
approve business exceptions. Tools enforce each write scope. The flow steward
owns canonical flow diagrams; the developer writer explains their code-level
implementation and links to them rather than creating duplicate diagrams.
The architect owns architecture decisions, not a second documentation tree.

## User guidance

Each delivered journey names its audience, prerequisites, campaign/role context,
steps, expected result, failure/permission feedback and recovery path. Include
evidence/justification requirements and the difference between saving, sign-off,
notification read state and actual workflow completion.

Use current synthetic SDET screenshots when useful, with accessible captions
and version references. Never include a real account, access listing, credential,
private URL, captured desktop or unimplemented button as a verified example.
Planned features are labeled planned and do not appear in supported-task guidance.

## Maintainer and auditor guidance

Explain entry points and dependency direction, shared contracts and provider
composition, server-side authorization/identity, SQL transactions/concurrency,
upstream snapshot boundaries, audit/evidence capture and failure/retry behavior.
Link claims to candidate-relative files/symbols and focused tests/receipts.
Use approved generated OpenAPI as API-reference input, not a manually duplicated
endpoint list. It does not replace a code-flow explanation or control evidence.

The audit index maps implemented requirements/controls to code, policies,
canonical flow IDs, tests and genuine receipts, with unknown/blocked gaps stated.
Show the limits of an integrity claim and distinguish diagnostic telemetry,
agent transcripts and the application's committed audit record.

Setup, test, migration and troubleshooting commands must be replayed by an
authorized SDET in a clean disposable environment. A documentation author cannot
grant a command network access or production permissions by writing it down.

## Key-flow catalog

The steward keeps a catalog with stable IDs, scope/status, actors/components,
entry point, canonical walkthrough, sequence, DFD interactions/boundaries,
related requirements/controls/tests and the revision at which claims were verified.
Use links/tables and existing model fields rather than a new graph platform.

Initial candidate flows to refine against the architect-approved design:

- Authentication, campaign discovery and role/ownership authorization.
- Campaign kickoff and context/lifecycle transitions.
- Asset-roster receipt and immutable scope publication.
- Owner scoping, exclusion/compliance resolution and permitted delegation.
- Access-population receipt, validation, publication, retry and manual fallback.
- Reviewer assignment, linked-identity SoD, exceptions and reassignment.
- Single/bulk decisions, stale submissions, justification and final attestation.
- State/audit commit, evidence capture, failure/retry and reconstruction.
- Notification request/delivery/failure and protected deep links.
- Later remediation, closure, export and read-only auditor access when implemented.

Each walkthrough includes a Mermaid `sequenceDiagram`, explanatory Markdown,
success and relevant refusal/partial-failure/retry/asynchronous paths. Participant
names match the component catalog. Include references to trust crossings, sensitive
data and security checks without confusing a sequence diagram with a DFD.

## Native model and synchronized views

The maintained `.tm7` must be a genuine Microsoft Threat Modeling Tool model.
Keep the reviewed generator's existing YAML/JSON input format for components,
assets, trust zones, data flows, threats, mitigations and security-test cases.
Do not add an incompatible private schema or implement a custom TM7 serializer.

The model/spec/report share stable IDs with the key-flow catalog; a business
journey can map to multiple request/response/async DFD edges. Explicitly record
that mapping and required trust boundaries. Sequence syntax alone cannot infer
data classification, authorization, store permissions or threat applicability.

Use the supplied pinned generator with update/merge support and its Markdown
report generator. Retain the prior reviewed `.tm7` as an input when updating.
The update facility is not an unlimited guarantee of preserving every native
edit: compare IDs, removed elements/threats, dispositions, notes and mitigations.
Never silently lose a reviewed threat or automatically accept a changed one.

Native review edits are evidence to reconcile back into the spec and report;
the update command does not make the Markdown twin automatically reflect them.
Changed endpoints, identity, authorization, data, boundary, storage or delivery
semantics require re-evaluation even if the old threat ID still matches.
Keep prior reasoning/history while marking affected dispositions for review.
Only an authorized human accepts risk; mitigation claims require control evidence.

## Tool binding contract

These are desired bounded operations, not tools already installed:

| Binding | Intended reuse and limits |
|---|---|
| validate_documentation | Existing Markdown/link checks and reviewed clean-environment command receipts; compare documented API links with candidate OpenAPI |
| render_mermaid | Pinned Mermaid CLI, local inputs/offline assets only, restricted config and temporary render outputs; no public renderer or general browser control |
| generate_threat_model | Pinned reviewed TM7 generation/update and Markdown-report scripts/assets, confined input/output paths and approved dependencies |
| validate_threat_model | Hardened model parsing and semantic/coverage checks using reviewed existing tooling; compare IDs, endpoints, threats/dispositions and input/output hashes |
| request_native_tm7_validation | Emit a bounded request to the trusted coordinator for the Windows validator; it is not permission to control the host desktop |

Source readers and documentation-scoped writes use the reusable confined
repository primitives. Reuse existing SDET evidence readers and lifecycle checks.
Do not install packages or activate a new skill/MCP service during an assignment.
New glue or tooling source changes require an approved coding task and review.

## Freshness and acceptance

For each candidate, the PM routes a documentation impact check. A changed
user-visible behavior, API/contract, identity/authorization/policy, data/provenance,
SQL transaction, trust boundary, notification or deployment assumption triggers
the relevant writers and model steward before integration.

Draft from approved design early, marking proposed behavior explicitly; refresh
against implemented source and SDET evidence before claiming it is current.
Later/deferred features remain clearly out of scope. An unaffected artifact may
reuse prior evidence only with an independently reviewed no-impact decision
linked to its original receipt, not an author's automatic "no changes" assertion.

Final receipts identify the implementation commit, documentation/model input and
output hashes, tool/template/profile versions, flow/control/test coverage, native
validation evidence if applicable, findings and unverified boundaries. Store final
candidate receipts externally to avoid a document needing to contain its own Git
commit hash. Candidate changes invalidate affected checks under the normal gates.

Required checks include Markdown links/anchors, executable example evidence,
Mermaid rendering, code-to-flow/model consistency, stable-ID/ref validity,
threat-disposition preservation and absence of sensitive content. Rendering and
well-formed XML prove neither semantic accuracy nor native TMT compatibility.

Native compatibility requires a pinned, trusted TMT version on an interactive
Windows runner: open, inspect diagram and threat/mitigation content, save/reopen,
and compare semantic identity and dispositions. Require an actual passed receipt,
not just process exit zero: unavailable native tooling can otherwise report a
successful skip. An automated-ready/pending-human result is not approval.

The coordinator must obtain explicit permission for any interactive automation
that takes over mouse/keyboard, or use a dedicated authorized Windows desktop.
Agents cannot borrow the user's active desktop implicitly. Missing native
validation blocks the affected model gate and is reported as blocked/not-run.

No application model exists yet; do not manufacture a placeholder `.tm7` and
describe it as an up-to-date threat assessment of an unimplemented design.
