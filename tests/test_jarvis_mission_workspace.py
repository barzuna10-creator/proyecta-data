from __future__ import annotations

import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from jarvis.mission_workspace import MissionWorkspaceError, MissionWorkspaceManager
from orchestrator.workspace import acquire_workspace_supervisor_lease


def _git(root: Path, *argv: str) -> str:
    result = subprocess.run(["git", *argv], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


class MissionWorkspaceManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "repo"
        self.root.mkdir()
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "user.name", "Test")
        (self.root / "base").write_text("base", encoding="utf-8")
        _git(self.root, "add", "base")
        _git(self.root, "commit", "-m", "base")
        self.sha = _git(self.root, "rev-parse", "HEAD")
        self.mid = str(uuid.uuid4())
        self.lease = acquire_workspace_supervisor_lease(self.root)
        self.manager = MissionWorkspaceManager(self.root, lease=self.lease)

    def tearDown(self):
        self.lease.close()
        self.tmp.cleanup()

    def placeholder(self):
        return {
            "worktree_path": "(unconfirmed)",
            "branch": f"mission/{self.mid}", "base_sha": self.sha,
            "isolation_confirmed": False,
        }

    def test_ensure_then_neutral_verify(self):
        binding = self.manager.ensure(self.mid, self.placeholder())
        self.assertTrue(binding.isolation_confirmed)
        record = dict(
            self.placeholder(), worktree_path=binding.worktree_path, isolation_confirmed=True,
        )
        self.assertEqual(binding.head_sha, self.manager.verify(self.mid, record).head_sha)

    def test_caller_cannot_choose_path_or_branch(self):
        for key, value in (("worktree_path", "/tmp/foreign"), ("branch", "main")):
            repository = self.placeholder()
            repository[key] = value
            with self.subTest(key=key), self.assertRaises(MissionWorkspaceError):
                self.manager.ensure(self.mid, repository)

    def test_ensure_refuses_already_confirmed(self):
        with self.assertRaises(MissionWorkspaceError):
            self.manager.ensure(self.mid, dict(self.placeholder(), isolation_confirmed=True))

    def test_manager_without_held_lease_cannot_touch_workspace(self):
        manager = MissionWorkspaceManager(self.root)
        with self.assertRaises(MissionWorkspaceError):
            manager.ensure(self.mid, self.placeholder())

    def test_lease_for_another_repository_is_refused(self):
        other = Path(self.tmp.name).resolve() / "other"
        other.mkdir()
        _git(other, "init")
        with self.assertRaises(MissionWorkspaceError):
            MissionWorkspaceManager(other, lease=self.lease)


if __name__ == "__main__":
    unittest.main()
