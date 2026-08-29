"""orchestrator/publish_commit_materializer.py -- real, disposable git
repositories (no mocking of git itself): commit-mode no-op, patch-mode
materialization, fail-closed on tamper/drift, and idempotent restart.

Missions are driven through the real Mission 004 pipeline (create_mission
-> record_repository_state -> scope authorization -> run_mission with a
deterministic AgentInvoker), exactly like tests/test_orchestrator_publish_
executor.py's own fixture, so the PASS-verdict/builder-artifact lookup this
module reuses from publish_identity_repair.py is exercised against a real,
validated Mission Record -- not a hand-assembled dict."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import orchestrator.chugel as chugel
from orchestrator.autonomous_runner import run_mission
from orchestrator.publish_commit_materializer import (
    MaterializeCommitError,
    materialize_reviewed_commit,
)
from tests.test_orchestrator_autonomous_runner import (
    _FakeAdapter,
    _create_intake_mission,
    _scope_gate_approval,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, stdout=subprocess.PIPE,
    ).stdout.decode("ascii").strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "zentra-test@example.invalid")
    _git(repo, "config", "user.name", "Zentra Test")
    (repo / "a.txt").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return _head(repo)


def _diff_sha256(repo: Path, base_sha: str) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary", base_sha], cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _builder_evidence_with_artifact(artifact: dict, attempt=0):
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": artifact,
        "changed_files": [{"path": "a.txt", "reason": "x"}],
        "checks": [{"command": "true", "working_directory": "/tmp", "exit_status": 0, "result": "ok"}],
        "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True, "no_remote_action": True,
            "no_production_access": True, "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": None,
        "conclusion": {"text": "ok", "label": "FACT"},
    }


def _reviewer_evidence_with_artifact(artifact: dict, attempt=0, verdict="PASS"):
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:05:00Z",
        "artifact_identity_confirmed_at_start": artifact,
        "artifact_identity_confirmed_before_conclusion": artifact,
        "rechecked_commands": [],
        "findings": [],
        "verdict": verdict,
        "blocked_reason": None,
    }


class PublishCommitMaterializerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self._repo = Path(self._tmpdir.name) / "repo"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_with_pass_verdict(self, artifact: dict, base_sha: str) -> str:
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": str(self._repo), "branch": "overnight/synthetic",
            "base_sha": base_sha, "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        adapters = {
            "codex": _FakeAdapter([dict(
                outcome="completed", model="codex-1", fresh_context_attested=True,
                provider_session_id=None, provider_conversation_id="builder-thread",
                evidence=_builder_evidence_with_artifact(artifact), error_detail=None, provider="codex",
            )]),
            "claude": _FakeAdapter([dict(
                outcome="completed", model="claude-1", fresh_context_attested=True,
                provider_session_id=None, provider_conversation_id="reviewer-thread",
                evidence=_reviewer_evidence_with_artifact(artifact), error_detail=None, provider="claude",
            )]),
        }
        run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(chugel.get_mission(mid)["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        return mid

    # --- mode == "commit" ---------------------------------------------------

    def test_commit_mode_reachable_is_a_noop(self):
        base_sha = _init_repo(self._repo)
        (self._repo / "a.txt").write_text("changed\n")
        _git(self._repo, "add", "-A")
        _git(self._repo, "commit", "-q", "-m", "the reviewed change")
        head_sha = _head(self._repo)

        artifact = {"mode": "commit", "commit_sha": head_sha,
                    "patch_path": None, "patch_sha256": None, "patch_byte_size": None}
        mid = self._mission_with_pass_verdict(artifact, base_sha)

        materialize_reviewed_commit(mid, str(self._repo), base_sha)

        self.assertEqual(_head(self._repo), head_sha)  # no new commit was made

    def test_commit_mode_unreachable_raises(self):
        base_sha = _init_repo(self._repo)
        artifact = {"mode": "commit", "commit_sha": "f" * 40,
                    "patch_path": None, "patch_sha256": None, "patch_byte_size": None}
        mid = self._mission_with_pass_verdict(artifact, base_sha)

        with self.assertRaises(MaterializeCommitError):
            materialize_reviewed_commit(mid, str(self._repo), base_sha)

    # --- mode == "patch" -----------------------------------------------------

    def test_patch_mode_materializes_exactly_one_matching_commit(self):
        base_sha = _init_repo(self._repo)
        (self._repo / "a.txt").write_text("reviewed change\n")
        expected_hash = _diff_sha256(self._repo, base_sha)

        artifact = {"mode": "patch", "commit_sha": None,
                    "patch_path": "/unused/artifact.patch",
                    "patch_sha256": expected_hash, "patch_byte_size": 42}
        mid = self._mission_with_pass_verdict(artifact, base_sha)

        materialize_reviewed_commit(mid, str(self._repo), base_sha)

        head_sha = _head(self._repo)
        self.assertNotEqual(head_sha, base_sha)
        log = subprocess.run(
            ["git", "log", "--oneline", f"{base_sha}..HEAD"], cwd=self._repo,
            check=True, stdout=subprocess.PIPE,
        ).stdout.decode("utf-8").strip().splitlines()
        self.assertEqual(len(log), 1)  # exactly one new commit
        self.assertEqual(_diff_sha256(self._repo, base_sha), expected_hash)

    def test_patch_mode_drift_fails_closed_and_commits_nothing(self):
        base_sha = _init_repo(self._repo)
        (self._repo / "a.txt").write_text("NOT what was reviewed\n")

        artifact = {"mode": "patch", "commit_sha": None,
                    "patch_path": "/unused/artifact.patch",
                    "patch_sha256": "0" * 64,  # deliberately wrong
                    "patch_byte_size": 42}
        mid = self._mission_with_pass_verdict(artifact, base_sha)

        with self.assertRaises(MaterializeCommitError):
            materialize_reviewed_commit(mid, str(self._repo), base_sha)

        self.assertEqual(_head(self._repo), base_sha)  # nothing was committed
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self._repo, check=True, stdout=subprocess.PIPE,
        ).stdout.decode("utf-8")
        self.assertIn("a.txt", status)  # the uncommitted drifted change is untouched

    def test_patch_mode_is_idempotent_on_restart(self):
        base_sha = _init_repo(self._repo)
        (self._repo / "a.txt").write_text("reviewed change\n")
        expected_hash = _diff_sha256(self._repo, base_sha)

        artifact = {"mode": "patch", "commit_sha": None,
                    "patch_path": "/unused/artifact.patch",
                    "patch_sha256": expected_hash, "patch_byte_size": 42}
        mid = self._mission_with_pass_verdict(artifact, base_sha)

        materialize_reviewed_commit(mid, str(self._repo), base_sha)
        head_after_first = _head(self._repo)

        materialize_reviewed_commit(mid, str(self._repo), base_sha)  # simulated restart
        head_after_second = _head(self._repo)

        self.assertEqual(head_after_first, head_after_second)  # no second commit


if __name__ == "__main__":
    unittest.main()
