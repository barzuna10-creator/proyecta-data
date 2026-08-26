"""Immutable structured values for Jarvis V1 Phase 0."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

EvidenceLabel = Literal["FACT", "INFERENCE", "ASSUMPTION", "INTENT"]
EvidenceSourceKind = Literal[
    "repository_file",
    "git_commit",
    "test_output",
    "mission_record",
    "human_statement",
    "product_document",
    "external_document",
    "web_source",
    "metric",
]


@dataclass(frozen=True)
class EvidenceSource:
    kind: EvidenceSourceKind
    locator: str
    observed_at: str
    commit_sha: str | None = None
    excerpt_sha256: str | None = None


@dataclass(frozen=True)
class ResearchEvidence:
    evidence_id: str
    claim: str
    label: EvidenceLabel
    sources: tuple[EvidenceSource, ...] = ()
    based_on_evidence_ids: tuple[str, ...] = ()
    uncertainty_reason: str | None = None


@dataclass(frozen=True)
class RepositoryContext:
    remote_ref: str
    expected_base_sha: str


@dataclass(frozen=True)
class MissionDefinitionDraft:
    outcome: str
    scope: tuple[str, ...]
    non_goals: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]


@dataclass(frozen=True)
class MissionDraft:
    schema_version: str
    draft_id: str
    revision: int
    created_at: str
    updated_at: str
    raw_intent: str
    mission_definition: MissionDefinitionDraft
    research_evidence: tuple[ResearchEvidence, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]
    repository_context: RepositoryContext | None


@dataclass(frozen=True)
class DraftEnvelope:
    draft: MissionDraft
    digest_algorithm: Literal["sha256"]
    digest: str


@dataclass(frozen=True)
class AuthorizationIntent:
    draft_id: str
    revision: int
    digest_algorithm: Literal["sha256"]
    digest: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str = "$"


@dataclass(frozen=True)
class DraftValidationResult:
    valid: bool
    errors: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class AuthorizationCheck:
    allowed: bool
    reasons: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class DraftChanges:
    raw_intent: str | None = None
    mission_definition: MissionDefinitionDraft | None = None
    research_evidence: tuple[ResearchEvidence, ...] | None = None
    risks: tuple[str, ...] | None = None
    open_questions: tuple[str, ...] | None = None
    repository_context: RepositoryContext | None = None
    replace_repository_context: bool = False


def mission_draft_to_dict(draft: MissionDraft) -> dict[str, Any]:
    """Return a JSON-compatible copy; tuple ordering is deliberately retained."""
    def json_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: json_value(child) for key, child in value.items()}
        if isinstance(value, tuple):
            return [json_value(child) for child in value]
        return value

    return json_value(asdict(draft))


def mission_draft_from_dict(value: dict[str, Any]) -> MissionDraft:
    definition = value["mission_definition"]
    evidence = tuple(
        ResearchEvidence(
            evidence_id=item["evidence_id"],
            claim=item["claim"],
            label=item["label"],
            sources=tuple(EvidenceSource(**source) for source in item["sources"]),
            based_on_evidence_ids=tuple(item["based_on_evidence_ids"]),
            uncertainty_reason=item["uncertainty_reason"],
        )
        for item in value["research_evidence"]
    )
    repository = value["repository_context"]
    return MissionDraft(
        schema_version=value["schema_version"],
        draft_id=value["draft_id"],
        revision=value["revision"],
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        raw_intent=value["raw_intent"],
        mission_definition=MissionDefinitionDraft(
            outcome=definition["outcome"],
            scope=tuple(definition["scope"]),
            non_goals=tuple(definition["non_goals"]),
            acceptance_criteria=tuple(definition["acceptance_criteria"]),
        ),
        research_evidence=evidence,
        risks=tuple(value["risks"]),
        open_questions=tuple(value["open_questions"]),
        repository_context=RepositoryContext(**repository) if repository is not None else None,
    )


def envelope_to_dict(envelope: DraftEnvelope) -> dict[str, Any]:
    return {
        "draft": mission_draft_to_dict(envelope.draft),
        "digest_algorithm": envelope.digest_algorithm,
        "digest": envelope.digest,
    }
