"""Pruebas para orchestrator/agent_invocation.py (Zentra Autonomous
Engineering V1, Level 2, Increment #12 -- capa base de invocación de
agentes per orchestrator/AGENT_INVOCATION_V1.md, ya revisado
independientemente por Emma).

Todas las pruebas usan un directorio temporal aislado como
orchestrator.chugel._MISSIONS_DIR -- ninguna prueba escribe jamás en el
directorio real orchestrator/missions/. Ninguna prueba invoca un LLM, red
o subprocess, y ningún AgentInvoker concreto existe en este incremento --
los resultados se construyen a mano como AgentInvocationResult, simulando
lo que un futuro adapter produciría."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel


# --- fixtures: misión real vía chugel.py, sin atajos ------------------

def _mission_definition_payload(authorized_by="jose"):
    return {
        "outcome": "ship the thing",
        "scope": ["do the thing"],
        "non_goals": [],
        "acceptance_criteria": ["it works"],
        "authorized_by": authorized_by,
        "authorized_at": "2026-08-19T12:00:00Z",
        "authorization_decision_ref": "ref-intake-1",
    }


def _create_intake_mission(intent_text="algo"):
    return chugel.create_mission(intent_text, _mission_definition_payload())


def _artifact_commit(sha=None):
    return {
        "mode": "commit",
        "commit_sha": sha or ("a" * 40),
        "patch_path": None,
        "patch_sha256": None,
        "patch_byte_size": None,
    }


def _builder_evidence(attempt=0, artifact=None, conclusion_text="SENTINEL_CONCLUSION"):
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": artifact or _artifact_commit(),
        "changed_files": [{"path": "SENTINEL_CHANGED_FILE.py", "reason": "SENTINEL_REASON"}],
        "checks": [{"command": "SENTINEL_CHECK_COMMAND", "working_directory": "/tmp",
                     "exit_status": 0, "result": "SENTINEL_CHECK_RESULT"}],
        "skipped_checks": [],
        "risks": ["SENTINEL_RISK"],
        "assumptions": [{"text": "SENTINEL_ASSUMPTION", "label": "ASSUMPTION"}],
        "rollback_notes": "SENTINEL_ROLLBACK_NOTES",
        "safety_confirmation": {
            "no_existing_work_altered": True,
            "no_main_change": True,
            "no_remote_action": True,
            "no_production_access": True,
            "no_protected_path_change": True,
            "complete_diff_inspected": True,
        },
        "handoff_document_ref": "SENTINEL_HANDOFF_REF",
        "conclusion": {"text": conclusion_text, "label": "FACT"},
    }


def _reviewer_evidence(attempt=0, artifact=None, verdict="PASS", findings=None):
    art = artifact or _artifact_commit()
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:05:00Z",
        "artifact_identity_confirmed_at_start": art,
        "artifact_identity_confirmed_before_conclusion": art,
        "rechecked_commands": [],
        "findings": findings or [],
        "verdict": verdict,
        "blocked_reason": "boom" if verdict == "BLOCKED" else None,
    }


def _gate_decision(status="approved", approved_for=None):
    ts = "2026-08-19T12:10:00Z"
    return {
        "status": status, "requested_at": ts, "decided_at": ts, "decided_by": "jose",
        "decision_ref": "ref-1", "approved_for": approved_for or {"marker": "x"},
    }


def _proposal(proposal_id="p1"):
    return {
        "proposal_id": proposal_id, "proposed_at": "2026-08-19T12:20:00Z", "proposed_by": "david",
        "label": "FACT", "rationale": "x", "diff_against_current_scope": {"added": ["x"], "removed": []},
        "status": "pending_human_decision", "decided_by": None, "decided_at": None,
        "resulting_mission_definition_version": None,
    }


def _mission_definition_entry():
    return {
        "outcome": "ship", "scope": ["x"], "non_goals": [], "acceptance_criteria": ["y"],
        "authorized_by": "jose", "authorized_at": "2026-08-19T12:25:00Z",
        "authorization_decision_ref": "r2",
    }


def _result(
    invocation_id,
    outcome="completed",
    evidence=None,
    provider="claude",
    model="claude-sonnet-5",
    fresh_context_attested=True,
    provider_session_id=None,
    provider_conversation_id=None,
    error_detail=None,
):
    return ai.AgentInvocationResult(
        invocation_id=invocation_id,
        outcome=outcome,
        provider=provider,
        model=model,
        responded_at="2026-08-19T12:30:00Z",
        fresh_context_attested=fresh_context_attested,
        provider_session_id=provider_session_id,
        provider_conversation_id=provider_conversation_id,
        evidence=evidence,
        error_detail=error_detail,
    )


class AgentInvocationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()


# --- allow-list: Emilio ------------------------------------------------

class PruebaAllowListEmilio(AgentInvocationTestCase):
    def test_task_contiene_solo_los_campos_permitidos(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        self.assertEqual(set(req.task.keys()), {"mission_definition", "repository"})
        self.assertEqual(
            set(req.task["mission_definition"].keys()),
            {"outcome", "scope", "non_goals", "acceptance_criteria"},
        )

    def test_attempt_1_incluye_solo_cited_findings_nunca_su_propia_evidencia_previa(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(
            attempt=0, verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P1", "summary": "SENTINEL_FINDING",
                       "file": None, "line_range": None, "category": "bug"}]))

        req = ai.build_emilio_invocation_request(mid, 1)
        self.assertEqual(
            set(req.task.keys()), {"mission_definition", "repository", "cited_findings"}
        )
        serialized = json.dumps(req.task)
        self.assertIn("SENTINEL_FINDING", serialized)
        # nunca su propia builder_evidence[0] (conclusion/assumptions/etc)
        self.assertNotIn("SENTINEL_CONCLUSION", serialized)

    def test_requested_fresh_context_es_siempre_false(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        self.assertFalse(req.requested_fresh_context)

    def test_agent_role_y_attempt(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        self.assertEqual(req.agent_role, "emilio")
        self.assertEqual(req.attempt, 0)


# --- allow-list: Emma (el más consecuente) ------------------------------

class PruebaAllowListEmma(AgentInvocationTestCase):
    def _mission_con_builder_evidence(self, attempt=0):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=attempt))
        return mid

    def test_campos_prohibidos_nunca_aparecen(self):
        """conclusion/assumptions/risks/rollback_notes/safety_confirmation
        -- el juicio de Emilio, nunca la evidencia -- no deben aparecer en
        ninguna parte del task de Emma, ni siquiera serializados."""
        mid = self._mission_con_builder_evidence(0)
        req = ai.build_emma_invocation_request(mid, 0)
        serialized = json.dumps(req.task)
        for sentinel in (
            "SENTINEL_CONCLUSION", "SENTINEL_RISK", "SENTINEL_ASSUMPTION",
            "SENTINEL_ROLLBACK_NOTES",
        ):
            self.assertNotIn(sentinel, serialized, f"{sentinel} leaked into Emma's task")
        self.assertNotIn("conclusion", req.task)
        self.assertNotIn("assumptions", req.task)
        self.assertNotIn("risks", req.task)
        self.assertNotIn("rollback_notes", req.task)
        self.assertNotIn("safety_confirmation", req.task)

    def test_campos_permitidos_si_aparecen(self):
        """changed_files/checks/handoff_document_ref -- evidencia fáctica,
        per el Incremento #7 corrective cycle -- deben llegar a Emma."""
        mid = self._mission_con_builder_evidence(0)
        req = ai.build_emma_invocation_request(mid, 0)
        serialized = json.dumps(req.task)
        for sentinel in ("SENTINEL_CHANGED_FILE.py", "SENTINEL_CHECK_RESULT", "SENTINEL_HANDOFF_REF"):
            self.assertIn(sentinel, serialized, f"{sentinel} missing from Emma's task")

    def test_task_no_contiene_builder_evidence_completo_solo_los_campos_permitidos(self):
        mid = self._mission_con_builder_evidence(0)
        req = ai.build_emma_invocation_request(mid, 0)
        self.assertEqual(
            set(req.task.keys()),
            {"mission_definition", "artifact", "changed_files", "checks",
             "handoff_document_ref", "repository"},
        )

    def test_requested_fresh_context_es_siempre_true(self):
        mid = self._mission_con_builder_evidence(0)
        req = ai.build_emma_invocation_request(mid, 0)
        self.assertTrue(req.requested_fresh_context)

    def test_attempt_1_incluye_propios_findings_previos(self):
        mid = self._mission_con_builder_evidence(0)
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(
            attempt=0, verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P1", "summary": "SENTINEL_OWN_FINDING",
                       "file": None, "line_range": None, "category": "bug"}]))
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=1))

        req = ai.build_emma_invocation_request(mid, 1)
        self.assertIn("own_prior_findings", req.task)
        self.assertIn("SENTINEL_OWN_FINDING", json.dumps(req.task["own_prior_findings"]))

    def test_falta_evidencia_de_builder_para_ese_attempt_lanza(self):
        m = _create_intake_mission("algo")
        with self.assertRaises(ValueError):
            ai.build_emma_invocation_request(m["mission_id"], 0)


# --- fresh-context: refusal + asimetría --------------------------------

class PruebaFreshContextEmma(AgentInvocationTestCase):
    def _mission_lista_para_review(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        return mid

    def test_fresh_context_attested_no_true_es_rechazado(self):
        mid = self._mission_lista_para_review()
        req = ai.build_emma_invocation_request(mid, 0)
        for bad_value in (False, None, 0, "true"):
            result = _result(req.invocation_id, fresh_context_attested=bad_value)
            with self.assertRaises(ai.FreshContextNotAttested, msg=repr(bad_value)):
                ai.consume_emma_result(req, result)

    def test_fresh_context_attested_true_es_aceptado(self):
        mid = self._mission_lista_para_review()
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          evidence=_reviewer_evidence(attempt=0))
        updated = ai.consume_emma_result(req, result)
        self.assertIsNotNone(updated)

    def test_requested_fresh_context_corrupto_en_el_request_es_rechazado(self):
        mid = self._mission_lista_para_review()
        req = ai.build_emma_invocation_request(mid, 0)
        # simula un request object corrupto -- nunca producido por el builder real
        import dataclasses
        corrupted = dataclasses.replace(req, requested_fresh_context=False)
        result = _result(corrupted.invocation_id, fresh_context_attested=True)
        with self.assertRaises(ai.FreshContextNotAttested):
            ai.consume_emma_result(corrupted, result)


class PruebaAsimetriaEmilio(AgentInvocationTestCase):
    def test_fresh_context_attested_false_no_es_rechazado_para_emilio(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        result = _result(req.invocation_id, fresh_context_attested=False,
                          evidence=_builder_evidence(attempt=0))
        updated = ai.consume_emilio_result(req, result)
        self.assertIsNotNone(updated)


# --- invocation_id mismatch ---------------------------------------------

class PruebaInvocationIdMismatch(AgentInvocationTestCase):
    def test_emilio_mismatch(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        result = _result(str(uuid.uuid4()), evidence=_builder_evidence(attempt=0))
        with self.assertRaises(ai.InvocationIdMismatch):
            ai.consume_emilio_result(req, result)

    def test_emma_mismatch(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(str(uuid.uuid4()), evidence=_reviewer_evidence(attempt=0))
        with self.assertRaises(ai.InvocationIdMismatch):
            ai.consume_emma_result(req, result)


# --- session/conversation-ID comparison (INV-2a) ------------------------

class PruebaStaleSessionReused(AgentInvocationTestCase):
    def _mission_lista(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        return mid

    def test_provider_session_id_igual_al_previo_es_rechazado_aunque_attested_sea_true(self):
        mid = self._mission_lista()
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          provider_session_id="SAME-SESSION-ID")
        with self.assertRaises(ai.StaleSessionReused):
            ai.consume_emma_result(req, result, prior_session_id="SAME-SESSION-ID")

    def test_provider_conversation_id_igual_al_previo_es_rechazado(self):
        mid = self._mission_lista()
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          provider_conversation_id="SAME-THREAD-ID")
        with self.assertRaises(ai.StaleSessionReused):
            ai.consume_emma_result(req, result, prior_conversation_id="SAME-THREAD-ID")

    def test_identificador_distinto_es_aceptado(self):
        mid = self._mission_lista()
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          provider_session_id="DIFFERENT-ID",
                          evidence=_reviewer_evidence(attempt=0))
        updated = ai.consume_emma_result(req, result, prior_session_id="SAME-SESSION-ID")
        self.assertIsNotNone(updated)

    def test_sin_identificador_previo_se_omite_la_comparacion_no_se_trata_como_pase(self):
        """Ausencia de un identificador previo (None) se omite -- ni
        aprueba ni rechaza por sí sola -- exactamente el principio ya
        establecido para 'el proveedor no expone identificador'."""
        mid = self._mission_lista()
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          provider_session_id=None, provider_conversation_id=None,
                          evidence=_reviewer_evidence(attempt=0))
        updated = ai.consume_emma_result(req, result)  # sin prior_session_id/prior_conversation_id
        self.assertIsNotNone(updated)


# --- attempt binding (Incremento #16, ciclo correctivo de frontera) -----

class PruebaAttemptBinding(AgentInvocationTestCase):
    """Cierra el hallazgo real confirmado de Discovery (B): request.attempt
    nunca se serializa en request.task, así que evidence['attempt'] era,
    antes de esta corrección, dato enteramente afirmado por el proveedor,
    sin ningún chequeo de vínculo con lo realmente solicitado antes de
    chugel.record_builder_evidence()/record_reviewer_evidence(). Un
    intento real de Codex explotó exactamente esta brecha: fabricó una
    narrativa completa de ciclo correctivo y se autoetiquetó
    attempt=1 mientras el request pedía attempt=0."""

    def test_emilio_attempt_coincidente_pasa(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        result = _result(req.invocation_id, evidence=_builder_evidence(attempt=0))
        updated = ai.consume_emilio_result(req, result)
        self.assertIsNotNone(updated)

    def test_emilio_attempt_no_coincidente_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        result = _result(req.invocation_id, evidence=_builder_evidence(attempt=1))
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emilio_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_emilio_attempt_faltante_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        bad_evidence = _builder_evidence(attempt=0)
        del bad_evidence["attempt"]
        result = _result(req.invocation_id, evidence=bad_evidence)
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emilio_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_emilio_attempt_malformado_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        bad_evidence = _builder_evidence(attempt="0")  # string, no int -- nunca coercido
        result = _result(req.invocation_id, evidence=bad_evidence)
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emilio_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_emma_attempt_coincidente_pasa(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)
        result = _result(req.invocation_id, fresh_context_attested=True,
                          evidence=_reviewer_evidence(attempt=0))
        updated = ai.consume_emma_result(req, result)
        self.assertIsNotNone(updated)

    def test_emma_attempt_no_coincidente_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        result = _result(req.invocation_id, fresh_context_attested=True,
                          evidence=_reviewer_evidence(attempt=1))
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emma_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_emma_attempt_faltante_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        bad_evidence = _reviewer_evidence(attempt=0)
        del bad_evidence["attempt"]
        result = _result(req.invocation_id, fresh_context_attested=True, evidence=bad_evidence)
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emma_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_emma_attempt_malformado_falla_antes_de_persistir(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        bad_evidence = _reviewer_evidence(attempt=None)
        result = _result(req.invocation_id, fresh_context_attested=True, evidence=bad_evidence)
        with self.assertRaises(ai.AttemptNumberMismatch):
            ai.consume_emma_result(req, result)
        self.assertEqual(path.read_bytes(), before)

    def test_attempt_binding_ocurre_despues_de_invocation_id(self):
        """invocation_id mismatch debe seguir teniendo prioridad -- un
        mismatch de attempt no debe enmascarar un protocolo violado más
        fundamental."""
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        result = _result(str(uuid.uuid4()), evidence=_builder_evidence(attempt=1))
        with self.assertRaises(ai.InvocationIdMismatch):
            ai.consume_emilio_result(req, result)

    def test_attempt_binding_no_interfiere_con_fresh_context_ni_stale_session(self):
        """Los chequeos preexistentes de fresh-context/stale-session de
        Emma deben seguir teniendo su propio tipo de excepción intacto --
        el chequeo de attempt no debe enmascararlos cuando evidence es
        None/placeholder, exactamente el escenario que ya ejercían estas
        pruebas preexistentes."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        req = ai.build_emma_invocation_request(mid, 0)

        result = _result(req.invocation_id, fresh_context_attested=False)
        with self.assertRaises(ai.FreshContextNotAttested):
            ai.consume_emma_result(req, result)

        result = _result(req.invocation_id, fresh_context_attested=True,
                          provider_session_id="SAME-SESSION-ID")
        with self.assertRaises(ai.StaleSessionReused):
            ai.consume_emma_result(req, result, prior_session_id="SAME-SESSION-ID")

    def test_attempt_1_coincidente_pasa_para_ambos_roles(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(
            attempt=0, verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P1", "summary": "x",
                       "file": None, "line_range": None, "category": "bug"}]))

        req1 = ai.build_emilio_invocation_request(mid, 1)
        result1 = _result(req1.invocation_id, evidence=_builder_evidence(attempt=1))
        self.assertIsNotNone(ai.consume_emilio_result(req1, result1))

        req_e1 = ai.build_emma_invocation_request(mid, 1)
        result_e1 = _result(req_e1.invocation_id, fresh_context_attested=True,
                             evidence=_reviewer_evidence(attempt=1))
        self.assertIsNotNone(ai.consume_emma_result(req_e1, result_e1))

    def test_outcome_no_completado_omite_el_chequeo_de_attempt(self):
        """Un outcome no-completado nunca persiste nada de todas formas --
        el chequeo de attempt no debe lanzar espuriamente cuando evidence
        es None, que es la forma normal de un resultado no-completado."""
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        for outcome in ("failed", "timeout", "invalid_output", "unavailable"):
            result = _result(req.invocation_id, outcome=outcome, evidence=None)
            returned = ai.consume_emilio_result(req, result)
            self.assertIsNone(returned, msg=outcome)


# --- outcome branches ----------------------------------------------------

class PruebaOutcomeBranches(AgentInvocationTestCase):
    def test_cada_outcome_no_completado_no_escribe_nada(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        for outcome in ("failed", "timeout", "invalid_output", "unavailable"):
            result = _result(req.invocation_id, outcome=outcome, evidence=None,
                              error_detail="boom")
            returned = ai.consume_emilio_result(req, result)
            self.assertIsNone(returned, msg=outcome)
            self.assertEqual(path.read_bytes(), before, msg=outcome)

    def test_completed_escribe_evidencia(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        result = _result(req.invocation_id, outcome="completed",
                          evidence=_builder_evidence(attempt=0))
        updated = ai.consume_emilio_result(req, result)
        self.assertEqual(len(updated["builder_evidence"]), 1)

    def test_completed_con_evidencia_invalida_falla_via_validador_existente(self):
        """evidence estructuralmente inválida pasa por
        validate_mission_record() sin cambios -- este módulo no duplica
        esa validación."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        req = ai.build_emilio_invocation_request(mid, 0)
        bad_evidence = _builder_evidence(attempt=0)
        del bad_evidence["conclusion"]  # rompe el schema (campo requerido)
        result = _result(req.invocation_id, outcome="completed", evidence=bad_evidence)
        with self.assertRaises(chugel.MissionValidationFailed):
            ai.consume_emilio_result(req, result)

    def test_outcome_desconocido_lanza_value_error(self):
        m = _create_intake_mission("algo")
        req = ai.build_emilio_invocation_request(m["mission_id"], 0)
        result = _result(req.invocation_id, outcome="not_a_real_outcome")
        with self.assertRaises(ValueError):
            ai.consume_emilio_result(req, result)


# --- ciclo completo de extremo a extremo --------------------------------

class PruebaCicloCorrectivoCompleto(AgentInvocationTestCase):
    def test_secuencia_de_cuatro_invocaciones(self):
        """Emilio(0) -> Emma(0), CHANGES_REQUIRED -> Emilio(1) -> Emma(1),
        PASS -- exactamente la secuencia de orchestrator/PROVIDER_ROUTER_V1.md
        section 13, simulando resultados de un AgentInvoker con dicts
        construidos a mano en vez de un adapter real."""
        m = _create_intake_mission("feature X")
        mid = m["mission_id"]
        chugel.decide_gate(mid, "scope_authorization", _gate_decision(
            approved_for={"mission_definition_version": 1}))
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="x")
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="x")
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/w", "branch": "b", "base_sha": "c" * 40,
            "isolation_confirmed": True,
        })
        chugel.transition(mid, "BUILDING", actor="chugel", reason="x")

        # Emilio, attempt 0
        req0 = ai.build_emilio_invocation_request(mid, 0)
        res0 = _result(req0.invocation_id, provider="codex", model="codex-1",
                        fresh_context_attested=False, provider_conversation_id="codex-thread-0",
                        evidence=_builder_evidence(attempt=0))
        updated = ai.consume_emilio_result(req0, res0)
        self.assertEqual(updated["corrective_cycle_count"], 0)

        chugel.transition(mid, "VERIFYING", actor="chugel", reason="x")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="x")
        chugel.transition(mid, "REVIEWING", actor="jose", reason="x")

        # Emma, attempt 0 -- fresh context, distinta sesión de la de Emilio
        req_e0 = ai.build_emma_invocation_request(mid, 0)
        res_e0 = _result(req_e0.invocation_id, provider="claude", model="claude-sonnet-5",
                          fresh_context_attested=True, provider_session_id="claude-session-0",
                          evidence=_reviewer_evidence(attempt=0, verdict="CHANGES_REQUIRED",
                              findings=[{"id": "f1", "severity": "P1", "summary": "fix it",
                                         "file": "x.py", "line_range": "1-2", "category": "bug"}]))
        updated = ai.consume_emma_result(req_e0, res_e0, prior_conversation_id="codex-thread-0")
        self.assertEqual(updated["reviewer_evidence"][0]["verdict"], "CHANGES_REQUIRED")

        chugel.transition(mid, "CHANGES_REQUIRED", actor="chugel", reason="x")
        chugel.transition(mid, "CORRECTING", actor="jose", reason="x")

        # Emilio, attempt 1 -- recibe solo los findings citados
        req1 = ai.build_emilio_invocation_request(mid, 1)
        self.assertIn("fix it", json.dumps(req1.task["cited_findings"]))
        res1 = _result(req1.invocation_id, provider="codex", model="codex-1",
                        fresh_context_attested=False, provider_conversation_id="codex-thread-1",
                        evidence=_builder_evidence(attempt=1))
        updated = ai.consume_emilio_result(req1, res1)
        self.assertEqual(updated["corrective_cycle_count"], 1)

        chugel.transition(mid, "VERIFYING", actor="chugel", reason="x")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="x")
        chugel.transition(mid, "REVIEWING", actor="jose", reason="x")

        # Emma, attempt 1 -- fresh context otra vez, distinta de attempt 0 y de Emilio
        req_e1 = ai.build_emma_invocation_request(mid, 1)
        self.assertIn("f1", json.dumps(req_e1.task["own_prior_findings"]))
        res_e1 = _result(req_e1.invocation_id, provider="claude", model="claude-sonnet-5",
                          fresh_context_attested=True, provider_session_id="claude-session-1",
                          evidence=_reviewer_evidence(attempt=1, verdict="PASS"))
        updated = ai.consume_emma_result(
            req_e1, res_e1,
            prior_conversation_id="codex-thread-1",  # nunca reutiliza el hilo de Emilio
        )
        self.assertEqual(updated["reviewer_evidence"][1]["verdict"], "PASS")

        final = chugel.get_mission(mid)
        self.assertEqual(len(final["builder_evidence"]), 2)
        self.assertEqual(len(final["reviewer_evidence"]), 2)
        self.assertEqual(final["corrective_cycle_count"], 1)


if __name__ == "__main__":
    unittest.main()
