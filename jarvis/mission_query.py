"""The sole read-only Jarvis-to-Chugel production boundary."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator import chugel

from jarvis.status import (
    MissionBucket,
    MissionStatus,
    UnknownMissionState,
    classify_mission_state,
    project_mission_status,
)
from jarvis.learning_projection import MissionLearningProjection, project_mission_learning


class MissionQueryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MissionListing:
    mission_id: str
    readable: bool
    state: str | None
    bucket: MissionBucket | None
    updated_at: str | None
    error_code: str | None


@dataclass(frozen=True)
class MissionAuthorizationBinding:
    definition: tuple[tuple[str, object], ...]
    repository_base_sha: str
    repository_branch: str


def get_mission_authorization_binding(mission_id: str) -> MissionAuthorizationBinding:
    """Detached allow-listed projection for authorization-effect retries."""
    try:
        record = chugel.get_mission(mission_id)
        version = record["mission_definition_history"][0]
        keys = (
            "version", "outcome", "scope", "non_goals", "acceptance_criteria",
            "source", "based_on_proposal_id", "authorized_by", "authorized_at",
            "authorization_decision_ref",
        )
        detached = []
        for key in keys:
            value = version[key]
            if isinstance(value, list):
                value = tuple(value)
            elif not isinstance(value, (str, int, bool, type(None))):
                raise TypeError(key)
            detached.append((key, value))
        repository = record["repository"]
        return MissionAuthorizationBinding(
            definition=tuple(detached),
            repository_base_sha=repository["base_sha"],
            repository_branch=repository["branch"],
        )
    except chugel.MissionNotFound as exc:
        raise MissionQueryError("MISSION_NOT_FOUND") from exc
    except (chugel.MissionRecordCorrupt, chugel.MissionRecordInvalid) as exc:
        raise MissionQueryError("MISSION_RECORD_INVALID") from exc
    except chugel.MissionRecordPathUnsafe as exc:
        raise MissionQueryError("MISSION_PATH_UNSAFE") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MissionQueryError("MISSION_AUTHORIZATION_PROJECTION_INVALID") from exc


def list_missions() -> tuple[MissionListing, ...]:
    return tuple(MissionListing(
        mission_id=item["mission_id"],
        readable=item["readable"],
        state=item["state"],
        bucket=classify_mission_state(item["state"]) if item["state"] is not None else None,
        updated_at=item["updated_at"],
        error_code=item["error_code"],
    ) for item in chugel.list_missions())


def get_mission_status(mission_id: str) -> MissionStatus:
    try:
        return project_mission_status(chugel.get_mission(mission_id))
    except chugel.MissionNotFound as exc:
        raise MissionQueryError("MISSION_NOT_FOUND") from exc
    except chugel.MissionRecordCorrupt as exc:
        raise MissionQueryError("MISSION_RECORD_CORRUPT") from exc
    except chugel.MissionRecordInvalid as exc:
        raise MissionQueryError("MISSION_RECORD_INVALID") from exc
    except chugel.MissionRecordPathUnsafe as exc:
        raise MissionQueryError("MISSION_PATH_UNSAFE") from exc
    except UnknownMissionState as exc:
        raise MissionQueryError("MISSION_STATE_UNKNOWN") from exc
    except ValueError as exc:
        raise MissionQueryError("INVALID_MISSION_ID") from exc


def get_mission_learning(mission_id: str) -> MissionLearningProjection:
    """Extend the existing read boundary with one detached learning projection."""
    try:
        return project_mission_learning(chugel.get_mission(mission_id))
    except chugel.MissionNotFound as exc:
        raise MissionQueryError("MISSION_NOT_FOUND") from exc
    except (chugel.MissionRecordCorrupt, chugel.MissionRecordInvalid) as exc:
        raise MissionQueryError("MISSION_RECORD_INVALID") from exc
    except chugel.MissionRecordPathUnsafe as exc:
        raise MissionQueryError("MISSION_PATH_UNSAFE") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionQueryError("MISSION_LEARNING_PROJECTION_INVALID") from exc
