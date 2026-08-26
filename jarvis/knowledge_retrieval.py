"""Deterministic eligibility filtering and ranking over trusted knowledge.

No language-model, reasoning, external-API, or network path exists here
or is reachable from here. This module calls only the public read surface
of FileKnowledgeStore (list_latest_entries) and the public interface of
RepositoryFreshnessResolver (resolve_commit) -- never Chugel, never a
mutation method, never repository_freshness.py's subprocess internals
directly."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.knowledge import KnowledgeEntry
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.repository_freshness import FreshnessError, RepositoryFreshnessResolver

_HARD_MAX_TOP_K = 50
_DEFAULT_TOP_K = 10


class KnowledgeSearchTopKInvalid(ValueError):
    code = "KNOWLEDGE_SEARCH_TOP_K_INVALID"


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    entry: KnowledgeEntry
    match_reasons: tuple[str, ...]
    rank: int


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResponse:
    results: tuple[KnowledgeSearchResult, ...]
    omitted_count: int
    eligible_beyond_top_k: int


def _label_priority(label: str) -> int:
    return {"INTENT": 0, "FACT": 1}.get(label, 2)


def _eligible(entry: KnowledgeEntry, *, product_areas: tuple[str, ...], resolver: RepositoryFreshnessResolver) -> tuple[bool, tuple[str, ...]]:
    """Return (eligible, match_reasons). Never raises: every failure mode
    is caught here and folded into an ineligible result."""
    if entry.status != "active":
        return False, ()
    if entry.label not in ("FACT", "INTENT"):
        return False, ()

    matched_areas = tuple(sorted(set(entry.applicability.product_areas) & set(product_areas)))
    if product_areas and not matched_areas:
        return False, ()

    reasons = [f"PRODUCT_AREA_MATCH:{area}" for area in matched_areas]
    reasons.append("LABEL_INTENT" if entry.label == "INTENT" else "LABEL_FACT")

    if entry.repository_binding is not None:
        try:
            resolved = resolver.resolve_commit(entry.repository_binding.repository_ref)
        except FreshnessError:
            return False, ()
        if resolved != entry.repository_binding.expected_commit_sha:
            return False, ()
        reasons.append("REPOSITORY_FRESH")
    else:
        reasons.append("NO_REPOSITORY_BINDING")

    return True, tuple(reasons)


def search(
    store: FileKnowledgeStore,
    resolver: RepositoryFreshnessResolver,
    *,
    product_areas: tuple[str, ...] = (),
    top_k: int = _DEFAULT_TOP_K,
) -> KnowledgeSearchResponse:
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= _HARD_MAX_TOP_K:
        raise KnowledgeSearchTopKInvalid(top_k)

    listing = store.list_latest_entries()
    omitted_count = listing.omitted_count

    eligible: list[tuple[KnowledgeEntry, tuple[str, ...]]] = []
    for entry in listing.entries:
        ok, reasons = _eligible(entry, product_areas=product_areas, resolver=resolver)
        if ok:
            eligible.append((entry, reasons))
        else:
            omitted_count += 1

    def sort_key(item: tuple[KnowledgeEntry, tuple[str, ...]]):
        entry, _ = item
        match_count = len(set(entry.applicability.product_areas) & set(product_areas))
        return (-match_count, _label_priority(entry.label), entry.knowledge_id, -entry.revision)

    eligible.sort(key=sort_key)

    kept = eligible[:top_k]
    eligible_beyond_top_k = max(0, len(eligible) - top_k)

    results = tuple(
        KnowledgeSearchResult(entry=entry, match_reasons=reasons, rank=rank)
        for rank, (entry, reasons) in enumerate(kept, 1)
    )
    return KnowledgeSearchResponse(results, omitted_count, eligible_beyond_top_k)
