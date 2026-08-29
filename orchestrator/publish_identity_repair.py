"""Mission 004 -- repairs a missing `publish.commit_sha` when a mission is
observed at MERGE_AWAITING_AUTHORIZATION without one (a crash between the
CI_PENDING -> MERGE_AWAITING_AUTHORIZATION transition and the original
record_publish_commit() call in orchestrator/publish_executor.py).

Never trusts a live GitHub read as the reviewed commit identity on its
own. The only source of truth this module uses for "what was actually
built and confirmed-reviewed" is builder_evidence[attempt].artifact,
where `attempt` is the attempt whose reviewer_evidence carries the
literal verdict "PASS" -- the one and only verdict value that can ever
have led autonomous_runner.py to PUBLISH_AWAITING_AUTHORIZATION in the
first place, so at most one such reviewer_evidence entry can ever exist
in a given Mission Record. validate_mission_record() already guarantees,
at every write, that entry's artifact_identity_confirmed_before_conclusion
is structurally identical to the matching builder_evidence attempt's own
artifact (orchestrator/validator.py's _check_artifact_identity_consistency())
-- this module reads that already-enforced fact, it does not re-derive or
re-check it.

A live PR head read is used only as one of two independent inputs to an
equality comparison against that durable identity -- never as the value
recorded on its own."""

from __future__ import annotations

import subprocess

from orchestrator import chugel

_MAX_STDOUT_BYTES = 4096
_TIMEOUT_SECONDS = 10.0


class PublishIdentityRepairError(Exception):
    pass


def durable_reviewed_artifact(record: dict) -> dict | None:
    """The one, already-existing, write-time-enforced source of "what was
    actually built and confirmed-reviewed" -- the full artifact dict
    (mode/commit_sha/patch_path/patch_sha256/patch_byte_size) for the
    attempt whose reviewer_evidence carries the literal verdict "PASS".
    Returns None when no PASS verdict exists yet. Shared with
    orchestrator/publish_commit_materializer.py, which needs the whole
    artifact (including patch-mode identity), not just a commit_sha --
    this is the single place that lookup is implemented."""
    reviewer_entries = record.get("reviewer_evidence") or []
    passed = next((e for e in reviewer_entries if e.get("verdict") == "PASS"), None)
    if passed is None:
        return None
    builder_entries = record.get("builder_evidence") or []
    builder = next((e for e in builder_entries if e.get("attempt") == passed.get("attempt")), None)
    if builder is None:
        return None
    return builder.get("artifact") or None


def _durable_reviewed_commit_sha(record: dict) -> str | None:
    """Returns None when no PASS verdict exists yet, or when the matching
    builder attempt's artifact is patch-mode (no commit_sha exists at
    all for that shape of evidence) -- both are legitimate "cannot
    repair" outcomes, not errors."""
    artifact = durable_reviewed_artifact(record)
    if artifact is None or artifact.get("mode") != "commit":
        return None
    return artifact.get("commit_sha")


def _live_pr_head_sha(pr_number: int, *, gh_executable: str) -> str:
    try:
        result = subprocess.run(
            [gh_executable, "pr", "view", str(pr_number), "--json", "headRefOid"],
            shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishIdentityRepairError(f"gh pr view failed: {exc}") from exc
    if result.returncode != 0 or len(result.stdout) > _MAX_STDOUT_BYTES:
        raise PublishIdentityRepairError(
            f"gh pr view {pr_number} did not succeed (exit {result.returncode})"
        )
    import json
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
        head = payload["headRefOid"]
    except (UnicodeDecodeError, ValueError, KeyError) as exc:
        raise PublishIdentityRepairError("gh pr view returned unparseable output") from exc
    if not isinstance(head, str) or not head:
        raise PublishIdentityRepairError("gh pr view returned no headRefOid")
    return head


def repair_if_needed(mission_id: str, *, gh_executable: str = "gh") -> bool:
    """Precondition: state == MERGE_AWAITING_AUTHORIZATION. No-op (returns
    False) when publish.commit_sha is already set -- never a second
    record_publish_commit() call, which is not itself idempotent. Returns
    True only when a repair was actually performed. Fails closed to
    BLOCKED, writing nothing to publish.commit_sha, when no durable
    reviewed identity exists, or when the live PR head does not match it
    exactly -- this is the one place this module refuses to guess."""
    record = chugel.get_mission(mission_id)
    if record.get("state") != "MERGE_AWAITING_AUTHORIZATION":
        raise ValueError(
            f"mission {mission_id}: repair_if_needed() requires state "
            f"'MERGE_AWAITING_AUTHORIZATION', got {record.get('state')!r}"
        )
    if (record.get("publish") or {}).get("commit_sha") is not None:
        return False

    reviewed = _durable_reviewed_commit_sha(record)
    if reviewed is None:
        chugel.transition(
            mission_id, "BLOCKED", actor="chugel",
            reason="publish.commit_sha missing and no independently reviewed "
                   "commit identity exists to repair from",
        )
        return False

    pr_number = (record.get("publish") or {}).get("pr_number")
    if pr_number is None:
        chugel.transition(
            mission_id, "BLOCKED", actor="chugel",
            reason="publish.commit_sha missing and no PR identity exists to "
                   "read a live head from",
        )
        return False

    try:
        live_head = _live_pr_head_sha(pr_number, gh_executable=gh_executable)
    except PublishIdentityRepairError as exc:
        chugel.transition(
            mission_id, "BLOCKED", actor="chugel",
            reason=f"could not read live PR head to repair publish identity: {exc}",
        )
        return False

    if live_head != reviewed:
        chugel.transition(
            mission_id, "BLOCKED", actor="chugel",
            reason="live PR head does not match the independently reviewed "
                   "commit_sha -- refusing to infer publish identity",
        )
        return False

    chugel.record_publish_commit(mission_id, reviewed)
    return True
