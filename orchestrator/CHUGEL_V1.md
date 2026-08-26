# Chugel V1 — Design

This document specifies Chugel V1: the deterministic record-keeper and
gate/state enforcer that persists a Mission Record to disk and mediates every
mutation to it through `orchestrator/validator.py`'s `validate_mission_record()`
and `orchestrator/state_machine.py`'s `can_transition()`.

This is design only. No code exists yet. `orchestrator/CHUGEL_V1.md` is the
only file this increment creates. `orchestrator/MISSION_RECORD.md`,
`orchestrator/schemas/mission_record.schema.json`, `orchestrator/validator.py`,
`orchestrator/state_machine.py`, `AGENTS.md`, and every existing agent
`CONTRACT.md` were re-read fresh for this design and are unmodified.

## What Chugel V1 is not

Per `agents/AGENT_STANDARD.md`'s explicit scope exclusion: Chugel is
deterministic orchestration code, not an LLM-based reasoning agent. It has no
`agents/chugel/` directory, no `CONTRACT.md`, no `IDENTITY.md`, no judgment,
and no authority beyond what is mechanically derivable from its inputs. Its
correct behavior is provable from those inputs, not a matter of interpretation.
Everything in this document describes ordinary Python code under
`orchestrator/`, in the same register as `validator.py`'s own module
docstring — never a persona, never something that "decides" in the sense
David/Emilio/Emma decide.

**Chugel V1 is explicitly a narrow slice, not the final architecture.** The
end goal of Zentra Autonomous Engineering V1 remains: José states a
high-level intent, and the system carries it through David intake, required
José authorization, Emilio build, Emma's independent review, a bounded
corrective cycle if needed, publish/merge/deploy gates, and production
verification, with the least human intervention reasonably necessary at each
step — never less oversight than the currently-authorized autonomy level of
each named agent permits. Chugel V1 does not build any of that flow's
automation. It builds the one piece every later piece will depend on: a
Mission Record that actually persists across turns and actually refuses an
unsafe mutation, instead of existing only as an in-memory test fixture. See
"Evolution Path" at the end of this document for what is deliberately left
undone and why, and what (not when) each remaining piece depends on.

## 1. Responsibilities and explicit non-responsibilities

### Responsibilities

Chugel V1 is responsible for exactly four things:

1. **Persisting** one Mission Record per mission as one JSON file under
   `orchestrator/missions/`, and nowhere else.
2. **Refusing** to persist any record that `validate_mission_record()` rejects.
3. **Refusing** any state transition that `can_transition()` rejects.
4. **Refusing** to record a human-gate decision unless the decision's
   `decided_by` field is literally `"jose"` — redundant with, never a
   substitute for, the schema's and validator's own enforcement of the same
   rule (defense-in-depth against a caller bug in the layer that constructs
   the decision payload before Chugel ever sees it).

### Explicit non-responsibilities

Chugel V1 does **not**:

- invoke David, Emilio, or Emma, in any form — no subprocess, no API call, no
  prompt construction; a human (today, José via chat with the assistant
  currently occupying the Emilio or Emma role) triggers every agent turn, and
  a human (or a human-run script, not built in V1) calls Chugel's functions
  to record what happened;
- decide *whether* a transition, gate approval, or corrective cycle *should*
  happen — it only decides whether a transition/approval/cycle that a caller
  is attempting to record is *legal* given the record's current state and
  evidence, per the already-existing pure functions;
- touch `database/proyecta.db` or any other product database, run a
  migration, or add a dependency;
- touch git, GitHub, CI, or Render in any way;
- expose a CLI (explicitly out of scope for V1, per your decision);
- read or write anything outside `orchestrator/missions/<mission_id>.json`
  for a mission's own state (module source under `orchestrator/` is read at
  import time as it already is by `validator.py`, not written);
- retry, poll, or run in a loop — every operation is a single synchronous
  call, invoked once per caller request, that either returns a result/raises
  immediately or does not run at all;
- interpret, summarize, or generate any free-text field (`state_reason`,
  `rationale`, `conclusion.text`, findings `summary`, etc.) — it stores and
  returns them verbatim, never reads them for a decision, exactly as
  `MISSION_RECORD.md`'s Design Principle 3 already requires of the schema
  itself.

## 2. Trust boundary

Chugel is the last deterministic checkpoint before a Mission Record is
considered "real" (persisted). Everything upstream of Chugel — a human typing
a gate decision, an assistant-as-Emilio or assistant-as-Emma producing
evidence in chat, a future David draft — is untrusted input from Chugel's
point of view, in the same sense `validate_mission_record()` already treats
its `record` argument as untrusted: Chugel does not assume any caller-supplied
dict is well-formed, safe, or honest merely because of who or what claims to
have produced it.

Concretely, Chugel trusts nothing about *why* a mutation is being requested —
only what `validate_mission_record()` and `can_transition()` can mechanically
verify about the resulting record. It never trusts a caller's claim that
"José already approved this" without the gate's own `decided_by` literal
being checked; it never trusts a caller's claim that "this is definitely the
next legal state" without `can_transition()` confirming it against the
persisted current record, not the caller's assertion of what that current
record contains.

The one thing Chugel *does* trust, because nothing downstream of it can
verify further: that the Python process calling it is running with the
access a human has already granted the current chat session/worktree (i.e.
the same trust already implicit in `AGENTS.md`'s isolated-worktree model).
Chugel V1 has no authentication or authorization layer of its own beyond the
`decided_by == "jose"` literal check — it is not a multi-tenant system, and
`AGENTS.md`'s existing worktree isolation is the actual security boundary for
"who can call Chugel's functions at all."

## 3. Persistence model

- **One file per mission**: `orchestrator/missions/<mission_id>.json`, where
  `<mission_id>` is the same UUID the schema's `mission_id` field already
  requires (pattern `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`).
- The directory `orchestrator/missions/` does not exist yet and is not
  created by this design increment. It is created by V1's implementation
  (not this document) the first time a mission is created, or is committed
  as an empty, `.gitkeep`-marked directory at implementation time — that
  choice is left to the implementation increment, not fixed here, since it
  has no bearing on correctness.
- File content is the Mission Record's full JSON serialization — the exact
  same shape `validate_mission_record()` already validates, with no envelope,
  wrapper, or Chugel-specific metadata added around it. A human (or a future
  auditor) can open the file directly and read exactly what the schema
  describes.
- No index, no separate manifest file, no cache of "which missions exist" is
  maintained by V1. Listing missions (if ever needed) is `ls
  orchestrator/missions/*.json` — deliberately not built as a Chugel
  operation in V1, since nothing in the currently-authorized flow requires
  it and a manifest file is one more place state could go stale relative to
  the directory's actual contents.

## 4. Mission ID / path safety

`mission_id` reaches Chugel from two directions, and both are treated as
untrusted:

- **Mission creation**: Chugel itself generates the UUID (`uuid.uuid4()`,
  stdlib, no dependency) — never accepts a caller-supplied ID for a new
  mission. This removes the one case where a malicious or malformed ID could
  otherwise be chosen by a caller before any validation exists to catch it.
- **Every other operation** (`record_builder_evidence`, `decide_gate`,
  `transition`, `get_mission`, etc.) takes a `mission_id` string from the
  caller identifying an *existing* mission. Before that string is used to
  construct any filesystem path, Chugel independently re-validates it against
  the exact same UUID pattern the schema already enforces (`re.fullmatch`,
  stdlib, not re-deriving the pattern by hand from memory — copied verbatim
  from `mission_record.schema.json`'s own `mission_id.pattern`, with a code
  comment pointing at that field so the two never silently drift apart). A
  string that does not match this pattern is rejected before any path is
  built or any filesystem call is made — this is what prevents path
  traversal (`../../etc/passwd`), null-byte injection, absolute-path
  confusion, or any other filename-as-attack-surface issue, since a
  validated UUID can never contain `/`, `..`, or a null byte.
- The resulting path is always constructed as
  `Path(__file__).resolve().parent / "missions" / f"{mission_id}.json"` (the
  same `Path(__file__).resolve().parent` pattern `validator.py` already uses
  for locating the schema file) — never by string concatenation, and never
  by trusting a path fragment supplied directly by a caller.
- Chugel never accepts an arbitrary file path from a caller for any
  operation. There is no "load this mission from wherever" function. If a
  future need for that arises, it is a new, separately-authorized capability,
  not an implicit extension of V1.

**Symlinks — corrective addition (Increment #5 corrective cycle, closing
Emma's independent review finding).** A validated `mission_id` closes path
*traversal*, but does not by itself close the separate risk of the resolved
path itself being a symbolic link (planted by a bug, stale artifact, or
local tampering) that points somewhere outside `orchestrator/missions/`.
Chugel V1 must never follow a symlink when reading or writing a Mission
Record file — this is a hard rule, not a best-effort one:

- Before every read, Chugel checks `os.path.islink(path)` on the exact
  target path; if true, the read is refused exactly like a corrupt record
  (§15) — a dedicated exception (`MissionRecordPathUnsafe`, alongside
  `MissionRecordCorrupt`/`MissionRecordInvalid`) is raised, naming the
  mission ID and the fact that a symlink was found where a regular file was
  expected. No attempt is made to read through it, resolve it, or report
  what it points to.
- The read itself, once the not-a-symlink check passes, opens the file with
  `O_NOFOLLOW` where the platform supports it (POSIX: pass
  `os.O_NOFOLLOW` to `os.open()` before wrapping in a file object; this is
  belt-and-suspenders against a symlink being swapped in between the
  `islink()` check and the `open()` call — a classic TOCTOU window that the
  explicit check alone does not close). On a platform without
  `os.O_NOFOLLOW` (this constant is POSIX-only; it does not exist on
  Windows), Chugel falls back to the `islink()` check alone and documents
  that narrower guarantee for that platform — this repository's actual
  deployment target (Render, Linux) has `O_NOFOLLOW`, so the fallback path
  is a documented portability note, not a gap in the primary target.
- The atomic-write step (§5) already writes to a freshly-created temp file
  and `os.replace()`s it onto the final path — `os.replace()` on POSIX
  replaces whatever is at the destination path (including a symlink, which
  gets unlinked and replaced by the new regular file, never dereferenced
  and written through) — so the write side does not need the same
  `O_NOFOLLOW` treatment the read side does; it is inherently safe by the
  semantics of `rename()`/`replace()`. This is stated explicitly here so
  the future implementation does not add unneeded complexity to the write
  path under the mistaken belief it shares the read path's risk.
- A pre-existing symlink at a mission's path is never silently replaced by
  Chugel as a matter of course — the `MissionRecordPathUnsafe` refusal
  applies uniformly to every operation, including `create_mission()` (which,
  per the corrective addition to §3/§17 below, already fails closed if
  anything exists at the destination path — a symlink is one such case,
  covered by that same check without needing separate logic).

## 5. Read → mutate → validate → atomic-write lifecycle

Every operation that changes a mission follows exactly this sequence, with no
step skipped or reordered:

1. **Read**: load the mission's current JSON file from disk in full. If it
   does not parse as JSON, or does not pass `validate_mission_record()`,
   stop here — see §9 ("what happens if the on-disk record is invalid").
   Never partially read or stream — the whole record is small (bounded by
   the schema's own `maxItems` caps) and is always loaded whole into memory.
2. **Mutate**: apply the requested change to an **in-memory copy**
   (`copy.deepcopy`, stdlib) of the record — e.g. append one
   `builder_evidence` entry, set one gate's `status`/`decided_by`/etc., or
   set `state`/append one `state_history` entry for a transition. The
   on-disk file and the original in-memory dict are never mutated in place;
   only the copy is. This mirrors `validator.py`'s own contract that
   `validate_mission_record()` and `can_transition()` never mutate their
   input — Chugel does not undo that guarantee at the layer that calls them.
3. **Validate**: run `validate_mission_record()` on the mutated copy (and,
   for a transition, `can_transition()` on the pre-mutation record for the
   target state — see §7 for why the transition check happens before, not
   after, the mutation). If either check fails, the operation is aborted:
   the on-disk file is never touched, and the caller receives the exact
   `ValidationError`/`TransitionCheck` reasons, unmodified — Chugel adds no
   interpretation on top of them.
4. **Atomic write**: only if step 3 passed, serialize the mutated copy to
   JSON and write it to disk atomically (see below). `updated_at` is set to
   the current UTC timestamp as part of the mutation in step 2, before
   validation, so a written record's `updated_at` always reflects what was
   actually validated and persisted — never set after the fact outside this
   sequence.

**Atomicity mechanism**: write the full JSON content to a temporary file in
the same directory (`orchestrator/missions/.<mission_id>.json.tmp-<random
suffix>`, stdlib `tempfile` module for the random suffix), `flush()` and
`os.fsync()` that file's descriptor, then `os.replace()` it onto the final
`<mission_id>.json` path. `os.replace()` is atomic on POSIX and on Windows
(unlike `os.rename()`, which is not atomic on Windows when the destination
exists) — this is the same guarantee any correct "atomic file write" needs,
implemented with stdlib only, no dependency. A reader (including a concurrent
Chugel call from another process — see §6) never observes a partially-written
file: it sees either the complete previous version or the complete new
version, never a torn write.

**Directory fsync — corrective addition (Increment #5 corrective cycle),
portability boundary stated explicitly.** `fsync`-ing the temp file's own
descriptor makes the file's *content* durable before the rename, but the
rename itself is a change to the *directory's* metadata, and on POSIX that
change is not guaranteed durable against a host-level crash (power loss, not
just process crash) until the containing directory's own file descriptor is
also `fsync`-ed. Where the platform supports it (POSIX: `os.open(dirpath,
os.O_RDONLY)` on `orchestrator/missions/`, then `os.fsync()` that
descriptor, then close it, performed once after `os.replace()` completes),
the future implementation performs this directory fsync as the final step
of a successful write. This is explicitly **not** available on Windows
(opening a directory with `os.open()` for this purpose is not portable
there), so this step is POSIX-only by construction, gated on `platform`/
`os.name`, and its absence on Windows is a documented, accepted portability
boundary rather than a silent gap — consistent with this document's own
stated threat model (§13: process crash, not power loss), for which the
directory fsync is a strictly-better-than-required hardening, not a
correctness requirement.

## 6. Concurrent access / race considerations

V1 assumes **at most one Chugel process actively mutating a given mission at
a time** — this is a stated assumption (ASSUMPTION, not a verified property),
consistent with the current reality that missions are driven one at a time
through a single chat session, and with `MASTER_ROADMAP.md`'s already-cited
"máximo una misión de implementación activa a la vez" rule referenced in
`MISSION_RECORD.md`. V1 does not implement a lock file, advisory lock, or any
cross-process mutual-exclusion mechanism — that would be new scope beyond
"persistence + deterministic enforcement," and no currently-authorized
workflow drives two concurrent writers against the same mission.

What V1 **does** guarantee even without a lock:

- The atomic-write mechanism in §5 means two concurrent writers can never
  produce a corrupted or torn file — the file on disk after any number of
  concurrent writes is always one complete, valid JSON write from among the
  writers that attempted one (a classic last-write-wins race on the *whole
  file*, not a partial/interleaved corruption).
- Because every write is preceded by a fresh read-and-validate of the
  current on-disk state (§5, step 1), a second writer that started after the
  first writer's read but finished its own write before the first writer's
  write would cause the first writer's write to silently overwrite the
  second's change — a genuine lost-update race. **This is the one
  concurrency risk V1 does not close**, and it is disclosed here rather than
  silently assumed away. Given the single-active-mission assumption above,
  the practical exposure is low, but it is real if that assumption is ever
  violated (e.g. by a bug that runs two Chugel calls for the same mission
  concurrently). Closing this fully requires either a file lock
  (`fcntl`/`msvcrt`, platform-specific, stdlib) or an optimistic-concurrency
  field (e.g. requiring the caller to pass the `updated_at` it last read, and
  rejecting the write if the on-disk `updated_at` no longer matches) — both
  are explicitly deferred to a future increment, not solved here, because
  neither is needed for the single-writer case V1 is scoped to.
- Two Chugel processes reading concurrently is always safe — reads never
  mutate anything, and `os.replace()`'s atomicity means a concurrent reader
  never sees a torn write regardless of how many writers exist.

## 7. Relationship with `validate_mission_record()`

Chugel treats `validate_mission_record()` as the single source of truth for
"is this record acceptable to persist," full stop. Chugel:

- calls it on **every** mutated-copy record before writing (§5, step 3) —
  never writes a record it has not itself just validated, regardless of
  whether the mutation "should obviously" be safe;
- never reimplements, duplicates, shortcuts, or second-guesses any check
  `validate_mission_record()` already performs — if a new invariant is
  needed, it belongs in `validator.py`, reviewed the same way Increment #4
  was, never bolted onto Chugel as a parallel, drifting copy;
- treats a `ValidationResult` with `errors` as an unconditional refusal to
  write, with no override, retry-with-different-input, or "write anyway with
  a warning" path — this matches `validator.py`'s own fail-closed design and
  `MISSION_RECORD.md` Design Principle 7 ("the record fails closed");
- passes through every `ValidationError` (`code`, `message`, `path`)
  unmodified to its own caller — Chugel never translates, summarizes, or
  drops a validation error, so a caller (human or future automation) gets
  the same machine-readable detail `validator.py` already produces.

## 8. Relationship with `can_transition()`

For any operation that changes `state` (i.e. `transition()`), Chugel calls
`can_transition(current_record, target_state)` **before** constructing the
mutated copy's `state`/`state_history` fields, using the freshly-read,
not-yet-mutated on-disk record as its `record` argument — never the
already-mutated copy, since `can_transition()`'s own evidence check (via
`evidence_errors_for_state`) is defined against the record as it stands
*before* the transition, per `orchestrator/state_machine.py`'s existing
contract. If `can_transition()` returns `allowed=False`, Chugel aborts
before ever appending to `state_history` or changing `state` in the copy —
the caller gets `TransitionCheck.reasons` unmodified, and nothing is written.

Only if `can_transition()` returns `allowed=True` does Chugel then: append
one `state_history` entry (`from_state` = current `state`, `to_state` =
`target_state`, `at` = current UTC timestamp, `actor` and `reason` supplied
by the caller), set `state` to `target_state`, and proceed to the full
`validate_mission_record()` check on that mutated copy (§5, step 3) as the
final gate before writing — `can_transition()` and `validate_mission_record()`
are both run for every transition, never either one alone, because
`can_transition()` already internally calls `validate_mission_record()` on
the base record as its own first step (per `state_machine.py`'s documented
three-step logic), but the *post-mutation* record (now carrying the new
`state`/`state_history` entry) still needs its own independent validation —
appending an entry is itself a mutation that must satisfy every cross-field
invariant (state-history continuity, etc.) that `validate_mission_record()`
checks, and `can_transition()` alone does not simulate that specific
post-append shape for every invariant beyond the target state's own evidence
requirement.

## 9. Human-gate enforcement

Chugel never sets any `human_gates.*.status` to `"approved"` or `"rejected"`
except through one function, `decide_gate(mission_id, gate_name, decision)`,
and that function:

- accepts a `decision` payload containing exactly the fields the schema's
  `human_gates.<name>` object requires for an approved/rejected status
  (`decided_by`, `decided_at`, `decision_ref`, and — for `approved` —
  `approved_for`);
- **hard-refuses**, before doing anything else, unless
  `decision["decided_by"] == "jose"` literally — string equality, no
  normalization, no case-insensitivity, no alias list. This is the one place
  in Chugel's own code (not just in the schema/validator it calls) that
  encodes `HUMAN_DECIDER`, imported directly from `orchestrator.validator`
  rather than redefined, so the literal can never drift between the two
  modules;
- does not itself decide *whether* José's approval is warranted — it has no
  opinion on that; it only refuses to record a decision that does not
  actually carry José's literal attribution, and defers everything else to
  `validate_mission_record()`'s existing gate-consistency checks (§7);
- never sets a gate to any status via any other code path — `transition()`,
  `record_builder_evidence()`, and every other operation are structurally
  incapable of touching `human_gates`, so a gate can never be approved as a
  side effect of an unrelated call.

Every gate's default remains `not_requested` from mission creation onward,
exactly as `MISSION_RECORD.md` §"Human gates" specifies — Chugel's
`create_mission()` writes the initial record with all three gates at their
schema-default `not_requested` state and never pre-populates any gate as a
convenience.

## 9a. Scope-change authorization enforcement (`decide_scope_change`) — corrective addition (Increment #5 corrective cycle)

Emma's independent review found that the operation accepting a proposed
scope change into `mission_definition_history` was specified with
materially less rigor than `decide_gate()` — mentioned only in passing prose
inside §17's `propose_scope_change` bullet, never given its own section,
José-check enforcement statement, test coverage, or acceptance-criteria
line. This corrects that gap. `mission_definition_history.authorized_by` is
exactly as safety-critical as a gate's `decided_by` — it defines the
mission's authorized scope — and is treated with the same explicit rigor
here.

Chugel never sets any `proposed_scope_changes[].status` to `"accepted"` or
`"rejected"`, and never appends a `mission_definition_history` entry, except
through one function, `decide_scope_change(mission_id, proposal_id,
decision)`, symmetric to `decide_gate()` in every respect that matters:

- accepts a `decision` payload containing exactly the fields the schema
  requires for the proposal's resulting status (`decided_by`, `decided_at`,
  and, for acceptance, whatever `resulting_mission_definition_version` and
  the new history entry's `authorized_at`/`authorization_decision_ref`
  require per the schema's `david_replan` shape);
- **hard-refuses**, before doing anything else, unless
  `decision["decided_by"] == "jose"` literally — the exact same
  `orchestrator.validator.HUMAN_DECIDER` import, the exact same string
  equality, no normalization, no alias list, as `decide_gate()` (§9) —
  stated here explicitly rather than left to be inferred by symmetry;
- does not itself decide *whether* the proposed scope change is warranted —
  it has no opinion on that, exactly as `decide_gate()` has none about
  whether a gate approval is warranted; it only refuses to record an
  acceptance/rejection that does not already carry José's literal
  attribution, and defers everything else (monotonic versioning, proposal/
  history-entry linkage, `authorized_by` consistency) to
  `validate_mission_record()`'s existing
  `_check_mission_definition_history_consistency` checks;
- on acceptance, atomically (within the same mutate-validate-write cycle,
  §5) both marks the proposal `accepted` with José's decision evidence
  *and* appends the resulting `mission_definition_history` entry — these
  are never two separate Chugel calls, because a persisted state where the
  proposal is accepted but no corresponding history entry exists (or vice
  versa) would fail `_check_mission_definition_history_consistency`'s
  `PROPOSAL_ACCEPTED_WITHOUT_RESULTING_VERSION`/`SCOPE_VERSION_PROPOSAL_MISMATCH`
  checks and be correctly refused — so representing this as one atomic
  operation is not just cleaner, it is the only shape that can ever
  actually pass validation;
- never sets `proposed_scope_changes[].status` or appends to
  `mission_definition_history` via any other code path — `propose_scope_change()`
  itself can only ever write a fresh proposal at `pending_human_decision`
  (§17), never `accepted`/`rejected` directly, so David (or a human acting
  in David's stead today) can never self-authorize his own proposal by
  construction, exactly as `MISSION_RECORD.md`'s re-planning model already
  requires.

**Chugel cannot accept or reject a proposal itself, under any code path.**
The only way `proposed_scope_changes[].status` ever becomes anything other
than `pending_human_decision`, and the only way `mission_definition_history`
ever grows past its `david_intake` entry, is a `decide_scope_change()` call
carrying José's literal, explicit attribution — mirroring §10's existing
guarantee for gates, extended here in full to scope changes.

## 10. How José's authorization is represented without Chugel inventing it

Chugel never synthesizes, infers, or defaults a `decided_by`, `decided_at`,
or `decision_ref` value. Every one of those three fields, for every gate
decision and every `mission_definition_history` entry's `authorized_by`/
`authorized_at`/`authorization_decision_ref`, must be supplied verbatim by
the caller — today, that caller is you (José) instructing the assistant
acting as the human interface to Chugel, exactly as every authorization in
this session so far has been an explicit, quoted human instruction, never an
assistant inference from context. Chugel's role is strictly to **refuse**
anything that does not already carry that explicit attribution — it never
fills in `"jose"` on a caller's behalf, never infers `decided_at` from "now"
when the caller didn't supply it, and never accepts a `decision_ref` it
constructs itself (the ref is a pointer into an external audit trail per
`MISSION_RECORD.md`, whose format remains explicitly out of scope, same as
it was for Increment #3).

## 11. Evidence recording for Emilio and Emma

`record_builder_evidence(mission_id, evidence)` and
`record_reviewer_evidence(mission_id, evidence)` each append exactly one
entry to `builder_evidence[]`/`reviewer_evidence[]` respectively, following
the read → mutate → validate → atomic-write lifecycle (§5) like every other
operation. Chugel:

- does not construct, summarize, or edit the `evidence` payload's content —
  it is supplied whole by the caller (today: a human transcribing what
  Emilio's or Emma's chat turn actually produced) and stored verbatim,
  including its `conclusion`/`verdict`/`findings`/artifact-identity fields
  exactly as `validator.py`'s cross-field checks (attempt sequencing,
  artifact-identity consistency, verdict/severity consistency) already
  expect to validate;
- does not decide which `attempt` number an entry gets — the caller supplies
  it, and `validate_mission_record()`'s existing `_check_attempt_sequencing`
  is what actually enforces that the resulting sequence is legal
  (`[]`/`[0]`/`[0, 1]`) — Chugel adds no separate attempt-numbering logic of
  its own that could disagree with the validator's;
- treats a rejected append (validation failure) as a full abort — no partial
  evidence entry is ever written, and the caller must resubmit a corrected
  payload as a new call, not a "retry the same broken one" loop Chugel
  manages internally.

**`corrective_cycle_count` atomicity — corrective addition (Increment #5
corrective cycle).** When the caller's `evidence` payload to
`record_builder_evidence()` carries `attempt: 1` (the corrective build), the
mutation in the same call also sets `corrective_cycle_count` to `1` on the
same in-memory copy, in the same read → mutate → validate → atomic-write
cycle (§5) — never as a separate call, and never left to the caller to set
independently. This is not optional: `validate_mission_record()`'s existing
`_check_corrective_cycle_consistency` already refuses any record where an
`attempt: 1` builder-evidence entry exists but `corrective_cycle_count` is
still `0` (`CORRECTIVE_CYCLE_COUNT_INCONSISTENT`), so a
`record_builder_evidence()` implementation that appended the entry without
also bumping the count would have every such call fail validation and
write nothing — the fail-closed design already prevents the unsafe
outcome (the record could never reach a state where the count under-counts
a real corrective attempt), but the operation's own mutation logic must
still perform both changes together for a legitimate `attempt: 1` call to
succeed at all. Symmetrically, `record_builder_evidence()` never
increments `corrective_cycle_count` for an `attempt: 0` entry — it stays at
its default `0` until the first (and only) corrective attempt is recorded.

## 12. Preservation of Emma's independence

This is the one place this design most directly touches the single clause
`agents/emma/CONTRACT.md` §16 calls "the single most safety-critical clause
in this contract." Chugel V1, because it does not invoke any agent at all
(§1), cannot itself violate that clause by construction — there is no code
path in V1 where Chugel passes Emilio's summary to "Emma" as if it were her
own finding, because V1 never passes anything to any agent. The
responsibility for actually triggering Emma in a fresh context, with the
artifact and authorized task/acceptance criteria and nothing else, remains
entirely with the human (today, José) driving the chat session — exactly as
every "switch to Emma" instruction in this session has already worked.

This is stated explicitly, not left implicit, because it is the property
future increments must preserve: **whatever eventually automates invoking
Emma must be designed against this exact clause from day one**, not
retrofitted after the fact. V1's contribution to that future safety property
is narrow but real: because `record_reviewer_evidence()` requires a
`reviewer_evidence` payload whose artifact-identity fields
(`artifact_identity_confirmed_at_start` / `_before_conclusion`) match what
`validate_mission_record()`'s `_check_artifact_identity_consistency` already
demands, any future automation that tried to skip Emma's actual independent
re-derivation and fabricate a passing review would still have to produce a
payload internally consistent enough to pass that check — it raises the cost
of cheating, though it does not and cannot, by itself, guarantee a human or
future orchestrator actually invoked Emma honestly. That guarantee is a
process property, not something any schema or persistence layer can enforce
alone, and this document does not claim otherwise.

## 13. Crash/restart/resume semantics

Because every write is atomic (§5) and every read re-validates the record it
loads (§9 in `MISSION_RECORD.md`'s terms; concretely, §5 step 1 and §14
below), a crash at any point during a Chugel operation leaves the on-disk
file in one of exactly two states: the complete pre-operation record
(nothing was written, or the write did not complete before the crash and
`os.replace()` never pointed the final path at a torn file), or the complete
post-operation record (the write fully completed before the crash). There is
no third, corrupted state reachable through Chugel's own write path.

Resuming after a crash requires no special Chugel logic beyond what already
exists: a caller calls `get_mission(mission_id)`, gets back whichever of the
two states above is actually on disk, and decides the next operation based
on that — exactly `MISSION_RECORD.md`'s existing "Resume and idempotency"
design (presence of an identity value as the idempotency signal,
append-only `state_history`) already describes, which V1 does not change or
reinterpret. V1 adds no new resume concept; it makes the existing
design's assumption (a record can always be read back exactly as last
validated) actually true on disk instead of only true in a test fixture.

**What V1 explicitly does not do on resume**: it never re-verifies external
reality (git, GitHub, Render) against the record's claims — that
re-verification is exactly the "future Chugel resuming after a crash...
re-verifies them against the real system" responsibility
`MISSION_RECORD.md` already flags as future work, not something V1's
persistence layer performs. V1's `get_mission()` returns what is on disk,
labeled as exactly that — a stored claim, not a freshly re-verified fact.

## 14. Idempotency

- **`create_mission()` is not idempotent by design** — each call generates a
  fresh UUID (§4) and creates a new mission; calling it twice for "the same"
  intent creates two distinct missions. This matches
  `MISSION_RECORD.md`: `mission_id` is "never reused, even after the mission
  reaches a terminal state," and a Mission Record has no natural
  deduplication key for intent text.
- **Every mutating operation on an existing mission is idempotent in the
  sense that re-validation always re-checks from the current on-disk
  truth**, not from an assumption of what "should" already be there — calling
  `transition(mission_id, "BUILDING", reason)` twice in a row is not
  dangerous: the second call reads the now-current record (already in
  `BUILDING`), asks `can_transition()` whether `(BUILDING, BUILDING)` is
  legal, and — because that pair is not in the canonical `TRANSITIONS` table
  — is correctly refused with `ILLEGAL_STATE_TRANSITION`, not silently
  treated as a no-op success. Chugel does not add special-case "already in
  that state, treat as success" logic — that would be inventing lenience the
  validator itself does not grant, and would risk masking a caller bug that
  thinks it's making forward progress when it is not.
- **Retried evidence appends are not deduplicated** by content — calling
  `record_builder_evidence()` twice with the same payload appends two
  entries, and `_check_attempt_sequencing`'s duplicate-attempt-number check
  is what would then correctly reject the second call at validation time (if
  both used the same `attempt` number) — again, Chugel relies on the
  existing validator rather than inventing its own deduplication heuristic.

## 15. What happens if the on-disk record is invalid

If `orchestrator/missions/<mission_id>.json` exists but:

- **is a symbolic link** (corrective addition, Increment #5 corrective
  cycle, cross-referencing §4's `MissionRecordPathUnsafe` mechanism): the
  read step refuses before ever attempting to open the target, exactly as
  the other two cases below — no operation proceeds, and no attempt is made
  to resolve, follow, or report what the link points to.
- **is not valid JSON** (malformed file, truncated write that somehow
  survived — should not be reachable given §5's atomicity, but checked
  regardless, defense-in-depth): the read step (§5, step 1) raises a
  Chugel-specific, clearly-named exception (e.g. `MissionRecordCorrupt`)
  identifying the mission ID and the parse error. No operation proceeds.
  Chugel never attempts to "repair" or partially interpret a malformed file.
- **is valid JSON but fails `validate_mission_record()`** (e.g. hand-edited
  by a human outside Chugel, or written by a future buggy version of
  Chugel): the read step raises a different, equally specific exception
  (e.g. `MissionRecordInvalid`), carrying the full `ValidationResult.errors`
  list unmodified. No operation proceeds — Chugel never mutates a record it
  cannot first confirm is valid, even to try to fix it.
- In both cases, the failure is loud and immediate (an exception a caller
  must handle), never a silent fallback to "treat as if the mission doesn't
  exist" or "treat as if it's in some default state" — that would be
  inventing mission state Chugel has no authority to invent, and would
  directly violate `MISSION_RECORD.md` Design Principle 7 ("the record fails
  closed: an incomplete, malformed, contradictory, or wrong-schema-version
  record is rejected outright, never leniently reinterpreted").
- Recovery from either state is a human decision (inspect the file, decide
  whether to hand-correct it and re-validate manually before Chugel will
  touch it again, or treat the mission as unrecoverable and escalate) — V1
  provides no automated repair path, because no automated repair could be
  trusted to preserve the audit-trail guarantees the rest of this system
  depends on.

## 16. What happens if the canonical schema version is unsupported

This is already `validate_mission_record()`'s own first substantive check
(`_check_schema_version`, returning `UNSUPPORTED_SCHEMA_VERSION` before any
other cross-field check runs, and — as of the Part A hardening — the
schema's own `const: "1.0.0"` catches it even earlier, at the structural
layer). Chugel adds no separate schema-version logic: an on-disk record
with any `schema_version` other than `"1.0.0"` fails validation at the read
step (§5, step 1) exactly like any other invalid record (§15) — it is not a
special case Chugel distinguishes, because `validate_mission_record()`
already makes "wrong schema version" indistinguishable in *consequence*
(read fails closed) from any other structural invalidity, even though the
specific error code differs and is preserved unmodified for the caller.

If a future schema version is ever introduced, per
`mission_record.schema.json`'s own description field ("a future schema
version gets its own schema file... never a silent extension of this one"),
Chugel would need an explicit, separately-authorized update to know about
it — V1's design assumes exactly one supported schema version, matching
`validator.py`'s current `SUPPORTED_SCHEMA_VERSION` constant, and makes no
attempt to be forward-compatible with a version that does not exist yet.

## 17. What operations V1 will eventually expose

The complete function surface, all synchronous, all pure Python, no
decorators/framework, living in a new module (name and exact file path left
to the implementation increment, e.g. `orchestrator/chugel.py`):

- `create_mission(intent_text: str, *, mission_id: str | None = None) -> dict`
  — creates and persists a new `INTAKE` record. (The optional `mission_id`
  parameter, if included at implementation time, exists only for
  deterministic testing — production callers never supply it, and if
  supplied it is still validated against the UUID pattern before use, per
  §4.) **Fails closed if the destination already exists — corrective
  addition, Increment #5 corrective cycle.** Before writing, `create_mission()`
  checks whether anything already exists at
  `orchestrator/missions/<mission_id>.json` (a regular file, a symlink, or
  any other filesystem entry) and refuses with a dedicated exception
  (`MissionRecordAlreadyExists`) rather than silently overwriting it. In
  practice this only matters for the astronomically unlikely case of a
  `uuid.uuid4()` collision, or for the explicit-`mission_id` testing path
  above being misused against an ID that already has a record — but the
  check costs nothing and closes the gap outright rather than relying on
  the collision probability alone. This existence check is also what
  transitively covers the case where a symlink has been planted at that
  exact path (§4/§15): `create_mission()` never writes through or replaces
  it, because the existence check alone is enough to refuse before any
  write is attempted, without needing separate symlink-specific logic here.
- `get_mission(mission_id: str) -> dict` — read-only; raises per §15 if the
  record is missing, corrupt, invalid, or unsafe (symlink).
- `record_builder_evidence(mission_id: str, evidence: dict) -> dict` —
  appends, validates, writes; returns the updated record. See §11 for the
  atomic `corrective_cycle_count` update this performs for an `attempt: 1`
  entry.
- `record_reviewer_evidence(mission_id: str, evidence: dict) -> dict` — same,
  for `reviewer_evidence`.
- `decide_gate(mission_id: str, gate_name: str, decision: dict) -> dict` —
  the only path to setting a `human_gates.*` status; hard-refuses non-`jose`
  attribution (§9).
- `propose_scope_change(mission_id: str, proposal: dict) -> dict` — appends
  to `proposed_scope_changes[]` (status `pending_human_decision` only —
  Chugel never appends a proposal already marked `accepted`).
- `decide_scope_change(mission_id: str, proposal_id: str, decision: dict) -> dict`
  — the only path to moving a proposal to `accepted`/`rejected` and, on
  acceptance, appending the resulting `mission_definition_history` entry;
  hard-refuses non-`jose` attribution (§9a). Listed here as its own
  first-class operation, not merely described in prose, per Emma's
  independent review finding.
- `transition(mission_id: str, target_state: str, *, actor: str, reason: str) -> dict`
  — the only path to changing `state`/appending to `state_history` (§8).

Every one of these returns the full, freshly-validated, freshly-persisted
record (never a partial view) so a caller always sees exactly what is now on
disk, or raises without writing anything (§5, §15).

## 18. What V1 explicitly will NOT implement

Restated in one place for clarity, all of these are deliberate exclusions,
not oversights:

- no invocation of David, Emilio, or Emma;
- no git, GitHub, CI, or Render integration of any kind;
- no CLI (per your explicit decision for this increment);
- no cross-process locking or optimistic-concurrency check (§6, disclosed
  gap, deferred);
- no mission listing/query/index beyond directory contents;
- no automated repair of a corrupt or invalid on-disk record (§15);
- no Budget Governor enforcement (budget fields are stored and returned
  verbatim, like any other field, but no ceiling is checked or enforced);
- no schema-version migration or multi-version support (§16);
- no changes to `AGENTS.md`, any agent `CONTRACT.md`, the Mission Record
  schema, `validator.py`, or `state_machine.py` — this increment is
  additive-only, a new consumer of the existing, already-reviewed contract
  those files establish.

## 19. Security considerations

- **Path safety**: covered in full at §4 — every `mission_id` from every
  caller is validated against the UUID pattern before any filesystem path is
  built from it, closing path traversal and null-byte injection at the
  earliest possible point.
- **No secrets ever pass through Chugel.** Nothing in the Mission Record
  schema (re-confirmed by re-reading the schema's `required`/`properties`
  list fresh for this design) contains a credential, API key, or token
  field — `decision_ref` is explicitly documented as "a pointer into an
  external audit trail," not the audit content itself, so Chugel's files
  never become a place secrets could leak into version control or logs by
  accident.
- **No code execution of any field content.** Every field Chugel reads from
  a persisted or caller-supplied record is treated as inert data — string,
  number, boolean, or nested structure — never `eval`'d, never used to
  construct a shell command, never used to import a module. This matters
  specifically for free-text fields (`state_reason`, `rationale`,
  `conclusion.text`) which a future, less careful integration might be
  tempted to feed to something more powerful; V1's own code never does.
- **File permissions**: `orchestrator/missions/*.json` files are created
  with the process's default umask, same as every other file this repository
  already writes (e.g. test-generated temp DBs) — no elevated or narrowed
  permission model is introduced, since Mission Records are not classified
  as more sensitive than the rest of this repository's own tracked content
  (they contain no secrets, per above) and the isolated-worktree model is
  the actual access boundary, not file-mode bits.
- **Denial-of-service via unbounded growth**: the schema's own
  `maxItems: 2` caps on `builder_evidence`/`reviewer_evidence`, `maximum: 1`
  cap on `corrective_cycle_count`, and implicit boundedness of a single
  mission's lifecycle mean a single Mission Record file cannot grow without
  bound — `validate_mission_record()` itself is the enforcement point, and
  Chugel inherits that bound for free by always validating before writing.

## 20. Test strategy for the future implementation

Not run in this increment (design only), but specified here so the
implementation increment has a concrete, pre-agreed bar, mirroring how
Increment #4's adversarial test list was specified before that code existed:

- **Lifecycle correctness**: create → record evidence → transition through a
  full legal path to a terminal state, asserting the on-disk file matches
  the in-memory return value at every step.
- **Fail-closed on invalid mutation**: attempt an operation that would
  produce a schema-invalid or cross-field-invalid record; assert the
  on-disk file is byte-identical to its pre-operation content (nothing was
  written) and the correct `ValidationError`s are raised/returned.
  Regression-test as a specific `pathological_write_must_not_occur` case
  around the highest-risk boundary: a mutation that is *almost* valid
  (passes schema, fails exactly one cross-field check), since that is where
  a validate-then-write ordering bug would most plausibly hide.
- **Fail-closed on illegal transition**: attempt an out-of-table transition
  and a table-legal-but-evidence-missing transition; assert no write
  occurred in either case, and that the reasons returned match
  `can_transition()`'s own reasons exactly (no re-summarization).
- **`decide_gate` attribution enforcement**: attempt a gate decision with
  `decided_by` set to anything other than `"jose"` (an agent name, empty
  string, `None`, a case-variant like `"Jose"`), asserting hard refusal
  before any read/write of the mission file — this should be testable
  without even needing an existing mission on disk, since the check is
  meant to happen before persistence logic runs.
- **Path safety**: attempt every operation with a `mission_id` containing
  `../`, a null byte, an absolute path, a non-UUID string, and a
  valid-looking-but-wrong-length UUID; assert rejection before any
  filesystem call (verifiable by mocking/spying on the filesystem layer in
  the test, or by asserting no new file appears under
  `orchestrator/missions/` after the call).
- **Symlink refusal** (corrective addition, Increment #5 corrective cycle):
  create a real mission, then replace its `<mission_id>.json` with a
  symlink pointing at another file (e.g. a second, unrelated valid mission
  record, and separately a file outside `orchestrator/missions/` entirely),
  and assert every operation against that mission ID raises
  `MissionRecordPathUnsafe` without reading the link's target content and
  without writing anything. Additionally: a TOCTOU-style test that swaps a
  regular file for a symlink between the `islink()` check and the open
  call is not practically constructible in a synchronous unit test, so the
  `O_NOFOLLOW` open flag's own correctness (§4) is instead covered by a
  direct unit test asserting that opening a known symlink with
  `os.O_NOFOLLOW` raises `OSError`/`FileNotFoundError` as CPython's `os`
  module documents, independent of Chugel's own wrapping logic.
- **`create_mission` existence check** (corrective addition, Increment #5
  corrective cycle): pre-create a file (and, separately, a symlink) at a
  specific `orchestrator/missions/<uuid>.json` path, then call
  `create_mission(..., mission_id=<that uuid>)`; assert
  `MissionRecordAlreadyExists` is raised and the pre-existing file's content
  is byte-identical afterward (never overwritten).
- **`decide_scope_change` José-attribution enforcement** (corrective
  addition, Increment #5 corrective cycle): symmetric to the
  `decide_gate` case above — attempt an acceptance/rejection with
  `decided_by` set to anything other than `"jose"`, asserting hard refusal
  before any read/write, and asserting that no `proposed_scope_changes[]`
  status changes and no `mission_definition_history` entry is appended.
  Additionally: assert that a successful acceptance call, when it
  succeeds, always produces both the `accepted` status change and the new
  `mission_definition_history` entry together in the same write — never
  one without the other (verifiable by injecting a validation failure
  specifically on the history-entry half and confirming the proposal's
  status change is also rolled back, i.e. nothing was written at all, per
  §5's all-or-nothing write step).
- **`corrective_cycle_count` atomic update** (corrective addition,
  Increment #5 corrective cycle): call `record_builder_evidence()` with an
  `attempt: 1` payload (after a legitimate preceding `CHANGES_REQUIRED`
  reviewer verdict) and assert the single resulting write has both the new
  evidence entry and `corrective_cycle_count == 1`; separately, assert that
  an `attempt: 0` call never changes `corrective_cycle_count` away from its
  default `0`.
- **Atomicity under simulated crash**: using a temp-file-write hook or
  monkeypatch that raises partway through the write step, assert the
  original file (or absence of a file, for a first write) is preserved
  unchanged, and no `.tmp-*` file is left behind by a *successful* run
  (cleanup-on-success is part of the atomic-write contract, even though a
  leftover tmp file from a genuinely crashed process is an acceptable,
  non-corrupting artifact per §13 — the test should distinguish "process
  crashed mid-write, tmp file remains, final file untouched" [acceptable]
  from "final file itself is corrupted" [never acceptable]).
- **Idempotency**: repeat a mutating call twice in a row where the second
  call's resulting state would be illegal (§14), asserting the second call
  fails exactly as specified and does not silently succeed as a no-op.
- **Corrupt/invalid on-disk record**: hand-write a malformed JSON file and,
  separately, a well-formed-but-schema-invalid JSON file into
  `orchestrator/missions/`, then call each operation against that mission
  ID, asserting the specific exception types from §15 and that no operation
  attempts to repair or silently proceed.
- All new tests live under `tests/` (no nested per-package test directory,
  per this repository's existing convention already followed by
  `tests/test_orchestrator_validator.py` and
  `tests/test_orchestrator_state_machine.py`), and — like those two files —
  never invoke an LLM, network, or subprocess; Chugel is pure filesystem +
  pure functions, and its tests should exercise exactly that, using a
  temporary directory (`tempfile.TemporaryDirectory`, monkeypatched as
  Chugel's missions root for the duration of the test) rather than writing
  into the real `orchestrator/missions/` during test runs.

## 21. Rollback strategy

Because every write is atomic and every prior version of a Mission Record is
never overwritten *except* by the single-file-replace mechanism in §5 (there
is no in-place field mutation, and no history is truncated — `state_history`,
`builder_evidence`, `reviewer_evidence`, and `mission_definition_history` are
all append-only by the schema's own design), "rolling back" a Chugel
operation has two distinct meanings, both addressed:

- **Rolling back a failed operation**: not applicable in the traditional
  sense — a failed operation (validation or transition rejection) never
  wrote anything in the first place (§5, §15), so there is nothing to roll
  back; the on-disk file is simply left exactly as it was.
- **Rolling back a successful-but-unwanted operation** (e.g. José authorized
  a gate, Chugel recorded it, and José now wants to undo that): V1 has no
  "undo" operation, by design; a human decision is reversed by a **new**
  decision, never a deletion or in-place edit of the prior one — e.g.
  `human_gates.merge_authorization` moving from `approved` to `rejected` via
  a new `decide_gate` call with a fresh `decision_ref` explaining why.
  **Wording correction (Increment #5 corrective cycle):** this document
  previously described this as gate decisions being "superseded forward,
  never rewritten backward," language borrowed from `mission_definition_history`'s
  genuinely append-only array. That claim overstated what the schema's
  actual `human_gates` structure guarantees. Unlike `state_history`,
  `builder_evidence`, `reviewer_evidence`, and `mission_definition_history`
  — each a genuinely append-only array where every prior entry remains
  physically present in the Mission Record — `human_gates.<name>` is a
  **single object**, not an array. A new `decide_gate()` call **overwrites**
  that object in place; the Mission Record itself retains no internal
  record of what the gate's *previous* `decided_at`/`decision_ref`/
  `approved_for` values were, only the current state. The only trace of a
  superseded gate decision is whatever `decision_ref` pointed to in the
  external audit trail (not yet built, per `MISSION_RECORD.md`) — the
  Mission Record file alone is not sufficient to reconstruct a gate's full
  decision history, only its latest one. A future increment wanting
  in-record gate history (mirroring how scope changes already work) would
  need a schema change to make `human_gates.<name>` an array — explicitly
  out of scope here, since this corrective cycle does not modify the
  schema. This document's own recommendation, stated plainly rather than
  implied: prefer this document's earlier wording only for the fields that
  actually are append-only arrays, and never describe `human_gates` the
  same way.
- **Recovering a mission's file itself** (accidental deletion, disk
  corruption unrelated to Chugel's own writes) is a matter for whatever this
  repository's existing backup/version-control practice already covers —
  V1 introduces no Chugel-specific backup mechanism. **Git-tracking
  decision (Increment #5 corrective cycle, resolving the previously broken
  "see 'Open question' below" cross-reference, which pointed at a section
  that did not exist):** `orchestrator/missions/*.json` files are local
  runtime state, not repository content, and should be **gitignored, not
  committed**. Reasoning: a Mission Record mutates on every gate decision,
  evidence append, and transition — committing it would require a git
  commit (or worse, an uncommitted dirty worktree) for every one of those
  mutations, which has no relationship to this repository's actual
  change-review discipline (`AGENTS.md`'s Builder/Reviewer process governs
  *code* changes, not operational state), and would make `git log` noisy
  with non-review-worthy state churn. This mirrors how
  `database/proyecta.db` — this repository's other piece of mutable
  runtime state — is already `*.db`-pattern-excluded from ordinary editing
  scope per `AGENTS.md`'s protected-path list, though Mission Records are
  not secret or product data and so do not need that list's full
  protection, only its "not committed as part of ordinary change review"
  treatment. This document does not itself modify `.gitignore` — doing so
  is outside this design-only increment's one-file scope
  (`orchestrator/CHUGEL_V1.md`) — but records the decision here so the
  implementation increment adds the corresponding `.gitignore` entry
  (`orchestrator/missions/*.json`, or the whole directory with a tracked
  `.gitkeep`) as part of its own, separately-authorized scope.

## 22. Acceptance criteria for implementing V1

When a future increment actually implements Chugel V1, it is complete only
when all of the following hold, verified the same way Increment #4 was
(targeted tests, full suite, `git diff --check`, `zentra_verify.py`,
independent Emma review):

1. `orchestrator/chugel.py` (or whatever its agreed filename becomes)
   imports and calls `validate_mission_record()`/`can_transition()` from the
   existing `orchestrator.validator`/`orchestrator.state_machine` modules
   without modifying either.
2. Every operation in §17 exists, follows the lifecycle in §5 exactly, and
   is covered by the test categories in §20.
3. No operation writes to disk when validation or the transition check
   fails — proven by the atomicity/fail-closed test cases in §20, not merely
   asserted.
4. `decide_gate()` hard-refuses any `decided_by` other than the literal
   `"jose"`, imported from `orchestrator.validator.HUMAN_DECIDER` rather than
   redefined.
5. Every `mission_id` from every caller is validated against the UUID
   pattern before any filesystem path is constructed, with a passing test
   for each attack shape in §20's path-safety case.
6. No new dependency is added to `requirements.txt` — stdlib only
   (`json`, `os`, `pathlib`, `tempfile`, `uuid`, `copy`, `re`, `datetime`),
   matching this document's design throughout.
7. No file outside `orchestrator/chugel.py`, its new test file(s) under
   `tests/`, and `orchestrator/missions/` (created empty or on first use, per
   §3) is touched.
8. `agents/`, `AGENTS.md`, `database/`, `.github/`, `render.yaml`,
   `proyecta-web/`, `orchestrator/MISSION_RECORD.md`,
   `orchestrator/schemas/`, `orchestrator/validator.py`, and
   `orchestrator/state_machine.py` all remain byte-identical to their state
   before that increment.
9. The full existing test suite (889 tests as of Increment #4) continues to
   pass unmodified, plus the new Chugel-specific tests, all green.
10. An independent Emma review confirms all of the above from a fresh
    reading of the actual diff, not from the Builder's handoff narrative.
11. **(Corrective addition, Increment #5 corrective cycle)** No Chugel
    operation ever follows a symlink when reading a Mission Record path —
    `os.path.islink()` is checked before every read, `O_NOFOLLOW` is used
    on the platforms that support it, and the symlink-refusal test category
    in §20 passes.
12. **(Corrective addition)** `decide_scope_change()` exists as its own
    first-class operation, hard-refuses any `decided_by` other than the
    literal `"jose"` (imported from the same `HUMAN_DECIDER` constant, not
    redefined), and every successful acceptance atomically produces both
    the proposal's status change and the new `mission_definition_history`
    entry in the same write, per the test category in §20.
13. **(Corrective addition)** `create_mission()` fails closed
    (`MissionRecordAlreadyExists`) if anything already exists at the
    destination path, never silently overwriting it, per the test category
    in §20.
14. **(Corrective addition)** `record_builder_evidence()` atomically sets
    `corrective_cycle_count` to `1` in the same write as an `attempt: 1`
    evidence entry, and never changes it for an `attempt: 0` entry, per the
    test category in §20.
15. **(Corrective addition)** `orchestrator/missions/*.json` is added to
    `.gitignore` (or the directory is tracked only via a `.gitkeep`,
    per §21's git-tracking decision) as part of the implementation
    increment's own scope.

## Evolution Path — Chugel V1 → Autonomous Zentra

This section separates what V1 builds from what remains for later, in terms
of **architecture and dependency**, not a numbered roadmap — per your
instruction, nothing below is assigned an increment number unless one
already exists in this repository's own documents.

**V1 (this document's scope): persistence + deterministic enforcement.**
A Mission Record can be created, mutated, and read back from disk, with
every mutation gated by the existing `validate_mission_record()` and
`can_transition()`. No agent, no external system, no autonomy change. This
is the foundation every item below depends on, because none of them can
safely act against a Mission Record that doesn't yet reliably persist.

**Future agent invocation (David, Emilio, Emma) — depends on V1.**
Today, every agent turn is a human (José) driving a chat session with the
assistant occupying that role. Automating any part of that — Chugel
literally starting a David/Emilio/Emma turn rather than a human triggering
it — requires: (a) V1's persistence, so there is a durable record of what
was asked and what came back; (b) a decision, not made in this document,
about what mechanism actually starts an agent turn (a new Claude
Agent SDK session? a scripted prompt construction? something else) — this
is a real architectural choice with its own safety review, not a detail to
wave at here; and (c) for Emilio specifically, nothing changes about his
`CONTRACT.md`'s existing authority — invocation-by-Chugel only changes *who*
presses "go" (per `agents/emilio/CONTRACT.md` §16, already written to
anticipate this).

**Fresh-context Emma invocation — depends on future agent invocation, and
additionally on preserving §12 above.**
This is called out separately from "future agent invocation" in general
because it carries the one clause (`agents/emma/CONTRACT.md` §16) explicitly
marked as the most safety-critical in the whole agent-contract system: any
mechanism that invokes Emma must guarantee a fresh context receiving the
artifact and authorized task/acceptance criteria, never Emilio's summary.
Whatever eventually implements agent invocation in general must be
specifically re-verified against this clause before it is ever used for
Emma, not assumed to satisfy it by extension from working for Emilio.

**David integration — depends on V1, independent of Emilio/Emma
invocation.**
David does not exist yet (`agents/david/` was explicitly not created in any
increment so far, including this one, per your standing instruction).
Building him is a separate, self-contained authorization — his
`CONTRACT.md` (per `agents/AGENT_STANDARD.md`, which already anticipates his
existence without creating him), his intake/research/planning behavior, and
only then his ability to write a `SCOPE_AWAITING_AUTHORIZATION`-stage
`mission_definition_history` entry via Chugel's persistence layer. Nothing
in V1 blocks or assumes David; V1's `create_mission()`/`propose_scope_change()`
operations are written generically enough that a human can call them today
in David's stead, and David, once built, would call the same functions no
human-callable Chugel operation needs to change shape to accommodate him.

**Git/GitHub/CI automation — depends on V1, independent of the above.**
Automating `PUBLISHING`/`CI_PENDING`/`MERGING` (commit, push, open a PR, poll
CI, merge) requires GitHub credentials/tokens and git-write access this
system does not currently have configured for autonomous use, plus its own
safety review given `AGENTS.md`'s absolute prohibition on push/merge without
separate explicit human authorization for that exact action — any
automation here must still surface each of those as a live gate check, not
bypass `human_gates.publish_authorization`/`merge_authorization` by being
"the same trusted code" that already passed `decide_gate()`'s literal check.
This is architecturally the least coupled to agent invocation (it never
needs an LLM) and the most coupled to `AGENTS.md`'s existing non-negotiable
contract, which this document does not and cannot loosen.

**Deploy/production verification — depends on Git/GitHub/CI automation
existing first** (there is no `merge.merge_commit_sha` to verify a deploy
against until a merge has actually happened), **and on read-only access to
Render's deploy-event log and `/health`/`/version` endpoints** — per
`MISSION_RECORD.md`'s `VERIFYING_PRODUCTION` state definition, this is
explicitly read-only, never a trigger (Render's own auto-deploy is what
actually deploys, never this system).

**Budget Governor — depends on V1's persistence for tracking `budget.consumed`
across calls, but is otherwise independent of every other item above.**
It could in principle be built against V1 alone (recording token/cost
consumption per agent per mission) without waiting on agent invocation,
David, or git automation — its only hard dependency is a place to durably
accumulate the numbers, which V1 provides.

**Recovery/resume — depends on V1 (§13 already specifies the persistence-
layer half of this) and, for anything beyond "read back what's on disk," on
whichever of the above integrations exist** — resuming a mission stuck in
`CI_PENDING` needs GitHub polling to exist; resuming one stuck in
`AWAITING_REVIEW` needs nothing beyond V1, since a human can just trigger
Emma's turn manually as already happens today.

**Higher autonomy levels — depends on all of the above, and on explicit
human approval for each specific level, per each agent's own `CONTRACT.md`.**
`agents/emilio/CONTRACT.md` §3 and `agents/emma/CONTRACT.md` §3 both already
state that naming an agent something more senior, or writing infrastructure
around it, never itself advances its autonomy level — that requires
"explicit human approval and evidence appropriate to the new capability"
each time. Building every item above does not, by itself, grant Emilio or
Emma a higher autonomy level; it only makes it *possible* for you to grant
one later, with actual evidence behind that decision, exactly as
`PLAYBOOK.md`'s progressive-autonomy ladder already requires.

Nothing above is a commitment to build any specific item next, or in any
specific order — it is a dependency map, so that whichever piece you choose
to authorize next has its actual prerequisites stated honestly rather than
discovered as a surprise mid-increment.
