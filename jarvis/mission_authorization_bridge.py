"""Control Plane V1 -- the single bridge from a validated MissionDraft
authorization intent to a real Chugel Mission Record.

decided_by="jose" attribution is NEVER produced here. This module's only
entry point, close_draft_authorization(), requires the caller to already
supply a fully-constructed `decision` dict -- exactly like
jarvis.mission_write's own functions -- and performs the same defensive
literal re-check jarvis.mission_write._require_current_turn_attribution()
does: never a default, never inferred from anything about the request
itself, never generated automatically by any retry or background process.
The only legitimate caller is jarvis.control_plane_server's draft-
authorization handler, and only after authentication, CSRF/origin,
the literal confirmation string, and an exact digest/revision match have
already passed there. This module trusts none of that context and only
re-verifies what it can independently check: the draft's own identity
against the exact digest/revision the caller claims to have reviewed.

Idempotent and crash-safe by construction, using the same "durable record
presence is the only idempotency signal" discipline as the rest of
Mission 004 (see orchestrator/publish_commit_materializer.py and
jarvis/mission_write.py's resume_from_blocked()):

1. A deterministic mission_id, derived from the authorization intent's own
   identity (uuid5 of draft_id:revision:digest), never random -- so even
   if this function crashes after the mission has already durably been
   created but before its own effect record is written, a retry that
   reaches jarvis.mission_write.create_mission_if_absent() with the same
   mission_id finds it already exists and returns the existing record,
   rather than creating a second mission.
2. A separate, atomically-created "authorization effect" record
   (jarvis.storage.FileJarvisStore.record/get_authorization_effect) is the
   durable proof that this exact intent has already been closed -- checked
   first, before any Chugel write is even attempted.

Every step runs under jarvis._safe_io.exclusive_entity_lock(draft_id), so
two concurrent identical retries cannot race past each other and both
believe they were the one to create the mission.

This module deliberately never imports orchestrator.chugel: Chugel access
is structurally confined to exactly three disclosed seams in this package
(jarvis.mission_query, jarvis.mission_write, jarvis.mission_coordinator --
enforced by tests/test_jarvis_foundation_boundaries.py), and this module
is none of them. Every Chugel effect this module needs --
create-if-absent semantics included -- is obtained by calling
jarvis.mission_write, never by importing chugel directly."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from orchestrator.validator import HUMAN_DECIDER

from jarvis import mission_query, mission_write
from jarvis._safe_io import exclusive_entity_lock
from jarvis.authorization import validate_authorization_intent
from jarvis.models import AuthorizationIntent
from jarvis.storage import (
    AuthorizationIntentAlreadyRecorded, authorization_intent_id, FileJarvisStore,
)

# Fixed, never-changing namespace -- deterministic mission_id derivation
# must be stable across processes and restarts, never random per call.
_MISSION_ID_NAMESPACE = uuid.UUID("6a7f6e2e-3b1a-4e9a-9e2a-5f6c7d8e9f00")


class DraftAuthorizationError(Exception):
    pass


class DraftAuthorizationRefused(DraftAuthorizationError):
    """The intent did not match the current draft exactly -- stale
    revision, digest mismatch, corrupt/unauthorization-ready draft, or an
    unknown draft_id. Fail closed: no Chugel write is ever attempted."""

    def __init__(self, reasons):
        codes = ", ".join(reason.code for reason in reasons)
        super().__init__(f"draft authorization refused: {codes}")
        self.reasons = reasons


class DraftAuthorizationAttributionError(DraftAuthorizationError):
    """decision["decided_by"] was not the literal HUMAN_DECIDER, or a
    previously-recorded effect for this exact intent_id names a different
    mission_id than the one just derived -- both are refused rather than
    silently trusting one side."""
    pass


class DraftAuthorizationDivergenceError(DraftAuthorizationError):
    """A local authorization-effect record names a mission_id that no
    longer reads back cleanly through mission_query -- not found, corrupt,
    or otherwise invalid. The local effect record is evidence of what this
    process believes happened; it is never treated as proof that Chugel's
    canonical Mission Record still agrees, and a local record that no
    longer corresponds to a real, readable mission is refused rather than
    reported as already_effective=True."""
    pass


def _derived_mission_id(intent_id: str) -> str:
    return str(uuid.uuid5(_MISSION_ID_NAMESPACE, intent_id))


@dataclass(frozen=True)
class DraftAuthorizationResult:
    mission_id: str
    intent_id: str
    already_effective: bool


def close_draft_authorization(
    store: FileJarvisStore,
    intent: AuthorizationIntent,
    decision: dict,
) -> DraftAuthorizationResult:
    """Precondition: the caller has already independently authenticated
    the request, checked CSRF/origin, and confirmed the literal
    confirmation string -- none of that is re-verified here. What IS
    re-verified, unconditionally, is that `intent` matches the draft's
    current, on-disk, digest-verified state exactly."""
    if decision.get("decided_by") != HUMAN_DECIDER:
        raise DraftAuthorizationAttributionError(
            f"refusing to close a draft authorization not attributed to the "
            f"literal {HUMAN_DECIDER!r}, got {decision.get('decided_by')!r}"
        )

    with exclusive_entity_lock(store.root, intent.draft_id):
        current = store.get_latest_draft(intent.draft_id)  # DraftNotFound propagates
        check = validate_authorization_intent(intent, current=current)
        if not check.allowed:
            raise DraftAuthorizationRefused(check.reasons)

        intent_id = authorization_intent_id(intent)
        mission_id = _derived_mission_id(intent_id)

        existing_effect = store.get_authorization_effect(intent_id)
        if existing_effect is not None:
            if existing_effect != mission_id:
                raise DraftAuthorizationAttributionError(
                    f"authorization effect for intent {intent_id!r} already recorded "
                    f"as mission {existing_effect!r}, which does not match the "
                    f"deterministically derived {mission_id!r} -- refusing to trust "
                    "either value"
                )
            # The local effect record is never sufficient on its own: it is
            # evidence of what this process previously believed, not proof
            # that Chugel's canonical Mission Record still agrees. Re-read
            # it through the one disclosed read seam before reporting
            # success -- a missing/corrupt/invalid canonical record fails
            # closed here rather than being reported as already_effective.
            try:
                mission_query.get_mission_status(mission_id)
            except mission_query.MissionQueryError as exc:
                raise DraftAuthorizationDivergenceError(
                    f"authorization effect for intent {intent_id!r} names mission "
                    f"{mission_id!r}, but it no longer reads back through mission_query "
                    f"({exc.code}) -- refusing to report this authorization as effective"
                ) from exc
            return DraftAuthorizationResult(
                mission_id=mission_id, intent_id=intent_id, already_effective=True,
            )

        # record_authorization_intent() is itself idempotent (dedup by the
        # same intent_id) -- a prior crashed attempt may have already
        # recorded it without ever reaching create_mission(); that is not
        # an error here, just evidence a retry is in progress.
        try:
            store.record_authorization_intent(intent)
        except AuthorizationIntentAlreadyRecorded:
            pass

        draft = current.draft
        mission_definition = {
            "outcome": draft.mission_definition.outcome,
            "scope": list(draft.mission_definition.scope),
            "non_goals": list(draft.mission_definition.non_goals),
            "acceptance_criteria": list(draft.mission_definition.acceptance_criteria),
            "authorized_by": HUMAN_DECIDER,
            "authorized_at": decision.get("decided_at"),
            "authorization_decision_ref": intent_id,
        }
        mission_write.create_mission_if_absent(
            draft.raw_intent, mission_definition, decision, mission_id=mission_id,
        )

        store.record_authorization_effect(intent_id, mission_id)
        store.mark_draft_authorized(intent.draft_id)
        return DraftAuthorizationResult(
            mission_id=mission_id, intent_id=intent_id, already_effective=False,
        )
