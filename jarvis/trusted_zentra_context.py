"""Deterministic, non-authoritative, read-only context assembly for Jarvis."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from jarvis import mission_query
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.mission_context import draft_briefing
from jarvis.repository_freshness import FreshnessError, RepositoryFreshnessResolver
from jarvis.zentra_evidence import ZentraSourcesPolicy
from jarvis.zentra_github_query import GitHubQueryError, ReadOnlyGitHubQuery

MAX_EXCERPT_CHARS = 6000
MAX_BUNDLE_BYTES = 48_000
MAX_SOURCES = 12
MAX_MISSIONS = 20
MAX_KNOWLEDGE = 5


@dataclass(frozen=True, slots=True)
class ContextSource:
    repository: str; path: str; tier: str; kind: str
    expected_commit_sha: str; observed_commit_sha: str | None
    observed_at: str; freshness: str; excerpt_sha256: str | None
    excerpt: str; truncated: bool; content_role: str = "untrusted_source_data"
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedZentraContext:
    schema_version: str; observed_at: str
    sources: tuple[ContextSource, ...]; missions: tuple[dict, ...]
    knowledge: tuple[dict, ...]; github: tuple[dict, ...]; omitted_count: int

    def to_prompt_payload(self) -> dict:
        return json.loads(json.dumps(asdict(self), sort_keys=True))


class TrustedZentraContextBuilder:
    def __init__(self, policy: ZentraSourcesPolicy, repository_roots: dict[str, Path], *, knowledge_store: FileKnowledgeStore | None = None, github_query: ReadOnlyGitHubQuery | None = None):
        self._policy = policy; self._roots = dict(repository_roots)
        self._knowledge_store = knowledge_store; self._github_query = github_query

    def build(self) -> TrustedZentraContext:
        import hashlib
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sources: list[ContextSource] = []; github: list[dict] = []; omitted = 0
        source_budget = MAX_SOURCES
        for repository in self._policy.repositories:
            root = self._roots.get(repository.key)
            resolver = None
            observed = None
            freshness = "unavailable"; error = "REPOSITORY_NOT_CONFIGURED"
            if root is not None:
                try:
                    resolver = RepositoryFreshnessResolver(root)
                    observed = resolver.resolve_commit(repository.authorized_ref)
                    freshness = "fresh" if observed == repository.authorized_commit_sha else "stale"
                    error = None if freshness == "fresh" else "COMMIT_MISMATCH"
                except FreshnessError:
                    error = "REPOSITORY_UNAVAILABLE"
            selected_sources = repository.sources[:source_budget]
            omitted += len(repository.sources) - len(selected_sources)
            source_budget -= len(selected_sources)
            for source in selected_sources:
                excerpt = ""; digest = None; truncated = False; source_error = error
                if freshness == "fresh" and resolver is not None:
                    try:
                        raw = resolver.read_blob(repository.authorized_commit_sha, source.path)
                        text = raw.decode("utf-8")
                        digest = hashlib.sha256(raw).hexdigest()
                        truncated = len(text) > MAX_EXCERPT_CHARS
                        excerpt = text[:MAX_EXCERPT_CHARS]
                        source_error = None
                    except (FreshnessError, UnicodeDecodeError):
                        source_error = "SOURCE_UNAVAILABLE"; freshness_for_source = "unavailable"
                    else: freshness_for_source = freshness
                else: freshness_for_source = freshness
                sources.append(ContextSource(
                    f"{repository.host}/{repository.owner}/{repository.name}", source.path, source.tier, source.kind,
                    repository.authorized_commit_sha, observed, now, freshness_for_source, digest,
                    excerpt, truncated, error_code=source_error,
                ))
            if self._github_query is not None:
                name = f"{repository.host}/{repository.owner}/{repository.name}"
                try:
                    observation = asdict(self._github_query.observe(name))
                    observation.update({"freshness":"current_at_read","status":"available"})
                    github.append(observation)
                except GitHubQueryError: github.append({"repository":name,"observed_at":now,"freshness":"unavailable","status":"unavailable","error_code":"GITHUB_QUERY_FAILED"})

        listings = mission_query.list_missions()
        missions = tuple({
            "mission_id": item.mission_id, "state": item.state, "bucket": item.bucket,
            "readable": item.readable, "updated_at": item.updated_at,
            "freshness": "current_at_read" if item.readable else "unavailable",
            "provenance": {"authority":"chugel","observed_at":now},
            "error_code": item.error_code,
        } for item in listings[:MAX_MISSIONS])
        omitted += max(0, len(listings) - MAX_MISSIONS)
        knowledge: tuple[dict, ...] = ()
        backend = next((r for r in self._policy.repositories if r.key == "backend"), None)
        if self._knowledge_store is not None and backend is not None and self._roots.get("backend") is not None:
            briefing = draft_briefing(self._knowledge_store, RepositoryFreshnessResolver(self._roots["backend"]), product_areas=("zentra",), top_k=MAX_KNOWLEDGE)
            knowledge = tuple({
                "knowledge_id":c.knowledge_id,"claim":c.claim,"label":c.label,"tier":c.tier,
                "freshness":"fresh" if c.repository_ref else "not_repository_bound",
                "provenance": {"repository_ref":c.repository_ref,"expected_commit_sha":c.expected_commit_sha,"sources":list(c.evidence_sources)},
            } for c in briefing.citations)
            omitted += briefing.omitted_count
        payload = TrustedZentraContext("1.0", now, tuple(sources), missions, knowledge, tuple(github), omitted)
        # A hard final bound. Drop source excerpts from the end, never truncate JSON invisibly.
        def payload_size() -> int:
            return len(json.dumps(payload.to_prompt_payload(), ensure_ascii=False).encode("utf-8"))
        while payload_size() > MAX_BUNDLE_BYTES and sources:
            index = next((index for index in range(len(sources) - 1, -1, -1) if sources[index].excerpt), None)
            if index is None:
                break
            last = sources[index]
            sources[index] = ContextSource(last.repository,last.path,last.tier,last.kind,last.expected_commit_sha,last.observed_commit_sha,last.observed_at,last.freshness,last.excerpt_sha256,"",True,last.content_role,"BUNDLE_LIMIT")
            payload = TrustedZentraContext("1.0", now, tuple(sources), missions, knowledge, tuple(github), omitted + 1)
        if payload_size() > MAX_BUNDLE_BYTES:
            raise ValueError("TRUSTED_CONTEXT_BUNDLE_LIMIT")
        return payload
