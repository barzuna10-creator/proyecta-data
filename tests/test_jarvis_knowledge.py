import dataclasses
import unittest

from jarvis.knowledge import (
    CANDIDATE_TRANSITIONS, ENTRY_TRANSITIONS, KnowledgeApplicability,
    KnowledgeCandidateContent, RepositoryBinding, build_candidate_envelope,
    candidate_content_from_dict, candidate_content_to_dict,
    require_candidate_transition, require_entry_transition, require_explicit_tier,
    validate_repository_binding,
)
from jarvis.models import EvidenceSource, ResearchEvidence

CID = "123e4567-e89b-42d3-a456-426614174000"
EVIDENCE = ResearchEvidence("fact_1", "Observed fact", "FACT", (EvidenceSource("mission_record", "mission:1", "2026-08-26T00:00:00Z", None, "a" * 64),))


def candidate(**changes):
    base = KnowledgeCandidateContent("1.0", CID, 1, "2026-08-26T00:00:00Z", None, None, None, "active", "A claim", "FACT", KnowledgeApplicability(("jarvis",)), None, (EVIDENCE,))
    return dataclasses.replace(base, **changes)


class KnowledgeModelTests(unittest.TestCase):
    def test_digest_binds_expected_target_state_and_content(self):
        first = build_candidate_envelope(candidate())
        changed = build_candidate_envelope(candidate(claim="Different"))
        self.assertNotEqual(first.content_digest, changed.content_digest)

    def test_repository_ref_complete_rejection_battery(self):
        valid = ("refs/heads/a", "refs/heads/release/v1", "refs/remotes/origin/main-1")
        for ref in valid:
            self.assertEqual(validate_repository_binding(RepositoryBinding(ref, "a" * 40)), ())
        invalid = ("", "main", "HEAD", "origin/main", "a" * 40, "refs/tags/v1", "refs/heads/-x", "refs/heads/.x", "refs/heads/a..b", "refs/heads/release.lock", "refs/heads/a//b", "refs/heads/a@{1}", "refs/heads/a b", "refs/heads/a\u00a0b", "refs/heads/a~1", "refs/heads/a^", "refs/heads/a:b", "refs/heads/a?", "refs/heads/a*", "refs/heads/a[", "refs/heads/a\\b", "refs/heads/" + "a" * 65)
        for ref in invalid:
            with self.subTest(ref=ref):
                self.assertTrue(validate_repository_binding(RepositoryBinding(ref, "a" * 40)))
        self.assertTrue(validate_repository_binding(RepositoryBinding("refs/heads/a", "A" * 40)))

    def test_target_binding_is_structural(self):
        with self.assertRaisesRegex(ValueError, "KNOWLEDGE_TARGET_BINDING_INVALID"):
            build_candidate_envelope(candidate(expected_current_status="active"))

    def test_transition_tables_are_exhaustive_and_terminal(self):
        for edge in CANDIDATE_TRANSITIONS:
            require_candidate_transition(*edge, label="FACT")
        for terminal in ("accepted", "rejected", "withdrawn"):
            for target in ("draft", "accepted", "withdrawn"):
                with self.assertRaises(ValueError):
                    require_candidate_transition(terminal, target, label="FACT")
        for edge in ENTRY_TRANSITIONS:
            require_entry_transition(*edge)
        for terminal in ("superseded", "retired"):
            for target in ("active", "stale", "retired"):
                with self.assertRaises(ValueError):
                    require_entry_transition(terminal, target)

    def test_inference_and_assumption_cannot_be_accepted(self):
        for label in ("INFERENCE", "ASSUMPTION"):
            with self.assertRaisesRegex(ValueError, "NOT_PROMOTABLE"):
                require_candidate_transition("awaiting_human_authorization", "accepted", label=label)


class EvidenceTierBackwardCompatibilityTests(unittest.TestCase):
    """Regression coverage for the tier field's backward-compatibility
    requirement: introducing it must never make previously-valid content
    fail to load, and absence must never be silently upgraded to a
    specific tier."""

    def test_default_tier_is_none_not_a_guessed_classification(self):
        self.assertIsNone(candidate().tier)

    def test_a_legacy_dict_with_no_tier_key_at_all_still_loads(self):
        # Simulates content persisted before this field existed -- not a
        # dict with tier: null, but one where the key is entirely absent,
        # exactly what candidate_content_to_dict() produced pre-migration.
        legacy = candidate_content_to_dict(candidate())
        self.assertIn("tier", legacy)  # current serialization always includes it...
        del legacy["tier"]  # ...so this line manufactures the pre-migration shape.
        loaded = candidate_content_from_dict(legacy)
        self.assertIsNone(loaded.tier)

    def test_legacy_content_without_tier_still_builds_a_valid_envelope(self):
        # The schema must not require "tier" -- a historical candidate
        # missing it must still pass validate_candidate_content() (called
        # by build_candidate_envelope()), not become KNOWLEDGE_CORRUPT.
        legacy = candidate_content_to_dict(candidate())
        del legacy["tier"]
        loaded = candidate_content_from_dict(legacy)
        envelope = build_candidate_envelope(loaded)  # must not raise
        self.assertIsNone(envelope.content.tier)

    def test_explicit_tier_round_trips_exactly(self):
        for tier in ("canonical", "complementary"):
            with self.subTest(tier=tier):
                loaded = candidate_content_from_dict(candidate_content_to_dict(candidate(tier=tier)))
                self.assertEqual(tier, loaded.tier)

    def test_require_explicit_tier_rejects_none(self):
        with self.assertRaisesRegex(ValueError, "KNOWLEDGE_TIER_REQUIRED"):
            require_explicit_tier(candidate())  # tier defaults to None

    def test_require_explicit_tier_accepts_canonical_and_complementary(self):
        for tier in ("canonical", "complementary"):
            require_explicit_tier(candidate(tier=tier))  # must not raise


if __name__ == "__main__": unittest.main()
