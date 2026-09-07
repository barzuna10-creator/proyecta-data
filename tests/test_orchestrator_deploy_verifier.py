"""Focused unit tests for orchestrator/deploy_verifier.py. Mocks the HTTP
layer (_http_get_json) and the ancestry-proof layer (_verify_ancestry)
directly -- exactly like tests/test_orchestrator_merge_executor.py mocks
its own module-level `_run` -- so these tests never touch a real network
or a real git subprocess (that end-to-end coverage lives in
tests/test_deploy_verifier_acceptance.py's disposable harness instead).
Real Chugel Mission Records, in a temp directory, via
tests/test_orchestrator_chugel_deploy.py's own fixtures -- never mocked,
since deploy_verifier.py's whole job is orchestrating real Chugel
mutators correctly."""

from __future__ import annotations

import datetime
import unittest
from unittest import mock

import orchestrator.chugel as chugel
import orchestrator.deploy_verifier as deploy_verifier
from tests.test_orchestrator_chugel import ChugelTestCase
from tests.test_orchestrator_chugel_deploy import (
    _mission_at_deploy_pending,
    _mission_at_merged,
    _mission_at_verifying_production,
)


def _version_ok(sha):
    return (200, {"version": "1.0", "commit": sha}, None)


def _version_malformed():
    return (200, {"version": "1.0"}, None)


def _version_unreachable():
    return (None, None, "timeout after 10s")


def _health_ok():
    return (200, {"status": "ok", "checks": {}}, None)


def _health_degraded():
    return (503, {"status": "degraded", "checks": {"database": "error"}}, None)


def _health_unreachable():
    return (None, None, "timeout after 10s")


class DeployVerifierTestCase(ChugelTestCase):
    def setUp(self):
        super().setUp()
        self._sleep_patch = mock.patch.object(deploy_verifier.time, "sleep")
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        super().tearDown()


class EntryGuardTests(DeployVerifierTestCase):
    def test_wrong_state_raises(self):
        record = _mission_at_merged()
        with self.assertRaises(ValueError):
            deploy_verifier.run(record["mission_id"], base_url="https://x.test", git_repo_path="/tmp")

    def test_budget_already_exhausted_blocks_without_any_poll(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        # Directly backdate observation_started_at far enough into the
        # past to exhaust the 900s budget -- test-only direct write,
        # exactly like ChugelTestCase's own temp-dir redirection reaches
        # into chugel internals for setup.
        stale = chugel.get_mission(mid)
        mutated = dict(stale)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["observation_started_at"] = "2020-01-01T00:00:00Z"
        chugel._write_mission_record(mutated)

        with mock.patch.object(deploy_verifier, "_http_get_json") as http_mock:
            result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        http_mock.assert_not_called()
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "OBSERVATION_BUDGET_EXHAUSTED")


class ReservationContentionTests(DeployVerifierTestCase):
    def test_skip_when_lock_already_held(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(chugel, "reserve_deploy_verification", return_value=None):
            with mock.patch.object(deploy_verifier, "_http_get_json") as http_mock:
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        http_mock.assert_not_called()
        self.assertEqual(result.status, "SKIPPED")
        final = chugel.get_mission(mid)
        self.assertIsNotNone(final["deploy"]["dispatch"]["last_skip_observed_at"])
        # Skip is diagnostic only -- state is untouched.
        self.assertEqual(final["state"], "DEPLOY_PENDING")


class DeployPendingPhaseTests(DeployVerifierTestCase):
    def test_ancestry_match_advances_and_continues_to_health_ok(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        expected_sha = record["deploy"]["expected_sha"]
        with mock.patch.object(deploy_verifier, "_http_get_json",
                                side_effect=[_version_ok("c" * 40), _health_ok()]):
            with mock.patch.object(deploy_verifier, "_verify_ancestry",
                                    return_value=deploy_verifier._ANCESTRY_MATCH) as ancestry_mock:
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        ancestry_mock.assert_called_once()
        self.assertEqual(ancestry_mock.call_args.args[:2], (expected_sha, "c" * 40))
        self.assertEqual(result.status, "COMPLETED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "COMPLETED")
        self.assertTrue(final["deploy"]["ancestry_verified"])
        self.assertEqual(final["deploy"]["observed_sha"], "c" * 40)

    def test_never_observed_identity_blocks_identity_never_observed(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_VERSION_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_malformed()):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_unreachable_version_endpoint_also_blocks_identity_never_observed(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_VERSION_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_unreachable()):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_definitive_non_ancestor_blocks_identity_mismatch_definitive(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_VERSION_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry",
                                        return_value=deploy_verifier._ANCESTRY_DEFINITIVE_NON_ANCESTOR):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_MISMATCH_DEFINITIVE")

    def test_unverifiable_ancestry_blocks_ancestry_unverifiable(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_VERSION_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry",
                                        return_value=deploy_verifier._ANCESTRY_UNVERIFIABLE):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "ANCESTRY_UNVERIFIABLE")

    def test_shallow_repo_never_produces_definitive_mismatch(self):
        """Fail-closed check from the design's own scenario (h): a
        shallow/incomplete git history that keeps returning UNVERIFIABLE
        must never be misclassified as IDENTITY_MISMATCH_DEFINITIVE, even
        though a matching well-formed identity was observed every time."""
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_VERSION_POLL_MAX_ATTEMPTS", 3):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry",
                                        return_value=deploy_verifier._ANCESTRY_UNVERIFIABLE):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        final = chugel.get_mission(mid)
        self.assertNotEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_MISMATCH_DEFINITIVE")
        self.assertEqual(final["deploy"]["last_blocked_classification"], "ANCESTRY_UNVERIFIABLE")


class VerifyingProductionPhaseTests(DeployVerifierTestCase):
    def test_health_ok_confirms_completed(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_health_ok()):
            result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "COMPLETED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "COMPLETED")
        self.assertIsNotNone(final["deploy"]["deploy_confirmed_at"])

    def test_degraded_503_body_is_parsed_and_blocks_health_degraded(self):
        """Proves the body-parse-past-503 fix: a 503 with a well-formed
        status='degraded' JSON body must classify as HEALTH_DEGRADED, not
        HEALTH_UNREACHABLE_OR_MALFORMED -- the response was never a
        transport failure."""
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_HEALTH_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_health_degraded()):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_DEGRADED")
        self.assertEqual(final["deploy"]["health_check"]["status_code"], 503)
        self.assertEqual(final["deploy"]["health_check"]["body_summary"] and True, True)

    def test_unreachable_health_endpoint_blocks_unreachable_or_malformed(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_HEALTH_POLL_MAX_ATTEMPTS", 2):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_health_unreachable()):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_UNREACHABLE_OR_MALFORMED")


class HttpGetJsonParsingTests(unittest.TestCase):
    """Directly proves the /health contract: parse the body regardless of
    status code, never raise_for_status()-before-parse."""

    def test_503_with_valid_json_body_is_parsed_not_treated_as_error(self):
        response = mock.Mock(status_code=503)
        response.json.return_value = {"status": "degraded"}
        with mock.patch.object(deploy_verifier.requests, "get", return_value=response):
            status_code, body, error = deploy_verifier._http_get_json("https://x.test/health", timeout=1)
        self.assertEqual(status_code, 503)
        self.assertEqual(body, {"status": "degraded"})
        self.assertIsNone(error)

    def test_200_with_invalid_json_is_reported_as_malformed(self):
        response = mock.Mock(status_code=200)
        response.json.side_effect = ValueError("bad json")
        with mock.patch.object(deploy_verifier.requests, "get", return_value=response):
            status_code, body, error = deploy_verifier._http_get_json("https://x.test/health", timeout=1)
        self.assertEqual(status_code, 200)
        self.assertIsNone(body)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
