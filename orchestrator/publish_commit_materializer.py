"""Mission 004 -- materializes the exact independently-reviewed artifact as
a real git commit before publish_executor.py ever pushes.

The CLI adapters never commit anything themselves (mode="patch" is the
normal, expected shape of builder_evidence[attempt].artifact); nothing in
Mission 004 previously turned that reviewed patch into a real commit
before publish. This module is that missing step, called from
orchestrator/publish_executor.py's PUBLISHING branch, before
_git_push().

Never trusts the live worktree diff, or the artifact's own patch_path
file, on its word alone -- the live, uncommitted diff against the
mission's recorded base_sha must independently hash to the exact
patch_sha256 already durably recorded for the PASS-verdict attempt
(orchestrator/publish_identity_repair.py's durable_reviewed_artifact(),
reused here rather than re-derived) before anything is committed, and
the resulting commit's own diff is re-verified against that same hash
immediately afterward. Any mismatch, at any point, raises rather than
guesses; orchestrator/publish_executor.py's run() catches
PublishExecutorError (this module's own MaterializeCommitError is a
subclass of it, so the existing except-block already turns any failure
here into BLOCKED, exactly like every other failure in that function).

Idempotent by construction: if HEAD has already moved past base_sha in a
way whose diff matches patch_sha256 (a prior attempt already committed,
then crashed before push), this is a no-op -- never a second commit."""

from __future__ import annotations

import hashlib
import subprocess

from orchestrator import chugel
from orchestrator.publish_executor import PublishExecutorError
from orchestrator.publish_identity_repair import durable_reviewed_artifact

_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 65536


class MaterializeCommitError(PublishExecutorError):
    pass


def _run(argv: list[str], *, cwd: str, timeout: float = _TIMEOUT_SECONDS):
    try:
        result = subprocess.run(
            argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaterializeCommitError(f"{argv[0]} failed to run: {exc}") from exc
    if len(result.stdout) > _MAX_OUTPUT_BYTES or len(result.stderr) > _MAX_OUTPUT_BYTES:
        raise MaterializeCommitError(f"{argv[0]} produced unexpectedly large output")
    return result


def _diff_sha256(repository_root: str, base_sha: str, *, git_executable: str) -> str:
    result = _run([git_executable, "diff", "--binary", base_sha], cwd=repository_root)
    if result.returncode != 0:
        raise MaterializeCommitError(
            f"git diff {base_sha} failed (exit {result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace')}"
        )
    return hashlib.sha256(result.stdout).hexdigest()


def _head_sha(repository_root: str, *, git_executable: str) -> str:
    result = _run([git_executable, "rev-parse", "HEAD"], cwd=repository_root)
    if result.returncode != 0:
        raise MaterializeCommitError(f"git rev-parse HEAD failed (exit {result.returncode})")
    return result.stdout.decode("ascii").strip()


def materialize_reviewed_commit(
    mission_id: str,
    repository_root: str,
    base_sha: str,
    *,
    git_executable: str = "git",
) -> None:
    """Precondition: state == PUBLISHING (checked by the caller,
    publish_executor.run(), not here). Performs no Chugel transition on
    the happy path; raises MaterializeCommitError on any failure, which
    the caller's existing except-block turns into BLOCKED."""
    record = chugel.get_mission(mission_id)
    artifact = durable_reviewed_artifact(record)
    if artifact is None:
        raise MaterializeCommitError(
            "no independently reviewed (PASS-verdict) artifact exists to materialize"
        )

    if artifact.get("mode") == "commit":
        expected_sha = artifact.get("commit_sha")
        check = _run(
            [git_executable, "cat-file", "-e", f"{expected_sha}^{{commit}}"],
            cwd=repository_root,
        )
        if check.returncode != 0:
            raise MaterializeCommitError(
                f"reviewed commit {expected_sha!r} is not reachable in this worktree"
            )
        return  # already a real commit; nothing to materialize

    expected_hash = artifact.get("patch_sha256")
    if not expected_hash:
        raise MaterializeCommitError(
            "reviewed artifact is patch-mode but has no patch_sha256 recorded"
        )

    current_head = _head_sha(repository_root, git_executable=git_executable)

    if current_head != base_sha:
        # Idempotent resume: a prior attempt may have already committed
        # (and possibly crashed before the subsequent push).
        already = _diff_sha256(repository_root, base_sha, git_executable=git_executable)
        if already == expected_hash:
            return  # already materialized -- no-op
        raise MaterializeCommitError(
            "worktree HEAD has diverged from the mission's recorded base_sha "
            "in a way that does not match the reviewed patch -- refusing to guess"
        )

    live_hash = _diff_sha256(repository_root, base_sha, git_executable=git_executable)
    if live_hash != expected_hash:
        raise MaterializeCommitError(
            "live uncommitted worktree diff does not match the independently "
            f"reviewed patch_sha256 ({expected_hash!r} expected, got {live_hash!r}) "
            "-- refusing to commit"
        )

    add = _run([git_executable, "add", "-A"], cwd=repository_root)
    if add.returncode != 0:
        raise MaterializeCommitError(
            f"git add failed (exit {add.returncode}): {add.stderr.decode('utf-8', 'replace')}"
        )
    commit = _run(
        [git_executable, "commit", "-m",
         f"materialize reviewed artifact for mission {mission_id}"],
        cwd=repository_root,
    )
    if commit.returncode != 0:
        raise MaterializeCommitError(
            f"git commit failed (exit {commit.returncode}): "
            f"{commit.stderr.decode('utf-8', 'replace')}"
        )

    post_hash = _diff_sha256(repository_root, base_sha, git_executable=git_executable)
    if post_hash != expected_hash:
        raise MaterializeCommitError(
            "post-commit diff no longer matches the independently reviewed "
            "patch_sha256 -- refusing to proceed"
        )
