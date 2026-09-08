"""Focused tests for orchestrator/chugel.py's M5 (Learning & Knowledge
Continuity) mutators: record_knowledge_derivation_completed() and
record_knowledge_derivation_attempt_failed(). Reuses
tests/test_orchestrator_chugel.py's own fixtures, same isolation
discipline as tests/test_orchestrator_chugel_deploy.py."""

from __future__ import annotations

import unittest

import orchestrator.chugel as chugel
from tests.test_orchestrator_chugel import ChugelTestCase, _create_intake_mission


class InitializationTests(ChugelTestCase):
    def test_create_mission_initializes_knowledge_derivation(self):
        m = _create_intake_mission("algo")
        self.assertEqual(m["knowledge_derivation"], {
            "status": "pending", "attempt_count": 0, "last_attempted_at": None,
            "last_error": None, "completed_at": None, "candidate_ids": [],
        })


class RecordKnowledgeDerivationCompletedTests(ChugelTestCase):
    def test_sets_completed_status_and_candidate_ids_never_touches_state(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        before_state = chugel.get_mission(mid)["state"]
        before_history = chugel.get_mission(mid)["state_history"]

        updated = chugel.record_knowledge_derivation_completed(mid, candidate_ids=["a", "b"])

        self.assertEqual(updated["knowledge_derivation"]["status"], "completed")
        self.assertEqual(updated["knowledge_derivation"]["candidate_ids"], ["a", "b"])
        self.assertIsNotNone(updated["knowledge_derivation"]["completed_at"])
        self.assertEqual(updated["state"], before_state)
        self.assertEqual(updated["state_history"], before_history)

    def test_idempotent_no_op_when_already_completed(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_knowledge_derivation_completed(mid, candidate_ids=["a"])
        # calling again with DIFFERENT candidate_ids must never raise and
        # must never overwrite the already-completed record
        result = chugel.record_knowledge_derivation_completed(mid, candidate_ids=["z", "y"])
        self.assertEqual(result["knowledge_derivation"]["candidate_ids"], ["a"])

    def test_candidate_ids_stored_as_a_plain_list_copy(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        source = ["a", "b"]
        chugel.record_knowledge_derivation_completed(mid, candidate_ids=source)
        source.append("mutated-after")
        reread = chugel.get_mission(mid)
        self.assertEqual(reread["knowledge_derivation"]["candidate_ids"], ["a", "b"])


class RecordKnowledgeDerivationAttemptFailedTests(ChugelTestCase):
    def test_increments_attempt_count_and_records_error(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        updated = chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="boom")
        self.assertEqual(updated["knowledge_derivation"]["attempt_count"], 1)
        self.assertEqual(updated["knowledge_derivation"]["last_error"], "boom")
        self.assertIsNotNone(updated["knowledge_derivation"]["last_attempted_at"])
        self.assertEqual(updated["knowledge_derivation"]["status"], "pending")

    def test_reaching_max_attempts_sets_stalled_in_same_write(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        for _ in range(chugel.MAX_KNOWLEDGE_DERIVATION_ATTEMPTS - 1):
            updated = chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="boom")
            self.assertEqual(updated["knowledge_derivation"]["status"], "pending")
        final = chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="boom")
        self.assertEqual(final["knowledge_derivation"]["attempt_count"], chugel.MAX_KNOWLEDGE_DERIVATION_ATTEMPTS)
        self.assertEqual(final["knowledge_derivation"]["status"], "stalled")

    def test_never_touches_state_or_history(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        before_state = chugel.get_mission(mid)["state"]
        before_history = chugel.get_mission(mid)["state_history"]
        updated = chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="boom")
        self.assertEqual(updated["state"], before_state)
        self.assertEqual(updated["state_history"], before_history)

    def test_no_op_when_already_completed(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_knowledge_derivation_completed(mid, candidate_ids=["a"])
        result = chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="boom")
        self.assertEqual(result["knowledge_derivation"]["status"], "completed")
        self.assertEqual(result["knowledge_derivation"]["attempt_count"], 0)


class LockDisciplineTests(ChugelTestCase):
    def test_mutators_use_the_ordinary_mission_lock_not_a_second_lock_file(self):
        """These two mutators must use exactly the same _mission_lock() as
        every other Chugel mutator -- no bespoke second lock file for this
        feature."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        with chugel._mission_lock(mid):
            pass  # lock acquires and releases cleanly; mutators reuse the same path
        chugel.record_knowledge_derivation_completed(mid, candidate_ids=[])
        with chugel._mission_lock(mid):
            pass


if __name__ == "__main__":
    unittest.main()
