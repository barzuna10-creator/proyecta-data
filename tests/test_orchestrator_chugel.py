"""Pruebas para orchestrator/chugel.py (Zentra Autonomous Engineering V1,
Level 2, Increment #6 -- implementación de Chugel V1 per
orchestrator/CHUGEL_V1.md, ya revisado independientemente por Emma, más la
corrección de Incremento #6 que hace de create_mission() el único camino
para la Mission Definition inicial).

Todas las pruebas usan un directorio temporal aislado como
orchestrator.chugel._MISSIONS_DIR -- ninguna prueba escribe jamás en el
directorio real orchestrator/missions/. Ninguna prueba invoca un LLM, red
o subprocess: chugel.py es filesystem + funciones puras sobre dicts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import orchestrator.chugel as chugel


# --- fixtures --------------------------------------------------------

def _mission_definition_payload(authorized_by="jose"):
    """Payload de contenido para create_mission() -- version/source/
    based_on_proposal_id son siempre derivados por Chugel, nunca
    confiados desde acá (ver docstring de create_mission())."""
    return {
        "outcome": "ship the thing",
        "scope": ["do the thing"],
        "non_goals": [],
        "acceptance_criteria": ["it works"],
        "authorized_by": authorized_by,
        "authorized_at": "2026-08-19T12:00:00Z",
        "authorization_decision_ref": "ref-intake-1",
    }


def _create_intake_mission(intent_text="algo", **kwargs):
    """Atajo para el caso común: crear una misión real con una Mission
    Definition inicial válida ya atribuida a José."""
    return chugel.create_mission(intent_text, _mission_definition_payload(), **kwargs)


def _artifact_commit(sha=None):
    return {
        "mode": "commit",
        "commit_sha": sha or ("a" * 40),
        "patch_path": None,
        "patch_sha256": None,
        "patch_byte_size": None,
    }


def _builder_evidence(attempt=0, artifact=None):
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": artifact or _artifact_commit(),
        "changed_files": [],
        "checks": [],
        "skipped_checks": [],
        "risks": [],
        "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {
            "no_existing_work_altered": True,
            "no_main_change": True,
            "no_remote_action": True,
            "no_production_access": True,
            "no_protected_path_change": True,
            "complete_diff_inspected": True,
        },
        "handoff_document_ref": None,
        "conclusion": {"text": "done", "label": "FACT"},
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


def _gate_decision(status="approved", approved_for=None, decided_by="jose"):
    ts = "2026-08-19T12:10:00Z"
    if status == "approved":
        return {
            "status": "approved",
            "requested_at": ts,
            "decided_at": ts,
            "decided_by": decided_by,
            "decision_ref": "ref-1",
            "approved_for": approved_for or {"marker": "x"},
        }
    if status == "not_requested":
        return {
            "status": "not_requested",
            "requested_at": None,
            "decided_at": None,
            "decided_by": None,
            "decision_ref": None,
            "approved_for": None,
        }
    return {
        "status": status,
        "requested_at": ts,
        "decided_at": ts if decided_by else None,
        "decided_by": decided_by,
        "decision_ref": "ref-1" if decided_by else None,
        "approved_for": None,
    }


def _proposal(proposal_id="p1", status="pending_human_decision"):
    return {
        "proposal_id": proposal_id,
        "proposed_at": "2026-08-19T12:20:00Z",
        "proposed_by": "david",
        "label": "FACT",
        "rationale": "need more scope",
        "diff_against_current_scope": {"added": ["extra thing"], "removed": []},
        "status": status,
        "decided_by": None,
        "decided_at": None,
        "resulting_mission_definition_version": None,
    }


def _mission_definition_entry():
    """Payload para decide_scope_change()'s decision['mission_definition_entry']
    -- version/source/based_on_proposal_id son siempre sobreescritos por
    Chugel; incluirlos acá solo prueba que efectivamente son ignorados."""
    return {
        "outcome": "ship the replanned thing",
        "scope": ["do the replanned thing"],
        "non_goals": [],
        "acceptance_criteria": ["it still works"],
        "authorized_by": "jose",
        "authorized_at": "2026-08-19T12:25:00Z",
        "authorization_decision_ref": "ref-2",
    }


class ChugelTestCase(unittest.TestCase):
    """Redirige orchestrator.chugel a un directorio temporal por prueba."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()


# --- ciclo de vida básico -----------------------------------------------

class PruebaCicloDeVidaBasico(ChugelTestCase):
    def test_create_get_roundtrip(self):
        created = _create_intake_mission("hacer algo")
        fetched = chugel.get_mission(created["mission_id"])
        self.assertEqual(created, fetched)
        self.assertEqual(fetched["state"], "INTAKE")
        self.assertFalse(fetched["repository"]["isolation_confirmed"])

    def test_ciclo_completo_deterministico_de_extremo_a_extremo(self):
        """Persiste, recarga, registra evidencia/decisiones, aplica
        transiciones y preserva el registro a lo largo de todo el camino,
        sin nunca saltarse un gate -- ahora arrancando desde una Mission
        Definition david_intake genuina escrita por create_mission()."""
        m = _create_intake_mission("feature X")
        mid = m["mission_id"]
        self.assertEqual(len(m["mission_definition_history"]), 1)
        self.assertEqual(m["mission_definition_history"][0]["source"], "david_intake")
        self.assertEqual(m["human_gates"]["scope_authorization"]["status"], "not_requested")

        # AUTHORIZED es el estado realmente gateado por scope_authorization
        # (SCOPE_AWAITING_AUTHORIZATION en sí no tiene un evidence checker
        # dedicado en can_transition() -- solo la tabla de transiciones).
        m = chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="draft ready")
        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "AUTHORIZED", actor="jose", reason="premature")

        chugel.decide_gate(mid, "scope_authorization", _gate_decision(
            approved_for={"mission_definition_version": 1}))

        m = chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/w", "branch": "feature/x",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        self.assertTrue(chugel.get_mission(mid)["repository"]["isolation_confirmed"])

        m = chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        m = chugel.transition(mid, "BUILDING", actor="chugel", reason="build starting")

        m = chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        self.assertEqual(m["corrective_cycle_count"], 0)

        m = chugel.transition(mid, "VERIFYING", actor="chugel", reason="checks running")
        m = chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks done")
        m = chugel.transition(mid, "REVIEWING", actor="jose", reason="emma turn")

        m = chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P1", "summary": "bug", "file": "x.py", "line_range": "1-2", "category": "correctness"}]))

        m = chugel.transition(mid, "CHANGES_REQUIRED", actor="chugel", reason="review says fix it")
        m = chugel.transition(mid, "CORRECTING", actor="jose", reason="corrective cycle authorized")

        m = chugel.record_builder_evidence(mid, _builder_evidence(attempt=1))
        self.assertEqual(m["corrective_cycle_count"], 1, "attempt=1 must atomically set corrective_cycle_count")

        m = chugel.transition(mid, "VERIFYING", actor="chugel", reason="re-checks running")
        m = chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="re-checks done")
        m = chugel.transition(mid, "REVIEWING", actor="jose", reason="emma re-review")
        m = chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=1, verdict="PASS"))
        m = chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="pass verdict")

        # Only a subset of states carry a dedicated evidence checker in
        # can_transition() (AUTHORIZED, BUILDING, REVIEWING, MERGING,
        # MERGED, DEPLOY_PENDING, VERIFYING_PRODUCTION, COMPLETED --
        # see orchestrator/validator.py's _STATE_EVIDENCE_CHECKERS).
        # PUBLISHING and CI_PENDING are not among them, so those two
        # transitions succeed on table-membership + base-validity alone;
        # MERGING genuinely is gated on human_gates.merge_authorization,
        # which this V1 slice has no path to satisfy (no publish/merge/
        # deploy-writing operation exists -- git/GitHub/CI automation is
        # explicitly out of scope for Chugel V1).
        m = chugel.transition(mid, "PUBLISHING", actor="jose", reason="publish authorized out-of-band")
        m = chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci running")
        m = chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="ci green")
        with self.assertRaises(chugel.MissionTransitionRejected) as ctx:
            chugel.transition(mid, "MERGING", actor="jose", reason="premature, merge_authorization never approved")
        self.assertTrue(any(r.code == "STATE_EVIDENCE_MISSING" for r in ctx.exception.reasons))

        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "MERGE_AWAITING_AUTHORIZATION")
        self.assertEqual(len(final["builder_evidence"]), 2)
        self.assertEqual(len(final["reviewer_evidence"]), 2)


# --- escritura atómica y fallo cerrado ------------------------------------

class PruebaEscrituraAtomicaYFalloCerrado(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def test_mutacion_invalida_no_escribe_nada(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        # attempt fuera de la secuencia legal ([]/[0]/[0,1]) debe fallar
        # en cross-field, dejando el archivo intacto.
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_builder_evidence(mid, _builder_evidence(attempt=1))  # sin attempt=0 previo

        self.assertEqual(path.read_bytes(), before)

    def test_no_queda_archivo_temporal_tras_una_escritura_exitosa(self):
        _create_intake_mission("algo")
        leftovers = list(chugel._MISSIONS_DIR.glob(".*.tmp-*"))
        self.assertEqual(leftovers, [])

    def test_fallo_durante_la_escritura_no_corrompe_el_archivo_final(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        original_replace = os.replace

        def _boom(*args, **kwargs):
            raise OSError("simulated crash before replace")

        os.replace = _boom
        try:
            with self.assertRaises(OSError):
                chugel.record_repository_state(mid, {
                    "worktree_path": "/tmp/w", "branch": "b",
                    "base_sha": "c" * 40, "isolation_confirmed": True,
                })
        finally:
            os.replace = original_replace

        self.assertEqual(path.read_bytes(), before, "el archivo final nunca debe quedar corrupto")
        leftovers = list(chugel._MISSIONS_DIR.glob(f".{mid}.json.tmp-*"))
        self.assertEqual(leftovers, [], "este módulo limpia su propio tmp file en cualquier fallo que observa")


# --- transiciones ilegales -----------------------------------------------

class PruebaTransicionesIlegales(ChugelTestCase):
    def test_transicion_fuera_de_tabla_no_escribe(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        with self.assertRaises(chugel.MissionTransitionRejected) as ctx:
            chugel.transition(mid, "COMPLETED", actor="jose", reason="nope")

        self.assertTrue(any(r.code == "ILLEGAL_STATE_TRANSITION" for r in ctx.exception.reasons))
        self.assertEqual(path.read_bytes(), before)


# --- seguridad de rutas ----------------------------------------------

class PruebaSeguridadDeRutas(ChugelTestCase):
    def test_mission_id_malformado_es_rechazado_en_cada_operacion(self):
        bad_ids = [
            "../../etc/passwd",
            "not-a-uuid",
            "3fa85f64-5717-4562-b3fc-2c963f66afa6\x00",
            "/etc/passwd",
            "3fa85f64-5717-4562-b3fc",  # demasiado corto
        ]
        for bad_id in bad_ids:
            with self.assertRaises(ValueError, msg=bad_id):
                chugel.get_mission(bad_id)
            self.assertFalse((chugel._MISSIONS_DIR / f"{bad_id}.json").exists())

    def test_mission_id_malformado_en_create_mission_explicito(self):
        with self.assertRaises(ValueError):
            _create_intake_mission("algo", mission_id="../evil")


# --- symlinks ------------------------------------------------------------

class PruebaSymlinks(ChugelTestCase):
    def test_symlink_en_lugar_del_registro_es_rechazado(self):
        m = _create_intake_mission("víctima")
        target_id = m["mission_id"]

        other = _create_intake_mission("otra cosa")
        target_path = chugel._mission_path(target_id)
        other_path = chugel._mission_path(other["mission_id"])
        target_path.unlink()
        target_path.symlink_to(other_path)

        with self.assertRaises(chugel.MissionRecordPathUnsafe):
            chugel.get_mission(target_id)
        with self.assertRaises(chugel.MissionRecordPathUnsafe):
            chugel.record_repository_state(target_id, {
                "worktree_path": "/x", "branch": "b", "base_sha": "d" * 40, "isolation_confirmed": True,
            })

    def test_symlink_apuntando_fuera_del_directorio_de_misiones(self):
        m = _create_intake_mission("víctima2")
        mid = m["mission_id"]
        path = chugel._mission_path(mid)
        outside = Path(self._tmpdir.name) / "outside.json"
        outside.write_text("{}")
        path.unlink()
        path.symlink_to(outside)

        with self.assertRaises(chugel.MissionRecordPathUnsafe):
            chugel.get_mission(mid)

    def test_o_nofollow_rechaza_un_symlink_directamente(self):
        real = Path(self._tmpdir.name) / "real.json"
        real.write_text("{}")
        link = Path(self._tmpdir.name) / "link.json"
        link.symlink_to(real)

        with self.assertRaises(OSError):
            fd = os.open(str(link), os.O_RDONLY | chugel._O_NOFOLLOW)
            os.close(fd)


# --- create_mission ya existente ------------------------------------------

class PruebaCreateMissionExistente(ChugelTestCase):
    def test_id_ya_usado_es_rechazado_sin_sobrescribir(self):
        m = _create_intake_mission("original", mission_id="3fa85f64-5717-4562-b3fc-2c963f66afa6")
        path = chugel._mission_path(m["mission_id"])
        before = path.read_bytes()

        with self.assertRaises(chugel.MissionRecordAlreadyExists):
            _create_intake_mission("intento de pisar", mission_id="3fa85f64-5717-4562-b3fc-2c963f66afa6")

        self.assertEqual(path.read_bytes(), before)

    def test_symlink_plantado_en_el_destino_es_rechazado(self):
        chugel._MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
        fixed_id = "3fa85f64-5717-4562-b3fc-2c963f66afa7"
        path = chugel._mission_path(fixed_id)
        elsewhere = chugel._MISSIONS_DIR / "elsewhere.json"
        elsewhere.write_text("{}")
        path.symlink_to(elsewhere)

        with self.assertRaises(chugel.MissionRecordAlreadyExists):
            _create_intake_mission("intento", mission_id=fixed_id)


# --- create_mission: Mission Definition inicial (corrección Incremento #6) ---

class PruebaCreateMissionMissionDefinitionInicial(ChugelTestCase):
    def test_una_sola_entrada_inicial_version_1_david_intake(self):
        """1-4: una misión real recién creada contiene exactamente una
        versión inicial, es la versión 1, su source es david_intake, y
        based_on_proposal_id es null."""
        m = _create_intake_mission("algo")
        history = m["mission_definition_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["version"], 1)
        self.assertEqual(history[0]["source"], "david_intake")
        self.assertIsNone(history[0]["based_on_proposal_id"])

    def test_no_hace_falta_ninguna_proposed_scope_change_falsa(self):
        """5: create_mission() no requiere ni escribe ninguna entrada en
        proposed_scope_changes[] para producir la versión inicial."""
        m = _create_intake_mission("algo")
        self.assertEqual(m["proposed_scope_changes"], [])

    def test_no_aprueba_silenciosamente_scope_authorization(self):
        """6: crear la Mission Definition nunca aprueba el gate de
        ejecución -- son conceptos separados."""
        m = _create_intake_mission("algo")
        gate = m["human_gates"]["scope_authorization"]
        self.assertEqual(gate["status"], "not_requested")
        self.assertIsNone(gate["decided_by"])
        self.assertIsNone(gate["decided_at"])
        self.assertIsNone(gate["approved_for"])

    def test_no_puede_avanzar_a_scope_awaiting_authorization_sin_decide_gate(self):
        """7: la misión no puede entrar a AUTHORIZED (el estado realmente
        gateado por human_gates.scope_authorization -- ver
        orchestrator/validator.py's _evidence_authorized) hasta que José
        la apruebe explícitamente via decide_gate() para la versión
        actual correcta."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="draft ready")

        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "AUTHORIZED", actor="jose", reason="premature")

        chugel.decide_gate(mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}))
        updated = chugel.transition(mid, "AUTHORIZED", actor="jose", reason="now approved")
        self.assertEqual(updated["state"], "AUTHORIZED")

    def test_replan_posterior_sigue_produciendo_david_replan_nunca_otro_david_intake(self):
        """8: un cambio de alcance posterior sigue usando
        propose_scope_change() -> decide_scope_change() y produce
        david_replan -- nunca otro david_intake, incluso si el caller
        intenta forzar 'source' en el payload."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.propose_scope_change(mid, _proposal(proposal_id="p1"))

        entry_payload = _mission_definition_entry()
        entry_payload["source"] = "david_intake"  # intento deliberado de forzarlo -- debe ser ignorado

        updated = chugel.decide_scope_change(mid, "p1", {
            "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z",
            "status": "accepted", "mission_definition_entry": entry_payload,
        })
        history = updated["mission_definition_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["source"], "david_intake")
        self.assertEqual(history[1]["version"], 2)
        self.assertEqual(history[1]["source"], "david_replan")
        self.assertEqual(history[1]["based_on_proposal_id"], "p1")

    def test_autorizacion_no_jose_en_la_definicion_inicial_falla_cerrado_sin_escribir(self):
        """9: un intento de crear la definición inicial con atribución
        distinta de José falla cerrado, sin escribir nada."""
        for bad_decider in ("emilio", "emma", "david", "", None, "Jose"):
            with self.assertRaises(ValueError, msg=repr(bad_decider)):
                chugel.create_mission(
                    "algo", _mission_definition_payload(authorized_by=bad_decider)
                )
        self.assertEqual(list(chugel._MISSIONS_DIR.glob("*.json")) if chugel._MISSIONS_DIR.exists() else [], [])


# --- decide_gate: atribución -----------------------------------------

class PruebaDecideGateAtribucion(ChugelTestCase):
    def test_decided_by_no_jose_es_rechazado_sin_misión_en_disco(self):
        for bad_decider in ("emilio", "emma", "", None, "Jose", "JOSE"):
            with self.assertRaises(ValueError, msg=repr(bad_decider)):
                chugel.decide_gate(
                    "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "scope_authorization",
                    _gate_decision(decided_by=bad_decider),
                )

    def test_gate_name_desconocido_es_rechazado(self):
        m = _create_intake_mission("algo")
        with self.assertRaises(ValueError):
            chugel.decide_gate(m["mission_id"], "not_a_real_gate", _gate_decision())

    def test_decision_valida_se_persiste(self):
        m = _create_intake_mission("algo")
        updated = chugel.decide_gate(m["mission_id"], "publish_authorization", _gate_decision())
        self.assertEqual(updated["human_gates"]["publish_authorization"]["status"], "approved")
        self.assertEqual(updated["human_gates"]["publish_authorization"]["decided_by"], "jose")


# --- decide_scope_change: atribución y atomicidad ------------------------

class PruebaDecideScopeChangeAtribucion(ChugelTestCase):
    def test_decided_by_no_jose_es_rechazado_sin_tocar_disco(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.propose_scope_change(mid, _proposal())
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        with self.assertRaises(ValueError):
            chugel.decide_scope_change(mid, "p1", {
                "decided_by": "emilio", "decided_at": "2026-08-19T12:30:00Z", "status": "accepted",
            })

        self.assertEqual(path.read_bytes(), before)

    def test_aceptacion_escribe_ambas_mitades_atomicamente(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.propose_scope_change(mid, _proposal())

        updated = chugel.decide_scope_change(mid, "p1", {
            "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z",
            "status": "accepted", "mission_definition_entry": _mission_definition_entry(),
        })

        proposal = updated["proposed_scope_changes"][0]
        self.assertEqual(proposal["status"], "accepted")
        self.assertEqual(proposal["resulting_mission_definition_version"], 2)
        self.assertEqual(len(updated["mission_definition_history"]), 2)
        self.assertEqual(updated["mission_definition_history"][1]["version"], 2)
        self.assertEqual(updated["mission_definition_history"][1]["based_on_proposal_id"], "p1")

    def test_rechazo_no_toca_mission_definition_history(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.propose_scope_change(mid, _proposal())

        updated = chugel.decide_scope_change(mid, "p1", {
            "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z", "status": "rejected",
        })
        self.assertEqual(updated["proposed_scope_changes"][0]["status"], "rejected")
        self.assertEqual(len(updated["mission_definition_history"]), 1)  # solo la david_intake original

    def test_secuenciacion_aprobacion_obsoleta_tras_replan(self):
        """La aceptación de un cambio de alcance NO autoriza automáticamente
        la nueva versión -- una scope_authorization ya aprobada para una
        versión anterior debe quedar obsoleta (fail-closed) en cuanto
        existe una versión más nueva, nunca arrastrada implícitamente."""
        m = _create_intake_mission("algo")
        mid = m["mission_id"]

        chugel.decide_gate(mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}))
        self.assertEqual(
            chugel.get_mission(mid)["human_gates"]["scope_authorization"]["status"], "approved"
        )

        # Re-plan a versión 2 -- el gate sigue apuntando a la versión 1.
        chugel.propose_scope_change(mid, _proposal(proposal_id="p2"))
        with self.assertRaises(chugel.MissionValidationFailed) as ctx:
            chugel.decide_scope_change(mid, "p2", {
                "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z",
                "status": "accepted", "mission_definition_entry": _mission_definition_entry(),
            })
        self.assertTrue(any(e.code == "STALE_APPROVAL" for e in ctx.exception.errors))

        current = chugel.get_mission(mid)
        self.assertEqual(len(current["mission_definition_history"]), 1)
        p2 = next(p for p in current["proposed_scope_changes"] if p["proposal_id"] == "p2")
        self.assertEqual(p2["status"], "pending_human_decision")


# --- corrective_cycle_count atómico ---------------------------------

class PruebaCorrectiveCycleCountAtomico(ChugelTestCase):
    def test_attempt_1_fija_el_contador_en_la_misma_escritura(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="CHANGES_REQUIRED",
            findings=[{"id": "f1", "severity": "P1", "summary": "x", "file": None, "line_range": None, "category": "bug"}]))

        updated = chugel.record_builder_evidence(mid, _builder_evidence(attempt=1))
        self.assertEqual(updated["corrective_cycle_count"], 1)

    def test_attempt_0_nunca_cambia_el_contador(self):
        m = _create_intake_mission("algo")
        updated = chugel.record_builder_evidence(m["mission_id"], _builder_evidence(attempt=0))
        self.assertEqual(updated["corrective_cycle_count"], 0)


# --- registro repository_state -----------------------------------------

class PruebaRepositoryState(ChugelTestCase):
    def test_placeholder_inicial_nunca_afirma_aislamiento(self):
        m = _create_intake_mission("algo")
        self.assertFalse(m["repository"]["isolation_confirmed"])

    def test_record_repository_state_reemplaza_el_placeholder(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        updated = chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/real-worktree", "branch": "feature/x",
            "base_sha": "e" * 40, "isolation_confirmed": True,
        })
        self.assertTrue(updated["repository"]["isolation_confirmed"])
        self.assertEqual(updated["repository"]["worktree_path"], "/tmp/real-worktree")

    def test_building_permanece_bloqueado_hasta_isolation_confirmed(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.decide_gate(mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}))
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="x")
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="x")

        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "BUILDING", actor="chugel", reason="premature")

        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/w", "branch": "b", "base_sha": "f" * 40, "isolation_confirmed": True,
        })
        updated = chugel.transition(mid, "BUILDING", actor="chugel", reason="now isolated")
        self.assertEqual(updated["state"], "BUILDING")


# --- registro inválido en disco -------------------------------------

class PruebaRegistroInvalidoEnDisco(ChugelTestCase):
    def test_json_malformado(self):
        chugel._MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
        mid = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        (chugel._MISSIONS_DIR / f"{mid}.json").write_text("{not json")
        with self.assertRaises(chugel.MissionRecordCorrupt):
            chugel.get_mission(mid)

    def test_json_valido_pero_invalido_segun_el_schema(self):
        chugel._MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
        mid = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        (chugel._MISSIONS_DIR / f"{mid}.json").write_text(json.dumps({"schema_version": "9.9.9"}))
        with self.assertRaises(chugel.MissionRecordInvalid):
            chugel.get_mission(mid)

    def test_mision_inexistente(self):
        with self.assertRaises(chugel.MissionNotFound):
            chugel.get_mission("3fa85f64-5717-4562-b3fc-2c963f66afa6")


# --- idempotencia ------------------------------------------------------

class PruebaIdempotencia(ChugelTestCase):
    def test_create_mission_dos_veces_crea_dos_misiones(self):
        a = _create_intake_mission("idea")
        b = _create_intake_mission("idea")
        self.assertNotEqual(a["mission_id"], b["mission_id"])

    def test_transicion_repetida_al_mismo_estado_falla_sin_exito_silencioso(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.decide_gate(mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}))
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="x")
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="x")
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/w", "branch": "b", "base_sha": "a" * 40, "isolation_confirmed": True,
        })
        chugel.transition(mid, "BUILDING", actor="chugel", reason="x")

        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "BUILDING", actor="chugel", reason="retry")

    def test_reintento_de_evidencia_con_mismo_attempt_falla(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        with self.assertRaises(chugel.MissionValidationFailed) as ctx:
            chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        self.assertTrue(any(e.code == "DUPLICATE_ATTEMPT_NUMBER" for e in ctx.exception.errors))


# --- pureza ----------------------------------------------------------

class PruebaPureza(ChugelTestCase):
    def test_get_mission_no_muta_el_archivo_ni_devuelve_una_referencia_compartida(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        a = chugel.get_mission(mid)
        a["state"] = "TAMPERED"
        b = chugel.get_mission(mid)
        self.assertEqual(b["state"], "INTAKE")


if __name__ == "__main__":
    unittest.main()
