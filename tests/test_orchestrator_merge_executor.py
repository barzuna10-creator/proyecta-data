"""orchestrator/merge_executor.py -- pre-merge gating vs. informational
checks, CLOSED-PR/already-merged handling, the --merge-only invariant,
and (M3 merge recovery hardening) reconciliation of an ambiguous local
`gh pr merge` outcome against GitHub's own authoritative PR state. Real
Chugel Mission Records; git/gh calls faked."""

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


class MergeRecoveryHardeningTests(MergeExecutorTestCase):
    """M3 merge recovery hardening: reproduces the exact live-acceptance
    failure deterministically -- `gh pr merge` issued, the local result
    is lost/ambiguous (a killed process, a transient error, or a
    zero-exit result the immediate re-read doesn't itself confirm), the
    remote merge may have continued independently -- and proves
    _reconcile_ambiguous_merge_outcome() resolves every real shape
    correctly: eventual MERGED, genuine failure, permanent ambiguity,
    and mismatched identity, always via read-only `gh pr view` polling,
    never a second real `gh pr merge` mutation."""

    def _view(self, **overrides):
        base = {"state": "OPEN", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN", "mergeCommit": None,
                "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        base.update(overrides)
        return base

    def _merge_argvs(self, run_mock):
        return [c.args[0] for c in run_mock.call_args_list
                if len(c.args[0]) > 2 and c.args[0][0] == "gh" and c.args[0][2] == "merge"]

    def test_ambiguous_local_failure_that_eventually_shows_merged_converges_to_merged(self):
        """The exact live failure: `gh pr merge` returns non-zero (the
        local controller lost the result / observed a transient
        conflict such as "Merge already in progress"), but the remote
        merge continues and GitHub's own state resolves to MERGED with
        the correct, authorized identity a couple of polls later."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"GraphQL: Merge already in progress\n")
        post_merge_view = self._view(state="MERGED", mergeCommit={"oid": "d" * 40})
        calls = [
            _json_result(self._view()),            # Step 1 pre-merge view
            _ok_result(stdout=(b"c" * 40)),         # origin/main rev-parse (informational)
            merge_failure,                          # the real, authorized gh pr merge attempt -- fails locally
            _json_result(self._view()),             # reconciliation poll #1 -- still OPEN (transient)
            _json_result(post_merge_view),          # reconciliation poll #2 -- now MERGED, correct identity
        ]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock, \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.state, "MERGED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "MERGED")
        self.assertEqual(record["merge"]["merge_commit_sha"], "d" * 40)
        # Never a second real merge mutation -- only the one, original,
        # authorized `gh pr merge` call.
        self.assertEqual(len(self._merge_argvs(run_mock)), 1)

    def test_rc0_success_with_immediate_head_sha_mismatch_does_not_converge(self):
        """P2 correction pass regression: `gh pr merge` itself returns
        rc=0 (an apparently successful CLI exit) -- but the immediate,
        authoritative post-merge `gh pr view` re-read reports a head SHA
        that does NOT match this mission's own authorized/reviewed
        commit. This is a distinct code path from the "rc!=0, reconcile,
        then find a mismatch" scenarios already covered above: here the
        very FIRST post-merge check (run() lines around the `if
        post.get('state') == 'MERGED' and post.get('headRefOid') ==
        reviewed_sha ...` condition) must itself refuse to treat this as
        success, falling through to reconciliation (which, on the same
        contradictory evidence, also refuses to converge) -- proving
        exact identity binding is enforced even on an apparently
        successful CLI return, not only on an already-ambiguous one."""
        mid = self._mission_merging()
        # gh pr merge itself "succeeds" locally...
        merge_success = _ok_result()
        # ...but the immediate post-merge view shows a MERGED PR whose
        # head SHA does not match this mission's own reviewed_sha
        # (_HEAD_SHA) -- e.g. a stale read, a race with some other
        # actor, or a genuinely wrong PR/commit having been merged.
        mismatched_post_view = self._view(state="MERGED", headRefOid="f" * 40, mergeCommit={"oid": "d" * 40})
        # Reconciliation (reached because the immediate check refused to
        # converge) observes the same contradictory identity again and
        # must also refuse.
        calls = [
            _json_result(self._view()),         # Step 1 pre-merge view
            _ok_result(stdout=(b"c" * 40)),      # origin/main rev-parse (informational)
            merge_success,                       # the real gh pr merge call -- reports rc=0
            _json_result(mismatched_post_view),  # immediate post-merge check -- identity mismatch
            _json_result(mismatched_post_view),  # reconciliation poll #1 -- still mismatched
        ]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock, \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("contradictory identity", result.reason)
        self.assertIsNone(chugel.get_mission(mid)["merge"]["merge_commit_sha"])
        # Exactly one real merge mutation was issued (the original,
        # apparently-successful one) -- the mismatch never triggers a
        # second one.
        self.assertEqual(len(self._merge_argvs(run_mock)), 1)

    def test_ambiguous_local_failure_followed_by_genuine_closed_pr_blocks(self):
        """The local `gh pr merge` result is lost, and reconciliation
        discovers GitHub's own definitive, terminal answer: the PR was
        closed without ever merging. No further polling is needed --
        resolves on the first reconciliation read."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"some transient gh error\n")
        closed_view = self._view(state="CLOSED", mergeCommit=None)
        calls = [
            _json_result(self._view()),
            _ok_result(stdout=(b"c" * 40)),
            merge_failure,
            _json_result(closed_view),  # reconciliation poll #1 -- definitively CLOSED
        ]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock, \
                mock.patch("orchestrator.merge_executor.time.sleep") as sleep_mock:
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("closed without merging", result.reason)
        sleep_mock.assert_not_called()  # resolved on the first read -- no need to poll further
        self.assertEqual(len(self._merge_argvs(run_mock)), 1)

    def test_permanently_ambiguous_state_blocks_after_bounded_polling_with_no_second_merge(self):
        """GitHub's own state never resolves within the bound -- stays
        OPEN through every reconciliation poll. Must fail closed to
        BLOCKED (never assume success, never assume failure), respect
        the exact poll bound, and never issue a second real merge
        mutation while waiting."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"transient\n")
        calls = [
            _json_result(self._view()),
            _ok_result(stdout=(b"c" * 40)),
            merge_failure,
        ] + [_json_result(self._view()) for _ in range(merge_executor._MERGE_RECONCILE_MAX_ATTEMPTS)]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock, \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("remained ambiguous", result.reason)
        # Exactly the bounded number of reconciliation polls -- never
        # more (the bound is respected), never fewer (it actually
        # exhausted the bound rather than giving up early).
        view_calls = [c.args[0] for c in run_mock.call_args_list
                      if len(c.args[0]) > 2 and c.args[0][0] == "gh" and c.args[0][2] == "view"]
        self.assertEqual(len(view_calls), 1 + merge_executor._MERGE_RECONCILE_MAX_ATTEMPTS)
        self.assertEqual(len(self._merge_argvs(run_mock)), 1)

    def test_wrong_head_identity_on_reconciliation_blocks_never_converges(self):
        """Reconciliation finds the PR MERGED, but with a head SHA that
        does NOT match this mission's own authorized/reviewed commit --
        contradictory evidence (e.g. a different PR/commit somehow
        merged). Must never be silently accepted as this mission's own
        success."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"transient\n")
        wrong_identity_view = self._view(state="MERGED", headRefOid="f" * 40, mergeCommit={"oid": "d" * 40})
        calls = [
            _json_result(self._view()),
            _ok_result(stdout=(b"c" * 40)),
            merge_failure,
            _json_result(wrong_identity_view),
        ]
        with mock.patch.object(merge_executor, "_run", side_effect=calls) as run_mock, \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("contradictory identity", result.reason)
        self.assertIsNone(chugel.get_mission(mid)["merge"]["merge_commit_sha"])
        self.assertEqual(len(self._merge_argvs(run_mock)), 1)

    def test_merged_but_missing_merge_commit_sha_on_reconciliation_blocks(self):
        """Reconciliation finds state MERGED but no merge commit SHA at
        all -- equally contradictory, must not converge."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"transient\n")
        no_commit_view = self._view(state="MERGED", mergeCommit=None)
        calls = [
            _json_result(self._view()),
            _ok_result(stdout=(b"c" * 40)),
            merge_failure,
            _json_result(no_commit_view),
        ]
        with mock.patch.object(merge_executor, "_run", side_effect=calls), \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("contradictory identity", result.reason)

    def test_gh_pr_view_itself_failing_throughout_reconciliation_blocks_after_bound(self):
        """Not just the merge call -- the read-only reconciliation calls
        themselves can also fail (a real MergeExecutorError from
        _pr_view, e.g. a transient gh/network failure). Must still be
        bounded, must still fail closed, must never crash uncaught."""
        mid = self._mission_merging()
        merge_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"transient\n")
        view_failure = mock.Mock(returncode=1, stdout=b"", stderr=b"gh: network error\n")
        calls = [
            _json_result(self._view()),
            _ok_result(stdout=(b"c" * 40)),
            merge_failure,
        ] + [view_failure for _ in range(merge_executor._MERGE_RECONCILE_MAX_ATTEMPTS)]
        with mock.patch.object(merge_executor, "_run", side_effect=calls), \
                mock.patch("orchestrator.merge_executor.time.sleep"):
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(chugel.get_mission(mid)["state"], "BLOCKED")
        self.assertIn("remained ambiguous", result.reason)

    def test_local_process_crash_then_restart_finds_already_merged_never_double_merges(self):
        """Restart/recovery idempotency for the exact live scenario: the
        local controller is killed/crashes mid-merge-attempt (never even
        reaching BLOCKED -- the mission record is simply left at
        MERGING, exactly as a real SIGKILL would leave it), but the
        remote merge had already gone through. A fresh run() invocation
        after restart (mission still MERGING, so the state guard still
        allows it) must discover the already-MERGED PR via the ordinary
        Step-2 check-before-merge path and converge cleanly -- never
        attempting a second real `gh pr merge` call."""
        mid = self._mission_merging()
        # Simulate the crash: leave the mission at MERGING (do nothing --
        # _mission_merging() already put it there), representing a
        # process that died before ever calling merge_executor.run() at
        # all, or died deep inside a prior call before any Chugel write.
        self.assertEqual(chugel.get_mission(mid)["state"], "MERGING")

        already_merged_view = {
            "state": "MERGED", "headRefOid": _HEAD_SHA, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "d" * 40},
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        }
        with mock.patch.object(merge_executor, "_run", return_value=_json_result(already_merged_view)) as run_mock:
            result = merge_executor.run(mid, repository_root="/tmp/repo")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(chugel.get_mission(mid)["state"], "MERGED")
        self.assertEqual(chugel.get_mission(mid)["merge"]["merge_commit_sha"], "d" * 40)
        self.assertEqual(len(self._merge_argvs(run_mock)), 0)  # no merge call at all -- pure check-before-merge


if __name__ == "__main__":
    unittest.main()
