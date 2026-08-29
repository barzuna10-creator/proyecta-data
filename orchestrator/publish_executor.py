"""Mission 004 -- drives PUBLISHING -> CI_PENDING -> MERGE_AWAITING_AUTHORIZATION.

Unlike jarvis/repository_freshness.py's single, fixed-env, read-only `git
rev-parse`, the operations here (git push, gh pr create/view) are
authenticated, network, state-mutating calls that legitimately need the
caller's real git/gh credential environment (SSH agent, gh auth token,
git credential helper) -- a fixed, stripped env is not applicable to
them. argv is still always a fixed list (never shell=True, never
string-interpolated), and every call is bounded by an explicit timeout.

Idempotent by construction at every mutating step: git push against an
already-current branch is a safe no-op (no --force is ever used, so a
genuinely diverged branch fails the push rather than silently
overwriting); PR creation is check-before-act (query for an existing PR
for this branch before ever calling `gh pr create`); CI polling is
bounded, never indefinite. See record_publish_pr()/record_ci_run()/
record_publish_commit() in orchestrator/chugel.py -- presence of a
recorded value IS this module's idempotency signal, exactly as that
schema's own description states."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from orchestrator import chugel
from orchestrator.gh_check_status import normalize_check_entry

_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 65536


@dataclass(frozen=True)
class ExecutorResult:
    status: str  # "COMPLETED" | "HUMAN_ACTION_REQUIRED"
    state: str
    reason: str = ""


class PublishExecutorError(Exception):
    pass


def _run(argv: list[str], *, cwd: str | None = None, timeout: float = _TIMEOUT_SECONDS):
    try:
        result = subprocess.run(
            argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishExecutorError(f"{argv[0]} failed to run: {exc}") from exc
    if len(result.stdout) > _MAX_OUTPUT_BYTES or len(result.stderr) > _MAX_OUTPUT_BYTES:
        raise PublishExecutorError(f"{argv[0]} produced unexpectedly large output")
    return result


def _git_push(repository_root: str, branch: str, *, git_executable: str) -> None:
    result = _run([git_executable, "push", "origin", f"{branch}:{branch}"], cwd=repository_root)
    if result.returncode != 0:
        raise PublishExecutorError(
            f"git push failed (exit {result.returncode}): {result.stderr.decode('utf-8', 'replace')}"
        )


def _find_existing_pr(branch: str, *, gh_executable: str, repository_root: str) -> dict | None:
    result = _run(
        [gh_executable, "pr", "list", "--head", branch, "--state", "all",
         "--json", "number,url,state"],
        cwd=repository_root,
    )
    if result.returncode != 0:
        raise PublishExecutorError(f"gh pr list failed (exit {result.returncode})")
    try:
        entries = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PublishExecutorError("gh pr list returned unparseable output") from exc
    if not entries:
        return None
    # Most recently created first is not guaranteed by gh; a single branch
    # should have at most one PR under normal operation, so any match is used.
    return entries[0]


def _create_pr(branch: str, base: str, title: str, *, gh_executable: str, repository_root: str) -> dict:
    """`gh pr create` does not support `--json` (unlike `gh pr view`/`gh pr
    list`) -- on success it prints only the new PR's URL as plain text to
    stdout. This never parses that stdout. Immediately after a successful
    create, it delegates to _find_existing_pr() -- the same, already-correct
    `--json`-based query this module's check-before-create step already
    uses -- to obtain structured number/url/state from one single source
    of truth."""
    result = _run(
        [gh_executable, "pr", "create", "--head", branch, "--base", base,
         "--title", title, "--body", ""],
        cwd=repository_root, timeout=_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise PublishExecutorError(
            f"gh pr create failed (exit {result.returncode}): {result.stderr.decode('utf-8', 'replace')}"
        )
    created = _find_existing_pr(branch, gh_executable=gh_executable, repository_root=repository_root)
    if created is None:
        raise PublishExecutorError(
            "gh pr create reported success but no PR is now found for this branch"
        )
    return created


def _pr_view(pr_number: int, *, gh_executable: str, repository_root: str) -> dict:
    result = _run(
        [gh_executable, "pr", "view", str(pr_number), "--json",
         "state,headRefOid,mergeable,mergeStateStatus,statusCheckRollup"],
        cwd=repository_root,
    )
    if result.returncode != 0:
        raise PublishExecutorError(f"gh pr view {pr_number} failed (exit {result.returncode})")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PublishExecutorError("gh pr view returned unparseable output") from exc


def _ci_conclusion(pr_view: dict) -> str | None:
    """Reduce statusCheckRollup entries to a single terminal conclusion,
    or None while still pending. Any non-success terminal state on any
    check is treated as failure -- fail closed rather than average.
    Per-entry normalization (CheckRun vs. StatusContext, fail-closed on
    anything unrecognized) is shared with orchestrator/merge_executor.py
    via orchestrator/gh_check_status.py -- only this reduction, which
    needs the cancelled/timed_out distinction merge_executor's single-shot
    gate does not, is specific to this module."""
    checks = pr_view.get("statusCheckRollup") or []
    if not checks:
        return None
    statuses = [normalize_check_entry(c) for c in checks]
    if any(s == "PENDING" for s in statuses):
        return None
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in statuses):
        return "success"
    if any(s == "CANCELLED" for s in statuses):
        return "cancelled"
    if any(s == "TIMED_OUT" for s in statuses):
        return "timed_out"
    return "failure"


def run(
    mission_id: str,
    *,
    repository_root: str,
    branch: str,
    base: str = "main",
    pr_title: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
    ci_poll_timeout_seconds: float,
    ci_poll_interval_seconds: float,
) -> ExecutorResult:
    record = chugel.get_mission(mission_id)
    state = record["state"]
    if state not in ("PUBLISHING", "CI_PENDING"):
        raise ValueError(f"mission {mission_id}: publish_executor.run() requires "
                          f"PUBLISHING or CI_PENDING, got {state!r}")

    try:
        if state == "PUBLISHING":
            from orchestrator.publish_commit_materializer import materialize_reviewed_commit
            materialize_reviewed_commit(
                mission_id, repository_root, record["repository"]["base_sha"],
                git_executable=git_executable,
            )

            _git_push(repository_root, branch, git_executable=git_executable)

            existing = _find_existing_pr(branch, gh_executable=gh_executable, repository_root=repository_root)
            if existing is None:
                created = _create_pr(branch, base, pr_title, gh_executable=gh_executable, repository_root=repository_root)
                record = chugel.record_publish_pr(mission_id, created["url"], created["number"])
            elif existing["state"] == "OPEN":
                if (record["publish"] or {}).get("pr_number") is None:
                    record = chugel.record_publish_pr(mission_id, existing["url"], existing["number"])
            elif existing["state"] == "CLOSED":
                chugel.transition(mission_id, "BLOCKED", actor="chugel",
                    reason=f"found a closed, unmerged PR (#{existing['number']}) for branch {branch!r}")
                return ExecutorResult("HUMAN_ACTION_REQUIRED", "BLOCKED",
                    "closed-unmerged PR found for this branch")
            else:  # MERGED -- an earlier crashed run already completed this step
                if (record["publish"] or {}).get("pr_number") is None:
                    record = chugel.record_publish_pr(mission_id, existing["url"], existing["number"])

            record = chugel.transition(mission_id, "CI_PENDING", actor="chugel",
                reason="publication opened, awaiting CI")

        pr_number = record["publish"]["pr_number"]
        deadline = time.monotonic() + ci_poll_timeout_seconds
        conclusion = None
        while time.monotonic() < deadline:
            view = _pr_view(pr_number, gh_executable=gh_executable, repository_root=repository_root)
            conclusion = _ci_conclusion(view)
            run_id = f"pr-{pr_number}"
            if conclusion is not None:
                chugel.record_ci_run(mission_id, run_id=run_id, conclusion=conclusion)
                break
            chugel.record_ci_run(mission_id, run_id=run_id, conclusion="pending")
            time.sleep(ci_poll_interval_seconds)

        if conclusion == "success":
            record = chugel.transition(mission_id, "MERGE_AWAITING_AUTHORIZATION",
                actor="chugel", reason="CI succeeded")
            chugel.record_publish_commit(mission_id, view["headRefOid"])
            return ExecutorResult("COMPLETED", "MERGE_AWAITING_AUTHORIZATION")

        reason = f"CI concluded {conclusion!r}" if conclusion is not None else \
            f"CI timed out after {ci_poll_timeout_seconds}s"
        chugel.transition(mission_id, "BLOCKED", actor="chugel", reason=reason)
        return ExecutorResult("HUMAN_ACTION_REQUIRED", "BLOCKED", reason)

    except PublishExecutorError as exc:
        current = chugel.get_mission(mission_id)
        if current["state"] not in ("BLOCKED",):
            chugel.transition(mission_id, "BLOCKED", actor="chugel", reason=str(exc))
        return ExecutorResult("HUMAN_ACTION_REQUIRED", "BLOCKED", str(exc))
