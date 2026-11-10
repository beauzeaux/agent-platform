# Documentation and native threat-model tooling - operator research

Researched 2026-11-05. This record is **not agent input** and does not authorize
package installation, skill activation, inference, model generation, desktop
automation or swarm execution. No tools were installed or run on project data.
Research used public documentation and pinned public source, not product code.

## Recommended implementation

Add three narrowly scoped documentation authors, not another application coder:

| Role | Inputs and outputs | Tool implementation to reuse |
|---|---|---|
| User documentation | Approved candidate UI/API and synthetic SDET journey evidence -> task-based Markdown guides | Confined repository/evidence tools, existing Markdown/link checks and SDET walkthroughs |
| Developer documentation | Candidate code, contracts, generated API reference, architect decisions and test receipts -> maintainer walkthroughs and control/evidence index | Existing repository primitives, first-party ASP.NET Core OpenAPI generation, SDET clean-environment command checks |
| Flows/threat model | Candidate architecture/code and control evidence -> Markdown/Mermaid sequences, native `test.tm7`, model input spec and Markdown security report | Mermaid CLI plus Microsoft HVE Core's existing TM7 generation/update/report tooling; separately authorized native Windows validation |

Keep Markdown in the repository. No new documentation portal, graph service,
or bespoke TM7 serializer is justified. Documentation examples/generator code
that require implementation changes remain tasks for the existing mai-code-2
authors; the three new roles cannot edit production code.

The user's later reviewer-diversity correction selects gpt-6.1-sol for the
architect/trail reviewer and gemini-4-argon for the SoD reviewer. A subsequent
delegated investigation established that upstream enables Argon with tool calls
but only `/chat/completions`; the old Responses-only gateway filtered it out.
Native Chat routing is now implemented and passed fixture tests plus an isolated
published metadata canary. The user-approved production cutover is complete:
LLM/ADO/MCP are ready and production discovery advertises exact Chat-only Argon.
Private bridge/actor Chat support remains a separate gate. Actual inference/tool
behavior is untested; there is no silent fallback. Documentation authors and the PM
retain proposed gpt-5.6-sol; production authors retain required mai-code-2.
The later SDET correction selects proposed gpt-6.1-terra for API/SQL testing
and grok-4.7 for browser/journey testing, requiring distinct model families.
Both were advertised by read-only discovery; tool-capable inference remains
an authorized preflight gate, not a result of this research.

## Verified reusable assets

### Mermaid CLI

Upstream [`mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli/tree/1423a0d7967d275a9d360ba0617f569550827c8b)
is MIT licensed. Its README documents rendering Mermaid source to SVG/PNG/PDF
and processing Mermaid blocks embedded in Markdown:

`mmdc -i input.mmd -o output.svg`

Use the reviewed local executable, offline fonts/assets, strict trusted config
and confined outputs. Do not copy upstream public npm/npx installation examples
into the runtime policy: Node dependency acquisition must use the approved 1ES
feed and actor checks use prefetched caches. Do not use a public diagram renderer.
An actual package/browser version and offline rendering proof remain launch work;
the source revision is evidence, not an installed executable pin.

Source: [pinned README](https://github.com/mermaid-js/mermaid-cli/blob/1423a0d7967d275a9d360ba0617f569550827c8b/README.md).

### Maintainer API documentation

ASP.NET Core provides first-party OpenAPI support through
`Microsoft.AspNetCore.OpenApi`; the documented build-time path also uses
`Microsoft.Extensions.ApiDescription.Server`. Prefer an approved generated
artifact over launching the app with valuable credentials or hand-copying API
routes. OpenAPI describes the API surface, not the entire code/control flow.
Any project integration remains an architect-approved coding task.

Source: [Microsoft Learn OpenAPI overview](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/openapi/overview?view=aspnetcore-10.0).

### Native TM7 generation and update

Microsoft-owned [`microsoft/hve-core`](https://github.com/microsoft/hve-core/tree/be6ab3795eb7fe7c735a7ba95f2e288209636c8b)
is MIT licensed. The reviewed source pin is
`be6ab3795eb7fe7c735a7ba95f2e288209636c8b`. Its security-planning package
already contains a deterministic TM7 generator, existing neutral input contract,
bundled template/profile assets, Markdown report generator, regression tests
and a Windows TMT validation harness.

This is reusable repository tooling, **not** a documented Microsoft TMT product
automation API or an activated skill in this session. Keep its version, assets,
license notices and dependency contract together; do not copy a few snippets
into a new serializer or silently track moving main.

Reviewed entry points under
`.github/skills/project-planning/security-planning`:

| Existing file | Verified contract |
|---|---|
| `scripts/generate_tm7.py` | Spec argument, `-o`, `--template`, `--mode`, `--update` and `--overlay-input`; emits actual DataContract-based TM7 with embedded knowledge base |
| `scripts/generate_markdown.py` | Same spec argument and `-o`; builds a Markdown STRIDE/NIST/security-test report using the generator's payload logic |
| `scripts/tm7_threat_contract.py` | Shared hardened XML and explicit threat/mapping contract, consumed by generator/population tools |
| `scripts/populate_tm7_threats.py` | Existing post-generation explicit threat population and expected-count guard; not a fictional standalone `validate_tm7.py` CLI |
| `scripts/validate_tm7_with_tmt.py` | Windows-local native probe/validation/feedback workflow with version, evidence and timeout contracts |
| `pyproject.toml`, `uv.lock`, assets and tests | Python >=3.11, pinned PyYAML/defusedxml, optional Windows UI automation dependencies, required profile/template assets and regression coverage |

The neutral input already models representations, components, assets, trust
zones, data flows, threats, mitigations, abuse cases and security-test cases.
Use that contract rather than inventing a parallel threat-model schema.
Stable IDs must also be referenced by the project's flow catalog.

The format reference declares its OTM-related attribution and applicable
CC BY-SA terms. Review and preserve relevant notices when adopting assets or
adapting schema material; do not assume the repository's MIT badge erases
asset-specific provenance. This plan paraphrases the interface and links source;
it does not vendor that schema or license text.

Sources: [package README](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/README.md),
[format/workflow reference](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/references/tm7-generation.md),
[generator](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/scripts/generate_tm7.py),
[Markdown generator](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/scripts/generate_markdown.py),
[dependency manifest](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/pyproject.toml).

## Important update limitations

Source inspection of `generate_tm7.py`, `merge_model` (lines 5445-5487 at the
pin), confirms matching IDs preserve element positions/notes and flow position/
retention. Matching threats preserve non-default reviewed state and notes.
This is useful existing behavior, **not** proof every native field or removed
threat is retained. The merge iterates the new spec's threats; deleted/unmatched
reviewed entries need explicit reconciliation and retained history.

The Markdown generator reads the spec, not the merged TM7 output. A native
review state/notes change can therefore leave the Markdown report stale unless
an approved reconciliation updates the spec. Stable IDs alone also do not prove
that an old mitigation still applies after a flow/control change.

The desired bounded validator should reuse these parsers/mapping functions and
tests, adding only necessary project coverage/semantic-diff glue. It must flag
lost threats, changed boundaries/endpoints, stale dispositions and mismatched
input/output hashes. No universal preservation guarantee has been tested here.

## Native Windows compatibility is a separate gate

Microsoft Learn describes TMT as a desktop tool for DFDs, trust boundaries,
STRIDE analysis and managed threat dispositions. A Mermaid sequence diagram is
not the same representation. Map shared identities and interactions explicitly;
do not claim a sequence-to-XML conversion constitutes a threat model.

The HVE harness documents a default pinned TMT version **7.3.51110.1** and an
interactive, unlocked Windows desktop. Its feedback automation may open/close/
reopen TMT and hold mouse/keyboard control. Do not run that on this user's active
desktop without separate explicit authorization. Prefer a dedicated authorized
Windows validation desktop; do not add a host-control service merely for this.

The documented initial check is `--mode probe`. Native validation additionally
needs an owned evidence directory, the matching model/spec and Windows
dependencies. Use `--require-tmt` and inspect the recorded status: absence can
otherwise produce `skipped` with exit zero. The harness's
`automated-ready-pending-human` means automated gates passed, not review approval.
Screenshots are not text-redacted and require synthetic content and local handling.
Do not silently upgrade tools/templates, delete stale threats or auto-promote
layout overlays.

Require genuine open/inspect/save/reopen evidence, semantic identity/disposition
comparison and independent human review of any required risk acceptance.
Headless cluster parsing/rendering can precede this check but cannot replace it.

Sources: [Microsoft TMT overview](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool),
[TMT getting started](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-getting-started),
[pinned HVE native workflow reference](https://github.com/microsoft/hve-core/blob/be6ab3795eb7fe7c735a7ba95f2e288209636c8b/.github/skills/project-planning/security-planning/references/tm7-generation.md).

## Remaining implementation/proof work

Before activation, acquire/review pinned tooling through approved channels,
provide offline dependencies and enforce confined file/operation boundaries.
Exercise a public synthetic fixture: generate a model/report, modify a flow,
update against a prior reviewed model, and prove preservation/review of threat
dispositions and removals. Check malformed/oversized XML/specs and wrong-path
requests with the existing regression coverage. Prove real Mermaid rendering.

Then separately prove native TMT open/save/reopen on the authorized Windows
runner, preserving IDs, notes, states, threat/mitigation content and meaningful
layout. Store version/hash-bound receipts and distinguish portable success,
native success, missing native proof and pending human review.

Those operations have **not** been executed here. No application architecture
exists yet, so no fake placeholder `.tm7` is presented as a current project model.
The inactive packet specifies how the new steward will maintain the real artifact.
