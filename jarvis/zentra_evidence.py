"""Jarvis Mission 005 -- the sole production module permitted to read
jarvis/zentra_sources_policy.json, and the sole production module
(besides repository_freshness.py itself) that calls
RepositoryFreshnessResolver.read_blob(). CLI-only: this module is never
imported by jarvis.control_plane_server or orchestrator.jarvis_conversation
-- see tests/test_jarvis_foundation_boundaries.py's
SOLE_ZENTRA_POLICY_READERS check. There is structurally no path from a
live conversation turn to anything in this file.

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
class ZentraSourcesPolicy:
    owner: str
    name: str
    authorized_ref: str
    authorized_commit_sha: str
    sources: tuple[ZentraSource, ...]


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

    paths = [item["path"] for item in payload["sources"]]
    if len(paths) != len(set(paths)):
        raise ZentraSourcesPolicyInvalid("duplicate path in sources")

    repository = payload["repository"]
    sources = tuple(ZentraSource(item["path"], item["tier"], item["kind"]) for item in payload["sources"])
    return ZentraSourcesPolicy(
        owner=repository["owner"], name=repository["name"], authorized_ref=repository["authorized_ref"],
        authorized_commit_sha=repository["authorized_commit_sha"], sources=sources,
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_evidence(
    path: str,
    *,
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
    matched = next((source for source in active_policy.sources if source.path == path), None)
    if matched is None:
        raise ZentraSourceNotAllowed(path)

    resolver = RepositoryFreshnessResolver(repository_root)
    content = resolver.read_blob(active_policy.authorized_commit_sha, path)
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
                commit_sha=active_policy.authorized_commit_sha,
                excerpt_sha256=excerpt_sha256,
            ),
        ),
    )
    return evidence, matched
