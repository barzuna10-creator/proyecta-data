"""MissionDraft validation, immutable revision, canonicalization, and digest."""

from __future__ import annotations

import copy
import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

import jsonschema

from jarvis.evidence import validate_evidence_set
from jarvis.models import (
    DraftChanges,
    DraftEnvelope,
    DraftValidationResult,
    MissionDraft,
    ValidationIssue,
    mission_draft_from_dict,
    mission_draft_to_dict,
)

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
with (_SCHEMAS_DIR / "mission_draft.schema.json").open(encoding="utf-8") as _handle:
    _SCHEMA = json.load(_handle)
with (_SCHEMAS_DIR / "research_evidence.schema.json").open(encoding="utf-8") as _handle:
    _EVIDENCE_SCHEMA = json.load(_handle)
# Keep the standalone evidence schema canonical while avoiding resolver/network behavior.
_RESOLVED_SCHEMA = copy.deepcopy(_SCHEMA)
_RESOLVED_SCHEMA["properties"]["research_evidence"]["items"] = _EVIDENCE_SCHEMA
_VALIDATOR = jsonschema.Draft202012Validator(
    _RESOLVED_SCHEMA, format_checker=jsonschema.FormatChecker()
)
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class DraftInvalid(ValueError):
    def __init__(self, errors: tuple[ValidationIssue, ...]):
        super().__init__("MissionDraft is invalid: " + "; ".join(e.code for e in errors))
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


def validate_mission_draft(value: object) -> DraftValidationResult:
    raw = mission_draft_to_dict(value) if isinstance(value, MissionDraft) else value
    errors: list[ValidationIssue] = []
    for error in sorted(_VALIDATOR.iter_errors(raw), key=lambda item: list(item.path)):
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        errors.append(ValidationIssue("DRAFT_SCHEMA_INVALID", error.message, path))
    if not isinstance(raw, dict):
        return DraftValidationResult(False, tuple(errors))

    for path in _non_nfc_paths(raw):
        errors.append(ValidationIssue("STRING_NOT_NFC", "all strings must already be Unicode NFC", path))
    for path in _unsupported_number_paths(raw):
        errors.append(ValidationIssue("FLOAT_FORBIDDEN", "floats are forbidden in MissionDraft", path))

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

    # Semantic evidence rules apply equally to untrusted decoded JSON and to
    # typed values. Structural success is required before constructing types.
    if not any(error.code == "DRAFT_SCHEMA_INVALID" for error in errors):
        try:
            typed = value if isinstance(value, MissionDraft) else mission_draft_from_dict(raw)
            errors.extend(validate_evidence_set(typed.research_evidence))
        except (KeyError, TypeError, ValueError):
            errors.append(ValidationIssue(
                "DRAFT_MODEL_INVALID", "draft could not be converted to immutable models"
            ))
    return DraftValidationResult(not errors, tuple(errors))


def canonicalize_mission_draft(draft: MissionDraft) -> bytes:
    result = validate_mission_draft(draft)
    if not result.valid:
        raise DraftInvalid(result.errors)
    rendered = json.dumps(
        mission_draft_to_dict(draft),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8")


def digest_mission_draft(draft: MissionDraft) -> str:
    return hashlib.sha256(canonicalize_mission_draft(draft)).hexdigest()


def build_draft_envelope(draft: MissionDraft) -> DraftEnvelope:
    return DraftEnvelope(draft=draft, digest_algorithm="sha256", digest=digest_mission_draft(draft))


def revise_mission_draft(
    previous: MissionDraft,
    *,
    updated_at: str,
    changes: DraftChanges,
) -> MissionDraft:
    repository = previous.repository_context
    if changes.replace_repository_context:
        repository = changes.repository_context
    revised = dataclasses.replace(
        previous,
        revision=previous.revision + 1,
        updated_at=updated_at,
        raw_intent=changes.raw_intent if changes.raw_intent is not None else previous.raw_intent,
        mission_definition=(
            changes.mission_definition
            if changes.mission_definition is not None
            else previous.mission_definition
        ),
        research_evidence=(
            changes.research_evidence
            if changes.research_evidence is not None
            else previous.research_evidence
        ),
        risks=changes.risks if changes.risks is not None else previous.risks,
        open_questions=(
            changes.open_questions if changes.open_questions is not None else previous.open_questions
        ),
        repository_context=repository,
    )
    result = validate_mission_draft(revised)
    if not result.valid:
        raise DraftInvalid(result.errors)
    previous_result = validate_mission_draft(previous)
    if not previous_result.valid:
        raise DraftInvalid(previous_result.errors)
    if updated_at <= previous.updated_at:
        raise DraftInvalid((ValidationIssue(
            "UPDATED_AT_NOT_ADVANCED", "updated_at must advance for a new revision", "$.updated_at"
        ),))
    return revised
