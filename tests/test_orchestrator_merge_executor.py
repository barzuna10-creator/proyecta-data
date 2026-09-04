"""orchestrator/merge_executor.py -- pre-merge gating vs. informational
checks, CLOSED-PR/already-merged handling, and the --merge-only
invariant. Real Chugel Mission Records; git/gh calls faked."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.chugel as chugel
from orchestrator import merge_executor
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


class MergeExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_merging(self, head_sha=_HEAD_SHA):
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
        chugel.record_publish_pr(mid, "https://example.invalid/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
        chugel.record_publish_commit(mid, head_sha)
        decision = {
            "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
            "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
            "decision_ref": "r1", "approved_for": {"head_sha": head_sha},
        }
        chugel.decide_gate(mid, "merge_authorization", decision)
        chugel.transition(mid, "MERGING", actor="chugel", reason="merge authorized")
        return mid


class GatingChecksTests(MergeExecutorTestCase):
    def _view(self, **overrides):
        base = {"state": "OPEN", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN", "mergeCommit": None,
                "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        base.update(overrides)
        return base

    def test_head_mismatch_blocks(self):
        mid = self._mission_merging()
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(self._view(headRefOid="f" * 40))):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")

    def test_ci_not_success_blocks(self):
        mid = self._mission_merging()
        view = self._view(statusCheckRollup=[{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}])
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")

    def test_real_pr5_payload_is_recognized_as_success_and_does_not_block(self):
        """The exact live payload captured from `gh pr view 5 --json
        statusCheckRollup` on barzuna10-creator/Proyecta -- a StatusContext
        ("Vercel", state=SUCCESS) mixed with a CheckRun (status=COMPLETED,
        conclusion=SUCCESS). Before the shared normalizer, this StatusContext
        entry had no `conclusion`/`status` field, so _ci_conclusion() treated
        it as not-success and this gate would have blocked a genuinely green
        merge on the very first (only) check -- worse than publish_executor's
        version, which at least polls before giving up."""
        mid = self._mission_merging()
        view = self._view(statusCheckRollup=[
            {
                "__typename": "StatusContext",
                "context": "Vercel",
                "startedAt": "2026-08-29T03:55:09Z",
                "state": "SUCCESS",
                "targetUrl": "https://vercel.com/proyecta3/proyecta/GyyiseRmzmab56hSGiBsWRVi8B7Z",
            },
            {
                "__typename": "CheckRun",
                "completedAt": "2026-08-29T04:33:55Z",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://vercel.com/github",
                "name": "Vercel Preview Comments",
                "startedAt": "2026-08-29T04:33:55Z",
                "status": "COMPLETED",
                "workflowName": "",
            },
        ])
        post_merge_view = self._view(state="MERGED", mergeCommit={"oid": "d" * 40})
        calls = [_json_result(view), _ok_result(stdout=(b"b" * 40)), _ok_result(), _json_result(post_merge_view)]
        with mock.patch.object(merge_executor, "_run", side_effect=calls):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(chugel.get_mission(mid)["state"], "MERGED")

    def test_status_context_error_blocks(self):
        mid = self._mission_merging()
        view = self._view(statusCheckRollup=[
            {"__typename": "StatusContext", "context": "Vercel", "state": "ERROR"},
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ])
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")

    def test_unrecognized_check_typename_blocks_fail_closed(self):
        mid = self._mission_merging()
        view = self._view(statusCheckRollup=[{"__typename": "SomeFutureNodeType", "state": "SUCCESS"}])
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")

    def test_not_clean_blocks(self):
        mid = self._mission_merging()
        view = self._view(mergeStateStatus="DIRTY")
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")

    def test_unrelated_base_advance_does_not_block_when_clean(self):
        """Corrective #2 Fix 2: base drift is informational only -- an
        unrelated commit on origin/main must not block an otherwise-clean
        merge."""
        mid = self._mission_merging()

        def side_effect(argv, **kwargs):
            if argv[0] == "gh" and "view" in argv:
                return _json_result(self._view())
            if argv[0] == "gh" and "merge" in argv:
                return _ok_result()
            if argv[0] == "git":  # origin/main rev-parse -- deliberately different from base_sha
                return _ok_result(stdout=(b"c" * 40))
            raise AssertionError(argv)

        post_merge_view = self._view(state="MERGED", mergeCommit={"oid": "d" * 40})
        calls = [_json_result(self._view()), _ok_result(stdout=(b"c" * 40)), _ok_result(), _json_result(post_merge_view)]
        with mock.patch.object(merge_executor, "_run", side_effect=calls):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(chugel.get_mission(mid)["state"], "MERGED")


class CheckBeforeMergeTests(MergeExecutorTestCase):
    def test_already_merged_is_not_merged_again(self):
        mid = self._mission_merging()
        view = {"state": "MERGED", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "d" * 40},
                "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)) as run_mock:
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "COMPLETED")
        argvs = [c.args[0] for c in run_mock.call_args_list]
        self.assertFalse(any("merge" in argv and "view" not in argv for argv in argvs))
        self.assertEqual(chugel.get_mission(mid)["merge"]["merge_commit_sha"], "d" * 40)

    def test_closed_unmerged_blocks_and_never_merges(self):
        mid = self._mission_merging()
        view = {"state": "CLOSED", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN", "mergeCommit": None,
                "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(view)) as run_mock:
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        argvs = [c.args[0] for c in run_mock.call_args_list]
        self.assertFalse(any("merge" in argv and "view" not in argv for argv in argvs))


class MergeStrategyTests(MergeExecutorTestCase):
    def test_merge_call_is_exactly_merge_flag_never_squash_or_rebase(self):
        mid = self._mission_merging()
        view = {"state": "OPEN", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN", "mergeCommit": None,
                "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        post_view = {**view, "state": "MERGED", "mergeCommit": {"oid": "d" * 40}}
        calls = [_json_result(view), _ok_result(stdout=b"b" * 40), _ok_result(), _json_result(post_view)]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock:
            merge_executor.run(mid, repository_root="/tmp/repo")
        merge_calls = [c.args[0] for c in run_mock.call_args_list if len(c.args[0]) > 2 and c.args[0][2] == "merge"]
        self.assertEqual(len(merge_calls), 1)
        argv = merge_calls[0]
        self.assertIn("--merge", argv)
        self.assertNotIn("--squash", argv)
        self.assertNotIn("--rebase", argv)


class M3ConcurrentMergeSerializationTests(MergeExecutorTestCase):
    """M3: two DIFFERENT missions' merge_executor.run() calls, driven
    concurrently on real threads, must never have their real `gh pr
    merge` subprocess calls in flight at the same instant -- proves
    chugel.merge_serialization_lock() is genuinely engaged around the
    real call site, not just present in the source. Pre-merge gating
    calls (the `view` step) are deliberately NOT serialized (per the
    module's own documented minimal-scope design) -- this test also
    confirms that view calls from the two missions DO overlap, so the
    lock is proven neither absent nor over-broad."""

    def test_dos_misiones_concurrentes_nunca_solapan_el_merge_real(self):
        import threading
        import time

        mid_a = self._mission_merging(head_sha="a" * 40)
        mid_b = self._mission_merging(head_sha="b" * 40)
        head_sha_by_mid = {mid_a: "a" * 40, mid_b: "b" * 40}
        oid_by_mid = {mid_a: "d" * 40, mid_b: "e" * 40}

        # merge_executor._run is patched exactly ONCE for the whole test
        # (mock.patch.object is a shared, non-threadsafe attribute swap --
        # each thread doing its own separate `with mock.patch.object(...)`
        # races the other and can silently unpatch it mid-call). Which
        # mission a given call belongs to is carried via a thread-local,
        # set by each thread before it ever calls merge_executor.run().
        thread_state = threading.local()

        merge_windows = []  # (mid, "enter"|"exit") in call order
        merge_active = {"count": 0}
        view_overlap_seen = {"value": False}
        view_active = {"count": 0}
        state_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def shared_side_effect(argv, **kwargs):
            mid = thread_state.mid
            head_sha = head_sha_by_mid[mid]
            if argv[0] == "git":  # origin/main rev-parse -- informational only
                return _ok_result(stdout=(b"c" * 40))
            if argv[0] == "gh" and "view" in argv and "pr" in argv:
                with state_lock:
                    view_active["count"] += 1
                    if view_active["count"] > 1:
                        view_overlap_seen["value"] = True
                time.sleep(0.03)
                with state_lock:
                    view_active["count"] -= 1
                already_merged = any(w[0] == mid for w in merge_windows)
                if not already_merged:
                    return _json_result({
                        "state": "OPEN", "headRefOid": head_sha, "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN", "mergeCommit": None,
                        "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
                    })
                return _json_result({
                    "state": "MERGED", "headRefOid": head_sha, "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": oid_by_mid[mid]},
                    "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
                })
            if argv[0] == "gh" and "merge" in argv:
                with state_lock:
                    merge_active["count"] += 1
                    merge_windows.append((mid, "enter"))
                    overlapped_here = merge_active["count"] > 1
                time.sleep(0.05)
                with state_lock:
                    merge_windows.append((mid, "exit"))
                    merge_active["count"] -= 1
                if overlapped_here:
                    raise AssertionError(f"mission {mid}: real gh pr merge overlapped another mission's")
                return _ok_result()
            raise AssertionError(argv)

        results = {}

        def drive(mid):
            thread_state.mid = mid
            barrier.wait()
            results[mid] = merge_executor.run(mid, repository_root="/tmp/repo")

        with mock.patch.object(merge_executor, "_run", side_effect=shared_side_effect):
            threads = [
                threading.Thread(target=drive, args=(mid_a,)),
                threading.Thread(target=drive, args=(mid_b,)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(results[mid_a].status, "COMPLETED")
        self.assertEqual(results[mid_b].status, "COMPLETED")
        self.assertEqual(chugel.get_mission(mid_a)["state"], "MERGED")
        self.assertEqual(chugel.get_mission(mid_b)["state"], "MERGED")

        # Each mission's own enter/exit pair is contiguous in the shared
        # timeline -- the second mission's merge call genuinely blocked
        # on the lock until the first released it.
        self.assertEqual(len(merge_windows), 4)
        self.assertEqual(merge_windows[0][1], "enter")
        self.assertEqual(merge_windows[1], (merge_windows[0][0], "exit"))
        self.assertEqual(merge_windows[2][1], "enter")
        self.assertEqual(merge_windows[3], (merge_windows[2][0], "exit"))
        self.assertNotEqual(merge_windows[0][0], merge_windows[2][0])

        # Confirms the lock is scoped to the merge call only -- the two
        # missions' pre-merge `gh pr view` calls were free to overlap.
        self.assertTrue(view_overlap_seen["value"], "view calls never overlapped -- test's own concurrency was too weak")


if __name__ == "__main__":
    unittest.main()
