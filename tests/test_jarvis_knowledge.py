import dataclasses
import unittest

from jarvis.knowledge import (
    CANDIDATE_TRANSITIONS, ENTRY_TRANSITIONS, KnowledgeApplicability,
    KnowledgeCandidateContent, RepositoryBinding, build_candidate_envelope,
    require_candidate_transition, require_entry_transition, validate_repository_binding,
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


if __name__ == "__main__": unittest.main()
