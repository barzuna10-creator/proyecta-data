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
then crashed before push), this is a no-op -- never a second commit.

Corrective (found via a real, live end-to-end run, not a unit test): a
mission whose reviewed artifact is "create a brand-new file" -- one that
was never `git add`-ed, still untracked in the worktree -- used to
BLOCKED here every single time, even when the artifact was completely
correct and matched what Emma actually reviewed. Root cause: `git diff
<base_sha>` alone never includes untracked files, only modifications to
files git already tracks -- so the live-identity hash this module
computed was always the hash of an empty diff for that case, permanently
mismatching the real patch_sha256. Fixed in `_diff_sha256()` below by
using the identical `git add -N` / `git diff --binary` / `git reset`
technique orchestrator/adapters/codex_cli_adapter.py and
orchestrator/adapters/claude_cli_adapter.py already use to compute the
very same patch_sha256 this function's result is compared against (see
both adapters' `_compute_uncommitted_patch_artifact()`) -- not a
different, merely-similar method, so both sides of the comparison always
mean the same thing by "the diff". Deliberately duplicated here rather
than imported from either adapter, matching this codebase's own existing
convention (see claude_cli_adapter.py's module docstring): this is pure
git-subprocess mechanics with no adapter-specific behavior in it, and a
third independent copy keeps this module's own audit boundary intact
exactly as the first two already do for each other -- introducing a new
shared module across adapters and the publish pipeline would be a larger
architectural change than this fix calls for.

Corrective, round 2 (an independent review of the round-1 fix above found
two further gaps, both closed in `_diff_sha256()`, see its own docstring
for detail): the round-1 fix still capped diff/reset output at
_MAX_OUTPUT_BYTES, a limit the adapters' own patch_sha256 computation
never applies -- any real patch over that cap raised here even though it
was genuinely correct and matched what was reviewed, so the fix only
actually worked for small new files. And `git add -N` ran before the
try/finally, so a failure or timeout there could skip the `git reset`
cleanup. Both closed; see `_diff_sha256()`'s docstring for the two
remaining, deliberately undefended preconditions (clean index, base_sha
== current HEAD) that round 2 documents rather than enforces, as out of
this corrective's scope."""

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


def _run(argv: list[str], *, cwd: str, timeout: float = _TIMEOUT_SECONDS,
         max_output_bytes: int | None = _MAX_OUTPUT_BYTES):
    """`max_output_bytes` defaults to the existing _MAX_OUTPUT_BYTES cap
    for every ordinary call in this module (git add/commit/cat-file/
    rev-parse are never expected to produce more than a few bytes, so an
    unexpectedly huge one is itself a signal something is wrong -- the
    cap stays a genuine safety check there). Pass None to disable it
    entirely for a specific call -- used only by _diff_sha256() below,
    where the output IS expected to be large (a real patch) and capping
    it would silently make this module's identity hash diverge from the
    adapters' own uncapped patch_sha256 computation for any patch over
    the cap (round-1 independent review, P2)."""
    try:
        result = subprocess.run(
            argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaterializeCommitError(f"{argv[0]} failed to run: {exc}") from exc
    if max_output_bytes is not None and (
        len(result.stdout) > max_output_bytes or len(result.stderr) > max_output_bytes
    ):
        raise MaterializeCommitError(f"{argv[0]} produced unexpectedly large output")
    return result


def _diff_sha256(repository_root: str, base_sha: str, *, git_executable: str) -> str:
    """The canonical live-worktree identity computation this module
    compares against patch_sha256, everywhere it is called (the
    idempotent-resume check, the pre-commit check, and the post-commit
    re-verification). `git add -N -- .` (intent-to-add: stages untracked
    paths' names into the index without staging their content) runs
    immediately before the diff so brand-new, still-untracked files are
    included as new-file diffs too -- exactly the same technique, in the
    same order, that orchestrator/adapters/codex_cli_adapter.py and
    orchestrator/adapters/claude_cli_adapter.py already use to compute
    the patch_sha256 this result is compared against. Both that call and
    `git diff` itself run with max_output_bytes=None (round-1 independent
    review, P2): the adapters impose no size cap on their own patch_sha256
    computation, so capping this side would make a real, correct, large
    patch mismatch for no reason other than its own size -- the two sides
    of the identity comparison must stay comparable regardless of patch
    size, not just for small ones. `git reset` runs immediately after,
    inside the SAME try/finally as `git add -N` (round-1 independent
    review, P3: the add call previously sat before the try, so a failure
    or timeout there could skip cleanup and leak an intent-to-add entry
    into the index -- moving it inside closes that gap, so this function
    now genuinely always attempts to undo its own staging on any exit
    path, matching what its own docstring already claimed). `git reset`'s
    own exit status is still deliberately not checked, matching both
    adapters' identical best-effort cleanup step.

    Unstated preconditions this function relies on but does not itself
    enforce, flagged rather than fixed here per this corrective's own
    scope (round-1 independent review, P3 -- resolving either would mean
    snapshotting/restoring the index or asserting repository state before
    every call, a larger behavioral change than this fix's mandate):
    the repository's index is expected to be clean (nothing already
    staged) when this function runs, exactly as record_repository_state()'s
    own isolation_confirmed already implies for a mission's worktree --
    if some other process staged content in the same worktree
    concurrently, `git reset` would silently discard that staging as a
    side effect of what is otherwise a read-only identity check. And the
    byte-for-byte equivalence with the adapters' own patch_sha256 holds
    only under that same clean-index assumption plus the caller-enforced
    base_sha == current HEAD precondition on the pre-commit path (already
    checked explicitly in materialize_reviewed_commit() below) -- neither
    is asserted defensively inside this function itself."""
    try:
        add = _run([git_executable, "add", "-N", "--", "."], cwd=repository_root, max_output_bytes=None)
        if add.returncode != 0:
            raise MaterializeCommitError(
                f"git add -N failed while computing the live diff identity "
                f"(exit {add.returncode}): {add.stderr.decode('utf-8', 'replace')}"
            )
        result = _run(
            [git_executable, "diff", "--binary", base_sha], cwd=repository_root, max_output_bytes=None,
        )
    finally:
        _run([git_executable, "reset"], cwd=repository_root, max_output_bytes=None)
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
