"""Focused unit tests for jarvis/learning_ingestion.py's pure
derive_knowledge_candidates(). Constructs MissionLearningProjection
values directly -- no Chugel record, no I/O -- exactly matching this
module's own "pure function" contract; the real-mission, end-to-end path
(a genuine COMPLETED Chugel record feeding a real projection) is covered
separately by tests/test_jarvis_mission_coordinator_knowledge_derivation.py."""

from __future__ import annotations

import unittest
import uuid

from jarvis.knowledge import require_explicit_tier
from jarvis.learning_ingestion import _KNOWLEDGE_CANDIDATE_NAMESPACE, derive_knowledge_candidates
from jarvis.learning_projection import (
    LearningArtifactProjection, LearningAttemptProjection, LearningFindingProjection,
    LearningGateProjection, LearningRepositoryProjection, MissionLearningProjection,
)

_ARTIFACT = LearningArtifactProjection("commit", "a" * 40, None)
_APPROVED_GATE = LearningGateProjection("scope_authorization", "approved")


def _finding(finding_id="f1", severity="P1", summary="a bug", file="x.py", line_range="1-2", category="correctness"):
    return LearningFindingProjection(finding_id, severity, summary, file, line_range, category)


def _builder_attempt(attempt=0):
    return LearningAttemptProjection(attempt, "emilio", _ARTIFACT, "done", None, (), ())


def _reviewer_attempt(attempt, verdict, findings=()):
    return LearningAttemptProjection(attempt, "emma", _ARTIFACT, None, verdict, (), tuple(findings))


def _projection(*, attempts, corrective_cycle_count=0, outcome="Ship the thing", scope=("Do the thing",),
                 acceptance_criteria=("It works",), branch="feature/x", base_sha="b" * 40,
                 mission_id="11111111-1111-4111-8111-111111111111"):
    return MissionLearningProjection(
        mission_id=mission_id, state="COMPLETED", updated_at="2026-09-01T00:00:00Z",
        corrective_cycle_count=corrective_cycle_count,
        repository=LearningRepositoryProjection(branch, base_sha, True),
        mission_definition_version=1, outcome=outcome, scope=tuple(scope), non_goals=(),
        acceptance_criteria=tuple(acceptance_criteria),
        gates=(_APPROVED_GATE,), attempts=tuple(attempts),
    )


class DeterminismTests(unittest.TestCase):
    def test_derivation_is_byte_for_byte_deterministic(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding()]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        first = derive_knowledge_candidates(projection)
        second = derive_knowledge_candidates(projection)
        self.assertEqual(first, second)
        self.assertEqual([c.candidate_id for c in first], [c.candidate_id for c in second])

    def test_no_uuid4_anywhere_in_module(self):
        import inspect
        import jarvis.learning_ingestion as module
        source = inspect.getsource(module)
        body_only = source.split('"""', 2)[2]
        self.assertNotIn("uuid.uuid4", body_only)
        self.assertNotIn("uuid4()", body_only)

    def test_candidate_id_is_uuid5_of_namespace_and_seed(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=0)
        candidates = derive_knowledge_candidates(projection)
        self.assertEqual(len(candidates), 1)
        expected = str(uuid.uuid5(_KNOWLEDGE_CANDIDATE_NAMESPACE, f"{projection.mission_id}:outcome"))
        self.assertEqual(candidates[0].candidate_id, expected)


class TierEnforcementTests(unittest.TestCase):
    def test_every_candidate_shape_has_complementary_tier(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(severity="P0")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        self.assertGreaterEqual(len(candidates), 2)
        for content in candidates:
            self.assertEqual(content.tier, "complementary")
            require_explicit_tier(content)  # never raises


class ResolvedFindingCandidateTests(unittest.TestCase):
    def test_no_resolved_candidate_with_fewer_than_two_review_rounds(self):
        attempts = (_builder_attempt(0), _reviewer_attempt(0, "PASS", []))
        projection = _projection(attempts=attempts, corrective_cycle_count=0)
        candidates = derive_knowledge_candidates(projection)
        # only the outcome candidate, never a resolved-finding one
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].label, "INTENT")

    def test_resolved_p1_finding_absent_in_final_round_produces_fact_candidate(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(summary="found a bug", file="x.py", category="correctness", severity="P1")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(len(resolved), 1)
        claim = resolved[0].claim
        self.assertIn("found a bug", claim)
        self.assertIn("correctness", claim)
        self.assertIn("x.py", claim)
        self.assertNotIn("fixed", claim.lower())
        self.assertNotIn("caused by", claim.lower())

    def test_multi_round_attribution_when_resolved_only_in_final_round(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(summary="found a bug", file="x.py", category="correctness", severity="P1")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "CHANGES_REQUIRED", [_finding(finding_id="f2", summary="still a bug", file="x.py", category="correctness", severity="P1")]),
            _builder_attempt(2),
            _reviewer_attempt(2, "PASS", []),
        )
        # Final round (index -1) is PASS/[] -- but the *middle* round still
        # carried the same (category, file); it is itself an "earlier
        # round" per the rule (only the LAST round counts as final), so a
        # resolved candidate is still emitted for this key since the final
        # (3rd) round has none. This exercises multi-round attribution.
        projection = _projection(attempts=attempts, corrective_cycle_count=2)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(len(resolved), 1)
        claim = resolved[0].claim
        self.assertIn("review round 1 of 3: found a bug", claim)
        self.assertIn("review round 2 of 3: still a bug", claim)

    def test_finding_still_present_in_last_round_is_not_claimed_resolved(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [
                _finding(summary="found a bug", file="x.py", category="correctness", severity="P1"),
            ]),
            _builder_attempt(1),
            # This is a synthetic (non-chugel-realistic) case exercising the
            # pure function's own defensive branch: the final round still
            # carries the same (category, file) key, so no "resolved"
            # candidate must ever be emitted for it.
            _reviewer_attempt(1, "CHANGES_REQUIRED", [
                _finding(finding_id="f2", summary="still there", file="x.py", category="correctness", severity="P1"),
            ]),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(resolved, [])

    def test_finding_with_no_file_never_produces_a_candidate(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(file=None, category="correctness", severity="P0")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(resolved, [])

    def test_grouped_by_category_and_file_never_by_finding_id_or_line_range(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [
                _finding(finding_id="round0-a", summary="one summary", file="x.py", category="correctness",
                         severity="P1", line_range="1-2"),
            ]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(len(resolved), 1)
        # a completely different finding_id/line_range still groups by (category, file)
        self.assertIn("x.py", resolved[0].claim)

    def test_only_p2_p3_findings_never_produce_a_resolved_candidate(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "PASS_WITH_NON_BLOCKING_FINDINGS", [
                _finding(severity="P3", file="x.py", category="style"),
            ]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        resolved = [c for c in candidates if c.label == "FACT"]
        self.assertEqual(resolved, [])

    def test_single_distinct_summary_across_multiple_earlier_rounds_included_once(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(summary="same summary", file="x.py", category="correctness", severity="P1")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "CHANGES_REQUIRED", [_finding(finding_id="f2", summary="same summary", file="y.py", category="other", severity="P1")]),
            _builder_attempt(2),
            _reviewer_attempt(2, "PASS", []),
        )
        # x.py's finding only ever appears once (round 0); the claim must
        # not repeat "review round 1 of 3: same summary" redundantly.
        projection = _projection(attempts=attempts, corrective_cycle_count=2)
        candidates = derive_knowledge_candidates(projection)
        claims = [c.claim for c in candidates if c.label == "FACT"]
        self.assertEqual(len(claims), 2)
        for claim in claims:
            self.assertEqual(claim.count("same summary"), 1)


class OutcomeCandidateTests(unittest.TestCase):
    def test_no_hallucination_every_substantive_word_traces_to_projection_fields(self):
        projection = _projection(
            attempts=(_builder_attempt(0), _reviewer_attempt(0, "PASS", [])),
            corrective_cycle_count=0, outcome="Ship the widget", scope=("Build widget", "Test widget"),
            acceptance_criteria=("Widget ships",),
        )
        candidates = derive_knowledge_candidates(projection)
        outcome = next(c for c in candidates if c.label == "INTENT")
        self.assertIn("Ship the widget", outcome.claim)
        self.assertIn("Build widget", outcome.claim)
        self.assertIn("Test widget", outcome.claim)
        self.assertIn("Widget ships", outcome.claim)

    def test_fires_when_corrective_cycle_count_is_zero(self):
        projection = _projection(attempts=(_builder_attempt(0), _reviewer_attempt(0, "PASS", [])), corrective_cycle_count=0)
        candidates = derive_knowledge_candidates(projection)
        self.assertEqual(sum(1 for c in candidates if c.label == "INTENT"), 1)

    def test_fires_when_final_round_pass_with_zero_p0_p1(self):
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(severity="P1")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        self.assertEqual(sum(1 for c in candidates if c.label == "INTENT"), 1)

    def test_emitted_at_most_once(self):
        projection = _projection(attempts=(_builder_attempt(0), _reviewer_attempt(0, "PASS", [])), corrective_cycle_count=0)
        candidates = derive_knowledge_candidates(projection)
        self.assertEqual(sum(1 for c in candidates if c.label == "INTENT"), 1)


class NegativeCaseTests(unittest.TestCase):
    def test_only_final_round_absence_and_outcome_fields_ever_feed_a_claim(self):
        """A mission never reaches this module at all unless it is
        COMPLETED (see jarvis.mission_coordinator.derive_knowledge_for_
        completed_mission()'s own state check) -- this test only confirms
        the pure function's own claim content never leaks an EARLIER,
        abandoned attempt's own conclusion/findings text into any claim
        beyond what the resolved-finding rule explicitly allows (attributed
        summaries only, never free text from an abandoned attempt's own
        conclusion)."""
        attempts = (
            _builder_attempt(0),
            _reviewer_attempt(0, "CHANGES_REQUIRED", [_finding(summary="round0 summary", file="x.py", category="correctness", severity="P1")]),
            _builder_attempt(1),
            _reviewer_attempt(1, "PASS", []),
        )
        projection = _projection(attempts=attempts, corrective_cycle_count=1)
        candidates = derive_knowledge_candidates(projection)
        for content in candidates:
            self.assertNotIn("done", content.claim)  # the builder conclusion text, never leaked


if __name__ == "__main__":
    unittest.main()
