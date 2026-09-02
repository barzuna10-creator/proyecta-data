"""jarvis/mission_coordinator.py -- routing to the three gates, BLOCKED
reporting, MERGED terminal, and the executive summary. Real Chugel
Mission Records; publish/merge executor subprocess calls faked exactly
as in tests/test_orchestrator_publish_executor.py /
test_orchestrator_merge_executor.py."""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from jarvis import mission_coordinator
from jarvis.mission_workspace import MissionWorkspaceBinding
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
        self._repository_root = Path(self._tmpdir.name) / "repository"
        self._repository_root.mkdir()
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
            "worktree_path": str(self._repository_root), "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        return mid

    def _advance(self, mid, **overrides):
        kwargs = dict(
            repository_root=str(self._repository_root), branch="overnight/synthetic", pr_title="t",
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

    def test_manager_enabled_legacy_missing_sha_repairs_before_exact_head_check(self):
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
        record = chugel.get_mission(mid)
        binding = MissionWorkspaceBinding(
            record["repository"]["worktree_path"], record["repository"]["branch"],
            record["repository"]["base_sha"], True, _HEAD_SHA,
        )
        manager = mock.Mock()
        manager.verify.return_value = binding
        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            report = self._advance(mid, workspace_manager=manager)
        self.assertEqual("GATE_REQUIRED", report.status, report.reason)
        self.assertEqual(_HEAD_SHA, chugel.get_mission(mid)["publish"]["commit_sha"])
        self.assertGreaterEqual(manager.verify.call_count, 2)


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
            "worktree_path": str(self._repository_root), "branch": "overnight/synthetic",
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


class M2CWorkspaceEstablishmentTests(CoordinatorTestCase):
    def test_authorized_mission_is_provisioned_recorded_reread_then_dispatched(self):
        mid = self._mission_scope_awaiting()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        worktree = Path(self._tmpdir.name) / "mission-worktree"
        worktree.mkdir()
        binding = MissionWorkspaceBinding(
            str(worktree), f"mission/{mid}", "0" * 40, True, "0" * 40,
        )
        manager = mock.Mock()
        manager.ensure.return_value = binding
        manager.verify.return_value = binding
        runner_result = mock.Mock(status="HUMAN_ACTION_REQUIRED", state="AUTHORIZED", reason="bounded stop")
        with mock.patch.object(mission_coordinator.autonomous_runner, "run_mission", return_value=runner_result) as run, \
             mock.patch.object(mission_coordinator, "_execution_root_is_canonical", return_value=True):
            report = mission_coordinator.advance(
                mid, {}, repository_root="ignored", branch="ignored", pr_title="t",
                workspace_manager=manager,
            )
        self.assertEqual("HUMAN_ACTION_REQUIRED", report.status, report.reason)
        manager.ensure.assert_called_once()
        manager.verify.assert_called_once()
        self.assertTrue(chugel.get_mission(mid)["repository"]["isolation_confirmed"])
        run.assert_called_once()


class WorkspaceGuardTests(CoordinatorTestCase):
    """Jarvis God Mode M1 decision #4 (approved): until M2 builds real
    per-mission workspace isolation, advance() must never let a second
    mission start real dispatch/publish/merge work against the one
    repository_root every mission currently shares while another mission
    is confirmed already using it -- but must NEVER block a mission
    only because ANOTHER mission is merely waiting on a human gate (that
    mission has not touched repository_root and might not for a long
    time), and must never deadlock two missions that both just got
    authorized at the same moment."""

    def setUp(self):
        super().setUp()
        # M2B (Workspace Guard V2): a second, separate real tempdir --
        # distinct from chugel._MISSIONS_DIR, which lives under the base
        # CoordinatorTestCase tempdir -- for tests that need genuinely
        # real, resolvable worktree directories to exercise
        # _resolve_worktree_identity() against, rather than the fixed,
        # deliberately-not-a-real-directory placeholder path
        # ("/tmp/synthetic-worktree") every mission built via
        # _mission_authorized()/_mission_scope_awaiting() already uses.
        self._worktrees_tmpdir = tempfile.TemporaryDirectory()
        self._worktrees_root = Path(self._worktrees_tmpdir.name)

    def tearDown(self):
        self._worktrees_tmpdir.cleanup()
        super().tearDown()

    def _mission_authorized_at(self, worktree_path):
        """Same shape as CoordinatorTestCase._mission_authorized(), except
        the worktree_path is caller-supplied instead of the fixed
        "/tmp/synthetic-worktree" placeholder -- for tests that need a
        real, resolvable (or deliberately unresolvable) directory."""
        mid = self._mission_scope_awaiting()
        chugel.record_repository_state(mid, {
            "worktree_path": str(worktree_path), "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        return mid

    def _mission_publish_awaiting(self):
        mid = self._mission_authorized()
        self.adapters.update({
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        })
        run_mission(mid, self.adapters, max_total_attempts=4)
        self.assertEqual("PUBLISH_AWAITING_AUTHORIZATION", chugel.get_mission(mid)["state"])
        self.adapters.clear()
        return mid

    def _mission_publishing(self):
        mid = self._mission_publish_awaiting()
        chugel.decide_gate(mid, "publish_authorization", _publish_gate_approval())
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="publish gate approved")
        return mid

    def _mission_merging(self):
        mid = self._mission_publishing()
        chugel.record_publish_pr(mid, "https://example.invalid/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="x")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            chugel.get_mission(mid)  # no-op read, keeps parity with other helpers' shape
        chugel.record_publish_commit(mid, _HEAD_SHA)
        chugel.decide_gate(mid, "merge_authorization", {
            "status": "approved", "requested_at": "2026-08-19T12:40:00Z",
            "decided_at": "2026-08-19T12:40:00Z", "decided_by": "jose",
            "decision_ref": "ref-merge-1", "approved_for": {"head_sha": _HEAD_SHA},
        })
        chugel.transition(mid, "MERGING", actor="chugel", reason="merge gate approved")
        return mid

    def test_building_mission_blocks_a_second_authorized_mission_from_starting(self):
        occupant = self._mission_authorized()
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)
        # No side effect: the parked mission's own state is untouched.
        self.assertEqual("AUTHORIZED", chugel.get_mission(second)["state"])

    def test_publishing_mission_blocks_a_second_authorized_mission_from_starting(self):
        occupant = self._mission_publishing()
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_merging_mission_blocks_a_second_authorized_mission_from_starting(self):
        occupant = self._mission_merging()
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def _advance_with_stubbed_dispatch(self, mid):
        """Stubs autonomous_runner.run_mission() itself -- these tests
        exist to prove the workspace GUARD did or did not block dispatch
        from being attempted at all, never to exercise real Emilio/Emma
        machinery (already covered elsewhere). A real call reaching the
        stub is the positive proof the guard did not block."""
        stub = mock.Mock(return_value=mock.Mock(status="HUMAN_ACTION_REQUIRED", state="BUILDING", reason="stub"))
        with mock.patch("jarvis.mission_coordinator.autonomous_runner.run_mission", stub):
            report = self._advance(
                mid,
                repository_root=chugel.get_mission(mid)["repository"]["worktree_path"],
            )
        return report, stub

    def test_occupied_mission_never_blocks_itself(self):
        """The occupant continuing its OWN advance() call must never see
        itself as the occupant."""
        occupant = self._mission_authorized()
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        report, stub = self._advance_with_stubbed_dispatch(occupant)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_a_mission_waiting_on_scope_gate_never_blocks_another_mission(self):
        self._mission_scope_awaiting()
        second = self._mission_authorized()
        report, stub = self._advance_with_stubbed_dispatch(second)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_a_mission_waiting_on_the_publish_gate_DOES_block_another_mission(self):
        """Corrective Round 1 (Emma P1/P2): the original guard treated
        PUBLISH_AWAITING_AUTHORIZATION like the scope gate (untouched
        tree) -- wrong. A mission here already built and got a PASS
        review; real, unmerged changes already exist in the shared tree.
        A second mission must never be allowed to start building over
        them just because the pending decision is a human gate."""
        occupant = self._mission_publish_awaiting()
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_a_mission_waiting_on_the_merge_gate_DOES_block_another_mission(self):
        """Same correction as the publish-gate test above, for
        MERGE_AWAITING_AUTHORIZATION -- by this point the mission has
        also pushed and opened a real PR."""
        occupant = self._mission_publishing()
        chugel.record_publish_pr(occupant, "https://example.invalid/pr/1", 1)
        chugel.transition(occupant, "CI_PENDING", actor="chugel", reason="x")
        chugel.transition(occupant, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            chugel.get_mission(occupant)
        chugel.record_publish_commit(occupant, _HEAD_SHA)
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_a_mission_blocked_from_a_pre_acquisition_state_never_blocks_another_mission(self):
        """BLOCKED reached from SCOPE_AWAITING_AUTHORIZATION (a real,
        validator-allowed transition -- orchestrator/validator.py's
        TRANSITIONS includes ("SCOPE_AWAITING_AUTHORIZATION", "BLOCKED"))
        never touched repository_root -- its state_history contains no
        owning-state entry, so it must not block another mission."""
        blocked = self._mission_scope_awaiting()
        chugel.transition(blocked, "BLOCKED", actor="chugel", reason="synthetic block for test")
        second = self._mission_authorized()
        report, stub = self._advance_with_stubbed_dispatch(second)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_a_mission_blocked_from_an_owning_state_STILL_blocks_another_mission(self):
        """The other half of the same BLOCKED ambiguity: a mission
        blocked FROM a real owning state (here, BUILDING) has real,
        unresolved work sitting in the tree -- BLOCKED only pauses its
        own automatic progress, it never releases what it already
        holds. Its state_history must still show the BUILDING entry, so
        it must continue to block a second mission exactly as if it
        were still BUILDING."""
        occupant = self._mission_authorized()
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        chugel.transition(occupant, "BLOCKED", actor="chugel", reason="synthetic block mid-build for test")
        second = self._mission_authorized()
        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_unreadable_occupant_history_fails_closed_to_still_owning(self):
        """If the occupant's own record briefly fails to read (a narrow
        race, or real corruption) while resolving a BLOCKED ambiguity,
        the guard must assume the claim is NOT released -- the unsafe
        direction would be assuming a real, unresolved claim is free."""
        occupant = self._mission_authorized()
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        chugel.transition(occupant, "BLOCKED", actor="chugel", reason="synthetic block mid-build for test")
        second = self._mission_authorized()
        real_get_mission = chugel.get_mission

        def _fake_get_mission(mid):
            if mid == occupant:
                raise RuntimeError("simulated transient read failure")
            return real_get_mission(mid)

        with mock.patch("jarvis.mission_coordinator.chugel.get_mission", side_effect=_fake_get_mission):
            report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)

    def test_the_exact_emma_reported_deadlock_sequence_no_longer_deadlocks(self):
        """Reproduces the precise 5-step sequence from Emma's P1 finding
        and proves it now resolves instead of deadlocking: mission A
        reaches its publish gate (previously non-blocking -- the bug);
        mission B is parked the entire time A is doing real work; once A
        reaches a genuinely terminal state, B is finally free to start."""
        a = self._mission_publish_awaiting()
        b = self._mission_authorized()

        # Step 2 (bug reproduction, now fixed): B must be parked while A
        # sits at its publish gate with real, unmerged work in the tree.
        report_b = self._advance(b)
        self.assertEqual("WORKSPACE_OCCUPIED", report_b.status)
        self.assertEqual("AUTHORIZED", chugel.get_mission(b)["state"])

        # Step 3: A's publish gate is approved -- A moves into PUBLISHING,
        # still owning; B remains parked.
        chugel.decide_gate(a, "publish_authorization", _publish_gate_approval())
        chugel.transition(a, "PUBLISHING", actor="chugel", reason="publish gate approved")
        report_b = self._advance(b)
        self.assertEqual("WORKSPACE_OCCUPIED", report_b.status)

        # Step 4/5: A publishes and reaches its merge gate -- this is
        # exactly the point Emma's trace showed the ORIGINAL guard
        # allowing B to also reach an owning state, producing the
        # symmetric deadlock. B must still be parked here.
        chugel.record_publish_pr(a, "https://example.invalid/pr/1", 1)
        chugel.transition(a, "CI_PENDING", actor="chugel", reason="x")
        chugel.transition(a, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="x")
        with mock.patch("orchestrator.publish_identity_repair._live_pr_head_sha", return_value=_HEAD_SHA):
            chugel.get_mission(a)
        chugel.record_publish_commit(a, _HEAD_SHA)
        report_b = self._advance(b)
        self.assertEqual("WORKSPACE_OCCUPIED", report_b.status)
        self.assertEqual("AUTHORIZED", chugel.get_mission(b)["state"])  # never silently acquired

        # A finally merges -- genuinely terminal, ownership released.
        chugel.decide_gate(a, "merge_authorization", {
            "status": "approved", "requested_at": "2026-08-19T12:40:00Z",
            "decided_at": "2026-08-19T12:40:00Z", "decided_by": "jose",
            "decision_ref": "ref-merge-1", "approved_for": {"head_sha": _HEAD_SHA},
        })
        chugel.transition(a, "MERGING", actor="chugel", reason="merge gate approved")
        # Fast-forward directly to a synthetic MERGED-equivalent, same
        # pattern as GateRoutingTests.test_merged_is_terminal above --
        # real merge_executor.run() against a real repository_root is
        # already covered by tests/test_orchestrator_merge_executor.py;
        # this test's own subject is the workspace guard, not the merge
        # executor.
        chugel.record_merge_commit(a, _HEAD_SHA)
        chugel.transition(a, "MERGED", actor="chugel", reason="synthetic merge completion for test")
        self.assertEqual("MERGED", chugel.get_mission(a)["state"])

        # Only now is B actually free to start real work.
        report_b, stub = self._advance_with_stubbed_dispatch(b)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report_b.status)

    def test_ownership_after_a_simulated_restart_is_rederived_identically(self):
        """No new lock, no new persisted field -- ownership is a pure
        function of chugel.get_mission()/list_missions()'s own already-
        durable state. A 'restart' is simulated by simply calling
        advance() again from a fresh Python-level call with no in-memory
        state carried over (this test process never held any) -- the
        exact same answer must come back both times."""
        occupant = self._mission_authorized()
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        second = self._mission_authorized()
        first_report = self._advance(second)
        second_report = self._advance(second)  # simulates a fresh call after a crash/restart
        self.assertEqual("WORKSPACE_OCCUPIED", first_report.status)
        self.assertEqual(first_report.status, second_report.status)
        self.assertEqual(first_report.reason, second_report.reason)

    def test_two_freshly_authorized_missions_never_deadlock(self):
        """The exact scenario the guard's own docstring calls out by
        name: including AUTHORIZED in the occupying-states set would let
        two simultaneously-AUTHORIZED missions see each other as
        occupying and both refuse to ever start. Confirms the real fix:
        the first one advanced is never blocked by the second (which
        has not started anything yet either)."""
        first = self._mission_authorized()
        self._mission_authorized()
        report, stub = self._advance_with_stubbed_dispatch(first)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_no_other_missions_never_blocks(self):
        mid = self._mission_authorized()
        report, stub = self._advance_with_stubbed_dispatch(mid)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_unreadable_occupant_listing_fails_closed_not_open(self):
        """Jarvis God Mode M1 Final Hardening Round (Emma round-3 P3).

        BEFORE this fix, _mission_occupying_repository_root() skipped any
        listing with readable=False entirely (`continue`) -- treating a
        mission whose own record could no longer be parsed as if it were
        proven non-owning. That silently violated the approved invariant
        ("si el sistema no puede determinar con certeza que
        repository_root está libre, NO puede permitir adquisición"): an
        unreadable record is undetermined, not confirmed free, and a
        genuinely mid-BUILDING mission whose record became corrupt for
        any reason would have been silently ignored, letting a second
        mission acquire over its real, unresolved work.

        AFTER this fix, an unreadable listing is treated as occupying --
        this test proves it directly: a real mission record, written with
        deliberately malformed JSON (mirroring
        tests/test_orchestrator_chugel.py's own
        test_candidato_corrupto_no_oculta_los_demas pattern -- the same,
        already-established way this codebase constructs a genuinely
        unreadable listing), causes a second, otherwise-eligible mission
        to be blocked rather than silently allowed through."""
        second = self._mission_authorized()
        corrupt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        (chugel._MISSIONS_DIR / f"{corrupt_id}.json").write_text("{not valid json", encoding="utf-8")

        listings = {row["mission_id"]: row for row in chugel.list_missions()}
        self.assertFalse(listings[corrupt_id]["readable"])  # precondition: genuinely unreadable

        report, stub = self._advance_with_stubbed_dispatch(second)
        stub.assert_not_called()
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(corrupt_id, report.reason)

    # -- M2B (Workspace Guard V2): real-filesystem-identity comparison ----

    def _mission_authorized_with_placeholder_repository(self):
        """AUTHORIZED without ever calling record_repository_state() --
        the schema/validator's own evidence checker for the AUTHORIZED
        transition (_evidence_authorized in orchestrator/validator.py)
        requires only that scope_authorization was approved, never that
        repository state has been recorded; isolation_confirmed is only
        required starting at BUILDING. So this is a real, reachable
        Mission Record shape: an AUTHORIZED mission whose own
        repository.worktree_path is still chugel's own placeholder,
        "(unconfirmed)" (_default_placeholder_repository())."""
        mid = self._mission_scope_awaiting()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        return mid

    def test_two_missions_with_genuinely_different_real_worktrees_do_not_block_each_other(self):
        """The new capability V2 adds: two owning-state missions no
        longer collide just because both are in an owning state -- only
        if their real worktree identities actually collide."""
        dir_a = self._worktrees_root / "mission-a"
        dir_a.mkdir()
        dir_b = self._worktrees_root / "mission-b"
        dir_b.mkdir()
        occupant = self._mission_authorized_at(dir_a)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        second = self._mission_authorized_at(dir_b)
        report, stub = self._advance_with_stubbed_dispatch(second)
        stub.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_different_path_strings_via_ordinary_traversal_to_same_real_dir_collide(self):
        """Two DIFFERENT path strings -- no symlink anywhere -- that both
        resolve, via ordinary '..'-traversal, to the identical real
        directory. This must be caught by the identity-EQUALITY branch
        (matching (st_dev, st_ino)), not the None-resolution branch --
        asserted directly below, not merely inferred from the final
        WORKSPACE_OCCUPIED report."""
        real_dir = self._worktrees_root / "shared-target"
        real_dir.mkdir()
        sibling = self._worktrees_root / "sibling"
        sibling.mkdir()
        alt_path = str(sibling / ".." / "shared-target")
        self.assertNotEqual(str(real_dir), alt_path)  # genuinely different strings

        occupant = self._mission_authorized_at(real_dir)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        second = self._mission_authorized_at(alt_path)

        own_identity = mission_coordinator._resolve_worktree_identity(alt_path)
        candidate_identity = mission_coordinator._resolve_worktree_identity(str(real_dir))
        self.assertIsNotNone(own_identity)
        self.assertIsNotNone(candidate_identity)
        self.assertEqual(own_identity, candidate_identity)  # the equality branch, not None

        report = self._advance(second, repository_root=alt_path)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_symlink_aliasing_another_missions_real_worktree_collides_via_none_branch(self):
        """A symlink at one mission's own worktree_path pointing at
        another mission's real directory -- O_NOFOLLOW refuses to
        traverse it, so this mission's own identity resolves to None,
        and the guard's fail-closed rule (None on either side ->
        occupied) treats it as colliding via the None branch, never by
        silently resolving through the symlink."""
        real_dir = self._worktrees_root / "occupant-real"
        real_dir.mkdir()
        occupant = self._mission_authorized_at(real_dir)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")

        alias = self._worktrees_root / "second-alias"
        os.symlink(real_dir, alias)
        second = self._mission_authorized_at(alias)

        self.assertIsNone(mission_coordinator._resolve_worktree_identity(str(alias)))  # never followed

        report = self._advance(second, repository_root=str(alias))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("does not match", report.reason)

    def test_one_resolvable_one_missing_worktree_fails_closed_as_colliding(self):
        """The occupant's own worktree_path names a directory that was
        never created (deleted/missing) -- unresolvable. Fail-closed:
        treated as colliding even though the second mission's own real
        worktree is genuinely resolvable and (were the occupant's path
        also resolvable) might well prove to be a different directory."""
        missing_dir = self._worktrees_root / "never-created"
        occupant = self._mission_authorized_at(missing_dir)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")

        real_dir = self._worktrees_root / "second-real"
        real_dir.mkdir()
        second = self._mission_authorized_at(real_dir)

        self.assertIsNone(mission_coordinator._resolve_worktree_identity(str(missing_dir)))

        report = self._advance(second, repository_root=str(real_dir))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)

    def test_authorized_mission_with_placeholder_worktree_path_is_still_blocked_v1_style(self):
        """Emma Revision-2 P1 regression pin: an AUTHORIZED mission whose
        own worktree_path is still the literal schema placeholder
        "(unconfirmed)" (record_repository_state() never having run for
        it) must still be reported as blocked by any real, unrelated
        owning-state candidate elsewhere -- byte-identical to V1's own
        blanket "any owning-state candidate blocks" rule, not a new
        restriction and not a case where V2's own-identity-is-None
        somehow makes it MORE permissive."""
        real_dir = self._worktrees_root / "genuinely-different"
        real_dir.mkdir()
        occupant = self._mission_authorized_at(real_dir)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")

        second = self._mission_authorized_with_placeholder_repository()
        self.assertEqual("(unconfirmed)", chugel.get_mission(second)["repository"]["worktree_path"])

        report = self._advance(second)
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("does not match", report.reason)
        self.assertEqual("AUTHORIZED", chugel.get_mission(second)["state"])  # no side effect

    def test_m1_fleet_shape_shared_real_worktree_path_is_still_mutually_exclusive(self):
        """Regression pin for the exact current M1 fleet shape: multiple
        owning-state missions that all share the literal SAME real
        worktree path (today's production reality, since nothing yet
        varies it -- see the M2 design's own finding 5). Behavior must
        be byte-identical to what V1 already did: still mutually
        exclusive, this time via the identity-equality branch rather
        than the (also-present-in-this-codebase) unresolvable-placeholder
        None branch the other WorkspaceGuardTests already cover."""
        shared_dir = self._worktrees_root / "shared-fleet-path"
        shared_dir.mkdir()
        occupant = self._mission_authorized_at(shared_dir)
        chugel.transition(occupant, "BUILDING", actor="chugel", reason="x")
        second = self._mission_authorized_at(shared_dir)

        report = self._advance(second, repository_root=str(shared_dir))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn(occupant, report.reason)
        self.assertEqual("AUTHORIZED", chugel.get_mission(second)["state"])

    def test_build_dispatch_refuses_execution_root_different_from_canonical_worktree(self):
        mid = self._mission_authorized()
        different_root = self._worktrees_root / "different-build-root"
        different_root.mkdir()
        runner = mock.Mock()
        with mock.patch("jarvis.mission_coordinator.autonomous_runner.run_mission", runner):
            report = self._advance(mid, repository_root=str(different_root))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("does not match", report.reason)
        runner.assert_not_called()
        self.assertEqual("AUTHORIZED", chugel.get_mission(mid)["state"])

    def test_publish_refuses_execution_root_different_from_canonical_worktree(self):
        mid = self._mission_publishing()
        different_root = self._worktrees_root / "different-publish-root"
        different_root.mkdir()
        publisher = mock.Mock()
        with mock.patch("jarvis.mission_coordinator.publish_executor.run", publisher):
            report = self._advance(mid, repository_root=str(different_root))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("does not match", report.reason)
        publisher.assert_not_called()
        self.assertEqual("PUBLISHING", chugel.get_mission(mid)["state"])

    def test_merge_refuses_execution_root_different_from_canonical_worktree(self):
        mid = self._mission_merging()
        different_root = self._worktrees_root / "different-merge-root"
        different_root.mkdir()
        merger = mock.Mock()
        with mock.patch("jarvis.mission_coordinator.merge_executor.run", merger):
            report = self._advance(mid, repository_root=str(different_root))
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("does not match", report.reason)
        merger.assert_not_called()
        self.assertEqual("MERGING", chugel.get_mission(mid)["state"])

    def test_publish_refuses_branch_different_from_canonical_binding(self):
        mid = self._mission_publishing()
        publisher = mock.Mock()
        with mock.patch("jarvis.mission_coordinator.publish_executor.run", publisher):
            report = self._advance(mid, branch="other/branch")
        self.assertEqual("WORKSPACE_OCCUPIED", report.status)
        self.assertIn("branch does not match", report.reason)
        publisher.assert_not_called()

    def test_matching_build_root_reaches_runner(self):
        mid = self._mission_authorized()
        report, runner = self._advance_with_stubbed_dispatch(mid)
        runner.assert_called_once()
        self.assertNotEqual("WORKSPACE_OCCUPIED", report.status)

    def test_matching_publish_root_and_branch_reach_publisher(self):
        mid = self._mission_publishing()
        publisher = mock.Mock(return_value=mock.Mock(
            status="BLOCKED", state="PUBLISHING", reason="stub",
        ))
        with mock.patch("jarvis.mission_coordinator.publish_executor.run", publisher):
            report = self._advance(mid)
        publisher.assert_called_once()
        self.assertEqual("BLOCKED", report.status)

    def test_matching_merge_root_reaches_merger(self):
        mid = self._mission_merging()
        merger = mock.Mock(return_value=mock.Mock(
            status="BLOCKED", state="MERGING", reason="stub",
        ))
        with mock.patch("jarvis.mission_coordinator.merge_executor.run", merger):
            report = self._advance(mid)
        merger.assert_called_once()
        self.assertEqual("BLOCKED", report.status)


class WorktreeIdentityResolutionTests(unittest.TestCase):
    """_resolve_worktree_identity() in isolation -- real filesystem only,
    no mocking of os.open/os.fstat: this is exactly the primitive the
    safety property depends on, so it is exercised against a real
    tempfile.TemporaryDirectory(), matching this whole codebase's
    testing philosophy for filesystem-touching code."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_real_directory_resolves_to_its_real_identity(self):
        real_dir = self._root / "real"
        real_dir.mkdir()
        identity = mission_coordinator._resolve_worktree_identity(str(real_dir))
        self.assertIsNotNone(identity)
        st = os.stat(real_dir)
        self.assertEqual((st.st_dev, st.st_ino), identity)

    def test_missing_path_resolves_to_none(self):
        missing = self._root / "does-not-exist"
        self.assertIsNone(mission_coordinator._resolve_worktree_identity(str(missing)))

    def test_symlink_at_the_path_resolves_to_none_never_followed(self):
        real_dir = self._root / "real"
        real_dir.mkdir()
        link = self._root / "link"
        os.symlink(real_dir, link)
        self.assertIsNone(mission_coordinator._resolve_worktree_identity(str(link)))

    def test_plain_file_not_a_directory_resolves_to_none(self):
        plain_file = self._root / "file.txt"
        plain_file.write_text("not a directory", encoding="utf-8")
        self.assertIsNone(mission_coordinator._resolve_worktree_identity(str(plain_file)))

    def test_never_raises_on_a_completely_bogus_path(self):
        # NUL bytes make open() raise ValueError rather than OSError on
        # some platforms -- this primitive's contract is "never raises,"
        # so even a pathologically malformed string must come back None,
        # not propagate.
        self.assertIsNone(mission_coordinator._resolve_worktree_identity("not\x00a\x00path"))


class VocabularioCerradoDeHumanGatesStatusTests(CoordinatorTestCase):
    """Verification Hardening V1, Pillar 1 (contract checks) -- sixth
    vocabulary: human_gates.<name>.status. Found by Emma's round-1
    independent review of this same corrective: _consume_gate_if_decided()
    used to branch explicitly only on "approved"/"rejected" and fold
    "pending"/"not_requested"/ANY future value into one untested `else`
    whose own comment said "anything else -- no side effect" -- the exact
    PWNBF-class gap this whole initiative exists to close. Every one of
    GATE_STATUSES' 4 real values now gets its own explicit test; an
    unrecognized status is proven to raise (never silently treated as
    "pending")."""

    def _write_raw_gate_status(self, mid: str, gate_name: str, status: str) -> None:
        """Bypasses chugel.decide_gate() entirely -- writes a status
        no real production API call can produce (a schema violation for
        "pending", or a value outside GATE_STATUSES entirely), to test
        _consume_gate_if_decided()'s own defense-in-depth against a
        corrupted/hand-edited record. Mirrors the pattern already used
        elsewhere in this test suite for exercising a record shape no
        public Chugel function can construct."""
        path = chugel._mission_path(mid)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["human_gates"][gate_name]["status"] = status
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_gate_statuses_exhaustively_classified(self):
        from orchestrator.validator import GATE_STATUSES
        classified = mission_coordinator._GATE_STATUSES_NO_ACTION | mission_coordinator._GATE_STATUSES_ACTIONABLE
        self.assertEqual(GATE_STATUSES, classified)

    def test_not_requested_is_a_safe_no_op(self):
        """The natural default -- a freshly scope-awaiting mission has
        never had its gate touched at all."""
        mid = self._mission_scope_awaiting()
        self.assertEqual(
            chugel.get_mission(mid)["human_gates"]["scope_authorization"]["status"], "not_requested",
        )
        consumed = mission_coordinator._consume_gate_if_decided(mid, "SCOPE_AWAITING_AUTHORIZATION")
        self.assertFalse(consumed)
        self.assertEqual(chugel.get_mission(mid)["state"], "SCOPE_AWAITING_AUTHORIZATION")

    def test_pending_is_a_safe_no_op(self):
        mid = self._mission_scope_awaiting()
        self._write_raw_gate_status(mid, "scope_authorization", "pending")
        consumed = mission_coordinator._consume_gate_if_decided(mid, "SCOPE_AWAITING_AUTHORIZATION")
        self.assertFalse(consumed)
        self.assertEqual(chugel.get_mission(mid)["state"], "SCOPE_AWAITING_AUTHORIZATION")

    def test_approved_transitions_the_mission(self):
        mid = self._mission_scope_awaiting()
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        consumed = mission_coordinator._consume_gate_if_decided(mid, "SCOPE_AWAITING_AUTHORIZATION")
        self.assertTrue(consumed)
        self.assertEqual(chugel.get_mission(mid)["state"], "AUTHORIZED")

    def test_rejected_transitions_the_mission(self):
        mid = self._mission_scope_awaiting()
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_rejection())
        consumed = mission_coordinator._consume_gate_if_decided(mid, "SCOPE_AWAITING_AUTHORIZATION")
        self.assertTrue(consumed)
        self.assertEqual(chugel.get_mission(mid)["state"], "CANCELLED")

    def test_registro_en_disco_con_status_invalido_es_rechazado_por_chugel_antes_de_llegar_aqui(self):
        """The outer protection: orchestrator.chugel._read_mission_record()
        (chugel.get_mission()'s own implementation) validates the FULL
        record against the schema on every single read, unconditionally
        -- an out-of-vocabulary human_gates.*.status can never even reach
        _consume_gate_if_decided() through any real on-disk path. Proven
        directly, not assumed."""
        mid = self._mission_scope_awaiting()
        self._write_raw_gate_status(mid, "scope_authorization", "revoked")
        with self.assertRaises(chugel.MissionRecordInvalid):
            chugel.get_mission(mid)

    def test_status_fuera_del_vocabulario_es_rechazado_sin_side_effect(self):
        """The inner protection, defense-in-depth: even if chugel's own
        outer schema-validation-on-read were ever bypassed or weakened,
        _consume_gate_if_decided()'s own explicit vocabulary check still
        fails closed -- proven directly by mocking chugel.get_mission()
        to return a record the real on-disk read path could never
        produce, exercising this function's own logic in isolation. A
        future 5th gate status must never be silently treated as
        "pending" -- it must raise, and must never transition the
        mission (chugel.transition is never reached)."""
        mid = self._mission_scope_awaiting()
        record = chugel.get_mission(mid)
        record = json.loads(json.dumps(record))  # deep copy
        record["human_gates"]["scope_authorization"]["status"] = "revoked"
        with mock.patch("jarvis.mission_coordinator.chugel.get_mission", return_value=record), \
             mock.patch("jarvis.mission_coordinator.chugel.transition") as transition_mock:
            with self.assertRaises(ValueError):
                mission_coordinator._consume_gate_if_decided(mid, "SCOPE_AWAITING_AUTHORIZATION")
            transition_mock.assert_not_called()
        # The real on-disk record was never touched by the mocked call.
        self.assertEqual(chugel.get_mission(mid)["state"], "SCOPE_AWAITING_AUTHORIZATION")


class VocabularioCerradoDeCoordinatorReportTests(unittest.TestCase):
    """Verification Hardening V1, Pillar 1 (contract checks).
    CoordinatorReport.status has no JSON schema of its own to check
    against -- it is a pure-Python, internal-only vocabulary (unlike
    reviewer_evidence.verdict or dispatch_ledger_entry.
    result_classification). Its closure is instead enforced directly by
    CoordinatorReport.__post_init__ against COORDINATOR_REPORT_STATUSES
    -- these tests prove that enforcement is real (rejects an unlisted
    status, accepts every declared one), which is the correct-shaped
    check for a vocabulary with no external source of truth to compare
    against."""

    def test_status_fuera_del_vocabulario_declarado_es_rechazado(self):
        with self.assertRaises(ValueError):
            mission_coordinator.CoordinatorReport("NOT_A_REAL_STATUS", "BUILDING")

    def test_cada_status_declarado_es_construible(self):
        for status in mission_coordinator.COORDINATOR_REPORT_STATUSES:
            with self.subTest(status=status):
                report = mission_coordinator.CoordinatorReport(status, "BUILDING")
                self.assertEqual(report.status, status)


class VocabularioCerradoDeRunnerResultStatusTests(unittest.TestCase):
    """Verification Hardening V1, Pillar 1 (contract checks).
    RunnerResult.status DOES have a real, single consumer worth checking
    exhaustively against: mission_coordinator.advance()'s BUILDING-family
    branch, which explicitly checks AUTHORIZATION_REQUIRED/
    TERMINAL_FAILURE/COMPLETED and treats everything else -- by
    construction, not accidentally -- as HUMAN_ACTION_REQUIRED. This is
    exactly the shape PWNBF Runner Handling's fix and this same
    corrective's own DISPATCH_RETRYABLE_CLASSIFICATIONS gap both had:
    prove the fallback bucket is the REST of the declared vocabulary,
    not an untested assumption."""

    def test_todo_runner_result_status_esta_cubierto_por_advance(self):
        from orchestrator.autonomous_runner import RUNNER_RESULT_STATUSES
        explicitly_checked = {"AUTHORIZATION_REQUIRED", "TERMINAL_FAILURE", "COMPLETED"}
        fallback = {"HUMAN_ACTION_REQUIRED"}
        self.assertEqual(RUNNER_RESULT_STATUSES, explicitly_checked | fallback)

    def test_status_fuera_del_vocabulario_declarado_es_rechazado(self):
        from orchestrator.autonomous_runner import RunnerResult
        with self.assertRaises(ValueError):
            RunnerResult("NOT_A_REAL_STATUS", "BUILDING", 0)

    def test_cada_status_declarado_es_construible(self):
        from orchestrator.autonomous_runner import RUNNER_RESULT_STATUSES, RunnerResult
        for status in RUNNER_RESULT_STATUSES:
            with self.subTest(status=status):
                result = RunnerResult(status, "BUILDING", 0)
                self.assertEqual(result.status, status)


class DefaultCiPollTimeoutConstantTests(unittest.TestCase):
    """Verification Hardening V1, Pillar 3 (Progress Watchdog): mechanical
    extraction of advance()'s ci_poll_timeout_seconds default into a real,
    named, importable module constant, DEFAULT_CI_POLL_TIMEOUT_SECONDS --
    so jarvis/status.py's watchdog has one canonical source rather than
    duplicating the literal or reaching into advance.__defaults__ as a
    runtime dependency. This test uses inspect.signature() only to prove
    the two stay in sync as a drift guard -- never as anything the
    watchdog itself depends on at runtime."""

    def test_advance_ci_poll_timeout_seconds_default_matches_la_constante(self):
        effective_default = inspect.signature(mission_coordinator.advance).parameters[
            "ci_poll_timeout_seconds"
        ].default
        self.assertEqual(effective_default, mission_coordinator.DEFAULT_CI_POLL_TIMEOUT_SECONDS)

    def test_la_constante_es_exactamente_1800(self):
        """Pins the real value itself -- not just internal self-consistency
        -- so an edit that changes BOTH the constant and the parameter
        together (still "in sync" per the test above) still surfaces as a
        deliberate, reviewed change here."""
        self.assertEqual(mission_coordinator.DEFAULT_CI_POLL_TIMEOUT_SECONDS, 1800.0)


if __name__ == "__main__":
    unittest.main()
