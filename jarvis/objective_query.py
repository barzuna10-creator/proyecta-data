"""Jarvis God Mode M7 (Program-Level Planning Depth) -- the sole read-only
seam jarvis.mission_supervisor uses to resolve an Objective decomposition
entry's `depends_on`. Mirrors the role jarvis.mission_query.py already
plays for orchestrator.chugel: mission_supervisor.py never imports
jarvis.storage or jarvis.objectives directly (see
tests/test_jarvis_foundation_boundaries.py's
test_only_objective_query_reads_objective_storage_for_mission_supervisor),
only this module, and only ever to READ an already-persisted, already-
validated Objective -- never to write one.

Unlike jarvis.mission_query (which reaches a fixed, hard-coded Chugel
directory with no configuration of its own), jarvis.storage.FileJarvisStore
requires an explicit, caller-supplied root -- so every function here takes
an already-constructed FileJarvisStore instance rather than reaching for
one itself. jarvis.control_plane_server's own build_server() passes the
exact same store instance it already constructs for drafts/objectives
(server.store) into jarvis.mission_supervisor.MissionSupervisor at
construction time; no second store, no new root, no new I/O beyond what
this module's own two functions below need."""

from __future__ import annotations

from jarvis.models import ObjectiveDecompositionEntry
from jarvis.storage import (
    FileJarvisStore,
    ObjectiveNotFound,
    StoragePathUnsafe,
    StoredArtifactCorrupt,
)


class ObjectiveQueryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def list_objective_ids(store: FileJarvisStore) -> tuple[str, ...]:
    """Read-only, no translation needed -- jarvis.storage's own return
    shape is already exactly what a caller here needs."""
    try:
        return store.list_objective_ids()
    except (StoragePathUnsafe, OSError) as exc:
        raise ObjectiveQueryError("OBJECTIVE_STORE_UNREADABLE") from exc


def get_decomposition_entry(
    store: FileJarvisStore, objective_id: str, draft_id: str,
) -> ObjectiveDecompositionEntry:
    """Returns the one decomposition entry of `objective_id` whose own
    draft_id matches. Raises ObjectiveQueryError -- never jarvis.storage's
    own exception types, exactly like jarvis.mission_query's own
    translation of orchestrator.chugel's -- with one of:
      - "OBJECTIVE_NOT_FOUND": no such objective_id at all.
      - "OBJECTIVE_RECORD_INVALID": the objective's own storage is
        unsafe/corrupt/malformed.
      - "DECOMPOSITION_ENTRY_NOT_FOUND": the objective exists and reads
        back cleanly, but none of its decomposition entries has this
        draft_id -- e.g. a stale/foreign draft_id the caller should never
        have associated with this objective_id in the first place."""
    try:
        envelope = store.get_latest_objective(objective_id)
    except ObjectiveNotFound as exc:
        raise ObjectiveQueryError("OBJECTIVE_NOT_FOUND") from exc
    except (StoragePathUnsafe, StoredArtifactCorrupt, KeyError, TypeError, ValueError, OSError) as exc:
        raise ObjectiveQueryError("OBJECTIVE_RECORD_INVALID") from exc
    for entry in envelope.objective.decomposition:
        if entry.draft_id == draft_id:
            return entry
    raise ObjectiveQueryError("DECOMPOSITION_ENTRY_NOT_FOUND")
