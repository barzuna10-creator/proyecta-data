"""Mission 004 -- the only Jarvis module (besides knowledge_retrieval.py
and cli.py) permitted to import jarvis.knowledge_retrieval. Produces a
ProposalBriefing shown to José for context only. Nothing here is ever
persisted into a Mission Record and nothing here is ever passed to
jarvis.mission_proposal.build_mission_definition() -- those two modules
share no types and no conversion function exists between them anywhere
in this codebase."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.knowledge_retrieval import KnowledgeSearchResponse, search
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.repository_freshness import RepositoryFreshnessResolver


@dataclass(frozen=True, slots=True)
class BriefingCitation:
    knowledge_id: str
    claim: str
    label: str
    # Explicit authority tier, carried through unchanged from the
    # KnowledgeEntry -- None means unclassified legacy content, never
    # silently treated as canonical by anything reading this citation.
    tier: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalBriefing:
    citations: tuple[BriefingCitation, ...]
    omitted_count: int


def draft_briefing(
    store: FileKnowledgeStore,
    resolver: RepositoryFreshnessResolver,
    *,
    product_areas: tuple[str, ...] = (),
    top_k: int = 5,
) -> ProposalBriefing:
    """Read-only. Shown to José in conversation for context only -- see
    the module docstring and agents/jarvis/PLAYBOOK.md's "Mission 004
    proposal mode" rule 5 for the behavioral rule this briefing's content
    must never be paraphrased into a MissionDefinition."""
    response: KnowledgeSearchResponse = search(
        store, resolver, product_areas=product_areas, top_k=top_k
    )
    citations = tuple(
        BriefingCitation(result.entry.knowledge_id, result.entry.claim, result.entry.label, result.entry.tier)
        for result in response.results
    )
    return ProposalBriefing(citations, response.omitted_count)
