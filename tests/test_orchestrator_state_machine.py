"""Pruebas para orchestrator/state_machine.py -- can_transition(), la única
función pública de este módulo (Zentra Autonomous Engineering V1, Level 2,
Increment #4).

Reutiliza los fixtures de tests/test_orchestrator_validator.py (mismo
patrón de import cruzado ya usado en este repo, ver
tests/test_compras.py::from tests.test_repositorio_proyectos import ...).
Ninguna prueba acá invoca un LLM, red, subprocess ni agente -- can_transition
es una función pura sobre un dict."""

import copy
import unittest

from orchestrator.state_machine import can_transition
from tests.test_orchestrator_validator import (
    _authorized_record,
    _built_and_reviewed_pass_record,
    _completed_record,
    _corrective_cycle_record,
    _minimal_intake_record,
)


class PruebaTransicionesLegales(unittest.TestCase):
    def test_intake_a_scope_awaiting_authorization_es_legal(self):
        record = _minimal_intake_record()
        check = can_transition(record, "SCOPE_AWAITING_AUTHORIZATION")
        self.assertTrue(check.allowed, check.reasons)

    def test_authorized_a_building_requiere_aislamiento_confirmado(self):
        record = _authorized_record()
        record["repository"]["isolation_confirmed"] = True
        check = can_transition(record, "BUILDING")
        self.assertTrue(check.allowed, check.reasons)

    def test_completed_a_rolled_back_es_legal(self):
        record = _completed_record()
        check = can_transition(record, "ROLLED_BACK")
        self.assertTrue(check.allowed, check.reasons)

    def test_ciclo_correctivo_changes_required_a_correcting_es_legal(self):
        # Toma el registro corregido y evalúa el paso intermedio real:
        # justo después del primer veredicto CHANGES_REQUIRED (índice 7:
        # REVIEWING -> CHANGES_REQUIRED), antes de que exista ningún
        # intento correctivo todavía.
        record = _corrective_cycle_record()
        record["state"] = "CHANGES_REQUIRED"
        record["state_history"] = record["state_history"][:8]
        record["builder_evidence"] = record["builder_evidence"][:1]
        record["reviewer_evidence"] = record["reviewer_evidence"][:1]
        record["corrective_cycle_count"] = 0
        check = can_transition(record, "CORRECTING")
        self.assertTrue(check.allowed, check.reasons)


class PruebaTransicionesIlegales(unittest.TestCase):
    def test_transicion_fuera_de_la_tabla_canonica(self):
        record = _minimal_intake_record()
        check = can_transition(record, "COMPLETED")
        self.assertFalse(check.allowed)
        self.assertTrue(any(r.code == "ILLEGAL_STATE_TRANSITION" for r in check.reasons), check.reasons)

    def test_building_sin_aislamiento_confirmado_se_deniega(self):
        record = _authorized_record()
        self.assertFalse(record["repository"]["isolation_confirmed"])
        check = can_transition(record, "BUILDING")
        self.assertFalse(check.allowed)
        self.assertTrue(any(r.code == "STATE_EVIDENCE_MISSING" for r in check.reasons), check.reasons)

    def test_merging_sin_merge_authorization_aprobada_se_deniega(self):
        record = _built_and_reviewed_pass_record()
        record["state"] = "MERGE_AWAITING_AUTHORIZATION"
        for from_s, to_s in [
            ("PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING"),
            ("PUBLISHING", "CI_PENDING"),
            ("CI_PENDING", "MERGE_AWAITING_AUTHORIZATION"),
        ]:
            record["state_history"].append({
                "from_state": from_s, "to_state": to_s,
                "at": "2026-08-19T12:56:00Z", "actor": "chugel", "reason": "advance",
            })
        record["human_gates"]["publish_authorization"] = {
            "status": "approved", "requested_at": "2026-08-19T12:50:00Z", "decided_at": "2026-08-19T12:51:00Z",
            "decided_by": "jose", "decision_ref": "audit:publish-1", "approved_for": {"commit_sha": "b" * 40},
        }
        record["publish"] = {"commit_sha": "b" * 40, "pushed_at": "2026-08-19T12:55:00Z", "pr_url": "https://x/1",
                              "pr_number": 1, "ci_runs": [{"run_id": "r1", "conclusion": "success", "checked_at": "2026-08-19T12:56:00Z"}]}
        # merge_authorization deliberately left not_requested -- this is the case under test.
        check = can_transition(record, "MERGING")
        self.assertFalse(check.allowed)
        self.assertTrue(any(r.code == "STATE_EVIDENCE_MISSING" for r in check.reasons), check.reasons)

    def test_registro_internamente_invalido_deniega_cualquier_transicion(self):
        record = _built_and_reviewed_pass_record()
        record["corrective_cycle_count"] = 1  # inconsistente: no hay attempt=1
        check = can_transition(record, "PUBLISHING")
        self.assertFalse(check.allowed)
        self.assertTrue(any(r.code == "BASE_RECORD_INVALID" for r in check.reasons), check.reasons)


class PruebaPureza(unittest.TestCase):
    def test_can_transition_nunca_muta_el_registro(self):
        record = _authorized_record()
        before = copy.deepcopy(record)
        can_transition(record, "BUILDING")
        self.assertEqual(record, before)

    def test_can_transition_nunca_muta_incluso_al_denegar(self):
        record = _minimal_intake_record()
        before = copy.deepcopy(record)
        can_transition(record, "COMPLETED")
        self.assertEqual(record, before)


if __name__ == "__main__":
    unittest.main()
