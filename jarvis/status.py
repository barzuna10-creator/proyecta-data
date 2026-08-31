"""Immutable, allow-listed projections of validated Chugel Mission Records."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Literal

MissionBucket = Literal["running", "waiting_on_jose", "blocked", "terminal"]

# Verification Hardening V1, Pillar 3 (Progress Watchdog): these three
# numbers are duplicated here, deliberately, rather than imported from
# their real sources -- jarvis.mission_coordinator.DEFAULT_CI_POLL_TIMEOUT_SECONDS,
# orchestrator.adapters.codex_cli_adapter.DEFAULT_TIMEOUT_SECONDS, and
# orchestrator.adapters.claude_cli_adapter.DEFAULT_TIMEOUT_SECONDS.
# tests/test_jarvis_foundation_boundaries.py enforces two structural walls
# that a runtime import here would violate: jarvis.mission_coordinator may
# only ever be imported by jarvis/mission_supervisor.py
# (SOLE_COORDINATOR_IMPORTER), and no jarvis production module may import
# anything under orchestrator.adapters/autonomous_runner/wiring/
# provider_router at all (FORBIDDEN_IMPORT_PREFIXES) -- jarvis is
# structurally walled off from execution infrastructure, full stop, cycle-
# safety notwithstanding. This module respects both walls rather than
# weakening either. The same declare-and-test-pin pattern already used
# above for _GATE_WAITING_STATES (vs. jarvis/mission_supervisor.py's own
# GATE_WAITING_STATES): tests/test_jarvis_status.py's drift-guard tests
# import the three real values directly (test files are not scanned by
# either boundary test) and assert exact equality with the constants
# below, so an edit to one without the other is caught immediately.
_EMILIO_DISPATCH_TIMEOUT_SECONDS = 600.0
_EMMA_DISPATCH_TIMEOUT_SECONDS = 300.0
DEFAULT_CI_POLL_TIMEOUT_SECONDS = 1800.0

# Verification Hardening V1, Pillar 3 (Progress Watchdog): a read-only,
# purely-derived classification of whether a mission has stopped
# progressing -- never persisted, never touches jarvis/mission_supervisor.py's
# own in-memory _stalled set (which this deliberately does not import or
# depend on), never a trigger for any auto-remediation, new transition, or
# gate. Recomputed fresh from the Mission Record on every read, so it is
# restart-equivalent by construction: nothing about how it's computed
# depends on process memory, and a fresh process reading the same record
# at the same wall-clock moment produces the identical answer.
Staleness = Literal["NORMAL", "WATCH", "STALLED"]


@dataclass(frozen=True)
class GateStatus:
    name: str
    status: str


@dataclass(frozen=True)
class BuilderStatus:
    attempt: int
    conclusion_label: str
    conclusion_text: str


@dataclass(frozen=True)
class FindingStatus:
    finding_id: str
    severity: str
    summary: str
    file: str | None
    line_range: str | None
    category: str


@dataclass(frozen=True)
class ReviewerStatus:
    attempt: int
    verdict: str
    findings: tuple[FindingStatus, ...]


@dataclass(frozen=True)
class RepositoryStatus:
    branch: str
    base_sha: str
    isolation_confirmed: bool


# Verification Hardening V1, Pillar 4 (Structured Progress / Timeline
# Projection): a read-only, purely-derived, deterministic merge of
# state_history and dispatch_ledger -- both already durable Mission
# Record fields, both already schema-validated by orchestrator/validator.py
# before any record this reads was ever written. No new persistence, no
# new event store: this is the same "compute a view, never store one"
# discipline Pillar 3's staleness already established. Two event
# "kinds" share one dataclass shape (rather than a real union type) so
# jarvis/control_plane_server.py can serialize a flat, homogeneous list
# without a discriminated-union encoding of its own; unused fields for a
# given `kind` are always None, never omitted.
TimelineEventKind = Literal["state_transition", "dispatch"]


@dataclass(frozen=True)
class TimelineEvent:
    at: str
    kind: TimelineEventKind
    # state_transition fields -- from record["state_history"][i]. Always
    # None on a "dispatch" event.
    from_state: str | None = None
    to_state: str | None = None
    actor: str | None = None
    # Deliberately NO `reason` field. Round-1 Emma review (P0): Chugel's
    # own schema defines state_history[i].reason as a free string, not a
    # closed enum -- and, verified directly, orchestrator/publish_executor.py
    # and orchestrator/merge_executor.py both have real, reachable
    # BLOCKED-transition call sites that pass `reason=str(exc)`, an
    # exception message that can embed raw subprocess/stderr content.
    # That data already sits durably in the Mission Record today (this
    # pillar changes nothing about that, and does not touch those
    # writers) -- but surfacing it through THIS projection would be the
    # first time it ever reaches an HTTP-exposed API, which is exactly
    # what "cero exception messages/free text nuevas" (this pillar's own
    # authorized constraint) forbids. No sanitization, no redaction, no
    # allow-list of "safe" reasons, no substitute field: a
    # state_transition event communicates only WHAT transitioned and
    # WHEN, via from_state/to_state/actor/at -- all four already
    # schema-closed or structurally safe (actor is a small, fixed set of
    # system/human identities, never free text). See
    # tests/test_jarvis_status.py's TimelineReasonNeverLeaksTests for the
    # explicit regression proving this.
    # dispatch fields -- from record["dispatch_ledger"][i]. Always None
    # on a "state_transition" event. Every one of these is either a
    # schema-closed enum or a nullable schema-closed enum -- role
    # ("emilio"|"emma"), provider ("codex"|"claude"|None), model (a
    # nullable provider-identity string, never free text), status
    # (RESERVED/IN_FLIGHT/RESULT_RECORDED/FINALIZED),
    # result_classification (completed/failed/timeout/invalid_output/
    # unavailable/None). `reason_code` is the ONE field lifted out of
    # dispatch_ledger[i]["diagnostic"] -- deliberately only reason_code,
    # never any other diagnostic field (exit_code, stderr_byte_length,
    # etc.), and never error_detail (which is not durable at all -- see
    # orchestrator/agent_invocation.py's own docstring) and never a raw
    # provider output excerpt or exception message. reason_code is
    # itself a schema-closed enum (orchestrator/schemas/mission_record.
    # schema.json's dispatch_diagnostic definition) -- e.g.
    # "TIMEOUT_EXCEEDED", "FAILED_NONZERO_EXIT" -- structurally
    # incapable of carrying free text.
    role: str | None = None
    attempt: int | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    result_classification: str | None = None
    reason_code: str | None = None


def _state_history_timeline_events(record: dict[str, Any]) -> list[TimelineEvent]:
    events = []
    for entry in record.get("state_history") or []:
        events.append(TimelineEvent(
            at=str(entry["at"]),
            kind="state_transition",
            from_state=None if entry["from_state"] is None else str(entry["from_state"]),
            to_state=str(entry["to_state"]),
            actor=str(entry["actor"]),
        ))
    return events


def _dispatch_ledger_timeline_events(record: dict[str, Any]) -> list[TimelineEvent]:
    events = []
    for entry in record.get("dispatch_ledger") or []:
        diagnostic = entry.get("diagnostic")
        reason_code = (
            str(diagnostic["reason_code"]) if isinstance(diagnostic, dict) else None
        )
        events.append(TimelineEvent(
            at=str(entry["updated_at"]),
            kind="dispatch",
            role=str(entry["role"]),
            attempt=int(entry["attempt"]),
            provider=None if entry["provider"] is None else str(entry["provider"]),
            model=None if entry["model"] is None else str(entry["model"]),
            status=str(entry["status"]),
            result_classification=(
                None if entry["result_classification"] is None
                else str(entry["result_classification"])
            ),
            reason_code=reason_code,
        ))
    return events


def compute_mission_timeline(record: dict[str, Any]) -> tuple[TimelineEvent, ...]:
    """Deterministic total order over state_history + dispatch_ledger
    events. Primary key: `at` (Chugel's own "%Y-%m-%dT%H:%M:%SZ" format,
    lexicographically sortable at second granularity). Tie-break for two
    events landing in the same second: state_transition events before
    dispatch events, then each source list's own original (already
    chronological, append-only for state_history; monotonically
    updated-in-place for dispatch_ledger) order -- a documented,
    deterministic convention, not a claim of true sub-second causal
    order finer than the record itself durably captures. A
    dispatch_ledger entry contributes exactly ONE event, at its current
    (most recent) status/updated_at -- Chugel mutates the same entry in
    place through RESERVED -> IN_FLIGHT -> RESULT_RECORDED -> FINALIZED
    rather than durably retaining each intermediate status, so "one
    event per (role, attempt) reflecting where it currently stands" is
    the real granularity the Mission Record itself preserves."""
    state_events = _state_history_timeline_events(record)
    dispatch_events = _dispatch_ledger_timeline_events(record)
    combined = (
        [(0, i, e) for i, e in enumerate(state_events)]
        + [(1, i, e) for i, e in enumerate(dispatch_events)]
    )
    combined.sort(key=lambda item: (item[2].at, item[0], item[1]))
    return tuple(e for _, _, e in combined)


@dataclass(frozen=True)
class MissionStatus:
    mission_id: str
    state: str
    bucket: MissionBucket
    updated_at: str
    mission_definition_version: int
    corrective_cycle_count: int
    repository: RepositoryStatus
    gates: tuple[GateStatus, ...]
    builder: tuple[BuilderStatus, ...]
    reviewer: tuple[ReviewerStatus, ...]
    human_action_required: str | None
    staleness: Staleness
    timeline: tuple[TimelineEvent, ...]


_HUMAN_ACTION_BY_STATE = {
    "SCOPE_AWAITING_AUTHORIZATION": "scope_authorization",
    "PUBLISH_AWAITING_AUTHORIZATION": "publish_authorization",
    "MERGE_AWAITING_AUTHORIZATION": "merge_authorization",
    "BLOCKED": "human_direction",
}

_BUCKET_BY_STATE: dict[str, MissionBucket] = {
    "INTAKE": "running",
    "SCOPE_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "AUTHORIZED": "running",
    "BUILDING": "running",
    "VERIFYING": "running",
    "AWAITING_REVIEW": "running",
    "REVIEWING": "running",
    "CHANGES_REQUIRED": "running",
    "CORRECTING": "running",
    "PUBLISH_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "PUBLISHING": "running",
    "CI_PENDING": "running",
    "MERGE_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "MERGING": "running",
    "MERGED": "running",
    "DEPLOY_PENDING": "running",
    "VERIFYING_PRODUCTION": "running",
    "COMPLETED": "terminal",
    "BLOCKED": "blocked",
    "FAILED": "terminal",
    "CANCELLED": "terminal",
    "ROLLED_BACK": "terminal",
}

# Verification Hardening V1, Pillar 3. Explicit policy, José-approved (not
# re-derived from anything -- this is the one category with no existing
# adapter/CI timeout to borrow, so the threshold is a direct policy
# decision): 30/90 minutes for a "running"-bucket state with no live
# dispatch and no CI/publish/merge machinery of its own.
_OTHER_LIVE_WATCH_SECONDS = 30 * 60
_OTHER_LIVE_STALLED_SECONDS = 90 * 60

# The three real human-gate states -- never a staleness candidate by
# elapsed time, however long they've been open (explicit policy).
# Declared independently here, not imported from
# jarvis/mission_supervisor.py's own GATE_WAITING_STATES: that module
# already imports jarvis/mission_query.py, which imports this module --
# importing back from mission_supervisor.py here would be a real import
# cycle. tests/test_jarvis_status.py cross-checks this set against
# mission_supervisor.GATE_WAITING_STATES directly, the same
# declare-and-test-pin pattern this codebase already uses elsewhere
# (e.g. orchestrator/validator.py's GATE_STATUSES vs its own consumers)
# rather than a runtime import that would cycle.
_GATE_WAITING_STATES = frozenset({
    "SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION",
})

# States where a real provider dispatch can genuinely be reserved, and
# which role dispatches there -- orchestrator/chugel.py's own
# reserve_dispatch()/_MISSION_ROLE_EXPECTED_STATE mapping is the
# authoritative source (BUILDING/CORRECTING -> emilio, REVIEWING ->
# emma). VERIFYING/AWAITING_REVIEW/CHANGES_REQUIRED are deliberately
# excluded: orchestrator/autonomous_runner.py's run_mission() drives
# each of those as a single mechanical chugel.transition() call with no
# dispatch of its own, always advancing within the same synchronous
# call under normal operation -- they belong in _OTHER_LIVE_STATES
# below instead, judged by elapsed time like any other quiet
# "running"-bucket state, not by a dispatch timeout that does not apply
# to them.
_DISPATCH_ROLE_BY_STATE = {
    "BUILDING": "emilio",
    "CORRECTING": "emilio",
    "REVIEWING": "emma",
}

_ROLE_DISPATCH_TIMEOUT_SECONDS = {
    "emilio": _EMILIO_DISPATCH_TIMEOUT_SECONDS,
    "emma": _EMMA_DISPATCH_TIMEOUT_SECONDS,
}

# publish_executor.py/merge_executor.py's own real deadline for driving
# these states -- a live process resolves PUBLISHING/CI_PENDING/MERGING
# one way or another (success, BLOCKED, or a real CI timeout) within
# ci_poll_timeout_seconds; sitting past it unchanged means no live
# process is actually working the mission, not "waiting on CI".
_CI_LIFECYCLE_STATES = frozenset({"PUBLISHING", "CI_PENDING", "MERGING"})

# The remaining "running"-bucket states judged by elapsed time alone
# (_OTHER_LIVE_WATCH_SECONDS/_OTHER_LIVE_STALLED_SECONDS above).
# MERGED/DEPLOY_PENDING/VERIFYING_PRODUCTION are deliberately excluded --
# jarvis/mission_coordinator.py's advance() has no branch for any of
# them (MERGED is its own terminal report; the other two are schema-legal
# future states no current code drives), so unlike a genuinely stuck
# auto-advanceable mission, no automatic action is ever expected there
# regardless of elapsed time -- treating them as staleness candidates
# would be a false positive by construction, not a real signal.
_OTHER_LIVE_STATES = frozenset({
    "INTAKE", "AUTHORIZED", "VERIFYING", "AWAITING_REVIEW", "CHANGES_REQUIRED",
})


def _parse_timestamp(raw: object) -> datetime.datetime | None:
    """Returns an aware UTC datetime, or None if `raw` is not a real,
    parseable timestamp in Chugel's own canonical shape (see
    orchestrator/chugel.py's _now(): always "%Y-%m-%dT%H:%M:%SZ", never
    fractional seconds, never a non-Z offset). Callers must treat None
    as "cannot determine staleness", never as "definitely stale" or
    "definitely fresh" -- this function is fail-safe, not fail-closed,
    for this one purely-informational signal: an invalid, missing, or
    otherwise unparseable timestamp must never itself manufacture a
    WATCH/STALLED classification nobody actually earned."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _elapsed_seconds(raw_timestamp: object, now: datetime.datetime) -> float | None:
    """None means "cannot determine" (unparseable, or a future timestamp
    -- clock skew or a corrupted record. Never negative, and never used
    to manufacture a stall the record did not actually earn)."""
    parsed = _parse_timestamp(raw_timestamp)
    if parsed is None:
        return None
    elapsed = (now - parsed).total_seconds()
    if elapsed < 0:
        return None
    return elapsed


def _latest_in_flight_dispatch_entry(record: dict[str, Any], role: str) -> dict[str, Any] | None:
    """The dispatch_ledger entry (if any) for this role that is still
    IN_FLIGHT -- RESERVED does not yet reflect a real running process;
    RESULT_RECORDED/FINALIZED are no longer live. Reads dispatch_ledger
    directly, deliberately never projected onto MissionStatus itself --
    matching this module's own allow-list discipline, only the DERIVED
    staleness classification is ever exposed, never a raw ledger entry."""
    ledger = record.get("dispatch_ledger")
    if not isinstance(ledger, list):
        return None
    candidates = [
        entry for entry in ledger
        if isinstance(entry, dict) and entry.get("role") == role and entry.get("status") == "IN_FLIGHT"
    ]
    if not candidates:
        return None
    # At most one IN_FLIGHT entry can exist per (role, attempt) by
    # chugel.reserve_dispatch()'s own construction; if more than one
    # somehow appears (a corrupted record), the most recently updated one
    # is the relevant "how long has this really been running" signal.
    return max(candidates, key=lambda entry: str(entry.get("updated_at") or ""))


def _classify_by_threshold(
    elapsed: float | None, *, watch_at: float, stalled_at: float, watch_inclusive: bool = False,
) -> Staleness:
    """`watch_inclusive` distinguishes the two boundary phrasings the
    José-approved policy actually uses: the dispatch/CI-lifecycle
    categories are "NORMAL <= timeout; WATCH > timeout" (exclusive --
    `watch_inclusive=False`, the default), while the "other live
    states" category is explicitly "NORMAL < 30min; WATCH >= 30min"
    (inclusive at the boundary -- `watch_inclusive=True`). Both share
    "STALLED >= stalled_at" (inclusive) either way."""
    if elapsed is None:
        return "NORMAL"
    if elapsed >= stalled_at:
        return "STALLED"
    if elapsed >= watch_at if watch_inclusive else elapsed > watch_at:
        return "WATCH"
    return "NORMAL"


def compute_staleness(record: dict[str, Any], *, now: datetime.datetime) -> Staleness:
    """Pure, deterministic, restart-equivalent: every input is either
    `record` (the already-durable Mission Record) or the caller-supplied
    `now` -- no clock read of its own, no process memory, no dependency
    on jarvis/mission_supervisor.py's in-memory _stalled set. Never
    raises for a genuinely unknown/malformed state -- classify_mission_state()
    is the function responsible for failing closed on that; this one
    simply returns "NORMAL" (nothing to watch) for anything outside its
    own known categories, exactly like it does for MERGED/terminal/blocked
    states."""
    state = record.get("state")

    if state in _GATE_WAITING_STATES:
        return "NORMAL"

    if state in _DISPATCH_ROLE_BY_STATE:
        role = _DISPATCH_ROLE_BY_STATE[state]
        timeout = _ROLE_DISPATCH_TIMEOUT_SECONDS[role]
        live_entry = _latest_in_flight_dispatch_entry(record, role)
        reference_timestamp = (
            live_entry.get("updated_at") if live_entry is not None else record.get("updated_at")
        )
        elapsed = _elapsed_seconds(reference_timestamp, now)
        return _classify_by_threshold(elapsed, watch_at=timeout, stalled_at=timeout * 2)

    if state in _CI_LIFECYCLE_STATES:
        elapsed = _elapsed_seconds(record.get("updated_at"), now)
        return _classify_by_threshold(
            elapsed, watch_at=DEFAULT_CI_POLL_TIMEOUT_SECONDS, stalled_at=DEFAULT_CI_POLL_TIMEOUT_SECONDS * 2,
        )

    if state in _OTHER_LIVE_STATES:
        elapsed = _elapsed_seconds(record.get("updated_at"), now)
        return _classify_by_threshold(
            elapsed, watch_at=_OTHER_LIVE_WATCH_SECONDS, stalled_at=_OTHER_LIVE_STALLED_SECONDS,
            watch_inclusive=True,
        )

    # Terminal, blocked, MERGED-family, or any state outside the
    # canonical vocabulary -- never a staleness candidate.
    return "NORMAL"


class UnknownMissionState(ValueError):
    """A state outside the canonical V1 vocabulary cannot be classified."""


def classify_mission_state(state: str) -> MissionBucket:
    try:
        return _BUCKET_BY_STATE[state]
    except (KeyError, TypeError) as exc:
        raise UnknownMissionState("unknown mission state") from exc


def project_mission_status(record: dict[str, Any], *, now: datetime.datetime | None = None) -> MissionStatus:
    """Copy only the documented V1 allow-list; never retain ``record`` values.

    The caller must provide a Mission Record already validated by Chugel. No
    unknown field, intent, provider identity/output, dispatch entry, raw gate
    decision, worktree path, or publication/deployment payload is projected --
    ``staleness`` and ``timeline`` are the exceptions in spirit, not in
    mechanism: both are DERIVED views computed only from already-allow-listed
    fields (state/updated_at/dispatch_ledger timestamps, and -- for
    ``timeline`` -- state_history plus dispatch_ledger's own schema-closed
    enum fields and diagnostic.reason_code), never a raw or free-text value.

    ``now``, if omitted, defaults to the real current UTC time -- injectable
    so ``staleness`` (Verification Hardening V1, Pillar 3) stays exactly as
    pure and deterministic for tests as the rest of this function already is.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    repository = record["repository"]
    gates = record["human_gates"]
    return MissionStatus(
        mission_id=str(record["mission_id"]),
        state=str(record["state"]),
        bucket=classify_mission_state(record["state"]),
        updated_at=str(record["updated_at"]),
        mission_definition_version=len(record["mission_definition_history"]),
        corrective_cycle_count=int(record["corrective_cycle_count"]),
        repository=RepositoryStatus(
            branch=str(repository["branch"]),
            base_sha=str(repository["base_sha"]),
            isolation_confirmed=bool(repository["isolation_confirmed"]),
        ),
        gates=tuple(
            GateStatus(name=name, status=str(gates[name]["status"]))
            for name in ("scope_authorization", "publish_authorization", "merge_authorization")
        ),
        builder=tuple(
            BuilderStatus(
                attempt=int(entry["attempt"]),
                conclusion_label=str(entry["conclusion"]["label"]),
                conclusion_text=str(entry["conclusion"]["text"]),
            )
            for entry in record["builder_evidence"]
        ),
        reviewer=tuple(
            ReviewerStatus(
                attempt=int(entry["attempt"]),
                verdict=str(entry["verdict"]),
                findings=tuple(
                    FindingStatus(
                        finding_id=str(finding["id"]),
                        severity=str(finding["severity"]),
                        summary=str(finding["summary"]),
                        file=None if finding["file"] is None else str(finding["file"]),
                        line_range=None if finding["line_range"] is None else str(finding["line_range"]),
                        category=str(finding["category"]),
                    )
                    for finding in entry["findings"]
                ),
            )
            for entry in record["reviewer_evidence"]
        ),
        human_action_required=_HUMAN_ACTION_BY_STATE.get(record["state"]),
        staleness=compute_staleness(record, now=now),
        timeline=compute_mission_timeline(record),
    )
