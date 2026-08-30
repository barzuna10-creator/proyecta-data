"""Jarvis Mission 005 -- the sole production module permitted to read
jarvis/zentra_sources_policy.json. Mission 005's candidate-producing path
remains operator-only; Trusted Zentra Context V1 reuses only this strictly
validated policy and performs its own bounded read-only composition.

Produces exactly one thing: a real, provenance-stamped ResearchEvidence
for one file already on the policy's fixed, hand-authored allow-list,
read at the policy's own fixed, hand-authored authorized commit --
never a caller-supplied path, never a caller-supplied commit. Reading
here is never authorization: the candidate this evidence feeds into
still requires the unmodified Emma-review + José-authorization + promote
sequence in jarvis.knowledge_storage before it can ever be cited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal

import jsonschema

from jarvis.knowledge import EvidenceTier
from jarvis.models import EvidenceSource, ResearchEvidence
from jarvis.repository_freshness import RepositoryFreshnessResolver

_POLICY_PATH = Path(__file__).resolve().parent / "zentra_sources_policy.json"
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "zentra_sources_policy.schema.json"

SourceKind = Literal["repository_file", "product_document"]


class ZentraSourcesPolicyError(Exception):
    code = "ZENTRA_SOURCES_POLICY_ERROR"


class ZentraSourcesPolicyInvalid(ZentraSourcesPolicyError):
    code = "ZENTRA_SOURCES_POLICY_INVALID"


class ZentraSourceNotAllowed(ZentraSourcesPolicyError):
    code = "ZENTRA_SOURCE_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class ZentraSource:
    path: str
    tier: EvidenceTier
    kind: SourceKind


@dataclass(frozen=True, slots=True)
class ZentraRepository:
    key: str
    host: str
    owner: str
    name: str
    authorized_ref: str
    authorized_commit_sha: str
    sources: tuple[ZentraSource, ...]


@dataclass(frozen=True, slots=True, init=False)
class ZentraSourcesPolicy:
    repositories: tuple[ZentraRepository, ...]

    def __init__(self, repositories: tuple[ZentraRepository, ...] | None = None, *, owner: str | None = None, name: str | None = None, authorized_ref: str | None = None, authorized_commit_sha: str | None = None, sources: tuple[ZentraSource, ...] | None = None):
        # Backward-compatible construction for existing callers/tests; the
        # persisted V1 policy itself always uses explicit repositories.
        if repositories is None:
            if None in (owner, name, authorized_ref, authorized_commit_sha, sources):
                raise TypeError("repositories or complete legacy repository fields required")
            repositories = (ZentraRepository("backend", "github.com", owner, name, authorized_ref, authorized_commit_sha, sources),)  # type: ignore[arg-type]
        object.__setattr__(self, "repositories", tuple(repositories))

    @property
    def _legacy(self) -> ZentraRepository: return self.repositories[0]
    @property
    def owner(self) -> str: return self._legacy.owner
    @property
    def name(self) -> str: return self._legacy.name
    @property
    def authorized_ref(self) -> str: return self._legacy.authorized_ref
    @property
    def authorized_commit_sha(self) -> str: return self._legacy.authorized_commit_sha
    @property
    def sources(self) -> tuple[ZentraSource, ...]: return self._legacy.sources

    def source(self, repository_key: str, path: str) -> tuple[ZentraRepository, ZentraSource] | None:
        for repository in self.repositories:
            if repository.key == repository_key:
                return next(((repository, source) for source in repository.sources if source.path == path), None)
        return None


def load_policy(policy_path: Path | None = None) -> ZentraSourcesPolicy:
    """Load and strictly validate the versioned, git-tracked source
    policy. Fail-closed: any missing file, malformed JSON, schema
    violation, duplicate path, or out-of-range source count refuses to
    return a policy at all -- never a partially-trusted one. This never
    reads anything other than the policy file itself; it never touches a
    live git repository."""
    path = policy_path or _POLICY_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ZentraSourcesPolicyInvalid(f"could not read policy file: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ZentraSourcesPolicyInvalid(f"policy file is not valid JSON: {exc}") from exc

    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZentraSourcesPolicyInvalid(f"could not load policy schema: {exc}") from exc

    # str(), not the raw path element: jsonschema error paths can mix int
    # (array index) and str (property name) components, which raise
    # TypeError if compared directly by sorted()'s default key -- that
    # would surface as an unhandled TypeError instead of the intended
    # fail-closed ZentraSourcesPolicyInvalid.
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(payload), key=lambda item: [str(part) for part in item.path])
    if errors:
        raise ZentraSourcesPolicyInvalid(errors[0].message)

    repositories = []
    seen_keys: set[str] = set()
    seen_names: set[str] = set()
    for repository in payload["repositories"]:
        if repository["key"] in seen_keys or f'{repository["owner"]}/{repository["name"]}' in seen_names:
            raise ZentraSourcesPolicyInvalid("duplicate repository")
        seen_keys.add(repository["key"]); seen_names.add(f'{repository["owner"]}/{repository["name"]}')
        paths = [item["path"] for item in repository["sources"]]
        if len(paths) != len(set(paths)):
            raise ZentraSourcesPolicyInvalid("duplicate path in repository sources")
        repositories.append(ZentraRepository(
            repository["key"], repository["host"], repository["owner"], repository["name"],
            repository["authorized_ref"], repository["authorized_commit_sha"],
            tuple(ZentraSource(item["path"], item["tier"], item["kind"]) for item in repository["sources"]),
        ))
    return ZentraSourcesPolicy(tuple(repositories))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_evidence(
    path: str,
    *,
    repository_key: str = "backend",
    repository_root: Path,
    evidence_id: str,
    claim: str,
    policy: ZentraSourcesPolicy | None = None,
) -> tuple[ResearchEvidence, ZentraSource]:
    """Read exactly one policy-allow-listed file at the policy's own
    authorized commit, and return a FACT ResearchEvidence for it plus the
    matched ZentraSource (carrying its tier/kind).

    `path` is matched by EXACT string equality against the policy's own
    `sources[].path` values -- never a prefix, glob, or pattern. A path
    not present verbatim in the policy is refused outright; this
    function has no notion of "close enough"."""
    active_policy = policy or load_policy()
    match = active_policy.source(repository_key, path)
    if match is None:
        raise ZentraSourceNotAllowed(path)
    repository, matched = match

    resolver = RepositoryFreshnessResolver(repository_root)
    content = resolver.read_blob(repository.authorized_commit_sha, path)
    excerpt_sha256 = hashlib.sha256(content).hexdigest()
    observed_at = _now()

    evidence = ResearchEvidence(
        evidence_id=evidence_id,
        claim=claim,
        label="FACT",
        sources=(
            EvidenceSource(
                kind=matched.kind,
                locator=path,
                observed_at=observed_at,
                commit_sha=repository.authorized_commit_sha,
                excerpt_sha256=excerpt_sha256,
            ),
        ),
    )
    return evidence, matched
