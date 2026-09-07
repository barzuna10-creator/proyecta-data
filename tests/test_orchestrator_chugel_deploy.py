"""Focused tests for orchestrator/chugel.py's M4 (Automatic Production
Deployment Observation & Verification) mutators: begin_deploy_observation,
record_deploy_version_check, record_deploy_health_check,
record_deploy_ancestry_result, record_deploy_confirmed,
record_deploy_blocked, reopen_deploy_observation_window,
reserve_deploy_verification/finalize_deploy_verification/
record_deploy_verification_skip.

Reuses tests/test_orchestrator_chugel.py's own fixtures (ChugelTestCase's
temp-directory redirect, _create_intake_mission, _gate_decision, etc.)
rather than duplicating them -- same isolation discipline: nothing here
ever touches orchestrator/missions/ on disk, and nothing invokes an LLM,
network, or real subprocess."""

from __future__ import annotations

import os
import unittest

import orchestrator.chugel as chugel
import orchestrator.validator as validator
from tests.test_orchestrator_chugel import (
    ChugelTestCase,
    _builder_evidence,
    _create_intake_mission,
    _gate_decision,
    _mission_at_publishing,
    _reviewer_evidence,
)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def _mission_at_merged():
    record = _mission_at_publishing()
    mid = record["mission_id"]
    chugel.record_publish_commit(mid, "b" * 40)
    chugel.record_publish_pr(mid, "https://example.test/pr/9", 9)
    chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
    chugel.record_ci_run(mid, run_id="run-1", conclusion="success")
    chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
    chugel.decide_gate(mid, "merge_authorization", _gate_decision(approved_for={"head_sha": "b" * 40}))
    chugel.transition(mid, "MERGING", actor="jose", reason="merge authorized")
    chugel.record_merge_commit(mid, "d" * 40)
    chugel.transition(mid, "MERGED", actor="chugel", reason="merge executed")
    return chugel.get_mission(mid)


def _mission_at_deploy_pending():
    record = _mission_at_merged()
    mid = record["mission_id"]
    chugel.begin_deploy_observation(mid)
    return chugel.get_mission(mid)


def _mission_at_verifying_production():
    record = _mission_at_deploy_pending()
    mid = record["mission_id"]
    chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="d" * 40)
    chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)
    chugel.transition(mid, "VERIFYING_PRODUCTION", actor="chugel", reason="ancestry confirmed")
    return chugel.get_mission(mid)


class BeginDeployObservationTests(ChugelTestCase):
    def test_sets_expected_sha_and_observation_started_at_and_transitions(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        updated = chugel.begin_deploy_observation(mid)
        self.assertEqual(updated["state"], "DEPLOY_PENDING")
        self.assertEqual(updated["deploy"]["expected_sha"], "d" * 40)
        self.assertIsNotNone(updated["deploy"]["observation_started_at"])
        self.assertEqual(updated["state_history"][-1]["actor"], "chugel")
        result = validator.validate_mission_record(updated)
        self.assertTrue(result.valid, result.errors)

    def test_never_touches_recovery_or_dispatch_fields(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        updated = chugel.begin_deploy_observation(mid)
        self.assertEqual(updated["deploy"]["recovery_count"], 0)
        self.assertEqual(updated["deploy"]["recovery_status"], "available")
        self.assertEqual(updated["deploy"]["recovery_history"], [])
        self.assertIsNone(updated["deploy"]["dispatch"]["invocation_id"])

    def test_wrong_state_refuses(self):
        record = _create_intake_mission("wrong state")
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.begin_deploy_observation(mid)


class RecordDeployVersionCheckTests(ChugelTestCase):
    def test_eligible_and_overwritable_at_deploy_pending(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="a")
        updated = chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:10Z", status_code=200, body_summary="b")
        self.assertEqual(updated["deploy"]["version_check"]["body_summary"], "b")

    def test_wrong_state_refuses(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="a")


class RecordDeployHealthCheckTests(ChugelTestCase):
    def test_eligible_only_at_verifying_production(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        updated = chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:01:00Z", status_code=200, body_summary="ok")
        self.assertEqual(updated["deploy"]["health_check"]["status_code"], 200)

    def test_deploy_pending_refuses(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="ok")


class RecordDeployAncestryResultTests(ChugelTestCase):
    def test_happy_path(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        updated = chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)
        self.assertEqual(updated["deploy"]["observed_sha"], "d" * 40)
        self.assertIs(updated["deploy"]["ancestry_verified"], True)

    def test_rejects_malformed_sha(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with self.assertRaises(ValueError):
            chugel.record_deploy_ancestry_result(mid, observed_sha="not-a-sha", ancestry_verified=True)

    def test_wrong_state_refuses(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)


class RecordDeployConfirmedTests(ChugelTestCase):
    def test_transitions_to_completed(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:01:00Z", status_code=200, body_summary="ok")
        chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="d" * 40)
        updated = chugel.record_deploy_confirmed(mid, confirmed_at="2026-09-01T00:02:00Z")
        self.assertEqual(updated["state"], "COMPLETED")
        result = validator.validate_mission_record(updated)
        self.assertTrue(result.valid, result.errors)

    def test_wrong_state_refuses(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.record_deploy_confirmed(mid, confirmed_at="2026-09-01T00:02:00Z")


class RecordDeployBlockedTests(ChugelTestCase):
    def test_valid_classification_transitions_to_blocked(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        updated = chugel.record_deploy_blocked(mid, classification="IDENTITY_NEVER_OBSERVED", reason="no identity")
        self.assertEqual(updated["state"], "BLOCKED")
        self.assertEqual(updated["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_rejects_unknown_classification(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with self.assertRaises(ValueError):
            chugel.record_deploy_blocked(mid, classification="NOT_A_REAL_CLASSIFICATION", reason="x")

    def test_wrong_state_refuses(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.record_deploy_blocked(mid, classification="IDENTITY_NEVER_OBSERVED", reason="x")


class ReopenDeployObservationWindowTests(ChugelTestCase):
    def _blocked_mission(self, classification="ANCESTRY_UNVERIFIABLE"):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="x")
        chugel.record_deploy_blocked(mid, classification=classification, reason="blocked for test")
        return mid

    def test_requires_literal_jose(self):
        mid = self._blocked_mission()
        with self.assertRaises(ValueError):
            chugel.reopen_deploy_observation_window(mid, decided_by="emilio", acknowledgement="ok")

    def test_requires_nonempty_acknowledgement(self):
        mid = self._blocked_mission()
        with self.assertRaises(ValueError):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="   ")

    def test_wrong_state_refuses(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployRecoveryNotEligible):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="ok")

    def test_happy_path_resets_window_and_appends_history(self):
        mid = self._blocked_mission(classification="ANCESTRY_UNVERIFIABLE")
        before = chugel.get_mission(mid)
        updated = chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="confirmed manually")
        self.assertEqual(updated["state"], "DEPLOY_PENDING")
        self.assertEqual(len(updated["deploy"]["recovery_history"]), 1)
        entry = updated["deploy"]["recovery_history"][0]
        self.assertEqual(entry["decided_by"], "jose")
        self.assertEqual(entry["acknowledgement"], "confirmed manually")
        self.assertEqual(entry["prior_blocked_reason"], "ANCESTRY_UNVERIFIABLE")
        self.assertEqual(entry["prior_version_check"], before["deploy"]["version_check"])
        self.assertIsNone(updated["deploy"]["observed_sha"])
        self.assertIsNone(updated["deploy"]["ancestry_verified"])
        self.assertIsNone(updated["deploy"]["last_blocked_classification"])
        self.assertIsNone(updated["deploy"]["version_check"]["body_summary"])
        self.assertEqual(updated["deploy"]["expected_sha"], before["deploy"]["expected_sha"])
        self.assertEqual(updated["deploy"]["recovery_count"], 1)
        self.assertEqual(updated["deploy"]["recovery_status"], "available")
        result = validator.validate_mission_record(updated)
        self.assertTrue(result.valid, result.errors)

    def test_third_reopen_exhausts_recovery(self):
        mid = self._blocked_mission()
        for _ in range(3):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="retry")
            chugel.record_deploy_blocked(mid, classification="ANCESTRY_UNVERIFIABLE", reason="still blocked")
        updated = chugel.get_mission(mid)
        self.assertEqual(updated["deploy"]["recovery_count"], 3)
        self.assertEqual(updated["deploy"]["recovery_status"], "exhausted")

    def test_fourth_reopen_is_refused(self):
        mid = self._blocked_mission()
        for _ in range(3):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="retry")
            chugel.record_deploy_blocked(mid, classification="ANCESTRY_UNVERIFIABLE", reason="still blocked")
        before = chugel.get_mission(mid)
        with self.assertRaises(chugel.DeployRecoveryNotEligible):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="one more try")
        after = chugel.get_mission(mid)
        self.assertEqual(before, after)


class DeployVerificationReservationTests(ChugelTestCase):
    def test_reserve_then_finalize_happy_path(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        invocation_id = chugel.reserve_deploy_verification(mid)
        self.assertIsNotNone(invocation_id)
        mid_record = chugel.get_mission(mid)
        self.assertEqual(mid_record["deploy"]["dispatch"]["status"], "reserved")
        self.assertEqual(mid_record["deploy"]["dispatch"]["invocation_id"], invocation_id)
        finalized = chugel.finalize_deploy_verification(mid, invocation_id)
        self.assertEqual(finalized["deploy"]["dispatch"]["status"], "completed")
        # Lock is released -- a second reservation must succeed immediately.
        second = chugel.reserve_deploy_verification(mid)
        self.assertIsNotNone(second)
        self.assertNotEqual(second, invocation_id)
        chugel.finalize_deploy_verification(mid, second)

    def test_finalize_with_stale_invocation_id_refuses(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        chugel.reserve_deploy_verification(mid)
        with self.assertRaises(chugel.DeployVerificationReservationStale):
            chugel.finalize_deploy_verification(mid, "00000000-0000-4000-8000-000000000000")

    @unittest.skipIf(fcntl is None, "POSIX-only")
    def test_reservation_contention_returns_none_and_writes_nothing(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        lock_path = chugel._deploy_verification_lock_path(mid)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            before = chugel.get_mission(mid)
            result = chugel.reserve_deploy_verification(mid)
            self.assertIsNone(result)
            after = chugel.get_mission(mid)
            self.assertEqual(before, after)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_record_deploy_verification_skip_sets_only_that_field(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        before = chugel.get_mission(mid)
        updated = chugel.record_deploy_verification_skip(mid)
        self.assertIsNotNone(updated["deploy"]["dispatch"]["last_skip_observed_at"])
        after_stripped = dict(updated["deploy"])
        after_stripped["dispatch"] = dict(after_stripped["dispatch"])
        after_stripped["dispatch"]["last_skip_observed_at"] = None
        before_stripped = dict(before["deploy"])
        self.assertEqual(after_stripped, before_stripped)

    def test_wrong_state_refuses_reservation(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        with self.assertRaises(chugel.DeployNotEligible):
            chugel.reserve_deploy_verification(mid)


class FullDeployLifecycleIntegrationTests(ChugelTestCase):
    def test_merged_to_completed_end_to_end(self):
        record = _mission_at_merged()
        mid = record["mission_id"]
        chugel.begin_deploy_observation(mid)
        chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="d" * 40)
        chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)
        chugel.transition(mid, "VERIFYING_PRODUCTION", actor="chugel", reason="ancestry confirmed")
        chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:01:00Z", status_code=200, body_summary="ok")
        final = chugel.record_deploy_confirmed(mid, confirmed_at="2026-09-01T00:02:00Z")
        self.assertEqual(final["state"], "COMPLETED")
        result = validator.validate_mission_record(final)
        self.assertTrue(result.valid, result.errors)


if __name__ == "__main__":
    unittest.main()
