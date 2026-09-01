"""M2A -- Per-Mission Workspace Provisioning tests. Real git repositories
in temporary directories throughout -- never a mocked subprocess call
EXCEPT in the adversarial race tests, where `os.open`/`subprocess.run`
are patched specifically to make an attack's timing deterministic
(perform the filesystem swap synchronously, in the same thread, at the
exact call this module itself makes -- never a sleep-based race, which
would be probabilistic and could pass even with the original P0 bug
still present, purely by getting lucky on timing)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

import orchestrator.workspace as workspace
from orchestrator.workspace import (
    OrphanReport, RemovalOutcome, WorkspaceProvisionError,
    derive_branch_name, derive_worktree_path, find_and_reconcile_orphaned_worktrees,
    provision_mission_worktree, remove_mission_worktree,
)


def _run(*args, cwd):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _replace_with_a_genuinely_different_directory(path: Path) -> None:
    """Simulates 'this path's real, physical identity changed' for a
    test -- WITHOUT relying on rmtree()-then-mkdir() at the same path,
    which is platform-dependent and unreliable: on at least one real
    Linux filesystem (confirmed by a genuine CI failure on this exact
    technique, not merely theorized), a freed inode can be immediately
    reallocated to a directory created moments later at the same path,
    giving the "replacement" the SAME (st_dev, st_ino) as the original --
    silently defeating the very identity-mismatch scenario a test using
    that technique means to simulate. This allocates the replacement
    directory at a SEPARATE path first (getting its own, unrelated inode
    while the original still exists, before any removal happens at all)
    and only then removes the original and renames the pre-existing
    replacement into place -- a genuinely different identity, reliably,
    regardless of any allocator's own reuse behavior or timing."""
    import shutil as _shutil
    swap = path.parent / f"{path.name}.swap-{uuid.uuid4()}"
    swap.mkdir()
    _shutil.rmtree(path)
    swap.rename(path)


def _init_base_repo(tmp: Path) -> tuple[Path, str]:
    base = tmp / "base"
    base.mkdir()
    _run("init", "-q", cwd=base)
    _run("config", "user.name", "t", cwd=base)
    _run("config", "user.email", "t@t.example", cwd=base)
    (base / "f.txt").write_text("hello\n")
    _run("add", "f.txt", cwd=base)
    _run("commit", "-q", "-m", "init", cwd=base)
    base_sha = _run("rev-parse", "HEAD", cwd=base)
    return base, base_sha


def _new_mission_id() -> str:
    return str(uuid.uuid4())


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.base, self.base_sha = _init_base_repo(self.tmp)

    def tearDown(self):
        self._tmpdir.cleanup()


class DeterministicIdentityTests(WorkspaceTestCase):
    def test_path_is_pure_and_deterministic(self):
        mid = _new_mission_id()
        p1 = derive_worktree_path(mid, self.base)
        p2 = derive_worktree_path(mid, self.base)
        self.assertEqual(p1, p2)
        self.assertEqual(p1, self.base.resolve() / "missions" / mid)

    def test_branch_is_pure_and_deterministic(self):
        mid = _new_mission_id()
        self.assertEqual(derive_branch_name(mid), derive_branch_name(mid))
        self.assertEqual(derive_branch_name(mid), f"mission/{mid}")

    def test_two_different_mission_ids_never_collide(self):
        a, b = _new_mission_id(), _new_mission_id()
        self.assertNotEqual(derive_worktree_path(a, self.base), derive_worktree_path(b, self.base))
        self.assertNotEqual(derive_branch_name(a), derive_branch_name(b))

    def test_invalid_mission_id_is_rejected_before_any_path_is_built(self):
        for bad in ("../../etc/passwd", "not-a-uuid", "", "a/b", "8f70cdfc-9ac2-45a5-a71a-1dedabb1726a/../evil"):
            with self.assertRaises(WorkspaceProvisionError) as ctx:
                derive_worktree_path(bad, self.base)
            self.assertEqual("INVALID_MISSION_ID", ctx.exception.reason_code)

    def test_derived_path_is_always_under_missions_subdir_of_base_root(self):
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        self.assertTrue(path.is_relative_to(self.base.resolve() / "missions"))


class ProvisioningTests(WorkspaceTestCase):
    def test_provisions_a_real_worktree_at_the_deterministic_path(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual(path, derive_worktree_path(mid, self.base))
        self.assertTrue(path.is_dir())
        self.assertEqual(_run("rev-parse", "HEAD", cwd=path), self.base_sha)
        self.assertEqual(_run("branch", "--show-current", cwd=path), derive_branch_name(mid))

    def test_second_call_with_identical_arguments_is_idempotent(self):
        mid = _new_mission_id()
        first = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        second = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual(first, second)
        # Still exactly one registered worktree for this mission, not two.
        listing = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertEqual(1, listing.count(str(first)))

    def test_crash_mid_provision_then_resume_with_matching_state_succeeds(self):
        mid = _new_mission_id()
        # Simulate: a prior process ran `git worktree add` for real and
        # then crashed before its own caller durably recorded success.
        provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        # A fresh "process" (nothing shared, just calling the function
        # again) resumes -- must recognize the already-correct state.
        resumed = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual(derive_worktree_path(mid, self.base), resumed)

    def test_resume_with_head_mismatch_fails_closed(self):
        mid = _new_mission_id()
        provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        # A second, real commit -- the resumed base_sha no longer matches
        # what was actually provisioned.
        (self.base / "f2.txt").write_text("more\n")
        _run("add", "f2.txt", cwd=self.base)
        _run("commit", "-q", "-m", "second", cwd=self.base)
        other_sha = _run("rev-parse", "HEAD", cwd=self.base)
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=other_sha)
        self.assertEqual("HEAD_MISMATCH_ON_RESUME", ctx.exception.reason_code)
        # Nothing about the original, correct worktree was touched.
        path = derive_worktree_path(mid, self.base)
        self.assertEqual(_run("rev-parse", "HEAD", cwd=path), self.base_sha)

    def test_a_stray_unrecognized_directory_at_the_deterministic_path_is_never_reused(self):
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        (path / "something").write_text("not ours")
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("PATH_EXISTS_UNRECOGNIZED", ctx.exception.reason_code)
        # The stray directory's own content is untouched -- no
        # auto-remediation.
        self.assertEqual("not ours", (path / "something").read_text())

    def test_a_symlink_at_the_deterministic_path_is_refused_not_followed(self):
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        path.symlink_to(elsewhere)
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("PATH_IS_SYMLINK", ctx.exception.reason_code)

    def test_nonexistent_git_executable_fails_closed(self):
        mid = _new_mission_id()
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha, git_executable="definitely-not-a-real-git-binary")
        self.assertEqual("GIT_EXECUTABLE_NOT_FOUND", ctx.exception.reason_code)

    def test_base_root_that_is_not_a_git_repository_fails_closed(self):
        mid = _new_mission_id()
        not_a_repo = self.tmp / "plain"
        not_a_repo.mkdir()
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=not_a_repo, base_sha=self.base_sha)
        self.assertEqual("BASE_ROOT_NOT_A_GIT_REPOSITORY", ctx.exception.reason_code)

    def test_unknown_base_sha_fails_closed_via_worktree_add_failure(self):
        mid = _new_mission_id()
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha="f" * 40)
        self.assertEqual("WORKTREE_ADD_FAILED", ctx.exception.reason_code)

    def test_reason_code_never_carries_raw_git_stderr_text(self):
        # A deliberately-broken base_sha guarantees a real git stderr
        # containing the literal bad value -- confirm it never leaks
        # into the one thing callers are allowed to record: reason_code.
        mid = _new_mission_id()
        bogus_sha = "deadbeef" * 5
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=bogus_sha)
        self.assertNotIn(bogus_sha, ctx.exception.reason_code)
        self.assertIn(ctx.exception.reason_code, workspace.PROVISION_FAILURE_REASONS)


class BranchPathCollisionAdversarialTests(WorkspaceTestCase):
    def test_a_foreign_branch_registered_at_the_deterministic_path_is_never_trusted(self):
        """Adversarial: something (a bug elsewhere, manual tampering)
        registered a real git worktree at exactly this mission's
        deterministic path, but on the WRONG branch. provision_mission_worktree()
        must refuse to treat this as a valid resume."""
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        _run("worktree", "add", str(path), "-b", "someone-elses-branch", self.base_sha, cwd=self.base)
        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("BRANCH_MISMATCH_ON_RESUME", ctx.exception.reason_code)

    def test_removal_refuses_a_foreign_branch_at_the_deterministic_path(self):
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        _run("worktree", "add", str(path), "-b", "someone-elses-branch", self.base_sha, cwd=self.base)
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("removal_failed", outcome)
        self.assertEqual("BRANCH_IDENTITY_MISMATCH", reason_code)
        # Never actually deleted.
        self.assertTrue(path.is_dir())
        listing = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertIn(str(path), listing)


class RemovalTests(WorkspaceTestCase):
    def test_already_absent_is_determined_explicitly_before_any_remove_attempt(self):
        mid = _new_mission_id()
        # Never provisioned at all.
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("already_absent", outcome)
        self.assertIsNone(reason_code)

    def test_a_stray_unregistered_directory_is_reported_absent_and_never_touched(self):
        """A plain directory sitting at the mission's deterministic path,
        never registered with git, genuinely has no WORKTREE for this
        function to remove -- "already_absent" here is an honest,
        registry-derived fact (no registered worktree exists), not a
        claim that the path itself is clear. Critically: this
        determination comes from the registry (git worktree list
        --porcelain), never from attempting `git worktree remove` and
        interpreting its generic failure -- that failure looks
        identical whether the path never existed, was already removed,
        or is exactly this stray, foreign directory, so blindly trusting
        it would risk exactly the ambiguity this test exists to rule
        out. The directory's own content is never touched either way --
        removal only ever acts on a path the registry itself
        recognizes."""
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        (path / "not-ours.txt").write_text("stray content")
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("already_absent", outcome)
        self.assertIsNone(reason_code)
        # Never touched -- no worktree was registered here, so nothing
        # was deleted, regardless of what physically sits at the path.
        self.assertTrue(path.is_dir())
        self.assertEqual("stray content", (path / "not-ours.txt").read_text())
        # And, independently: a subsequent provisioning attempt still
        # correctly refuses to reuse this same stray path (proven by
        # ProvisioningTests.test_a_stray_unrecognized_directory_at_the_deterministic_path_is_never_reused)
        # -- "already_absent" here never implies "safe to reprovision,"
        # that is a separate check this function makes no claim about.

    def test_a_real_owned_worktree_is_actually_removed(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("removed", outcome)
        self.assertIsNone(reason_code)
        self.assertFalse(path.exists())
        listing = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertNotIn(str(path), listing)

    def test_removal_is_idempotent(self):
        mid = _new_mission_id()
        provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        first_outcome, first_reason = remove_mission_worktree(mid, base_root=self.base)
        second_outcome, second_reason = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("removed", first_outcome)
        self.assertIsNone(first_reason)
        self.assertEqual("already_absent", second_outcome)
        self.assertIsNone(second_reason)

    def test_a_symlink_at_the_deterministic_path_is_never_removed(self):
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        elsewhere = self.tmp / "elsewhere2"
        elsewhere.mkdir()
        path.symlink_to(elsewhere)
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("removal_failed", outcome)
        self.assertEqual("PATH_IS_SYMLINK", reason_code)
        self.assertTrue(path.is_symlink())
        self.assertTrue(elsewhere.is_dir())

    def test_a_real_owned_worktree_with_uncommitted_changes_is_still_removed(self):
        """Terminal-mission cleanup must succeed even for a mission that
        left real uncommitted work behind (e.g. FAILED/CANCELLED) --
        --force is safe here specifically because ownership (branch
        identity) was already unambiguously confirmed first."""
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        (path / "uncommitted.txt").write_text("dirty\n")
        outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)
        self.assertEqual("removed", outcome)
        self.assertIsNone(reason_code)
        self.assertFalse(path.exists())


class OrphanReconciliationTests(WorkspaceTestCase):
    def _list_missions_stub(self, missions):
        def _fn():
            return list(missions)
        return _fn

    def test_active_owned_worktree_is_reported_but_never_touched(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base,
            list_missions=self._list_missions_stub([
                {"mission_id": mid, "readable": True, "state": "BUILDING", "updated_at": "2026-01-01T00:00:00Z", "error_code": None},
            ]),
        )
        self.assertEqual(1, len(reports))
        self.assertEqual("OWNED_ACTIVE", reports[0].classification)
        self.assertTrue(path.is_dir())

    def test_terminal_owned_worktree_is_cleaned_up(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base,
            list_missions=self._list_missions_stub([
                {"mission_id": mid, "readable": True, "state": "COMPLETED", "updated_at": "2026-01-01T00:00:00Z", "error_code": None},
            ]),
        )
        self.assertEqual(1, len(reports))
        self.assertEqual("OWNED_TERMINAL_CLEANED", reports[0].classification)
        self.assertEqual("removed", reports[0].removal_outcome)
        self.assertIsNone(reports[0].removal_reason_code)
        self.assertFalse(path.exists())

    def test_merged_state_is_conservatively_left_alone_not_cleaned(self):
        """M2A's own deliberately narrow RECLAIMABLE_TERMINAL_STATES --
        MERGED is NOT in it (see that constant's docstring)."""
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base,
            list_missions=self._list_missions_stub([
                {"mission_id": mid, "readable": True, "state": "MERGED", "updated_at": "2026-01-01T00:00:00Z", "error_code": None},
            ]),
        )
        self.assertEqual("OWNED_ACTIVE", reports[0].classification)
        self.assertTrue(path.is_dir())

    def test_mission_unknown_to_chugel_is_ambiguous_and_never_deleted(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base, list_missions=self._list_missions_stub([]),
        )
        self.assertEqual(1, len(reports))
        self.assertEqual("AMBIGUOUS_UNRECOGNIZED", reports[0].classification)
        self.assertIsNone(reports[0].removal_outcome)
        self.assertTrue(path.is_dir())

    def test_unreadable_mission_listing_is_ambiguous_and_never_deleted(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base,
            list_missions=self._list_missions_stub([
                {"mission_id": mid, "readable": False, "state": None, "updated_at": None, "error_code": "SCHEMA_INVALID"},
            ]),
        )
        self.assertEqual("AMBIGUOUS_UNRECOGNIZED", reports[0].classification)
        self.assertTrue(path.is_dir())

    def test_a_path_whose_name_does_not_parse_as_a_mission_id_is_ambiguous_and_never_deleted(self):
        stray = self.base.resolve() / "missions" / "not-a-real-mission-id"
        stray.parent.mkdir(parents=True, exist_ok=True)
        _run("worktree", "add", str(stray), "-b", "some-operators-own-branch", self.base_sha, cwd=self.base)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base, list_missions=self._list_missions_stub([]),
        )
        self.assertEqual(1, len(reports))
        self.assertEqual("AMBIGUOUS_UNRECOGNIZED", reports[0].classification)
        self.assertIsNone(reports[0].mission_id)
        self.assertTrue(stray.is_dir())

    def test_a_worktree_outside_missions_subdir_is_not_this_modules_concern(self):
        outside = self.tmp / "elsewhere-worktree"
        _run("worktree", "add", str(outside), "-b", "unrelated", self.base_sha, cwd=self.base)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base, list_missions=self._list_missions_stub([]),
        )
        self.assertEqual(0, len(reports))

    def test_multiple_worktrees_report_independently_one_failure_does_not_hide_others(self):
        active_mid = _new_mission_id()
        terminal_mid = _new_mission_id()
        provision_mission_worktree(active_mid, base_root=self.base, base_sha=self.base_sha)
        provision_mission_worktree(terminal_mid, base_root=self.base, base_sha=self.base_sha)
        reports = find_and_reconcile_orphaned_worktrees(
            base_root=self.base,
            list_missions=self._list_missions_stub([
                {"mission_id": active_mid, "readable": True, "state": "REVIEWING", "updated_at": "2026-01-01T00:00:00Z", "error_code": None},
                {"mission_id": terminal_mid, "readable": True, "state": "FAILED", "updated_at": "2026-01-01T00:00:00Z", "error_code": None},
            ]),
        )
        by_mission = {r.mission_id: r for r in reports}
        self.assertEqual("OWNED_ACTIVE", by_mission[active_mid].classification)
        self.assertEqual("OWNED_TERMINAL_CLEANED", by_mission[terminal_mid].classification)
        self.assertEqual("removed", by_mission[terminal_mid].removal_outcome)


class ReasonCodeExhaustivenessTests(unittest.TestCase):
    """VocabularioCerradoDe... pattern established throughout this
    codebase -- every reason_code this module can actually construct
    must be a member of its own declared closed set, and every declared
    member must be reachable from at least one real code path (checked
    indirectly above by ProvisioningTests/RemovalTests exercising most
    of them directly; this class pins the vocabulary itself)."""

    def test_removal_outcome_literal_matches_the_declared_closed_set(self):
        self.assertEqual({"removed", "already_absent", "removal_failed"}, workspace.REMOVAL_OUTCOMES)

    def test_orphan_classification_literal_matches_the_declared_closed_set(self):
        self.assertEqual(
            {"OWNED_ACTIVE", "OWNED_TERMINAL_CLEANED", "AMBIGUOUS_UNRECOGNIZED"},
            workspace.ORPHAN_CLASSIFICATIONS,
        )

    def test_reclaimable_terminal_states_is_a_subset_of_the_real_terminal_bucket(self):
        """Cross-pinned against jarvis.status's own real schema-terminal
        classification -- test-only import, exempt from the production
        orchestrator-must-not-import-jarvis boundary
        (tests/test_jarvis_foundation_boundaries.py only scans jarvis/*.py
        production modules, never test files or orchestrator/*.py)."""
        import json as _json
        from pathlib import Path as _Path
        from jarvis.status import classify_mission_state

        schema_path = _Path(__file__).resolve().parents[1] / "orchestrator" / "schemas" / "mission_record.schema.json"
        states = _json.loads(schema_path.read_text(encoding="utf-8"))["properties"]["state"]["enum"]
        real_terminal = {s for s in states if classify_mission_state(s) == "terminal"}
        self.assertEqual(real_terminal, workspace.RECLAIMABLE_TERMINAL_STATES)

    def test_every_provision_failure_reason_is_reachable(self):
        """Round-2 Emma security review, P2, closed: PROVISION_FAILURE_REASONS
        had no equivalent to REMOVAL_FAILURE_REASONS' own reachability
        test -- POST_ADD_VERIFICATION_FAILED and provisioning-side
        WORKTREE_LIST_FAILED were live, reachable code, but nothing in
        the suite exercised them, so a future regression there
        (accidentally making one unreachable, or firing on the wrong
        condition) would have gone uncaught. Every one of the 11
        declared members is driven through a real call to
        provision_mission_worktree() below, and covered is asserted to
        equal the full declared set at the end -- not just each
        individually present, but nothing declared left untested."""
        covered: set[str] = set()

        def _expect(reason_code, base_root, mission_id, **kwargs):
            with self.assertRaises(WorkspaceProvisionError) as ctx:
                provision_mission_worktree(mission_id, base_root=base_root, **kwargs)
            self.assertEqual(reason_code, ctx.exception.reason_code)
            covered.add(reason_code)

        with tempfile.TemporaryDirectory() as tmp:
            base, base_sha = _init_base_repo(Path(tmp))

            _expect("INVALID_MISSION_ID", base, "not-a-uuid", base_sha=base_sha)

            not_a_repo = Path(tmp) / "not-a-repo"
            not_a_repo.mkdir()
            _expect("BASE_ROOT_NOT_A_GIT_REPOSITORY", not_a_repo, _new_mission_id(), base_sha=base_sha)

            _expect(
                "GIT_EXECUTABLE_NOT_FOUND", base, _new_mission_id(),
                base_sha=base_sha, git_executable="definitely-not-a-real-git-binary",
            )

            mid_symlink = _new_mission_id()
            leaf = derive_worktree_path(mid_symlink, base)
            leaf.parent.mkdir(parents=True, exist_ok=True)
            elsewhere = Path(tmp) / "elsewhere_provision_reach"
            elsewhere.mkdir()
            leaf.symlink_to(elsewhere)
            _expect("PATH_IS_SYMLINK", base, mid_symlink, base_sha=base_sha)
            leaf.unlink()

            mid_unrecognized = _new_mission_id()
            stray = derive_worktree_path(mid_unrecognized, base)
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.mkdir()
            _expect("PATH_EXISTS_UNRECOGNIZED", base, mid_unrecognized, base_sha=base_sha)

            mid_branch = _new_mission_id()
            branch_path = derive_worktree_path(mid_branch, base)
            _run("worktree", "add", str(branch_path), "-b", "someone-elses-branch", base_sha, cwd=base)
            _expect("BRANCH_MISMATCH_ON_RESUME", base, mid_branch, base_sha=base_sha)

            mid_head = _new_mission_id()
            provision_mission_worktree(mid_head, base_root=base, base_sha=base_sha)
            (base / "f2.txt").write_text("second\n")
            _run("add", "f2.txt", cwd=base)
            _run("commit", "-q", "-m", "second", cwd=base)
            other_sha = _run("rev-parse", "HEAD", cwd=base)
            _expect("HEAD_MISMATCH_ON_RESUME", base, mid_head, base_sha=other_sha)

            _expect("WORKTREE_ADD_FAILED", base, _new_mission_id(), base_sha="f" * 40)

        with tempfile.TemporaryDirectory() as tmp2:
            base2, base_sha2 = _init_base_repo(Path(tmp2))
            mid_list_failed = _new_mission_id()
            with mock.patch.object(workspace, "_list_registered_worktrees") as fake_list:
                fake_list.side_effect = WorkspaceProvisionError("WORKTREE_LIST_FAILED", "synthetic")
                _expect("WORKTREE_LIST_FAILED", base2, mid_list_failed, base_sha=base_sha2)

        with tempfile.TemporaryDirectory() as tmp3:
            base3, base_sha3 = _init_base_repo(Path(tmp3))
            mid_post_add = _new_mission_id()
            def _always_empty_registry(base_root, git_executable):
                # Both the pre-add lookup (nothing registered yet -- proceed
                # to create) and the post-add lookup (pretend `git worktree
                # add` left no trace, even though it genuinely just
                # succeeded) return empty -- this is exactly the shape
                # POST_ADD_VERIFICATION_FAILED exists to catch.
                return {}
            with mock.patch.object(workspace, "_list_registered_worktrees", side_effect=_always_empty_registry):
                _expect("POST_ADD_VERIFICATION_FAILED", base3, mid_post_add, base_sha=base_sha3)
            # A real worktree WAS actually created by the real `git worktree add`
            # call inside this -- confirm it, then clean it up via the real
            # registry (not a manual rm/rmtree), so this test doesn't leak
            # into any assertion below it.
            real_path = derive_worktree_path(mid_post_add, base3)
            self.assertTrue(real_path.is_dir())
            outcome, _ = remove_mission_worktree(mid_post_add, base_root=base3)
            self.assertEqual("removed", outcome)

        with tempfile.TemporaryDirectory() as tmp4:
            base4, base_sha4 = _init_base_repo(Path(tmp4))
            mid_identity = _new_mission_id()
            path4 = derive_worktree_path(mid_identity, base4)
            real_run = subprocess.run

            def _tamper_after_add(argv, *args, **kwargs):
                result = real_run(argv, *args, **kwargs)
                if "add" in argv and result.returncode == 0:
                    _replace_with_a_genuinely_different_directory(path4)
                return result

            with mock.patch("subprocess.run", side_effect=_tamper_after_add):
                _expect("POST_ADD_IDENTITY_MISMATCH", base4, mid_identity, base_sha=base_sha4)

        self.assertEqual(workspace.PROVISION_FAILURE_REASONS, covered)

    def test_every_removal_failure_reason_is_reachable(self):
        """Round-1 Emma security review, P2, closed: REMOVAL_FAILURE_REASONS
        was previously declared but never actually returned by anything.
        Every member below is driven through a real call to
        remove_mission_worktree() and asserted to be the exact reason_code
        produced -- not merely present in the constant."""
        base_tmp = tempfile.TemporaryDirectory()
        try:
            base, base_sha = _init_base_repo(Path(base_tmp.name))
            mid = _new_mission_id()

            self.assertEqual(
                ("removal_failed", "INVALID_MISSION_ID"),
                remove_mission_worktree("not-a-uuid", base_root=base),
            )

            not_a_repo = Path(base_tmp.name) / "not-a-repo"
            not_a_repo.mkdir()
            self.assertEqual(
                ("removal_failed", "BASE_ROOT_NOT_A_GIT_REPOSITORY"),
                remove_mission_worktree(mid, base_root=not_a_repo),
            )

            self.assertEqual(
                ("removal_failed", "GIT_EXECUTABLE_NOT_FOUND"),
                remove_mission_worktree(mid, base_root=base, git_executable="definitely-not-a-real-git-binary"),
            )

            path = derive_worktree_path(mid, base)
            path.parent.mkdir(parents=True, exist_ok=True)
            elsewhere = Path(base_tmp.name) / "elsewhere"
            elsewhere.mkdir()
            path.symlink_to(elsewhere)
            self.assertEqual(("removal_failed", "PATH_IS_SYMLINK"), remove_mission_worktree(mid, base_root=base))
            path.unlink()

            mid2 = _new_mission_id()
            path2 = derive_worktree_path(mid2, base)
            _run("worktree", "add", str(path2), "-b", "someone-elses-branch", base_sha, cwd=base)
            self.assertEqual(("removal_failed", "BRANCH_IDENTITY_MISMATCH"), remove_mission_worktree(mid2, base_root=base))

            # WORKTREE_LIST_FAILED and WORKTREE_REMOVE_FAILED/
            # WORKTREE_REMOVE_DID_NOT_TAKE_EFFECT are exercised
            # individually below via targeted patches -- reaching them
            # through pure filesystem setup alone is impractical (they
            # represent git itself misbehaving).
        finally:
            base_tmp.cleanup()

        with tempfile.TemporaryDirectory() as tmp2:
            base2, base_sha2 = _init_base_repo(Path(tmp2))
            mid3 = _new_mission_id()
            path3 = provision_mission_worktree(mid3, base_root=base2, base_sha=base_sha2)
            with mock.patch.object(workspace, "_list_registered_worktrees") as fake_list:
                fake_list.side_effect = WorkspaceProvisionError("WORKTREE_LIST_FAILED", "synthetic failure for this test")
                self.assertEqual(
                    ("removal_failed", "WORKTREE_LIST_FAILED"),
                    remove_mission_worktree(mid3, base_root=base2),
                )

        with tempfile.TemporaryDirectory() as tmp3:
            base3, base_sha3 = _init_base_repo(Path(tmp3))
            mid4 = _new_mission_id()
            provision_mission_worktree(mid4, base_root=base3, base_sha=base_sha3)
            real_run = subprocess.run

            def _fail_only_remove(argv, *args, **kwargs):
                if "remove" in argv:
                    raise OSError("synthetic subprocess failure")
                return real_run(argv, *args, **kwargs)

            with mock.patch("subprocess.run", side_effect=_fail_only_remove):
                self.assertEqual(
                    ("removal_failed", "WORKTREE_REMOVE_FAILED"),
                    remove_mission_worktree(mid4, base_root=base3),
                )

        with tempfile.TemporaryDirectory() as tmp4:
            base4, base_sha4 = _init_base_repo(Path(tmp4))
            mid5 = _new_mission_id()
            provision_mission_worktree(mid5, base_root=base4, base_sha=base_sha4)
            real_run2 = subprocess.run

            def _fake_successful_remove_that_does_nothing(argv, *args, **kwargs):
                if "remove" in argv:
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
                return real_run2(argv, *args, **kwargs)

            with mock.patch("subprocess.run", side_effect=_fake_successful_remove_that_does_nothing):
                self.assertEqual(
                    ("removal_failed", "WORKTREE_REMOVE_DID_NOT_TAKE_EFFECT"),
                    remove_mission_worktree(mid5, base_root=base4),
                )


class RemovalRaceAdversarialTests(WorkspaceTestCase):
    """Round-2 Emma security review, P2, closed: remove_mission_worktree()
    now performs the same O_NOFOLLOW fd-chain identity confirmation
    TWICE -- once after the registry lookup, once immediately before
    invoking `git worktree remove` -- and never attempts any manual
    cleanup on a mismatch. These tests deterministically synchronize
    (call-count-based function patching, or a targeted subprocess.run
    patch keyed on the exact "remove" argv -- never a sleep) an attack
    landing exactly between the two confirmations, and exactly between
    the final confirmation and git's own invocation, to prove both the
    module's own new defense AND the honestly-documented residual
    boundary (git's own internal consistency check) each do what they
    claim."""

    def test_leaf_identity_changed_between_the_two_confirmations_is_rejected_without_invoking_git(self):
        mid = _new_mission_id()
        path = provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        real_capture = workspace._capture_leaf_identity
        call_count = {"n": 0}
        remove_invoked = threading.Event()
        real_run = subprocess.run

        def _counting_run(argv, *args, **kwargs):
            if "remove" in argv:
                remove_invoked.set()
            return real_run(argv, *args, **kwargs)

        def _tamper_on_second_call(resolved_base_root, mission_id_arg):
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Between our own first and second confirmation: replace
                # the real (non-empty, real-checkout-content) directory
                # with a DIFFERENT real, empty directory -- a genuinely
                # different identity, not a symlink, so this specifically
                # tests the identity comparison, not the symlink check.
                # This is the TEST's own simulated-attacker action,
                # never the module's own code (which never performs
                # manual cleanup of any kind).
                _replace_with_a_genuinely_different_directory(path)
            return real_capture(resolved_base_root, mission_id_arg)

        with mock.patch.object(workspace, "_capture_leaf_identity", side_effect=_tamper_on_second_call), \
             mock.patch("subprocess.run", side_effect=_counting_run):
            outcome, reason_code = remove_mission_worktree(mid, base_root=self.base)

        self.assertEqual("removal_failed", outcome)
        self.assertEqual("LEAF_IDENTITY_CHANGED_BEFORE_REMOVAL", reason_code)
        self.assertFalse(remove_invoked.is_set(), "git worktree remove must never be invoked once the identity check fails")
        # No manual cleanup was ever attempted either -- the tampered
        # (but real, ordinary) replacement directory is left exactly as
        # it was.
        self.assertTrue(path.is_dir())
        self.assertFalse(path.is_symlink())
        # And the mission's real, original worktree registration is
        # untouched -- still registered, still on its own branch.
        registry = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertIn(derive_branch_name(mid), registry)

    def test_symlink_swap_immediately_before_git_invocation_is_refused_by_gits_own_check_victim_untouched(self):
        """Deterministic regression for the specific claim in
        remove_mission_worktree()'s own docstring: even in the narrow
        window this module's own fd-chain re-confirmation cannot close
        (between its own last check and git's own internal path
        resolution), git's "points back to" consistency check refuses
        to delete a foreign target -- re-proven here as a committed,
        repeatable test, not only as an ad hoc reviewer PoC."""
        victim_mid = _new_mission_id()
        victim_path = provision_mission_worktree(victim_mid, base_root=self.base, base_sha=self.base_sha)
        (victim_path / "victim_only_file.txt").write_text("do not touch\n")

        target_mid = _new_mission_id()
        target_path = provision_mission_worktree(target_mid, base_root=self.base, base_sha=self.base_sha)

        real_run = subprocess.run

        def _swap_immediately_before_remove(argv, *args, **kwargs):
            if "remove" in argv and str(target_path) in argv:
                import shutil as _shutil
                _shutil.rmtree(target_path)
                target_path.symlink_to(victim_path)
            return real_run(argv, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=_swap_immediately_before_remove):
            outcome, reason_code = remove_mission_worktree(target_mid, base_root=self.base)

        self.assertEqual("removal_failed", outcome)
        self.assertEqual("WORKTREE_REMOVE_FAILED", reason_code)
        # The victim survives, completely untouched, still registered,
        # still holding its own real content.
        self.assertTrue((victim_path / "victim_only_file.txt").is_file())
        self.assertEqual("do not touch\n", (victim_path / "victim_only_file.txt").read_text())
        registry = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertIn(str(victim_path), registry)
        self.assertIn(derive_branch_name(victim_mid), registry)


class ProvisioningRaceAdversarialTests(WorkspaceTestCase):
    """Round-1 Emma security review, P0, closed: the original code
    checked `Path.is_symlink()` once, then re-resolved the same path
    string a second time when handing it to `git worktree add` as a
    subprocess argument -- a real, reproduced TOCTOU. Every test here
    synchronizes the attack's timing DETERMINISTICALLY, by patching the
    exact library call the corrective implementation makes and
    performing the filesystem swap synchronously inside that patch,
    before delegating to the real underlying call -- never a sleep, and
    never merely probabilistic: these tests must fail reliably against
    the pre-corrective code and pass reliably against the corrected
    code, every single run."""

    def test_parent_missions_dir_symlink_swap_between_verified_steps_is_rejected(self):
        """Attack: `missions/` itself is swapped for a symlink pointing
        elsewhere, in the exact window after base_root's own fd is
        opened but before `missions/` is opened relative to it -- the
        one level the ORIGINAL code had zero defense for at all (it
        never touched `missions/` as a distinct step)."""
        mid = _new_mission_id()
        attacker_target = self.tmp / "attacker_target_parent"
        attacker_target.mkdir()
        missions_dir = self.base.resolve() / "missions"
        real_open = os.open
        attacked = threading.Event()

        def _patched_open(path, flags, *args, dir_fd=None, **kwargs):
            if not attacked.is_set() and path == "missions" and dir_fd is not None:
                attacked.set()
                # missions/ does not exist yet -- nothing to swap out
                # first; plant the symlink exactly where the real
                # missions/ directory would otherwise be created.
                if missions_dir.exists():
                    missions_dir.rmdir()
                os.symlink(str(attacker_target), str(missions_dir))
            return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

        with mock.patch("os.open", side_effect=_patched_open):
            with self.assertRaises(WorkspaceProvisionError) as ctx:
                provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("PATH_IS_SYMLINK", ctx.exception.reason_code)
        self.assertTrue(attacked.is_set(), "the attack must actually have fired for this test to mean anything")
        # Nothing was ever written into the attacker's target directory.
        self.assertEqual([], list(attacker_target.iterdir()))

    def test_leaf_symlink_swap_between_mkdir_and_verify_open_is_rejected(self):
        """The exact P0 shape Emma originally reproduced: the leaf is
        created, then swapped for a symlink before this module's own
        verifying open runs."""
        mid = _new_mission_id()
        attacker_target = self.tmp / "attacker_target_leaf"
        attacker_target.mkdir()
        leaf_path = derive_worktree_path(mid, self.base)
        real_open = os.open
        attacked = threading.Event()

        def _patched_open(path, flags, *args, dir_fd=None, **kwargs):
            if not attacked.is_set() and path == mid and dir_fd is not None:
                attacked.set()
                if leaf_path.exists():
                    leaf_path.rmdir()
                os.symlink(str(attacker_target), str(leaf_path))
            return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

        with mock.patch("os.open", side_effect=_patched_open):
            with self.assertRaises(WorkspaceProvisionError) as ctx:
                provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("PATH_IS_SYMLINK", ctx.exception.reason_code)
        self.assertTrue(attacked.is_set(), "the attack must actually have fired for this test to mean anything")
        self.assertEqual([], list(attacker_target.iterdir()), "git worktree add must never have run against the attacker's target")

    def test_a_preexisting_symlink_at_the_leaf_path_is_rejected_without_any_race(self):
        """No timing needed at all -- the attacker plants the symlink
        before provisioning is ever attempted. os.mkdir() fails closed
        (FileExistsError) against ANY existing entry, symlink included,
        and the subsequent verify-open then correctly refuses it."""
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        attacker_target = self.tmp / "attacker_target_preexisting"
        attacker_target.mkdir()
        path.symlink_to(attacker_target)

        with self.assertRaises(WorkspaceProvisionError) as ctx:
            provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("PATH_IS_SYMLINK", ctx.exception.reason_code)
        self.assertEqual([], list(attacker_target.iterdir()))
        # The symlink itself was never touched/removed either -- an
        # ambiguous path is left exactly as found, for a human to
        # resolve, never auto-remediated.
        self.assertTrue(path.is_symlink())

    def test_post_add_identity_mismatch_never_triggers_any_cleanup(self):
        """Defense-in-depth only, per the corrective design: simulate a
        real `git worktree add` that succeeds, then -- deterministically,
        inside the same patched call, no timing race needed here since
        this specifically tests the DETECTION path, not the prevention
        path -- swap the destination's identity before this module's own
        post-add re-verification runs. Must fail closed with
        POST_ADD_IDENTITY_MISMATCH and must NEVER invoke `git worktree
        remove` or otherwise delete anything."""
        mid = _new_mission_id()
        path = derive_worktree_path(mid, self.base)
        real_run = subprocess.run
        remove_invoked = threading.Event()

        def _patched_run(argv, *args, **kwargs):
            if "remove" in argv:
                remove_invoked.set()
            result = real_run(argv, *args, **kwargs)
            if argv[:2] == [argv[0], "-C"] and "add" in argv and result.returncode == 0:
                # git worktree add just genuinely succeeded -- tamper
                # with the destination's identity before this module's
                # own post-add fd-based re-check runs.
                _replace_with_a_genuinely_different_directory(path)
            return result

        with mock.patch("subprocess.run", side_effect=_patched_run):
            with self.assertRaises(WorkspaceProvisionError) as ctx:
                provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha)
        self.assertEqual("POST_ADD_IDENTITY_MISMATCH", ctx.exception.reason_code)
        self.assertFalse(remove_invoked.is_set(), "no cleanup/removal may ever be attempted on an unconfirmed identity")
        # The tampered (but real, ordinary) directory is left exactly as
        # it was -- no destructive action taken against it.
        self.assertTrue(path.is_dir())
        self.assertFalse(path.is_symlink())

    def test_two_real_concurrent_provisioners_for_the_same_mission_id_never_both_run_git_worktree_add(self):
        """Real threads, real synchronization via threading.Barrier (not
        a sleep) -- both callers are released to start
        provision_mission_worktree() for the IDENTICAL mission_id at the
        same instant. os.mkdir()'s own kernel-level atomicity (verified
        empirically in the corrective design's own scratch experiments:
        of N simultaneous callers, exactly one ever succeeds) means this
        outcome is deterministic regardless of exact scheduling, not
        merely probable."""
        mid = _new_mission_id()
        barrier = threading.Barrier(2)
        real_run = subprocess.run
        add_invocations = []
        lock = threading.Lock()

        def _counting_run(argv, *args, **kwargs):
            if "add" in argv:
                with lock:
                    add_invocations.append(argv)
            return real_run(argv, *args, **kwargs)

        results = {}

        def _worker(name):
            barrier.wait()
            try:
                results[name] = ("ok", provision_mission_worktree(mid, base_root=self.base, base_sha=self.base_sha))
            except WorkspaceProvisionError as exc:
                results[name] = ("error", exc.reason_code)

        with mock.patch("subprocess.run", side_effect=_counting_run):
            threads = [threading.Thread(target=_worker, args=(f"t{i}",)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

        self.assertEqual(2, len(results))
        self.assertEqual(1, len(add_invocations), "exactly one real `git worktree add` may ever run for a given mission_id, however many concurrent callers race for it")
        # Every outcome is either a successful, correct path, or a real,
        # closed-vocabulary failure -- never an uncaught exception, never
        # silent corruption.
        for outcome, value in results.values():
            if outcome == "ok":
                self.assertEqual(derive_worktree_path(mid, self.base), value)
            else:
                self.assertIn(value, workspace.PROVISION_FAILURE_REASONS)
        # Exactly one real worktree ends up registered for this mission,
        # never two, never zero.
        final_path = derive_worktree_path(mid, self.base)
        listing = _run("worktree", "list", "--porcelain", cwd=self.base)
        self.assertEqual(1, listing.count(str(final_path)))
        self.assertEqual(self.base_sha, _run("rev-parse", "HEAD", cwd=final_path))
        self.assertEqual(derive_branch_name(mid), _run("branch", "--show-current", cwd=final_path))


if __name__ == "__main__":
    unittest.main()
