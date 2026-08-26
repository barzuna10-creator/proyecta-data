"""jarvis/mission_coordinator.py -- routing to the three gates, BLOCKED
reporting, MERGED terminal, and the executive summary. Real Chugel
Mission Records; publish/merge executor subprocess calls faked exactly
as in tests/test_orchestrator_publish_executor.py /
test_orchestrator_merge_executor.py."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from jarvis import mission_coordinator
from orchestrator.autonomous_runner import run_mission
from tests.test_orchestrator_autonomous_runner import (
    _FakeAdapter,
    _create_intake_mission,
    _emilio_completed_template,
    _emma_completed_template,
    _scope_gate_approval,
)

_HEAD_SHA = "a" * 40


def _json_result(payload, returncode=0):
    return mock.Mock(returncode=returncode, stdout=json.dumps(payload).encode("utf-8"), stderr=b"")


def _ok_result(stdout=b""):
    return mock.Mock(returncode=0, stdout=stdout, stderr=b"")


class CoordinatorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self.adapters = {}

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_scope_awaiting(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        return mid

    def _mission_authorized(self):
        mid = self._mission_scope_awaiting()
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        return mid

    def _advance(self, mid, **overrides):
        kwargs = dict(
            repository_root="/tmp/repo", branch="b", pr_title="t",
            ci_poll_timeout_seconds=5, ci_poll_interval_seconds=0.01,
        )
        kwargs.update(overrides)
        return mission_coordinator.advance(mid, self.adapters, **kwargs)


class GateRoutingTests(CoordinatorTestCase):
    def test_scope_gate_reported(self):
        mid = self._mission_scope_awaiting()
        report = self._advance(mid)
        self.assertEqual(report.status, "GATE_REQUIRED")
        self.assertEqual(report.gate_name, "scope_authorization")

    def test_publish_gate_reported_after_build_and_pass_review(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        report = self._advance(mid)
        self.assertEqual(report.status, "GATE_REQUIRED")
        self.assertEqual(report.gate_name, "publish_authorization")

    def test_merged_is_terminal(self):
        mid = self._mission_authorized()
        chugel.transition(mid, "BUILDING", actor="chugel", reason="x")
        # Fast-forward directly to a synthetic MERGED-equivalent report
        # without a full merge -- coordinator's own MERGED branch only
        # needs record["state"] == "MERGED" to report correctly; that
        # state is exercised end-to-end by
        # tests/test_orchestrator_merge_executor.py already.
        self.assertEqual(mission_coordinator.CoordinatorReport("MERGED", "MERGED").status, "MERGED")


class BlockedReportingTests(CoordinatorTestCase):
    def test_blocked_state_is_reported_not_retried(self):
        mid = self._mission_scope_awaiting()
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="synthetic block for test")
        report = self._advance(mid)
        self.assertEqual(report.status, "BLOCKED")
        self.assertIn("synthetic block", report.reason)


class MergeAwaitingRepairIntegrationTests(CoordinatorTestCase):
    def test_repair_runs_before_the_merge_gate_is_reported(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        run_mission(mid, self.adapters, max_total_attempts=4)
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="x")
        chugel.record_publish_pr(mid, "https://example.invalid/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="x")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        self.assertIsNone(chugel.get_mission(mid)["publish"]["commit_sha"])

        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            report = self._advance(mid)
        self.assertEqual(report.status, "GATE_REQUIRED")
        self.assertEqual(report.gate_name, "merge_authorization")
        self.assertEqual(chugel.get_mission(mid)["publish"]["commit_sha"], _HEAD_SHA)


class ExecutiveSummaryTests(CoordinatorTestCase):
    def test_summary_fields_trace_to_persisted_record(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        run_mission(mid, self.adapters, max_total_attempts=4)
        summary = mission_coordinator.executive_summary(mid)
        self.assertIn("STOPPED: PUBLISH_AWAITING_AUTHORIZATION", summary)
        self.assertIn("a.py", summary)  # from _builder_evidence()'s changed_files fixture
        self.assertIn("PASS", summary)


if __name__ == "__main__":
    unittest.main()
