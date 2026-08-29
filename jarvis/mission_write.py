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

from orchestrator import chugel
from orchestrator.validator import HUMAN_DECIDER

_GATE_STATE = {
    "scope_authorization": "SCOPE_AWAITING_AUTHORIZATION",
    "publish_authorization": "PUBLISH_AWAITING_AUTHORIZATION",
    "merge_authorization": "MERGE_AWAITING_AUTHORIZATION",
}

_RESUMABLE_PRIOR_STATES = frozenset({
    "PUBLISHING", "CI_PENDING", "MERGE_AWAITING_AUTHORIZATION", "MERGING",
})


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


def create_mission(intent_text: str, mission_definition: dict, decision: dict, *, mission_id: str | None = None) -> dict:
    """The only path Jarvis has to create a Mission Record. `decision`
    must carry the current-turn José attribution used for
    mission_definition["authorized_by"]; orchestrator.chugel.create_mission()
    itself already hard-refuses unless that field is literally
    HUMAN_DECIDER -- this function's re-check is defensive, not the only
    enforcement, exactly like _authorize() above."""
    _require_current_turn_attribution(decision)
    return chugel.create_mission(intent_text, mission_definition, mission_id=mission_id)


def create_mission_if_absent(
    intent_text: str, mission_definition: dict, decision: dict, *, mission_id: str,
) -> dict:
    """Control Plane V1: identical to create_mission() (same attribution
    requirement, same underlying write), except a MissionRecordAlreadyExists
    at the given `mission_id` is treated as idempotent success -- the
    existing record is returned, not raised -- rather than as a caller
    error. Exists specifically so jarvis.mission_authorization_bridge,
    which is not one of Chugel's three disclosed import seams, never needs
    to import orchestrator.chugel itself just to catch this one exception;
    this stays inside mission_write.py's own already-allowed write seam."""
    _require_current_turn_attribution(decision)
    try:
        return chugel.create_mission(intent_text, mission_definition, mission_id=mission_id)
    except chugel.MissionRecordAlreadyExists:
        return chugel.get_mission(mission_id)


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
    restricted to the four Mission 004 V1 resumable states; any other
    prior state, or any Chugel-level rejection of the resulting
    transition, fails closed here."""
    _require_current_turn_attribution(decision)
    record = chugel.get_mission(mission_id)
    if record["state"] != "BLOCKED":
        raise ResumeNotEligible(mission_id, f"state is {record['state']!r}, not BLOCKED")

    prior_state = record["state_history"][-1]["from_state"]
    if prior_state not in _RESUMABLE_PRIOR_STATES:
        raise ResumeNotEligible(
            mission_id,
            f"BLOCKED was entered from {prior_state!r}, which Mission 004 V1 "
            "does not support resuming",
        )

    return chugel.transition(
        mission_id, prior_state, actor="chugel",
        reason="resumed from BLOCKED on José's explicit confirmation",
    )
