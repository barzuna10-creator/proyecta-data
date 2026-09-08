# Recovery Contract V1

M6 (BLOCKED-State Resume Wiring & Unified Recovery Contract). This is the
single, unified reference for how a mission ever leaves `BLOCKED`, and for
every human-gate/escalation surface this codebase exposes -- replacing the
fragmentation that previously lived only across `orchestrator/validator.py`'s
`TRANSITIONS` comment, `jarvis/mission_write.py`'s `resume_from_blocked()`
docstring, `orchestrator/chugel.py`'s `reopen_deploy_observation_window()`
docstring, and `orchestrator/deploy_recovery_policy.py`'s `RecoveryAction`
docstring. Nothing here changes any of those functions' behavior; this
document describes the real, already-implemented and already-tested
system, and is kept in sync with it mechanically (see "Keeping this in
sync with the code" below).

## 1. The state -> BLOCKED and BLOCKED -> state edge inventory

Every state in `orchestrator/validator.py::STATES` may transition to
`BLOCKED` (17 distinct `(X, "BLOCKED")` edges exist in `TRANSITIONS` today,
one per non-terminal state reachable in the mission lifecycle, plus
`(None, "INTAKE")` which is unrelated to BLOCKED). This document is about
the other direction: the 17 `("BLOCKED", X)` edges `TRANSITIONS` legalizes,
split into two disjoint groups.

### 1a. The 6 real, wired states

These are exactly `jarvis/mission_write.py::_RESUMABLE_PRIOR_STATES`. A
mission BLOCKED from one of these can actually be moved forward again,
today, through a real HTTP caller (`POST /v1/missions/{id}/resume`, M6):

- `PUBLISHING`
- `CI_PENDING`
- `MERGE_AWAITING_AUTHORIZATION`
- `MERGING`
- `DEPLOY_PENDING` (subject to the recovery-policy check in section 3 below)
- `VERIFYING_PRODUCTION` (subject to the same check)

### 1b. The 11 phantom (legal-but-unreachable) states

`TRANSITIONS` legalizes a `("BLOCKED", X)` edge for each of these states
too -- so a hand-constructed `chugel.transition()` call to one of them is
not rejected by the transition table itself -- but no production code path
ever drives a mission through one of these edges, and M6 does not change
that. They remain legal-but-unreachable, exactly as before this milestone:

- `INTAKE`
- `SCOPE_AWAITING_AUTHORIZATION`
- `AUTHORIZED`
- `BUILDING`
- `VERIFYING`
- `AWAITING_REVIEW`
- `REVIEWING`
- `CORRECTING`
- `PUBLISH_AWAITING_AUTHORIZATION`
- `FAILED`
- `CANCELLED`

M6 does **not** wire any of these 11 edges. Wiring them is explicitly out
of this milestone's scope (see the M6 design's own non-goals) -- doing so
would require, for each one, its own product decision about what "resuming
into BUILDING/REVIEWING/etc. after a human-directed BLOCKED" should even
mean operationally, which nothing has authorized yet.

### Machine-parseable edge inventory

The fenced block below is parsed verbatim by
`tests/test_docs_recovery_contract_sync.py` (`ast.literal_eval` per
non-comment line) and asserted, by exact set equality, against the real,
live computation of `{t for t in orchestrator.validator.TRANSITIONS if
t[0] == "BLOCKED"} - {("BLOCKED", s) for s in
jarvis.mission_write._RESUMABLE_PRIOR_STATES}`. If `TRANSITIONS` or
`_RESUMABLE_PRIOR_STATES` ever change, that test fails loudly until this
block is updated to match -- this document can never silently drift from
the real code.

```
# PHANTOM_BLOCKED_OUT_EDGES
("BLOCKED", "AUTHORIZED")
("BLOCKED", "AWAITING_REVIEW")
("BLOCKED", "BUILDING")
("BLOCKED", "CANCELLED")
("BLOCKED", "CORRECTING")
("BLOCKED", "FAILED")
("BLOCKED", "INTAKE")
("BLOCKED", "PUBLISH_AWAITING_AUTHORIZATION")
("BLOCKED", "REVIEWING")
("BLOCKED", "SCOPE_AWAITING_AUTHORIZATION")
("BLOCKED", "VERIFYING")
```

Pinned counts (also asserted by the same test): **17** total
`("BLOCKED", X)` edges in `TRANSITIONS`; **6** of them match a
`_RESUMABLE_PRIOR_STATES` member, with zero orphans (every
`_RESUMABLE_PRIOR_STATES` member is a real `TRANSITIONS` edge target);
**11** phantom.

## 2. The two real HTTP recovery endpoints (M6)

Neither is addressed by `gate_id`, and neither is added to
`jarvis/control_plane_server.py`'s `_AUTHORIZE_BY_KIND` -- an earlier
design round explicitly rejected extending that dict: it addresses by
`gate_id`, not `mission_id`; it has no room for `reopen`'s additional
`acknowledgement` field; and it would let `DeployRecoveryRequired` fall
through to the generic 500 handler instead of surfacing actionable
guidance. `_AUTHORIZE_BY_KIND` and `_handle_authorize()` are untouched by
M6.

### `POST /v1/missions/{mission_id}/resume`

Wraps `jarvis/mission_write.py::resume_from_blocked()`. Requires the
literal confirmation phrase (the same `_CONFIRMATION` constant every other
human-authorized action in this module requires) and is always attributed
to the literal `HUMAN_DECIDER` (`"jose"`), constructed by the server
itself -- never taken from the request body, exactly like the three
pre-existing gates. On success, the mission transitions from `BLOCKED`
back to its own `prior_state` (mechanically derived from
`state_history`, never a caller-supplied target).

Three distinct, honestly-worded failure responses when
`resume_from_blocked()` raises `DeployRecoveryRequired` (only possible
when `prior_state` was `DEPLOY_PENDING` or `VERIFYING_PRODUCTION` --
see section 3):

- **`EXCEPTIONAL_REOPEN_REQUIRED`** -- explicitly tells the caller to use
  `POST /v1/missions/{id}/reopen-deploy-observation` with a real
  `acknowledgement`.
- **`RECOVERY_EXHAUSTED`** -- explicitly states no automated path remains.
  **Never** suggests reopen -- see section 4's fail-closed guarantee.
- **`DENY`** -- explicitly states no automated recovery path is defined by
  policy for this classification. **Deliberately does not suggest
  reopen**, even though (see section 4) a direct reopen call can still
  succeed for some DENY cases.

`ResumeNotEligible` (mission not `BLOCKED`, or `BLOCKED` from a state
outside `_RESUMABLE_PRIOR_STATES`, i.e. one of the 11 phantom states) maps
to a 409 as well, with no recovery-specific wording.

### `POST /v1/missions/{mission_id}/reopen-deploy-observation`

Wraps `jarvis/mission_write.py::reopen_deploy_observation_window()`
(itself a thin, attribution-checking wrapper around
`orchestrator/chugel.py::reopen_deploy_observation_window()` -- the sole
disclosed Chugel write seam this module is allowed to reach, per
`tests/test_jarvis_foundation_boundaries.py`). Same fixed, never-caller-
supplied attribution as `/resume`. The one additional thing this endpoint
requires from the caller is a real, non-empty `acknowledgement` string --
validated once, shallowly, by the HTTP handler (fail fast on an obviously
empty request), and again, more deeply, by Chugel itself. On success, the
mission transitions `BLOCKED -> DEPLOY_PENDING` unconditionally --
regardless of whether `BLOCKED` was entered from `DEPLOY_PENDING` or
`VERIFYING_PRODUCTION` -- because a fresh observation window always
restarts from the `/version` poll, never resumes mid-`/health`.

## 3. The resume-vs-reopen decision tree

Mirrors `orchestrator/deploy_recovery_policy.py::recovery_action_for()`'s
real mapping exactly -- this is a description of that function, not an
independent restatement that could drift from it.

`resume_from_blocked()` only consults this policy when `prior_state` is
`DEPLOY_PENDING` or `VERIFYING_PRODUCTION` -- a complete no-op (zero new
code executes) for the other 4 resumable states.

Inputs: `classification` (`deploy.last_blocked_classification`, one of the
7 `DEPLOY_BLOCKED_CLASSIFICATIONS`, or `None`), `recovery_status`
(`deploy.recovery_status`), `remaining_budget_seconds` (derived from
`deploy.observation_started_at` against the fixed 900-second
`_DEPLOY_OBSERVATION_BUDGET_SECONDS`).

1. **`recovery_status == "exhausted"`** -> always `RECOVERY_EXHAUSTED`,
   regardless of classification or budget. This check runs first and
   short-circuits everything below.
2. Else, if `classification` is one of `ANCESTRY_UNVERIFIABLE`,
   `HEALTH_UNREACHABLE_OR_MALFORMED`, `IDENTITY_NEVER_OBSERVED` **and**
   `remaining_budget_seconds > 0` -> `PLAIN_RESUME`.
3. Else, if `classification` is one of `OBSERVATION_BUDGET_EXHAUSTED`,
   `IDENTITY_MISMATCH_DEFINITIVE`, `HEALTH_DEGRADED`,
   `IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK`, `IDENTITY_NEVER_OBSERVED`
   -> `EXCEPTIONAL_REOPEN_REQUIRED`.
4. Else -> `DENY` (an unrecognized/unclassified combination; fails closed
   rather than guessing).

**`IDENTITY_NEVER_OBSERVED` deliberately appears in both step 2 and step
3.** This is intentional, not a bug: with budget remaining
(`remaining_budget_seconds > 0`), the classification alone does not yet
prove anything is actually wrong (the identity check may simply not have
had a chance to run yet), so a plain, automatic resume is safe. Once the
budget is exhausted for that same classification, the mission has now
spent its entire observation window without ever confirming identity --
that is no longer "wait and see", so the same classification now requires
a real human acknowledgement (`EXCEPTIONAL_REOPEN_REQUIRED`) before moving
forward again.

Note that this table cannot itself produce `DENY` for a classification
covered by step 2 or step 3 while `remaining_budget_seconds > 0` and
`recovery_status == "available"` and the classification is
`ANCESTRY_UNVERIFIABLE`/`HEALTH_UNREACHABLE_OR_MALFORMED` -- `DENY` is
only reached today via `ANCESTRY_UNVERIFIABLE` or
`HEALTH_UNREACHABLE_OR_MALFORMED` once `remaining_budget_seconds <= 0`
(the observation window has elapsed in wall-clock time) while
`recovery_status` is still `"available"` (as opposed to already
`"exhausted"`, which step 1 would have already caught) -- neither step 2
(budget exhausted) nor step 3 (classification not in that set) matches,
so it falls through. This is a real, reachable combination, not a
theoretical one; `tests/test_jarvis_control_plane_server.py`'s
`test_deny_resume_refuses_with_no_suggested_next_step_but_a_direct_reopen_still_succeeds`
constructs it directly.

## 4. The DENY / RECOVERY_EXHAUSTED asymmetry (real, pre-existing M4 behavior)

**This is the single most important thing a future reader of this
document must not get wrong**, because the two failure modes look
superficially similar (both mean "`/resume` will not help you") but are
enforced completely differently one layer down:

- **`RECOVERY_EXHAUSTED` is enforced fail-closed at the Chugel layer.**
  `orchestrator/chugel.py::reopen_deploy_observation_window()` itself
  unconditionally refuses (raises `DeployRecoveryNotEligible`) whenever
  `deploy.recovery_status != "available"`. It does not matter whether the
  caller goes through `/resume`, `/reopen-deploy-observation`, or calls
  the Chugel function directly with a perfectly-formed acknowledgement --
  once `recovery_status` is `"exhausted"`, no path in this codebase can
  reopen that mission's deploy-observation window again. This is why the
  `/resume` error message for `RECOVERY_EXHAUSTED` never suggests reopen:
  suggesting it would be actively dishonest, not merely unhelpful.

- **`DENY` is NOT gated by classification at the Chugel layer at all.**
  `reopen_deploy_observation_window()` checks exactly two things before
  proceeding: `state == "BLOCKED"` and `deploy.recovery_status ==
  "available"`. It never reads `deploy.last_blocked_classification`. So a
  mission whose `recovery_action_for()` result is `DENY` -- meaning
  `/resume` will correctly refuse it, and correctly suggest nothing -- can
  still have its deploy-observation window reopened by a **direct** call
  to `/reopen-deploy-observation` with a real acknowledgement, and that
  call will **succeed**, because `recovery_status` is still `"available"`
  for a DENY case (DENY and RECOVERY_EXHAUSTED are mutually exclusive
  outcomes of the same function -- see section 3, step 1).

This asymmetry is real, pre-existing M4 behavior. M6 documents it here
precisely so no future reader assumes `DENY` is fail-closed the way
`RECOVERY_EXHAUSTED` is, and precisely so nobody "fixes" `/resume`'s DENY
message to suggest reopen -- it deliberately does not, because doing so
would encourage exactly the direct-call path this section describes,
without any of the review/acknowledgement discipline that path is
supposed to carry. Changing this behavior (e.g. gating reopen on
classification too) would be a real policy change, and is explicitly out
of M6's scope; `tests/test_jarvis_control_plane_server.py`'s
`test_deny_resume_refuses_with_no_suggested_next_step_but_a_direct_reopen_still_succeeds`
is a characterization test, not a claim that this is the right long-term
policy.

## 5. Human-gate / escalation-surface inventory

| Surface | Endpoint | Gate/addressing | Attribution |
| --- | --- | --- | --- |
| `scope_authorization` | `POST /v1/gates/{gate_id}/authorize` | `_AUTHORIZE_BY_KIND["scope"]` | literal `HUMAN_DECIDER`, server-constructed |
| `publish_authorization` | `POST /v1/gates/{gate_id}/authorize` | `_AUTHORIZE_BY_KIND["publish"]` | literal `HUMAN_DECIDER`, server-constructed |
| `merge_authorization` | `POST /v1/gates/{gate_id}/authorize` | `_AUTHORIZE_BY_KIND["merge"]` | literal `HUMAN_DECIDER`, server-constructed |
| draft authorization | `POST /v1/gates/{draft_id}/authorize` | digest/revision-exact match, not in `_AUTHORIZE_BY_KIND` | literal `HUMAN_DECIDER`, server-constructed |
| resume from BLOCKED (M6) | `POST /v1/missions/{mission_id}/resume` | addressed by `mission_id`, not in `_AUTHORIZE_BY_KIND` | literal `HUMAN_DECIDER`, server-constructed |
| reopen deploy-observation window (M6) | `POST /v1/missions/{mission_id}/reopen-deploy-observation` | addressed by `mission_id`, not in `_AUTHORIZE_BY_KIND` | literal `HUMAN_DECIDER`, server-constructed, plus a real `acknowledgement` string |

**Explicitly out of scope for M6, flagged as a separate finding for a
future increment, not silently absorbed here:** M5's knowledge-promotion
gate (`KnowledgeAuthorizationIntent`/`parse_knowledge_authorization`) has
its own, independent wiring gap -- it is not touched by M6, and this
document does not attempt to describe its recovery contract. It deserves
its own dedicated increment, exactly like BLOCKED-resume did before M6.

## Keeping this in sync with the code

`tests/test_docs_recovery_contract_sync.py` parses `TRANSITIONS` and
`_RESUMABLE_PRIOR_STATES` from the real, live modules and the
`# PHANTOM_BLOCKED_OUT_EDGES` block above from this file at runtime, and
asserts they are exactly equal as sets of tuples -- not merely equal in
count. If either the transition table or the resumable-states set is ever
extended (wiring one of the 11 phantom edges, or adding a new BLOCKED-
reachable state), that test fails until this document's fenced block, and
the surrounding prose above, is updated to match.
