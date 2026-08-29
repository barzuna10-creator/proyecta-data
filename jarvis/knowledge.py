"""Immutable, non-authoritative trusted-knowledge contracts for Jarvis V1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

import jsonschema

from jarvis.evidence import validate_evidence_set
from jarvis.models import EvidenceSource, ResearchEvidence, ValidationIssue

KnowledgeLabel = Literal["FACT", "INTENT"]
CandidateLabel = Literal["FACT", "INFERENCE", "ASSUMPTION", "INTENT"]
CandidateStatus = Literal[
    "draft", "awaiting_emma_review", "changes_required",
    "awaiting_human_authorization", "accepted", "rejected", "withdrawn",
]
KnowledgeEntryStatus = Literal["active", "stale", "conflicted", "superseded", "retired"]
EmmaVerdict = Literal["PASS", "CHANGES_REQUIRED", "BLOCKED"]
# Authority tier -- deliberately distinct from KnowledgeLabel (a truth
# label) and KnowledgeEntryStatus (a lifecycle state). None means
# "unknown/not classified" -- the only backward-compatible reading for
# content persisted before this field existed. None must never be
# silently treated as "canonical": jarvis.knowledge_retrieval ranks it
# below both canonical and complementary, and _every_ new candidate this
# codebase constructs going forward must set it explicitly (see
# require_explicit_tier() below) -- None is a legacy-read affordance
# only, never a value new code is allowed to choose deliberately.
EvidenceTier = Literal["canonical", "complementary"]

_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_REF = re.compile(
    r"\Arefs/(?:heads|remotes/origin)/(?:[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?)"
    r"(?:/(?:[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?))*\Z"
)
_UTC = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    repository_ref: str
    expected_commit_sha: str


@dataclass(frozen=True, slots=True)
class KnowledgeApplicability:
    product_areas: tuple[str, ...]
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeCandidateContent:
    schema_version: Literal["1.0"]
    candidate_id: str
    revision: int
    created_at: str
    target_knowledge_id: str | None
    expected_target_revision: int | None
    expected_current_status: KnowledgeEntryStatus | None
    proposed_entry_status: KnowledgeEntryStatus
    claim: str
    label: CandidateLabel
    applicability: KnowledgeApplicability
    repository_binding: RepositoryBinding | None
    research_evidence: tuple[ResearchEvidence, ...]
    based_on: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    uncertainty_reason: str | None = None
    # Backward-compatible: absent in content persisted before this field
    # existed (see candidate_content_from_dict()'s .get()). Never defaults
    # to "canonical" or "complementary" -- absence reads as None, exactly
    # what it is: unclassified, never an implicit upgrade in authority.
    tier: EvidenceTier | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeCandidateEnvelope:
    content: KnowledgeCandidateContent
    digest_algorithm: Literal["sha256"]
    content_digest: str


@dataclass(frozen=True, slots=True)
class EmmaKnowledgeReview:
    candidate_id: str
    revision: int
    content_digest: str
    verdict: EmmaVerdict
    reviewed_at: str
    findings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeAuthorizationIntent:
    candidate_id: str
    revision: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    schema_version: Literal["1.0"]
    knowledge_id: str
    revision: int
    created_at: str
    status: KnowledgeEntryStatus
    label: KnowledgeLabel
    claim: str
    applicability: KnowledgeApplicability
    repository_binding: RepositoryBinding | None
    research_evidence: tuple[ResearchEvidence, ...]
    based_on: tuple[str, ...]
    contradicts: tuple[str, ...]
    supersedes: tuple[str, ...]
    candidate_id: str
    candidate_revision: int
    candidate_content_digest: str
    # Copied verbatim from the promoting candidate's own tier at
    # promotion time (see knowledge_storage.promote()) -- never inferred,
    # never defaulted here. Backward-compatible read: absent in entries
    # persisted before this field existed (see knowledge_entry_from_dict()).
    tier: EvidenceTier | None = None


def _timestamp_valid(value: object) -> bool:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def validate_repository_binding(binding: RepositoryBinding) -> tuple[ValidationIssue, ...]:
    ref = binding.repository_ref
    issues: list[ValidationIssue] = []
    invalid = (
        not isinstance(ref, str) or not 1 <= len(ref) <= 200
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in ref)
        or _REF.fullmatch(ref) is None
        or any(token in ref for token in ("..", "//", "@{", "\\", "~", "^", ":", "?", "*", "["))
    )
    components = ref.split("/") if isinstance(ref, str) else []
    if not invalid:
        invalid = any(
            not 1 <= len(component) <= 64 or component in {".", ".."}
            or component.startswith(("-", ".")) or component.endswith((".", ".lock"))
            for component in components[2:]
        )
    if invalid:
        issues.append(ValidationIssue("REPOSITORY_REF_INVALID", "repository_ref is outside the approved Git-ref subset", "$.repository_binding.repository_ref"))
    if not isinstance(binding.expected_commit_sha, str) or _COMMIT.fullmatch(binding.expected_commit_sha) is None:
        issues.append(ValidationIssue("EXPECTED_COMMIT_SHA_INVALID", "expected_commit_sha must be lowercase 40-hex", "$.repository_binding.expected_commit_sha"))
    return tuple(issues)


def _json_value(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(child) for child in value]
    return value


def candidate_content_to_dict(content: KnowledgeCandidateContent) -> dict[str, Any]:
    return _json_value(content)


def candidate_content_from_dict(value: dict[str, Any]) -> KnowledgeCandidateContent:
    applicability = value["applicability"]
    binding = value["repository_binding"]
    return KnowledgeCandidateContent(
        schema_version=value["schema_version"], candidate_id=value["candidate_id"], revision=value["revision"],
        created_at=value["created_at"], target_knowledge_id=value["target_knowledge_id"],
        expected_target_revision=value["expected_target_revision"], expected_current_status=value["expected_current_status"],
        proposed_entry_status=value["proposed_entry_status"], claim=value["claim"], label=value["label"],
        applicability=KnowledgeApplicability(tuple(applicability["product_areas"]), tuple(applicability["limitations"])),
        repository_binding=RepositoryBinding(**binding) if binding else None,
        research_evidence=tuple(research_evidence_from_dict(item) for item in value["research_evidence"]),
        based_on=tuple(value["based_on"]), contradicts=tuple(value["contradicts"]), supersedes=tuple(value["supersedes"]),
        uncertainty_reason=value["uncertainty_reason"],
        # .get(), not [...]: content persisted before this field existed
        # has no "tier" key at all -- that must keep loading, as None
        # (unclassified), never raise and never silently default to a
        # specific tier.
        tier=value.get("tier"),
    )


def canonical_candidate_bytes(content: KnowledgeCandidateContent) -> bytes:
    return json.dumps(candidate_content_to_dict(content), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_candidate_content(content: KnowledgeCandidateContent) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    schema_path = Path(__file__).resolve().parent / "schemas" / "knowledge_candidate.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for error in sorted(jsonschema.Draft202012Validator(schema).iter_errors(candidate_content_to_dict(content)), key=lambda item: list(item.path)):
        issues.append(ValidationIssue("KNOWLEDGE_CANDIDATE_SCHEMA_INVALID", error.message))
    if not _timestamp_valid(content.created_at):
        issues.append(ValidationIssue("KNOWLEDGE_CREATED_AT_INVALID", "created_at must be a valid UTC whole-second timestamp"))
    creating = content.target_knowledge_id is None
    if creating:
        if content.expected_target_revision is not None or content.expected_current_status is not None or content.proposed_entry_status != "active":
            issues.append(ValidationIssue("KNOWLEDGE_TARGET_BINDING_INVALID", "new entries require null expected target fields and active status"))
    else:
        if _UUID.fullmatch(content.target_knowledge_id or "") is None or not isinstance(content.expected_target_revision, int) or isinstance(content.expected_target_revision, bool) or content.expected_target_revision < 1 or content.expected_current_status is None:
            issues.append(ValidationIssue("KNOWLEDGE_TARGET_BINDING_INVALID", "transitions require exact target ID, revision, and status"))
    if content.repository_binding is not None:
        issues.extend(validate_repository_binding(content.repository_binding))
    issues.extend(validate_evidence_set(content.research_evidence))
    return tuple(issues)


def build_candidate_envelope(content: KnowledgeCandidateContent) -> KnowledgeCandidateEnvelope:
    issues = validate_candidate_content(content)
    if issues:
        raise ValueError(issues[0].code)
    return KnowledgeCandidateEnvelope(content, "sha256", hashlib.sha256(canonical_candidate_bytes(content)).hexdigest())


def require_explicit_tier(content: KnowledgeCandidateContent) -> None:
    """A stricter check than validate_candidate_content()/the schema:
    tier is optional at the storage/schema layer (content persisted
    before this field existed must keep loading as tier=None), but any
    NEW candidate this codebase itself constructs must always set it
    explicitly. Call this before save_candidate() for anything freshly
    authored -- never for a value read back from storage."""
    if content.tier not in ("canonical", "complementary"):
        raise ValueError("KNOWLEDGE_TIER_REQUIRED")


def knowledge_entry_to_dict(entry: KnowledgeEntry) -> dict[str, Any]:
    return _json_value(entry)


def research_evidence_from_dict(value: dict[str, Any]) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=value["evidence_id"], claim=value["claim"], label=value["label"],
        sources=tuple(EvidenceSource(**source) for source in value["sources"]),
        based_on_evidence_ids=tuple(value["based_on_evidence_ids"]),
        uncertainty_reason=value["uncertainty_reason"],
    )


def knowledge_entry_from_dict(value: dict[str, Any]) -> KnowledgeEntry:
    applicability = value["applicability"]
    binding = value["repository_binding"]
    return KnowledgeEntry(
        schema_version=value["schema_version"], knowledge_id=value["knowledge_id"], revision=value["revision"],
        created_at=value["created_at"], status=value["status"], label=value["label"], claim=value["claim"],
        applicability=KnowledgeApplicability(tuple(applicability["product_areas"]), tuple(applicability["limitations"])),
        repository_binding=RepositoryBinding(**binding) if binding else None,
        research_evidence=tuple(research_evidence_from_dict(item) for item in value["research_evidence"]),
        based_on=tuple(value["based_on"]), contradicts=tuple(value["contradicts"]), supersedes=tuple(value["supersedes"]),
        candidate_id=value["candidate_id"], candidate_revision=value["candidate_revision"],
        candidate_content_digest=value["candidate_content_digest"],
        # Same backward-compatible .get() as candidate_content_from_dict().
        tier=value.get("tier"),
    )


CANDIDATE_TRANSITIONS: frozenset[tuple[str | None, str]] = frozenset({
    (None, "draft"), ("draft", "awaiting_emma_review"), ("draft", "withdrawn"),
    ("awaiting_emma_review", "changes_required"), ("awaiting_emma_review", "awaiting_human_authorization"),
    ("awaiting_emma_review", "rejected"), ("changes_required", "draft"), ("changes_required", "withdrawn"),
    ("awaiting_human_authorization", "accepted"), ("awaiting_human_authorization", "rejected"),
    ("awaiting_human_authorization", "withdrawn"),
})
ENTRY_TRANSITIONS: frozenset[tuple[str | None, str]] = frozenset({
    (None, "active"), ("active", "stale"), ("active", "conflicted"), ("active", "superseded"), ("active", "retired"),
    ("stale", "active"), ("stale", "conflicted"), ("stale", "superseded"), ("stale", "retired"),
    ("conflicted", "active"), ("conflicted", "stale"), ("conflicted", "superseded"), ("conflicted", "retired"),
})


def require_candidate_transition(current: str | None, new: str, *, label: str) -> None:
    if (current, new) not in CANDIDATE_TRANSITIONS:
        raise ValueError("KNOWLEDGE_CANDIDATE_TRANSITION_FORBIDDEN")
    if new == "accepted" and label not in {"FACT", "INTENT"}:
        raise ValueError("KNOWLEDGE_LABEL_NOT_PROMOTABLE")


def require_entry_transition(current: str | None, new: str) -> None:
    if (current, new) not in ENTRY_TRANSITIONS:
        raise ValueError("KNOWLEDGE_ENTRY_TRANSITION_FORBIDDEN")
