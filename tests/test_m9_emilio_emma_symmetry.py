"""M9 -- Read-Only Business Context Expansion, Correction 4/8 functional
half of acceptance test 12: a business fact that should influence a
mission's scope must be written textually into `mission_definition`
BEFORE orchestrator/agent_invocation.py runs, and both
build_emilio_invocation_request() and build_emma_invocation_request()
consume it identically -- never asymmetrically. (The structural AST half
of test 12 lives in tests/test_jarvis_foundation_boundaries.py.)"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel

_BUSINESS_FACT = "SENTINEL_BUSINESS_FACT: matching_failure_summary muestra 42% de rechazo en categoria Vidrios"


def _mission_definition_payload_with_business_fact():
    return {
        "outcome": f"reduce el rechazo de matching en Vidrios -- {_BUSINESS_FACT}",
        "scope": [_BUSINESS_FACT],
        "non_goals": [],
        "acceptance_criteria": ["it works"],
        "authorized_by": "jose",
        "authorized_at": "2026-08-19T12:00:00Z",
        "authorization_decision_ref": "ref-intake-1",
    }


def _artifact_commit():
    return {
        "mode": "commit", "commit_sha": "a" * 40,
        "patch_path": None, "patch_sha256": None, "patch_byte_size": None,
    }


def _builder_evidence(attempt=0):
    return {
        "attempt": attempt, "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": _artifact_commit(),
        "changed_files": [{"path": "x.py", "reason": "r"}],
        "checks": [{"command": "cmd", "working_directory": "/tmp", "exit_status": 0, "result": "ok"}],
        "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True,
            "no_remote_action": True, "no_production_access": True,
            "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": "ref", "conclusion": {"text": "done", "label": "FACT"},
        "invocation_id": "11111111-1111-4111-8111-111111111111",
        "provider": "codex", "provider_session_id": None, "provider_conversation_id": "thread-1",
    }


class PruebaSimetriaHechoDeNegocio(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def test_hecho_de_negocio_visible_identicamente_a_emilio_y_emma(self):
        record = chugel.create_mission("algo", _mission_definition_payload_with_business_fact())
        mid = record["mission_id"]

        req_emilio = ai.build_emilio_invocation_request(mid, 0, str(uuid.uuid4()))
        self.assertIn(_BUSINESS_FACT, json.dumps(req_emilio.task["mission_definition"]))

        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req_emma = ai.build_emma_invocation_request(mid, 0, str(uuid.uuid4()))
        self.assertIn(_BUSINESS_FACT, json.dumps(req_emma.task["mission_definition"]))

        # Consumido idénticamente -- el mismo objeto mission_definition,
        # nunca una copia asimétrica para uno u otro.
        self.assertEqual(req_emilio.task["mission_definition"], req_emma.task["mission_definition"])


if __name__ == "__main__":
    unittest.main()
