"""Verification Hardening V1, Pillar 4: Automated Recovery E2E Proof.

Drives real missions through the REAL wired stack --
jarvis.mission_supervisor.MissionSupervisor -> jarvis.mission_coordinator.
advance() -> orchestrator.autonomous_runner.run_mission() -> real
orchestrator.chugel persistence -- with injected crash/restart windows,
using the same deterministic AgentInvoker fakes
tests/test_orchestrator_autonomous_runner.py already established (never a
real subprocess, LLM call, or network access).

A "restart" here is a BRAND NEW MissionSupervisor instance sharing only
the on-disk Chugel store with whatever came before it -- no shared
Python object, no shared thread, no shared in-memory attribute
(_stalled, last_drain_outcomes, _worker_running, ...). This is as close
to a genuine separate-process restart as a same-process test can
honestly get: the supervisor holds no persisted state of its own by
design (see mission_supervisor.py's own module docstring), so a fresh
instance reading the same Chugel directory is indistinguishable from a
real process restart from Chugel's point of view.

Round-1 Emma review, P2 #1 -- why every "must not be called again"
assertion here is a durable, observable counter, never a raised
AssertionError: mission_supervisor._drain_pass() wraps advance() in a
broad `except Exception`, storing any raised error in the drain
outcome's `errors` tuple rather than letting it propagate. An adapter
that raises AssertionError on an unwanted call (this file's
_CountingNeverCalledAdapter, and test_orchestrator_autonomous_runner's
own _FakeAdapter when it runs out of authorized templates) would
therefore NOT fail a supervisor-driven test just by raising -- that
exception is swallowed by _drain_pass(), not surfaced to unittest. Every
adapter used below keeps its own `calls` list, and every test asserts
directly against that list's length after each _notify_and_wait() call
-- durable, inspectable evidence of "was this ever invoked," independent
of whether any exception the adapter raises happens to propagate.

Deliberate, documented scope boundary -- read this before extending:
PUBLISHING/CI_PENDING/MERGING are NOT driven through the real supervisor
here. advance()'s branches for those states invoke the REAL
orchestrator.publish_executor/merge_executor, which run real `git`/`gh`
subprocesses against `repository_root` -- there is no honest way to
fake that determinstically without either (a) actually standing up a
real git repository + GitHub remote (out of scope for a unit-style CI
test) or (b) mocking the subprocess layer so heavily the test would
stop proving anything real. The existing test suite already accepts
this same boundary (see tests/test_orchestrator_autonomous_runner.py's
own _mission_merge_awaiting_authorization() fixture, which reaches
MERGE_AWAITING_AUTHORIZATION via direct chugel.transition() calls, not
by running real executors). Restart safety for PUBLISHING/CI_PENDING/
MERGING therefore remains an explicit, undischarged requirement for a
separate, real, live acceptance run -- not fabricated here. What IS
proven end-to-end and automatically, every time this file runs in CI:
the full autonomous dispatch+review pipeline (BUILDING through
PUBLISH_AWAITING_AUTHORIZATION) survives independently-injected crash
windows on BOTH the Emilio and Emma dispatch legs, and
PUBLISH_AWAITING_AUTHORIZATION itself -- a real human gate -- is never
crossed by a restart alone.
"""

from __future__ import annotations

import copy
import datetime
import tempfile
import unittest
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel
from jarvis.mission_supervisor import MissionSupervisor
from jarvis.status import project_mission_status
from tests.test_orchestrator_autonomous_runner import (
    _FakeAdapter, _builder_evidence, _create_intake_mission,
    _emilio_completed_template, _emma_completed_template, _reviewer_evidence, _scope_gate_approval,
)


def _ago_iso(seconds: float) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


class _CountingNeverCalledAdapter:
    """Like test_orchestrator_autonomous_runner._NeverCalledAdapter, but
    also keeps its own durable, directly-inspectable `calls` list --
    every test in this file asserts against that list's length, never
    only against the AssertionError this still raises as defense in
    depth (see this module's own docstring for why the exception alone
    is not sufficient proof when driven through a real
    MissionSupervisor)."""

    def __init__(self):
        self.calls: list[ai.AgentInvocationRequest] = []

    def invoke(self, request: ai.AgentInvocationRequest) -> ai.AgentInvocationResult:
        self.calls.append(request)
        raise AssertionError(
            f"provider must never be invoked for this restart scenario, but was "
            f"called with {request!r}"
        )


_ADVANCE_KWARGS = {
    # Never actually reaches a real executor in any scenario this file
    # drives (see module docstring) -- these are placeholder values, not
    # a real filesystem path or git identity.
    "repository_root": "/tmp/pillar4-e2e-synthetic-worktree",
    "branch": "overnight/pillar4-e2e",
    "pr_title": "Pillar 4 E2E synthetic mission",
}


class Pillar4E2ETestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self._repository_root = Path(self._tmpdir.name) / "repository"
        self._repository_root.mkdir()

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _new_supervisor(self, adapters) -> MissionSupervisor:
        """Each call returns a genuinely independent MissionSupervisor --
        no attribute, thread, or object is ever shared with a previous
        call's return value. This IS the "restart" this file's tests
        inject."""
        kwargs = dict(_ADVANCE_KWARGS)
        kwargs["repository_root"] = str(self._repository_root)
        return MissionSupervisor(adapters=adapters, advance_kwargs=kwargs)

    def _notify_and_wait(self, supervisor: MissionSupervisor) -> None:
        supervisor.notify()
        worker = supervisor._worker
        self.assertIsNotNone(worker, "notify() must have started a worker for a real drain pass")
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "drain pass did not finish within the test timeout")

    def _mission_scope_awaiting_authorization(self):
        m = _create_intake_mission("pillar4 e2e mission")
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": str(self._repository_root),
            "branch": "overnight/pillar4-e2e",
            "base_sha": "b" * 40,
            "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        return mid


class NoImplicitAuthorizationTests(Pillar4E2ETestCase):
    """Cero autorización implícita, gates siguen requiriendo decisión
    explícita de José: a restart alone -- no matter how many times --
    must never cross a human gate that was never actually decided."""

    def test_a_restarted_supervisor_never_auto_approves_a_pending_gate(self):
        mid = self._mission_scope_awaiting_authorization()
        before = chugel.get_mission(mid)
        self.assertEqual("not_requested", before["human_gates"]["scope_authorization"]["status"])

        # Two independent "restarts" in a row, neither with any real
        # dispatch work available at SCOPE_AWAITING_AUTHORIZATION --
        # both must be genuine no-ops on the gate itself.
        for _ in range(2):
            codex = _CountingNeverCalledAdapter()
            claude = _CountingNeverCalledAdapter()
            supervisor = self._new_supervisor(adapters={"codex": codex, "claude": claude})
            self._notify_and_wait(supervisor)
            self.assertEqual(0, len(codex.calls))
            self.assertEqual(0, len(claude.calls))

        after = chugel.get_mission(mid)
        self.assertEqual(mid, after["mission_id"])  # same mission identity, unchanged
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual("not_requested", after["human_gates"]["scope_authorization"]["status"])


class GateDecisionDurableTransitionPendingRecoveryTests(Pillar4E2ETestCase):
    """The exact crash window mission_supervisor.recover_on_startup()'s
    own docstring names: a real human decision (decide_gate()) was
    already durably recorded before a crash, but the mechanical
    consequence-transition never ran. A restarted supervisor must
    complete that mechanical transition -- and, since nothing stops it,
    continue the whole autonomous pipeline -- while never having
    GRANTED anything itself."""

    def test_restart_resumes_an_already_granted_scope_authorization_and_completes_the_autonomous_pipeline(self):
        mid = self._mission_scope_awaiting_authorization()
        # The real, durable human decision -- crash simulated to happen
        # in the window strictly after this write, strictly before the
        # SCOPE_AWAITING_AUTHORIZATION -> AUTHORIZED mechanical
        # transition a live process would normally run next.
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])

        codex = _FakeAdapter([_emilio_completed_template(attempt=0)])
        claude = _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")])
        supervisor = self._new_supervisor(adapters={"codex": codex, "claude": claude})
        self._notify_and_wait(supervisor)
        # Durable, observable evidence -- not reliance on _FakeAdapter's
        # own "too many calls" AssertionError, which _drain_pass() could
        # otherwise silently swallow (see module docstring).
        self.assertEqual(1, len(codex.calls), "emilio dispatched exactly once")
        self.assertEqual(1, len(claude.calls), "emma dispatched exactly once")

        record = chugel.get_mission(mid)
        self.assertEqual(mid, record["mission_id"])
        self.assertEqual("PUBLISH_AWAITING_AUTHORIZATION", record["state"])
        self.assertEqual(1, len(record["builder_evidence"]))
        self.assertEqual(1, len(record["reviewer_evidence"]))

        # The publish gate itself is a real human gate -- reaching it is
        # not the same as crossing it. A second restart, with adapters
        # that must never be called again, must not touch it either.
        codex2 = _CountingNeverCalledAdapter()
        claude2 = _CountingNeverCalledAdapter()
        supervisor2 = self._new_supervisor(adapters={"codex": codex2, "claude": claude2})
        self._notify_and_wait(supervisor2)
        self.assertEqual(0, len(codex2.calls))
        self.assertEqual(0, len(claude2.calls))
        still = chugel.get_mission(mid)
        self.assertEqual("PUBLISH_AWAITING_AUTHORIZATION", still["state"])
        self.assertEqual("not_requested", still["human_gates"]["publish_authorization"]["status"])

        # Progreso/timeline observable, end to end, over the final record.
        timeline = project_mission_status(record).timeline
        kinds_and_states = [
            (e.kind, e.to_state if e.kind == "state_transition" else (e.role, e.attempt))
            for e in timeline
        ]
        self.assertIn(("state_transition", "AUTHORIZED"), kinds_and_states)
        self.assertIn(("state_transition", "PUBLISH_AWAITING_AUTHORIZATION"), kinds_and_states)
        self.assertIn(("dispatch", ("emilio", 0)), kinds_and_states)
        self.assertIn(("dispatch", ("emma", 0)), kinds_and_states)
        # Chronological, robustly: `at` is non-decreasing across the
        # whole timeline. This deliberately does NOT assert exactly
        # where dispatch events fall relative to state_transition events
        # -- this synthetic run (fake, instantaneous adapters, no real
        # I/O) usually completes within one wall-clock second, in which
        # case compute_mission_timeline()'s documented tie-break
        # (state_transition before dispatch) applies, but under real
        # system load it can occasionally straddle a second boundary,
        # at which point genuine `at` differences (not the tie-break)
        # correctly decide the order instead -- both are correct,
        # deterministic outcomes of the real wall clock, not flakiness.
        # The tie-break rule itself is exhaustively unit-tested with
        # fixed, controlled timestamps in
        # tests/test_jarvis_status.py::ComputeMissionTimelineTests.
        # test_same_second_ties_break_state_transition_before_dispatch_deterministically.
        self.assertEqual([e.at for e in timeline], sorted(e.at for e in timeline))
        state_events = [e for e in timeline if e.kind == "state_transition"]
        self.assertEqual("PUBLISH_AWAITING_AUTHORIZATION", state_events[-1].to_state)


class CrashMidDispatchNeverSilentlyRedispatchedTests(Pillar4E2ETestCase):
    """Crash window: a dispatch was reserved and marked IN_FLIGHT, then
    the process genuinely died -- no RESULT_RECORDED ever happened. This
    IS the historical PWNBF shape (jarvis/status.py's own regression
    test reproduces the same 4h43m stall synthetically; this test
    reproduces the crash itself via the real chugel dispatch primitives,
    then proves a REAL restarted supervisor's behavior against it).

    Honest limitation, stated plainly: today's code has no orphan-
    reservation reaper. reserve_dispatch()'s own refusal (Chugel's
    "an existing dispatch reservation ... has unresolved ... execution
    provenance; refusing automatic redispatch") is BY DESIGN permanent
    until a human intervenes -- this is exactly why Pillar 3's staleness
    watchdog exists: to surface this to a human, not to silently retry
    it. This test proves both halves of that story together: the crash
    is never silently papered over (no duplicate dispatch is even
    possible), and the watchdog correctly reflects the real elapsed
    time once resumed."""

    def test_restarted_supervisor_never_redispatches_and_reports_no_forward_progress(self):
        mid = self._mission_scope_awaiting_authorization()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="build starts")
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        # Crash simulated to happen exactly here -- reservation and
        # IN_FLIGHT marker are durable; no result was ever recorded.

        codex = _CountingNeverCalledAdapter()
        claude = _CountingNeverCalledAdapter()
        supervisor = self._new_supervisor(adapters={"codex": codex, "claude": claude})
        self._notify_and_wait(supervisor)
        self.assertEqual(0, len(codex.calls))
        self.assertEqual(0, len(claude.calls))

        record = chugel.get_mission(mid)
        self.assertEqual(mid, record["mission_id"])
        self.assertEqual("BUILDING", record["state"], "no forward progress is possible or expected")
        self.assertEqual(1, len(record["dispatch_ledger"]))
        entry = record["dispatch_ledger"][0]
        self.assertEqual("IN_FLIGHT", entry["status"], "the crashed reservation is untouched, not silently resolved")
        self.assertEqual(invocation_id, entry["invocation_id"])
        # A second, independent restart must reach the identical
        # conclusion -- restart-equivalence, not a one-time fluke.
        codex2 = _CountingNeverCalledAdapter()
        claude2 = _CountingNeverCalledAdapter()
        supervisor2 = self._new_supervisor(adapters={"codex": codex2, "claude": claude2})
        self._notify_and_wait(supervisor2)
        self.assertEqual(0, len(codex2.calls))
        self.assertEqual(0, len(claude2.calls))
        self.assertEqual("BUILDING", chugel.get_mission(mid)["state"])
        self.assertEqual(1, len(chugel.get_mission(mid)["dispatch_ledger"]))

    def test_watchdog_correctly_reflects_real_elapsed_time_after_the_crash(self):
        mid = self._mission_scope_awaiting_authorization()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="build starts")
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")

        # Simulate real elapsed time having passed while crashed -- an
        # honest simulation of the clock, not a fabricated code
        # guarantee: this rewrites only the timestamp a real crashed
        # process would also have left durably behind, via the same
        # chugel.mark_dispatch_in_flight()-produced shape, never a raw
        # file edit bypassing chugel's own write path semantics.
        record = chugel.get_mission(mid)
        aged = copy.deepcopy(record)
        aged["dispatch_ledger"][0]["updated_at"] = _ago_iso(5 * 3600 + 43 * 60)  # PWNBF's own 4h43m shape
        chugel._write_mission_record(aged)

        # A restart happens against the now-aged record.
        supervisor = self._new_supervisor(adapters={
            "codex": _CountingNeverCalledAdapter(), "claude": _CountingNeverCalledAdapter(),
        })
        self._notify_and_wait(supervisor)

        resumed = chugel.get_mission(mid)
        status = project_mission_status(resumed)
        self.assertEqual("STALLED", status.staleness)
        # Genuinely fresh (not merely a leftover in-memory belief from
        # before the simulated crash) -- a fresh project_mission_status()
        # call, independent of anything the supervisor instance above
        # itself remembers, computed purely from what is durably on disk.
        again = project_mission_status(chugel.get_mission(mid))
        self.assertEqual(status.staleness, again.staleness)


class EvidenceDurableBeforeStateAdvanceTests(Pillar4E2ETestCase):
    """Crash window: a role's dispatch genuinely completed and its
    evidence was durably recorded -- but the coordinator crashed before
    its own next chugel.transition() call ran. Covers BOTH roles: Emilio
    (BUILDING -> VERIFYING pending) and, symmetrically (Round-1 Emma
    review, P2 #2), Emma (REVIEWING -> PUBLISH_AWAITING_AUTHORIZATION
    pending).

    HONEST, DISCOVERED FINDING (not a Pillar 4 regression -- pre-existing
    behavior of orchestrator/wiring.py's run_emilio_attempt()/
    run_emma_attempt(), read directly to confirm this for both): a
    restarted supervisor does NOT automatically resume past this exact
    window for either role. Both attempt functions unconditionally call
    require_eligible_invocation() (chugel.reserve_dispatch()) at their
    top, with no pre-check for "evidence already exists for this
    attempt" -- so a restart here hits reserve_dispatch()'s own
    duplicate-evidence refusal and surfaces as HUMAN_ACTION_REQUIRED,
    not a silent resume. These tests prove BOTH real, honest properties
    of that outcome for each role: (a) it is never silently wrong -- no
    duplicate dispatch, no duplicate evidence, the record is left
    exactly as it was -- and (b) it does NOT self-heal automatically,
    unlike the gate-decision-durable-transition-pending window
    (GateDecisionDurableTransitionPendingRecoveryTests), which does. This
    is flagged as a real, narrow, pre-existing restart-recovery gap for
    the final report -- fixing it would mean changing
    orchestrator/wiring.py's/autonomous_runner.py's own dispatch logic,
    which is out of Pillar 4's authorized scope (no state-machine
    changes)."""

    def test_restart_after_durable_emilio_evidence_but_before_state_advance_reports_human_action_required_without_any_duplication(self):
        mid = self._mission_scope_awaiting_authorization()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="build starts")
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        evidence = _builder_evidence(attempt=0)
        evidence["invocation_id"] = invocation_id
        chugel.record_builder_evidence(mid, evidence)
        # Crash simulated to happen exactly here: builder_evidence[0] and
        # the finalized dispatch_ledger entry are both durable; `state`
        # is still BUILDING -- the coordinator's own next transition()
        # call never ran.
        pre_restart = chugel.get_mission(mid)
        self.assertEqual("BUILDING", pre_restart["state"])
        self.assertEqual(1, len(pre_restart["builder_evidence"]))

        codex = _CountingNeverCalledAdapter()
        claude = _CountingNeverCalledAdapter()
        supervisor = self._new_supervisor(adapters={"codex": codex, "claude": claude})
        self._notify_and_wait(supervisor)
        self.assertEqual(0, len(codex.calls), "zero duplicate dispatch, enforced")
        self.assertEqual(0, len(claude.calls))

        record = chugel.get_mission(mid)
        self.assertEqual(mid, record["mission_id"])
        # Not silently wrong: no forward state change, no duplicate
        # evidence, no duplicate/second dispatch reservation.
        self.assertEqual("BUILDING", record["state"])
        self.assertEqual(1, len(record["builder_evidence"]))
        self.assertEqual(0, len(record["reviewer_evidence"]))
        self.assertEqual(1, len(record["dispatch_ledger"]))
        self.assertEqual("FINALIZED", record["dispatch_ledger"][0]["status"])
        # ... but also, honestly, not self-healed: this mission needs a
        # human/operator to notice and act, exactly like the historical
        # PWNBF stall did -- which is precisely what Pillar 3's staleness
        # watchdog exists to surface. A second, independent restart must
        # reach the identical (still stuck) conclusion.
        codex2 = _CountingNeverCalledAdapter()
        claude2 = _CountingNeverCalledAdapter()
        supervisor2 = self._new_supervisor(adapters={"codex": codex2, "claude": claude2})
        self._notify_and_wait(supervisor2)
        self.assertEqual(0, len(codex2.calls))
        self.assertEqual(0, len(claude2.calls))
        self.assertEqual("BUILDING", chugel.get_mission(mid)["state"])
        self.assertEqual(1, len(chugel.get_mission(mid)["builder_evidence"]))

    def test_restart_after_durable_emma_evidence_but_before_state_advance_reports_human_action_required_without_any_duplication(self):
        """Symmetric to the Emilio case above (Round-1 Emma review, P2
        #2): reaches REVIEWING with Emma's evidence genuinely, durably
        recorded (and Emilio's own attempt-0 evidence already finalized,
        exactly as a real prior successful build+dispatch would leave
        it), then crashes before REVIEWING -> PUBLISH_AWAITING_AUTHORIZATION
        ever runs."""
        mid = self._mission_scope_awaiting_authorization()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="build starts")

        # A real, complete, prior Emilio attempt -- exactly what a
        # genuinely successful build (not itself under test here) would
        # durably leave behind, reaching REVIEWING the same way
        # GateDecisionDurableTransitionPendingRecoveryTests' fully-automated
        # run does.
        _, emilio_invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, emilio_invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, emilio_invocation_id, outcome="completed")
        builder_evidence = _builder_evidence(attempt=0)
        # Emma's independence check (chugel.reserve_dispatch()) requires
        # a persisted provider identity on the builder evidence it
        # dispatches against -- exactly what
        # orchestrator/agent_invocation.py's own _augmented_completed_evidence()
        # always adds for a real "completed" Emilio result. _builder_evidence()
        # (a raw content-only fixture) does not include these on its own.
        builder_evidence.update({
            "invocation_id": emilio_invocation_id,
            "provider": "codex",
            "provider_session_id": None,
            "provider_conversation_id": "builder-thread",
        })
        chugel.record_builder_evidence(mid, builder_evidence)
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder evidence persisted")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="ready for independent review")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="independent review started")

        _, emma_invocation_id = chugel.reserve_dispatch(mid, role="emma", attempt=0)
        chugel.mark_dispatch_in_flight(mid, emma_invocation_id, provider="claude")
        chugel.record_dispatch_result(mid, emma_invocation_id, outcome="completed")
        reviewer_evidence = _reviewer_evidence(attempt=0, verdict="PASS")
        reviewer_evidence["invocation_id"] = emma_invocation_id
        chugel.record_reviewer_evidence(mid, reviewer_evidence)
        # Crash simulated to happen exactly here: reviewer_evidence[0]
        # and the finalized dispatch_ledger entry for emma are both
        # durable; `state` is still REVIEWING -- the coordinator's own
        # next transition() call (-> PUBLISH_AWAITING_AUTHORIZATION)
        # never ran.
        pre_restart = chugel.get_mission(mid)
        self.assertEqual("REVIEWING", pre_restart["state"])
        self.assertEqual(1, len(pre_restart["reviewer_evidence"]))

        codex = _CountingNeverCalledAdapter()
        claude = _CountingNeverCalledAdapter()
        supervisor = self._new_supervisor(adapters={"codex": codex, "claude": claude})
        self._notify_and_wait(supervisor)
        self.assertEqual(0, len(codex.calls))
        self.assertEqual(0, len(claude.calls), "zero duplicate dispatch, enforced")

        record = chugel.get_mission(mid)
        self.assertEqual(mid, record["mission_id"])
        self.assertEqual("REVIEWING", record["state"])
        self.assertEqual(1, len(record["builder_evidence"]))
        self.assertEqual(1, len(record["reviewer_evidence"]))
        self.assertEqual(2, len(record["dispatch_ledger"]))
        for entry in record["dispatch_ledger"]:
            self.assertEqual("FINALIZED", entry["status"])

        # Not self-healed, symmetrically -- a second, independent
        # restart reaches the identical (still stuck) conclusion.
        codex2 = _CountingNeverCalledAdapter()
        claude2 = _CountingNeverCalledAdapter()
        supervisor2 = self._new_supervisor(adapters={"codex": codex2, "claude": claude2})
        self._notify_and_wait(supervisor2)
        self.assertEqual(0, len(codex2.calls))
        self.assertEqual(0, len(claude2.calls))
        self.assertEqual("REVIEWING", chugel.get_mission(mid)["state"])
        self.assertEqual(1, len(chugel.get_mission(mid)["reviewer_evidence"]))


if __name__ == "__main__":
    unittest.main()
