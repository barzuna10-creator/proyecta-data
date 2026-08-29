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
    its own bugs."""
    gate_name = _GATE_NAME_FOR_STATE[state]
    record = chugel.get_mission(mission_id)
    gate = (record.get("human_gates") or {}).get(gate_name) or {}
    status = gate.get("status")
    if status == "approved":
        target = _GATE_APPROVED_TARGET[state]
    elif status == "rejected":
        target = _GATE_REJECTED_TARGET
    else:
        return False  # "pending" / "not_requested" / anything else -- no side effect
    chugel.transition(mission_id, target, actor=_SYSTEM_ACTOR, reason=_GATE_CONSUMED_REASON)
    return True


def _last_transition_reason(record: dict) -> str:
    """chugel.transition() appends to state_history but does not update
    the top-level state_reason field (which stays whatever create_mission()
    set it to, "mission created", forever) -- the reason for the current
    state is always state_history[-1]["reason"], never record["state_reason"]."""
    history = record.get("state_history") or []
    return history[-1].get("reason", "") if history else ""


@dataclass(frozen=True)
class CoordinatorReport:
    status: str  # "GATE_REQUIRED" | "BLOCKED" | "HUMAN_ACTION_REQUIRED" | "TERMINAL_FAILURE" | "MERGED"
    state: str
    gate_name: str | None = None
    reason: str = ""


def advance(
    mission_id: str,
    adapters: dict,
    *,
    repository_root: str,
    branch: str,
    pr_title: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
    ci_poll_timeout_seconds: float = 1800.0,
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
