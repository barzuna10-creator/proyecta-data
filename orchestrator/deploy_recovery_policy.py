"""M4 (Automatic Production Deployment Observation & Verification) --
one pure function over one exhaustive, frozen table.

recovery_action_for() answers exactly one question -- "given how a
mission's deploy observation last failed, and whether its recovery budget
is exhausted, what may happen next?" -- and answers it purely from its own
arguments. No I/O, no mission-record read, no Chugel import, no clock
read of its own (the caller computes remaining_budget_seconds and passes
it in): this module is trivially unit-testable and has no side effects
whatsoever."""

from __future__ import annotations

import enum


class RecoveryAction(enum.Enum):
    """PLAIN_RESUME: automatic resume is safe -- the failure was
    transient/inconclusive and budget remains, no human acknowledgement
    required (jarvis/mission_write.py's resume_from_blocked() is still the
    only path there, but it does not itself require a NEW
    reopen_deploy_observation_window() call).
    EXCEPTIONAL_REOPEN_REQUIRED: only orchestrator.chugel.
    reopen_deploy_observation_window() (José's literal, current-turn
    acknowledgement) may move this mission forward again.
    RECOVERY_EXHAUSTED: deploy.recovery_status is already 'exhausted' --
    no further reopen is ever possible for this mission.
    DENY: an unrecognized/unclassified combination -- fails closed rather
    than guessing."""

    PLAIN_RESUME = "plain_resume"
    EXCEPTIONAL_REOPEN_REQUIRED = "exceptional_reopen_required"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    DENY = "deny"


def recovery_action_for(
    classification: str | None,
    *,
    recovery_status: str,
    remaining_budget_seconds: float,
) -> RecoveryAction:
    if recovery_status == "exhausted":
        return RecoveryAction.RECOVERY_EXHAUSTED
    if classification in {
        "ANCESTRY_UNVERIFIABLE", "HEALTH_UNREACHABLE_OR_MALFORMED", "IDENTITY_NEVER_OBSERVED",
    } and remaining_budget_seconds > 0:
        return RecoveryAction.PLAIN_RESUME
    if classification in {
        "OBSERVATION_BUDGET_EXHAUSTED", "IDENTITY_MISMATCH_DEFINITIVE",
        "HEALTH_DEGRADED", "IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK",
        "IDENTITY_NEVER_OBSERVED",
    }:
        return RecoveryAction.EXCEPTIONAL_REOPEN_REQUIRED
    return RecoveryAction.DENY
