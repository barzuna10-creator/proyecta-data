"""Provider Router V1 -- deterministic adapter selection, per
orchestrator/PROVIDER_ROUTER_V1.md (already committed, already
independently reviewed by Emma). This module implements exactly that
design's section 1 algorithm; it is Chugel-core code, not a provider
adapter, not an AgentInvoker implementation, and never makes a network
call, reads a credential, or touches human_gates/state.

`select_adapter()` decides only *which* already-authorized AgentInvoker
handles an already-authorized invocation attempt -- it never decides
whether an invocation should happen, never calls an adapter itself, and
is called exactly once per attempt by a caller (mirroring
AGENT_INVOCATION_V1.md section 5's lifecycle; this module adds no new
autonomous step, per PROVIDER_ROUTER_V1.md section 15).

Deferred, not implemented here -- Provider Router V1 acceptance criteria
8 and 9 (PROVIDER_ROUTER_V1.md section 21) require a separately-authorized
schema/validator increment and are intentionally NOT satisfied by this
module:
  - INV-LOG-1 (section 10): deterministic enforcement that at most one
    invocation_log[] entry per logical_attempt_group has
    resulted_in_evidence == true. This requires a persisted
    invocation_log[] array and a validator cross-field check, neither of
    which exists in the Mission Record schema and neither of which this
    increment is authorized to add.
  - produced_by_invocation_id linkage (section 10): requires the same
    schema addition.
This module implements no alternate persistence and duplicates none of
these future schema concepts locally -- AttemptRecord (below) is
deliberately not the invocation_log[] entry shape.

**José's explicit V1 policy decision (Increment #13 corrective cycle,
closing Emma's P2 finding)**: PROVIDER_ROUTER_V1.md section 1 step 3's
three bullets are not exhaustively combinatorial as written -- they leave
one combination unstated: an outcome that *is* failover-eligible, while
`failover_enabled` is `False`, on an attempt that has not yet reached the
fallback. Emma's independent review flagged this as a genuine design-text
gap rather than a settled implementation detail. José has now explicitly
approved the resolution as V1 policy, not merely Emilio's reading of an
ambiguous branch: **when `failover_enabled=False`, the router must never
switch providers because of a provider failure -- a failover-eligible
failure on the current provider resolves to `same_provider_retry`,
identically to the non-eligible case.** This is implemented by folding
both combinations into the same `same_provider_retry` branch below. This
decision is recorded here, in the implementation/test surface, because
this corrective cycle's authorized scope is exactly this module and its
test file; PROVIDER_ROUTER_V1.md section 1 itself still reads as
ambiguous on this exact combination and should be updated to state this
policy explicitly in a future, separately-authorized doc-only change --
flagged here so that synchronization is never silently skipped.

**P1 fix (Increment #13 corrective cycle, closing Emma's P1 finding)**:
`AttemptRecord.provider` is now validated at construction against the
same V1 provider universe `RoleProviderPolicy` already validates against
(`_KNOWN_PROVIDERS`). Emma independently demonstrated that a malformed
provider string, `None`, or an empty string on a caller-supplied
`AttemptRecord` previously flowed unchanged through the
`same_provider_retry` branch into `RoutingDecision.adapter_name`,
violating that field's own documented contract
(`adapter_name: str  # "claude" | "codex"`). For V1, an `AttemptRecord`
represents an invocation that actually occurred, so its `provider` must
be exactly one of the two supported providers -- construction now fails
closed on anything else, with no normalization (no case-folding, no
whitespace-stripping, no alias table): a caller/adapter that produces
anything other than the literal string `"claude"` or `"codex"` has a bug
that must be surfaced at the point of construction, not silently
corrected.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderRouterError(Exception):
    """Base class for every exception this module raises."""


class AlreadyCompletedAttempt(ProviderRouterError):
    """prior_attempts already contains an entry with outcome == "completed"
    for this logical attempt slot -- routing a further attempt is never
    legitimate (PROVIDER_ROUTER_V1.md section 1 step 0, section 12a)."""


class UnknownAgentRole(ProviderRouterError):
    """agent_role is not a key in config.roles -- fails closed rather than
    raising a raw KeyError, per the "invalid role/config fails closed"
    requirement."""


_KNOWN_PROVIDERS = frozenset({"claude", "codex"})

# Failover-eligible outcomes, PROVIDER_ROUTER_V1.md section 3. "completed"
# never reaches this function (refused at step 0); "invalid_output" is
# eligible only when a caller has explicitly set failover_on_invalid_output.
_FAILOVER_ELIGIBLE = frozenset({"unavailable", "timeout", "failed"})


@dataclass(frozen=True)
class RoleProviderPolicy:
    primary: str
    fallback: str
    failover_enabled: bool = True
    failover_on_invalid_output: bool = False

    def __post_init__(self) -> None:
        if self.primary not in _KNOWN_PROVIDERS:
            raise ValueError(f"primary {self.primary!r} is not one of {sorted(_KNOWN_PROVIDERS)}")
        if self.fallback not in _KNOWN_PROVIDERS:
            raise ValueError(f"fallback {self.fallback!r} is not one of {sorted(_KNOWN_PROVIDERS)}")
        if self.primary == self.fallback:
            raise ValueError(
                f"primary and fallback must differ, both were {self.primary!r}"
            )


@dataclass(frozen=True)
class ProviderConfig:
    roles: dict[str, RoleProviderPolicy]


DEFAULT_PROVIDER_CONFIG = ProviderConfig(
    roles={
        "emilio": RoleProviderPolicy(primary="codex", fallback="claude"),
        "emma": RoleProviderPolicy(primary="claude", fallback="codex"),
    }
)


@dataclass(frozen=True)
class AttemptRecord:
    """Ephemeral, caller-constructed routing input only -- never persisted
    anywhere, never read back from a Mission Record, and NOT a substitute
    for the future invocation_log[] schema addition
    (PROVIDER_ROUTER_V1.md section 10) or its AttemptRecord shape sketch
    there. Deliberately carries only the three fields select_adapter()'s
    algorithm actually needs: `outcome` and `provider` are read by the
    routing decision; `error_detail` is carried but its content is never
    read by any branch (PROVIDER_ROUTER_V1.md section 1 step 4) -- it
    exists on this shape solely so a caller/test can prove that varying
    it never changes a routing decision. None of section 10's other
    proposed invocation_log[] fields (logical_attempt_group,
    routing_reason, resulted_in_evidence, requested_fresh_context,
    fresh_context_attested, invocation_id, mission_id, timestamps) are
    represented here -- those are the future persisted schema's concern,
    not this in-memory routing input's."""

    outcome: str
    provider: str
    error_detail: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in _KNOWN_PROVIDERS:
            raise ValueError(
                f"provider {self.provider!r} is not one of {sorted(_KNOWN_PROVIDERS)} -- "
                "an AttemptRecord represents an invocation that actually occurred, so its "
                "provider must be exactly one of the supported providers; no normalization "
                "or aliasing is performed"
            )


@dataclass(frozen=True)
class RoutingDecision:
    adapter_name: str  # "claude" | "codex" -- matches AgentInvocationResult.provider
    reason: str  # closed enum, never free text


def select_adapter(
    agent_role: str,
    attempt: int,
    config: ProviderConfig,
    prior_attempts: tuple[AttemptRecord, ...],
) -> RoutingDecision:
    """Deterministic, pure: same inputs, same output, every time -- no I/O,
    no randomness, no clock read, no free-text field ever consulted.
    `attempt` is accepted (matching PROVIDER_ROUTER_V1.md section 1's
    signature) but not itself branched on -- Option B (José's approved V1
    policy) means each attempt is routed independently from `config`'s
    primary using only that attempt's own `prior_attempts`; the caller
    (never this function) is responsible for scoping `prior_attempts` to
    the correct attempt slot and never carrying attempt=0's history into
    an attempt=1 call."""
    if agent_role not in config.roles:
        raise UnknownAgentRole(
            f"agent_role {agent_role!r} is not configured; known roles: "
            f"{sorted(config.roles.keys())}"
        )
    policy = config.roles[agent_role]

    # Step 0 (PROVIDER_ROUTER_V1.md section 1, corrective addition,
    # section 12a): any completed entry anywhere in prior_attempts refuses
    # routing outright, regardless of position or of what a later entry
    # (there should never legitimately be one) might say.
    if any(a.outcome == "completed" for a in prior_attempts):
        raise AlreadyCompletedAttempt(
            f"mission attempt already has a completed entry for "
            f"agent_role={agent_role!r}; refusing to route a further attempt"
        )

    if not prior_attempts:
        return RoutingDecision(adapter_name=policy.primary, reason="primary_default")

    last = prior_attempts[-1]
    eligible = last.outcome in _FAILOVER_ELIGIBLE or (
        last.outcome == "invalid_output" and policy.failover_on_invalid_output
    )

    if eligible and policy.failover_enabled and last.provider == policy.primary:
        return RoutingDecision(
            adapter_name=policy.fallback, reason=f"failover_after_{last.outcome}"
        )

    if last.provider == policy.fallback:
        return RoutingDecision(
            adapter_name=policy.primary, reason="both_providers_exhausted_retry_primary"
        )

    # Reached when either: the outcome is not failover-eligible, or it is
    # eligible but policy.failover_enabled is False (José's explicit V1
    # policy decision, recorded in this module's docstring, closing
    # Emma's P2 finding) -- in both cases the router never switches
    # providers here, it retries whichever provider just ran.
    return RoutingDecision(adapter_name=last.provider, reason="same_provider_retry")
