import unittest

from jarvis.evidence import validate_evidence_set, validate_research_evidence
from jarvis.models import EvidenceSource, ResearchEvidence


NOW = "2026-08-25T20:00:00Z"
SHA = "a" * 40


def source(kind="repository_file", commit_sha=SHA):
    return EvidenceSource(kind=kind, locator="example", observed_at=NOW, commit_sha=commit_sha)


class JarvisEvidenceTests(unittest.TestCase):
    def codes(self, errors):
        return {error.code for error in errors}

    def test_valid_fact_and_inputs_are_not_mutated(self):
        fact = ResearchEvidence("fact_1", "Observed", "FACT", (source(),))
        before = repr(fact)
        self.assertEqual((), validate_research_evidence(fact, known_evidence_ids={"fact_1"}))
        self.assertEqual(before, repr(fact))

    def test_fact_requires_source_and_no_uncertainty(self):
        item = ResearchEvidence("fact_1", "Claim", "FACT", (), (), "maybe")
        codes = self.codes(validate_research_evidence(item))
        self.assertIn("FACT_SOURCE_REQUIRED", codes)
        self.assertIn("FACT_UNCERTAINTY_FORBIDDEN", codes)

    def test_inference_requires_basis_and_uncertainty(self):
        item = ResearchEvidence("inference_1", "Conclusion", "INFERENCE")
        codes = self.codes(validate_research_evidence(item))
        self.assertIn("INFERENCE_BASIS_REQUIRED", codes)
        self.assertIn("INFERENCE_UNCERTAINTY_REQUIRED", codes)

    def test_missing_reference_and_self_reference_are_rejected(self):
        missing = ResearchEvidence(
            "inference_1", "Conclusion", "INFERENCE", (), ("missing",), "Not directly observed"
        )
        self.assertIn("EVIDENCE_REFERENCE_MISSING", self.codes(validate_research_evidence(missing)))
        self_ref = ResearchEvidence(
            "inference_1", "Conclusion", "INFERENCE", (), ("inference_1",), "Gap"
        )
        codes = self.codes(validate_research_evidence(self_ref, known_evidence_ids={"inference_1"}))
        self.assertIn("EVIDENCE_SELF_REFERENCE", codes)

    def test_assumption_requires_resolution_reason(self):
        item = ResearchEvidence("assumption_1", "Unknown", "ASSUMPTION")
        self.assertIn("ASSUMPTION_UNCERTAINTY_REQUIRED", self.codes(validate_research_evidence(item)))

    def test_intent_requires_human_statement(self):
        bad = ResearchEvidence("intent_1", "José wants it", "INTENT", (source(),))
        self.assertIn("INTENT_HUMAN_SOURCE_REQUIRED", self.codes(validate_research_evidence(bad)))
        good = ResearchEvidence(
            "intent_1", "José wants it", "INTENT", (source("human_statement", None),)
        )
        self.assertEqual((), validate_research_evidence(good, known_evidence_ids={"intent_1"}))

    def test_repository_source_requires_commit(self):
        item = ResearchEvidence("fact_1", "Observed", "FACT", (source(commit_sha=None),))
        self.assertIn("REPOSITORY_SOURCE_COMMIT_REQUIRED", self.codes(validate_research_evidence(item)))

    def test_observed_at_requires_real_utc_calendar_second(self):
        malformed = (
            "not-a-date",
            "2026-02-30T20:00:00Z",
            "2025-02-29T20:00:00Z",
            "2026-08-25T24:00:00Z",
            "2026-08-25T20:00:60Z",
            "2026-08-25T20:00:00+00:00",
            "2026-08-25T20:00:00.000Z",
            "2026-8-25T20:00:00Z",
            "２０２６-０８-２５T２０:００:００Z",
        )
        for observed_at in malformed:
            item = ResearchEvidence(
                "fact_1",
                "Observed",
                "FACT",
                (EvidenceSource("repository_file", "example", observed_at, SHA),),
            )
            self.assertIn(
                "OBSERVED_AT_INVALID",
                self.codes(validate_research_evidence(item)),
                observed_at,
            )

    def test_observed_at_accepts_valid_leap_day(self):
        item = ResearchEvidence(
            "fact_1",
            "Observed",
            "FACT",
            (EvidenceSource("repository_file", "example", "2024-02-29T23:59:59Z", SHA),),
        )
        self.assertNotIn("OBSERVED_AT_INVALID", self.codes(validate_research_evidence(item)))

    def test_duplicate_ids_and_cycles_are_rejected(self):
        duplicate = ResearchEvidence("fact_1", "Observed", "FACT", (source(),))
        self.assertIn("EVIDENCE_ID_DUPLICATE", self.codes(validate_evidence_set((duplicate, duplicate))))
        a = ResearchEvidence("a", "A", "INFERENCE", (), ("b",), "gap")
        b = ResearchEvidence("b", "B", "INFERENCE", (), ("a",), "gap")
        self.assertIn("EVIDENCE_DEPENDENCY_CYCLE", self.codes(validate_evidence_set((a, b))))


if __name__ == "__main__":
    unittest.main()
