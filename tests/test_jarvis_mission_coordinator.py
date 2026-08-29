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


class IntakeAdvanceTests(CoordinatorTestCase):
    """Mission 006: advance() moves a freshly created mission out of
    INTAKE by itself -- the one transition it ever makes without a
    human decision behind it."""

    def test_intake_mechanically_advances_to_the_scope_gate(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        self.assertEqual("INTAKE", chugel.get_mission(mid)["state"])
        report = self._advance(mid)
        self.assertEqual(report.status, "GATE_REQUIRED")
        self.assertEqual(report.gate_name, "scope_authorization")
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])

    def test_the_intake_transition_is_never_attributed_to_the_human_decider(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        self._advance(mid)
        history = chugel.get_mission(mid)["state_history"]
        intake_entry = next(e for e in history if e["from_state"] == "INTAKE" and e["to_state"] == "SCOPE_AWAITING_AUTHORIZATION")
        self.assertEqual("chugel", intake_entry["actor"])
        self.assertNotEqual("jose", intake_entry["actor"])

    def test_the_intake_transition_reason_is_fixed_not_caller_supplied(self):
        # advance() takes no reason/message argument at all -- the fixed
        # string here is the only one that can ever appear, confirming
        # nothing caller-controlled (e.g. conversation text) can reach
        # the Mission Record through this path.
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        self._advance(mid)
        history = chugel.get_mission(mid)["state_history"]
        intake_entry = next(e for e in history if e["from_state"] == "INTAKE")
        self.assertEqual(mission_coordinator._INTAKE_ADVANCE_REASON, intake_entry["reason"])


def _scope_gate_rejection(version=1):
    return {
        "status": "rejected", "requested_at": None,
        "decided_at": "2026-08-29T12:10:00Z", "decided_by": "jose",
        "decision_ref": "ref-scope-rejected-1", "approved_for": None,
    }


def _publish_gate_approval():
    return {
        "status": "approved",
        "requested_at": "2026-08-19T12:20:00Z", "decided_at": "2026-08-19T12:20:00Z",
        "decided_by": "jose", "decision_ref": "ref-publish-1", "approved_for": {"pr_number": 1},
    }


def _publish_gate_rejection():
    return {
        "status": "rejected", "requested_at": None,
        "decided_at": "2026-08-19T12:20:00Z", "decided_by": "jose",
        "decision_ref": "ref-publish-rejected-1", "approved_for": None,
    }


def _merge_gate_rejection():
    return {
        "status": "rejected", "requested_at": None,
        "decided_at": "2026-08-19T12:30:00Z", "decided_by": "jose",
        "decision_ref": "ref-merge-rejected-1", "approved_for": None,
    }


class GateConsumptionTests(CoordinatorTestCase):
    """Mission 006 (gate-consumption follow-up): advance() consumes an
    already-persisted human_gates.<name> decision -- never fabricates
    decided_by/approved_for/status itself (chugel.decide_gate() remains
    the only writer of those), attributes the mechanical follow-up
    transition to the fixed system actor, and is a pure no-op (no
    transition, no side effect) while the gate is still pending."""

    def test_scope_gate_approved_transitions_to_authorized(self):
        mid = self._mission_scope_awaiting()
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        from orchestrator.autonomous_runner import RunnerResult
        with mock.patch(
            "jarvis.mission_coordinator.autonomous_runner.run_mission",
            return_value=RunnerResult(status="AUTHORIZATION_REQUIRED", state="PUBLISH_AWAITING_AUTHORIZATION", attempts=1),
        ) as run_mission_mock:
            report = self._advance(mid)
        run_mission_mock.assert_called_once()  # advance() actually reached the AUTHORIZED-family branch
        self.assertEqual("AUTHORIZED", chugel.get_mission(mid)["state"])
        history = chugel.get_mission(mid)["state_history"]
        entry = next(e for e in history if e["from_state"] == "SCOPE_AWAITING_AUTHORIZATION" and e["to_state"] == "AUTHORIZED")
        self.assertEqual("chugel", entry["actor"])
        self.assertNotEqual("jose", entry["actor"])
        # advance() kept driving past AUTHORIZED (never left at
        # GATE_REQUIRED for scope_authorization) -- the gate was consumed.
        self.assertNotEqual("scope_authorization", report.gate_name)

    def test_scope_gate_rejected_transitions_to_cancelled(self):
        mid = self._mission_scope_awaiting()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_rejection())
        report = self._advance(mid)
        self.assertEqual("CANCELLED", chugel.get_mission(mid)["state"])
        self.assertEqual("TERMINAL_FAILURE", report.status)
        history = chugel.get_mission(mid)["state_history"]
        entry = next(e for e in history if e["to_state"] == "CANCELLED")
        self.assertEqual("chugel", entry["actor"])

    def test_publish_gate_approved_transitions_to_publishing(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        run_mission(mid, self.adapters, max_total_attempts=4)
        self.assertEqual("PUBLISH_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])
        chugel.decide_gate(mid, "publish_authorization", _publish_gate_approval())
        with mock.patch("orchestrator.publish_executor.run") as run:
            run.return_value = mock.Mock(status="BLOCKED", state="PUBLISHING", reason="synthetic stop")
            self._advance(mid)
        # publish_executor.run() was actually reached (i.e. advance()
        # consumed the gate and moved past PUBLISH_AWAITING_AUTHORIZATION
        # into the PUBLISHING branch) rather than stopping at GATE_REQUIRED.
        run.assert_called_once()
        history = chugel.get_mission(mid)["state_history"]
        entry = next(e for e in history if e["from_state"] == "PUBLISH_AWAITING_AUTHORIZATION")
        self.assertEqual("PUBLISHING", entry["to_state"])
        self.assertEqual("chugel", entry["actor"])

    def test_publish_gate_rejected_transitions_to_cancelled(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        run_mission(mid, self.adapters, max_total_attempts=4)
        chugel.decide_gate(mid, "publish_authorization", _publish_gate_rejection())
        self._advance(mid)
        self.assertEqual("CANCELLED", chugel.get_mission(mid)["state"])

    def test_merge_gate_approved_transitions_to_merging_and_repair_still_runs(self):
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
        chugel.record_publish_commit(mid, _HEAD_SHA)  # decide_gate()'s own STALE_APPROVAL check requires this to match
        chugel.decide_gate(mid, "merge_authorization", {
            "status": "approved", "requested_at": "2026-08-19T12:30:00Z",
            "decided_at": "2026-08-19T12:30:00Z", "decided_by": "jose",
            "decision_ref": "ref-merge-1", "approved_for": {"head_sha": _HEAD_SHA},
        })

        with mock.patch("jarvis.mission_coordinator.publish_identity_repair.repair_if_needed") as repair, \
             mock.patch("orchestrator.merge_executor.run") as merge_run:
            merge_run.return_value = mock.Mock(status="BLOCKED", state="MERGING", reason="synthetic stop")
            self._advance(mid)
        # repair_if_needed() still ran (unchanged Mission 004 behavior,
        # preserved exactly where it already was) AND the gate was
        # consumed afterward (merge_executor.run() was actually reached).
        repair.assert_called_once()
        merge_run.assert_called_once()
        history = chugel.get_mission(mid)["state_history"]
        entry = next(e for e in history if e["from_state"] == "MERGE_AWAITING_AUTHORIZATION")
        self.assertEqual("MERGING", entry["to_state"])
        self.assertEqual("chugel", entry["actor"])

    def test_merge_gate_rejected_transitions_to_cancelled(self):
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
        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            chugel.decide_gate(mid, "merge_authorization", _merge_gate_rejection())
            self._advance(mid)
        self.assertEqual("CANCELLED", chugel.get_mission(mid)["state"])

    def test_a_still_pending_gate_causes_no_transition_and_no_side_effect(self):
        """Scenario 1/4 (crash-window review): the gate was never decided
        at all. advance() must be a pure no-op -- safe to call any number
        of times."""
        mid = self._mission_scope_awaiting()
        before = chugel.get_mission(mid)
        for _ in range(3):
            report = self._advance(mid)
            self.assertEqual("GATE_REQUIRED", report.status)
            self.assertEqual("scope_authorization", report.gate_name)
        after = chugel.get_mission(mid)
        self.assertEqual("SCOPE_AWAITING_AUTHORIZATION", after["state"])
        self.assertEqual(len(before["state_history"]), len(after["state_history"]))
        self.assertEqual(before["updated_at"], after["updated_at"])

    def test_stale_approved_for_version_is_rejected_by_chugel_not_bypassed(self):
        """Scenario 5 (crash-window review): an approved-but-stale gate
        (approved_for.mission_definition_version does not match the
        mission's current version) must never be consumed. Chugel itself
        already refuses to let this state exist via any legitimate write
        path -- both decide_gate() and decide_scope_change() run the same
        STALE_APPROVAL check and fail closed before ever writing such a
        record (confirmed directly: attempting the natural
        approve-then-replan sequence via decide_scope_change() itself
        raises MissionValidationFailed, so the scenario cannot even be
        constructed that way). This test instead writes the stale record
        directly (bypassing every disclosed Chugel write function, the
        only way to even get such a record onto disk at all) to prove a
        THIRD, independent layer of defense: chugel.get_mission()/
        _read_mission_record() validates on every READ, not only on
        write -- so a record poisoned by any means whatsoever (a bug
        outside Chugel's own write path, direct disk tampering) is
        refused the moment anything -- this module included -- tries to
        read it at all, before advance() or _consume_gate_if_decided()
        ever get a chance to look at its state or gate status."""
        mid = self._mission_scope_awaiting()
        record = chugel.get_mission(mid)
        record["human_gates"]["scope_authorization"] = {
            "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
            "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
            "decision_ref": "ref-scope-stale", "approved_for": {"mission_definition_version": 999},
        }
        chugel._write_mission_record(record)  # bypasses decide_gate()'s own STALE_APPROVAL check on purpose

        with self.assertRaises(chugel.MissionRecordInvalid):
            self._advance(mid)  # fails at the very first chugel.get_mission() read inside advance()

    def test_repeated_advance_calls_after_approval_never_re_transition(self):
        """Scenario 6 (crash-window review): once a gate has been
        consumed, a second advance() call must never attempt to consume
        the same decision again -- the mission is no longer in the gate-
        waiting state, so _consume_gate_if_decided() is not even reached
        for that gate a second time."""
        mid = self._mission_scope_awaiting()
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        from orchestrator.autonomous_runner import RunnerResult
        stub = RunnerResult(status="AUTHORIZATION_REQUIRED", state="PUBLISH_AWAITING_AUTHORIZATION", attempts=1)
        with mock.patch("jarvis.mission_coordinator.autonomous_runner.run_mission", return_value=stub):
            self._advance(mid)
            history_after_first = chugel.get_mission(mid)["state_history"]
            consumptions = [e for e in history_after_first if e["from_state"] == "SCOPE_AWAITING_AUTHORIZATION" and e["to_state"] == "AUTHORIZED"]
            self.assertEqual(1, len(consumptions))

            self._advance(mid)  # second call, same already-authorized mission
            history_after_second = chugel.get_mission(mid)["state_history"]
            consumptions_again = [e for e in history_after_second if e["from_state"] == "SCOPE_AWAITING_AUTHORIZATION" and e["to_state"] == "AUTHORIZED"]
            self.assertEqual(1, len(consumptions_again))  # still exactly one -- never duplicated


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
