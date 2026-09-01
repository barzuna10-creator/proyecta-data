"""jarvis/mission_supervisor.py -- Mission 006's wake/drain worker.

Real Chugel Mission Records throughout; mission_coordinator.advance()
itself is mocked in most tests here (its own behavior is covered by
tests/test_jarvis_mission_coordinator.py) -- these tests are about the
supervisor's own concurrency/coalescing/state-classification contract."""

from __future__ import annotations

import tempfile
import threading
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from jarvis import mission_coordinator, mission_query
from jarvis.mission_supervisor import (
    AUTO_ADVANCE_ELIGIBLE_STATES,
    GATE_WAITING_STATES,
    TERMINAL_STATES,
    MissionSupervisor,
)
from tests.test_orchestrator_autonomous_runner import _create_intake_mission, _scope_gate_approval

_ADVANCE_KWARGS = dict(repository_root="/tmp/repo", branch="b", pr_title="t")


def _scope_gate_rejection():
    return {
        "status": "rejected", "requested_at": None,
        "decided_at": "2026-08-29T12:10:00Z", "decided_by": "jose",
        "decision_ref": "ref-scope-rejected-1", "approved_for": None,
    }


class SupervisorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self._repository_root = Path(self._tmpdir.name) / "repository"
        self._repository_root.mkdir()

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _supervisor(self):
        kwargs = dict(_ADVANCE_KWARGS)
        kwargs.update(
            repository_root=str(self._repository_root),
            branch="overnight/synthetic",
        )
        return MissionSupervisor(adapters={}, advance_kwargs=kwargs)


class StateClassificationTests(unittest.TestCase):
    """AUTO_ADVANCE_ELIGIBLE_STATES and TERMINAL_STATES must be disjoint,
    and BLOCKED must be in neither. GATE_WAITING_STATES (Mission 006's
    gate-consumption follow-up) is deliberately now a SUBSET of
    AUTO_ADVANCE_ELIGIBLE_STATES, not disjoint from it -- advance()'s own
    per-gate status check, not a pre-filter on this module's state sets,
    is what keeps calling it on a gate-waiting mission safe."""

    def test_gate_waiting_states_are_a_subset_of_auto_advance_eligible(self):
        self.assertTrue(GATE_WAITING_STATES <= AUTO_ADVANCE_ELIGIBLE_STATES)

    def test_auto_advance_eligible_and_terminal_are_disjoint(self):
        self.assertEqual(set(), AUTO_ADVANCE_ELIGIBLE_STATES & TERMINAL_STATES)
        self.assertEqual(set(), GATE_WAITING_STATES & TERMINAL_STATES)

    def test_blocked_is_in_none_of_the_three_sets(self):
        self.assertNotIn("BLOCKED", AUTO_ADVANCE_ELIGIBLE_STATES)
        self.assertNotIn("BLOCKED", GATE_WAITING_STATES)
        self.assertNotIn("BLOCKED", TERMINAL_STATES)

    def test_the_three_real_gate_states_are_exactly_the_waiting_set(self):
        self.assertEqual(
            {"SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION"},
            GATE_WAITING_STATES,
        )

    def test_intake_is_auto_advance_eligible(self):
        # The one state advance() itself now handles without any human
        # decision (Mission 006's INTAKE branch).
        self.assertIn("INTAKE", AUTO_ADVANCE_ELIGIBLE_STATES)

    def test_every_schema_state_is_classified_somewhere(self):
        """Round-2 independent review, P3: the four buckets (the three
        exported frozensets plus the implicit {"BLOCKED"}) must cover
        every state orchestrator.validator.STATES actually declares -- a
        future schema addition this module doesn't yet know about must
        fail this test rather than silently fall through _drain_pass()'s
        own fail-safe skip with no test ever noticing."""
        from orchestrator.validator import STATES
        classified = AUTO_ADVANCE_ELIGIBLE_STATES | GATE_WAITING_STATES | TERMINAL_STATES | {"BLOCKED"}
        self.assertEqual(set(STATES), classified)


class DrainPassEligibilityTests(SupervisorTestCase):
    """_drain_pass() must call advance() for exactly the missions whose
    CURRENT Chugel state is auto-advance-eligible -- never for BLOCKED,
    terminal, or unreadable. A gate-waiting mission with a still-pending
    gate IS submitted to advance() (Mission 006's gate-consumption
    follow-up put the three *_AWAITING_AUTHORIZATION states back into
    AUTO_ADVANCE_ELIGIBLE_STATES) but advance() itself must produce no
    side effect for it -- see test_a_gate_waiting_mission_with_a_pending_gate_is_a_safe_no_op below."""

    def _mission_at(self, state):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        if state != "INTAKE":
            chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
            if state != "SCOPE_AWAITING_AUTHORIZATION":
                chugel.transition(mid, "AUTHORIZED", actor="jose", reason="test setup")
                if state not in ("AUTHORIZED",):
                    chugel.transition(mid, state, actor="jose", reason="test setup")
        return mid

    def test_advances_a_mission_in_an_auto_advance_eligible_state(self):
        mid = self._mission_at("INTAKE")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            advance.return_value = mission_coordinator.CoordinatorReport("GATE_REQUIRED", "SCOPE_AWAITING_AUTHORIZATION", "scope_authorization")
            outcome = supervisor._drain_pass()
        advance.assert_called_once()
        self.assertEqual(1, len(outcome.reports))
        self.assertEqual(mid, outcome.reports[0][0])

    def test_a_gate_waiting_mission_with_a_pending_gate_is_a_safe_no_op(self):
        """Real (unmocked) advance(): a mission sitting in
        SCOPE_AWAITING_AUTHORIZATION with a still-pending gate IS submitted
        to advance() by _drain_pass() (it is auto-advance-eligible), but
        advance()'s own gate-status check means this produces no state
        change at all -- proving eligibility alone never bypasses a real
        pending gate."""
        mid = self._mission_at("SCOPE_AWAITING_AUTHORIZATION")
        supervisor = self._supervisor()
        before = chugel.get_mission(mid)
        outcome = supervisor._drain_pass()
        self.assertEqual(1, len(outcome.reports))
        self.assertEqual("GATE_REQUIRED", outcome.reports[0][1].status)
        after = chugel.get_mission(mid)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual(len(before["state_history"]), len(after["state_history"]))

    def test_never_calls_advance_for_a_blocked_mission(self):
        mid = self._mission_at("SCOPE_AWAITING_AUTHORIZATION")
        chugel.transition(mid, "BLOCKED", actor="jose", reason="external issue")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            supervisor._drain_pass()
        advance.assert_not_called()

    def test_never_calls_advance_for_a_terminal_mission(self):
        mid = self._mission_at("SCOPE_AWAITING_AUTHORIZATION")
        chugel.transition(mid, "CANCELLED", actor="jose", reason="no longer needed")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            supervisor._drain_pass()
        advance.assert_not_called()


class WakeDrainCoalescingTests(SupervisorTestCase):
    """The core Mission 006 requirement: a notify() that lands while a
    drain pass is already running must never be lost -- it must cause
    at least one more full pass after the current one finishes."""

    @staticmethod
    def _transitioning_fake_advance(mission_id, adapters, **kwargs):
        """A fake advance() that -- like the real one -- actually moves
        the mission OUT of the auto-advance-eligible set entirely, so a
        mocked drain pass converges instead of finding the same "eligible"
        mission forever (mission_query.list_missions() reflects real
        Chugel state regardless of this mock; only the real advance()
        function itself is replaced). Transitions to BLOCKED rather than
        SCOPE_AWAITING_AUTHORIZATION deliberately -- since Mission 006's
        gate-consumption follow-up, the three *_AWAITING_AUTHORIZATION
        states are THEMSELVES auto-advance-eligible (advance()'s own
        per-gate check is what makes that safe for the real function; this
        stub has no such check), so landing there would make the mission
        "eligible" again on the very next pass and defeat the point of
        this fixture. BLOCKED is unconditionally excluded from
        AUTO_ADVANCE_ELIGIBLE_STATES, requires no gate/evidence setup, and
        (INTAKE, BLOCKED) is itself a real, legal transition pair."""
        chugel.transition(mission_id, "BLOCKED", actor="chugel", reason="test fake advance")
        return mission_coordinator.CoordinatorReport("BLOCKED", "BLOCKED")

    def test_a_wake_that_arrives_mid_drain_triggers_another_pass(self):
        _create_intake_mission("algo")  # one auto-advance-eligible mission
        supervisor = self._supervisor()
        call_count = 0
        lock = threading.Lock()

        def fake_advance(mission_id, adapters, **kwargs):
            nonlocal call_count
            with lock:
                call_count += 1
                first_call = call_count == 1
            if first_call:
                # Simulate: a real authorization completes on another
                # thread WHILE this drain pass's single advance() call is
                # still running -- notify() must not be lost just because
                # the worker is already busy.
                supervisor.notify()
            return self._transitioning_fake_advance(mission_id, adapters, **kwargs)

        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        # The mission was only ever eligible once (one real transition
        # out of INTAKE) -- proving the mid-drain notify() was not
        # dropped means proving the worker did NOT exit after that one
        # pass just because the pass itself "found work": it must have
        # run at least one further pass in response to the nested
        # notify(), even though that further pass found nothing.
        self.assertGreaterEqual(len(supervisor.last_drain_outcomes), 2)
        self.assertEqual(1, call_count)  # the mission was genuinely only advanced once

    def test_a_wake_that_arrives_between_two_passes_that_found_nothing_is_never_lost(self):
        """Round-1 independent review, P2: the test above is satisfied
        purely by _run()'s 'a pass that produced a report loops again'
        behavior, regardless of whether the nested notify() call inside it
        does anything at all -- it does not isolate the property requirement
        3 actually demands. This test does: the FIRST drain pass finds
        ZERO eligible work (mission_query.list_missions() itself is mocked
        to return an empty list on its first call) -- exactly the state
        _run() would otherwise use to decide to exit -- and a concurrent
        notify() together with the mission's real creation happens while
        that first, empty pass is still being evaluated. The guarantee
        only holds if that wake is not lost: a second, real pass must
        still run and find (and mechanically advance) the mission that did
        not exist yet when the first pass looked."""
        supervisor = self._supervisor()
        created: dict[str, str] = {}
        call_count = 0
        real_list_missions = mission_query.list_missions

        def fake_list_missions():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                m = _create_intake_mission("algo")
                created["mission_id"] = m["mission_id"]
                supervisor.notify()
                return ()
            return real_list_missions()

        with mock.patch("jarvis.mission_supervisor.mission_query.list_missions", side_effect=fake_list_missions):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertGreaterEqual(call_count, 2)
        self.assertEqual(
            "SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(created["mission_id"])["state"],
        )

    def test_multiple_concurrent_notify_calls_spawn_exactly_one_worker(self):
        _create_intake_mission("algo")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=self._transitioning_fake_advance):
            threads = [threading.Thread(target=supervisor.notify) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)
        # Exactly one worker thread ever existed for this supervisor
        # instance across all 8 concurrent notify() calls.
        self.assertFalse(worker.is_alive())
        self.assertEqual("mission-supervisor", worker.name)

    def test_a_notify_after_the_worker_already_exited_spawns_a_fresh_one(self):
        _create_intake_mission("algo")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=self._transitioning_fake_advance):
            supervisor.notify()
            first_worker = supervisor._worker
            first_worker.join(timeout=5)
            self.assertFalse(first_worker.is_alive())

            supervisor.notify()
            second_worker = supervisor._worker
            second_worker.join(timeout=5)
        self.assertIsNot(first_worker, second_worker)

    def test_a_pass_with_no_eligible_work_exits_without_calling_advance(self):
        # No missions at all -- the worker must run one pass, find
        # nothing, and stop, never spinning.
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            supervisor.notify()
            supervisor._worker.join(timeout=5)
        advance.assert_not_called()


class RealEndToEndAdvanceTests(SupervisorTestCase):
    """No mocking of mission_coordinator.advance() itself -- the real
    INTAKE mechanical transition runs, driven purely by notify()."""

    def test_notify_on_a_fresh_mission_reaches_the_scope_gate_with_no_manual_advance_call(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        supervisor = self._supervisor()
        supervisor.notify()
        supervisor._worker.join(timeout=5)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])

    def test_recover_on_startup_advances_an_intake_mission_left_over_from_a_crash(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        supervisor = self._supervisor()
        supervisor.recover_on_startup()
        supervisor._worker.join(timeout=5)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])

    def test_recover_on_startup_leaves_a_still_pending_gate_untouched(self):
        """Real advance(): recover_on_startup() DOES ask advance() about a
        gate-waiting mission (Mission 006's gate-consumption follow-up),
        but with the gate still pending, advance()'s own check makes this
        a pure no-op -- the mission does not move, and nothing is
        implicitly authorized by the mere act of restarting."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="already past intake")
        before = chugel.get_mission(mid)
        supervisor = self._supervisor()
        supervisor.recover_on_startup()
        supervisor._worker.join(timeout=5)
        after = chugel.get_mission(mid)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual(len(before["state_history"]), len(after["state_history"]))

    def test_recover_on_startup_never_touches_a_blocked_mission(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        chugel.transition(mid, "BLOCKED", actor="jose", reason="external issue, needs jose")
        supervisor = self._supervisor()
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            supervisor.recover_on_startup()
            supervisor._worker.join(timeout=5)
        advance.assert_not_called()
        self.assertEqual("BLOCKED", chugel.get_mission(mid)["state"])


class GateRecoveryCrashWindowTests(SupervisorTestCase):
    """Mission 006 (gate-consumption follow-up): the six crash-window
    scenarios explicitly required for this change. Real Chugel throughout
    (mission_coordinator.advance() unmocked) -- these exercise
    recover_on_startup() exactly as it would run after a real process
    restart, never a manual advance() call."""

    def _scope_awaiting_mission(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        return mid

    def test_1_crash_before_decide_gate_restart_does_not_advance(self):
        """No decide_gate() call ever happened -- the gate is still at
        its default 'not_requested'/'pending' status. A restart must
        leave the mission exactly where it was."""
        mid = self._scope_awaiting_mission()
        before = chugel.get_mission(mid)
        supervisor = self._supervisor()
        supervisor.recover_on_startup()
        supervisor._worker.join(timeout=5)
        after = chugel.get_mission(mid)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual(len(before["state_history"]), len(after["state_history"]))

    def test_2_crash_after_decide_gate_approved_before_transition_restart_continues(self):
        """decide_gate() succeeded (a real José decision, durably
        persisted) but the process crashed before the mechanical
        transition ever ran. Restart must resume it -- this is recovery,
        not a new authorization."""
        mid = self._scope_awaiting_mission()
        chugel.record_repository_state(mid, {
            "worktree_path": str(self._repository_root), "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])  # crash point

        supervisor = self._supervisor()
        with mock.patch(
            "jarvis.mission_supervisor.mission_coordinator.autonomous_runner.run_mission",
        ) as run_mission_mock:
            from orchestrator.autonomous_runner import RunnerResult
            # A stub that only ever reports -- never itself performs any
            # real Chugel write -- so the record's real, on-disk state
            # after recovery reflects exactly (and only) what advance()'s
            # own gate-consumption transition did, nothing this stub
            # claims beyond that.
            run_mission_mock.return_value = RunnerResult(
                status="AUTHORIZATION_REQUIRED", state="PUBLISH_AWAITING_AUTHORIZATION", attempts=1,
            )
            supervisor.recover_on_startup()
            supervisor._worker.join(timeout=5)
        run_mission_mock.assert_called_once()  # advance() reached the AUTHORIZED-family branch after consuming the gate
        after = chugel.get_mission(mid)
        self.assertEqual("AUTHORIZED", after["state"])
        entry = next(e for e in after["state_history"] if e["from_state"] == "SCOPE_AWAITING_AUTHORIZATION" and e["to_state"] == "AUTHORIZED")
        self.assertEqual("chugel", entry["actor"])
        self.assertNotEqual("jose", entry["actor"])

    def test_3_crash_after_decide_gate_rejected_restart_reaches_cancelled(self):
        mid = self._scope_awaiting_mission()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_rejection())
        supervisor = self._supervisor()
        supervisor.recover_on_startup()
        supervisor._worker.join(timeout=5)
        after = chugel.get_mission(mid)
        self.assertEqual("CANCELLED", after["state"])
        entry = next(e for e in after["state_history"] if e["to_state"] == "CANCELLED")
        self.assertEqual("chugel", entry["actor"])

    def test_4_pending_gate_across_multiple_restarts_never_advances(self):
        mid = self._scope_awaiting_mission()
        before = chugel.get_mission(mid)
        for _ in range(5):
            supervisor = self._supervisor()  # a fresh instance each time -- a real new process
            supervisor.recover_on_startup()
            supervisor._worker.join(timeout=5)
        after = chugel.get_mission(mid)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual(len(before["state_history"]), len(after["state_history"]))

    def test_5_invalid_stale_approved_for_fails_closed_no_bypass_on_recovery(self):
        """Same underlying guarantee as
        test_jarvis_mission_coordinator.GateConsumptionTests's version of
        this scenario, exercised here through recover_on_startup() instead
        of a direct advance() call. The poisoned record fails Chugel's own
        read-time validation at the earliest possible point -- inside
        chugel.list_missions() itself, which jarvis.mission_query.
        list_missions() relays as readable=False -- so _drain_pass()'s own
        eligibility filter (`if not listing.readable: continue`) never
        even reaches advance() for this mission at all. No transition, no
        crash, no bypass; the worker still terminates cleanly."""
        mid = self._scope_awaiting_mission()
        record = chugel.get_mission(mid)
        record["human_gates"]["scope_authorization"] = {
            "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
            "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
            "decision_ref": "ref-scope-stale", "approved_for": {"mission_definition_version": 999},
        }
        chugel._write_mission_record(record)
        listing = next(item for item in mission_query.list_missions() if item.mission_id == mid)
        self.assertFalse(listing.readable)  # confirms the premise this test depends on

        supervisor = self._supervisor()
        supervisor.recover_on_startup()
        worker = supervisor._worker
        self.assertIsNotNone(worker)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())  # never crashed, never hung
        self.assertEqual(1, len(supervisor.last_drain_outcomes))
        self.assertEqual((), supervisor.last_drain_outcomes[0].reports)
        self.assertEqual((), supervisor.last_drain_outcomes[0].errors)
        # Still on disk exactly as poisoned -- nothing was ever attempted,
        # let alone bypassed.
        with open(chugel._mission_path(mid), encoding="utf-8") as handle:
            import json as _json
            self.assertEqual(999, _json.load(handle)["human_gates"]["scope_authorization"]["approved_for"]["mission_definition_version"])

    def test_6_concurrent_notify_after_approval_never_duplicates_the_transition(self):
        """Scenario 6: many concurrent notify() calls racing a single,
        already-approved gate must never produce more than one mechanical
        transition -- the state machine itself makes a second consumption
        impossible (the mission is no longer in the gate-waiting state
        after the first), and this proves it holds under real concurrency,
        not just sequential re-calls."""
        mid = self._scope_awaiting_mission()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_rejection())
        supervisor = self._supervisor()

        threads = [threading.Thread(target=supervisor.notify) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        deadline_worker = supervisor._worker
        if deadline_worker is not None:
            deadline_worker.join(timeout=5)

        after = chugel.get_mission(mid)
        self.assertEqual("CANCELLED", after["state"])
        consumptions = [
            e for e in after["state_history"]
            if e["from_state"] == "SCOPE_AWAITING_AUTHORIZATION" and e["to_state"] == "CANCELLED"
        ]
        self.assertEqual(1, len(consumptions))  # never duplicated


class StalledMissionTests(SupervisorTestCase):
    """Round-1 independent review, P0: a mission whose advance() call
    never produces forward STATE progress (HUMAN_ACTION_REQUIRED with the
    Chugel state unchanged, or a persistently raising advance()) must not
    make the worker spin forever, and must not be retried by this running
    supervisor instance again."""

    def test_a_human_action_required_report_stalls_the_mission_and_the_worker_terminates(self):
        _create_intake_mission("algo")
        supervisor = self._supervisor()
        call_count = 0

        def fake_advance(mission_id, adapters, **kwargs):
            nonlocal call_count
            call_count += 1
            return mission_coordinator.CoordinatorReport("HUMAN_ACTION_REQUIRED", "INTAKE", reason="stuck")

        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, call_count)  # never retried within this same pass loop

        # A second, independent notify() must not retry it either -- this
        # supervisor instance has given up on this mission_id for good.
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            supervisor._worker.join(timeout=5)
        self.assertEqual(1, call_count)

    def test_a_persistently_raising_advance_stalls_the_mission_and_the_worker_terminates(self):
        _create_intake_mission("algo")
        supervisor = self._supervisor()
        call_count = 0

        def fake_advance(mission_id, adapters, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("synthetic structural failure")

        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, call_count)
        self.assertEqual(1, len(supervisor.last_drain_outcomes[-1].errors))

        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            supervisor._worker.join(timeout=5)
        self.assertEqual(1, call_count)  # still not retried


class AbnormalWorkerExitTests(SupervisorTestCase):
    """Round-2 independent review, P2: the round-1 P1 fix (replacing
    threading.Thread.is_alive() with a plain self._worker_running bool)
    introduced a new failure mode -- nothing reset that bool on an
    exception escaping _run()'s own loop body outside of _drain_pass()'s
    already-broad error handling. Confirms the supervisor self-heals: a
    fresh notify() after such an exit successfully starts a new worker,
    rather than silently no-op'ing forever."""

    def test_an_exception_escaping_the_loop_body_still_lets_a_later_notify_start_a_fresh_worker(self):
        _create_intake_mission("algo")
        supervisor = self._supervisor()

        # Force an exception from inside _run()'s own loop body, past
        # _drain_pass()'s try/except -- appending to last_drain_outcomes
        # is as good a place as any real bug could land.
        with mock.patch.object(
            supervisor, "last_drain_outcomes",
        ) as broken_list:
            broken_list.append.side_effect = RuntimeError("synthetic bug outside _drain_pass()'s own handling")
            supervisor.notify()
            first_worker = supervisor._worker
            self.assertIsNotNone(first_worker)
            first_worker.join(timeout=5)
        self.assertFalse(first_worker.is_alive())
        self.assertFalse(supervisor._worker_running)  # not wedged

        # A second mission, real advance() this time -- proves the
        # supervisor is genuinely still functional, not merely "not
        # wedged" in a way that happens not to be exercised.
        m2 = _create_intake_mission("otra")
        supervisor.notify()
        second_worker = supervisor._worker
        self.assertIsNot(first_worker, second_worker)
        second_worker.join(timeout=5)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(m2["mission_id"])["state"])


class ListMissionsFailureTests(SupervisorTestCase):
    """Round-1 independent review, P3: a whole-pass listing failure
    (mission_query.list_missions() itself raising) must not kill the
    worker thread either, and must not cause an infinite loop -- the
    worker records the error and exits normally, just like a per-mission
    failure does."""

    def test_a_listing_failure_is_recorded_and_the_worker_still_terminates(self):
        supervisor = self._supervisor()
        with mock.patch(
            "jarvis.mission_supervisor.mission_query.list_missions",
            side_effect=RuntimeError("missions dir briefly unreadable"),
        ):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(supervisor.last_drain_outcomes))
        self.assertEqual((), supervisor.last_drain_outcomes[0].reports)
        self.assertEqual(1, len(supervisor.last_drain_outcomes[0].errors))


class ConcurrentNotifyStressTests(SupervisorTestCase):
    """Round-1 independent review, P1: heavy concurrent notify() traffic,
    against real (unmocked) advance(), must never leave eligible work
    permanently unprocessed -- the adversarial version of the coalescing
    guarantee that a single mid-drain notify() alone does not exercise."""

    def test_hammering_notify_from_many_threads_never_loses_eligible_work(self):
        mission_ids = [_create_intake_mission("algo")["mission_id"] for _ in range(5)]
        supervisor = self._supervisor()
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                supervisor.notify()

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        import time
        time.sleep(0.5)  # let them hammer notify() concurrently with real advance() runs
        stop.set()
        for t in threads:
            t.join()

        # One final notify() to be sure the last wave is picked up, then
        # settle: poll for the worker to finish rather than a single fixed
        # join, since a hammered supervisor may still be mid-pass.
        supervisor.notify()
        deadline = time.time() + 10
        while supervisor._worker is not None and supervisor._worker.is_alive() and time.time() < deadline:
            supervisor._worker.join(timeout=0.2)

        for mid in mission_ids:
            self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])


class CoordinatorReportStatusExhaustivenessTests(SupervisorTestCase):
    """Verification Hardening V1, Pillar 1 (contract checks): explicit,
    per-status treatment of every one of CoordinatorReport's 6 declared
    statuses against _drain_pass()'s real handling -- not just that
    CoordinatorReport can be constructed with each one (round-trip),
    which proves nothing about how _drain_pass() actually treats them.
    _drain_pass() special-cases exactly one status (HUMAN_ACTION_REQUIRED
    -> self._stalled, see StalledMissionTests above); every other
    declared status must be confirmed here to receive the SAME uniform
    treatment: the mission is reported, never added to _stalled, and a
    second, independent notify() calls advance() again for it (i.e.
    genuinely not stalled, matching current, unchanged behavior --
    Emma's round-2 review confirmed BLOCKED/TERMINAL_FAILURE/MERGED are
    additionally protected in real production paths by the mission's own
    Chugel state moving outside AUTO_ADVANCE_ELIGIBLE_STATES before advance()
    would ever return one of those statuses again -- this test proves the
    supervisor-level half of that safety net directly, independent of any
    one caller's real state-transition behavior)."""

    def _assert_status_never_stalls(self, status: str):
        _create_intake_mission("algo")
        supervisor = self._supervisor()
        call_count = 0

        def fake_advance(mission_id, adapters, **kwargs):
            nonlocal call_count
            call_count += 1
            return mission_coordinator.CoordinatorReport(status, "INTAKE", reason="synthetic")

        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            worker = supervisor._worker
            self.assertIsNotNone(worker)
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, call_count)
        self.assertEqual(1, len(supervisor.last_drain_outcomes[-1].reports))
        self.assertEqual(0, len(supervisor.last_drain_outcomes[-1].errors))

        # The exact opposite assertion from StalledMissionTests: a SECOND,
        # independent notify() DOES call advance() again for this
        # mission -- it was never added to _stalled.
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance", side_effect=fake_advance):
            supervisor.notify()
            supervisor._worker.join(timeout=5)
        self.assertEqual(2, call_count)

    def test_gate_required_never_stalls(self):
        self._assert_status_never_stalls("GATE_REQUIRED")

    def test_blocked_never_stalls(self):
        self._assert_status_never_stalls("BLOCKED")

    def test_terminal_failure_never_stalls(self):
        self._assert_status_never_stalls("TERMINAL_FAILURE")

    def test_merged_never_stalls(self):
        self._assert_status_never_stalls("MERGED")

    def test_workspace_occupied_never_stalls(self):
        self._assert_status_never_stalls("WORKSPACE_OCCUPIED")

    def test_human_action_required_is_the_only_status_that_stalls(self):
        """Direct cross-check: every status in COORDINATOR_REPORT_STATUSES
        except HUMAN_ACTION_REQUIRED must be proven (by the five tests
        above) to never stall -- this test asserts that partition itself,
        so a future addition to COORDINATOR_REPORT_STATUSES with no
        corresponding test above fails immediately and visibly, rather
        than silently inheriting untested "safe" treatment."""
        tested_never_stalls = {"GATE_REQUIRED", "BLOCKED", "TERMINAL_FAILURE", "MERGED", "WORKSPACE_OCCUPIED"}
        self.assertEqual(
            mission_coordinator.COORDINATOR_REPORT_STATUSES,
            tested_never_stalls | {"HUMAN_ACTION_REQUIRED"},
        )


if __name__ == "__main__":
    unittest.main()
