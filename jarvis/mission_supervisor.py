"""Mission 006 -- the single-worker, coalescing wake/drain that drives
jarvis.mission_coordinator.advance() forward after a real human
authorization, without a second queue or a second state machine.

Design invariant: this module never decides WHAT to do -- it only decides
WHEN to ask mission_coordinator.advance() to look. Every unit of "is there
eligible work" is re-derived, every single time, from jarvis.mission_query
(the disclosed read-only Chugel seam) -- never from anything this module
remembers between calls. A crash here loses nothing durable: the next
notify() (a real authorization event, or the one-shot startup recovery
sweep) re-derives the same answer from Chugel's own persisted state.

CLI-only/authorization-only callers, by construction, not by convention:
this module is the sole production importer of jarvis.mission_coordinator
(see tests/test_jarvis_foundation_boundaries.py's
test_only_mission_supervisor_imports_mission_coordinator) --
jarvis.control_plane_server never imports jarvis.mission_coordinator
directly, only this module, and only calls notify() from the two real
authorization-consequence branches inside _handle_authorize() (draft
authorization, and the real scope/publish/merge gate branch --
jarvis.mission_write.resume_from_blocked() exists but has no HTTP call
site anywhere today, so this module has nothing to notify() for it yet).
notify() is never reachable from _handle_conversation() -- see
tests/test_jarvis_control_plane_server.py's boundary coverage.

Mission 006 (gate-consumption follow-up): the three *_AWAITING_AUTHORIZATION
states are auto-advance-eligible too -- see mission_coordinator.py's own
docstring for why this is not a bypass. advance() itself is what makes
this safe: for those three states it first checks whether
jarvis.mission_write.authorize_scope/publish/merge (via
chugel.decide_gate()) already recorded a real decision, and only ever
transitions the mission if so; if the gate is still "pending"/
"not_requested", advance() is a pure no-op for that mission (returns
GATE_REQUIRED, touches nothing) -- exactly as safe to call from
recover_on_startup() after a crash as from a real notify(). This module
itself does not know or care which of the two cases applies; it only
knows these three states are worth asking advance() about, on every
trigger, same as any other auto-advance-eligible state."""

from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from jarvis import mission_coordinator, mission_query

# States from which mission_coordinator.advance() can make real forward
# progress -- either STATE progress, or (for the three gate states) a
# side-effect-free check of whether it can. Mirrors advance()'s own
# dispatch table exactly (jarvis/mission_coordinator.py), never redefines
# a second one. INTAKE (Mission 006's one mechanical, non-human
# transition), the autonomous_runner-driven build/review/corrective
# family, the publish/merge-executor-driven family, and -- Mission 006's
# gate-consumption follow-up -- the three *_AWAITING_AUTHORIZATION states:
# advance() itself only ever transitions one of those if
# human_gates.<name>.status is already "approved"/"rejected" (a real,
# already-persisted decide_gate() decision); if still "pending"/
# "not_requested" it is a pure no-op (GATE_REQUIRED, nothing touched), so
# calling it from every trigger -- notify() or recover_on_startup() after
# a crash -- never bypasses or implicitly grants a gate. See
# mission_coordinator.py's own module docstring for the full authority
# argument.
AUTO_ADVANCE_ELIGIBLE_STATES = frozenset({
    "INTAKE",
    "SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED", "BUILDING", "VERIFYING", "AWAITING_REVIEW", "REVIEWING",
    "CHANGES_REQUIRED", "CORRECTING",
    "PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING", "CI_PENDING",
    "MERGE_AWAITING_AUTHORIZATION", "MERGING",
})

# The three real Chugel states a human gate authorization is pending
# against -- kept purely as a documented, tested SUBSET of
# AUTO_ADVANCE_ELIGIBLE_STATES (see StateClassificationTests), not as an
# eligibility exclusion: as of the gate-consumption follow-up, these are
# no longer excluded from the drain pass -- advance()'s own per-gate
# status check (see above) is what keeps calling it on them safe.
GATE_WAITING_STATES = frozenset({
    "SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION",
})

# No further work is ever possible from these -- excluded from the drain
# pass by construction (not a special case: they are simply not in
# AUTO_ADVANCE_ELIGIBLE_STATES). Includes the three post-MERGED states
# (DEPLOY_PENDING, VERIFYING_PRODUCTION, COMPLETED) that exist in
# orchestrator/schemas/mission_record.schema.json's state enum and in
# validator.TRANSITIONS but that mission_coordinator.advance() itself has
# no branch for -- it treats MERGED as advance()'s own terminal report
# (Mission 004, unmodified here) and this module drives nothing beyond
# what advance() itself drives. Round-2 independent review, P3: without
# these three, the classification was not exhaustive over the schema
# state enum -- see test_every_schema_state_is_classified_somewhere.
TERMINAL_STATES = frozenset({
    "MERGED", "FAILED", "CANCELLED", "ROLLED_BACK",
    "DEPLOY_PENDING", "VERIFYING_PRODUCTION", "COMPLETED",
})

# BLOCKED is its own bucket: waiting on a human to confirm an external
# issue is resolved (jarvis.mission_write.resume_from_blocked()) -- never
# retried automatically, and deliberately absent from every set above.


@dataclass(frozen=True)
class DrainOutcome:
    """What one worker pass actually did -- exposed for tests and
    observability, never used to make a second-source-of-truth decision
    anywhere. `reports` is a tuple of (mission_id, CoordinatorReport),
    one entry per mission this pass successfully called advance() on.
    `errors` is a tuple of (mission_id, exception) for any mission whose
    advance() call raised -- never re-raised out of the worker thread."""
    reports: tuple[tuple[str, "mission_coordinator.CoordinatorReport"], ...]
    errors: tuple[tuple[str, BaseException], ...] = ()


class MissionSupervisor:
    """Single-worker, coalescing wake/drain over Chugel's own mission
    state. notify() is the only way to cause work to happen; it never
    blocks the caller and never runs advance() on the calling thread --
    the actual work always happens on this supervisor's own background
    worker thread, of which at most one is ever alive at a time."""

    def __init__(self, *, adapters: dict | None = None, adapter_factory=None, advance_kwargs: dict,
                 max_concurrency: int = 1, lease=None):
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or not 1 <= max_concurrency <= 8:
            raise ValueError("max_concurrency must be an integer from 1 through 8")
        if adapter_factory is not None and adapters is not None:
            raise ValueError("provide adapter_factory or adapters, not both")
        self._adapter_factory = adapter_factory or (lambda: dict(adapters or {}))
        self._advance_kwargs = dict(advance_kwargs)
        self._max_concurrency = max_concurrency
        self._lease = lease
        self._pool = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="mission-worker")
        self._inflight: set[str] = set()
        self._closed = False
        self._wake = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        # The sole source of truth for "is a worker currently owned/
        # responsible for servicing wakes" -- deliberately NOT derived from
        # threading.Thread.is_alive(). Emma's independent review (round 1)
        # found a genuine lost-wake race: is_alive() can still read True
        # for a brief window after the worker's target function has
        # already returned (the OS thread has not finished tearing itself
        # down yet), so notify() could conclude "an existing worker will
        # handle this" for a wake that worker had already decided (holding
        # the same lock, having seen the flag clear) never to re-check.
        # This boolean is instead flipped only at the two points that
        # matter (notify() setting it True under the lock right before
        # starting a thread; _run() setting it False under the SAME lock
        # only once it has verified, still holding that lock, that no
        # notify() call is racing it) -- so the two sides can never both
        # conclude "the other one owns this wake."
        self._worker_running = False
        # Test/observability hook only -- never read by this module's own
        # control flow, which always re-derives from mission_query. Round-2
        # independent review, P3: bounded (a plain list here grew without
        # bound for the whole process lifetime, one entry per drain pass --
        # harmless in a test process, unbounded in a long-lived Control
        # Plane process). The exact bound is arbitrary; it only needs to be
        # "enough for a human or a test to see recent history," never
        # "every pass this process has ever run."
        self.last_drain_outcomes: deque[DrainOutcome] = deque(maxlen=200)
        # Mission_ids this supervisor instance has given up on for the
        # rest of its process lifetime -- populated the moment advance()
        # either raises for that mission or reports HUMAN_ACTION_REQUIRED
        # (Chugel's own state model has no distinct persisted state for
        # "auto-advance-eligible state, but structurally stuck" -- e.g. an
        # exhausted attempt/deadline budget inside BUILDING -- so this is
        # the only place that fact can be remembered at all). Requirement
        # 5: once a mission lands here, the supervisor never touches it
        # again on its own; only a fresh process start re-attempts it once
        # (see recover_on_startup()'s own docstring), matching how BLOCKED/
        # terminal states are already excluded by AUTO_ADVANCE_ELIGIBLE_STATES
        # itself. This is also what keeps _run()'s loop from spinning
        # forever on a mission that will never make further state progress
        # (Emma round 1, P0): once stalled, _drain_pass() no longer submits
        # it, so a pass that finds nothing else eligible correctly reports
        # empty and the loop's existing termination condition applies.
        self._stalled: set[str] = set()

    @property
    def workspace_base_root(self):
        manager = self._advance_kwargs.get("workspace_manager")
        return manager.base_root if manager is not None else None

    def notify(self) -> None:
        """Coalescing wake: safe to call any number of times, from any
        number of threads, concurrently or not. Guarantees that after
        this call returns, either a worker pass that starts strictly
        after this call's wake.set() will eventually run, or an already-
        running worker has been -- atomically, under the same lock this
        method itself uses -- confirmed still responsible for observing
        this wake before it could exit. See _worker_running's docstring
        for why this is not simply threading.Thread.is_alive()."""
        with self._worker_lock:
            if self._closed:
                raise RuntimeError("mission supervisor is closed")
            self._wake.set()
            if not self._worker_running:
                self._worker_running = True
                self._worker = threading.Thread(target=self._run, daemon=True, name="mission-supervisor")
                try:
                    self._worker.start()
                except BaseException:
                    # Round-2 independent review, P2: if the thread never
                    # actually started, nothing will ever reach _run()'s
                    # own try/finally to clear this -- roll it back here,
                    # still holding the lock, so this is not a permanent,
                    # silent wedge (every notify() thereafter taking the
                    # "an existing worker will handle it" branch forever,
                    # with no worker actually running). The caller still
                    # sees this exception -- notify() deliberately does not
                    # swallow it -- but the supervisor itself recovers.
                    self._worker_running = False
                    self._worker = None
                    raise

    def recover_on_startup(self) -> None:
        """One-shot, non-recurring: call exactly once when the Control
        Plane process starts. Re-derives eligible work from Chugel's own
        persisted state -- never a retry of BLOCKED or any terminal state
        (structurally absent from AUTO_ADVANCE_ELIGIBLE_STATES, the only
        set the drain pass ever consults), and never an implicit grant of
        a still-pending human gate: the three *_AWAITING_AUTHORIZATION
        states ARE in that set (Mission 006's gate-consumption follow-up),
        but advance() itself only ever transitions one of them if
        human_gates.<name>.status is already "approved"/"rejected" -- a
        real decision decide_gate() already persisted, possibly before a
        crash that happened before the mechanical transition ran. This is
        resuming an already-granted authorization, not granting one. This
        is not a poll loop: it fires once, then all further work is
        event-driven via notify()."""
        self.notify()

    def _run(self) -> None:
        # Round-2 independent review, P2: the intended exit path below
        # (wake not set -> clear _worker_running -> return, all inside one
        # critical section) is what makes notify() and this method's own
        # exit decision race-free -- deliberately left untouched here. But
        # anything escaping _drain_pass()'s own error handling (a
        # BaseException, or a bug this method's own code introduces) must
        # still not leave _worker_running stuck True forever -- that would
        # silently wedge all future notify() calls into a no-op, with no
        # process restart trigger and no error surfaced to any HTTP
        # caller. This except is a distinct safety net for that abnormal
        # case only; it does not run, and does not need to coordinate
        # with notify(), on the normal path Emma's round-2 review traced
        # and confirmed race-free.
        try:
            while True:
                # Clear BEFORE draining: a notify() that lands after this
                # clear (even mid-drain, from another authorization
                # completing concurrently) is preserved for the next loop
                # iteration below -- never lost, never requiring the
                # caller to know whether a worker happens to already be
                # running.
                self._wake.clear()
                outcome = self._drain_pass()
                self.last_drain_outcomes.append(outcome)
                with self._worker_lock:
                    if not self._wake.is_set():
                        # No notify() call is racing this decision -- both
                        # this check and any concurrent notify() hold the
                        # same lock, so exactly one of "we keep looping" /
                        # "a fresh worker gets started later" is true,
                        # never neither.
                        self._worker_running = False
                        return
                    # Something woke us again while we were draining or
                    # deciding to exit -- loop and drain again rather than
                    # exit and rely on that notify() call to have started
                    # a (redundant) new worker.
        except BaseException:
            with self._worker_lock:
                self._worker_running = False
            raise

    def _drain_pass(self) -> DrainOutcome:
        """Never lets one mission's failure kill this worker thread or
        skip the rest of the pass: mission_coordinator.advance() itself
        already fails closed for provider/authorization problems (BLOCKED/
        TERMINAL_FAILURE/HUMAN_ACTION_REQUIRED reports, never raised) --
        what is caught HERE is a narrower, structural class this module
        must still not crash on (a record deleted/corrupted between the
        listing read and the advance() call, or any other unexpected
        exception). A caught error is recorded, never retried within this
        same pass; the next notify() or startup sweep will simply
        re-derive the same eligible-work answer from Chugel fresh."""
        reports: list[tuple[str, "mission_coordinator.CoordinatorReport"]] = []
        errors: list[tuple[str, BaseException]] = []
        try:
            listings = mission_query.list_missions()
        except Exception as exc:  # noqa: BLE001 -- a whole-pass listing failure (e.g. the
            # missions directory itself briefly unreadable) must not kill
            # this worker thread either -- recorded under a fixed
            # non-mission-id key so callers can tell it apart from a
            # per-mission failure. The next notify() (or the wake this
            # very pass may still be racing, see _run()) drives a fresh
            # attempt; nothing here is treated as a reason to give up
            # permanently the way a per-mission failure below is.
            return DrainOutcome((), ((("<list_missions>"), exc),))
        candidates = []
        for listing in sorted(listings, key=lambda item: (item.updated_at or "", item.mission_id)):
            if not listing.readable or listing.state not in AUTO_ADVANCE_ELIGIBLE_STATES:
                continue
            if listing.mission_id in self._stalled:
                continue
            if listing.mission_id in self._inflight:
                continue
            candidates.append(listing.mission_id)

        pending = {}
        while candidates or pending:
            while candidates and len(pending) < self._max_concurrency:
                with self._worker_lock:
                    if self._closed:
                        candidates.clear()
                        break
                    mission_id = candidates.pop(0)
                    self._inflight.add(mission_id)
                    try:
                        adapters = self._adapter_factory()
                        future = self._pool.submit(
                            mission_coordinator.advance, mission_id, adapters,
                            **self._advance_kwargs,
                        )
                    except Exception as exc:  # fail only this reserved mission
                        self._inflight.discard(mission_id)
                        self._stalled.add(mission_id)
                        errors.append((mission_id, exc))
                        continue
                pending[future] = mission_id
            if not pending:
                break
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                mission_id = pending.pop(future)
                self._inflight.discard(mission_id)
                try:
                    report = future.result()
                except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
                # A structural failure to advance this mission at all --
                # never transient by construction here (no retry/backoff
                # is this module's to add, see requirement 5), so this
                # mission is stalled exactly like a HUMAN_ACTION_REQUIRED
                # report below: never resubmitted by this supervisor
                # instance again.
                    self._stalled.add(mission_id)
                    errors.append((mission_id, exc))
                    continue
                if report.status == "HUMAN_ACTION_REQUIRED":
                # advance()'s own state stays inside AUTO_ADVANCE_ELIGIBLE_STATES
                # for this report (Chugel has no distinct persisted state
                # for it) -- without this, the next pass would find the
                # exact same mission "eligible" again forever. Requirement
                # 5: once here, only a fresh process start (recover_on_startup())
                # attempts it again -- never this running supervisor on its
                # own.
                    self._stalled.add(mission_id)
                reports.append((mission_id, report))
        return DrainOutcome(tuple(reports), tuple(errors))

    def close(self) -> None:
        with self._worker_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self._lease is not None:
            self._lease.close()
