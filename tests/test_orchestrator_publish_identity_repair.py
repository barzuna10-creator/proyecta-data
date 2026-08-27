"""Real-persistence tests for orchestrator/publish_identity_repair.py.
Reuses tests/test_orchestrator_autonomous_runner.py's proven fixture
helpers (real Chugel + real wiring/autonomous_runner, only the provider
adapter is fake) to reach PUBLISH_AWAITING_AUTHORIZATION, then drives the
publish/merge-pipeline states by hand (no automation for those exists in
autonomous_runner.py) exactly as orchestrator/publish_executor.py would."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from orchestrator import publish_identity_repair
from orchestrator.autonomous_runner import run_mission
from tests.test_orchestrator_autonomous_runner import (
    _FakeAdapter,
    _create_intake_mission,
    _emilio_completed_template,
    _emma_completed_template,
    _scope_gate_approval,
)

_HEAD_SHA = "a" * 40


class RepairTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_authorized(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        return mid

    def _mission_at_merge_awaiting_no_commit_sha(self, head_sha=_HEAD_SHA):
        """Reaches MERGE_AWAITING_AUTHORIZATION with publish.pr_number set
        but publish.commit_sha still null -- exactly the crash window
        between the CI_PENDING->MERGE_AWAITING_AUTHORIZATION transition
        and the original record_publish_commit() call."""
        mid = self._mission_authorized()
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        }
        run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(chugel.get_mission(mid)["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="publish authorized")
        chugel.record_publish_pr(mid, "https://example.invalid/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
        return mid


class RepairSuccessTests(RepairTestCase):
    def test_restores_commit_sha_when_live_head_matches_the_reviewed_identity(self):
        mid = self._mission_at_merge_awaiting_no_commit_sha()
        with mock.patch.object(publish_identity_repair, "_live_pr_head_sha", return_value=_HEAD_SHA):
            performed = publish_identity_repair.repair_if_needed(mid)
        self.assertTrue(performed)
        record = chugel.get_mission(mid)
        self.assertEqual(record["publish"]["commit_sha"], _HEAD_SHA)
        self.assertEqual(record["state"], "MERGE_AWAITING_AUTHORIZATION")

    def test_no_op_when_commit_sha_already_recorded(self):
        mid = self._mission_at_merge_awaiting_no_commit_sha()
        chugel.record_publish_commit(mid, _HEAD_SHA)
        with mock.patch.object(publish_identity_repair, "_live_pr_head_sha") as live:
            performed = publish_identity_repair.repair_if_needed(mid)
        self.assertFalse(performed)
        live.assert_not_called()


class RepairFailsClosedTests(RepairTestCase):
    def test_live_head_mismatch_blocks_and_does_not_infer(self):
        """The exact adversarial scenario Emma's review named: a commit
        pushed during the crash gap must never be silently adopted as
        the reviewed identity."""
        mid = self._mission_at_merge_awaiting_no_commit_sha()
        with mock.patch.object(publish_identity_repair, "_live_pr_head_sha", return_value="f" * 40):
            performed = publish_identity_repair.repair_if_needed(mid)
        self.assertFalse(performed)
        record = chugel.get_mission(mid)
        self.assertIsNone(record["publish"]["commit_sha"])
        self.assertEqual(record["state"], "BLOCKED")

    def test_patch_mode_artifact_has_no_durable_commit_identity_and_blocks(self):
        mid = self._mission_authorized()
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        }
        run_mission(mid, adapters, max_total_attempts=4)
        # Simulate a patch-mode build by overwriting the artifact in place
        # (record_builder_evidence() is append-only; this test only needs
        # a schema-valid, patch-mode entry already present, not a second
        # real dispatch).
        record = chugel.get_mission(mid)
        mutated = dict(record)
        patch_artifact = {"mode": "patch", "commit_sha": None,
                           "patch_path": "/tmp/x.patch", "patch_sha256": "0" * 64, "patch_byte_size": 10}

        builder_entries = list(mutated["builder_evidence"])
        builder_entries[0] = {**builder_entries[0], "artifact": patch_artifact}
        mutated["builder_evidence"] = builder_entries

        # validate_mission_record() cross-checks reviewer_evidence's
        # confirmed identity against the matching builder attempt's own
        # artifact -- both must be updated together, or this synthetic
        # record fails validity on the very next read (which is a
        # correct, unrelated invariant this test must not fight).
        reviewer_entries = list(mutated["reviewer_evidence"])
        reviewer_entries[0] = {
            **reviewer_entries[0],
            "artifact_identity_confirmed_at_start": patch_artifact,
            "artifact_identity_confirmed_before_conclusion": patch_artifact,
        }
        mutated["reviewer_evidence"] = reviewer_entries
        chugel._write_mission_record(mutated)

        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="publish authorized")
        chugel.record_publish_pr(mid, "https://example.invalid/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")

        with mock.patch.object(publish_identity_repair, "_live_pr_head_sha") as live:
            performed = publish_identity_repair.repair_if_needed(mid)
        self.assertFalse(performed)
        live.assert_not_called()
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")

    def test_wrong_state_raises(self):
        mid = self._mission_authorized()
        with self.assertRaises(ValueError):
            publish_identity_repair.repair_if_needed(mid)


if __name__ == "__main__":
    unittest.main()
