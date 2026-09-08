"""Jarvis God Mode M7 (Program-Level Planning Depth) -- pure, stateless
resolution of an Objective decomposition entry's `depends_on` against
Chugel's own current mission states.

No durable state of any kind lives here, and none is ever created:
every function in this module recomputes its answer from scratch, every
call, from whatever (depends_on_draft_ids, draft_id_to_mission_state,
depends_on_by_draft_id) the caller supplies this time -- exactly the same
"recompute, never persist a pending marker" discipline already used
throughout M2D/M3/M4/M5 (see jarvis/mission_supervisor.py's own module
docstring). A crash here loses nothing: the next call (a fresh
_drain_pass() cycle, or a fresh projection read) re-derives the same
answer from Chugel's and jarvis.storage's own already-durable state.

Dependency status, for one draft_id, given the mission (if any) it was
authorized into:
  - "satisfied": the mission exists and its state is MERGED or later in
    the lifecycle (MERGED, DEPLOY_PENDING, VERIFYING_PRODUCTION,
    COMPLETED) -- the code is durably in main.
  - "unmet": the mission does not exist yet, or exists in a state earlier
    than MERGED.
  - "failed": the mission's own state is a terminal failure
    (FAILED/CANCELLED/ROLLED_BACK).

`failed_dependencies()` classifies TRANSITIVELY: a dependency is "failed"
if its own state is a terminal failure, OR if any of ITS OWN dependencies
(walking depends_on_by_draft_id all the way down) is itself transitively
failed -- never only the immediate prerequisite. This is intentionally
defended in two independent layers (see jarvis/objectives.py's own
validate_objective() docstring on the M7 cycle check it enforces at
persistence time): the graph this module is ever handed in production is
already guaranteed acyclic by that persistence-time check, but every
traversal in this module carries its OWN visited-node guard regardless,
so a hypothetical cyclic graph (a bug elsewhere, or data persisted before
this invariant existed) still terminates deterministically here instead
of recursing forever -- never validating or legitimizing the cycle, only
refusing to crash on it."""

from __future__ import annotations

from typing import Literal, Mapping

DependencyStatus = Literal["satisfied", "unmet", "failed"]

# MERGED or later in the lifecycle -- the code is durably in main. Mirrors
# jarvis/mission_supervisor.py's own AUTO_ADVANCE_ELIGIBLE_STATES/
# TERMINAL_STATES vocabulary; duplicated here (not imported) so this pure
# module never depends on mission_supervisor.py, only the other way
# around.
_SATISFIED_STATES = frozenset({"MERGED", "DEPLOY_PENDING", "VERIFYING_PRODUCTION", "COMPLETED"})
_FAILED_STATES = frozenset({"FAILED", "CANCELLED", "ROLLED_BACK"})


def _single_status(draft_id: str, draft_id_to_mission_state: Mapping[str, str | None]) -> DependencyStatus:
    state = draft_id_to_mission_state.get(draft_id)
    if state in _SATISFIED_STATES:
        return "satisfied"
    if state in _FAILED_STATES:
        return "failed"
    return "unmet"  # missing entirely, or any pre-MERGED state


def unmet_dependencies(
    depends_on_draft_ids: tuple[str, ...],
    draft_id_to_mission_state: Mapping[str, str | None],
) -> tuple[str, ...]:
    """Immediate (non-transitive) check only -- the subset of
    depends_on_draft_ids whose own mission is missing or pre-MERGED, never
    including one that is already terminally failed (that is
    failed_dependencies()'s classification, not this one's)."""
    return tuple(
        draft_id for draft_id in depends_on_draft_ids
        if _single_status(draft_id, draft_id_to_mission_state) == "unmet"
    )


def failed_dependencies(
    depends_on_draft_ids: tuple[str, ...],
    draft_id_to_mission_state: Mapping[str, str | None],
    depends_on_by_draft_id: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """The subset of depends_on_draft_ids that are transitively failed:
    either that draft_id's own mission reached a terminal failure state,
    or any dependency of ITS OWN (walking depends_on_by_draft_id, however
    deep) is itself transitively failed. `depends_on_by_draft_id` maps
    every draft_id known in this evaluation to its own depends_on tuple
    (typically every entry of one Objective's decomposition); a draft_id
    absent from it is treated as having no further dependencies of its
    own, never as an error.

    Cycle-safe by an independent, in-function guard (an in-progress
    "visiting" set, standard DFS cycle-detection discipline) -- this does
    NOT rely on the persistence-time acyclicity guarantee
    jarvis/objectives.py's validate_objective() already enforces; even a
    hypothetical cyclic `depends_on_by_draft_id` terminates
    deterministically here, treating a node revisited while still being
    explored as not (yet) proven failed, never re-entering it."""
    memo: dict[str, bool] = {}
    visiting: set[str] = set()

    def is_failed(draft_id: str) -> bool:
        if draft_id in memo:
            return memo[draft_id]
        if draft_id in visiting:
            # Cycle guard: a node re-encountered while still being
            # explored is never reprocessed -- treated as not (yet) proven
            # failed via this path, so the traversal always terminates.
            return False
        visiting.add(draft_id)
        status = _single_status(draft_id, draft_id_to_mission_state)
        if status == "failed":
            result = True
        elif status == "satisfied":
            result = False
        else:
            result = any(is_failed(dep) for dep in depends_on_by_draft_id.get(draft_id, ()))
        visiting.discard(draft_id)
        memo[draft_id] = result
        return result

    return tuple(draft_id for draft_id in depends_on_draft_ids if is_failed(draft_id))


def dependency_status(
    depends_on_draft_ids: tuple[str, ...],
    draft_id_to_mission_state: Mapping[str, str | None],
    depends_on_by_draft_id: Mapping[str, tuple[str, ...]],
) -> DependencyStatus:
    """The single overall status for one decomposition entry's own
    depends_on tuple -- "failed" takes precedence over "unmet" (a
    transitively failed prerequisite is never merely "still waiting"),
    "satisfied" only when every dependency is itself satisfied and none
    is failed. An entry with no dependencies at all is always
    "satisfied" (vacuously -- nothing is blocking it)."""
    if not depends_on_draft_ids:
        return "satisfied"
    if failed_dependencies(depends_on_draft_ids, draft_id_to_mission_state, depends_on_by_draft_id):
        return "failed"
    if unmet_dependencies(depends_on_draft_ids, draft_id_to_mission_state):
        return "unmet"
    return "satisfied"


def failed_root_cause_for(
    draft_id: str,
    draft_id_to_mission_state: Mapping[str, str | None],
    depends_on_by_draft_id: Mapping[str, tuple[str, ...]],
) -> str | None:
    """Given a decomposition entry's OWN draft_id (not one of its
    dependencies), walks its depends_on chain, however deep, to find the
    specific ancestor draft_id whose own mission state is itself a
    terminal failure -- the root cause a human needs to see, rather than
    just the immediate (possibly still-"unmet", never-itself-failed)
    prerequisite. Returns None if draft_id is not transitively failed at
    all. Deterministic traversal order (depends_on_by_draft_id's own tuple
    order, depth-first) and cycle-safe via the same in-progress "visiting"
    guard failed_dependencies() uses -- independent of, never assuming,
    the persistence-time acyclicity guarantee."""
    visiting: set[str] = set()

    def search(node: str) -> str | None:
        if node in visiting:
            return None
        visiting.add(node)
        try:
            if _single_status(node, draft_id_to_mission_state) == "failed":
                return node
            for dep in depends_on_by_draft_id.get(node, ()):
                found = search(dep)
                if found is not None:
                    return found
            return None
        finally:
            visiting.discard(node)

    for dep in depends_on_by_draft_id.get(draft_id, ()):
        found = search(dep)
        if found is not None:
            return found
    return None
