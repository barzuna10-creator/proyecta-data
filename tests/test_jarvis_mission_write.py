"""Real-persistence tests for jarvis/mission_write.py -- Mission 004's
sole Jarvis-to-Chugel write seam. Drives real Chugel Mission Records
(never mocked) through the actual create_mission/transition/decide_gate
call chain, mirroring tests/test_orchestrator_autonomous_runner.py's
fixture style."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import orchestrator.chugel as chugel
from jarvis.mission_write import (
    GateNotYetEligible,
    MissionWriteError,
    ResumeNotEligible,
    authorize_merge,
    authorize_publish,
    authorize_scope,
    create_mission,
    resume_from_blocked,
)


def _mission_definition_payload(authorized_by="jose"):
    return {
        "outcome": "ship the thing", "scope": ["do the thing"], "non_goals": [],
        "acceptance_criteria": ["it works"], "authorized_by": authorized_by,
        "authorized_at": "2026-08-19T12:00:00Z", "authorization_decision_ref": "ref-intake-1",
    }


def _decision(status="approved", decided_by="jose"):
    return {
        "status": status, "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": decided_by,
        "decision_ref": "ref-1", "approved_for": {"mission_definition_version": 1},
    }


class MissionWriteTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _fresh_mission(self):
        record = create_mission("algo", _mission_definition_payload(), _decision())
        mid = record["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="ready")
        return mid


class CreateMissionAttributionTests(MissionWriteTestCase):
    def test_rejects_non_jose_attribution(self):
        with self.assertRaises(MissionWriteError):
            create_mission("algo", _mission_definition_payload(), _decision(decided_by="not-jose"))

    def test_accepts_jose_attribution(self):
        record = create_mission("algo", _mission_definition_payload(), _decision())
        self.assertEqual(record["state"], "INTAKE")


class GateStateGuardTests(MissionWriteTestCase):
    def test_authorize_scope_succeeds_at_the_right_state(self):
        mid = self._fresh_mission()
        record = authorize_scope(mid, _decision())
        self.assertEqual(record["human_gates"]["scope_authorization"]["status"], "approved")

    def test_authorize_scope_rejects_non_jose_attribution(self):
        mid = self._fresh_mission()
        with self.assertRaises(MissionWriteError):
            authorize_scope(mid, _decision(decided_by="not-jose"))

    def test_every_gate_rejects_a_representative_spread_of_non_matching_states(self):
        """Each authorize_* must refuse in states other than its own exact
        match -- not just the obvious case. Every state below is reached
        through real, legal chugel.transition() calls (never a hacked
        record) -- restricted to states with no dedicated evidence
        function (orchestrator/validator.py), which BLOCKED can legally
        reach unconditionally, so no fabricated evidence is required."""
        reachable_via_blocked = (
            "INTAKE", "PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING",
            "CI_PENDING", "MERGE_AWAITING_AUTHORIZATION", "CANCELLED",
        )
        gate_state = {
            authorize_scope: "SCOPE_AWAITING_AUTHORIZATION",
            authorize_publish: "PUBLISH_AWAITING_AUTHORIZATION",
            authorize_merge: "MERGE_AWAITING_AUTHORIZATION",
        }
        for fn, correct_state in gate_state.items():
            for state in reachable_via_blocked:
                if state == correct_state:
                    continue
                with self.subTest(fn=fn.__name__, state=state):
                    mid = self._fresh_mission()
                    chugel.transition(mid, "BLOCKED", actor="chugel", reason="test setup")
                    chugel.transition(mid, state, actor="chugel", reason="test setup")
                    with self.assertRaises(GateNotYetEligible):
                        fn(mid, _decision())
        # BLOCKED itself, and SCOPE_AWAITING_AUTHORIZATION for the non-scope gates.
        for fn in (authorize_publish, authorize_merge):
            mid = self._fresh_mission()
            with self.assertRaises(GateNotYetEligible):
                fn(mid, _decision())
        mid = self._fresh_mission()
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="test setup")
        with self.assertRaises(GateNotYetEligible):
            authorize_scope(mid, _decision())


class ResumeFromBlockedTests(MissionWriteTestCase):
    def _blocked_from(self, mid, prior_state):
        """Reaches BLOCKED from `prior_state` via two real, legal
        chugel.transition() calls -- never a hacked record. Every
        prior_state this fixture is used with (PUBLISHING, CI_PENDING,
        MERGE_AWAITING_AUTHORIZATION, MERGING, BUILDING) has no
        dedicated evidence function requiring extra setup, per
        orchestrator/validator.py's TRANSITION_EVIDENCE_TABLE, except
        MERGING (which needs an approved merge_authorization gate,
        supplied here for that one case)."""
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="test setup")
        if prior_state == "MERGING":
            # merge_authorization's approved_for.head_sha must exactly
            # match publish.commit_sha (orchestrator/validator.py's
            # _check_stale_approvals()) -- both are set here first.
            chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
            chugel.record_publish_commit(mid, "a" * 40)
            decision = dict(_decision())
            decision["approved_for"] = {"head_sha": "a" * 40}
            chugel.decide_gate(mid, "merge_authorization", decision)
            chugel.transition(mid, "BLOCKED", actor="chugel", reason="test setup")
        chugel.transition(mid, prior_state, actor="chugel", reason="test setup")
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="synthetic block")

    def test_resumes_each_of_the_four_supported_prior_states(self):
        for prior in ("PUBLISHING", "CI_PENDING", "MERGE_AWAITING_AUTHORIZATION", "MERGING"):
            with self.subTest(prior=prior):
                mid = self._fresh_mission()
                self._blocked_from(mid, prior)
                record = resume_from_blocked(mid, _decision())
                self.assertEqual(record["state"], prior)

    def test_rejects_resume_from_a_state_not_in_v1_scope(self):
        mid = self._fresh_mission()
        self._blocked_from(mid, "VERIFYING")
        with self.assertRaises(ResumeNotEligible):
            resume_from_blocked(mid, _decision())

    def test_rejects_when_not_currently_blocked(self):
        mid = self._fresh_mission()
        with self.assertRaises(ResumeNotEligible):
            resume_from_blocked(mid, _decision())

    def test_rejects_non_jose_attribution(self):
        mid = self._fresh_mission()
        self._blocked_from(mid, "PUBLISHING")
        with self.assertRaises(MissionWriteError):
            resume_from_blocked(mid, _decision(decided_by="not-jose"))

    def test_never_called_without_an_explicit_decision_this_turn(self):
        """No automatic-retry path exists: resume_from_blocked() always
        requires a caller-supplied decision object -- there is no
        zero-argument or state-polling variant anywhere in this module."""
        import inspect
        sig = inspect.signature(resume_from_blocked)
        self.assertEqual(list(sig.parameters), ["mission_id", "decision"])


if __name__ == "__main__":
    unittest.main()
