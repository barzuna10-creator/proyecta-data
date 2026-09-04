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

M3 (Concurrent Publish/Merge Live Validation & Hardening): the actual,
real, mutating `gh pr merge` call is wrapped in `chugel.
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
change what counts as safe to merge.

M3 merge recovery hardening: the immediate post-merge re-read and any
failure-path Chugel persistence happen AFTER releasing the lock, not
inside it -- see the lock's own call site below for why. A real,
disposable-repo live-acceptance run demonstrated that a killed/crashed
local `gh pr merge` client does not cancel the mutation server-side:
GitHub can, and did, complete the authorized merge independently of
whatever this process observed or was killed before observing. Any
non-definitive local outcome (a non-zero exit, a raised
MergeExecutorError, or a zero-exit result whose immediate post-merge
check doesn't itself confirm a clean, identity-matched MERGED state)
is never treated as a definitive failure -- it is reconciled against
GitHub's own authoritative PR state by
_reconcile_ambiguous_merge_outcome(), bounded and read-only (it never
re-issues `gh pr merge`), converging to MERGED only on an exact
identity match, to BLOCKED on a definitive non-merge or a mismatched/
contradictory identity, and to a distinctly-worded BLOCKED if the
ambiguity is never resolved within the bound."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from orchestrator import chugel
from orchestrator import publish_identity_repair
from orchestrator.gh_check_status import normalize_check_entry

_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 65536

# M3 merge recovery hardening: a real live-acceptance run (see the
# harness's own evidence) demonstrated that a crashed/killed local `gh
# pr merge` client does not necessarily cancel the mutation server-side
# -- GitHub can, and did, complete the authorized merge independently of
# whether this process ever observed a response. A bare, unbounded
# reconciliation poll could hang forever if GitHub itself never
# resolves the ambiguity; this bound guarantees run() always terminates
# and always converges to an explicit, evidence-backed outcome.
_MERGE_RECONCILE_MAX_ATTEMPTS = 5
_MERGE_RECONCILE_POLL_INTERVAL_SECONDS = 2.0


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


def _reconcile_ambiguous_merge_outcome(
    pr_number: int, mission_id: str, reviewed_sha: str, *, original_error: str,
    gh_executable: str, repository_root: str,
    poll_attempts: int = _MERGE_RECONCILE_MAX_ATTEMPTS,
    poll_interval: float = _MERGE_RECONCILE_POLL_INTERVAL_SECONDS,
) -> ExecutorResult:
    """M3 merge recovery hardening.

    Called whenever the authorized `gh pr merge` attempt did not
    immediately, cleanly confirm success -- a non-zero exit, a raised
    MergeExecutorError (including a killed/crashed local process,
    covered by the crash-barrier live-acceptance protocol), or a
    zero-exit result whose immediate post-merge `_pr_view()` read did
    not itself show a clean, identity-matched MERGED state. Never
    trusts that first, ambiguous local observation as a definitive
    failure: the real mutation request may already have reached GitHub
    and be completing (or have completed) entirely independently of
    what this process observed or was killed before observing.

    Deliberately never re-issues a mutating `gh pr merge` call anywhere
    in this function -- only read-only `gh pr view` reconciliation,
    bounded to `poll_attempts` tries `poll_interval` seconds apart, so
    this can never itself cause a duplicate real-world merge mutation
    and can never loop forever. This is intentionally NOT keyed to any
    specific error string (e.g. "Merge already in progress") -- it
    reconciles against GitHub's own authoritative PR state for ANY
    non-definitive local outcome, which is the actual underlying
    uncertain-outcome/idempotency problem, not one particular symptom
    of it.

    Resolves to exactly one of:
      - MERGED, with headRefOid == reviewed_sha and a real merge commit
        SHA: the authorized merge genuinely completed on GitHub --
        converges the Mission Record to MERGED via the same durable
        chugel.record_merge_commit()/transition() calls the normal
        success path uses.
      - CLOSED without ever having a merge commit: GitHub's own
        definitive, terminal "did not merge" outcome -- BLOCKED
        immediately, no further polling needed.
      - MERGED, but with headRefOid != reviewed_sha, or missing a
        merge commit SHA despite state MERGED: contradictory or
        mismatched identity evidence -- never silently accepted as
        success; BLOCKED with an explicit identity-mismatch reason.
      - Anything else (still OPEN throughout the whole bound, or
        `gh pr view` itself kept failing): genuinely, permanently
        ambiguous within this bound -- BLOCKED with a reason explicitly
        distinguishing "could not be resolved" from "confirmed failed",
        so a human resuming this mission knows to check GitHub directly
        rather than assume either outcome."""
    for attempt in range(poll_attempts):
        try:
            view = _pr_view(pr_number, gh_executable=gh_executable, repository_root=repository_root)
        except MergeExecutorError as exc:
            if attempt + 1 >= poll_attempts:
                return _block(
                    mission_id,
                    f"merge outcome for pr #{pr_number} remained ambiguous after {poll_attempts} "
                    f"reconciliation attempts -- gh pr view itself kept failing (last: {exc}); "
                    f"original attempt: {original_error}. Refusing to guess and refusing to issue "
                    "another merge mutation."
                )
            time.sleep(poll_interval)
            continue

        if view.get("state") == "MERGED":
            merge_commit_sha = (view.get("mergeCommit") or {}).get("oid")
            if view.get("headRefOid") != reviewed_sha or not merge_commit_sha:
                return _block(
                    mission_id,
                    f"reconciliation found pr #{pr_number} MERGED but with contradictory identity "
                    f"(headRefOid={view.get('headRefOid')!r}, expected {reviewed_sha!r}, "
                    f"merge_commit_sha={merge_commit_sha!r}) -- refusing to converge on unverified "
                    f"evidence. Original attempt: {original_error}"
                )
            chugel.record_merge_commit(mission_id, merge_commit_sha)
            chugel.transition(
                mission_id, "MERGED", actor="chugel",
                reason="reconciled: the authorized merge attempt completed on GitHub despite an "
                       f"ambiguous/non-definitive local result ({original_error})",
            )
            return ExecutorResult("COMPLETED", "MERGED")

        if view.get("state") == "CLOSED":
            return _block(
                mission_id,
                f"pr #{pr_number} was closed without merging (observed while reconciling an ambiguous "
                f"merge outcome; original attempt: {original_error})",
            )

        # Still OPEN (or any other non-terminal shape) -- the authorized
        # attempt may still be completing server-side. Keep polling
        # within the bound; never re-issue `gh pr merge` here.
        if attempt + 1 < poll_attempts:
            time.sleep(poll_interval)

    return _block(
        mission_id,
        f"merge outcome for pr #{pr_number} remained ambiguous after {poll_attempts} reconciliation "
        f"attempts ({poll_attempts * poll_interval:.0f}s) -- neither a confirmed MERGED nor a "
        f"definitive failure could be established. Original attempt: {original_error}. Refusing to "
        "guess and refusing to issue another merge mutation."
    )


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
        # left outside this block.
        #
        # M3 merge recovery hardening: the lock is held ONLY around the
        # single, real mutating `gh pr merge` call itself. A prior
        # version also held it across the immediate post-merge re-read
        # and every failure branch's Chugel write -- a real live-
        # acceptance run demonstrated a genuine gap in that shape: a
        # killed/crashed local `gh pr merge` client does not cancel the
        # mutation server-side, so the true outcome can remain
        # ambiguous for a real, non-negligible window afterward.
        # Reconciling that ambiguity (see _reconcile_ambiguous_merge_
        # outcome()) is read-only (never re-issues `gh pr merge`), so
        # holding the global lock through its bounded, multi-second
        # polling would only add needless latency to the OTHER
        # mission's own merge attempt without protecting anything --
        # the hazard this lock exists to prevent is two real mutating
        # calls in flight at once, not how long this mission takes to
        # interpret the result of its own single call. So the lock is
        # released before any reconciliation or Chugel write happens.
        with chugel.merge_serialization_lock():
            try:
                result = _run(
                    [gh_executable, "pr", "merge", str(pr_number), "--merge", "--delete-branch=false"],
                    cwd=repository_root, timeout=_TIMEOUT_SECONDS,
                )
                mutation_error = None if result.returncode == 0 else (
                    f"gh pr merge failed (exit {result.returncode}): "
                    f"{result.stderr.decode('utf-8', 'replace')}"
                )
            except MergeExecutorError as exc:
                mutation_error = str(exc)

        if mutation_error is None:
            post = _pr_view(pr_number, gh_executable=gh_executable, repository_root=repository_root)
            merge_commit_sha = (post.get("mergeCommit") or {}).get("oid")
            if post.get("state") == "MERGED" and post.get("headRefOid") == reviewed_sha and merge_commit_sha:
                chugel.record_merge_commit(mission_id, merge_commit_sha)
                chugel.transition(mission_id, "MERGED", actor="chugel", reason="merge executed")
                return ExecutorResult("COMPLETED", "MERGED")
            mutation_error = (
                f"gh pr merge reported success but the immediate post-merge check did not confirm a "
                f"clean, identity-matched MERGED state (state={post.get('state')!r}, "
                f"headRefOid={post.get('headRefOid')!r}, merge_commit_sha={merge_commit_sha!r})"
            )

        return _reconcile_ambiguous_merge_outcome(
            pr_number, mission_id, reviewed_sha, original_error=mutation_error,
            gh_executable=gh_executable, repository_root=repository_root,
        )

    chugel.record_merge_commit(mission_id, merge_commit_sha)
    chugel.transition(mission_id, "MERGED", actor="chugel", reason="merge executed")
    return ExecutorResult("COMPLETED", "MERGED")
