"""Structural and semantic validation for Jarvis research evidence."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
from typing import Collection, Sequence

import jsonschema

from jarvis.models import ResearchEvidence, ValidationIssue

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "research_evidence.schema.json"
with _SCHEMA_PATH.open(encoding="utf-8") as _handle:
    _SCHEMA = json.load(_handle)
_VALIDATOR = jsonschema.Draft202012Validator(
    _SCHEMA, format_checker=jsonschema.FormatChecker()
)
_UTC_SECOND_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


def _is_valid_utc_second_timestamp(value: object) -> bool:
    """Require canonical UTC shape and an actually valid calendar instant."""
    if not isinstance(value, str) or _UTC_SECOND_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _as_dict(evidence: ResearchEvidence) -> dict:
    return {
        "evidence_id": evidence.evidence_id,
        "claim": evidence.claim,
        "label": evidence.label,
        "sources": [
            {
                "kind": source.kind,
                "locator": source.locator,
                "observed_at": source.observed_at,
                "commit_sha": source.commit_sha,
                "excerpt_sha256": source.excerpt_sha256,
            }
            for source in evidence.sources
        ],
        "based_on_evidence_ids": list(evidence.based_on_evidence_ids),
        "uncertainty_reason": evidence.uncertainty_reason,
    }


def validate_research_evidence(
    evidence: ResearchEvidence,
    *,
    known_evidence_ids: Collection[str] = (),
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for error in sorted(_VALIDATOR.iter_errors(_as_dict(evidence)), key=lambda item: list(item.path)):
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        issues.append(ValidationIssue("EVIDENCE_SCHEMA_INVALID", error.message, path))

    ids = set(known_evidence_ids)
    if evidence.evidence_id in evidence.based_on_evidence_ids:
        issues.append(ValidationIssue(
            "EVIDENCE_SELF_REFERENCE", "evidence may not cite itself", "$.based_on_evidence_ids"
        ))
    for dependency in evidence.based_on_evidence_ids:
        if dependency not in ids:
            issues.append(ValidationIssue(
                "EVIDENCE_REFERENCE_MISSING",
                f"referenced evidence {dependency!r} is not present",
                "$.based_on_evidence_ids",
            ))

    if evidence.label == "FACT":
        if not evidence.sources:
            issues.append(ValidationIssue("FACT_SOURCE_REQUIRED", "FACT requires a source", "$.sources"))
        if evidence.uncertainty_reason is not None:
            issues.append(ValidationIssue(
                "FACT_UNCERTAINTY_FORBIDDEN",
                "FACT uncertainty_reason must be null",
                "$.uncertainty_reason",
            ))
    elif evidence.label == "INFERENCE":
        if not evidence.based_on_evidence_ids:
            issues.append(ValidationIssue(
                "INFERENCE_BASIS_REQUIRED",
                "INFERENCE requires based_on_evidence_ids",
                "$.based_on_evidence_ids",
            ))
        if not evidence.uncertainty_reason:
            issues.append(ValidationIssue(
                "INFERENCE_UNCERTAINTY_REQUIRED",
                "INFERENCE must explain its inferential gap",
                "$.uncertainty_reason",
            ))
    elif evidence.label == "ASSUMPTION":
        if not evidence.uncertainty_reason:
            issues.append(ValidationIssue(
                "ASSUMPTION_UNCERTAINTY_REQUIRED",
                "ASSUMPTION must state what remains unverified and how to resolve it",
                "$.uncertainty_reason",
            ))
    elif evidence.label == "INTENT":
        if not any(source.kind == "human_statement" for source in evidence.sources):
            issues.append(ValidationIssue(
                "INTENT_HUMAN_SOURCE_REQUIRED",
                "INTENT requires a human_statement source",
                "$.sources",
            ))

    for index, source in enumerate(evidence.sources):
        if not _is_valid_utc_second_timestamp(source.observed_at):
            issues.append(ValidationIssue(
                "OBSERVED_AT_INVALID",
                "observed_at must be a valid UTC calendar timestamp at whole-second precision",
                f"$.sources[{index}].observed_at",
            ))
        if source.kind in {"repository_file", "git_commit"} and source.commit_sha is None:
            issues.append(ValidationIssue(
                "REPOSITORY_SOURCE_COMMIT_REQUIRED",
                "repository evidence must be bound to an exact commit",
                f"$.sources[{index}].commit_sha",
            ))
    return tuple(issues)


def validate_evidence_set(
    evidence: Sequence[ResearchEvidence],
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    identifiers = [item.evidence_id for item in evidence]
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    for duplicate in duplicates:
        issues.append(ValidationIssue(
            "EVIDENCE_ID_DUPLICATE", f"duplicate evidence_id {duplicate!r}", "$.research_evidence"
        ))

    known = set(identifiers)
    for index, item in enumerate(evidence):
        for issue in validate_research_evidence(item, known_evidence_ids=known):
            issues.append(ValidationIssue(issue.code, issue.message, f"$.research_evidence[{index}]{issue.path[1:]}"))

    graph = {item.evidence_id: tuple(item.based_on_evidence_ids) for item in evidence}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        cycle = any(dependency in graph and visit(dependency) for dependency in graph.get(node, ()))
        visiting.remove(node)
        visited.add(node)
        return cycle

    if any(visit(node) for node in graph if node not in visited):
        issues.append(ValidationIssue(
            "EVIDENCE_DEPENDENCY_CYCLE",
            "based_on_evidence_ids must form an acyclic graph",
            "$.research_evidence",
        ))
    return tuple(issues)
