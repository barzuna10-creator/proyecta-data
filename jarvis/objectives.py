"""Objective validation, immutable revision, canonicalization, and digest.

Jarvis God Mode M1. Deliberate mirror of jarvis/drafts.py's own structure
and rigor -- same schema-driven validation, same canonical-JSON digest,
same strictly-advancing-revision discipline. Like drafts.py, this module
never imports orchestrator.chugel: an Objective carries no execution
state and no authority of its own (see jarvis/models.py's own Objective
docstring) -- the only thing that ever links it to real work is
`decomposition`, a tuple of already-created MissionDraft ids, populated
by the caller (jarvis/control_plane_server.py), never derived here."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

import jsonschema

from jarvis.models import (
    Objective,
    ObjectiveChanges,
    ObjectiveEnvelope,
    ValidationIssue,
    objective_envelope_to_dict,
    objective_from_dict,
    objective_to_dict,
)

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
with (_SCHEMAS_DIR / "objective.schema.json").open(encoding="utf-8") as _handle:
    _SCHEMA = json.load(_handle)
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA, format_checker=jsonschema.FormatChecker())
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ObjectiveInvalid(ValueError):
    def __init__(self, errors: tuple[ValidationIssue, ...]):
        super().__init__("Objective is invalid: " + "; ".join(e.code for e in errors))
        self.errors = errors


def _non_nfc_paths(value: Any, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, str) and unicodedata.normalize("NFC", value) != value:
        paths.append(path)
    elif isinstance(value, dict):
        for key, child in value.items():
            paths.extend(_non_nfc_paths(key, f"{path}.<key>"))
            paths.extend(_non_nfc_paths(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            paths.extend(_non_nfc_paths(child, f"{path}[{index}]"))
    return paths


def _unsupported_number_paths(value: Any, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, float):
        paths.append(path)
    elif isinstance(value, dict):
        for key, child in value.items():
            paths.extend(_unsupported_number_paths(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            paths.extend(_unsupported_number_paths(child, f"{path}[{index}]"))
    return paths


def validate_objective(value: object) -> "ObjectiveValidationResult":
    raw = objective_to_dict(value) if isinstance(value, Objective) else value
    errors: list[ValidationIssue] = []
    for error in sorted(_VALIDATOR.iter_errors(raw), key=lambda item: list(item.path)):
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        errors.append(ValidationIssue("OBJECTIVE_SCHEMA_INVALID", error.message, path))
    if not isinstance(raw, dict):
        return ObjectiveValidationResult(False, tuple(errors))

    for path in _non_nfc_paths(raw):
        errors.append(ValidationIssue("STRING_NOT_NFC", "all strings must already be Unicode NFC", path))
    for path in _unsupported_number_paths(raw):
        errors.append(ValidationIssue("FLOAT_FORBIDDEN", "floats are forbidden in Objective", path))

    revision = raw.get("revision")
    if isinstance(revision, bool):
        errors.append(ValidationIssue("REVISION_BOOL_FORBIDDEN", "revision must not be boolean", "$.revision"))
    for field in ("created_at", "updated_at"):
        timestamp = raw.get(field)
        if isinstance(timestamp, str) and _UTC_TIMESTAMP.fullmatch(timestamp) is None:
            errors.append(ValidationIssue(
                "TIMESTAMP_NOT_CANONICAL_UTC", "timestamp must be YYYY-MM-DDTHH:MM:SSZ", f"$.{field}"
            ))
    created = raw.get("created_at")
    updated = raw.get("updated_at")
    if isinstance(created, str) and isinstance(updated, str) and updated < created:
        errors.append(ValidationIssue(
            "UPDATED_BEFORE_CREATED", "updated_at must not precede created_at", "$.updated_at"
        ))

    # Same invariant the schema's own allOf/if/then already enforces
    # structurally (decomposed => 2-4 items, proposed => 0) -- re-checked
    # here as a semantic, not just structural, guarantee, exactly like
    # drafts.py re-checks research_evidence semantics beyond its own
    # schema. A duplicate draft_id across decomposition entries is a
    # schema-shape violation the JSON Schema `uniqueItems` keyword alone
    # would not catch if title/rationale differed -- caught explicitly
    # here instead.
    decomposition = raw.get("decomposition")
    if isinstance(decomposition, list):
        draft_ids = [item.get("draft_id") for item in decomposition if isinstance(item, dict)]
        if len(draft_ids) != len(set(draft_ids)):
            errors.append(ValidationIssue(
                "DUPLICATE_DECOMPOSITION_DRAFT_ID",
                "decomposition entries must not repeat the same draft_id",
                "$.decomposition",
            ))
        # M7 (Program-Level Planning Depth) -- depends_on shape/acyclicity,
        # enforced here (not in jarvis/control_plane_server.py's own
        # 2-4-item count check) precisely because this is the ONE place
        # every path that ever persists an Objective's decomposition
        # already goes through -- both _handle_objective_decomposition()
        # (via build_objective_envelope() -> canonicalize_objective() ->
        # this function) and revise_objective() (which calls this function
        # directly). A cycle, a self-reference, or a dangling reference is
        # rejected here with the exact same severity as the schema's own
        # 2-4 count -- never a warning, never silently dropped.
        known_draft_ids = set(draft_ids)
        edges: dict[str, list[str]] = {}
        for item in decomposition:
            if not isinstance(item, dict):
                continue
            draft_id = item.get("draft_id")
            depends_on = item.get("depends_on")
            if not isinstance(depends_on, list):
                continue
            edges[draft_id] = [dep for dep in depends_on if isinstance(dep, str)]
            for dep in depends_on:
                if not isinstance(dep, str):
                    continue
                if dep == draft_id:
                    errors.append(ValidationIssue(
                        "DECOMPOSITION_DEPENDENCY_SELF_REFERENCE",
                        "a decomposition entry must not depend on itself",
                        "$.decomposition",
                    ))
                elif dep not in known_draft_ids:
                    errors.append(ValidationIssue(
                        "DECOMPOSITION_DEPENDENCY_DANGLING",
                        "a decomposition entry must only depend on another entry of the same decomposition",
                        "$.decomposition",
                    ))
        # Real cycle detection (Kahn's algorithm / topological sort), not a
        # heuristic -- run only over edges whose endpoints are all known,
        # real draft_ids (self-references/dangling references above are
        # already reported separately and never participate here, so one
        # malformed edge cannot mask a genuine cycle among the rest).
        if not any(e.code in ("DECOMPOSITION_DEPENDENCY_SELF_REFERENCE", "DECOMPOSITION_DEPENDENCY_DANGLING") for e in errors):
            in_degree = {draft_id: 0 for draft_id in known_draft_ids}
            for draft_id, deps in edges.items():
                for _dep in deps:
                    in_degree[draft_id] = in_degree.get(draft_id, 0) + 1
            queue = [draft_id for draft_id, degree in in_degree.items() if degree == 0]
            visited_count = 0
            # Reverse adjacency: dep -> [draft_ids that depend on dep]
            dependents: dict[str, list[str]] = {draft_id: [] for draft_id in known_draft_ids}
            for draft_id, deps in edges.items():
                for dep in deps:
                    dependents.setdefault(dep, []).append(draft_id)
            while queue:
                node = queue.pop()
                visited_count += 1
                for dependent in dependents.get(node, []):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)
            if visited_count != len(known_draft_ids):
                errors.append(ValidationIssue(
                    "DECOMPOSITION_DEPENDENCY_CYCLE",
                    "decomposition dependencies must not form a cycle",
                    "$.decomposition",
                ))

    if not any(error.code == "OBJECTIVE_SCHEMA_INVALID" for error in errors):
        try:
            value if isinstance(value, Objective) else objective_from_dict(raw)
        except (KeyError, TypeError, ValueError):
            errors.append(ValidationIssue(
                "OBJECTIVE_MODEL_INVALID", "objective could not be converted to immutable models"
            ))
    return ObjectiveValidationResult(not errors, tuple(errors))


@dataclasses.dataclass(frozen=True)
class ObjectiveValidationResult:
    valid: bool
    errors: tuple[ValidationIssue, ...]


def canonicalize_objective(objective: Objective) -> bytes:
    result = validate_objective(objective)
    if not result.valid:
        raise ObjectiveInvalid(result.errors)
    rendered = json.dumps(
        objective_to_dict(objective),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8")


def digest_objective(objective: Objective) -> str:
    return hashlib.sha256(canonicalize_objective(objective)).hexdigest()


def build_objective_envelope(objective: Objective) -> ObjectiveEnvelope:
    return ObjectiveEnvelope(
        objective=objective, digest_algorithm="sha256", digest=digest_objective(objective),
    )


def revise_objective(
    previous: Objective,
    *,
    updated_at: str,
    changes: ObjectiveChanges,
) -> Objective:
    revised = dataclasses.replace(
        previous,
        revision=previous.revision + 1,
        updated_at=updated_at,
        status=changes.status if changes.status is not None else previous.status,
        decomposition=(
            changes.decomposition if changes.decomposition is not None else previous.decomposition
        ),
    )
    result = validate_objective(revised)
    if not result.valid:
        raise ObjectiveInvalid(result.errors)
    previous_result = validate_objective(previous)
    if not previous_result.valid:
        raise ObjectiveInvalid(previous_result.errors)
    if updated_at <= previous.updated_at:
        raise ObjectiveInvalid((ValidationIssue(
            "UPDATED_AT_NOT_ADVANCED", "updated_at must advance for a new revision", "$.updated_at"
        ),))
    return revised
