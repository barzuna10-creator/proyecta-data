"""jarvis/objective_query.py -- M7 (Program-Level Planning Depth). The
sole read-only seam jarvis.mission_supervisor uses to reach an Objective's
decomposition. Real FileJarvisStore against a temp directory throughout --
no mocking of jarvis.storage itself."""

import tempfile
import unittest
from pathlib import Path

from jarvis import objective_query
from jarvis.models import Objective, ObjectiveDecompositionEntry
from jarvis.objectives import build_objective_envelope
from jarvis.storage import FileJarvisStore

OBJECTIVE_ID = "223e4567-e89b-42d3-a456-426614174000"
DRAFT_A = "323e4567-e89b-42d3-a456-426614174001"
DRAFT_B = "323e4567-e89b-42d3-a456-426614174002"
NOW = "2026-08-30T20:00:00Z"


def _entry(draft_id, **overrides):
    values = {
        "draft_id": draft_id, "title": "Item", "rationale": "because",
        "outcome": "it works", "scope": ("do it",), "non_goals": (),
        "acceptance_criteria": ("done",), "open_questions": (), "depends_on": (),
    }
    values.update(overrides)
    return ObjectiveDecompositionEntry(**values)


def _objective(**overrides):
    values = {
        "schema_version": "1.0.0", "objective_id": OBJECTIVE_ID, "revision": 1,
        "created_at": NOW, "updated_at": NOW, "raw_intent": "improve things",
        "priority": "unset", "status": "decomposed",
        "decomposition": (_entry(DRAFT_A), _entry(DRAFT_B, depends_on=(DRAFT_A,))),
    }
    values.update(overrides)
    return Objective(**values)


class ObjectiveQueryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = FileJarvisStore(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_list_objective_ids_reflects_the_store(self):
        self.assertEqual((), objective_query.list_objective_ids(self.store))
        self.store.save_objective(build_objective_envelope(_objective()))
        self.assertEqual((OBJECTIVE_ID,), objective_query.list_objective_ids(self.store))

    def test_get_decomposition_entry_returns_the_matching_entry_with_its_depends_on(self):
        self.store.save_objective(build_objective_envelope(_objective()))
        entry = objective_query.get_decomposition_entry(self.store, OBJECTIVE_ID, DRAFT_B)
        self.assertEqual(DRAFT_B, entry.draft_id)
        self.assertEqual((DRAFT_A,), entry.depends_on)

    def test_unknown_objective_id_is_translated(self):
        with self.assertRaises(objective_query.ObjectiveQueryError) as ctx:
            objective_query.get_decomposition_entry(self.store, OBJECTIVE_ID, DRAFT_A)
        self.assertEqual("OBJECTIVE_NOT_FOUND", ctx.exception.code)

    def test_unknown_draft_id_within_a_real_objective_is_translated(self):
        self.store.save_objective(build_objective_envelope(_objective()))
        with self.assertRaises(objective_query.ObjectiveQueryError) as ctx:
            objective_query.get_decomposition_entry(self.store, OBJECTIVE_ID, "not-a-real-draft-id")
        self.assertEqual("DECOMPOSITION_ENTRY_NOT_FOUND", ctx.exception.code)

    def test_never_leaks_jarvis_storage_exception_types(self):
        """Exactly like jarvis.mission_query's own translation of
        orchestrator.chugel's exceptions -- a caller of this seam should
        never need to import jarvis.storage just to catch its errors."""
        from jarvis.storage import ObjectiveNotFound

        with self.assertRaises(objective_query.ObjectiveQueryError):
            try:
                objective_query.get_decomposition_entry(self.store, OBJECTIVE_ID, DRAFT_A)
            except ObjectiveNotFound:
                self.fail("ObjectiveNotFound leaked past jarvis.objective_query")
            except objective_query.ObjectiveQueryError:
                raise


if __name__ == "__main__":
    unittest.main()
