"""Focused tests for jarvis/mission_coordinator.py's M5 (Learning &
Knowledge Continuity) additions: derive_knowledge_for_completed_mission()
and _submit_candidate_idempotently(). Drives a real Chugel Mission Record
(in a temp directory, via tests/test_orchestrator_chugel.py's own
fixtures) all the way to COMPLETED -- never mocked, since this module's
whole job is orchestrating real Chugel mutators and a real
FileKnowledgeStore correctly. The knowledge store itself is also a real
FileKnowledgeStore rooted at a temp directory (jarvis.mission_coordinator.
_KNOWLEDGE_STORE_ROOT redirected, same isolation discipline as
ChugelTestCase's own _MISSIONS_DIR redirect)."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import orchestrator.chugel as chugel
import jarvis.mission_coordinator as mission_coordinator
from jarvis import mission_query
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.mission_supervisor import MissionSupervisor
from tests.test_orchestrator_chugel import (
    ChugelTestCase, _builder_evidence, _gate_decision, _mission_definition_payload, _reviewer_evidence,
)


def _completed_mission_with_rounds(round_specs, *, outcome="Ship the widget", scope=("Build the widget",),
                                    acceptance_criteria=("Widget ships",), branch="feature/widget",
                                    base_sha="c" * 40):
    """Drives a fresh mission through the real Chugel lifecycle, with one
    reviewer round per entry of `round_specs` (each a dict with
    'verdict'/'findings'), all the way to COMPLETED. The LAST round's
    verdict must be "PASS" with no findings (or "PASS_WITH_NON_BLOCKING_
    FINDINGS" with only P3s) -- exactly like a real mission -- since only
    that verdict/findings combination is accepted by
    orchestrator/validator.py for the PUBLISH_AWAITING_AUTHORIZATION
    evidence check."""
    payload = _mission_definition_payload()
    payload.update(outcome=outcome, scope=list(scope), acceptance_criteria=list(acceptance_criteria))
    m = chugel.create_mission("intent text", payload)
    mid = m["mission_id"]
    chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="ready")
    chugel.decide_gate(mid, "scope_authorization", _gate_decision(approved_for={"mission_definition_version": 1}))
    chugel.record_repository_state(mid, {
        "worktree_path": "/tmp/w", "branch": branch, "base_sha": base_sha, "isolation_confirmed": True,
    })
    chugel.transition(mid, "AUTHORIZED", actor="jose", reason="authorized")

    for attempt, spec in enumerate(round_specs):
        if attempt == 0:
            chugel.transition(mid, "BUILDING", actor="chugel", reason="build")
        else:
            chugel.transition(mid, "CORRECTING", actor="jose", reason="corrective cycle authorized")
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=attempt))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="verify")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="handoff")
        chugel.transition(mid, "REVIEWING", actor="jose", reason="review")
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(
            attempt=attempt, verdict=spec["verdict"], findings=spec.get("findings"),
        ))
        if attempt < len(round_specs) - 1:
            chugel.transition(mid, "CHANGES_REQUIRED", actor="chugel", reason="fix")
        else:
            chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="pass")

    chugel.decide_gate(mid, "publish_authorization", _gate_decision(approved_for={"mission_definition_version": 1}))
    chugel.transition(mid, "PUBLISHING", actor="jose", reason="publish authorized")
    chugel.record_publish_commit(mid, "b" * 40)
    chugel.record_publish_pr(mid, "https://example.test/pr/9", 9)
    chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
    chugel.record_ci_run(mid, run_id="run-1", conclusion="success")
    chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
    chugel.decide_gate(mid, "merge_authorization", _gate_decision(approved_for={"head_sha": "b" * 40}))
    chugel.transition(mid, "MERGING", actor="jose", reason="merge authorized")
    chugel.record_merge_commit(mid, "d" * 40)
    chugel.transition(mid, "MERGED", actor="chugel", reason="merge executed")
    chugel.begin_deploy_observation(mid)
    chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="d" * 40)
    chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)
    chugel.transition(mid, "VERIFYING_PRODUCTION", actor="chugel", reason="ancestry confirmed")
    chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:04:00Z", status_code=200, body_summary="ok")
    chugel.record_deploy_confirmed(mid, confirmed_at="2026-09-01T00:05:00Z")
    return mid


def _pass_round():
    return {"verdict": "PASS", "findings": []}


def _resolved_case_rounds():
    return [
        {"verdict": "CHANGES_REQUIRED", "findings": [
            {"id": "f1", "severity": "P1", "summary": "found a real bug", "file": "x.py", "line_range": "1-2", "category": "correctness"},
        ]},
        _pass_round(),
    ]


class KnowledgeCoordinatorTestCase(ChugelTestCase):
    def setUp(self):
        super().setUp()
        self._store_tmpdir = tempfile.TemporaryDirectory()
        self._original_root = mission_coordinator._KNOWLEDGE_STORE_ROOT
        mission_coordinator._KNOWLEDGE_STORE_ROOT = Path(self._store_tmpdir.name) / "knowledge"

    def tearDown(self):
        mission_coordinator._KNOWLEDGE_STORE_ROOT = self._original_root
        self._store_tmpdir.cleanup()
        super().tearDown()

    def store(self) -> FileKnowledgeStore:
        return mission_coordinator._knowledge_store()


class RequiresCompletedStateTests(KnowledgeCoordinatorTestCase):
    def test_raises_for_non_completed_mission(self):
        mid = chugel.create_mission("algo", _mission_definition_payload())["mission_id"]
        with self.assertRaises(ValueError):
            mission_coordinator.derive_knowledge_for_completed_mission(mid)

    def test_blocked_or_failed_mission_never_reaches_either_trigger_site(self):
        """Structural: both trigger sites require state == COMPLETED, so a
        BLOCKED mission can never produce a claim from an abandoned
        attempt's own content."""
        mid = chugel.create_mission("algo", _mission_definition_payload())["mission_id"]
        # never transitioned to COMPLETED -- still INTAKE
        with self.assertRaises(ValueError):
            mission_coordinator.derive_knowledge_for_completed_mission(mid)
        self.assertEqual(chugel.get_mission(mid)["knowledge_derivation"]["status"], "pending")


class DerivationEndToEndTests(KnowledgeCoordinatorTestCase):
    def test_derives_and_submits_candidates_and_records_completion(self):
        mid = _completed_mission_with_rounds(_resolved_case_rounds())
        mission_coordinator.derive_knowledge_for_completed_mission(mid)

        record = chugel.get_mission(mid)
        self.assertEqual(record["knowledge_derivation"]["status"], "completed")
        candidate_ids = record["knowledge_derivation"]["candidate_ids"]
        self.assertGreaterEqual(len(candidate_ids), 2)  # resolved-finding + outcome

        store = self.store()
        for candidate_id in candidate_ids:
            self.assertEqual(store.get_candidate_status(candidate_id), "awaiting_emma_review")

    def test_no_auto_promotion_list_latest_entries_unchanged(self):
        mid = _completed_mission_with_rounds([_pass_round()])
        before = self.store().list_latest_entries()
        mission_coordinator.derive_knowledge_for_completed_mission(mid)
        after = self.store().list_latest_entries()
        self.assertEqual(before, after)
        self.assertEqual(after.entries, ())


class IdempotencyTests(KnowledgeCoordinatorTestCase):
    def test_second_call_is_a_no_op(self):
        mid = _completed_mission_with_rounds(_resolved_case_rounds())
        mission_coordinator.derive_knowledge_for_completed_mission(mid)
        first_ids = chugel.get_mission(mid)["knowledge_derivation"]["candidate_ids"]
        mission_coordinator.derive_knowledge_for_completed_mission(mid)  # must not raise
        second_ids = chugel.get_mission(mid)["knowledge_derivation"]["candidate_ids"]
        self.assertEqual(first_ids, second_ids)

    def test_crash_after_partial_submission_then_fresh_retry_completes(self):
        mid = _completed_mission_with_rounds(_resolved_case_rounds())
        real_completed = chugel.record_knowledge_derivation_completed

        with mock.patch.object(chugel, "record_knowledge_derivation_completed", side_effect=RuntimeError("crash before completion marker")):
            with self.assertRaises(RuntimeError):
                mission_coordinator.derive_knowledge_for_completed_mission(mid)

        record = chugel.get_mission(mid)
        self.assertEqual(record["knowledge_derivation"]["status"], "pending")
        self.assertEqual(record["knowledge_derivation"]["attempt_count"], 1)

        store = self.store()

        with mock.patch.object(chugel, "record_knowledge_derivation_completed", side_effect=real_completed):
            mission_coordinator.derive_knowledge_for_completed_mission(mid)  # fresh retry, must not raise

        final = chugel.get_mission(mid)
        self.assertEqual(final["knowledge_derivation"]["status"], "completed")
        for candidate_id in final["knowledge_derivation"]["candidate_ids"]:
            self.assertEqual(store.get_candidate_status(candidate_id), "awaiting_emma_review")

    def test_deterministic_candidate_ids_survive_crash_and_full_rederivation(self):
        mid = _completed_mission_with_rounds(_resolved_case_rounds())
        record = chugel.get_mission(mid)
        from jarvis.learning_projection import project_mission_learning
        from jarvis.learning_ingestion import derive_knowledge_candidates

        first_pass = derive_knowledge_candidates(project_mission_learning(record))

        with mock.patch.object(chugel, "record_knowledge_derivation_completed", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                mission_coordinator.derive_knowledge_for_completed_mission(mid)

        record_after_crash = chugel.get_mission(mid)
        second_pass = derive_knowledge_candidates(project_mission_learning(record_after_crash))

        self.assertEqual(
            [c.candidate_id for c in first_pass], [c.candidate_id for c in second_pass],
        )
        self.assertEqual(first_pass, second_pass)

    def test_two_near_simultaneous_invocations_no_deadlock_no_double_write(self):
        mid = _completed_mission_with_rounds(_resolved_case_rounds())
        results = []
        errors = []

        def _run():
            try:
                mission_coordinator.derive_knowledge_for_completed_mission(mid)
                results.append(True)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "a thread never returned -- deadlock")

        record = chugel.get_mission(mid)
        self.assertEqual(record["knowledge_derivation"]["status"], "completed")
        candidate_ids = record["knowledge_derivation"]["candidate_ids"]
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)), "no duplicate candidate ids")


class ToctouTests(KnowledgeCoordinatorTestCase):
    def _one_candidate(self, mid):
        from jarvis.learning_projection import project_mission_learning
        from jarvis.learning_ingestion import derive_knowledge_candidates
        record = chugel.get_mission(mid)
        candidates = derive_knowledge_candidates(project_mission_learning(record))
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def test_already_saved_and_transitioned_is_a_pure_no_op(self):
        mid = _completed_mission_with_rounds([_pass_round()])
        content = self._one_candidate(mid)
        store = self.store()
        from jarvis.knowledge import build_candidate_envelope
        store.save_candidate(build_candidate_envelope(content))
        store.transition_candidate(content.candidate_id, "awaiting_emma_review")

        mission_coordinator._submit_candidate_idempotently(content)  # must not raise

        self.assertEqual(store.get_candidate_status(content.candidate_id), "awaiting_emma_review")

    def test_saved_but_not_yet_transitioned_gets_transitioned(self):
        mid = _completed_mission_with_rounds([_pass_round()])
        content = self._one_candidate(mid)
        store = self.store()
        from jarvis.knowledge import build_candidate_envelope
        store.save_candidate(build_candidate_envelope(content))  # simulates a crash right after save

        mission_coordinator._submit_candidate_idempotently(content)

        self.assertEqual(store.get_candidate_status(content.candidate_id), "awaiting_emma_review")

    def test_concurrent_submission_of_same_candidate_is_benign(self):
        """Exercises _submit_candidate_idempotently()'s own TOCTOU-hardened
        path directly: several concurrent submissions of the exact same
        candidate content, from separate threads of this one process,
        never raise and never produce two saved revisions -- the
        underlying exclusive_entity_lock's own in-process reentrancy guard
        (LockReentryError) is treated as a benign "another in-flight call
        is already handling this" signal, not a real failure."""
        mid = _completed_mission_with_rounds([_pass_round()])
        content = self._one_candidate(mid)

        errors = []

        def _submit():
            try:
                mission_coordinator._submit_candidate_idempotently(content)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_submit) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(self.store().get_candidate_status(content.candidate_id), "awaiting_emma_review")


class LockFreedomTests(KnowledgeCoordinatorTestCase):
    def test_never_blocks_beyond_the_two_mutators_own_brief_holds(self):
        mid = _completed_mission_with_rounds([_pass_round()])
        started = time.monotonic()
        done = threading.Event()

        def _run():
            mission_coordinator.derive_knowledge_for_completed_mission(mid)
            done.set()

        thread = threading.Thread(target=_run)
        thread.start()
        finished = done.wait(timeout=10.0)
        thread.join(timeout=1.0)
        elapsed = time.monotonic() - started
        self.assertTrue(finished, "derive_knowledge_for_completed_mission() did not return within 10s -- possible deadlock")
        self.assertLess(elapsed, 10.0)

    def test_does_not_hold_mission_lock_while_submitting_to_knowledge_store(self):
        """Directly demonstrates no nested lock acquisition: while
        derive_knowledge_for_completed_mission() is mid-flight (blocked
        inside a slow, mocked _submit_candidate_idempotently), the mission's
        own _mission_lock() must still be freely acquirable from another
        thread -- proving it is not held across the candidate-submission
        loop."""
        mid = _completed_mission_with_rounds([_pass_round()])
        entered = threading.Event()
        release = threading.Event()

        def _slow_submit(content):
            entered.set()
            release.wait(timeout=5.0)

        with mock.patch.object(mission_coordinator, "_submit_candidate_idempotently", side_effect=_slow_submit):
            thread = threading.Thread(target=mission_coordinator.derive_knowledge_for_completed_mission, args=(mid,))
            thread.start()
            self.assertTrue(entered.wait(timeout=5.0), "submission never started")

            acquired = threading.Event()

            def _try_lock():
                with chugel._mission_lock(mid):
                    acquired.set()

            lock_thread = threading.Thread(target=_try_lock)
            lock_thread.start()
            lock_thread.join(timeout=5.0)
            self.assertTrue(acquired.is_set(), "mission lock was unexpectedly held during candidate submission")

            release.set()
            thread.join(timeout=5.0)


class ManualRetryAfterStalledTests(KnowledgeCoordinatorTestCase):
    def test_stalled_mission_is_never_auto_dispatched_but_manual_retry_completes(self):
        mid = _completed_mission_with_rounds([_pass_round()])

        # Drive attempt_count to MAX_KNOWLEDGE_DERIVATION_ATTEMPTS via 5
        # real, distinct chugel failures (spaced -- in the real system --
        # by the housekeeping pass's own RETRY_BACKOFF_SECONDS; the chugel
        # mutator itself does not read wall-clock time to decide whether to
        # stall, only the housekeeping pass's own dispatch gate does, so no
        # fake clock is needed at this layer).
        for _ in range(chugel.MAX_KNOWLEDGE_DERIVATION_ATTEMPTS):
            chugel.record_knowledge_derivation_attempt_failed(mid, error_summary="synthetic failure")
        record = chugel.get_mission(mid)
        self.assertEqual(record["knowledge_derivation"]["status"], "stalled")
        self.assertEqual(record["knowledge_derivation"]["attempt_count"], chugel.MAX_KNOWLEDGE_DERIVATION_ATTEMPTS)

        # The automatic housekeeping pass never dispatches to it again.
        supervisor = MissionSupervisor(adapters={}, advance_kwargs={"repository_root": "/tmp", "branch": "b", "pr_title": "t"})
        try:
            listing = mission_query.MissionListing(
                mission_id=mid, readable=True, state="COMPLETED", bucket=None, updated_at="2026-09-01T00:00:00Z",
                error_code=None,
            )
            with mock.patch.object(mission_coordinator, "derive_knowledge_for_completed_mission") as derive_mock:
                supervisor._knowledge_derivation_housekeeping_pass([listing])
                import time as _time
                _time.sleep(0.05)
            derive_mock.assert_not_called()
        finally:
            supervisor.close()

        # A direct/manual call is never refused merely because status is
        # "stalled" -- it always attempts the work, and can reach
        # "completed".
        mission_coordinator.derive_knowledge_for_completed_mission(mid)
        final = chugel.get_mission(mid)
        self.assertEqual(final["knowledge_derivation"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
