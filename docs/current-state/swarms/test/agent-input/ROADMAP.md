# test - outcome roadmap and seed work packages

All outcomes start unimplemented. The PM decomposes this roadmap into small
work packages with requirement IDs and runnable acceptance, not speculative
infrastructure or claims about another product.

## Horizons

| Horizon | Outcome | Requirements |
|---|---|---|
| Foundation | Minimal dependency boundaries, contracts, protected API/UI, isolated SQL test loop, attribution, query-budget tests and initial documentation/flow/threat-model outlines | T-01, T-11, T-18-22 |
| First demonstration | Kickoff and campaign navigation; roster receipt; owner scoping; compliance exception/exclusion experience | T-01-03, T-21 |
| Intermediate demonstration | One accepted access snapshot, import visibility, eligible assignment, personal review/attestation, baseline outreach; permitted fallback and supplemental intake | T-04-10 |
| Later UAR completion | Operational completion, service-account workflows, remediation/lookback, evidence packaging, closure/auditor access and reporting | T-12-15 |
| Separate later epic | Governed CM population and review | T-16 |
| Deferred / optional | Advisory AI deferred; Teams outreach optional after in-app/email | T-10, T-17 |

Foundational SoD and audit history are not deferred just because complete
evidence packaging is later. Do not claim the first slice delivers the whole BRD.

## Seed work packages

These are planning inputs, not queued tasks or permission to start.

| Key | Outcome / lead | Prerequisites | Smallest useful acceptance |
|---|---|---|---|
| P-00 | PM maps requirements, decisions and first vertical slice | None | Every first-slice requirement has an owner, acceptance/check owner and known blockers; no invented policy |
| P-01 | Architect establishes minimal dependency rules, versioned contracts and composition | P-00 | API/core, persistence and UX can consume the same reviewed contracts independently; policy/identity/provenance seams explicit |
| P-02 | SDET-contract defines independent fixtures/oracles; specialists define control cases | P-00; align with P-01 when contracts settle | Synthetic personas include linked-account conflict, missing owner and independent healthy item; failure expectations trace to requirements |
| P-03 | API/core implements protected kickoff/context and first scoping behavior | P-01 and approved slice decisions | Authorized outcome succeeds; wrong owner/campaign, stale write and missing justification are refused without state loss |
| P-04 | Persistence implements the same contracts, migrations, transactional state/audit and query instrumentation | P-01 and approved slice decisions | Real disposable SQL tests prove restart persistence, atomic failure, repeat-safe writes, concurrency and fixed read-query budget |
| P-05 | UX implements campaign shell and assigned scoping against approved API contracts | P-01 | Mock-contract UX checks can run before backend completion; integrated checks do not mock away authorization or write outcomes |
| P-06 | SDETs test an immutable integrated candidate in independent copies using different model families | P-02, P-03, P-04, P-05 | SQL/API and browser journeys pass with actual model IDs and receipts pinned to exactly the same candidate and fixture/policy versions |
| P-09 | User-documentation writer delivers candidate-accurate role/task guides | P-05, P-06 | SDET-journey has executed the documented steps; no proposed feature is described as supported |
| P-10 | Developer-documentation writer explains maintainer setup and code/control paths | P-03, P-04, P-05, P-06 | SDET-contract verifies commands/mappings; code/API links and audit evidence references resolve |
| P-11 | Flow/model steward maintains Markdown/Mermaid key flows and native threat model | P-01; refresh against P-03, P-04, P-05 and P-06 | Stable flow IDs map implemented code to sequences/DFD/security tests; portable checks and required native TMT receipts exist |
| P-07 | Architect, SoD and trail specialists independently evaluate candidate and documentation/model evidence | P-06, P-09, P-10, P-11 | Each returns pass/changes requested/blocked with exact commit, requirements, evidence and untested boundaries |
| P-08 | PM proposes first-slice disposition and next population/assignment slice | P-07 with all required gates passed | Honest coverage/gaps and next dependency-ready work; integration remains a trusted human/coordinator action |

P-03, P-04 and P-05 are intentionally parallel after contract approval.
P-02 can design cases while contracts settle; executable tests bind to the
reviewed contract version. A missing upstream or business decision becomes a
specific blocked work item, not an administrator queue for unrelated work.

P-09 through P-11 can draft outlines after P-01; their listed dependencies
govern candidate-accurate completion, not the earliest drafting time. Keep stable
work-package IDs even though final review follows those documentation packages.
DOCUMENTATION.md defines ownership, update triggers, native-model validation
and independently reviewed no-impact handling for unchanged artifacts.

## Required work-package fields

Every implementation task must identify:

- Stable task ID; goal; requirement and approved policy/contract versions.
- Owning role, allowed files, exact base/candidate commit and dependencies.
- Assigned model; SDET assignments preserve cross-model-family diversity.
- High-capability reviewer assignments preserve Sol/Argon family diversity.
- Inputs and expected artifacts; non-goals and explicit open decisions.
- Given/when/then acceptance with positive and refusal/failure cases.
- Reviewed validation command, fixture/environment needs and fixed query budget.
- Independent reviewers/testers and required completion receipts.
- User/developer/flow/threat-model impact and the responsible documentation owners.
- Bounded rework allowance and stop/escalation conditions.

An author saying "done" is not a test result. PM completion summaries cannot
override missing receipts, audit findings or business decisions.

## Candidate acceptance

All checks refer to the exact candidate commit, requirement/contract/policy
versions, test overlay, fixture version and tool/runtime versions.

1. Author's focused tests/build/format checks succeed.
2. Architect approves layering, consistency, minimality and contract adherence.
   The code/control reviewer assignments collectively preserve distinct
   high-capability Sol/Argon families and report actual model IDs.
3. SDET-contract proves API/persistence semantics against real disposable SQL.
4. SDET-journey, using a different model family, proves role-aware browser behavior
   and error/partial-failure UX. Both report actual model IDs; neither may silently
   fall back to the other's model.
5. SoD reviewer independently validates current assignment and linked-identity
   controls, including refused direct API attempts.
6. Trail reviewer reconciles committed business facts with audit/evidence output
   across success, failure, retry and restart, declaring limits of integrity proof.
7. SDETs verify affected user instructions, developer commands and audit mappings;
   the architect independently reviews flow/sequence/model consistency.
8. The trusted Windows validator supplies native TMT compatibility evidence for
   the baseline model and affected model/tool/template changes. Missing native
   evidence is blocked/not-run, never a successful skip.
9. Trusted integration owner verifies all receipts and approves integration.

Receipts use pass, fail or blocked/not-run; never a pass for an unavailable
environment. Candidate changes invalidate affected approvals/tests. Scope or
shared-contract changes go back to the architect and impacted owners, not an
unbounded author-fixes-every-layer loop.
