import dataclasses
import copy
import unittest

from jarvis.learning_projection import project_mission_learning
from orchestrator import chugel
from tests.test_orchestrator_chugel import (
    ChugelTestCase, _builder_evidence, _create_intake_mission, _gate_decision,
    _reviewer_evidence,
)


class LearningProjectionTests(ChugelTestCase):
    def test_real_canonical_record_projects_exact_allowlist_and_detaches(self):
        created = _create_intake_mission("canonical learning projection")
        mission_id = created["mission_id"]
        builder = _builder_evidence()
        builder["changed_files"] = [{"path": "jarvis/example.py", "reason": "test"}]
        chugel.transition(mission_id, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="ready")
        chugel.decide_gate(mission_id, "scope_authorization", _gate_decision(
            approved_for={"mission_definition_version": 1}
        ))
        chugel.record_repository_state(mission_id, {
            "worktree_path": "/tmp/canonical-learning",
            "branch": "codex/canonical-learning",
            "base_sha": "b" * 40,
            "isolation_confirmed": True,
        })
        chugel.transition(mission_id, "AUTHORIZED", actor="jose", reason="authorized")
        chugel.transition(mission_id, "BUILDING", actor="chugel", reason="build")
        chugel.record_builder_evidence(mission_id, builder)
        chugel.transition(mission_id, "VERIFYING", actor="chugel", reason="verified")
        chugel.transition(mission_id, "AWAITING_REVIEW", actor="chugel", reason="handoff")
        chugel.transition(mission_id, "REVIEWING", actor="emma", reason="review")
        chugel.record_reviewer_evidence(mission_id, _reviewer_evidence(
            verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P2", "summary": "bounded", "file": "jarvis/example.py", "line_range": "1", "category": "correctness"}]
        ))
        source = chugel.get_mission(mission_id)
        self.assertNotIn("definition", source["mission_definition_history"][0])
        self.assertNotIn("artifact_sha256", source["builder_evidence"][0])
        self.assertNotIn("artifact_sha256", source["reviewer_evidence"][0])

        projection = project_mission_learning(source)
        source["mission_definition_history"][0]["scope"][0] = "mutated"
        source["builder_evidence"][0]["artifact"]["commit_sha"] = "f" * 40
        self.assertEqual(projection.scope, ("do the thing",))
        self.assertEqual(projection.attempts[0].artifact.commit_sha, "a" * 40)
        self.assertEqual(projection.attempts[0].changed_files, ("jarvis/example.py",))
        self.assertEqual(projection.attempts[1].artifact.commit_sha, "a" * 40)
        self.assertEqual(projection.attempts[1].findings[0].finding_id, "f1")
        self.assertNotIn("secret", repr(projection))
        with self.assertRaises(dataclasses.FrozenInstanceError): projection.state = "x"

        old_flat_artifact = copy.deepcopy(source)
        old_flat_artifact["builder_evidence"][0].pop("artifact")
        old_flat_artifact["builder_evidence"][0]["artifact_sha256"] = "a" * 64
        with self.assertRaises(KeyError):
            project_mission_learning(old_flat_artifact)

    def test_malformed_previous_assumptions_fail_closed(self):
        created = _create_intake_mission("wrong shapes")
        wrong = chugel.get_mission(created["mission_id"])
        wrong["mission_definition_history"][0] = {"definition": wrong["mission_definition_history"][0]}
        with self.assertRaises(KeyError): project_mission_learning(wrong)


if __name__ == "__main__": unittest.main()
