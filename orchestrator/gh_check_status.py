"""Mission 004 -- the single source of truth for interpreting one entry of
`gh pr view --json statusCheckRollup`.

statusCheckRollup is a GraphQL union of two node shapes, distinguished by
__typename (which `gh pr view --json statusCheckRollup` includes on every
entry) -- never by which fields happen to be present:

- CheckRun (the modern Checks API): `status` (QUEUED/IN_PROGRESS/
  COMPLETED/...) and `conclusion` (only non-null once status ==
  "COMPLETED": SUCCESS/FAILURE/NEUTRAL/CANCELLED/TIMED_OUT/
  ACTION_REQUIRED/STALE/SKIPPED).
- StatusContext (the legacy Status API -- e.g. a classic Vercel deployment
  status): `state` (SUCCESS/PENDING/ERROR/FAILURE, and sometimes
  EXPECTED). It has no `status`/`conclusion` fields at all.

Shared by orchestrator/publish_executor.py (a bounded poller distinguishing
5 outcomes: pending/success/cancelled/timed_out/failure) and
orchestrator/merge_executor.py (a single-shot pre-merge gate distinguishing
only success/not-success) -- both need the exact same per-entry
normalization; only their own aggregate reduction differs, by contract, so
that stays local to each module rather than being forced into one shared
shape here.

This is the one place that normalization is implemented -- both callers
import normalize_check_entry() rather than re-deriving it, so a future fix
or schema addition only has to happen once. See the incident this module
resolves: PR #5's real payload mixed a StatusContext ("Vercel", SUCCESS)
with a CheckRun (COMPLETED, SUCCESS); code that only ever read
conclusion/status treated the StatusContext entry as permanently pending,
because it has neither field."""

from __future__ import annotations

_CHECK_RUN_PENDING_STATUSES = frozenset({"QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "PENDING"})
_STATUS_CONTEXT_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
_STATUS_CONTEXT_SUCCESS_STATES = frozenset({"SUCCESS"})


def normalize_check_entry(entry: dict) -> str:
    """Returns one PENDING/SUCCESS/NEUTRAL/SKIPPED/CANCELLED/TIMED_OUT/
    FAILURE value per entry. Anything structurally unrecognized -- a
    missing or unfamiliar __typename, a COMPLETED CheckRun with no
    conclusion, an unfamiliar state/status/conclusion value -- normalizes
    to FAILURE: fail closed immediately with a clear BLOCKED reason,
    rather than silently waiting out a poll timeout (or, for a single-shot
    gate, silently treating an unrecognized shape as passing) looking like
    a hung or successful check when it is neither."""
    typename = entry.get("__typename")

    if typename == "CheckRun":
        status = entry.get("status")
        if status in _CHECK_RUN_PENDING_STATUSES:
            return "PENDING"
        if status != "COMPLETED":
            return "FAILURE"  # unrecognized status value -- fail closed
        conclusion = entry.get("conclusion")
        if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED", "CANCELLED", "TIMED_OUT"):
            return conclusion
        return "FAILURE"  # covers FAILURE/ACTION_REQUIRED/STALE/None/unknown

    if typename == "StatusContext":
        state = entry.get("state")
        if state in _STATUS_CONTEXT_PENDING_STATES:
            return "PENDING"
        if state in _STATUS_CONTEXT_SUCCESS_STATES:
            return "SUCCESS"
        return "FAILURE"  # covers ERROR/FAILURE/None/unknown

    return "FAILURE"  # unrecognized or missing __typename -- schema drift
