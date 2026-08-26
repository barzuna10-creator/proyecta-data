"""Deterministic transition gate for Zentra Mission Records.

Thin, pure layer on top of orchestrator/validator.py: this module's only
job is `can_transition(record, target_state) -> TransitionCheck`. It never
mutates `record`, never performs I/O, never calls an LLM, never invokes
David/Emilio/Emma, and never merges or deploys anything. It answers
exactly one question -- "is this transition legal right now, given the
evidence already in the record?" -- and answers it by denying with
structured reasons whenever it cannot say yes with confidence."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.validator import (
    TRANSITIONS,
    ValidationError,
    evidence_errors_for_state,
    validate_mission_record,
)


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    reasons: tuple[ValidationError, ...]


def can_transition(record: dict, target_state: str) -> TransitionCheck:
    """Fails closed at every step:

    1. If the record is not itself internally valid, no transition out of
       it can be trusted -- deny, citing the underlying validation errors.
    2. If (current_state, target_state) is not in the canonical transition
       table, deny.
    3. If the target state's minimum entry evidence (orchestrator/
       MISSION_RECORD.md's state table) would not actually be satisfied,
       deny -- simulated without mutating the real record.

    Only if all three hold does this return allowed=True. This function
    never itself changes `record`, never decides whether the underlying
    human authorization SHOULD be granted (that's José's decision, already
    captured or not in the record before this function ever runs), and
    never substitutes for or pre-decides Emma's review -- it only checks
    whether her verdict, if present, is internally consistent and whether a
    resulting transition the record claims is legal actually is."""
    base_result = validate_mission_record(record)
    if not base_result.valid:
        return TransitionCheck(False, tuple(
            ValidationError(
                "BASE_RECORD_INVALID",
                f"cannot evaluate a transition on an internally invalid record ({error.code}: {error.message})",
                error.path,
            )
            for error in base_result.errors
        ))

    current_state = record.get("state")
    pair = (current_state, target_state)
    if pair not in TRANSITIONS:
        return TransitionCheck(False, (ValidationError(
            "ILLEGAL_STATE_TRANSITION",
            f"{current_state!r} -> {target_state!r} is not a legal transition in the canonical table",
            "$.state",
        ),))

    evidence_errors = evidence_errors_for_state(record, target_state)
    if evidence_errors:
        return TransitionCheck(False, evidence_errors)

    return TransitionCheck(True, ())
