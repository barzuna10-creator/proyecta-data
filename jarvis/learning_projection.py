"""Frozen, recursively detached allow-list for mission-derived learning evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LearningRepositoryProjection:
    branch: str
    base_sha: str
    isolation_confirmed: bool


@dataclass(frozen=True, slots=True)
class LearningGateProjection:
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class LearningFindingProjection:
    finding_id: str
    severity: str
    summary: str
    file: str | None
    line_range: str | None
    category: str


@dataclass(frozen=True, slots=True)
class LearningArtifactProjection:
    mode: str
    commit_sha: str | None
    patch_sha256: str | None


@dataclass(frozen=True, slots=True)
class LearningAttemptProjection:
    attempt: int
    actor: str
    artifact: LearningArtifactProjection
    conclusion: str | None
    verdict: str | None
    changed_files: tuple[str, ...]
    findings: tuple[LearningFindingProjection, ...]


@dataclass(frozen=True, slots=True)
class MissionLearningProjection:
    mission_id: str
    state: str
    updated_at: str
    corrective_cycle_count: int
    repository: LearningRepositoryProjection
    mission_definition_version: int
    outcome: str
    scope: tuple[str, ...]
    non_goals: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    gates: tuple[LearningGateProjection, ...]
    attempts: tuple[LearningAttemptProjection, ...]


def _project_artifact(value: dict[str, Any]) -> LearningArtifactProjection:
    """Project identity only; deliberately exclude the schema's patch path."""
    mode = value["mode"]
    if mode == "commit":
        commit_sha = value["commit_sha"]
        if not isinstance(commit_sha, str) or value["patch_sha256"] is not None:
            raise ValueError("invalid canonical commit artifact")
        return LearningArtifactProjection("commit", str(commit_sha), None)
    if mode == "patch":
        patch_sha256 = value["patch_sha256"]
        if not isinstance(patch_sha256, str) or value["commit_sha"] is not None:
            raise ValueError("invalid canonical patch artifact")
        return LearningArtifactProjection("patch", None, str(patch_sha256))
    raise ValueError("unknown canonical artifact mode")


def project_mission_learning(record: dict[str, Any]) -> MissionLearningProjection:
    history = record["mission_definition_history"]
    definition = history[-1] if history else {"outcome": "", "scope": [], "non_goals": [], "acceptance_criteria": []}
    repository = record["repository"]
    gates = record["human_gates"]
    attempts: list[LearningAttemptProjection] = []
    for item in record["builder_evidence"]:
        attempts.append(LearningAttemptProjection(
            int(item["attempt"]), "emilio", _project_artifact(item["artifact"]),
            None if item.get("conclusion") is None else str(item["conclusion"].get("text", "")), None,
            tuple(str(value["path"]) for value in item["changed_files"]), (),
        ))
    for item in record["reviewer_evidence"]:
        attempts.append(LearningAttemptProjection(
            int(item["attempt"]), "emma", _project_artifact(item["artifact_identity_confirmed_before_conclusion"]),
            None, str(item["verdict"]), (), tuple(
                LearningFindingProjection(str(finding["id"]), str(finding["severity"]), str(finding["summary"]),
                    None if finding.get("file") is None else str(finding["file"]),
                    None if finding.get("line_range") is None else str(finding["line_range"]), str(finding["category"]))
                for finding in item["findings"]
            ),
        ))
    return MissionLearningProjection(
        str(record["mission_id"]), str(record["state"]), str(record["updated_at"]), int(record["corrective_cycle_count"]),
        LearningRepositoryProjection(str(repository["branch"]), str(repository["base_sha"]), bool(repository["isolation_confirmed"])),
        len(history), str(definition["outcome"]), tuple(str(value) for value in definition["scope"]),
        tuple(str(value) for value in definition["non_goals"]), tuple(str(value) for value in definition["acceptance_criteria"]),
        tuple(LearningGateProjection(name, str(gates[name]["status"])) for name in ("scope_authorization", "publish_authorization", "merge_authorization")),
        tuple(attempts),
    )
