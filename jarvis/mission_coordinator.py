"""Mission 004 -- the sole Jarvis module permitted to import
orchestrator.autonomous_runner, orchestrator.publish_executor,
orchestrator.merge_executor, and orchestrator.publish_identity_repair.

advance() drives a mission as far as it can go without a human, then
returns a CoordinatorReport describing exactly why it stopped -- a gate
needing José, a BLOCKED mission waiting for confirmation, a terminal
failure, or MERGED (ready for the executive summary). It never calls
chugel.decide_gate()/create_mission()/jarvis.mission_write.* itself with
a fabricated decision; every gate/resume decision it relays must already
be a real dict the calling turn built directly from José's own message,
this turn.

Mission 006: advance() is also the sole place that moves a freshly
created mission out of INTAKE. That single edge (INTAKE ->
SCOPE_AWAITING_AUTHORIZATION) carries no evidence requirement in
orchestrator/validator.py's _STATE_EVIDENCE_CHECKERS -- the mission
definition is already complete, verbatim, from the draft that was
authorized to create this mission (jarvis.mission_authorization_bridge,
which stops at creating the record and never itself progresses it). It
is intentionally the ONLY transition this module ever produces without
a human decision behind it, and it is attributed to the literal actor
"chugel" -- one of the five schema-fixed values in
orchestrator/schemas/mission_record.schema.json's actor enum
(["jose", "david", "emilio", "emma", "chugel"]), never "jose"
(HUMAN_DECIDER), and never a free-form string invented here.

Mission 006 (gate-consumption follow-up): the three *_AWAITING_AUTHORIZATION
branches below also each mechanically consume an ALREADY-PERSISTED gate
decision, never fabricate one. jarvis.mission_write.authorize_scope/
authorize_publish/authorize_merge already require a real, current-turn
José attribution before they ever call chugel.decide_gate() -- this
module reads only human_gates.<name>.status/approved_for exactly as
decide_gate() left them, and never itself writes decided_by,
approved_for, or the gate status (chugel.decide_gate() remains the only
path to any of those). If the gate is still "pending"/"not_requested",
these branches are unchanged from Mission 004: they return GATE_REQUIRED
with no side effect, safe to call any number of times, from any trigger
(a real notify(), or recover_on_startup() after a crash). If the gate is
already "approved" (a real decision decide_gate() already wrote,
possibly before a crash that happened before the mechanical transition
below ever ran), the corresponding mechanical transition to the next
execution state runs, attributed to the same fixed "chugel" actor as the
INTAKE edge -- resuming an already-granted human authorization, never
granting one implicitly. If "rejected", the mechanical transition target
is CANCELLED instead. chugel.transition()'s own can_transition() check
independently re-verifies the target state's evidence before allowing
either transition -- a bug here that tried to consume a gate that was
not actually decided correctly would still be rejected by Chugel itself,
never merely trusted from this module's own read. Every other branch
below is unchanged from Mission 004."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator import autonomous_runner, chugel, merge_executor, publish_executor
from orchestrator import publish_identity_repair
from orchestrator.agent_invocation import InvocationNotAuthorized
from orchestrator.validator import GATE_STATUSES

_GATE_STATES = frozenset({
    "SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION",
})
_TERMINAL_FAILURE_STATES = frozenset({"FAILED", "CANCELLED", "ROLLED_BACK"})

# The one non-human, system-attributed actor this module is ever allowed
# to use for a transition -- schema-fixed (see module docstring), never
# HUMAN_DECIDER, never invented per-call.
_SYSTEM_ACTOR = "chugel"
_INTAKE_ADVANCE_REASON = (
    "mission definition already complete from the authorized draft; "
    "no additional evidence required for this transition"
)

# Mission 006 (gate-consumption follow-up): the mechanical target for each
# *_AWAITING_AUTHORIZATION state's gate, keyed by the same gate_name
# jarvis.mission_write.authorize_scope/publish/merge already use. Both
# tables are intentionally the identity map from "which gate" to "which
# state" -- never a separate, second source of truth about which gate
# belongs to which state (jarvis.mission_write._GATE_STATE, unchanged, is
# the other place this same mapping already lives; duplicated as a plain
# dict here rather than imported, since jarvis.mission_write is a write
# seam this read-only mapping has no need to depend on).
_GATE_NAME_FOR_STATE = {
    "SCOPE_AWAITING_AUTHORIZATION": "scope_authorization",
    "PUBLISH_AWAITING_AUTHORIZATION": "publish_authorization",
    "MERGE_AWAITING_AUTHORIZATION": "merge_authorization",
}
_GATE_APPROVED_TARGET = {
    "SCOPE_AWAITING_AUTHORIZATION": "AUTHORIZED",
    "PUBLISH_AWAITING_AUTHORIZATION": "PUBLISHING",
    "MERGE_AWAITING_AUTHORIZATION": "MERGING",
}
_GATE_REJECTED_TARGET = "CANCELLED"  # same for all three gates -- validator.TRANSITIONS allows it from each
_GATE_CONSUMED_REASON = (
    "mechanically consuming an already-persisted human gate decision; "
    "no new evidence, decided_by, or approved_for written here"
)

# Jarvis God Mode M1 -- Corrective Round 1 (closing Emma's P1/P2 on the
# original workspace guard). The lifecycle-traced, deterministic unit of
# ownership over the one repository_root every mission currently shares
# (until M2 builds real per-mission isolation):
#
# ACQUISITION happens exactly once, at the AUTHORIZED -> BUILDING edge
# inside autonomous_runner.run_mission() -- the first moment any real
# subprocess dispatch (Emilio/Emma), `git push`/`gh pr create`, CI poll,
# or `git`/`gh` merge ever touches the shared tree. Before that
# (INTAKE, SCOPE_AWAITING_AUTHORIZATION, AUTHORIZED itself) a mission
# owns nothing.
#
# RETENTION is CONTINUOUS from that acquisition through every state
# where real, unmerged work still exists in that tree -- this is the
# fix: the original guard incorrectly treated PUBLISH_AWAITING_AUTHORIZATION
# and MERGE_AWAITING_AUTHORIZATION as if they were like the
# SCOPE_AWAITING_AUTHORIZATION gate (untouched tree) -- they are not. A
# mission sitting at either of those two gates has already built,
# reviewed, and (for the merge gate) published real changes into the
# shared tree; the human gate pending there is a decision ABOUT that
# work, not a reason to consider the tree free. This table -- verified
# exhaustively against orchestrator/schemas/mission_record.schema.json's
# own 22-state enum and orchestrator/validator.py's real TRANSITIONS
# table, not guessed -- is every state where that is true:
_REPOSITORY_ROOT_OWNING_STATES = frozenset({
    "BUILDING", "VERIFYING", "AWAITING_REVIEW", "REVIEWING", "CHANGES_REQUIRED", "CORRECTING",
    "PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING", "CI_PENDING",
    "MERGE_AWAITING_AUTHORIZATION", "MERGING",
})
# Deliberately EXCLUDED from the table above, with a reason each:
#   - SCOPE_AWAITING_AUTHORIZATION: pre-acquisition -- a mission waiting
#     on ITS FIRST gate has never touched repository_root. Blocking
#     another mission because of a gate no one has acted on yet is
#     exactly the "misión esperando un gate humano... bloquea otra
#     innecesariamente" failure mode this guard exists to avoid.
#   - AUTHORIZED: the acquisition instant itself, not yet real work.
#     Excluding it is not a convenience -- including it creates a real
#     deadlock: two simultaneously-AUTHORIZED missions would each see
#     the OTHER as owning and both refuse to ever start, forever
#     (neither one's state would ever change to break the symmetry).
#     Excluding it breaks that symmetry: the first AUTHORIZED mission
#     this module actually advances finds the table empty (the second
#     is only AUTHORIZED, not a member) and is the one that gets to
#     acquire -- becoming BUILDING (a member) before the second is ever
#     checked.
#   - Every terminal state (MERGED, FAILED, CANCELLED, ROLLED_BACK,
#     DEPLOY_PENDING, VERIFYING_PRODUCTION, COMPLETED): release -- no
#     automatic action is ever taken from any of them again.
#   - BLOCKED is deliberately NOT in this table -- see
#     _mission_owns_repository_root()'s own docstring for why a fixed
#     current-state table alone cannot classify it correctly.


def _mission_owns_repository_root(mission_id: str, state: str) -> bool:
    """True if `state` alone already proves ownership (member of
    _REPOSITORY_ROOT_OWNING_STATES); for BLOCKED specifically, the
    current state alone is genuinely ambiguous and must not be
    classified either way by a fixed table -- orchestrator/validator.py's
    real TRANSITIONS set structurally allows BOTH ("SCOPE_AWAITING_AUTHORIZATION",
    "BLOCKED") and ("INTAKE", "BLOCKED") (pre-acquisition; owns nothing)
    AND every owning state transitioning to BLOCKED (post-acquisition;
    still owns real, unresolved work in the tree) -- confirmed by reading
    that table directly, not assumed. Resolved deterministically from
    canonical persisted state, never a new lock or field: a mission's own
    state_history (chugel.get_mission(), already one of this module's
    disclosed Chugel calls) durably records every transition it has ever
    made. If any prior transition ever landed on an owning state, this
    mission has never released it -- BLOCKED only ever *pauses* automatic
    progress (advance()'s own `if state == "BLOCKED":` branch takes no
    action), it never releases whatever the mission already holds. If no
    such transition exists, this mission never acquired anything and
    BLOCKED here is exactly the pre-acquisition case. Fails closed on any
    read failure: unable to confirm the record no longer holds a real
    claim is treated as still holding one, never the reverse -- the
    opposite direction (treating an unreadable record as free) is what
    could let a second mission start real work over an unresolved one."""
    if state in _REPOSITORY_ROOT_OWNING_STATES:
        return True
    if state != "BLOCKED":
        return False
    try:
        record = chugel.get_mission(mission_id)
    except Exception:  # noqa: BLE001 -- fail-closed: cannot confirm release, so not released.
        return True
    history = record.get("state_history") or []
    return any(entry.get("to_state") in _REPOSITORY_ROOT_OWNING_STATES for entry in history)


def _mission_occupying_repository_root(mission_id: str) -> str | None:
    """Read-only, via the same disclosed chugel.list_missions()/
    get_mission() seams this module already uses nowhere else directly
    (both are among Chugel's three disclosed import seams -- see module
    docstring) -- no second state engine, no new persisted concept:
    ownership is entirely re-derived, every call, from the same
    canonical Mission Record state (current state, and -- only for the
    ambiguous BLOCKED case -- state_history) everything else in this
    module already reads. A fixed frozenset membership test plus one
    bounded history scan, nothing else -- no model output, no free-text
    interpretation, no judgment call.

    Jarvis God Mode M1 Final Hardening Round (closing Emma's P3, round 3
    of independent review): an unreadable listing (corrupt/invalid/unsafe)
    is treated as OWNING, never skipped. The approved invariant is
    unconditional -- "si el sistema no puede determinar con certeza que
    repository_root está libre, NO puede permitir adquisición" -- and an
    unreadable record is exactly that: undetermined, not confirmed free.
    A prior version of this function skipped unreadable listings,
    reasoning only about whether a corrupt record could itself newly
    ACQUIRE ownership going forward (it cannot, since every dispatch
    branch in this module starts with a successful chugel.get_mission()
    read) -- but that reasoning never addressed a mission that was
    already a real, legitimate owner (mid-BUILDING, say, with genuine
    uncommitted changes in the shared tree) whose record then becomes
    unreadable for any reason before release. Silently skipping such a
    listing let a second mission acquire over it -- a real fail-open gap,
    now closed: this function can no longer be fooled by a record it
    cannot read into believing repository_root is free.

    Returns the other mission_id if repository_root is owned (or its
    status cannot be confirmed), None only when every other listing is
    both readable and confirmed non-owning. `mission_id` itself is
    always excluded -- this checks for a DIFFERENT mission, never treats
    a mission's own current state as contention with itself."""
    for listing in chugel.list_missions():
        if listing["mission_id"] == mission_id:
            continue
        if not listing["readable"]:
            return listing["mission_id"]
        if _mission_owns_repository_root(listing["mission_id"], listing["state"]):
            return listing["mission_id"]
    return None


# Verification Hardening V1, Pillar 1 (contract checks) -- sixth
# vocabulary: human_gates.<name>.status, orchestrator.validator's own
# GATE_STATUSES (the single source of truth this partitions, imported
# rather than duplicated). Before this corrective, _consume_gate_if_decided()
# branched explicitly only on "approved"/"rejected" and folded
# "pending"/"not_requested"/anything else into one untested `else: return
# False` -- the code's own prior comment literally said "anything else --
# no side effect". A future 5th gate status would have silently taken
# that same no-op path, exactly the PWNBF-class gap this whole initiative
# exists to close. Every value now has explicit, tested treatment; an
# unrecognized status raises rather than being silently treated as
# equivalent to "pending" -- see
# tests/test_jarvis_mission_coordinator.py's own exhaustiveness test.
_GATE_STATUSES_NO_ACTION = frozenset({"not_requested", "pending"})
_GATE_STATUSES_ACTIONABLE = frozenset({"approved", "rejected"})


def _consume_gate_if_decided(mission_id: str, state: str) -> bool:
    """Mission 006 (gate-consumption follow-up): reads ONLY
    human_gates.<name>.status -- the field jarvis.mission_write.
    authorize_scope/publish/merge already wrote via chugel.decide_gate(),
    itself reachable only with a real, current-turn José attribution.
    This function never writes decided_by, approved_for, or status --
    chugel.decide_gate() remains the only path to any of those. Returns
    True (the caller should re-derive the next action via _recurse())
    only if a transition actually ran; False (the caller should report
    GATE_REQUIRED itself, exactly as before this function existed) if the
    gate is still "pending"/"not_requested" -- side-effect-free in that
    case, safe to call on every advance() invocation regardless of
    trigger (a real notify(), or recover_on_startup() after a crash:
    resuming an already-granted authorization is not the same as
    granting one implicitly). chugel.transition()'s own can_transition()
    independently re-verifies the target state's evidence -- this
    function does not need to duplicate that check to be safe against
    its own bugs.

    Fails closed (ValueError) for any status outside GATE_STATUSES'S own
    four declared values -- an on-disk Mission Record with a status
    validate_mission_record() itself would already reject cannot reach
    this function through any real Chugel write path, so this is
    defense-in-depth against a corrupted/hand-edited record, never a
    reachable production behavior."""
    gate_name = _GATE_NAME_FOR_STATE[state]
    record = chugel.get_mission(mission_id)
    gate = (record.get("human_gates") or {}).get(gate_name) or {}
    status = gate.get("status")
    if status in _GATE_STATUSES_NO_ACTION:
        return False
    if status == "approved":
        target = _GATE_APPROVED_TARGET[state]
    elif status == "rejected":
        target = _GATE_REJECTED_TARGET
    else:
        raise ValueError(
            f"mission {mission_id}: human_gates.{gate_name}.status is {status!r}, "
            f"not one of orchestrator.validator.GATE_STATUSES {sorted(GATE_STATUSES)} "
            "-- refusing to guess whether this gate was decided"
        )
    chugel.transition(mission_id, target, actor=_SYSTEM_ACTOR, reason=_GATE_CONSUMED_REASON)
    return True


def _last_transition_reason(record: dict) -> str:
    """chugel.transition() appends to state_history but does not update
    the top-level state_reason field (which stays whatever create_mission()
    set it to, "mission created", forever) -- the reason for the current
    state is always state_history[-1]["reason"], never record["state_reason"]."""
    history = record.get("state_history") or []
    return history[-1].get("reason", "") if history else ""


# Verification Hardening V1, Pillar 1 (contract checks): advance()'s
# closed vocabulary of report statuses -- previously documented only as a
# Python comment on the field below, never a real, checkable constant.
# Declared here so CoordinatorReport.__post_init__ can fail closed against
# a typo'd or genuinely-new-but-forgotten status at every construction
# site, and so both real exhaustiveness tests have a source of truth:
# tests/test_jarvis_mission_coordinator.py's
# VocabularioCerradoDeCoordinatorReportTests (closure: every declared
# status is constructible, an undeclared one is rejected) and
# tests/test_jarvis_mission_supervisor.py's
# CoordinatorReportStatusExhaustivenessTests (the real per-status
# behavior against _drain_pass() itself -- construction alone proves
# nothing about how the consumer treats each value).
COORDINATOR_REPORT_STATUSES = frozenset({
    "GATE_REQUIRED", "BLOCKED", "HUMAN_ACTION_REQUIRED",
    "TERMINAL_FAILURE", "MERGED", "WORKSPACE_OCCUPIED",
})


@dataclass(frozen=True)
class CoordinatorReport:
    status: str
    state: str
    gate_name: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in COORDINATOR_REPORT_STATUSES:
            raise ValueError(
                f"CoordinatorReport.status must be one of {sorted(COORDINATOR_REPORT_STATUSES)}, "
                f"got {self.status!r} -- every construction site in this module must use "
                "one of the declared statuses, never an ad hoc string"
            )


# Verification Hardening V1, Pillar 3 (Progress Watchdog): hoisted out of
# advance()'s own parameter default so it has one real, importable,
# canonical source -- previously this value existed nowhere but as a bare
# literal in that one default, which the watchdog would otherwise have
# had to either duplicate (a magic-number-drift risk) or reach into via
# `advance.__defaults__` introspection (fragile, effectively the same
# duplication one layer removed). Mechanical extraction only: advance()'s
# real default behavior is unchanged -- see
# tests/test_jarvis_mission_coordinator.py's own regression proving the
# parameter's effective default still resolves to this constant, so a
# future edit to one without the other is caught immediately.
DEFAULT_CI_POLL_TIMEOUT_SECONDS = 1800.0


def advance(
    mission_id: str,
    adapters: dict,
    *,
    repository_root: str,
    branch: str,
    pr_title: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
    ci_poll_timeout_seconds: float = DEFAULT_CI_POLL_TIMEOUT_SECONDS,
    ci_poll_interval_seconds: float = 30.0,
    build_review_deadline: float | None = None,
) -> CoordinatorReport:
    record = chugel.get_mission(mission_id)
    state = record["state"]

    # Shared by every branch below that recurses after completing a
    # mechanical step -- re-derives the next action from the freshly-
    # transitioned record rather than duplicating the state-dispatch
    # logic at each call site. Pure convenience: behaviorally identical
    # to writing out the same advance(mission_id, adapters, ...) call
    # with every kwarg forwarded, every time.
    def _recurse() -> CoordinatorReport:
        return advance(mission_id, adapters, repository_root=repository_root, branch=branch,
                        pr_title=pr_title, git_executable=git_executable, gh_executable=gh_executable,
                        ci_poll_timeout_seconds=ci_poll_timeout_seconds,
                        ci_poll_interval_seconds=ci_poll_interval_seconds,
                        build_review_deadline=build_review_deadline)

    if state == "INTAKE":
        # The one transition this function ever makes without a human
        # decision behind it -- see the module docstring.
        chugel.transition(mission_id, "SCOPE_AWAITING_AUTHORIZATION", actor=_SYSTEM_ACTOR, reason=_INTAKE_ADVANCE_REASON)
        return _recurse()

    if state == "SCOPE_AWAITING_AUTHORIZATION":
        if _consume_gate_if_decided(mission_id, state):
            return _recurse()
        return CoordinatorReport("GATE_REQUIRED", state, "scope_authorization")

    if state in ("AUTHORIZED", "BUILDING", "VERIFYING", "AWAITING_REVIEW", "REVIEWING",
                 "CHANGES_REQUIRED", "CORRECTING"):
        # Jarvis God Mode M1 workspace guard: real dispatch below touches
        # the one repository_root every mission currently shares (see
        # _REPOSITORY_ROOT_OWNING_STATES's own docstring). Checked
        # here, before autonomous_runner.run_mission() ever runs -- never
        # after -- so a second mission is never even offered a dispatch
        # attempt while another one is confirmed already using it.
        occupant = _mission_occupying_repository_root(mission_id)
        if occupant is not None:
            return CoordinatorReport(
                "WORKSPACE_OCCUPIED", state,
                reason=f"repository_root is occupied by mission {occupant}",
            )
        try:
            result = autonomous_runner.run_mission(
                mission_id, adapters, deadline=build_review_deadline,
            )
        except InvocationNotAuthorized as exc:
            return CoordinatorReport("HUMAN_ACTION_REQUIRED", state, reason=str(exc))
        if result.status == "AUTHORIZATION_REQUIRED":
            gate_name = {
                "SCOPE_AWAITING_AUTHORIZATION": "scope_authorization",
                "PUBLISH_AWAITING_AUTHORIZATION": "publish_authorization",
                "MERGE_AWAITING_AUTHORIZATION": "merge_authorization",
            }[result.state]
            return CoordinatorReport("GATE_REQUIRED", result.state, gate_name)
        if result.status == "TERMINAL_FAILURE":
            return CoordinatorReport("TERMINAL_FAILURE", result.state, reason=result.reason)
        if result.status == "COMPLETED":
            return advance(mission_id, adapters, repository_root=repository_root, branch=branch,
                            pr_title=pr_title, git_executable=git_executable, gh_executable=gh_executable,
                            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
                            ci_poll_interval_seconds=ci_poll_interval_seconds,
                            build_review_deadline=build_review_deadline)
        return CoordinatorReport("HUMAN_ACTION_REQUIRED", result.state, reason=result.reason)

    if state == "PUBLISH_AWAITING_AUTHORIZATION":
        if _consume_gate_if_decided(mission_id, state):
            return _recurse()
        return CoordinatorReport("GATE_REQUIRED", state, "publish_authorization")

    if state in ("PUBLISHING", "CI_PENDING"):
        # Same workspace guard as above -- publish_executor.run() below
        # does real `git push`/`gh pr create`/CI polling against
        # repository_root. A mission already IN this branch (state ==
        # PUBLISHING/CI_PENDING) is itself excluded by
        # _mission_occupying_repository_root()'s own mission_id check,
        # so this is a genuine check for a DIFFERENT occupant, not a
        # mission blocking its own continued progress.
        occupant = _mission_occupying_repository_root(mission_id)
        if occupant is not None:
            return CoordinatorReport(
                "WORKSPACE_OCCUPIED", state,
                reason=f"repository_root is occupied by mission {occupant}",
            )
        result = publish_executor.run(
            mission_id, repository_root=repository_root, branch=branch, pr_title=pr_title,
            git_executable=git_executable, gh_executable=gh_executable,
            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
            ci_poll_interval_seconds=ci_poll_interval_seconds,
        )
        if result.status == "COMPLETED":
            return advance(mission_id, adapters, repository_root=repository_root, branch=branch,
                            pr_title=pr_title, git_executable=git_executable, gh_executable=gh_executable,
                            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
                            ci_poll_interval_seconds=ci_poll_interval_seconds,
                            build_review_deadline=build_review_deadline)
        return CoordinatorReport("BLOCKED", result.state, reason=result.reason)

    if state == "MERGE_AWAITING_AUTHORIZATION":
        # Unchanged, and deliberately still runs before the gate check
        # below regardless of whether the gate is decided yet -- repair
        # is about publish-identity data (commit_sha), not about the
        # merge gate's own authorization, and is idempotent/safe to run
        # on every call exactly as it already was in Mission 004.
        publish_identity_repair.repair_if_needed(mission_id, gh_executable=gh_executable)
        record = chugel.get_mission(mission_id)
        if record["state"] == "BLOCKED":
            return CoordinatorReport("BLOCKED", "BLOCKED", reason=_last_transition_reason(record))
        if _consume_gate_if_decided(mission_id, record["state"]):
            return _recurse()
        return CoordinatorReport("GATE_REQUIRED", record["state"], "merge_authorization")

    if state == "MERGING":
        # Same workspace guard -- merge_executor.run() below does a real
        # `git`/`gh` merge against repository_root.
        occupant = _mission_occupying_repository_root(mission_id)
        if occupant is not None:
            return CoordinatorReport(
                "WORKSPACE_OCCUPIED", state,
                reason=f"repository_root is occupied by mission {occupant}",
            )
        result = merge_executor.run(
            mission_id, repository_root=repository_root,
            git_executable=git_executable, gh_executable=gh_executable,
        )
        if result.status == "COMPLETED":
            return CoordinatorReport("MERGED", "MERGED")
        return CoordinatorReport("BLOCKED", result.state, reason=result.reason)

    if state == "MERGED":
        return CoordinatorReport("MERGED", state)

    if state == "BLOCKED":
        return CoordinatorReport("BLOCKED", state, reason=_last_transition_reason(record))

    if state in _TERMINAL_FAILURE_STATES:
        return CoordinatorReport("TERMINAL_FAILURE", state, reason=_last_transition_reason(record))

    return CoordinatorReport("HUMAN_ACTION_REQUIRED", state, reason="no automatic action is defined")


def executive_summary(mission_id: str) -> str:
    record = chugel.get_mission(mission_id)
    outcome = record["mission_definition_history"][-1]["outcome"]
    state = record["state"]

    if state == "MERGED":
        result_line = "MERGED"
    elif state == "BLOCKED":
        result_line = f"BLOCKED: {_last_transition_reason(record)}"
    else:
        result_line = f"STOPPED: {state}"

    changed_files = []
    for entry in record.get("builder_evidence") or []:
        changed_files.extend(item["path"] for item in entry.get("changed_files") or [])

    verdicts = [e.get("verdict") for e in (record.get("reviewer_evidence") or [])]
    ci_runs = (record.get("publish") or {}).get("ci_runs") or []
    ci_line = ci_runs[-1]["conclusion"] if ci_runs else "n/a"

    pr_url = (record.get("publish") or {}).get("pr_url") or "n/a"
    merge_sha = (record.get("merge") or {}).get("merge_commit_sha") or "n/a"

    needs = "nothing" if state in ("MERGED",) else f"resolve {state} and confirm to continue"

    return (
        f"Mission 004 -- {outcome}\n"
        f"Result: {result_line}\n"
        f"What changed: {', '.join(sorted(set(changed_files))) or 'n/a'}\n"
        f"Verification: Emma verdict(s) {verdicts or 'n/a'}, CI {ci_line}\n"
        f"PR: {pr_url}   Merge commit: {merge_sha}\n"
        f"Needs from you: {needs}\n"
    )
