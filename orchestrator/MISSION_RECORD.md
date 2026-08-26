# Zentra Mission Record V1

This document specifies the durable, machine-readable operational state for
one Zentra Autonomous Engineering V1 (Level 2) mission. It is the design
rationale for `orchestrator/schemas/mission_record.schema.json`, which is
the enforceable artifact — a Python validator (not built in this increment)
will eventually reject any record that does not satisfy that schema, without
needing an LLM to interpret it.

This is schema and contract design only. No orchestrator code, state
machine, or agent invocation exists yet. Nothing here calls David, Emilio,
or Emma, and nothing here integrates with GitHub or Render.

## Design principles this record follows

1. The Mission Record stores state; it does not make decisions. "What
   happens next" is always derivable from `state` by an external,
   deterministic transition table (not yet built) — this record never
   caches a decision about what should happen next.
2. Chugel, once built, is deterministic code operating over this record. No
   field in this schema requires an LLM to interpret.
3. LLM prose is never authoritative over a structured field. Every claim
   that matters for a decision (a review verdict, an authorization, an
   artifact identity) is a typed, enum-constrained, or pattern-constrained
   field — never a sentence Chugel would have to parse for meaning.
4. Human authorization is explicit and auditable, or it does not exist.
5. Absence of authorization means **not authorized** — every gate defaults
   to `not_requested`, never to an implicit approval.
6. Every field needed to validate a state transition deterministically is
   present in the record (this increment stops short of writing the
   transition table itself, per scope).
7. The record fails closed: an incomplete, malformed, contradictory, or
   wrong-schema-version record is rejected outright, never leniently
   reinterpreted.
8. Nothing is inferred from conversation tone, prior missions, agent output,
   or elapsed time. Every fact traces to a specific evidence field.
9. Emma's verdict is a literal enum value (`PASS` /
   `PASS_WITH_NON_BLOCKING_FINDINGS` / `CHANGES_REQUIRED` / `BLOCKED`),
   never inferred from a findings list or a summary sentence.
10. Emilio's handoff is stored as evidence (`builder_evidence[]`), with his
    own conclusion explicitly labeled and never used as an approval signal
    anywhere in the schema.
11. `corrective_cycle_count` is hard-capped at 1 in the schema itself
    (`maximum: 1`), and both `builder_evidence` and `reviewer_evidence` are
    hard-capped at 2 entries each (`maxItems: 2`) — matching `AGENTS.md`'s
    one-bounded-corrective-cycle rule at the schema level, not just by
    convention.
12. Re-planning proposals are structurally separate from the authorized
    scope and can never merge into it without a new, explicit human
    authorization event (see "Re-planning model" below).
13. Every place mission *knowledge* (not raw command output) is persisted
    carries a `FACT | INFERENCE | ASSUMPTION | INTENT` label, per
    `agents/AGENT_STANDARD.md` section 9.
14. Resume safety is derived from primitive evidence already in the record
    (see "Resume and idempotency" below) — this schema deliberately does
    **not** include a cached "safe to resume" flag, because a cached
    judgment can go stale and be trusted wrongly; only primitives that can
    be independently re-verified are stored.
15. Kept minimal: no field exists here "for later." Every field answers one
    of the goal-list questions this increment was asked to satisfy, or is
    structurally required to make another field auditable (e.g. an
    `approved_for` scoping object, so a stale approval can be detected).

## Source authority re-read for this increment

`AGENTS.md`, `agents/AGENT_STANDARD.md`, `agents/emilio/CONTRACT.md`,
`agents/emma/CONTRACT.md`, `docs/zentra/BUILDER_V1.md`, `docs/zentra/
REVIEWER_QA_V1.md`, `docs/zentra/HANDOFF_TEMPLATE.md`, and `docs/zentra/
MASTER_ROADMAP.md` were re-read fresh before this design. Field names and
enum values below are traceable to that reading, not invented — in
particular: the `REPO-VERIFIED` / `HUMAN-CONFIRMED` / `UNVERIFIED` labeling
convention already established in `MASTER_ROADMAP.md`, and the "máximo una
misión de implementación activa a la vez" rule in its "MISSION PROTOCOL"
section, both directly motivated this schema's single-mission-per-record
design and its `evidence_label` vocabulary (renamed to the FACT/INFERENCE/
ASSUMPTION/INTENT terms `agents/AGENT_STANDARD.md` and both knowledge maps
already use, since that vocabulary — not the roadmap's three-tier one — is
what governs *agent* claims specifically).

## Canonical state vocabulary

Twenty-one states. Each entry states its meaning, the actor who normally
owns work in that state, and the minimum evidence that must already exist
in the record before a (future, not-yet-built) Chugel may enter it.

| State | Meaning | Normal owner | Minimum entry evidence |
|---|---|---|---|
| `INTAKE` | David is turning José's raw idea into a drafted mission. | David | `intent.raw_text` present. |
| `SCOPE_AWAITING_AUTHORIZATION` | David's draft is ready; waiting on José. | José (human gate) | A candidate `mission_definition_history` entry exists with `authorized_by`/`authorized_at` still unset (i.e. drafted, not yet authorized). |
| `AUTHORIZED` | José approved the scope; work has not started. | Chugel (transitional) | `human_gates.scope_authorization.status == "approved"`, matching the drafted version now recorded in `mission_definition_history`. |
| `BUILDING` | Emilio is implementing. | Emilio | `repository.isolation_confirmed == true`; `mission_definition_history` non-empty. |
| `VERIFYING` | Deterministic checks (tests, `git diff --check`, `zentra_verify.py`) run against Emilio's handoff. This is Chugel's own deterministic work, not an LLM step. | Chugel | A `builder_evidence[]` entry with a resolved `artifact` identity. |
| `AWAITING_REVIEW` | Checks are done; ready for Emma. | Chugel | The same `builder_evidence[]` entry has `checks` populated and its artifact identity re-confirmed unchanged. |
| `REVIEWING` | Emma is independently reviewing, in a fresh context. | Emma | A `reviewer_evidence[]` entry exists with `artifact_identity_confirmed_at_start` matching the `builder_evidence[]` artifact under review. |
| `CHANGES_REQUIRED` | Emma returned a blocking verdict and the one bounded corrective cycle has not yet been used. | Chugel (transitional) | `reviewer_evidence[-1].verdict == "CHANGES_REQUIRED"` and `corrective_cycle_count == 0`. |
| `CORRECTING` | Emilio is addressing only the cited findings. | Emilio | `corrective_cycle_count` incremented to `1`; the triggering `reviewer_evidence[-1].findings` is non-empty. |
| `PUBLISH_AWAITING_AUTHORIZATION` | Emma returned a passing verdict; waiting on José to authorize commit/push/PR. | José (human gate) | `reviewer_evidence[-1].verdict` is `PASS` or `PASS_WITH_NON_BLOCKING_FINDINGS`. |
| `PUBLISHING` | Chugel commits (if not already committed), pushes, and opens a Draft PR. | Chugel | `human_gates.publish_authorization.status == "approved"`, scoped to the reviewed artifact. |
| `CI_PENDING` | Waiting on GitHub Actions. | Chugel (poll only) | `publish.pr_number` set. |
| `MERGE_AWAITING_AUTHORIZATION` | CI is green; waiting on José to authorize merge. | José (human gate) | `publish.ci_runs[-1].conclusion == "success"`. |
| `MERGING` | Chugel marks the PR ready and merges with a merge commit. | Chugel | `human_gates.merge_authorization.status == "approved"`, scoped to the exact head/base SHAs revalidated immediately prior. |
| `MERGED` | The merge commit exists on `main`. Deploy not yet confirmed. | Chugel | `merge.merge_commit_sha` set. |
| `DEPLOY_PENDING` | Waiting on the platform's own auto-deploy (never triggered by this system). | Chugel (poll only) | `merge.merge_commit_sha` set. |
| `VERIFYING_PRODUCTION` | Reading (never mutating) the platform's deploy-event log and `/health`/`/version`. | Chugel | `deploy.expected_sha` set to the merge commit SHA. |
| `COMPLETED` | Deploy confirmed for the exact expected SHA, `/health` healthy. Terminal, success. | — | `deploy.deploy_confirmed_at` set; `deploy.version_check` body matches `deploy.expected_sha`. |
| `BLOCKED` | Escalated; requires a human decision to resume. Not necessarily terminal. | José | `state_reason` names the exact blocking condition. |
| `FAILED` | Unrecoverable within this mission's bounded retries. Terminal; a human decides whether to open a new mission. | José | `state_reason` names the exact failure. |
| `CANCELLED` | José explicitly abandoned the mission. Terminal. | José | A `state_history` entry with `actor == "jose"` and a stated reason. |
| `ROLLED_BACK` | José reverted a completed mission's production change. Terminal; reachable only from `COMPLETED`. | José | A `state_history` entry with `actor == "jose"` and a stated reason. |

Two deliberate departures from José's suggested minimal list, both
justified against what this session's real missions actually did, not
speculation:

- **`MERGED`, `DEPLOY_PENDING`, `VERIFYING_PRODUCTION`, `COMPLETED`** are
  kept as four distinct states rather than collapsed into one "merged/done."
  Every real mission this session (Phase 1 memory instrumentation, worker
  memory protection, the Agent Standard commits) treated "merged" and
  "deploy verified in production" as separate, separately-gated facts —
  `git log` shows the merge commit lands before Render's auto-deploy
  completes, and production `/health`/`/version` were always checked
  afterward as their own step. Collapsing these would lose exactly the
  distinction the goal list asks for ("What happened if execution stopped
  or failed?" needs to distinguish "merged but deploy unconfirmed" from
  "fully verified").
- **`ROLLED_BACK`** is kept, reachable only from `COMPLETED`, because a
  human-initiated production rollback is a real, already-practiced action
  (`git revert` after a bad deploy), not a hypothetical.

## Mission Record structure (top level)

See `orchestrator/schemas/mission_record.schema.json` for the enforceable
shape. Summary of each top-level section and which goal-list question it
answers:

- **Identity** (`schema_version`, `mission_id`, `created_at`, `updated_at`) —
  "What mission is this?"
- **`state`, `state_reason`, `state_history`** — "What state is the mission
  currently in?" and "Why did the mission transition states?"
- **`intent`** — the immutable anchor for "What did José authorize?"
- **`mission_definition_history`** (dedicated section, per the prompt's
  suggestion to separate identity concerns) — "What did José authorize?"
  and "What is explicitly out of scope?" (via `non_goals`) and "What
  acceptance criteria apply?"
- **`proposed_scope_changes`** — the re-planning model (below).
- **`human_gates`** — the three Level 2 gates (below).
- **`repository`** (dedicated section) — "What artifact/commit/worktree/
  branch is being operated on?" (base identity; per-attempt artifact
  identity lives in `builder_evidence[].artifact`).
- **`builder_evidence`**, **`reviewer_evidence`** — "What evidence has been
  produced?", "What review verdict was returned?", "Are there blocking
  findings?"
- **`corrective_cycle_count`** — "How many corrective cycles have
  occurred?"
- **`publish`, `merge`, `deploy`** — artifact/CI/deploy identity, answering
  the remaining parts of "What artifact/commit... is being operated on?"
- **`budget`** — "What budget has been consumed?"

"Which agent owns the current step?" and "What action is allowed next?" are
**intentionally not stored fields** — both are pure functions of `state`
(see the vocabulary table's "Normal owner" column and Design Principle 1).
Storing them would create a second, cacheable copy of something the state
already determines, and caches go stale.

## Human gates

Three gates, each independently tracked, each defaulting to `not_requested`
— a record missing any of the three is rejected by the schema (`required`
at the `human_gates` level), so "the gate field was simply omitted" can
never be silently read as "approved."

Each gate's `status` is one of `not_requested | pending | approved |
rejected`. An `approved` status is schema-conditioned (`if/then`) to
require `decided_by == "jose"` (the only literal value the schema accepts
there — no agent name is a valid value), `decided_at`, `decision_ref`, and
`approved_for` all present. `decision_ref` is a pointer into an external
audit trail (not yet built), not the verbatim message — this keeps the
Mission Record itself free of potentially sensitive conversational content
while still making every decision traceable.

**`approved_for`** is the scoping mechanism that prevents a stale approval
from being silently reused: it records exactly which SHA/artifact/PR the
approval was granted for. A future Chugel must treat an approval whose
`approved_for` no longer matches the current artifact as **not approved**,
never as still valid — this is a documented rule for that future code, not
something the schema alone can enforce (JSON Schema cannot compare a gate's
`approved_for` against a value elsewhere in the same document across a
mutation over time).

## Artifact / evidence identity model

Two identity concepts are kept deliberately separate, matching how
`BUILDER_V1.md` and `REVIEWER_QA_V1.md` already distinguish them:

- **`repository`** — the isolated worktree/branch/base SHA a mission
  operates in as a whole. Set once, early, rarely re-examined.
- **`artifact_identity`** (a shared definition used inside each
  `builder_evidence[]` and `reviewer_evidence[]` entry) — the specific
  reviewable unit for *one* build/review attempt: either an immutable
  commit SHA, or a captured patch (path + SHA-256 + byte size), matching
  `HANDOFF_TEMPLATE.md`'s "Review artifact identity" section exactly. The
  schema enforces that a `commit` mode has a SHA and nulls for the patch
  fields, and a `patch` mode has all three patch fields and a null commit
  SHA — the two modes can never be mixed or left ambiguous.

Every `reviewer_evidence[]` entry carries the artifact identity **twice** —
once confirmed at the start of review, once confirmed immediately before
concluding — mirroring `REVIEWER_QA_V1.md` step 9 exactly ("Immediately
before concluding, validate the same artifact identity again. Any commit,
patch digest, byte-size, or worktree change invalidates the review"). The
schema cannot itself assert these two objects are equal (JSON Schema has no
general cross-field equality operator), so this is documented here as a
mandatory rule for the future deterministic validator layer described
below, not something a reader can assume from the schema file alone.

**A note on what JSON Schema can and cannot enforce**, stated plainly so
nobody mistakes schema-validity for full correctness: this schema enforces
structure, types, enums, required fields, and the numeric/pattern bounds
that don't require comparing two different parts of the document. It does
**not** enforce, and a future non-LLM Python validator layer must:

- that the two artifact-identity snapshots in a `reviewer_evidence[]` entry
  are actually equal (or that `verdict` is forced to `BLOCKED` when they
  are not);
- that a `human_gates.*.approved_for` still matches the current artifact
  before that approval is treated as live;
- that `state` is only ever set to a value the (not-yet-built) transition
  table actually allows from the current `state_history[-1].to_state`.

## Corrective-cycle model

`corrective_cycle_count` is capped at `1` directly in the schema
(`maximum: 1`) — not by convention, by construction; a record claiming `2`
fails validation (verified below). `builder_evidence` and `reviewer_evidence`
are each capped at two entries (`maxItems: 2`), so even a would-be third
build or review attempt cannot be represented at all, let alone accepted.
This directly answers the self-challenge question "Could two corrective
cycles occur when only one is allowed?" — not at the record level; a
system that tried would produce a record the schema itself refuses.

## Re-planning model

Requirements from the mission: re-planning cannot invisibly mutate
authorized scope; proposed changes must be distinguishable from authorized
scope; an expansion needs new explicit human authorization; history stays
auditable.

Design:

- `mission_definition_history` is **append-only and versioned**
  (`version: 1, 2, 3, ...`). The currently authorized scope is always
  `mission_definition_history[-1]`. Earlier versions are never deleted or
  edited — they remain as a permanent record of what was authorized before.
- Every entry requires `authorized_by: "jose"` (the schema's `const`
  constraint makes any other value invalid) and an `authorized_at` /
  `authorization_decision_ref`. **An agent can never appear as the
  authorizer of a mission definition version** — this is enforced at the
  schema level, not by convention.
- `proposed_scope_changes` is a **separate array**. A David re-planning
  proposal lives here, with `status: pending_human_decision | accepted |
  rejected`, and never touches `mission_definition_history` directly. Only
  when a proposal's `status` becomes `accepted` — which itself requires
  `decided_by: "jose"` — does a *new* `mission_definition_history` entry
  get appended, with `source: "david_replan"` and
  `based_on_proposal_id` pointing back to the proposal that produced it
  (schema-enforced: `david_replan` requires a non-null
  `based_on_proposal_id`; `david_intake` requires it to be null).
- Nothing in the schema allows a proposal to silently become the current
  scope. The only path from "proposed" to "authorized" is through a new,
  independently auditable `mission_definition_history` entry carrying its
  own human authorization evidence.

This directly satisfies "Could an agent rewrite previously authorized
scope?" — no: the schema has no field an agent could write that overwrites
`mission_definition_history[N]`; the array only grows.

## Budget representation

No numeric limit is invented. `budget.configured` is `null` when nothing is
configured — an **explicit**, visible null, not an absent field (the field
itself is required by the schema; only its value may be null). `budget.
consumed` is always tracked regardless of whether a ceiling exists, so "What
budget has been consumed?" stays answerable even with no configured ceiling.
`exhausted` is schema-conditioned: whenever `configured` is `null`,
`exhausted` **must** be `false` — there is no ceiling to exhaust, and the
schema refuses a record that claims otherwise. This directly closes the
self-challenge question "Could missing budget information accidentally mean
unlimited budget?" — the record always makes the absence of a ceiling
explicit and still requires consumption to be tracked; nothing about a null
ceiling silently implies "don't bother counting."

## Resume and idempotency

No cached "safe to resume" boolean exists in this schema (Design Principle
14). Instead, resumability is meant to be **derived** by a future reader
from primitives already present:

- **Presence of an identity value is the idempotency signal.** `publish.
  commit_sha`, `publish.pr_number`, `merge.merge_commit_sha`, and `deploy.
  deploy_confirmed_at` are all `null` until the corresponding real action is
  independently confirmed to have happened. A future Chugel resuming after
  a crash checks these fields *and* re-verifies them against the real
  system (git, GitHub, Render) before deciding whether to act — the record
  never claims something happened without also being checkable.
- **`state_history` is append-only and ordered**, so "what was the last
  confirmed transition" is always answerable without guessing.
- **`corrective_cycle_count` and the `maxItems: 2` caps** mean a resumed
  process can never be tricked into performing a third build or review
  attempt even if it mishandles its own resume logic — the record itself
  refuses to represent that state.

This is a documented resume *procedure* for a future implementation, not
code delivered in this increment.

## FACT / INFERENCE / ASSUMPTION / INTENT placement

The `evidence_label` enum (`FACT | INFERENCE | ASSUMPTION | INTENT`) is used
wherever mission *knowledge* — as opposed to raw command output — is
persisted: `proposed_scope_changes[].label` (David's rationale for a
re-planning proposal), `builder_evidence[].assumptions[].label` (anything
Emilio flags as an unverified premise), and `builder_evidence[].
conclusion.label` (Emilio's own summary, always labeled, never treated as
approval — `agents/emilio/CONTRACT.md` section 12 is explicit that a
Builder conclusion "is not approval"). Raw evidence — command exit codes,
test output strings, HTTP status codes — is not labeled, because it isn't a
claim requiring a confidence category; it's a direct observation.

## Self-challenge

- **Could Chugel accidentally proceed without José's approval?** No —
  every gate defaults to `not_requested`, and `approved` is schema-blocked
  without `decided_by: "jose"`, `decided_at`, and `decision_ref` all
  present. Verified by the `approved_gate_without_decision_evidence` test
  case below (rejected).
- **Could an agent rewrite previously authorized scope?**
  No — `mission_definition_history` only grows; every entry requires
  `authorized_by: "jose"`, schema-enforced via `const`. Verified by the
  `agent_self_authorized_scope` test case (rejected).
- **Could Emma's verdict be inferred rather than explicit?** No —
  `reviewer_evidence[].verdict` is a required, closed 4-value enum; nothing
  resembling free text is accepted there. Verified by the
  `reviewer_verdict_not_enum` test case (rejected).
- **Could a stale commit/branch be mistaken for the reviewed artifact?**
  Mitigated at the schema level (dual artifact-identity snapshots per
  review, full 40-char SHAs only, no short-SHA aliasing) and explicitly
  flagged above as requiring a non-schema equality check in the future
  validator layer — this is the one place I could not make the schema
  alone fully sufficient, and I said so rather than implying it was solved.
- **Could a mission resume into the wrong state?** Not resolved by this
  increment's schema alone (no state machine exists yet) — but the record
  never lets a resumed process invent progress: idempotency-relevant fields
  stay `null` until independently confirmed, so a resume procedure has
  primitives to re-verify against, not a claim to trust blindly.
- **Could two corrective cycles occur when only one is allowed?** No —
  schema-capped at the field level (`maximum: 1`, `maxItems: 2`). Verified
  by two test cases (rejected).
- **Could missing budget information accidentally mean unlimited budget?**
  No — `configured: null` is required to be explicit and paired with
  `exhausted: false`; `consumed` is always tracked. Verified by the
  `exhausted_true_with_no_configured_budget` test case (rejected).
- **Could malformed or future-version records be accepted silently?** No —
  `schema_version` is a `const`, `additionalProperties: false` everywhere,
  and every object rejects unknown fields. Verified by three test cases
  (rejected).
- **Could free-form prose become machine authority?** No field a
  transition would depend on (`state`, `verdict`, gate `status`,
  `corrective_cycle_count`, artifact identity) accepts free text — every one
  is an enum, a pattern, or a typed number. Free text exists only in
  clearly-labeled evidence fields (`rationale`, `conclusion.text`, findings
  `summary`) that a future Chugel would never read to make a decision.

## Validation performed

`orchestrator/schemas/mission_record.schema.json` was checked, using a
temporary local `jsonschema` installation in an isolated scratch virtualenv
(not added to this repository's dependencies, not committed anywhere):

1. **JSON syntax** — valid.
2. **JSON Schema draft-07 well-formedness** — `jsonschema.Draft7Validator.
   check_schema(...)` passes.
3. **Representative valid records** — a minimal fresh `INTAKE` record, and a
   fully populated record carrying a mission through `AUTHORIZED` →
   `BUILDING` → `VERIFYING` → `AWAITING_REVIEW` → `REVIEWING` →
   `PUBLISH_AWAITING_AUTHORIZATION` with a real `PASS` verdict — both
   validate successfully.
4. **Twelve deliberately malformed/unsafe records** — each targeting one of
   the self-challenge questions above (missing gate, wrong schema version,
   an `approved` gate without decision evidence, an agent authorizing its
   own scope, `corrective_cycle_count: 2`, a third build/review attempt, a
   free-text verdict, a `BLOCKED` verdict with no reason, `exhausted: true`
   with no configured budget, a short/invalid SHA, an unknown top-level
   field, and a state value outside the vocabulary) — **all twelve were
   correctly rejected**, none accepted.

All 14 cases behaved as expected (2 valid accepted, 12 invalid rejected).

## Ambiguities / open questions

- The equality check between a `reviewer_evidence[]` entry's two
  artifact-identity snapshots, and the freshness check on a
  `human_gates.*.approved_for` value, cannot be expressed in JSON Schema
  alone (no cross-document-position equality operator) — flagged above as
  required work for a future deterministic (still non-LLM) validator layer,
  not solved in this increment.
- `decision_ref` is specified as "a pointer into an external audit trail,"
  but that audit trail's own format is not designed in this increment
  (matches scope: audit trail design was not authorized here).
- Whether `budget.per_agent_consumed` should eventually gain a `david` key
  extended with sub-fields once David exists is left open — the current
  three-key shape (`david`, `emilio`, `emma`) already anticipates his
  existence without implementing anything about him, per the instruction
  not to create David in this increment.
