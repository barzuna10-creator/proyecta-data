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


def _fake_clock(start: float, step: float):
    """Returns (remaining_budget_side_effect, advance). The side_effect is
    a _remaining_budget(...)-compatible callable that reads a SHARED
    counter -- multiple reads within the same poll iteration (e.g. a
    pre-ancestry-check and a loop-bottom check) see the SAME value,
    exactly like real wall-clock barely moves across a few fast Python
    calls. `advance()` moves the counter forward by `step` and is meant to
    be called exactly once per real poll (once per HTTP call) -- modeling
    one real interval's worth of wall-clock elapsing BETWEEN polls, never
    between internal budget checks within the same poll. This is what
    makes the fake clock behave like the real one instead of racing
    against however many internal _remaining_budget() call sites the
    implementation happens to have."""
    state = {"remaining": start}

    def _remaining_budget_side_effect(_observation_started_at):
        return state["remaining"]

    def _advance():
        state["remaining"] -= step

    return _remaining_budget_side_effect, _advance


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
        # A deterministic fake clock -- advanced once per real poll, read
        # (possibly more than once) per internal budget check -- runs a
        # small, exact number of real, evaluated polls before exhausting,
        # with no dependency on real wall-clock timing.
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        remaining_budget_side_effect, advance = _fake_clock(start=3.0, step=1.0)

        def _http(*_args, **_kwargs):
            advance()
            return _version_malformed()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_unreachable_version_endpoint_also_blocks_identity_never_observed(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        remaining_budget_side_effect, advance = _fake_clock(start=3.0, step=1.0)

        def _http(*_args, **_kwargs):
            advance()
            return _version_unreachable()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_definitive_non_ancestor_blocks_identity_mismatch_definitive(self):
        # The clock advances inside the (mocked) ancestry check, not the
        # HTTP call -- matching real production, where _verify_ancestry
        # itself (lock wait + real git subprocesses) is what can consume
        # real time WITHIN one iteration, between the pre-ancestry-check
        # (still positive) and the loop-bottom check (now exhausted) --
        # exactly the sequence that must reach the "genuinely evaluated"
        # classification, not an early OBSERVATION_BUDGET_EXHAUSTED bail.
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        remaining_budget_side_effect, advance = _fake_clock(start=1.0, step=1.0)

        def _ancestry(*_args, **_kwargs):
            advance()
            return deploy_verifier._ANCESTRY_DEFINITIVE_NON_ANCESTOR

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry", side_effect=_ancestry):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_MISMATCH_DEFINITIVE")

    def test_unverifiable_ancestry_blocks_ancestry_unverifiable(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        remaining_budget_side_effect, advance = _fake_clock(start=1.0, step=1.0)

        def _ancestry(*_args, **_kwargs):
            advance()
            return deploy_verifier._ANCESTRY_UNVERIFIABLE

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry", side_effect=_ancestry):
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
        remaining_budget_side_effect, advance = _fake_clock(start=3.0, step=1.0)

        def _ancestry(*_args, **_kwargs):
            advance()
            return deploy_verifier._ANCESTRY_UNVERIFIABLE

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry", side_effect=_ancestry):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        final = chugel.get_mission(mid)
        self.assertNotEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_MISMATCH_DEFINITIVE")
        self.assertEqual(final["deploy"]["last_blocked_classification"], "ANCESTRY_UNVERIFIABLE")


class BudgetDrivenTerminationTests(DeployVerifierTestCase):
    """Direct regression coverage for the corrected P0 bug: version/health
    polling must be bounded by the mission's real, PERSISTED observation
    budget (TOTAL_OBSERVATION_BUDGET_SECONDS, derived from
    deploy.observation_started_at), never by a fixed attempt count. Before
    the fix, a fixed 30-attempt cap (at a 10s interval) silently terminated
    polling at ~300s even when the real, authorized budget was 900s --
    these tests prove that can no longer happen, using a deterministic
    fake remaining-budget clock so the assertion is exact and fast rather
    than actually sleeping for minutes."""

    def test_version_polling_does_not_stop_at_the_old_thirty_attempt_boundary(self):
        """(1) Proves polling cannot terminate at ~300s (the old fixed
        30 x 10s cap) while a real 900s-equivalent budget still remains."""
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        call_count = {"n": 0}
        remaining_budget_side_effect, advance = _fake_clock(start=900.0, step=10.0)

        def _http(*_args, **_kwargs):
            call_count["n"] += 1
            advance()
            return _version_malformed()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")

        self.assertGreater(
            call_count["n"], 30,
            "polling stopped at or before the old fixed 30-attempt cap even though the "
            "persisted budget had not been exhausted",
        )
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_definitive_non_ancestor_only_classifies_after_real_budget_exhaustion(self):
        """(2) Proves the core P0 scenario: a continuously-observed,
        still-propagating old commit (a genuine 'non-ancestor' on every
        single poll -- indistinguishable, mid-flight, from a real mismatch)
        must NOT be classified IDENTITY_MISMATCH_DEFINITIVE merely because
        some fixed attempt count was reached -- only once the real,
        persisted budget is genuinely spent."""
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        call_count = {"n": 0}
        # Advances inside the (mocked) ancestry check, not the HTTP call --
        # matching real production, where _verify_ancestry itself (a real
        # lock wait plus real git subprocesses) is what can consume real
        # time WITHIN one iteration.
        remaining_budget_side_effect, advance = _fake_clock(start=900.0, step=10.0)

        def _ancestry(*_args, **_kwargs):
            call_count["n"] += 1
            advance()
            return deploy_verifier._ANCESTRY_DEFINITIVE_NON_ANCESTOR

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", return_value=_version_ok("c" * 40)):
                with mock.patch.object(deploy_verifier, "_verify_ancestry", side_effect=_ancestry):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")

        self.assertGreater(
            call_count["n"], 30,
            "classified as a definitive mismatch before the real persisted budget was spent",
        )
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_MISMATCH_DEFINITIVE")

    def test_budget_expiry_before_any_evaluation_produces_observation_budget_exhausted(self):
        """(3) Budget expiry detected before any poll this call can even be
        evaluated classifies OBSERVATION_BUDGET_EXHAUSTED, never a stale
        reuse of some earlier classification."""
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_remaining_budget", return_value=0.0):
            with mock.patch.object(deploy_verifier, "_http_get_json") as http_mock:
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        http_mock.assert_not_called()
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "OBSERVATION_BUDGET_EXHAUSTED")

    def test_success_well_before_deadline_still_exits_normally(self):
        """(4) A match found well before the (simulated, still-large)
        budget is exhausted must still complete normally on the very first
        poll -- the budget-driven loop must never force needless extra
        polling once the real work is done."""
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        with mock.patch.object(deploy_verifier, "_remaining_budget", return_value=900.0):
            with mock.patch.object(deploy_verifier, "_http_get_json",
                                    side_effect=[_version_ok("c" * 40), _health_ok()]):
                with mock.patch.object(deploy_verifier, "_verify_ancestry",
                                        return_value=deploy_verifier._ANCESTRY_MATCH):
                    result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "COMPLETED")

    def test_health_polling_does_not_stop_at_a_fixed_attempt_boundary_either(self):
        """Same regression, health phase: a persistently degraded response
        must not be classified until the real persisted budget -- not a
        fixed attempt count -- is exhausted."""
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        call_count = {"n": 0}
        remaining_budget_side_effect, advance = _fake_clock(start=900.0, step=5.0)

        def _http(*_args, **_kwargs):
            call_count["n"] += 1
            advance()
            return _health_degraded()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")

        self.assertGreater(call_count["n"], 6,
                            "health polling stopped at or before the old fixed 6-attempt cap "
                            "even though the persisted budget had not been exhausted")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_DEGRADED")


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
        remaining_budget_side_effect, advance = _fake_clock(start=3.0, step=1.0)

        def _http(*_args, **_kwargs):
            advance()
            return _health_degraded()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
                result = deploy_verifier.run(mid, base_url="https://x.test", git_repo_path="/tmp")
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_DEGRADED")
        self.assertEqual(final["deploy"]["health_check"]["status_code"], 503)
        self.assertEqual(final["deploy"]["health_check"]["body_summary"] and True, True)

    def test_unreachable_health_endpoint_blocks_unreachable_or_malformed(self):
        record = _mission_at_verifying_production()
        mid = record["mission_id"]
        remaining_budget_side_effect, advance = _fake_clock(start=3.0, step=1.0)

        def _http(*_args, **_kwargs):
            advance()
            return _health_unreachable()

        with mock.patch.object(deploy_verifier, "_remaining_budget", side_effect=remaining_budget_side_effect):
            with mock.patch.object(deploy_verifier, "_http_get_json", side_effect=_http):
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
