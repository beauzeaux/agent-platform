# test - business brief

## Mission and delivery boundary

Build a new campaign-based User Access Review (UAR) application, provisionally
named **test**. Replace fragmented spreadsheets and email with accountable
scoping, population review, exception handling and defensible evidence.
Change Management (CM) is a separate later epic whose business policy is
incomplete. The application supports compliance work; neither this experiment
nor an agent's approval certifies regulatory compliance.

Start from an empty codebase. This brief is the product authority supplied to
the team. Do not search for another implementation, infer undocumented behavior,
or treat missing requirements as permission to invent business policy.

The first delivery horizon is a demonstrable end-to-end slice: campaign kickoff,
asset scoping, one governed access population, eligible reviewer assignment,
attributable decisions, and the supporting exception/notification experiences.
Foundational authorization, concurrency, SoD and activity attribution are real,
even when the inputs and external adapters are synthetic.

## People

- Engineering team leads scope their assigned assets.
- Asset and security-group owners review assigned human access.
- Service-account owners handle later non-human mapping and review.
- Compliance administrators restore flow for exceptions and monitor campaigns.
- Auditors later inspect authorized, read-only evidence and historical state.

All people, groups, records, credentials, notifications and upstream outputs
used by this experiment must be synthetic.

## Required outcomes

| ID | Requirement and observable acceptance |
|---|---|
| T-01 | Authorized users can discover/select an allowed campaign. Campaign screens consistently show name, stage/status, deadline, SLA target and escalation context. An unauthorized user cannot read another campaign through the API or UI. |
| T-02 | A campaign receives a versioned asset roster from a governed upstream output. Preserve source identifiers, capture/copy times and prior-cycle context. Scope and population versions remain distinguishable and traceable. |
| T-03 | Assigned owners can include/exclude assets, supply required exclusion justification/evidence, delegate where permitted and attest/sign off. Compliance admins handle exclusions and unresolved ownership with attributable comments. Missing history must not erase current work. |
| T-04 | Controlled supplemental-asset intake can add previously missing assets during the permitted scoping window with required metadata, justification and applicable approval. Cutoff and duplicate handling are explicit. This is separate from uploading an access population. |
| T-05 | Copy governed, already-transformed access outputs into immutable accepted campaign snapshots. Report not-started/running/success/partial/failed, source/output/scope identities, freshness, counts, missing segments and attempt history. Retain failed/retried attempts; never silently publish partial data or overwrite accepted history. Source-record farming belongs upstream. |
| T-06 | An explicitly permitted manual population fallback validates file/schema/rows, metadata, source method and submitter attestation before publication. Report accepted/rejected counts and actionable errors. Replacement needs a reason and retained version history. File type/size and approval policy remain unresolved. |
| T-07 | Assign each review item to an authorized eligible reviewer with recorded basis. Prevent self-review across linked ordinary, alternate and privileged identities at assignment, delegation/reassignment and final decision submission. Unresolved identity, missing ownership or conflicts become visible exceptions; do not silently fail open. |
| T-08 | Reviewers see only their assigned records and can Approve, Deny with justification, and Delegate where policy permits. Capture approval basis, applicable evidence, actor, time, assignment/policy versions and provenance. Attestation requires valid decisions for all currently assigned items. Stale or unauthorized writes cannot overwrite newer work. |
| T-09 | Provide accessible, high-density review of at least 1,000 records with search/filter/sort/paging, relevant identity/access-path context, details/history and progress. Eligible bulk actions preserve individual attribution, required basis/justification and unaffected successes; bulk limits need an approved denominator and policy. |
| T-10 | In-app and email notifications support assignments, reminders, failures, deadlines and escalation with configurable recipients/cadence. Keep requested/delivered/failed/suppressed history and avoid duplicate occurrences. Emails deep-link to protected actions; replies do not decide reviews. Opening a notice does not complete work. Teams is later optional outreach. |
| T-11 | Every material mutation/system action has attributable, append-only audit history linked to campaign, scope/population, record, actor/system identity, time, reason, policy and operation/run identity as applicable. Preserve relevant prior/new states and concurrency outcomes. State and required audit facts cannot silently diverge on failure or retry. |
| T-12 | Evidence status, references, integrity information and access are governed. Later closure seals decisions/evidence/logs read-only; authorized auditor views and exports support point-in-time reconstruction. SOX-related retention is at least seven years, with disposition/legal-hold policy still requiring approval. A short test cannot prove seven years of retention or tamper resistance against every administrator. |
| T-13 | Later service-account workflows capture ownership, credential/key-vault relationships and access decisions with explicit eligibility/evidence rules. Required mapping and review policy must be agreed before implementation. |
| T-14 | Later remediation tracks required removals, verification, exceptions, justification and escalation. Whether the application executes removals or only tracks them is unresolved. Preserve history for lookback; do not invoke live revocation. |
| T-15 | Later scorecards, counts, trends and exports use named, versioned metric definitions, grains, denominators, timing and sources. Do not substitute UI guesses or telemetry counts for committed business facts. |
| T-16 | CM import/review is a separate later epic. Its owner, schema, period, reviewer policy, SLA, evidence and UAR relationship need approval. Do not block the UAR demonstration on CM. |
| T-17 | AI features are deferred. Any later recommendation is clearly advisory; final business review decisions remain human. Building this application with agents does not authorize agents to decide user access. |
| T-18 | Use an API-first .NET application, React web experience and SQL Server/Azure SQL-compatible mutable audit store. Keep cross-layer contracts provider-neutral; providers implement them. Backend owns authentication, authorization and orchestration. Browser code cannot directly access SQL, upstream analytics, identity APIs or evidence storage. Deployed identity is passwordless; test identity is explicitly synthetic. |
| T-19 | Volatile policy stays versioned/configurable rather than embedded in controllers/UI. Each normal read has a documented fixed query budget independent of result cardinality; test at representative sizes and reject N+1 behavior. |
| T-20 | Deliver small vertical slices with executable acceptance checks, concurrency/failure/retry tests, trustworthy activity and no false success. Upstream analytics is read-only. Diagnostic telemetry must not expose sensitive records or substitute for the product ledger. Environment-gated tests are reported as not run, not passed. |
| T-21 | Valid assigned work becomes independently actionable without a campaign-wide administrator acknowledgement. Administrators fix exceptional work; unresolved items do not gate unrelated healthy items. Required eligibility, source-readiness and stage controls still apply to each affected item. |
| T-22 | Provide a repeatable isolated development/test environment and metadata-only end-to-end diagnostics. Use only approved package feeds, synthetic data and disposable owned resources. Define performance thresholds as reviewed targets rather than invented business SLAs. |

## Engineering invariants

- SQL owns mutable campaign/review/evidence metadata and the activity record.
  Upstream analytics is read-only; external farming and synthetic upstream fixture
  provisioning are separate responsibilities.
- Shared domain/application/API contracts must not expose persistence-specific
  public types. Dependency direction and composition are architect-reviewed.
  Choose the smallest design meeting the requirements; no generic workflow engine
  or duplicate domain model without a demonstrated need.
- Server-side authorization is authoritative. Hidden UI buttons are not controls.
  Recheck current assignment, canonical identity, policy, stage and concurrency
  when saving, not only when reading a page.
- Each stage owns its transition conditions and repeat-safe, attributable writes.
  A shared campaign context is not authority for a central approval bottleneck.
- Persist accepted provenance and policy versions. Do not recompute historical
  decisions using today's policy and describe the result as original evidence.
- Preserve uncertainty: a failed/missing log or export is not proof of success.
  A checksum demonstrates consistency only within its actual trust boundary.

## Decisions requiring a business owner

Agents can propose options and implement approved seams, but cannot approve:

1. Published upstream schemas, stable identities, completeness/count/freshness
   semantics and publication/replacement contracts.
2. Assignment precedence, secondary-reviewer selection, canonical linked-account
   authority, conflict/OOO/management-level eligibility and override authority.
   Self-review prohibition must not be weakened by an invented override.
3. Delegation rules, approval bases, denial reasons, evidence-required cases,
   direct/group outcome reconciliation and bulk limits/denominators.
4. Manual upload types, maximum size, schema, attestations, submitter/approver
   authority and fallback/replacement eligibility; supplemental-asset questions.
5. Review windows, business/calendar time, notification templates/cadence/ladder,
   deadline dispositions and group-size threshold behavior.
6. Service-account policy, removal operating model, closure certification/signers,
   exceptions at closure, evidence package design and retention/disposition.
7. Canonical reporting metrics and all CM-specific governance.

Missing policy blocks the dependent work, not the entire project. Clearly marked
synthetic scenario configurations may demonstrate approved mechanisms; record
their status and never present them as production-approved policy.

Business guidance to reconcile includes a 25% bulk-selection cap, a 500-member
group threshold and two weeks of owner scoping followed by a third week of
compliance review. Preserve these as candidate versioned policy inputs; resolve
denominators, timing, applicability and exception behavior with the business owner
instead of silently changing them or embedding them as controller/UI constants.
