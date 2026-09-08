"""Mission 004 -- the sole Jarvis production module permitted to import
orchestrator.chugel's write operations (create_mission, decide_gate,
transition). Every function here requires the caller to already hold an
attribution verified this turn, directly from José's own message; this
module performs no verification of its own beyond a defensive,
literal-string re-check identical to what orchestrator.chugel already
enforces -- defense in depth, never the only check, and never a
substitute for the calling agent turn actually having a real,
current-turn José message to relay."""

from __future__ import annotations

import datetime

from orchestrator import chugel
from orchestrator.deploy_recovery_policy import RecoveryAction, recovery_action_for
from orchestrator.validator import HUMAN_DECIDER

_GATE_STATE = {
    "scope_authorization": "SCOPE_AWAITING_AUTHORIZATION",
    "publish_authorization": "PUBLISH_AWAITING_AUTHORIZATION",
    "merge_authorization": "MERGE_AWAITING_AUTHORIZATION",
}

# M4: extended from the original four states to six -- DEPLOY_PENDING and
# VERIFYING_PRODUCTION are now resumable too, subject to the additional
# recovery-policy check resume_from_blocked() performs below for exactly
# those two states (see step 3 in that function's own docstring).
_RESUMABLE_PRIOR_STATES = frozenset({
    "PUBLISHING", "CI_PENDING", "MERGE_AWAITING_AUTHORIZATION", "MERGING",
    "DEPLOY_PENDING", "VERIFYING_PRODUCTION",
})

# M4: total production-observation budget, mirrored from
# orchestrator/deploy_verifier.py's own TOTAL_OBSERVATION_BUDGET_SECONDS --
# duplicated here (not imported) so this module, already one of Chugel's
# disclosed write seams, has no need to depend on deploy_verifier.py, which
# is an execution module, not a write seam.
_DEPLOY_OBSERVATION_BUDGET_SECONDS = 900.0

_DEPLOY_RESUMABLE_PRIOR_STATES = frozenset({"DEPLOY_PENDING", "VERIFYING_PRODUCTION"})


class MissionWriteError(Exception):
    pass


class GateNotYetEligible(MissionWriteError):
    def __init__(self, mission_id: str, gate_name: str, actual_state: str):
        super().__init__(
            f"mission {mission_id}: {gate_name} not eligible in state {actual_state!r}"
        )
        self.mission_id = mission_id
        self.gate_name = gate_name
        self.actual_state = actual_state


class ResumeNotEligible(MissionWriteError):
    def __init__(self, mission_id: str, reason: str):
        super().__init__(f"mission {mission_id}: {reason}")
        self.mission_id = mission_id


class DeployReopenNotEligible(MissionWriteError):
    """M6: reopen_deploy_observation_window() (this module's function,
    below) was called but orchestrator.chugel.reopen_deploy_observation_window()
    itself refused -- the mission is not currently BLOCKED, or
    deploy.recovery_status is not 'available' (already 'exhausted', so no
    further reopen is ever possible). Exists for exactly the same reason
    GateNotYetEligible/ResumeNotEligible do: jarvis.control_plane_server
    never imports orchestrator.chugel directly (it is not one of the
    three disclosed Chugel seams -- see that module's own docstring and
    tests/test_jarvis_foundation_boundaries.py), so it needs a
    mission_write-level exception type to catch instead of
    orchestrator.chugel.DeployRecoveryNotEligible."""

    def __init__(self, mission_id: str, reason: str):
        super().__init__(f"mission {mission_id}: {reason}")
        self.mission_id = mission_id


class DeployRecoveryRequired(MissionWriteError):
    """M4: resume_from_blocked() was called for a mission BLOCKED from
    DEPLOY_PENDING/VERIFYING_PRODUCTION, but orchestrator.deploy_recovery_policy.
    recovery_action_for() says plain resume is not safe for this
    classification/budget/recovery_status combination. No transition is
    performed -- either the mission's recovery budget is exhausted
    (`action` is RECOVERY_EXHAUSTED) or an exceptional
    orchestrator.chugel.reopen_deploy_observation_window() call, carrying
    José's own literal acknowledgement, is required first (`action` is
    EXCEPTIONAL_REOPEN_REQUIRED) -- or the combination is simply
    unrecognized and this fails closed (`action` is DENY)."""

    def __init__(self, mission_id: str, action, classification: str | None):
        super().__init__(
            f"mission {mission_id}: plain resume is not safe for deploy-observation "
            f"classification {classification!r} -- recovery_action_for() says {action!r}"
        )
        self.mission_id = mission_id
        self.action = action
        self.classification = classification


def _require_current_turn_attribution(decision: dict) -> None:
    """Defensive re-check, not the only enforcement -- orchestrator.chugel
    itself already hard-refuses decide_gate()/create_mission() unless the
    attribution field is literally HUMAN_DECIDER. This exists so a bug in
    this module's own call sites fails here, immediately, rather than
    only inside chugel's deeper write path."""
    if decision.get("decided_by") != HUMAN_DECIDER:
        raise MissionWriteError(
            f"refusing to relay a gate/resume decision not attributed to "
            f"the literal {HUMAN_DECIDER!r}, got {decision.get('decided_by')!r}"
        )


def _authorize(mission_id: str, gate_name: str, decision: dict) -> dict:
    record = chugel.get_mission(mission_id)
    if record["state"] != _GATE_STATE[gate_name]:
        raise GateNotYetEligible(mission_id, gate_name, record["state"])
    _require_current_turn_attribution(decision)
    return chugel.decide_gate(mission_id, gate_name, decision)


def create_mission(intent_text: str, mission_definition: dict, decision: dict, *, mission_id: str | None = None,
                   repository: dict | None = None, origin: dict | None = None) -> dict:
    """The only path Jarvis has to create a Mission Record. `decision`
    must carry the current-turn José attribution used for
    mission_definition["authorized_by"]; orchestrator.chugel.create_mission()
    itself already hard-refuses unless that field is literally
    HUMAN_DECIDER -- this function's re-check is defensive, not the only
    enforcement, exactly like _authorize() above. `origin` (M7) is relayed
    verbatim -- see orchestrator.chugel.create_mission()'s own docstring."""
    _require_current_turn_attribution(decision)
    return chugel.create_mission(
        intent_text, mission_definition, mission_id=mission_id, repository=repository, origin=origin,
    )


def create_mission_if_absent(
    intent_text: str, mission_definition: dict, decision: dict, *, mission_id: str,
    repository: dict | None = None, origin: dict | None = None,
) -> dict:
    """Control Plane V1: identical to create_mission() (same attribution
    requirement, same underlying write), except a MissionRecordAlreadyExists
    at the given `mission_id` is treated as idempotent success -- the
    existing record is returned, not raised -- rather than as a caller
    error. Exists specifically so jarvis.mission_authorization_bridge,
    which is not one of Chugel's three disclosed import seams, never needs
    to import orchestrator.chugel itself just to catch this one exception;
    this stays inside mission_write.py's own already-allowed write seam.

    `origin` (M7, Program-Level Planning Depth): relayed to
    chugel.create_mission() verbatim on first creation; on a retry that
    finds an existing record, it is compared for divergence exactly like
    `repository` already is below -- a retry that supplies a different
    origin than what was actually persisted is refused, never silently
    ignored."""
    _require_current_turn_attribution(decision)
    try:
        return chugel.create_mission(
            intent_text, mission_definition, mission_id=mission_id, repository=repository, origin=origin,
        )
    except chugel.MissionRecordAlreadyExists:
        existing = chugel.get_mission(mission_id)
        expected_definition = {
            "outcome": mission_definition["outcome"],
            "scope": mission_definition["scope"],
            "non_goals": mission_definition.get("non_goals", []),
            "acceptance_criteria": mission_definition["acceptance_criteria"],
            "authorized_by": mission_definition["authorized_by"],
            "authorized_at": mission_definition["authorized_at"],
            "authorization_decision_ref": mission_definition["authorization_decision_ref"],
        }
        actual_definition = existing["mission_definition_history"][0]
        if any(actual_definition.get(key) != value for key, value in expected_definition.items()):
            raise MissionWriteError(f"mission {mission_id}: existing definition diverges from retry")
        if repository is not None and existing.get("repository") != repository:
            raise MissionWriteError(f"mission {mission_id}: existing repository binding diverges from retry")
        if origin is not None:
            expected_origin = {
                "objective_id": origin.get("objective_id"),
                "draft_id": origin.get("draft_id") or mission_id,
            }
            if existing.get("origin") != expected_origin:
                raise MissionWriteError(f"mission {mission_id}: existing origin diverges from retry")
        return existing


def authorize_scope(mission_id: str, decision: dict) -> dict:
    return _authorize(mission_id, "scope_authorization", decision)


def authorize_publish(mission_id: str, decision: dict) -> dict:
    return _authorize(mission_id, "publish_authorization", decision)


def authorize_merge(mission_id: str, decision: dict) -> dict:
    """`decision["approved_for"]["head_sha"]`, if the caller supplies one
    at all, is ignored and always overwritten with the mission's current
    `publish.commit_sha` -- orchestrator.validator's own
    `_check_stale_approvals()` requires this field to exactly match the
    currently published commit or the whole record fails validation as a
    STALE_APPROVAL, and this is not a matter of José's judgment to type
    out: it is mechanically derivable from the Mission Record at the
    moment of authorization, exactly like create_mission()'s own
    never-trust-the-caller fields for anything mechanically derivable."""
    record = chugel.get_mission(mission_id)
    if record["state"] != _GATE_STATE["merge_authorization"]:
        raise GateNotYetEligible(mission_id, "merge_authorization", record["state"])
    head_sha = (record.get("publish") or {}).get("commit_sha")
    if head_sha is None:
        raise MissionWriteError(
            f"mission {mission_id}: cannot authorize merge before publish.commit_sha is recorded"
        )
    decision = dict(decision)
    decision["approved_for"] = {"head_sha": head_sha}
    _require_current_turn_attribution(decision)
    return chugel.decide_gate(mission_id, "merge_authorization", decision)


def resume_from_blocked(mission_id: str, decision: dict) -> dict:
    """The only path Jarvis has to move a mission out of BLOCKED. Never
    called automatically -- the caller (jarvis.mission_coordinator) must
    hold a literal, current-turn confirmation from José that the
    external issue is resolved. Derives the single legal target state
    mechanically from state_history (never a caller-supplied target),
    restricted to the six Mission 004/M4 V1 resumable states; any other
    prior state, or any Chugel-level rejection of the resulting
    transition, fails closed here.

    Exact ordering (M4 addition is step 3; steps 1/2/4 are unchanged from
    Mission 004):
      1. _require_current_turn_attribution(decision) -- unchanged, runs first.
      2. _RESUMABLE_PRIOR_STATES membership check -- unchanged, now
         evaluated against the six-member set.
      3. NEW, only when prior_state is DEPLOY_PENDING or
         VERIFYING_PRODUCTION: read deploy.last_blocked_classification/
         deploy.recovery_status, compute the mission's remaining
         production-observation budget from deploy.observation_started_at,
         and ask orchestrator.deploy_recovery_policy.recovery_action_for().
         If the result is not RecoveryAction.PLAIN_RESUME, raise
         DeployRecoveryRequired and perform no transition. This step is a
         complete no-op (zero new code executes) for the four pre-existing
         resumable states.
      4. chugel.transition(...) -- unchanged, reached only when step 2
         passed and (for the two deploy states) step 3 returned
         PLAIN_RESUME."""
    _require_current_turn_attribution(decision)
    record = chugel.get_mission(mission_id)
    if record["state"] != "BLOCKED":
        raise ResumeNotEligible(mission_id, f"state is {record['state']!r}, not BLOCKED")

    prior_state = record["state_history"][-1]["from_state"]
    if prior_state not in _RESUMABLE_PRIOR_STATES:
        raise ResumeNotEligible(
            mission_id,
            f"BLOCKED was entered from {prior_state!r}, which Mission 004/M4 V1 "
            "does not support resuming",
        )

    if prior_state in _DEPLOY_RESUMABLE_PRIOR_STATES:
        deploy = record.get("deploy") or {}
        classification = deploy.get("last_blocked_classification")
        recovery_status = deploy.get("recovery_status", "available")
        observation_started_at = deploy.get("observation_started_at")
        if observation_started_at:
            elapsed = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromisoformat(observation_started_at.replace("Z", "+00:00"))
            ).total_seconds()
        else:
            elapsed = _DEPLOY_OBSERVATION_BUDGET_SECONDS
        remaining_budget_seconds = _DEPLOY_OBSERVATION_BUDGET_SECONDS - elapsed

        action = recovery_action_for(
            classification, recovery_status=recovery_status,
            remaining_budget_seconds=remaining_budget_seconds,
        )
        if action != RecoveryAction.PLAIN_RESUME:
            raise DeployRecoveryRequired(mission_id, action, classification)

    return chugel.transition(
        mission_id, prior_state, actor="chugel",
        reason="resumed from BLOCKED on José's explicit confirmation",
    )


def reopen_deploy_observation_window(mission_id: str, *, decided_by: str, acknowledgement: str) -> dict:
    """M6: the only path Jarvis has to reopen a BLOCKED mission's deploy-
    observation window via the exceptional recovery path -- the sole
    additional caller orchestrator.chugel.reopen_deploy_observation_window()
    gains this milestone. Never called automatically, exactly like
    resume_from_blocked() above: the caller (jarvis.control_plane_server)
    must already hold a literal, current-turn confirmation from José, and
    a real, non-empty acknowledgement string, before this is ever
    invoked.

    Attribution is checked here, defensively, in addition to
    orchestrator.chugel.reopen_deploy_observation_window()'s own
    unconditional identical check -- the same defense-in-depth discipline
    _require_current_turn_attribution() gives every other write in this
    module. `chugel.DeployRecoveryNotEligible` (mission not BLOCKED, or
    deploy.recovery_status is not 'available') is translated to this
    module's own DeployReopenNotEligible so jarvis.control_plane_server
    never needs to import orchestrator.chugel directly to catch it."""
    if decided_by != HUMAN_DECIDER:
        raise MissionWriteError(
            f"refusing to relay a deploy-reopen decision not attributed to "
            f"the literal {HUMAN_DECIDER!r}, got {decided_by!r}"
        )
    try:
        return chugel.reopen_deploy_observation_window(
            mission_id, decided_by=decided_by, acknowledgement=acknowledgement,
        )
    except chugel.DeployRecoveryNotEligible as exc:
        raise DeployReopenNotEligible(mission_id, str(exc)) from exc
