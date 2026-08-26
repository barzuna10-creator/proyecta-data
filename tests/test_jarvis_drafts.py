import dataclasses
import json
import unittest

from jarvis.drafts import (
    DraftInvalid,
    build_draft_envelope,
    canonicalize_mission_draft,
    digest_mission_draft,
    revise_mission_draft,
    validate_mission_draft,
)
from jarvis.models import (
    DraftChanges,
    EvidenceSource,
    MissionDefinitionDraft,
    MissionDraft,
    RepositoryContext,
    ResearchEvidence,
    mission_draft_to_dict,
)


DRAFT_ID = "123e4567-e89b-42d3-a456-426614174000"
NOW = "2026-08-25T20:00:00Z"


def valid_draft(**overrides):
    values = {
        "schema_version": "1.0.0",
        "draft_id": DRAFT_ID,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "raw_intent": "Improve plan uploads",
        "mission_definition": MissionDefinitionDraft(
            outcome="Uploads fail clearly and recoverably",
            scope=("Classify failures",),
            non_goals=("Redesign the frontend",),
            acceptance_criteria=("Verified failures return structured errors",),
        ),
        "research_evidence": (
            ResearchEvidence(
                evidence_id="intent_1",
                claim="José wants plan uploads improved",
                label="INTENT",
                sources=(EvidenceSource(
                    kind="human_statement", locator="conversation-1", observed_at=NOW
                ),),
            ),
        ),
        "risks": ("Compatibility regression",),
        "open_questions": (),
        "repository_context": RepositoryContext("origin/main", "a" * 40),
    }
    values.update(overrides)
    return MissionDraft(**values)


class JarvisDraftTests(unittest.TestCase):
    def codes(self, result):
        return {error.code for error in result.errors}

    def test_minimal_valid_draft(self):
        self.assertTrue(validate_mission_draft(valid_draft()).valid)

    def test_authority_and_execution_fields_are_structurally_rejected(self):
        raw = mission_draft_to_dict(valid_draft())
        for field in ("authorized_by", "human_gates", "state", "mission_id", "execute"):
            candidate = dict(raw)
            candidate[field] = "forbidden"
            self.assertFalse(validate_mission_draft(candidate).valid, field)

    def test_untrusted_dict_receives_semantic_evidence_validation(self):
        raw = mission_draft_to_dict(valid_draft())
        raw["research_evidence"][0]["label"] = "INFERENCE"
        raw["research_evidence"][0]["based_on_evidence_ids"] = []
        raw["research_evidence"][0]["uncertainty_reason"] = None
        codes = self.codes(validate_mission_draft(raw))
        self.assertIn("INFERENCE_BASIS_REQUIRED", codes)
        self.assertIn("INFERENCE_UNCERTAINTY_REQUIRED", codes)

    def test_bool_revision_and_float_are_rejected(self):
        raw = mission_draft_to_dict(valid_draft())
        raw["revision"] = True
        self.assertIn("REVISION_BOOL_FORBIDDEN", self.codes(validate_mission_draft(raw)))
        raw = mission_draft_to_dict(valid_draft())
        raw["risk_score"] = 1.2
        self.assertIn("FLOAT_FORBIDDEN", self.codes(validate_mission_draft(raw)))

    def test_non_nfc_and_noncanonical_timestamp_are_rejected(self):
        draft = valid_draft(raw_intent="Cafe\u0301")
        self.assertIn("STRING_NOT_NFC", self.codes(validate_mission_draft(draft)))
        draft = valid_draft(updated_at="2026-08-25T20:00:00+00:00")
        self.assertIn("TIMESTAMP_NOT_CANONICAL_UTC", self.codes(validate_mission_draft(draft)))

    def test_canonicalization_is_compact_utf8_and_stable(self):
        draft = valid_draft()
        canonical = canonicalize_mission_draft(draft)
        self.assertFalse(canonical.endswith(b"\n"))
        self.assertNotIn(b'": ', canonical)
        self.assertNotIn(b', "', canonical)
        reversed_keys = dict(reversed(list(mission_draft_to_dict(draft).items())))
        normalized = json.loads(canonical)
        alternate = json.dumps(
            reversed_keys, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode()
        self.assertEqual(canonical, alternate)
        self.assertEqual(normalized["mission_definition"]["scope"], ["Classify failures"])

    def test_digest_has_fixed_golden_vector(self):
        self.assertEqual(
            digest_mission_draft(valid_draft()),
            "b2e7cf64f13bf9dd37e0fd9b35cc7db617aa0b9754e19d8ea513ee9613f51ad3",
        )

    def test_material_change_changes_digest(self):
        original = valid_draft()
        changed = dataclasses.replace(original, risks=("Different risk",))
        self.assertNotEqual(digest_mission_draft(original), digest_mission_draft(changed))

    def test_revision_is_immutable_and_advances_exactly_once(self):
        original = valid_draft()
        revised = revise_mission_draft(
            original,
            updated_at="2026-08-25T20:00:01Z",
            changes=DraftChanges(raw_intent="Improve reliable plan uploads"),
        )
        self.assertEqual(1, original.revision)
        self.assertEqual(2, revised.revision)
        self.assertEqual(original.created_at, revised.created_at)
        self.assertNotEqual(original.raw_intent, revised.raw_intent)

    def test_revision_timestamp_must_advance(self):
        with self.assertRaises(DraftInvalid):
            revise_mission_draft(valid_draft(), updated_at=NOW, changes=DraftChanges(risks=()))

    def test_invalid_draft_cannot_be_canonicalized(self):
        with self.assertRaises(DraftInvalid):
            build_draft_envelope(valid_draft(schema_version="wrong"))


if __name__ == "__main__":
    unittest.main()
