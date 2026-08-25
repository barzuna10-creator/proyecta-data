"""Controlled one-step wiring from Mission Record to provider adapter.

Eligibility is checked before adapter selection -- and, in the same call,
the dispatch is durably reserved (chugel.reserve_dispatch(), via
require_eligible_invocation()) strictly before any adapter is ever invoked.
No supported path in this module can reach adapter.invoke() for a
(mission_id, role, attempt) that does not already have a durable RESERVED
ledger entry. Completed evidence receives infrastructure-owned invocation
identity in the consumer. Emma's independence decision comes only from the
corresponding builder evidence persisted for the same mission and attempt,
and is rechecked immediately before reviewer evidence is written. No retry,
transition, gate mutation, or fallback is performed autonomously.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.agent_invocation import (
    AgentInvocationRequest,
    AgentInvocationResult,
    AgentInvoker,
    build_emilio_invocation_request,
    build_emma_invocation_request,
    consume_emilio_result,
    consume_emma_result,
    mark_invocation_dispatched,
    record_invocation_result,
    require_eligible_invocation,
)
from orchestrator.provider_router import (
    AttemptRecord,
    DEFAULT_PROVIDER_CONFIG,
    ProviderConfig,
    RoutingDecision,
    select_adapter,
)


class WiringError(Exception):
    """Base class for every exception this module raises directly (not
    counting exceptions this module lets propagate unchanged from the
    functions it calls -- see module docstring)."""


class UnknownAdapterSelected(WiringError):
    """select_adapter() returned a RoutingDecision.adapter_name this
    call's `adapters` mapping has no entry for. Fails closed rather than
    guessing or silently falling back to any adapter -- this is a
    caller-configuration error (an incomplete `adapters` mapping, or a
    ProviderConfig naming a provider the caller never wired up), never a
    provider-side outcome."""


class ProviderMismatch(WiringError):
    """adapter.invoke() returned an AgentInvocationResult whose `provider`
    field does not match the RoutingDecision that selected it (module
    docstring, P2). Fails closed before the result is ever passed to
    consume_emilio_result()/consume_emma_result() -- no evidence is
    written for a mismatched result."""


def to_attempt_record(result: AgentInvocationResult) -> AttemptRecord:
    """Mechanical conversion only, per this increment's requirement 6 --
    invents nothing, persists nothing. `AttemptRecord.__post_init__`
    (provider_router.py, unmodified) still validates `provider` against
    the known provider set, so a malformed `result.provider` fails closed
    here exactly as it would anywhere else an `AttemptRecord` is
    constructed -- this function adds no separate validation of its own
    because it needs none."""
    return AttemptRecord(
        outcome=result.outcome,
        provider=result.provider,
        error_detail=result.error_detail,
    )


@dataclass(frozen=True)
class AttemptOutcome:
    """Returned after exactly one caller-authorized role/attempt step.
    Carries enough ephemeral routing/result information (requirement 13)
    for a human to decide and explicitly authorize the next step -- e.g.
    building `prior_attempts=(previous.attempt_record,)` for a caller-
    authorized failover retry -- without this module persisting any of it
    itself. `updated_mission` is `None` whenever `result.outcome` was not
    `"completed"`: consume_emilio_result()/consume_emma_result() wrote
    nothing to the Mission Record in that case (requirement 8), and this
    field says so directly rather than requiring the caller to re-derive
    that from `result.outcome`."""

    request: AgentInvocationRequest
    result: AgentInvocationResult
    routing_decision: RoutingDecision
    attempt_record: AttemptRecord
    updated_mission: dict | None


def _select_and_dispatch(
    *,
    agent_role: str,
    attempt: int,
    request: AgentInvocationRequest,
    adapters: dict[str, AgentInvoker],
    config: ProviderConfig,
    prior_attempts: tuple[AttemptRecord, ...],
) -> tuple[RoutingDecision, AgentInvocationResult]:
    """Shared by both public functions below -- select_adapter(), durably
    mark the reservation IN_FLIGHT with the resolved provider, then exactly
    one adapter.invoke() call, then durably record the raw result before
    this function returns. Not itself public: callers use
    run_emilio_attempt()/run_emma_attempt(), which additionally apply the
    role-specific request/consume steps this helper knows nothing about.

    request.invocation_id was already durably reserved (status RESERVED)
    by require_eligible_invocation() before this function is ever called
    -- mark_dispatch_in_flight() below is the only place that reservation
    is allowed to advance, and it always runs strictly before
    adapter.invoke(). A crash before it leaves the reservation at RESERVED
    (unknown launch provenance); a crash after leaves it at IN_FLIGHT
    (launch confirmed, result unknown) -- both fail closed identically on
    restart (chugel.reserve_dispatch()'s own eligibility check), so this
    function does not need to distinguish them further."""
    decision = select_adapter(agent_role, attempt, config, prior_attempts)
    adapter = adapters.get(decision.adapter_name)
    if adapter is None:
        raise UnknownAdapterSelected(
            f"select_adapter() chose adapter_name={decision.adapter_name!r} "
            f"(reason={decision.reason!r}), but the caller-supplied `adapters` "
            f"mapping has no entry for it (has: {sorted(adapters.keys())}) -- "
            "refusing to guess or fall back to any other adapter"
        )
    mark_invocation_dispatched(
        request.mission_id, request.invocation_id, provider=decision.adapter_name
    )
    result = adapter.invoke(request)  # exactly once (requirement 5)
    record_invocation_result(
        request.mission_id, request.invocation_id, outcome=result.outcome
    )
    if result.provider != decision.adapter_name:
        raise ProviderMismatch(
            f"select_adapter() chose adapter_name={decision.adapter_name!r}, but the "
            f"invoked adapter's result reports provider={result.provider!r} -- refusing "
            "to consume this result; no evidence is written"
        )
    return decision, result


def run_emilio_attempt(
    mission_id: str,
    attempt: int,
    adapters: dict[str, AgentInvoker],
    *,
    config: ProviderConfig = DEFAULT_PROVIDER_CONFIG,
    prior_attempts: tuple[AttemptRecord, ...] = (),
) -> AttemptOutcome:
    """Exactly one Emilio role/attempt step: build his allow-listed
    request (build_emilio_invocation_request(), unmodified), route it
    (select_adapter(), unmodified), invoke the resolved adapter exactly
    once, and consume the result (consume_emilio_result(), unmodified) --
    which writes builder_evidence exactly once, only on
    outcome == "completed", and raises InvocationIdMismatch unchanged if
    the result's invocation_id does not match this call's own request.
    That exception, and any other this function's own callees raise,
    propagates to the caller unchanged -- this function does not catch or
    soften a protocol violation into a return value.

    Never calls itself, run_emma_attempt(), chugel.transition(), or
    chugel.decide_gate(). Never invoked again automatically for a second
    attempt -- the caller supplies `attempt` and `prior_attempts`
    explicitly for whichever single step it is now authorizing.

    require_eligible_invocation() durably reserves this dispatch and
    returns the invocation_id it reserved -- build_emilio_invocation_request()
    is given that exact id, never generating its own, so the request that
    reaches the adapter always carries the identity that was reserved
    before dispatch, never a different one."""
    invocation_id = require_eligible_invocation(mission_id, role="emilio", attempt=attempt)
    request = build_emilio_invocation_request(mission_id, attempt, invocation_id)
    decision, result = _select_and_dispatch(
        agent_role="emilio",
        attempt=attempt,
        request=request,
        adapters=adapters,
        config=config,
        prior_attempts=prior_attempts,
    )
    updated_mission = consume_emilio_result(request, result)
    return AttemptOutcome(
        request=request,
        result=result,
        routing_decision=decision,
        attempt_record=to_attempt_record(result),
        updated_mission=updated_mission,
    )


def run_emma_attempt(
    mission_id: str,
    attempt: int,
    adapters: dict[str, AgentInvoker],
    *,
    config: ProviderConfig = DEFAULT_PROVIDER_CONFIG,
    prior_attempts: tuple[AttemptRecord, ...] = (),
) -> AttemptOutcome:
    """Exactly one Emma role/attempt step.  Eligibility reloads the matching
    persisted builder identity before routing, and consume_emma_result()
    reloads it again before any reviewer evidence write.  No in-memory
    preceding AttemptOutcome participates in the security decision.

    require_eligible_invocation() durably reserves this dispatch and
    returns the invocation_id it reserved -- see run_emilio_attempt()'s
    docstring for the identical guarantee on Emilio's side."""
    invocation_id = require_eligible_invocation(mission_id, role="emma", attempt=attempt)
    request = build_emma_invocation_request(mission_id, attempt, invocation_id)
    decision, result = _select_and_dispatch(
        agent_role="emma",
        attempt=attempt,
        request=request,
        adapters=adapters,
        config=config,
        prior_attempts=prior_attempts,
    )
    updated_mission = consume_emma_result(request, result)
    return AttemptOutcome(
        request=request,
        result=result,
        routing_decision=decision,
        attempt_record=to_attempt_record(result),
        updated_mission=updated_mission,
    )
