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

Cross-invocation session/conversation-ID comparison (INV-2a): this
increment has no persisted invocation_log[] to read a prior invocation's
identifier from (orchestrator/PROVIDER_ROUTER_V1.md's invocation_log[]
proposal remains a future, separately-authorized schema change, per
Increment #11 Discovery's finding that it is not required to make this
layer function correctly). Emma's response-consumer therefore accepts the
prior identifier(s) to compare against as explicit, caller-supplied
parameters -- today, a human/script holding Emilio's just-produced result
in memory supplies them. Omitting them (both None) is treated exactly as
AGENT_INVOCATION_V1.md's own "provider exposes no usable identifier" case
already specifies: the comparison is skipped, never treated as a pass."""

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
    """Emma's provider_session_id/provider_conversation_id matches a
    caller-supplied prior identifier for the same mission -- refused
    unconditionally, regardless of fresh_context_attested's value."""


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

    if result.outcome != "completed":
        return None

    return chugel.record_builder_evidence(request.mission_id, result.evidence)


def consume_emma_result(
    request: AgentInvocationRequest,
    result: AgentInvocationResult,
    *,
    prior_session_id: str | None = None,
    prior_conversation_id: str | None = None,
) -> dict | None:
    """Emma's independence checks (AGENT_INVOCATION_V1.md sections 4, 8, 9):
    invocation_id match, requested_fresh_context/fresh_context_attested
    both required True, and -- when a comparable prior identifier is
    supplied -- a session/conversation-ID equality refusal that overrides
    fresh_context_attested regardless of its value. `prior_session_id`/
    `prior_conversation_id` are today supplied explicitly by the caller
    (no persisted invocation_log[] exists yet to read them from
    automatically -- see this module's docstring); omitting both is
    treated as "no usable identifier," per the design's own existing
    principle, never as a passing comparison."""
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

    if prior_session_id is not None and result.provider_session_id == prior_session_id:
        raise StaleSessionReused(
            f"mission {request.mission_id}: Emma's provider_session_id "
            f"{result.provider_session_id!r} matches the supplied prior "
            "identifier -- refusing regardless of fresh_context_attested"
        )
    if (
        prior_conversation_id is not None
        and result.provider_conversation_id == prior_conversation_id
    ):
        raise StaleSessionReused(
            f"mission {request.mission_id}: Emma's provider_conversation_id "
            f"{result.provider_conversation_id!r} matches the supplied prior "
            "identifier -- refusing regardless of fresh_context_attested"
        )

    if result.outcome != "completed":
        return None

    return chugel.record_reviewer_evidence(request.mission_id, result.evidence)
