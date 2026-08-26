"""Immutable, allow-listed projections of validated Chugel Mission Records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MissionBucket = Literal["running", "waiting_on_jose", "blocked", "terminal"]


@dataclass(frozen=True)
class GateStatus:
    name: str
    status: str


@dataclass(frozen=True)
class BuilderStatus:
    attempt: int
    conclusion_label: str
    conclusion_text: str


@dataclass(frozen=True)
class FindingStatus:
    finding_id: str
    severity: str
    summary: str
    file: str | None
    line_range: str | None
    category: str


@dataclass(frozen=True)
class ReviewerStatus:
    attempt: int
    verdict: str
    findings: tuple[FindingStatus, ...]


@dataclass(frozen=True)
class RepositoryStatus:
    branch: str
    base_sha: str
    isolation_confirmed: bool


@dataclass(frozen=True)
class MissionStatus:
    mission_id: str
    state: str
    bucket: MissionBucket
    updated_at: str
    mission_definition_version: int
    corrective_cycle_count: int
    repository: RepositoryStatus
    gates: tuple[GateStatus, ...]
    builder: tuple[BuilderStatus, ...]
    reviewer: tuple[ReviewerStatus, ...]
    human_action_required: str | None


_HUMAN_ACTION_BY_STATE = {
    "SCOPE_AWAITING_AUTHORIZATION": "scope_authorization",
    "PUBLISH_AWAITING_AUTHORIZATION": "publish_authorization",
    "MERGE_AWAITING_AUTHORIZATION": "merge_authorization",
    "BLOCKED": "human_direction",
}

_BUCKET_BY_STATE: dict[str, MissionBucket] = {
    "INTAKE": "running",
    "SCOPE_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "AUTHORIZED": "running",
    "BUILDING": "running",
    "VERIFYING": "running",
    "AWAITING_REVIEW": "running",
    "REVIEWING": "running",
    "CHANGES_REQUIRED": "running",
    "CORRECTING": "running",
    "PUBLISH_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "PUBLISHING": "running",
    "CI_PENDING": "running",
    "MERGE_AWAITING_AUTHORIZATION": "waiting_on_jose",
    "MERGING": "running",
    "MERGED": "running",
    "DEPLOY_PENDING": "running",
    "VERIFYING_PRODUCTION": "running",
    "COMPLETED": "terminal",
    "BLOCKED": "blocked",
    "FAILED": "terminal",
    "CANCELLED": "terminal",
    "ROLLED_BACK": "terminal",
}


class UnknownMissionState(ValueError):
    """A state outside the canonical V1 vocabulary cannot be classified."""


def classify_mission_state(state: str) -> MissionBucket:
    try:
        return _BUCKET_BY_STATE[state]
    except (KeyError, TypeError) as exc:
        raise UnknownMissionState("unknown mission state") from exc


def project_mission_status(record: dict[str, Any]) -> MissionStatus:
    """Copy only the documented V1 allow-list; never retain ``record`` values.

    The caller must provide a Mission Record already validated by Chugel. No
    unknown field, intent, provider identity/output, dispatch entry, raw gate
    decision, worktree path, or publication/deployment payload is projected.
    """
    repository = record["repository"]
    gates = record["human_gates"]
    return MissionStatus(
        mission_id=str(record["mission_id"]),
        state=str(record["state"]),
        bucket=classify_mission_state(record["state"]),
        updated_at=str(record["updated_at"]),
        mission_definition_version=len(record["mission_definition_history"]),
        corrective_cycle_count=int(record["corrective_cycle_count"]),
        repository=RepositoryStatus(
            branch=str(repository["branch"]),
            base_sha=str(repository["base_sha"]),
            isolation_confirmed=bool(repository["isolation_confirmed"]),
        ),
        gates=tuple(
            GateStatus(name=name, status=str(gates[name]["status"]))
            for name in ("scope_authorization", "publish_authorization", "merge_authorization")
        ),
        builder=tuple(
            BuilderStatus(
                attempt=int(entry["attempt"]),
                conclusion_label=str(entry["conclusion"]["label"]),
                conclusion_text=str(entry["conclusion"]["text"]),
            )
            for entry in record["builder_evidence"]
        ),
        reviewer=tuple(
            ReviewerStatus(
                attempt=int(entry["attempt"]),
                verdict=str(entry["verdict"]),
                findings=tuple(
                    FindingStatus(
                        finding_id=str(finding["id"]),
                        severity=str(finding["severity"]),
                        summary=str(finding["summary"]),
                        file=None if finding["file"] is None else str(finding["file"]),
                        line_range=None if finding["line_range"] is None else str(finding["line_range"]),
                        category=str(finding["category"]),
                    )
                    for finding in entry["findings"]
                ),
            )
            for entry in record["reviewer_evidence"]
        ),
        human_action_required=_HUMAN_ACTION_BY_STATE.get(record["state"]),
    )
