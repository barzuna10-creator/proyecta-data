"""orchestrator/publish_executor.py -- idempotency, CLOSED-PR handling,
CI timeout, and record_publish_commit() ordering. Real Chugel Mission
Records; git/gh subprocess calls are faked via a scripted subprocess.run
double (no real network, no real git/gh CLI dependency)."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from orchestrator import publish_commit_materializer, publish_executor
from orchestrator.autonomous_runner import run_mission
from tests.test_orchestrator_autonomous_runner import (
    _FakeAdapter,
    _create_intake_mission,
    _emilio_completed_template,
    _emma_completed_template,
    _scope_gate_approval,
)


def _json_result(payload, returncode=0):
    return mock.Mock(returncode=returncode, stdout=json.dumps(payload).encode("utf-8"), stderr=b"")


def _ok_result(stdout=b""):
    return mock.Mock(returncode=0, stdout=stdout, stderr=b"")


class PublishExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_publish_awaiting_authorization(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        }
        run_mission(mid, adapters, max_total_attempts=4)
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="publish authorized")
        return mid


class CheckBeforeCreateTests(PublishExecutorTestCase):
    def test_creates_pr_when_none_exists(self):
        mid = self._mission_publish_awaiting_authorization()
        calls = [
            _ok_result(),  # git push
            _json_result([]),  # gh pr list -> none found (check-before-create)
            _ok_result(b"https://example.invalid/pr/7\n"),  # gh pr create (plain-text stdout, never parsed)
            _json_result([{"number": 7, "url": "https://example.invalid/pr/7", "state": "OPEN"}]),  # gh pr list (post-create lookup)
            _json_result({"state": "OPEN", "headRefOid": "a" * 40, "mergeable": "MERGEABLE",
                          "mergeStateStatus": "CLEAN", "statusCheckRollup": [
                              {"conclusion": "SUCCESS"}]}),  # gh pr view (CI poll)
        ]
        with mock.patch.object(publish_commit_materializer, "_run", return_value=_ok_result()), \
             mock.patch.object(publish_executor, "_run", side_effect=calls):
            result = publish_executor.run(
                mid, repository_root="/tmp/repo", branch="b", pr_title="t",
                ci_poll_timeout_seconds=5, ci_poll_interval_seconds=0.01,
            )
        self.assertEqual(result.status, "COMPLETED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["publish"]["pr_number"], 7)
        self.assertEqual(record["state"], "MERGE_AWAITING_AUTHORIZATION")
        # record_publish_commit() must have run only after the transition
        # into MERGE_AWAITING_AUTHORIZATION -- proven here by its success
        # at all, since that call's own precondition would otherwise raise.
        self.assertEqual(record["publish"]["commit_sha"], "a" * 40)

    def test_reuses_existing_open_pr_never_creates_a_second_one(self):
        mid = self._mission_publish_awaiting_authorization()
        calls = [
            _ok_result(),  # git push
            _json_result([{"number": 9, "url": "https://example.invalid/pr/9", "state": "OPEN"}]),
            _json_result({"state": "OPEN", "headRefOid": "a" * 40, "mergeable": "MERGEABLE",
                          "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}),
        ]
        with mock.patch.object(publish_commit_materializer, "_run", return_value=_ok_result()), \
             mock.patch.object(publish_executor, "_run", side_effect=calls) as run_mock:
            result = publish_executor.run(
                mid, repository_root="/tmp/repo", branch="b", pr_title="t",
                ci_poll_timeout_seconds=5, ci_poll_interval_seconds=0.01,
            )
        self.assertEqual(result.status, "COMPLETED")
        argvs = [c.args[0] for c in run_mock.call_args_list]
        self.assertFalse(any("create" in argv for argv in argvs))
        self.assertEqual(chugel.get_mission(mid)["publish"]["pr_number"], 9)

    def test_closed_unmerged_pr_blocks_and_never_creates(self):
        mid = self._mission_publish_awaiting_authorization()
        calls = [
            _ok_result(),  # git push
            _json_result([{"number": 3, "url": "https://example.invalid/pr/3", "state": "CLOSED"}]),
        ]
        with mock.patch.object(publish_commit_materializer, "_run", return_value=_ok_result()), \
             mock.patch.object(publish_executor, "_run", side_effect=calls) as run_mock:
            result = publish_executor.run(
                mid, repository_root="/tmp/repo", branch="b", pr_title="t",
                ci_poll_timeout_seconds=5, ci_poll_interval_seconds=0.01,
            )
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        argvs = [c.args[0] for c in run_mock.call_args_list]
        self.assertFalse(any("create" in argv for argv in argvs))


class CiTimeoutTests(PublishExecutorTestCase):
    def test_pending_forever_reaches_bounded_timeout_and_blocks(self):
        mid = self._mission_publish_awaiting_authorization()
        pending_view = _json_result({"state": "OPEN", "headRefOid": "a" * 40, "mergeable": "MERGEABLE",
                                      "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"conclusion": None, "status": "IN_PROGRESS"}]})

        created = {"done": False}

        def side_effect(*args, **kwargs):
            argv = args[0]
            if argv[1] == "push":
                return _ok_result()
            if "list" in argv:
                if created["done"]:
                    return _json_result([{"number": 1, "url": "https://example.invalid/pr/1", "state": "OPEN"}])
                return _json_result([])
            if "create" in argv:
                created["done"] = True
                return _ok_result(b"https://example.invalid/pr/1\n")
            return pending_view

        with mock.patch.object(publish_commit_materializer, "_run", return_value=_ok_result()), \
             mock.patch.object(publish_executor, "_run", side_effect=side_effect):
            result = publish_executor.run(
                mid, repository_root="/tmp/repo", branch="b", pr_title="t",
                ci_poll_timeout_seconds=0.05, ci_poll_interval_seconds=0.02,
            )
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("timed out", result.reason)


if __name__ == "__main__":
    unittest.main()
