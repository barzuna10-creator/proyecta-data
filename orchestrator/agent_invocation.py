"""Agent Invocation Layer V1 -- deterministic request/response envelopes
for invoking Emilio and Emma, per orchestrator/AGENT_INVOCATION_V1.md
(already independently reviewed by Emma, committed unchanged). This module
implements exactly that design; no provider (Claude/Codex) code lives
here -- this module knows nothing about how a concrete AgentInvoker
actually reaches a provider, and no concrete AgentInvoker is implemented
in this increment (orchestrator/PROVIDER_ROUTER_V1.md and
orchestrator/PROVIDER_INTEGRATION_V1.md's adapters are separate,
not-yet-authorized increments).

Chugel remains deterministic code, not an LLM-based reasoning agent
(agents/AGENT_STANDARD.md's exclusion). Every function here either builds
a structurally allow-listed request or consumes an already-produced
result through the existing, unmodified chugel.py operations -- nothing
here invokes a provider, decides a verdict, or touches human_gates.

Deviation from AGENT_INVOCATION_V1.md, disclosed rather than silently
resolved: that design's own signature for the Emilio request builder
(section 6) names a `task_override` parameter without ever explaining its
purpose anywhere in the document's text. Implementing it as "replace the
allow-listed task with arbitrary caller-supplied content" would directly
contradict the allow-list guarantee every other part of this module
exists to enforce, so it is omitted here. See the Increment #12 Builder
Handoff for this decision, flagged for Emma/José to resolve in the source
document if a real need for it is ever identified.

Invocation identity is persisted on the evidence entry itself.  Provider
output never owns these fields: consumers reject any model-supplied copy,
then inject the request/result envelope values into a deep copy immediately
before canonical persistence.  Emma reloads the matching builder attempt's
persisted identity, so restart/resume has the same independence semantics as
an uninterrupted process without adding an invocation_log[]."""

from __future__ import annotations

import copy
import datetime
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from orchestrator import chugel


# --- exception taxonomy -------------------------------------------------
# Kept deliberately small, mirroring chugel.py's own discipline: a
# protocol violation raises immediately, before anything is written;
# a legitimate non-"completed" outcome is not a violation and does not
# raise (see consume_emilio_result()/consume_emma_result()).

class AgentInvocationError(Exception):
    """Base class for every exception this module raises."""


class InvocationIdMismatch(AgentInvocationError):
    """result.invocation_id does not match the request it purports to
    answer -- refused before evidence is ever touched."""


class FreshContextNotAttested(AgentInvocationError):
    """agent_role == 'emma' and fresh_context_attested is not literally
    True -- refused before evidence is ever touched."""


class StaleSessionReused(AgentInvocationError):
    """Emma reused the matching builder attempt's persisted provider ID."""


class InvocationNotAuthorized(AgentInvocationError):
    """The requested role/attempt is not eligible in the Mission Record's
    current state, or evidence for that exact role/attempt already exists.
    Refused while building the request, before any provider is selected or
    invoked."""


class AttemptNumberMismatch(AgentInvocationError):
    """Completed evidence is not bound to the exact requested attempt."""


class ProviderControlledIdentityMetadata(AgentInvocationError):
    """Model evidence attempted to supply infrastructure-owned metadata."""


class PersistedBuilderIdentityUnavailable(AgentInvocationError):
    """The matching builder evidence lacks sufficient persisted identity."""


class IndependenceUnverifiable(AgentInvocationError):
    """Same-provider builder/reviewer results expose no comparable ID."""


# --- structured envelopes ------------------------------------------------
# Provider-neutral by construction (AGENT_INVOCATION_V1.md section 4):
# these describe what Emilio/Emma must produce, never how a provider
# produces it. No concrete AgentInvoker exists in this increment.

class AgentInvoker(Protocol):
    def invoke(self, request: "AgentInvocationRequest") -> "AgentInvocationResult":
        ...


@dataclass(frozen=True)
class AgentInvocationRequest:
    invocation_id: str
    mission_id: str
    agent_role: str  # "emilio" | "emma" | (future) "david"
    attempt: int  # 0 or 1
    task: dict
    requested_at: str
    requested_fresh_context: bool


@dataclass(frozen=True)
class AgentInvocationResult:
    invocation_id: str
    outcome: str  # "completed" | "failed" | "timeout" | "invalid_output" | "unavailable"
    provider: str | None
    model: str | None
    responded_at: str
    fresh_context_attested: bool
    provider_session_id: str | None
    provider_conversation_id: str | None
    evidence: dict | None
    error_detail: str | None


_OUTCOMES = frozenset({"completed", "failed", "timeout", "invalid_output", "unavailable"})
INFRASTRUCTURE_EVIDENCE_FIELDS = frozenset({
    "invocation_id",
    "provider",
    "provider_session_id",
    "provider_conversation_id",
})


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- allow-listed request construction ------------------------------------

def _mission_definition_content(record: dict) -> dict:
    """The current authorized scope's content fields only -- never the
    authorized_by/authorized_at/authorization_decision_ref attribution
    metadata, version, source, or based_on_proposal_id (AGENT_INVOCATION_V1.md
    sections 6-7, unchanged by this implementation)."""
    entry = record["mission_definition_history"][-1]
    return {
        "outcome": entry["outcome"],
        "scope": copy.deepcopy(entry["scope"]),
        "non_goals": copy.deepcopy(entry["non_goals"]),
        "acceptance_criteria": copy.deepcopy(entry["acceptance_criteria"]),
    }


def _cited_findings(record: dict, attempt_zero_only: bool = True) -> list:
    reviewer_evidence = record.get("reviewer_evidence") or []
    entry = next((e for e in reviewer_evidence if e.get("attempt") == 0), None)
    if entry is None:
        return []
    return copy.deepcopy(entry.get("findings", []))


def _persisted_builder_identity(mission_id: str, attempt: int) -> dict:
    """Return the matching builder entry only when it has enough canonical
    infrastructure identity to support a new Emma invocation."""
    record = chugel.get_mission(mission_id)
    entries = record.get("builder_evidence") or []
    entry = next(
        (item for item in entries
         if isinstance(item, dict) and type(item.get("attempt")) is int
         and item.get("attempt") == attempt),
        None,
    )
    if (
        not isinstance(entry, dict)
        or entry.get("invocation_id") is None
        or entry.get("provider") is None
        or (
            entry.get("provider_session_id") is None
            and entry.get("provider_conversation_id") is None
        )
    ):
        raise PersistedBuilderIdentityUnavailable(
            f"mission {mission_id}: builder_evidence attempt {attempt} lacks "
            "invocation_id/provider and at least one persisted provider identity"
        )
    return copy.deepcopy(entry)


def require_eligible_invocation(mission_id: str, *, role: str, attempt: int) -> None:
    """Fail closed before provider dispatch for state, attempt and duplicate
    evidence.  A provider call is an authorized state-machine action, not a
    harmless precursor whose invalidity may be discovered only while
    persisting its result."""
    record = chugel.get_mission(mission_id)
    if type(attempt) is not int or attempt not in (0, 1):
        raise InvocationNotAuthorized(
            f"mission {record.get('mission_id')}: {role} attempt must be the integer 0 or 1, "
            f"got {attempt!r}"
        )

    expected_state = ("BUILDING" if attempt == 0 else "CORRECTING") if role == "emilio" else "REVIEWING"
    if record.get("state") != expected_state:
        raise InvocationNotAuthorized(
            f"mission {record.get('mission_id')}: {role} attempt {attempt} requires state "
            f"{expected_state}, got {record.get('state')!r}"
        )

    evidence_field = "builder_evidence" if role == "emilio" else "reviewer_evidence"
    entries = record.get(evidence_field) or []
    if any(isinstance(entry, dict) and entry.get("attempt") == attempt for entry in entries):
        raise InvocationNotAuthorized(
            f"mission {record.get('mission_id')}: {evidence_field} already contains "
            f"attempt {attempt}; refusing a duplicate provider invocation"
        )
    if role == "emma":
        _persisted_builder_identity(mission_id, attempt)


def build_emilio_invocation_request(mission_id: str, attempt: int) -> AgentInvocationRequest:
    """Section 6's allow-list exactly: mission_definition content,
    repository, and -- for attempt 1 only -- Emma's attempt-0 cited
    findings, never his own prior builder_evidence fields and never her
    verdict enum value."""
    record = chugel.get_mission(mission_id)

    task: dict = {
        "mission_definition": _mission_definition_content(record),
        "repository": copy.deepcopy(record["repository"]),
    }
    if attempt == 1:
        task["cited_findings"] = _cited_findings(record)

    return AgentInvocationRequest(
        invocation_id=str(uuid.uuid4()),
        mission_id=mission_id,
        agent_role="emilio",
        attempt=attempt,
        task=task,
        requested_at=_now(),
        requested_fresh_context=False,
    )


def build_emma_invocation_request(mission_id: str, attempt: int) -> AgentInvocationRequest:
    """Section 7's allow-list exactly: mission_definition content,
    builder_evidence[attempt]'s artifact/changed_files/checks/
    handoff_document_ref (factual evidence, per the Increment #7
    corrective cycle's reconciliation with agents/emma/CONTRACT.md
    section 5), repository, and -- for attempt 1 only -- her own
    attempt-0 findings. Never conclusion/assumptions/risks/
    rollback_notes/safety_confirmation, and never a prior
    reviewer_evidence entry from a different attempt number."""
    record = chugel.get_mission(mission_id)

    builder_entries = record.get("builder_evidence") or []
    builder_entry = next((e for e in builder_entries if e.get("attempt") == attempt), None)
    if builder_entry is None:
        raise ValueError(
            f"mission {mission_id}: no builder_evidence entry for attempt {attempt} "
            "to review yet"
        )

    task: dict = {
        "mission_definition": _mission_definition_content(record),
        "artifact": copy.deepcopy(builder_entry["artifact"]),
        "changed_files": copy.deepcopy(builder_entry["changed_files"]),
        "checks": copy.deepcopy(builder_entry["checks"]),
        "handoff_document_ref": builder_entry["handoff_document_ref"],
        "repository": copy.deepcopy(record["repository"]),
    }
    if attempt == 1:
        task["own_prior_findings"] = _cited_findings(record)

    return AgentInvocationRequest(
        invocation_id=str(uuid.uuid4()),
        mission_id=mission_id,
        agent_role="emma",
        attempt=attempt,
        task=task,
        requested_at=_now(),
        requested_fresh_context=True,
    )


# --- response consumers ---------------------------------------------------

def _validate_result_shape(result: AgentInvocationResult) -> None:
    if result.outcome not in _OUTCOMES:
        raise ValueError(f"outcome {result.outcome!r} is not one of {sorted(_OUTCOMES)}")


def _augmented_completed_evidence(
    request: AgentInvocationRequest, result: AgentInvocationResult
) -> dict | None:
    """Bind completed provider evidence to the request and add only
    infrastructure-owned identity.  Non-completed outcomes persist nothing."""
    if result.outcome != "completed":
        return None
    if not isinstance(result.evidence, dict):
        raise AttemptNumberMismatch("completed result evidence must be an object")
    evidence_attempt = result.evidence.get("attempt")
    if type(evidence_attempt) is not int or evidence_attempt != request.attempt:
        raise AttemptNumberMismatch(
            f"mission {request.mission_id}: evidence attempt {evidence_attempt!r} "
            f"does not exactly match requested attempt {request.attempt!r}"
        )
    supplied = INFRASTRUCTURE_EVIDENCE_FIELDS.intersection(result.evidence)
    if supplied:
        raise ProviderControlledIdentityMetadata(
            "model evidence contains infrastructure-owned field(s): "
            + ", ".join(sorted(supplied))
        )
    augmented = copy.deepcopy(result.evidence)
    augmented.update({
        "invocation_id": request.invocation_id,
        "provider": result.provider,
        "provider_session_id": result.provider_session_id,
        "provider_conversation_id": result.provider_conversation_id,
    })
    return augmented


def _check_persisted_builder_independence(
    request: AgentInvocationRequest, result: AgentInvocationResult
) -> None:
    builder = _persisted_builder_identity(request.mission_id, request.attempt)
    session_id = builder.get("provider_session_id")
    conversation_id = builder.get("provider_conversation_id")
    if session_id is not None and result.provider_session_id == session_id:
        raise StaleSessionReused(
            f"mission {request.mission_id}: Emma reused builder provider_session_id"
        )
    if conversation_id is not None and result.provider_conversation_id == conversation_id:
        raise StaleSessionReused(
            f"mission {request.mission_id}: Emma reused builder provider_conversation_id"
        )
    if builder.get("provider") == result.provider:
        comparable = (
            (session_id is not None and result.provider_session_id is not None)
            or (conversation_id is not None and result.provider_conversation_id is not None)
        )
        if not comparable:
            raise IndependenceUnverifiable(
                f"mission {request.mission_id}: same-provider identities for attempt "
                f"{request.attempt} cannot be compared"
            )


def consume_emilio_result(
    request: AgentInvocationRequest, result: AgentInvocationResult
) -> dict | None:
    """Emilio has no independence requirement of his own
    (agents/emilio/CONTRACT.md section 17) -- fresh_context_attested is
    never checked for him, matching AGENT_INVOCATION_V1.md section 8's
    stated asymmetry. Returns the updated Mission Record on a
    "completed" write, or None for any other legitimate outcome (nothing
    written). Raises only on an invocation_id mismatch -- a protocol
    violation, not an outcome."""
    if result.invocation_id != request.invocation_id:
        raise InvocationIdMismatch(
            f"result.invocation_id {result.invocation_id!r} does not match "
            f"request.invocation_id {request.invocation_id!r}"
        )
    _validate_result_shape(result)

    evidence = _augmented_completed_evidence(request, result)
    if evidence is None:
        return None
    return chugel.record_builder_evidence(request.mission_id, evidence)


def consume_emma_result(
    request: AgentInvocationRequest,
    result: AgentInvocationResult,
) -> dict | None:
    """Fail closed using the matching builder attempt's persisted identity.

    The Mission Record is reloaded immediately before persistence, so a
    restart or a stale caller cannot bypass the independence comparison.
    """
    if result.invocation_id != request.invocation_id:
        raise InvocationIdMismatch(
            f"result.invocation_id {result.invocation_id!r} does not match "
            f"request.invocation_id {request.invocation_id!r}"
        )
    _validate_result_shape(result)

    if not request.requested_fresh_context:
        raise FreshContextNotAttested(
            "request.requested_fresh_context must be True for agent_role='emma' "
            "-- this is a defensive assertion against a corrupted request object, "
            "since build_emma_invocation_request() always sets it True"
        )
    if result.fresh_context_attested is not True:
        raise FreshContextNotAttested(
            f"result.fresh_context_attested must be the literal True, got "
            f"{result.fresh_context_attested!r}"
        )

    # Independence is a prerequisite for accepting reviewer evidence, not
    # for reporting a legitimate provider failure that writes no evidence.
    # Keeping this boundary immediately before the completed write preserves
    # fail-closed persistence while leaving an unpersisted attempt retryable.
    if result.outcome != "completed":
        return None
    _check_persisted_builder_independence(request, result)
    evidence = _augmented_completed_evidence(request, result)
    assert evidence is not None  # outcome is completed; helper validates shape
    return chugel.record_reviewer_evidence(request.mission_id, evidence)
