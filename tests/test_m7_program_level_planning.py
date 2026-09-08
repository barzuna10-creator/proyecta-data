"""M7 (Program-Level Planning Depth) -- end-to-end acceptance tests
covering the 12 acceptance criteria from JARVIS_M7_DESIGN_V3.md's final
consolidated list, plus an explicit crash-recovery test.

Real orchestrator.chugel Mission Records and a real jarvis.storage.
FileJarvisStore throughout (both against temp directories) -- only
mission_coordinator.advance() is mocked, exactly like
tests/test_jarvis_mission_supervisor.py already does, since this module's
own dispatch behavior (not advance()'s state-machine internals) is what
M7 changes."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock as mock
import uuid
from pathlib import Path

import orchestrator.chugel as chugel
from jarvis import mission_coordinator
from jarvis.control_plane_server import _build_projection, _objective_decomposition_entries
from jarvis.mission_supervisor import MissionSupervisor
from jarvis.models import Objective, ObjectiveDecompositionEntry
from jarvis.objectives import ObjectiveInvalid, build_objective_envelope, revise_objective
from jarvis.models import ObjectiveChanges
from jarvis.storage import FileJarvisStore
from orchestrator.jarvis_conversation import DecompositionItemSuggestion
from orchestrator.validator import HUMAN_DECIDER
from jarvis import mission_write

_ADVANCE_KWARGS = dict(repository_root="/tmp/repo", branch="overnight/mission", pr_title="Mission")
_NOW = "2026-08-30T20:00:00Z"


def _mission_definition_payload():
    return {
        "outcome": "ship it", "scope": ["do it"], "non_goals": [],
        "acceptance_criteria": ["done"], "authorized_by": HUMAN_DECIDER,
        "authorized_at": "2026-08-19T12:00:00Z", "authorization_decision_ref": "ref-1",
    }


def _entry(draft_id, **overrides):
    values = {
        "draft_id": draft_id, "title": "Item", "rationale": "because",
        "outcome": "it works", "scope": ("do it",), "non_goals": (),
        "acceptance_criteria": ("done",), "open_questions": (), "depends_on": (),
    }
    values.update(overrides)
    return ObjectiveDecompositionEntry(**values)


def _fake_report():
    return mission_coordinator.CoordinatorReport(
        "GATE_REQUIRED", "SCOPE_AWAITING_AUTHORIZATION", "scope_authorization",
    )


def _decision(decided_by="jose"):
    return {
        "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": decided_by,
        "decision_ref": "ref-1", "approved_for": {"head_sha": "a" * 40},
    }


def _scope_decision(decided_by="jose"):
    return {
        "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": decided_by,
        "decision_ref": "ref-scope-1", "approved_for": {"mission_definition_version": 1},
    }


class M7TestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self.store = FileJarvisStore(Path(self._tmpdir.name) / "jarvis")

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _supervisor(self, max_concurrency=1):
        return MissionSupervisor(
            adapters={}, advance_kwargs=dict(_ADVANCE_KWARGS),
            objective_store=self.store, max_concurrency=max_concurrency,
        )

    def _create_mission(self, mission_id=None, *, objective_id=None, draft_id=None):
        mission_id = mission_id or str(uuid.uuid4())
        origin = None
        if objective_id is not None or draft_id is not None:
            origin = {"objective_id": objective_id, "draft_id": draft_id}
        return chugel.create_mission(
            "intent", _mission_definition_payload(), mission_id=mission_id, origin=origin,
        )["mission_id"]

    def _save_objective(self, objective_id, decomposition, **overrides):
        values = dict(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=_NOW, updated_at=_NOW, raw_intent="improve things",
            priority="unset", status="decomposed", decomposition=decomposition,
        )
        values.update(overrides)
        objective = Objective(**values)
        self.store.save_objective(build_objective_envelope(objective))
        return objective

    def _advance_to_merged(self, mission_id):
        """Same BLOCKED-shortcut sequence tests/test_jarvis_control_plane_server.py's
        own _mission_at_merged() helper already uses -- (BLOCKED, X) is a
        legal edge for every X per orchestrator/validator.py's TRANSITIONS
        table, so this reaches MERGED without needing real builder/reviewer
        evidence for every intermediate state."""
        chugel.transition(mission_id, "BLOCKED", actor="chugel", reason="test setup")
        chugel.transition(mission_id, "PUBLISHING", actor="chugel", reason="test setup")
        chugel.transition(mission_id, "CI_PENDING", actor="chugel", reason="test setup")
        chugel.transition(mission_id, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
        chugel.record_publish_commit(mission_id, "a" * 40)
        chugel.decide_gate(mission_id, "merge_authorization", _decision())
        chugel.transition(mission_id, "MERGING", actor="chugel", reason="test setup")
        chugel.record_merge_commit(mission_id, "b" * 40)
        chugel.transition(mission_id, "MERGED", actor="chugel", reason="test setup")

    def _advance_to_failed(self, mission_id):
        # (BLOCKED, FAILED) is itself a legal edge in orchestrator/
        # validator.py's own TRANSITIONS table -- no need to pass through
        # BUILDING (which would additionally require real builder evidence
        # to satisfy evidence_errors_for_state()).
        chugel.transition(mission_id, "BLOCKED", actor="chugel", reason="test setup")
        chugel.transition(mission_id, "FAILED", actor="chugel", reason="unrecoverable")

    def _drain(self, supervisor):
        with mock.patch("jarvis.mission_supervisor.mission_coordinator.advance") as advance:
            advance.return_value = _fake_report()
            supervisor._drain_pass()
        return {call.args[0] for call in advance.call_args_list}


# --- Acceptance test 1 -----------------------------------------------------

class RealDependencyGatingTests(M7TestCase):
    def test_dependent_mission_excluded_until_prerequisite_merged_then_dispatched_next_cycle(self):
        objective_id = str(uuid.uuid4())
        draft_a, draft_b = str(uuid.uuid4()), str(uuid.uuid4())
        self._save_objective(objective_id, (_entry(draft_a), _entry(draft_b, depends_on=(draft_a,))))
        mission_a = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        mission_b = self._create_mission(objective_id=objective_id, draft_id=draft_b)
        supervisor = self._supervisor()

        submitted = self._drain(supervisor)
        self.assertIn(mission_a, submitted)
        self.assertNotIn(mission_b, submitted)

        self._advance_to_merged(mission_a)
        submitted = self._drain(supervisor)
        self.assertIn(mission_b, submitted)


# --- Acceptance test 2 -----------------------------------------------------

class CycleRejectionTests(M7TestCase):
    def test_a_two_item_cycle_by_ordinal_index_is_rejected_before_persisting(self):
        objective_id = str(uuid.uuid4())
        items = (
            DecompositionItemSuggestion(
                title="A", outcome="a-outcome", scope=("s",), acceptance_criteria=("c",),
                depends_on_index=(1,),
            ),
            DecompositionItemSuggestion(
                title="B", outcome="b-outcome", scope=("s",), acceptance_criteria=("c",),
                depends_on_index=(0,),
            ),
        )
        entries = _objective_decomposition_entries(objective_id, items)
        objective = Objective(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=_NOW, updated_at=_NOW, raw_intent="x",
            priority="unset", status="decomposed", decomposition=entries,
        )
        with self.assertRaises(ObjectiveInvalid) as ctx:
            build_objective_envelope(objective)
        self.assertIn("DECOMPOSITION_DEPENDENCY_CYCLE", {e.code for e in ctx.exception.errors})
        # Never silently persisted.
        self.assertEqual((), self.store.list_objective_ids())


# --- Acceptance test 3 -----------------------------------------------------

class FailedDependencyTests(M7TestCase):
    def test_failed_prerequisite_suppresses_auto_dispatch_but_not_manual_authorization(self):
        objective_id = str(uuid.uuid4())
        draft_a, draft_b = str(uuid.uuid4()), str(uuid.uuid4())
        self._save_objective(objective_id, (_entry(draft_a), _entry(draft_b, depends_on=(draft_a,))))
        mission_a = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        mission_b = self._create_mission(objective_id=objective_id, draft_id=draft_b)
        self._advance_to_failed(mission_a)

        supervisor = self._supervisor()
        submitted = self._drain(supervisor)
        self.assertNotIn(mission_b, submitted)

        projection = _build_projection(self.store)
        [objective_projection] = [o for o in projection["objectives"] if o["id"] == objective_id]
        [entry_b] = [e for e in objective_projection["decomposition"] if e["draftId"] == draft_b]
        self.assertEqual("failed", entry_b["dependencyStatus"])

        # M7 never blocks human scope authorization -- only the
        # supervisor's own automatic dispatch. B is still eligible for a
        # human to authorize scope by hand.
        chugel.transition(mission_b, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
        record = mission_write.authorize_scope(mission_b, _scope_decision())
        self.assertEqual("approved", record["human_gates"]["scope_authorization"]["status"])


# --- Acceptance test 4 -----------------------------------------------------

class ConcurrencyPreservedTests(M7TestCase):
    def test_two_independent_missions_in_the_same_objective_dispatch_together(self):
        objective_id = str(uuid.uuid4())
        draft_a, draft_b = str(uuid.uuid4()), str(uuid.uuid4())
        self._save_objective(objective_id, (_entry(draft_a), _entry(draft_b)))  # no depends_on either way
        mission_a = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        mission_b = self._create_mission(objective_id=objective_id, draft_id=draft_b)
        supervisor = self._supervisor(max_concurrency=2)
        submitted = self._drain(supervisor)
        self.assertEqual({mission_a, mission_b}, submitted)


# --- Acceptance test 5 -----------------------------------------------------

class AuthorityRegressionTests(M7TestCase):
    def test_scope_gate_still_requires_jose_regardless_of_origin(self):
        objective_id = str(uuid.uuid4())
        draft_a = str(uuid.uuid4())
        mission_id = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        chugel.transition(mission_id, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
        with self.assertRaises(mission_write.MissionWriteError):
            mission_write.authorize_scope(mission_id, _scope_decision(decided_by="not-jose"))
        # A manually-created mission (origin.objective_id is None) is held
        # to the exact same standard -- no divergence in behavior.
        manual_mission_id = self._create_mission()
        chugel.transition(manual_mission_id, "SCOPE_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
        with self.assertRaises(mission_write.MissionWriteError):
            mission_write.authorize_scope(manual_mission_id, _scope_decision(decided_by="not-jose"))


# --- Acceptance test 6 -----------------------------------------------------

class OriginInitializationTests(M7TestCase):
    def test_origin_is_fully_populated_from_intake_with_and_without_an_objective(self):
        objective_id = str(uuid.uuid4())
        draft_id = str(uuid.uuid4())
        with_objective = chugel.create_mission(
            "intent", _mission_definition_payload(),
            origin={"objective_id": objective_id, "draft_id": draft_id},
        )
        self.assertEqual("INTAKE", with_objective["state"])
        self.assertEqual({"objective_id": objective_id, "draft_id": draft_id}, with_objective["origin"])

        without_objective = chugel.create_mission("intent", _mission_definition_payload())
        self.assertEqual("INTAKE", without_objective["state"])
        self.assertIsNone(without_objective["origin"]["objective_id"])
        # No real draft supplied -- falls back to the mission's own id,
        # never null (schema requires a real draft_id string).
        self.assertEqual(without_objective["mission_id"], without_objective["origin"]["draft_id"])


# --- Acceptance test 7 -----------------------------------------------------

class CrossDecompositionReferenceRejectedTests(M7TestCase):
    def test_out_of_range_ordinal_index_is_rejected_like_a_dangling_reference(self):
        objective_id = str(uuid.uuid4())
        items = (
            DecompositionItemSuggestion(
                title="A", outcome="a", scope=("s",), acceptance_criteria=("c",),
            ),
            DecompositionItemSuggestion(
                title="B", outcome="b", scope=("s",), acceptance_criteria=("c",),
                depends_on_index=(7,),  # out of range -- only indices 0/1 exist
            ),
        )
        entries = _objective_decomposition_entries(objective_id, items)
        objective = Objective(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=_NOW, updated_at=_NOW, raw_intent="x",
            priority="unset", status="decomposed", decomposition=entries,
        )
        with self.assertRaises(ObjectiveInvalid) as ctx:
            build_objective_envelope(objective)
        self.assertIn("DECOMPOSITION_DEPENDENCY_DANGLING", {e.code for e in ctx.exception.errors})

    def test_a_foreign_draft_id_from_outside_the_decomposition_is_rejected(self):
        objective_id = str(uuid.uuid4())
        foreign_draft_id = str(uuid.uuid4())
        entries = (
            _entry(str(uuid.uuid4())),
            _entry(str(uuid.uuid4()), depends_on=(foreign_draft_id,)),
        )
        objective = Objective(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=_NOW, updated_at=_NOW, raw_intent="x",
            priority="unset", status="decomposed", decomposition=entries,
        )
        with self.assertRaises(ObjectiveInvalid) as ctx:
            build_objective_envelope(objective)
        self.assertIn("DECOMPOSITION_DEPENDENCY_DANGLING", {e.code for e in ctx.exception.errors})


# --- Acceptance test 8 -----------------------------------------------------

class IndexToDraftIdTranslationTests(M7TestCase):
    def test_ordinal_index_translates_to_the_real_draft_id_end_to_end(self):
        objective_id = str(uuid.uuid4())
        items = (
            DecompositionItemSuggestion(title="A", outcome="a", scope=("s",), acceptance_criteria=("c",)),
            DecompositionItemSuggestion(
                title="B", outcome="b", scope=("s",), acceptance_criteria=("c",), depends_on_index=(0,),
            ),
        )
        entries = _objective_decomposition_entries(objective_id, items)
        self.assertEqual((), entries[0].depends_on)
        self.assertEqual((entries[0].draft_id,), entries[1].depends_on)

        objective = Objective(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=_NOW, updated_at=_NOW, raw_intent="x",
            priority="unset", status="decomposed", decomposition=entries,
        )
        envelope = build_objective_envelope(objective)  # never raises -- valid, acyclic
        self.store.save_objective(envelope)
        persisted = self.store.get_latest_objective(objective_id).objective
        self.assertEqual((persisted.decomposition[0].draft_id,), persisted.decomposition[1].depends_on)


# --- Acceptance test 9 -----------------------------------------------------

class PerCandidateIsolationTests(M7TestCase):
    def test_a_corrupt_objective_for_one_candidate_never_blocks_the_rest_of_the_pass(self):
        # Candidate 1: a perfectly healthy, no-objective mission.
        healthy_mission = self._create_mission()
        # Candidate 2: claims an objective_id that was never actually
        # persisted -- objective_query.get_decomposition_entry() will
        # raise ObjectiveQueryError("OBJECTIVE_NOT_FOUND") for it.
        broken_objective_id = str(uuid.uuid4())
        broken_draft_id = str(uuid.uuid4())
        broken_mission = self._create_mission(objective_id=broken_objective_id, draft_id=broken_draft_id)

        supervisor = self._supervisor()
        with self.assertLogs("jarvis.mission_supervisor", level="ERROR") as logs:
            submitted = self._drain(supervisor)
        self.assertIn(healthy_mission, submitted)
        self.assertNotIn(broken_mission, submitted)
        joined = "\n".join(logs.output)
        self.assertIn(broken_mission, joined)
        self.assertIn(broken_objective_id, joined)


# --- Acceptance test 10 -----------------------------------------------------

class TransitiveFailureTests(M7TestCase):
    def test_chain_of_three_both_downstream_entries_are_failed_with_the_correct_root_cause(self):
        objective_id = str(uuid.uuid4())
        draft_a, draft_b, draft_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        self._save_objective(objective_id, (
            _entry(draft_a),
            _entry(draft_b, depends_on=(draft_a,)),
            _entry(draft_c, depends_on=(draft_b,)),
        ))
        mission_a = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        # B and C are never even authorized into real missions here --
        # transitive failure must still be visible in the projection from
        # the decomposition entries alone (dependencyStatus is computed at
        # serve time, never requires every entry to already be a mission).
        self._advance_to_failed(mission_a)

        projection = _build_projection(self.store)
        [objective_projection] = [o for o in projection["objectives"] if o["id"] == objective_id]
        by_draft = {e["draftId"]: e for e in objective_projection["decomposition"]}
        self.assertEqual("failed", by_draft[draft_b]["dependencyStatus"])
        self.assertEqual("failed", by_draft[draft_c]["dependencyStatus"])
        self.assertEqual(draft_a, by_draft[draft_c]["failedRootCause"])
        self.assertEqual(draft_a, by_draft[draft_b]["failedRootCause"])


# --- Acceptance test 12 (11 is the full hermetic suite itself) -------------

class SharedLayerAcyclicityTests(M7TestCase):
    def test_revise_objective_rejects_a_cycle_the_same_way_decomposition_does(self):
        objective_id = str(uuid.uuid4())
        draft_a, draft_b = str(uuid.uuid4()), str(uuid.uuid4())
        objective = self._save_objective(objective_id, (_entry(draft_a), _entry(draft_b)))
        cyclic_decomposition = (
            _entry(draft_a, depends_on=(draft_b,)),
            _entry(draft_b, depends_on=(draft_a,)),
        )
        with self.assertRaises(ObjectiveInvalid) as ctx:
            revise_objective(
                objective, updated_at="2026-08-30T20:00:01Z",
                changes=ObjectiveChanges(decomposition=cyclic_decomposition),
            )
        self.assertIn("DECOMPOSITION_DEPENDENCY_CYCLE", {e.code for e in ctx.exception.errors})


# --- Explicit crash-recovery test -------------------------------------------

class CrashRecoveryTests(M7TestCase):
    def test_dependency_resolution_is_recomputed_fresh_with_no_durable_pending_marker(self):
        """No in-memory state on any one MissionSupervisor instance drives
        the answer -- a brand-new supervisor instance (simulating a fresh
        process after a crash, with an empty _stalled/_inflight set) must
        compute the exact same dependency-gated result purely from
        Chugel's and jarvis.storage's own already-durable state."""
        objective_id = str(uuid.uuid4())
        draft_a, draft_b = str(uuid.uuid4()), str(uuid.uuid4())
        self._save_objective(objective_id, (_entry(draft_a), _entry(draft_b, depends_on=(draft_a,))))
        mission_a = self._create_mission(objective_id=objective_id, draft_id=draft_a)
        mission_b = self._create_mission(objective_id=objective_id, draft_id=draft_b)

        first_process_supervisor = self._supervisor()
        submitted = self._drain(first_process_supervisor)
        self.assertIn(mission_a, submitted)
        self.assertNotIn(mission_b, submitted)

        self._advance_to_merged(mission_a)

        # A brand new instance -- no shared memory with the one above.
        second_process_supervisor = self._supervisor()
        submitted = self._drain(second_process_supervisor)
        self.assertIn(mission_b, submitted)


if __name__ == "__main__":
    unittest.main()
