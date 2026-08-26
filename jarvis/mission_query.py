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
