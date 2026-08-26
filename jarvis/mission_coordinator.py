"""Mission 004 -- the sole Jarvis module permitted to import
orchestrator.autonomous_runner, orchestrator.publish_executor,
orchestrator.merge_executor, and orchestrator.publish_identity_repair.

advance() drives a mission as far as it can go without a human, then
returns a CoordinatorReport describing exactly why it stopped -- a gate
needing José, a BLOCKED mission waiting for confirmation, a terminal
failure, or MERGED (ready for the executive summary). It never calls
chugel.decide_gate()/create_mission()/jarvis.mission_write.* itself with
a fabricated decision; every gate/resume decision it relays must already
be a real dict the calling turn built directly from José's own message,
this turn."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator import autonomous_runner, chugel, merge_executor, publish_executor
from orchestrator import publish_identity_repair
from orchestrator.agent_invocation import InvocationNotAuthorized

_GATE_STATES = frozenset({
    "SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION", "MERGE_AWAITING_AUTHORIZATION",
})
_TERMINAL_FAILURE_STATES = frozenset({"FAILED", "CANCELLED", "ROLLED_BACK"})


def _last_transition_reason(record: dict) -> str:
    """chugel.transition() appends to state_history but does not update
    the top-level state_reason field (which stays whatever create_mission()
    set it to, "mission created", forever) -- the reason for the current
    state is always state_history[-1]["reason"], never record["state_reason"]."""
    history = record.get("state_history") or []
    return history[-1].get("reason", "") if history else ""


@dataclass(frozen=True)
class CoordinatorReport:
    status: str  # "GATE_REQUIRED" | "BLOCKED" | "HUMAN_ACTION_REQUIRED" | "TERMINAL_FAILURE" | "MERGED"
    state: str
    gate_name: str | None = None
    reason: str = ""


def advance(
    mission_id: str,
    adapters: dict,
    *,
    repository_root: str,
    branch: str,
    pr_title: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
    ci_poll_timeout_seconds: float = 1800.0,
    ci_poll_interval_seconds: float = 30.0,
    build_review_deadline: float | None = None,
) -> CoordinatorReport:
    record = chugel.get_mission(mission_id)
    state = record["state"]

    if state == "SCOPE_AWAITING_AUTHORIZATION":
        return CoordinatorReport("GATE_REQUIRED", state, "scope_authorization")

    if state in ("AUTHORIZED", "BUILDING", "VERIFYING", "AWAITING_REVIEW", "REVIEWING",
                 "CHANGES_REQUIRED", "CORRECTING"):
        try:
            result = autonomous_runner.run_mission(
                mission_id, adapters, deadline=build_review_deadline,
            )
        except InvocationNotAuthorized as exc:
            return CoordinatorReport("HUMAN_ACTION_REQUIRED", state, reason=str(exc))
        if result.status == "AUTHORIZATION_REQUIRED":
            gate_name = {
                "SCOPE_AWAITING_AUTHORIZATION": "scope_authorization",
                "PUBLISH_AWAITING_AUTHORIZATION": "publish_authorization",
                "MERGE_AWAITING_AUTHORIZATION": "merge_authorization",
            }[result.state]
            return CoordinatorReport("GATE_REQUIRED", result.state, gate_name)
        if result.status == "TERMINAL_FAILURE":
            return CoordinatorReport("TERMINAL_FAILURE", result.state, reason=result.reason)
        if result.status == "COMPLETED":
            return advance(mission_id, adapters, repository_root=repository_root, branch=branch,
                            pr_title=pr_title, git_executable=git_executable, gh_executable=gh_executable,
                            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
                            ci_poll_interval_seconds=ci_poll_interval_seconds,
                            build_review_deadline=build_review_deadline)
        return CoordinatorReport("HUMAN_ACTION_REQUIRED", result.state, reason=result.reason)

    if state == "PUBLISH_AWAITING_AUTHORIZATION":
        return CoordinatorReport("GATE_REQUIRED", state, "publish_authorization")

    if state in ("PUBLISHING", "CI_PENDING"):
        result = publish_executor.run(
            mission_id, repository_root=repository_root, branch=branch, pr_title=pr_title,
            git_executable=git_executable, gh_executable=gh_executable,
            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
            ci_poll_interval_seconds=ci_poll_interval_seconds,
        )
        if result.status == "COMPLETED":
            return advance(mission_id, adapters, repository_root=repository_root, branch=branch,
                            pr_title=pr_title, git_executable=git_executable, gh_executable=gh_executable,
                            ci_poll_timeout_seconds=ci_poll_timeout_seconds,
                            ci_poll_interval_seconds=ci_poll_interval_seconds,
                            build_review_deadline=build_review_deadline)
        return CoordinatorReport("BLOCKED", result.state, reason=result.reason)

    if state == "MERGE_AWAITING_AUTHORIZATION":
        publish_identity_repair.repair_if_needed(mission_id, gh_executable=gh_executable)
        record = chugel.get_mission(mission_id)
        if record["state"] == "BLOCKED":
            return CoordinatorReport("BLOCKED", "BLOCKED", reason=_last_transition_reason(record))
        return CoordinatorReport("GATE_REQUIRED", record["state"], "merge_authorization")

    if state == "MERGING":
        result = merge_executor.run(
            mission_id, repository_root=repository_root,
            git_executable=git_executable, gh_executable=gh_executable,
        )
        if result.status == "COMPLETED":
            return CoordinatorReport("MERGED", "MERGED")
        return CoordinatorReport("BLOCKED", result.state, reason=result.reason)

    if state == "MERGED":
        return CoordinatorReport("MERGED", state)

    if state == "BLOCKED":
        return CoordinatorReport("BLOCKED", state, reason=_last_transition_reason(record))

    if state in _TERMINAL_FAILURE_STATES:
        return CoordinatorReport("TERMINAL_FAILURE", state, reason=_last_transition_reason(record))

    return CoordinatorReport("HUMAN_ACTION_REQUIRED", state, reason="no automatic action is defined")


def executive_summary(mission_id: str) -> str:
    record = chugel.get_mission(mission_id)
    outcome = record["mission_definition_history"][-1]["outcome"]
    state = record["state"]

    if state == "MERGED":
        result_line = "MERGED"
    elif state == "BLOCKED":
        result_line = f"BLOCKED: {_last_transition_reason(record)}"
    else:
        result_line = f"STOPPED: {state}"

    changed_files = []
    for entry in record.get("builder_evidence") or []:
        changed_files.extend(item["path"] for item in entry.get("changed_files") or [])

    verdicts = [e.get("verdict") for e in (record.get("reviewer_evidence") or [])]
    ci_runs = (record.get("publish") or {}).get("ci_runs") or []
    ci_line = ci_runs[-1]["conclusion"] if ci_runs else "n/a"

    pr_url = (record.get("publish") or {}).get("pr_url") or "n/a"
    merge_sha = (record.get("merge") or {}).get("merge_commit_sha") or "n/a"

    needs = "nothing" if state in ("MERGED",) else f"resolve {state} and confirm to continue"

    return (
        f"Mission 004 -- {outcome}\n"
        f"Result: {result_line}\n"
        f"What changed: {', '.join(sorted(set(changed_files))) or 'n/a'}\n"
        f"Verification: Emma verdict(s) {verdicts or 'n/a'}, CI {ci_line}\n"
        f"PR: {pr_url}   Merge commit: {merge_sha}\n"
        f"Needs from you: {needs}\n"
    )
