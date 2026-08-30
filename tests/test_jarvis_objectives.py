"""jarvis/objectives.py -- Jarvis God Mode M1. Deliberate mirror of
tests/test_jarvis_drafts.py's own structure and rigor."""

import unittest

from jarvis.objectives import (
    ObjectiveInvalid,
    build_objective_envelope,
    canonicalize_objective,
    digest_objective,
    revise_objective,
    validate_objective,
)
from jarvis.models import (
    Objective,
    ObjectiveChanges,
    ObjectiveDecompositionEntry,
    objective_to_dict,
)

OBJECTIVE_ID = "223e4567-e89b-42d3-a456-426614174000"
DRAFT_ID_1 = "323e4567-e89b-42d3-a456-426614174001"
DRAFT_ID_2 = "323e4567-e89b-42d3-a456-426614174002"
NOW = "2026-08-30T20:00:00Z"


def decomposition_entry(**overrides):
    values = {
        "draft_id": DRAFT_ID_1, "title": "Improve upload error handling",
        "rationale": "Uploads fail unrecoverably today",
        "outcome": "Uploads fail clearly and recoverably",
        "scope": ("Classify failures",), "non_goals": (),
        "acceptance_criteria": ("Verified failures return structured errors",),
        "open_questions": (),
    }
    values.update(overrides)
    return ObjectiveDecompositionEntry(**values)


def proposed_objective(**overrides):
    values = {
        "schema_version": "1.0.0", "objective_id": OBJECTIVE_ID, "revision": 1,
        "created_at": NOW, "updated_at": NOW,
        "raw_intent": "Improve test coverage of the upload module",
        "priority": "unset", "status": "proposed", "decomposition": (),
    }
    values.update(overrides)
    return Objective(**values)


def decomposed_objective(**overrides):
    values = {
        "status": "decomposed",
        "decomposition": (
            decomposition_entry(),
            decomposition_entry(draft_id=DRAFT_ID_2, title="Add regression tests for uploads"),
        ),
    }
    values.update(overrides)
    return proposed_objective(**values)


class JarvisObjectiveTests(unittest.TestCase):
    def codes(self, result):
        return {error.code for error in result.errors}

    def test_minimal_valid_proposed_objective(self):
        self.assertTrue(validate_objective(proposed_objective()).valid)

    def test_minimal_valid_decomposed_objective(self):
        self.assertTrue(validate_objective(decomposed_objective()).valid)

    def test_proposed_status_forbids_any_decomposition_entries(self):
        raw = objective_to_dict(proposed_objective())
        raw["decomposition"] = [objective_to_dict(decomposed_objective())["decomposition"][0]]
        self.assertFalse(validate_objective(raw).valid)

    def test_decomposed_status_requires_at_least_two_entries(self):
        raw = objective_to_dict(decomposed_objective())
        raw["decomposition"] = raw["decomposition"][:1]
        self.assertFalse(validate_objective(raw).valid)

    def test_decomposed_status_forbids_more_than_four_entries(self):
        raw = objective_to_dict(decomposed_objective())
        extra = dict(raw["decomposition"][0])
        raw["decomposition"] = [
            {**extra, "draft_id": f"323e4567-e89b-42d3-a456-42661417{4000 + i:04d}"} for i in range(5)
        ]
        self.assertFalse(validate_objective(raw).valid)

    def test_duplicate_draft_id_across_entries_is_rejected(self):
        raw = objective_to_dict(decomposed_objective())
        raw["decomposition"][1]["draft_id"] = raw["decomposition"][0]["draft_id"]
        result = validate_objective(raw)
        self.assertFalse(result.valid)
        self.assertIn("DUPLICATE_DECOMPOSITION_DRAFT_ID", self.codes(result))

    def test_authority_and_execution_fields_are_structurally_rejected(self):
        raw = objective_to_dict(proposed_objective())
        for field in ("authorized_by", "human_gates", "state", "mission_id", "decided_by"):
            candidate = dict(raw)
            candidate[field] = "forbidden"
            self.assertFalse(validate_objective(candidate).valid, field)

    def test_closed_status_is_a_structurally_valid_enum_value(self):
        # M1 never WRITES "closed" (decision #3: deferred), but the schema
        # must already accept it so a later milestone's write path never
        # has to migrate existing data.
        self.assertTrue(validate_objective(proposed_objective(status="closed")).valid)

    def test_invalid_priority_is_rejected(self):
        raw = objective_to_dict(proposed_objective())
        raw["priority"] = "urgent"
        self.assertFalse(validate_objective(raw).valid)

    def test_digest_is_stable_and_order_sensitive(self):
        a = digest_objective(proposed_objective())
        b = digest_objective(proposed_objective())
        self.assertEqual(a, b)
        c = digest_objective(proposed_objective(raw_intent="Something else entirely"))
        self.assertNotEqual(a, c)

    def test_build_objective_envelope_round_trips(self):
        objective = decomposed_objective()
        envelope = build_objective_envelope(objective)
        self.assertEqual("sha256", envelope.digest_algorithm)
        self.assertEqual(64, len(envelope.digest))
        self.assertEqual(objective, envelope.objective)

    def test_canonicalize_invalid_objective_raises(self):
        with self.assertRaises(ObjectiveInvalid):
            canonicalize_objective(proposed_objective(schema_version="9.9.9"))

    def test_revise_objective_advances_revision_and_updated_at(self):
        objective = proposed_objective()
        revised = revise_objective(
            objective, updated_at="2026-08-30T20:00:01Z",
            changes=ObjectiveChanges(status="decomposed", decomposition=decomposed_objective().decomposition),
        )
        self.assertEqual(2, revised.revision)
        self.assertEqual("decomposed", revised.status)
        self.assertEqual(2, len(revised.decomposition))
        self.assertEqual("proposed", objective.status)  # original untouched -- frozen dataclass

    def test_revise_objective_rejects_non_advancing_updated_at(self):
        objective = proposed_objective()
        with self.assertRaises(ObjectiveInvalid):
            revise_objective(
                objective, updated_at=objective.updated_at,
                changes=ObjectiveChanges(status="closed"),
            )

    def test_revise_objective_rejects_a_result_that_fails_validation(self):
        objective = proposed_objective()
        with self.assertRaises(ObjectiveInvalid):
            revise_objective(
                objective, updated_at="2026-08-30T20:00:01Z",
                # decomposed with zero entries -- structurally invalid.
                changes=ObjectiveChanges(status="decomposed"),
            )


if __name__ == "__main__":
    unittest.main()
