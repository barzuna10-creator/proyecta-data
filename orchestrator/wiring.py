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

Corrective #7 (closing the "API_USAGE_STILL_REACHABLE" finding from the
subscription-only-guarantee investigation): `run_mission()`/
`run_emilio_attempt()`/`run_emma_attempt()` accept an arbitrary caller-
supplied `adapters` mapping, and nothing previously stopped a caller from
populating it with `ProviderWorkerInvoker` (the API-key-backed worker
proxy from `orchestrator/provider_credentials.py::build_provider_adapters()`)
or the legacy `CodexAdapter`/`ClaudeAdapter` tombstones instead of the
zero-cost subscription-CLI adapters. Nothing else in the orchestrator
(Chugel, agent_invocation, autonomous_runner, durable dispatch, attempt
budgets, human gates) constrained this either -- the guarantee that
autonomous Emilio/Emma execution only ever spends a subscription, never an
API-billed credential, existed only as operator discipline about which
adapter-construction function happened to be called, not as anything the
code itself enforced.

This module is the single, narrowest chokepoint both roles' every dispatch
already passes through (`_select_and_dispatch()`, below) -- the adapter
object is resolved here, immediately after `select_adapter()` names a
provider and immediately before it is ever invoked. The new check there
fails closed: `adapter` must be an instance of exactly `CodexCliAdapter`
or `ClaudeCliAdapter` (imported directly from their own modules, not
duck-typed), or `UnapprovedAdapterType` is raised before
`mark_invocation_dispatched()`/`adapter.invoke()` ever run -- an API-key
adapter, a hand-rolled fake, or any other object merely satisfying the
`AgentInvoker` Protocol structurally is refused exactly like an unknown
adapter name already was. This is strictly additive: every existing
subscription-CLI dispatch (real pilots, all tests using
`build_cli_subscription_adapters()`) is unaffected, and the existing
codex<->claude provider-name failover in `DEFAULT_PROVIDER_CONFIG` is
completely untouched -- it still resolves whichever name `select_adapter()`
picks, and that resolved adapter is checked by the exact same rule
regardless of which of the two names it came from. The legacy API-key
adapters are not removed (a separate, larger, out-of-scope decision) --
they are made structurally unreachable from this dispatch path.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from orchestrator.adapters.claude_cli_adapter import ClaudeCliAdapter
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter
from orchestrator.agent_invocation import (
    AgentInvocationRequest,
    AgentInvocationResult,
    AgentInvoker,
    build_emilio_invocation_request,
    build_emma_invocation_request,
    consume_emilio_result,
    consume_emma_result,
    get_builder_provider,
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

# The complete, closed set of adapter types the autonomous Emilio/Emma
# mission-dispatch path is ever permitted to invoke. A tuple of exact
# classes, checked via isinstance() -- a genuine subclass of either is
# still permitted, but ProviderWorkerInvoker, CodexAdapter, ClaudeAdapter,
# and any ad-hoc object are not, since none of them derive from either
# class. Adding a provider to this set is a deliberate, reviewable code
# change here, never an implicit consequence of whatever `adapters`
# mapping happens to be passed in.
_SUBSCRIPTION_ONLY_ADAPTER_TYPES: tuple[type, ...] = (CodexCliAdapter, ClaudeCliAdapter)


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


class UnapprovedAdapterType(WiringError):
    """Corrective #7: the adapter `select_adapter()`'s chosen name resolved
    to, in the caller-supplied `adapters` mapping, is not an instance of
    `CodexCliAdapter`/`ClaudeCliAdapter` -- e.g. a `ProviderWorkerInvoker`
    (API-key-backed), a legacy `CodexAdapter`/`ClaudeAdapter` tombstone, or
    any other object that merely satisfies the `AgentInvoker` Protocol
    structurally. Fails closed before `mark_invocation_dispatched()` or
    `adapter.invoke()` ever run -- no durable IN_FLIGHT marking, no
    provider spend, no evidence. Like `UnknownAdapterSelected`, this is a
    caller-configuration error, never a provider-side outcome."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _provider_independence_unavailable_result(
    request: AgentInvocationRequest, provider: str
) -> AgentInvocationResult:
    """Synthetic, never-invoked result standing in for a genuine adapter
    response, used only when routing would send Emma to the exact same
    provider builder_evidence[attempt] already recorded for Emilio.
    outcome="unavailable" is deliberately the same classification an
    adapter itself reports when it cannot dispatch at all -- it is
    already a member of DISPATCH_RETRYABLE_CLASSIFICATIONS, so this
    reuses the existing retry path in chugel.reserve_dispatch() without
    any change there. fresh_context_attested is set True (not a claim
    about a model that never ran -- consume_emma_result() asserts this
    field is literally True for every result shape, completed or not,
    before it even reaches the outcome branch)."""
    return AgentInvocationResult(
        invocation_id=request.invocation_id,
        outcome="unavailable",
        provider=provider,
        model=None,
        responded_at=_now(),
        fresh_context_attested=True,
        provider_session_id=None,
        provider_conversation_id=None,
        evidence=None,
        error_detail=(
            f"reviewer provider {provider!r} matches the builder provider "
            f"already recorded for attempt {request.attempt} -- independent "
            "review is unavailable from this provider for this attempt"
        ),
    )


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
    if not isinstance(adapter, _SUBSCRIPTION_ONLY_ADAPTER_TYPES):
        raise UnapprovedAdapterType(
            f"adapter for adapter_name={decision.adapter_name!r} is "
            f"{type(adapter).__name__!r}, not one of "
            f"{tuple(cls.__name__ for cls in _SUBSCRIPTION_ONLY_ADAPTER_TYPES)} -- "
            "refusing to dispatch through a non-subscription-CLI adapter"
        )

    # Emma-only provider-independence guard: a failover that would land
    # Emma on the exact same provider builder_evidence[attempt] already
    # recorded for Emilio can never produce a publishable approval --
    # refuse before ever invoking it, and record the outcome as
    # "unavailable" (already retryable) rather than let it complete as
    # if it were a real, independent review. Emilio has no independence
    # requirement of his own, so this never applies to his role.
    if agent_role == "emma":
        builder_provider = get_builder_provider(request.mission_id, attempt)
        if builder_provider is not None and decision.adapter_name == builder_provider:
            mark_invocation_dispatched(
                request.mission_id, request.invocation_id, provider=decision.adapter_name
            )
            result = _provider_independence_unavailable_result(request, decision.adapter_name)
            record_invocation_result(
                request.mission_id, request.invocation_id, outcome=result.outcome
            )
            return decision, result

    mark_invocation_dispatched(
        request.mission_id, request.invocation_id, provider=decision.adapter_name
    )
    result = adapter.invoke(request)  # exactly once (requirement 5)
    # Structured Allow-Listed Diagnostics: result.diagnostic, when the
    # adapter built one, is a small closed-reason-code/typed-fields dict
    # -- never free text -- forwarded here so record_invocation_result()/
    # chugel.record_dispatch_result() can persist it durably (subject to
    # eligibility/schema enforcement there). result.error_detail is
    # intentionally NOT forwarded anywhere durable -- it remains only
    # what it always was, an ephemeral in-memory string used to build
    # prior_attempts within this same run_mission() call.
    record_invocation_result(
        request.mission_id, request.invocation_id,
        outcome=result.outcome, diagnostic=result.diagnostic,
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
