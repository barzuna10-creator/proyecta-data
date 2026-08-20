"""Controlled Orchestration Wiring V1 -- orchestrator/wiring.py

Implements exactly the "smallest safe next increment" Increment #15
Discovery identified: a glue layer connecting Mission Record -> Chugel ->
Provider Router -> Adapter -> evidence, for exactly one caller-authorized
role/attempt step at a time. This module invents no new design -- every
function it calls (`build_emilio_invocation_request`,
`build_emma_invocation_request`, `select_adapter`, `consume_emilio_result`,
`consume_emma_result`) is already implemented, already independently
reviewed, and unchanged by this increment.

**Explicitly deferred, not implemented here** (per this increment's own
authorization, accepted by José as a temporary pilot audit gap, applying
only to this bounded increment -- Provider Router V1 acceptance criteria
8-9 remain explicitly NOT satisfied, not merely unmentioned):
`invocation_log[]`, `logical_attempt_group`, `resulted_in_evidence`,
`produced_by_invocation_id`, `INV-LOG-1`. A non-`"completed"` outcome
therefore writes no trace anywhere in the Mission Record -- only this
call's own return value (`AttemptOutcome`) carries it, for the duration of
the caller's own process, to whatever human is deciding the next
authorized step. This module does not fake, simulate, or locally persist
any of these deferred concepts.

**Single-step discipline, structural, not merely a convention**: every
public function in this module performs exactly one
build-request -> select-adapter -> invoke -> consume-result sequence, then
returns. Nothing here calls `chugel.transition()`, `chugel.decide_gate()`,
itself, the other role's function, or an adapter's `invoke()` more than
once. There is no loop, no polling, no retry, no fallback chaining, and no
autonomous continuation anywhere in this module -- the caller (today, a
human driving each step explicitly) decides whether and how to authorize
the next call, exactly as `AGENT_INVOCATION_V1.md` section 5's lifecycle
and `PROVIDER_ROUTER_V1.md` section 15 already require of any caller of
these functions.

**Corrective cycle (closing Emma's independent-review P1/P2 findings):**

- **P1 -- Emma's freshness parameters are no longer two independently
  forgettable optional strings.** `run_emma_attempt()` previously accepted
  `prior_session_id`/`prior_conversation_id` as two loose, defaulted-to-
  `None` keyword arguments -- Emma's review empirically demonstrated that
  a caller who already held Emilio's real identifiers (from a preceding
  `run_emilio_attempt()` call's own return value) could silently omit
  both, causing `consume_emma_result()`'s INV-2a cross-check
  (`AGENT_INVOCATION_V1.md`) to be skipped entirely with no warning. This
  was not the already-accepted "no persisted `invocation_log[]`" gap --
  the identifiers were already sitting in the caller's hand; nothing
  forced or even nudged threading them through.

  `run_emma_attempt()` now takes a single, **required** (no default)
  keyword parameter, `preceding_emilio_outcome: AttemptOutcome | None`,
  instead. The normal call site passes the actual `AttemptOutcome` object
  a preceding `run_emilio_attempt()` call returned; this function derives
  `provider_session_id`/`provider_conversation_id` from
  `preceding_emilio_outcome.result` itself, internally, unconditionally --
  there is no code path where a caller holding a real `AttemptOutcome` can
  cause its identifiers to be silently dropped, because there is no longer
  a second, separate place to forget to copy them into. Making the
  parameter required (not defaulted) means a caller must explicitly decide
  `preceding_emilio_outcome=some_outcome` or
  `preceding_emilio_outcome=None` -- omitting the argument entirely is now
  a `TypeError` at the call site, not a silent, semantically-different
  default. Passing `None` remains the correct, honest way to represent a
  genuine absence of a preceding Emilio attempt in this exact scope (e.g.
  a resumed process that no longer holds the original object) -- this
  module does not invent persistence to avoid that legitimate case, per
  this increment's explicit instruction not to add `invocation_log[]` or
  any substitute for it.

  If `preceding_emilio_outcome.result` exposes neither
  `provider_session_id` nor `provider_conversation_id` (a provider that
  documents no such identifier), both derived values are `None`, and
  `consume_emma_result()`'s own unmodified behavior applies unchanged:
  the comparison is skipped, never treated as proof of freshness -- this
  module does not claim otherwise anywhere in its own text.

- **P2 -- the dispatched adapter's self-reported provider is now
  cross-checked against the routing decision that selected it**, before
  either role's result is ever handed to `consume_emilio_result()`/
  `consume_emma_result()`. `select_adapter()` decides *which* adapter
  should run; nothing previously confirmed that the adapter which actually
  ran agreed about *which provider it was*. A mismatch (a misconfigured
  `adapters` mapping, or a misbehaving/mislabeled `AgentInvoker`) now
  raises `ProviderMismatch` immediately after `invoke()` returns, before
  any evidence-writing call -- no evidence is ever written for a mismatched
  result. The check is a plain equality comparison on the closed
  `provider`/`adapter_name` enum values, never a parse of `error_detail`
  or any other free-text field.
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
    """Shared by both public functions below -- select_adapter() then
    exactly one adapter.invoke() call, nothing else. Not itself public:
    callers use run_emilio_attempt()/run_emma_attempt(), which additionally
    apply the role-specific request/consume steps this helper knows
    nothing about."""
    decision = select_adapter(agent_role, attempt, config, prior_attempts)
    adapter = adapters.get(decision.adapter_name)
    if adapter is None:
        raise UnknownAdapterSelected(
            f"select_adapter() chose adapter_name={decision.adapter_name!r} "
            f"(reason={decision.reason!r}), but the caller-supplied `adapters` "
            f"mapping has no entry for it (has: {sorted(adapters.keys())}) -- "
            "refusing to guess or fall back to any other adapter"
        )
    result = adapter.invoke(request)  # exactly once (requirement 5)
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
    explicitly for whichever single step it is now authorizing."""
    request = build_emilio_invocation_request(mission_id, attempt)
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
    preceding_emilio_outcome: AttemptOutcome | None,
    config: ProviderConfig = DEFAULT_PROVIDER_CONFIG,
    prior_attempts: tuple[AttemptRecord, ...] = (),
) -> AttemptOutcome:
    """Exactly one Emma role/attempt step, symmetric to
    run_emilio_attempt() above, with one required addition closing Emma's
    independent-review P1 finding: `preceding_emilio_outcome` -- the
    actual `AttemptOutcome` a preceding `run_emilio_attempt()` call
    returned, or `None` if this exact process no longer holds one (e.g. a
    resumed session). There is deliberately no default and no separate
    identifier-shaped parameter to forget: `provider_session_id`/
    `provider_conversation_id` are derived internally from
    `preceding_emilio_outcome.result` and passed to
    consume_emma_result() -- this is the load-bearing freshness boundary
    (AGENT_INVOCATION_V1.md's INV-2a): if Emilio's own result reported
    either identifier, and Emma's own result reports the identical one,
    consume_emma_result() refuses unconditionally, regardless of
    fresh_context_attested. If `preceding_emilio_outcome` is `None`, or if
    its `result` exposed neither identifier, both derived values are
    `None` -- consume_emma_result()'s own unmodified behavior applies: the
    comparison is skipped, never treated as proof of freshness. This
    function never fabricates an identifier and never invents persistence
    to recover one a caller has genuinely lost -- it only removes the
    possibility of silently dropping one the caller still has in hand."""
    if preceding_emilio_outcome is None:
        prior_session_id = None
        prior_conversation_id = None
    else:
        prior_session_id = preceding_emilio_outcome.result.provider_session_id
        prior_conversation_id = preceding_emilio_outcome.result.provider_conversation_id

    request = build_emma_invocation_request(mission_id, attempt)
    decision, result = _select_and_dispatch(
        agent_role="emma",
        attempt=attempt,
        request=request,
        adapters=adapters,
        config=config,
        prior_attempts=prior_attempts,
    )
    updated_mission = consume_emma_result(
        request,
        result,
        prior_session_id=prior_session_id,
        prior_conversation_id=prior_conversation_id,
    )
    return AttemptOutcome(
        request=request,
        result=result,
        routing_decision=decision,
        attempt_record=to_attempt_record(result),
        updated_mission=updated_mission,
    )
