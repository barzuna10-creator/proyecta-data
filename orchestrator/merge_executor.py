"""Mission 004 -- drives MERGING -> MERGED.

Merge strategy is a fixed constant, never a parameter: only `--merge`
(a true merge commit) is ever invoked, never --squash or --rebase, so the
exact commit Emma reviewed remains, unchanged, an ancestor of the
resulting base branch.

Pre-merge re-verification distinguishes gating checks (head SHA matches
the recorded reviewed identity; CI is currently success for that exact
head; the PR's mergeStateStatus is CLEAN) from informational-only ones
(whether the base branch has advanced since this mission's scope was
authorized -- unrelated, harmless concurrent activity from other
missions is expected and must not block a merge that is otherwise safe;
the CLEAN mergeability check is what actually captures real conflict
risk).

M3 (Concurrent Publish/Merge Live Validation & Hardening): the actual
`gh pr merge` call, its immediate post-merge re-read, and the failure-
path Chugel persistence performed by _block() on any of that block's
three failure branches, are now wrapped in `chugel.
merge_serialization_lock()` -- a cross-mission, kernel-released-on-
crash lock closing the one real race this module's own concurrency
story left open: M2 proved multiple missions build/review truly
concurrently, but nothing previously stopped two of them from
attempting the literal merge subprocess at the same instant. The
pre-merge gating checks above, and pushes/PR-creation in
publish_executor.py, remain unlocked and unchanged -- already
per-branch and safe by construction, and this pre-merge re-verification
already correctly refuses an unsafe merge on its own; the lock only
makes the *ordering* of contended attempts deterministic, it does not
change what counts as safe to merge."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from orchestrator import chugel
from orchestrator import publish_identity_repair
from orchestrator.gh_check_status import normalize_check_entry

_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 65536


@dataclass(frozen=True)
class ExecutorResult:
    status: str  # "COMPLETED" | "HUMAN_ACTION_REQUIRED"
    state: str
    reason: str = ""


class MergeExecutorError(Exception):
    pass


def _run(argv: list[str], *, cwd: str | None = None, timeout: float = _TIMEOUT_SECONDS):
    try:
        result = subprocess.run(
            argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeExecutorError(f"{argv[0]} failed to run: {exc}") from exc
    if len(result.stdout) > _MAX_OUTPUT_BYTES or len(result.stderr) > _MAX_OUTPUT_BYTES:
        raise MergeExecutorError(f"{argv[0]} produced unexpectedly large output")
    return result


def _pr_view(pr_number: int, *, gh_executable: str, repository_root: str) -> dict:
    result = _run(
        [gh_executable, "pr", "view", str(pr_number), "--json",
         "state,headRefOid,mergeable,mergeStateStatus,mergeCommit,statusCheckRollup"],
        cwd=repository_root,
    )
    if result.returncode != 0:
        raise MergeExecutorError(f"gh pr view {pr_number} failed (exit {result.returncode})")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MergeExecutorError("gh pr view returned unparseable output") from exc


def _origin_main_sha(*, git_executable: str, repository_root: str) -> str | None:
    result = _run([git_executable, "rev-parse", "origin/main"], cwd=repository_root)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace").strip()


def _ci_conclusion(pr_view: dict) -> str | None:
    """Single-shot pre-merge gate -- only success vs. not-success matters
    here (never cancelled/timed_out as distinct outcomes, unlike
    publish_executor.py's bounded poller). Per-entry normalization
    (CheckRun vs. StatusContext, fail-closed on anything unrecognized) is
    shared with orchestrator/publish_executor.py via
    orchestrator/gh_check_status.py."""
    checks = pr_view.get("statusCheckRollup") or []
    if not checks:
        return None
    statuses = [normalize_check_entry(c) for c in checks]
    if any(s == "PENDING" for s in statuses):
        return None
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in statuses):
        return "success"
    return "failure"


def _block(mission_id: str, reason: str) -> ExecutorResult:
    chugel.transition(mission_id, "BLOCKED", actor="chugel", reason=reason)
    return ExecutorResult("HUMAN_ACTION_REQUIRED", "BLOCKED", reason)


def run(
    mission_id: str,
    *,
    repository_root: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
) -> ExecutorResult:
    record = chugel.get_mission(mission_id)
    if record["state"] != "MERGING":
        raise ValueError(f"mission {mission_id}: merge_executor.run() requires "
                          f"MERGING, got {record['state']!r}")

    # Step 0 -- defensive repair. Only reachable in practice if the
    # coordinator's own pre-authorization repair call (mission_coordinator.py)
    # was somehow skipped; a genuine no-op otherwise. repair_if_needed()
    # itself requires MERGE_AWAITING_AUTHORIZATION, which this mission is
    # already past -- so this call only matters, and only fires, in the
    # narrow case publish.commit_sha is still unset here, which cannot
    # happen via the normal coordinator path but is checked anyway rather
    # than assumed.
    if (record.get("publish") or {}).get("commit_sha") is None:
        return _block(mission_id, "publish.commit_sha missing at MERGING -- "
                                   "repair must run before entering MERGING")

    pr_number = record["publish"]["pr_number"]
    try:
        view = _pr_view(pr_number, gh_executable=gh_executable, repository_root=repository_root)
    except MergeExecutorError as exc:
        return _block(mission_id, str(exc))

    reviewed_sha = record["publish"]["commit_sha"]

    # Step 1 -- gating pre-merge checks.
    if view["headRefOid"] != reviewed_sha:
        return _block(mission_id, "live PR head no longer matches the reviewed commit_sha")
    if _ci_conclusion(view) != "success":
        return _block(mission_id, "CI is not currently success for this exact head")
    if view.get("mergeStateStatus") != "CLEAN":
        return _block(mission_id, f"PR is not cleanly mergeable "
                                   f"(mergeStateStatus={view.get('mergeStateStatus')!r})")

    # Informational only -- never gates. Recorded via the block reason
    # only if this ever becomes relevant to a human debugging a BLOCKED
    # mission; a harmless base advance from an unrelated mission is
    # expected and, given the CLEAN check above already passed, safe.
    _origin_main_sha(git_executable=git_executable, repository_root=repository_root)

    # Step 2 -- check-before-merge.
    if view["state"] == "MERGED":
        merge_commit_sha = (view.get("mergeCommit") or {}).get("oid")
        if not merge_commit_sha:
            return _block(mission_id, "PR already merged but no merge commit SHA is reported")
    elif view["state"] == "CLOSED":
        return _block(mission_id, "PR was closed without merging")
    elif view["state"] != "OPEN":
        return _block(mission_id, f"PR is in an unexpected state {view['state']!r}")
    else:
        # M3: serialize the one real concurrency hazard -- two missions'
        # `gh pr merge` calls racing the shared base branch at literally
        # the same instant. See chugel.merge_serialization_lock()'s own
        # docstring for the full design rationale. The pre-merge gating
        # checks above and push/PR-creation in publish_executor.py are
        # already per-branch and safe without it, and are deliberately
        # left outside this block. Inside it: the mutating `gh pr merge`
        # call, its immediate post-merge re-read, and -- on any of the
        # three failure branches below -- the _block() call's own
        # Chugel persistence (a per-mission record read/write/fsync,
        # itself serialized against other mutators of THIS mission by
        # the separate, per-mission _mission_lock() acquired inside
        # chugel.transition()). That nesting (this global lock held
        # outer, a per-mission lock taken inner) is safe and never
        # reversed anywhere else in the codebase, so it cannot deadlock
        # -- but it does mean held time on a failure path is the merge
        # attempt plus one Chugel write, not the bare subprocess call
        # alone.
        with chugel.merge_serialization_lock():
            try:
                result = _run(
                    [gh_executable, "pr", "merge", str(pr_number), "--merge", "--delete-branch=false"],
                    cwd=repository_root, timeout=_TIMEOUT_SECONDS,
                )
            except MergeExecutorError as exc:
                return _block(mission_id, str(exc))
            if result.returncode != 0:
                return _block(mission_id, f"gh pr merge failed (exit {result.returncode}): "
                                           f"{result.stderr.decode('utf-8', 'replace')}")
            post = _pr_view(pr_number, gh_executable=gh_executable, repository_root=repository_root)
            merge_commit_sha = (post.get("mergeCommit") or {}).get("oid")
            if not merge_commit_sha:
                return _block(mission_id, "merge reported success but no merge commit SHA is available")

    chugel.record_merge_commit(mission_id, merge_commit_sha)
    chugel.transition(mission_id, "MERGED", actor="chugel", reason="merge executed")
    return ExecutorResult("COMPLETED", "MERGED")
