"""Mission 004 -- builds the persisted MissionDefinition. Structurally
forbidden from importing jarvis.knowledge_retrieval, jarvis.knowledge_storage,
or jarvis.repository_freshness (enforced by
tests/test_jarvis_foundation_boundaries.py) -- this module cannot search
trusted knowledge itself, and its only public entry point,
build_mission_definition(), accepts no parameter capable of carrying a
jarvis.mission_context.ProposalBriefing object.

This closes the "wrong object type" smuggling path structurally. It does
NOT, and cannot, prevent the calling conversation turn from copying
matching *string content* between a briefing citation and a
JoseDecisions field -- both are plain str/tuple[str, ...] values, and no
type system can distinguish "this string happens to equal briefing
text" from any other string. That residual is controlled by two other,
independent mechanisms, not by this module's typing: the data-flow
overlap test (tests/test_jarvis_mission_context.py) and
agents/jarvis/PLAYBOOK.md's "Mission 004 proposal mode" rule 5
(never phrase scope/acceptance_criteria/outcome using wording drawn
from a knowledge-search result, even paraphrased)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JoseDecisions:
    """Every field is a direct, structured restatement of something José
    actually said this turn -- never free text copied from a
    ProposalBriefing or any other knowledge-derived source. No field is a
    general-purpose prose slot beyond what it names."""
    outcome: str
    scope: tuple[str, ...]
    non_goals: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]


def build_mission_definition(objective_text: str, decisions: JoseDecisions) -> dict:
    if not isinstance(objective_text, str) or not objective_text.strip():
        raise ValueError("objective_text must be a non-empty string")
    if not isinstance(decisions, JoseDecisions):
        raise TypeError("decisions must be a JoseDecisions instance")
    return {
        "outcome": decisions.outcome,
        "scope": list(decisions.scope),
        "non_goals": list(decisions.non_goals),
        "acceptance_criteria": list(decisions.acceptance_criteria),
    }
