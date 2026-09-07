"""Focused tests for jarvis/mission_supervisor.py's M5 (Learning &
Knowledge Continuity) housekeeping pass: _knowledge_derivation_housekeeping_
pass(), its backoff, and its process-local done-cache. Uses synthetic
MissionListing rows and mocks jarvis.mission_coordinator's own two seam
functions (knowledge_derivation_status/derive_knowledge_for_completed_mission)
directly -- this module's own contract is "when to ask", never "what to
do", so its tests never need a real Chugel record."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from jarvis import mission_coordinator, mission_query, mission_supervisor
from jarvis.mission_supervisor import MissionSupervisor, RETRY_BACKOFF_SECONDS


def _listing(mission_id, state="COMPLETED"):
    return mission_query.MissionListing(
        mission_id=mission_id, readable=True, state=state, bucket=None, updated_at="2026-09-01T00:00:00Z",
        error_code=None,
    )


def _derivation(status, last_attempted_at=None):
    return {
        "status": status, "attempt_count": 0, "last_attempted_at": last_attempted_at,
        "last_error": None, "completed_at": None, "candidate_ids": [],
    }


class HousekeepingPassTestCase(unittest.TestCase):
    def setUp(self):
        self.supervisor = MissionSupervisor(adapters={}, advance_kwargs={"repository_root": "/tmp", "branch": "b", "pr_title": "t"})

    def tearDown(self):
        self.supervisor.close()

    def _wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


class DispatchGatingTests(HousekeepingPassTestCase):
    def test_ignores_non_completed_missions(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status") as status_mock:
            self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1", state="BUILDING")])
        status_mock.assert_not_called()

    def test_skips_missions_already_in_done_cache(self):
        self.supervisor._knowledge_derivation_done_cache.add("m1")
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status") as status_mock:
            self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
        status_mock.assert_not_called()

    def test_dispatches_for_pending_completed_mission(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("pending")):
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                self.assertTrue(self._wait_for(lambda: derive_mock.call_count == 1))
        derive_mock.assert_called_once_with("m1")

    def test_completed_status_populates_done_cache_and_never_dispatches(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("completed")):
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
        derive_mock.assert_not_called()
        self.assertIn("m1", self.supervisor._knowledge_derivation_done_cache)

    def test_stalled_status_never_dispatches_automatically(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("stalled")):
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                time.sleep(0.05)
        derive_mock.assert_not_called()
        self.assertNotIn("m1", self.supervisor._knowledge_derivation_done_cache)

    def test_a_record_read_failure_never_crashes_the_pass(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", side_effect=RuntimeError("gone")):
            self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1"), _listing("m2")])  # must not raise


class BackoffTests(HousekeepingPassTestCase):
    def test_does_not_redispatch_within_backoff_window(self):
        with mock.patch.object(mission_supervisor, "_elapsed_seconds_since", return_value=RETRY_BACKOFF_SECONDS - 1):
            with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("pending", "2026-09-01T00:00:00Z")):
                with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                    self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                    time.sleep(0.05)
        derive_mock.assert_not_called()

    def test_redispatches_once_backoff_elapsed(self):
        with mock.patch.object(mission_supervisor, "_elapsed_seconds_since", return_value=RETRY_BACKOFF_SECONDS + 1):
            with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("pending", "2026-09-01T00:00:00Z")):
                with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                    self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                    self.assertTrue(self._wait_for(lambda: derive_mock.call_count == 1))
        derive_mock.assert_called_once_with("m1")

    def test_never_attempted_before_dispatches_immediately(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("pending", None)):
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                self.assertTrue(self._wait_for(lambda: derive_mock.call_count == 1))


class DoneCacheTests(HousekeepingPassTestCase):
    def test_completed_mission_is_never_re_read_within_same_process(self):
        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("completed")) as status_mock:
            self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
            self.assertEqual(status_mock.call_count, 1)
            self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
            self.assertEqual(status_mock.call_count, 1)  # never re-read

    def test_fresh_process_empty_cache_reverifies(self):
        # A "fresh process" here is just a brand-new MissionSupervisor
        # instance with its own empty _knowledge_derivation_done_cache.
        fresh = MissionSupervisor(adapters={}, advance_kwargs={"repository_root": "/tmp", "branch": "b", "pr_title": "t"})
        try:
            with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("completed")) as status_mock:
                fresh._knowledge_derivation_housekeeping_pass([_listing("m1")])
                self.assertEqual(status_mock.call_count, 1)
                self.assertIn("m1", fresh._knowledge_derivation_done_cache)
        finally:
            fresh.close()


class DispatchDeduplicationTests(HousekeepingPassTestCase):
    def test_same_mission_id_never_dispatched_twice_concurrently(self):
        entered = threading.Event()
        release = threading.Event()
        call_count = {"n": 0}

        def _slow(mission_id):
            call_count["n"] += 1
            entered.set()
            release.wait(timeout=5.0)

        with mock.patch.object(mission_coordinator, "knowledge_derivation_status", return_value=_derivation("pending")):
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission", side_effect=_slow):
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                self.assertTrue(entered.wait(timeout=5.0))
                # A second pass, while the first dispatch is still in flight
                # (mission_id still in self._inflight), must not dispatch again.
                self.supervisor._knowledge_derivation_housekeeping_pass([_listing("m1")])
                release.set()
                self.assertTrue(self._wait_for(lambda: call_count["n"] >= 1))
                time.sleep(0.1)
        self.assertEqual(call_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
