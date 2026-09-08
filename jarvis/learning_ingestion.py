"""M5 (Learning & Knowledge Continuity) -- pure derivation of knowledge
CANDIDATES from a completed mission's own frozen learning projection
(jarvis.learning_projection.MissionLearningProjection).

derive_knowledge_candidates() is a PURE function: no I/O, no LLM/
reasoning, no randomness. Given the same projection it always returns the
byte-for-byte same tuple of KnowledgeCandidateContent, including every
candidate_id -- this determinism is load-bearing, not a nice-to-have: it
is what lets jarvis.mission_coordinator.derive_knowledge_for_completed_
mission() re-derive after a crash and land on the exact same candidates
a prior, interrupted attempt already (idempotently) submitted, so a crash
between "submit some candidates" and "record completion" can always be
safely retried from scratch.

This module never talks to jarvis.knowledge_storage, orchestrator.chugel,
or any mission-record mutator -- it only ever consumes a
MissionLearningProjection and returns KnowledgeCandidateContent values.
Submitting them through the real draft -> awaiting_emma_review ->
awaiting_human_authorization -> accepted pipeline (unmodified, no new
authority) is jarvis.mission_coordinator's job, not this module's.

--- Derivation rules -------------------------------------------------

Resolved-finding-class candidates: `review_rounds` is
`projection.attempts` filtered to `actor == "emma"`, preserving original
order (mirrors reviewer_evidence's own append-only array order). If fewer
than two review rounds exist, no resolved-finding candidate is derived
(a claim of "resolved" needs at least one earlier round plus a final
round to compare against). Otherwise, every P0/P1 finding in every round
except the last is grouped by (category, file) -- NEVER by finding_id
(not guaranteed stable across rounds) and NEVER by line_range (drifts as
unrelated code moves between corrective cycles, which would produce a
false "resolved" claim for a finding that was only relocated, not
fixed). A finding with file=None is skipped entirely for this rule (a
deliberate false negative: under-claiming is safe, a claim must never be
issued against an unattributed file). For every (category, file) key that
appears in an earlier round but has zero P0/P1 findings under that same
key in the LAST round, one candidate is emitted. Its claim lists, once
each, every distinct (by exact string equality) summary text found for
that key across the qualifying earlier rounds -- attributed by 1-indexed
round position ("review round N of M") when more than one distinct
summary exists, included bare (no attribution) when only one distinct
summary exists across all qualifying earlier rounds -- followed by a
fixed trailer stating only that the final, accepted review reported no
P0/P1 finding in that category for that file. This claim NEVER asserts
causation (that a specific earlier finding was fixed by a specific
change) -- only absence-in-the-final-round.

Outcome candidate: emitted at most once, only if
`projection.corrective_cycle_count == 0` OR the last review round (if any
exist) has `verdict == "PASS"` and zero P0/P1 findings. Its claim is built
ONLY from verbatim concatenation of `projection.outcome`,
`projection.scope`, and `projection.acceptance_criteria` -- every
substantive word traces directly to one of those three flat projection
fields, nothing is synthesized or inferred.

EvidenceTier is hard-fixed to "complementary" for every candidate this
module produces -- a literal constant, never a parameter, never
conditionally chosen. require_explicit_tier() is called on every
constructed KnowledgeCandidateContent before it is returned.

--- candidate_id determinism -------------------------------------------

Every candidate_id is `str(uuid.uuid5(_KNOWLEDGE_CANDIDATE_NAMESPACE, seed))`
for a seed built only from the projection's own mission_id plus (for
resolved-finding candidates) the (category, file) key, or the literal
suffix ":outcome" for the outcome candidate. `_KNOWLEDGE_CANDIDATE_
NAMESPACE` is a fixed, hardcoded uuid.UUID constant defined once below
and must never change -- changing it would silently break crash-recovery
idempotency for every mission whose knowledge derivation is already
in flight. `uuid.uuid4()` is never used anywhere in this module.

--- product_areas mapping (documented here since the design review left
    the exact mapping to this implementation's judgment) -------------

Mission Records store `repository.branch` as a bare branch name (e.g.
"feature/x"), never already in refs/ form -- `_repository_ref()` below
maps it to `refs/heads/<branch>` (mission-derived knowledge is always
about the mission's own local branch, never a remote-tracking ref)
unless the stored value already carries an explicit `refs/heads/` or
`refs/remotes/origin/` prefix.

`_derive_product_areas()` builds a small, deterministic, bounded set of
tags a human already recognizes from this codebase's existing ad hoc
product_area vocabulary (short lowercase words like "jarvis", "billing",
"auth" -- see tests/test_jarvis_knowledge_retrieval.py and
tests/test_jarvis_mission_context.py): the fixed literal "zentra" (every
mission-derived candidate belongs to the one shared Zentra codebase this
system operates on, so it is always eligible for a "zentra"-scoped
query) plus, deterministically, the first 1-3 lowercase alphanumeric
tokens of the branch's own path (e.g. "feat/m5-learning-continuity" ->
tokens "m5", "learning", "continuity") plus the first lowercase
alphanumeric token of every `projection.scope` entry. The result is
sorted for determinism and has no fixed upper bound beyond the schema's
own 50-item limit (a mission's scope list is itself schema-bounded), but
in practice stays small. This is a bounded, good-enough default for this
increment, not a general-purpose taxonomy -- a future increment is free
to replace it without touching anything else in this module.

--- Emma review-instruction requirement (documentation only; see
    orchestrator/CHUGEL_V1.md / EmmaKnowledgeReview's own schema for the
    only actually-enforced review contract -- there is no code
    enforcement mechanism for the checklist below beyond what already
    exists) --------------------------------------------------------

Any EmmaKnowledgeReview of a mission-derived candidate produced by this
module must, before verdicting PASS, satisfy ALL of:

1. Verify the absence claim against the real mission diff and both the
   earlier and final review rounds -- not merely re-read the candidate's
   own text.
2. Check the failure to re-find a P0/P1 finding isn't explained by:
   (a) the file being deleted/renamed so the category trivially no
   longer applies, (b) the final round simply not re-examining that
   file, (c) a coincidental unrelated line-range shift.
3. Check whether the (category, file) pair still has ANY finding of ANY
   severity (including P2/P3) in the final round -- if so, this is a
   severity downgrade, not a genuine absence; do not verdict PASS unless
   the candidate's own claim text already reflects that nuance.
4. Never verdict PASS on a candidate whose claim asserts or implies that
   a *specific* earlier finding was fixed by a *specific* change -- the
   claim must only ever assert absence-in-the-final-round, never
   causation."""

from __future__ import annotations

import re
import uuid

from jarvis.knowledge import (
    KnowledgeApplicability, KnowledgeCandidateContent, RepositoryBinding, require_explicit_tier,
)
from jarvis.learning_projection import LearningAttemptProjection, MissionLearningProjection
from jarvis.models import EvidenceSource, ResearchEvidence

# Fixed, permanent, never changed -- see the module docstring's
# "candidate_id determinism" section. This is an arbitrary, but
# permanently fixed, literal UUID.
_KNOWLEDGE_CANDIDATE_NAMESPACE = uuid.UUID("6f1f6a1e-3f0a-4c9a-9e9a-1c9a5b2e7d10")

_RESOLVED_SEVERITIES = frozenset({"P0", "P1"})
_TOKEN = re.compile(r"[a-z0-9]+")


def _repository_ref(branch: str) -> str:
    if branch.startswith("refs/heads/") or branch.startswith("refs/remotes/origin/"):
        return branch
    return f"refs/heads/{branch}"


def _derive_product_areas(repository_ref: str, scope: tuple[str, ...]) -> tuple[str, ...]:
    areas: set[str] = {"zentra"}
    branch_component = repository_ref.split("/", 2)[-1] if repository_ref.count("/") >= 2 else repository_ref
    branch_tokens = _TOKEN.findall(branch_component.lower())
    areas.update(branch_tokens[:3])
    for item in scope:
        tokens = _TOKEN.findall(item.lower())
        if tokens:
            areas.add(tokens[0])
    return tuple(sorted(areas))


def _repository_binding(projection: MissionLearningProjection) -> RepositoryBinding:
    return RepositoryBinding(_repository_ref(projection.repository.branch), projection.repository.base_sha)


def _applicability(projection: MissionLearningProjection) -> KnowledgeApplicability:
    ref = _repository_ref(projection.repository.branch)
    return KnowledgeApplicability(_derive_product_areas(ref, projection.scope), ())


def _research_evidence(candidate_id: str, claim: str, projection: MissionLearningProjection) -> tuple[ResearchEvidence, ...]:
    evidence_id = f"ev-{candidate_id}"
    source = EvidenceSource("mission_record", projection.mission_id, projection.updated_at)
    return (ResearchEvidence(evidence_id, claim, "FACT", (source,)),)


def _build_candidate(
    *, candidate_id: str, claim: str, label: str, projection: MissionLearningProjection,
) -> KnowledgeCandidateContent:
    content = KnowledgeCandidateContent(
        schema_version="1.0",
        candidate_id=candidate_id,
        revision=1,
        created_at=projection.updated_at,
        target_knowledge_id=None,
        expected_target_revision=None,
        expected_current_status=None,
        proposed_entry_status="active",
        claim=claim,
        label=label,
        applicability=_applicability(projection),
        repository_binding=_repository_binding(projection),
        research_evidence=_research_evidence(candidate_id, claim, projection),
        tier="complementary",
    )
    require_explicit_tier(content)
    return content


def _review_rounds(projection: MissionLearningProjection) -> tuple[LearningAttemptProjection, ...]:
    return tuple(attempt for attempt in projection.attempts if attempt.actor == "emma")


def _resolved_finding_candidates(
    projection: MissionLearningProjection, review_rounds: tuple[LearningAttemptProjection, ...],
) -> tuple[KnowledgeCandidateContent, ...]:
    if len(review_rounds) < 2:
        return ()

    earlier_rounds = review_rounds[:-1]
    final_round = review_rounds[-1]
    total_rounds = len(review_rounds)

    final_keys = {
        (finding.category, finding.file)
        for finding in final_round.findings
        if finding.severity in _RESOLVED_SEVERITIES
    }

    grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for index, round_ in enumerate(earlier_rounds):
        for finding in round_.findings:
            if finding.severity not in _RESOLVED_SEVERITIES:
                continue
            if finding.file is None:
                continue
            key = (finding.category, finding.file)
            grouped.setdefault(key, []).append((index, finding.summary))

    candidates: list[KnowledgeCandidateContent] = []
    for (category, file), entries in grouped.items():
        if (category, file) in final_keys:
            continue

        distinct: list[tuple[int, str]] = []
        seen_summaries: set[str] = set()
        for index, summary in entries:
            if summary in seen_summaries:
                continue
            seen_summaries.add(summary)
            distinct.append((index, summary))

        if len(distinct) == 1:
            claim_lead = distinct[0][1]
        else:
            claim_lead = "; ".join(
                f"review round {index + 1} of {total_rounds}: {summary}" for index, summary in distinct
            )

        claim = (
            f"{claim_lead}; the mission's final, accepted review reported no P0/P1 finding "
            f"in category {category} for {file}."
        )
        candidate_id = str(uuid.uuid5(_KNOWLEDGE_CANDIDATE_NAMESPACE, f"{projection.mission_id}:{category}:{file}"))
        candidates.append(_build_candidate(candidate_id=candidate_id, claim=claim, label="FACT", projection=projection))

    return tuple(candidates)


def _outcome_candidate(
    projection: MissionLearningProjection, review_rounds: tuple[LearningAttemptProjection, ...],
) -> KnowledgeCandidateContent | None:
    fires = projection.corrective_cycle_count == 0
    if not fires and review_rounds:
        last = review_rounds[-1]
        fires = last.verdict == "PASS" and not any(
            finding.severity in _RESOLVED_SEVERITIES for finding in last.findings
        )
    if not fires:
        return None

    # Every substantive word here is a verbatim copy of a real, flat
    # projection field. The "Objective:"/"Scope:"/"Acceptance criteria:"
    # labels are pure structural framing (no factual content of their
    # own) -- unlike an earlier draft of this function, this claim does
    # NOT append any trailer sentence asserting the mission "reached
    # COMPLETED" or any other fact not drawn directly from the three
    # fields below: that would be new, non-verbatim factual content, and
    # this derivation function's whole safety property depends on never
    # introducing any (a mission-derived candidate that asserted more
    # than the schema's own fields could support was exactly the kind of
    # overclaim this design was corrected, across several rounds of
    # independent review, to never make).
    claim = (
        f"Objective: {projection.outcome}. "
        f"Scope: {'; '.join(projection.scope)}. "
        f"Acceptance criteria: {'; '.join(projection.acceptance_criteria)}."
    )
    candidate_id = str(uuid.uuid5(_KNOWLEDGE_CANDIDATE_NAMESPACE, f"{projection.mission_id}:outcome"))
    return _build_candidate(candidate_id=candidate_id, claim=claim, label="INTENT", projection=projection)


def derive_knowledge_candidates(projection: MissionLearningProjection) -> tuple[KnowledgeCandidateContent, ...]:
    """Pure, deterministic. See module docstring for the full derivation
    rules; this function only orchestrates the two rule families above in
    a fixed order (resolved-finding candidates, then the outcome
    candidate) so the returned tuple's ORDER is itself deterministic too."""
    review_rounds = _review_rounds(projection)
    candidates = list(_resolved_finding_candidates(projection, review_rounds))
    outcome = _outcome_candidate(projection, review_rounds)
    if outcome is not None:
        candidates.append(outcome)
    return tuple(candidates)
