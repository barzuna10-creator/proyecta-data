"""Bounded autonomous coordinator over the existing Chugel and wiring APIs.

Durable dispatch (orchestrator/agent_invocation.py's
require_eligible_invocation(), orchestrator/chugel.py's reserve_dispatch()):
this coordinator itself holds no dispatch state across restarts -- every
loop iteration reloads canonical Mission Record state fresh and re-derives
which (role, schema attempt) is eligible from that state alone. A crash at
any point, followed by a fresh run_mission() call against the same
mission_id, observes exactly the same durable dispatch_ledger[] the crashed
process would have, and behaves identically -- restart and uninterrupted
execution have the same security semantics by construction, not by any
additional runner-level bookkeeping this module invents.

Corrective cycle (closing a real bug this durable-dispatch rebuild
surfaced, not previously reported): the schema's own attempt identity is
always exactly 0 or 1 -- 0 while the record is in BUILDING/first REVIEWING,
1 only after a genuine CHANGES_REQUIRED-triggered corrective cycle. The
prior version of this coordinator instead passed its own local
`builder`/`reviewer` retry counters directly as the schema attempt number,
so a second provider-level retry of the *same* attempt (e.g. a transient
timeout while still in BUILDING) was silently sent to
require_eligible_invocation()/build_emilio_invocation_request() as
attempt=1 -- which asserts state=="CORRECTING", not "BUILDING", and would
have raised (or, before this rebuild, produced nonsensical requests) on
the very first retry. This version derives the schema attempt entirely
from persisted state (`_emilio_schema_attempt()`/`_emma_schema_attempt()`)
-- attempt BUDGET counts (see below) are an unrelated concept from the
schema's 0/1 attempt identity, and are never passed to
run_emilio_attempt()/run_emma_attempt() as the attempt number.

A second, related fix: orchestrator/provider_router.py's select_adapter()
explicitly documents that `prior_attempts` must be scoped to one attempt
slot, "never carrying attempt=0's history into an attempt=1 call". The
prior version accumulated `prior` across the whole run_mission() call
regardless of which schema attempt was active, mixing routing history
across attempts. This version resets `prior` whenever the (role, schema
attempt) slot changes.

Durable attempt budgets (Emma's P2-2 finding, P2 hardening cycle): the
prior version bounded builder/reviewer/total dispatch attempts with local
Python counters (`builder = reviewer = total = 0`, incremented once per
dispatch within one run_mission() call). Those counters vanished on
process exit, so a human/operator who simply restarted the runner got a
fresh `max_builder_attempts`/`max_reviewer_attempts`/`max_total_attempts`
budget every time -- the *configured* budget was never actually a bound
on how many real provider dispatches a mission could accumulate over its
whole lifetime, only on how many any single process invocation would make.
This version derives all three counts from `dispatch_ledger` itself
(`_durable_attempt_counts()`) -- the same durable record every dispatch
already writes to before it happens (reserve_dispatch()) -- every loop
iteration, from a fresh chugel.get_mission() read. No second persistence
store is introduced: dispatch_ledger is exactly the existing durable
dispatch ledger, already required to durably record every genuine
dispatch attempt regardless of its eventual outcome. Restarting the
runner process cannot reset these counts, because they are never held
anywhere but in the Mission Record.

Every ledger entry counts toward its role's budget regardless of status
-- RESERVED and IN_FLIGHT entries (execution outcome not yet known)
included, not just resolved ones. Treating an unresolved entry as "free"
would let a crash-and-restart cycle manufacture unlimited budget by
repeatedly reserving and never letting an attempt resolve; counting it
immediately, the moment reserve_dispatch() durably reserves it, closes
that gap. A superseded (FINALIZED-by-supersession) entry from a retryable
failure still counts once for the attempt it represents -- consistent
with "one real dispatch, one unit of budget consumed" regardless of how
that dispatch's provenance later resolved."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from orchestrator import chugel
from orchestrator.agent_invocation import InvocationNotAuthorized
from orchestrator.provider_router import AttemptRecord, DEFAULT_PROVIDER_CONFIG, ProviderConfig
from orchestrator.wiring import run_emilio_attempt, run_emma_attempt


@dataclass(frozen=True)
class RunnerResult:
    status: str
    state: str
    attempts: int
    reason: str = ""


class AutonomousRunnerError(Exception):
    pass


def _emilio_schema_attempt(state: str) -> int:
    """0 while BUILDING (the initial build), 1 while CORRECTING (the
    single bounded corrective cycle) -- the only two states from which
    this coordinator ever dispatches Emilio, so this mapping is total."""
    return 0 if state == "BUILDING" else 1


def _emma_schema_attempt(record: dict) -> int:
    """0 for the first review, 1 for the re-review after a corrective
    cycle -- derived entirely from whether an attempt=1 builder_evidence
    entry already exists, never from a local counter, so this is correct
    identically on the very first call after a restart and mid-run."""
    entries = record.get("builder_evidence") or []
    return 1 if any(isinstance(e, dict) and e.get("attempt") == 1 for e in entries) else 0


def _durable_attempt_counts(record: dict) -> tuple[int, int, int]:
    """(total, builder, reviewer) dispatch attempt counts, derived
    entirely from record["dispatch_ledger"] -- every entry ever written by
    chugel.reserve_dispatch() for this mission, regardless of its current
    lifecycle status (RESERVED/IN_FLIGHT/RESULT_RECORDED/FINALIZED) or
    whether it was later superseded by a retryable redispatch. This is the
    durable replacement for what used to be local `total`/`builder`/
    `reviewer` counters -- see this module's docstring."""
    ledger = record.get("dispatch_ledger") or []
    builder = sum(1 for e in ledger if isinstance(e, dict) and e.get("role") == "emilio")
    reviewer = sum(1 for e in ledger if isinstance(e, dict) and e.get("role") == "emma")
    return len(ledger), builder, reviewer


def run_mission(
    mission_id: str,
    adapters: Mapping[str, object],
    *,
    config: ProviderConfig = DEFAULT_PROVIDER_CONFIG,
    max_total_attempts: int = 4,
    max_builder_attempts: int = 2,
    max_reviewer_attempts: int = 2,
    deadline: float | None = None,
) -> RunnerResult:
    """Continue legal Emilio/Emma cycles until a persisted boundary.

    Every iteration reloads Chugel state fresh, including the durable
    attempt-budget counts (_durable_attempt_counts()) -- this coordinator
    never approves a human gate and never mutates records except through
    public Chugel calls (via wiring.py, which itself durably reserves
    every dispatch before invoking a provider -- see the module
    docstring).

    A dispatch this coordinator cannot durably authorize right now --
    because chugel.reserve_dispatch() (via require_eligible_invocation())
    finds an unresolved RESERVED/IN_FLIGHT reservation for this exact
    (role, schema attempt), or a RESULT_RECORDED one that is not a
    durably-recorded retryable result -- raises InvocationNotAuthorized.
    This coordinator never treats that as license to guess what happened;
    it stops and reports HUMAN_ACTION_REQUIRED, identically whether this
    is the very first call for this mission or a restart after a crash
    mid-dispatch. Only a durably recorded retryable result (RESULT_RECORDED
    with a failed/timeout/unavailable classification) authorizes
    reserve_dispatch() to hand out a fresh attempt with a fresh
    invocation_id for the same (role, schema attempt) slot -- this
    coordinator does nothing special to request that; it simply calls
    run_emilio_attempt()/run_emma_attempt() again with the same schema
    attempt, and chugel's own ledger invariants decide whether that is
    legal.

    Attempt budgets (max_total_attempts/max_builder_attempts/
    max_reviewer_attempts) are checked against the durable ledger-derived
    counts at the top of every iteration, before any dispatch is
    attempted -- restarting this function in a fresh process cannot
    reset them, since they are read fresh from the Mission Record every
    time, never carried in a local variable across iterations or calls."""
    if min(max_total_attempts, max_builder_attempts, max_reviewer_attempts) <= 0:
        raise ValueError("attempt limits must be positive")
    prior: tuple[AttemptRecord, ...] = ()
    prior_slot: tuple[str, int] | None = None

    while True:
        record = chugel.get_mission(mission_id)
        state = record["state"]
        total_attempts, builder_attempts, reviewer_attempts = _durable_attempt_counts(record)

        if deadline is not None and time.monotonic() >= deadline:
            return RunnerResult("HUMAN_ACTION_REQUIRED", state, total_attempts, "deadline exhausted")

        if state in {"COMPLETED", "MERGED"}:
            return RunnerResult("COMPLETED", state, total_attempts)
        if state in {"SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION"}:
            return RunnerResult("AUTHORIZATION_REQUIRED", state, total_attempts)
        if state == "AUTHORIZED":
            chugel.transition(mission_id, "BUILDING", actor="chugel", reason="persisted authorization starts build")
            continue
        if state == "CHANGES_REQUIRED":
            chugel.transition(mission_id, "CORRECTING", actor="chugel", reason="resume persisted corrective cycle")
            continue
        if state in {"FAILED", "CANCELLED", "ROLLED_BACK", "BLOCKED"}:
            return RunnerResult("TERMINAL_FAILURE", state, total_attempts, record.get("state_reason", ""))
        if total_attempts >= max_total_attempts:
            return RunnerResult("HUMAN_ACTION_REQUIRED", state, total_attempts, "total attempt budget exhausted")

        if state in {"BUILDING", "CORRECTING"}:
            if builder_attempts >= max_builder_attempts:
                return RunnerResult("HUMAN_ACTION_REQUIRED", state, total_attempts, "builder attempt budget exhausted")
            schema_attempt = _emilio_schema_attempt(state)
            slot = ("emilio", schema_attempt)
            if slot != prior_slot:
                prior = ()
                prior_slot = slot
            try:
                outcome = run_emilio_attempt(
                    mission_id, schema_attempt, dict(adapters), config=config, prior_attempts=prior
                )
            except InvocationNotAuthorized as exc:
                current = chugel.get_mission(mission_id)
                return RunnerResult(
                    "HUMAN_ACTION_REQUIRED", current["state"], _durable_attempt_counts(current)[0],
                    f"dispatch not eligible: {exc}",
                )
            prior += (outcome.attempt_record,)
            if outcome.result.outcome != "completed":
                # The just-completed dispatch is already durably reflected
                # in dispatch_ledger (reserve_dispatch() wrote it before
                # the provider was ever invoked) -- the next iteration's
                # top-of-loop budget check re-derives total/builder counts
                # fresh and decides whether to retry or stop, identically
                # whether this is the same process continuing or a fresh
                # restart observing the same ledger.
                continue
            current = chugel.get_mission(mission_id)
            if current["state"] in {"BUILDING", "CORRECTING"}:
                # Both are legal (state, "VERIFYING") pairs in the
                # canonical TRANSITIONS table (orchestrator/validator.py)
                # -- a corrective-cycle builder attempt must advance to
                # VERIFYING exactly like the initial attempt does, or a
                # completed attempt=1 leaves the mission stuck in
                # CORRECTING forever (reserve_dispatch() then correctly,
                # but unhelpfully, refuses every further attempt=1
                # dispatch as a duplicate).
                chugel.transition(mission_id, "VERIFYING", actor="chugel", reason="builder evidence persisted")
            continue

        if state in {"VERIFYING", "AWAITING_REVIEW", "REVIEWING"}:
            if reviewer_attempts >= max_reviewer_attempts:
                return RunnerResult("HUMAN_ACTION_REQUIRED", state, total_attempts, "reviewer attempt budget exhausted")
            if state == "VERIFYING":
                chugel.transition(mission_id, "AWAITING_REVIEW", actor="chugel", reason="ready for independent review")
                continue
            if state == "AWAITING_REVIEW":
                chugel.transition(mission_id, "REVIEWING", actor="chugel", reason="independent review started")
                continue
            schema_attempt = _emma_schema_attempt(record)
            slot = ("emma", schema_attempt)
            if slot != prior_slot:
                prior = ()
                prior_slot = slot
            try:
                outcome = run_emma_attempt(
                    mission_id, schema_attempt, dict(adapters), config=config, prior_attempts=prior
                )
            except InvocationNotAuthorized as exc:
                current = chugel.get_mission(mission_id)
                return RunnerResult(
                    "HUMAN_ACTION_REQUIRED", current["state"], _durable_attempt_counts(current)[0],
                    f"dispatch not eligible: {exc}",
                )
            prior += (outcome.attempt_record,)
            if outcome.result.outcome != "completed":
                continue
            current = chugel.get_mission(mission_id)
            verdict = (outcome.result.evidence or {}).get("verdict")
            if verdict == "CHANGES_REQUIRED":
                chugel.transition(mission_id, "CHANGES_REQUIRED", actor="emma", reason="review requested changes")
                chugel.transition(mission_id, "CORRECTING", actor="chugel", reason="start bounded corrective cycle")
            elif verdict == "PASS":
                chugel.transition(mission_id, "PUBLISH_AWAITING_AUTHORIZATION", actor="emma", reason="review passed")
            else:
                return RunnerResult(
                    "HUMAN_ACTION_REQUIRED", current["state"], _durable_attempt_counts(current)[0],
                    "review verdict is not actionable",
                )
            continue

        return RunnerResult("HUMAN_ACTION_REQUIRED", state, total_attempts, "no autonomous transition is defined")
