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

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import orchestrator.chugel as chugel
import orchestrator.validator as validator


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


def _mission_at_publishing():
    """Build the shortest canonical PASS path to the publication state."""
    m = _create_intake_mission("publish identity")
    mid = m["mission_id"]
    chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="ready")
    chugel.decide_gate(mid, "scope_authorization", _gate_decision(
        approved_for={"mission_definition_version": 1}
    ))
    chugel.record_repository_state(mid, {
        "worktree_path": "/tmp/publish-worktree",
        "branch": "local/publish",
        "base_sha": "b" * 40,
        "isolation_confirmed": True,
    })
    chugel.transition(mid, "AUTHORIZED", actor="jose", reason="authorized")
    chugel.transition(mid, "BUILDING", actor="chugel", reason="build")
    chugel.record_builder_evidence(mid, _builder_evidence())
    chugel.transition(mid, "VERIFYING", actor="chugel", reason="verified")
    chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="handoff")
    chugel.transition(mid, "REVIEWING", actor="emma", reason="review")
    chugel.record_reviewer_evidence(mid, _reviewer_evidence(verdict="PASS"))
    chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="pass")
    chugel.transition(mid, "PUBLISHING", actor="jose", reason="publish authorized")
    return chugel.get_mission(mid)


def _mission_at_merge_awaiting_without_publish_identity():
    """Reach the merge gate through the real lifecycle with no recorded SHA."""
    record = _mission_at_publishing()
    mid = record["mission_id"]
    chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
    chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
    record = chugel.get_mission(mid)
    if record["publish"]["commit_sha"] is not None:
        raise AssertionError("fixture must reach merge authorization without publication identity")
    return record


class ChugelTestCase(unittest.TestCase):
    """Redirige orchestrator.chugel a un directorio temporal por prueba."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()


class PruebaListadoSoloLectura(ChugelTestCase):
    def test_lista_solo_nombres_canonicos_y_no_escribe(self):
        first = _create_intake_mission("uno")
        second = _create_intake_mission("dos")
        before = {p.name: p.read_bytes() for p in chugel._MISSIONS_DIR.iterdir()}
        (chugel._MISSIONS_DIR / "notes.txt").write_text("ignore", encoding="utf-8")
        (chugel._MISSIONS_DIR / "pending.tmp").write_text("ignore", encoding="utf-8")
        (chugel._MISSIONS_DIR / "not-a-uuid.json").write_text("{}", encoding="utf-8")
        (chugel._MISSIONS_DIR / f"{'a' * 8}-aaaa-aaaa-aaaa-{'a' * 12}.json").mkdir()

        listed = chugel.list_missions()

        self.assertEqual({row["mission_id"] for row in listed}, {
            first["mission_id"], second["mission_id"]
        })
        self.assertTrue(all(set(row) == {
            "mission_id", "readable", "state", "updated_at", "error_code"
        } for row in listed))
        after = {p.name: p.read_bytes() for p in chugel._MISSIONS_DIR.iterdir() if p.name in before}
        self.assertEqual(before, after)

    def test_candidato_corrupto_no_oculta_los_demas(self):
        valid = _create_intake_mission("valid")
        corrupt_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        (chugel._MISSIONS_DIR / f"{corrupt_id}.json").write_text("{", encoding="utf-8")
        rows = {row["mission_id"]: row for row in chugel.list_missions()}
        self.assertTrue(rows[valid["mission_id"]]["readable"])
        self.assertEqual(rows[corrupt_id], {
            "mission_id": corrupt_id, "readable": False, "state": None,
            "updated_at": None, "error_code": "MISSION_RECORD_CORRUPT",
        })

    def test_candidato_json_invalido_usa_codigo_estable_sin_payload(self):
        chugel._MISSIONS_DIR.mkdir()
        invalid_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        (chugel._MISSIONS_DIR / f"{invalid_id}.json").write_text(
            json.dumps({"mission_id": invalid_id, "intent": {"raw_text": "secret"}}),
            encoding="utf-8",
        )
        self.assertEqual(chugel.list_missions(), [{
            "mission_id": invalid_id, "readable": False, "state": None,
            "updated_at": None, "error_code": "MISSION_RECORD_INVALID",
        }])

    def test_symlink_canonico_es_error_acotado_y_directorio_no_aparece(self):
        chugel._MISSIONS_DIR.mkdir()
        target = Path(self._tmpdir.name) / "secret"
        target.write_text("secret-value", encoding="utf-8")
        linked_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        directory_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        (chugel._MISSIONS_DIR / f"{linked_id}.json").symlink_to(target)
        (chugel._MISSIONS_DIR / f"{directory_id}.json").mkdir()
        self.assertEqual(chugel.list_missions(), [{
            "mission_id": linked_id, "readable": False, "state": None,
            "updated_at": None, "error_code": "MISSION_PATH_UNSAFE",
        }])

    def test_directorio_ausente_no_se_crea(self):
        self.assertEqual(chugel.list_missions(), [])
        self.assertFalse(chugel._MISSIONS_DIR.exists())


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


class PruebaRegistroAtomicoDeCommitPublicado(ChugelTestCase):
    def test_sha_valido_se_persiste_y_solo_cambia_publish_y_updated_at(self):
        before = _mission_at_merge_awaiting_without_publish_identity()
        sha = "c" * 40

        updated = chugel.record_publish_commit(before["mission_id"], sha)

        self.assertEqual(updated["publish"]["commit_sha"], sha)
        persisted = chugel.get_mission(before["mission_id"])
        self.assertEqual(persisted["publish"]["commit_sha"], sha)
        expected = copy.deepcopy(before)
        expected["publish"]["commit_sha"] = sha
        expected["updated_at"] = updated["updated_at"]
        self.assertEqual(updated, expected)

    def test_valida_registro_completo_una_vez_y_escribe_una_vez(self):
        record = _mission_at_merge_awaiting_without_publish_identity()
        with (
            mock.patch.object(chugel, "_read_mission_record", return_value=record),
            mock.patch.object(
                chugel,
                "validate_mission_record",
                wraps=chugel.validate_mission_record,
            ) as validate,
            mock.patch.object(chugel, "_write_mission_record") as write,
        ):
            updated = chugel.record_publish_commit(record["mission_id"], "c" * 40)

        validate.assert_called_once_with(updated)
        write.assert_called_once_with(updated)

    def test_fallo_de_validacion_no_escribe(self):
        record = _mission_at_merge_awaiting_without_publish_identity()
        invalid = mock.Mock(valid=False, errors=("forced",))
        with (
            mock.patch.object(chugel, "_read_mission_record", return_value=record),
            mock.patch.object(chugel, "validate_mission_record", return_value=invalid) as validate,
            mock.patch.object(chugel, "_write_mission_record") as write,
            self.assertRaises(chugel.MissionValidationFailed),
        ):
            chugel.record_publish_commit(record["mission_id"], "c" * 40)

        validate.assert_called_once()
        write.assert_not_called()

    def test_sha_ausente_malformado_o_coercionado_falla_antes_de_leer(self):
        invalid = (None, True, False, 1, b"c" * 40, "", "c" * 39, "C" * 40, "g" * 40)
        with mock.patch.object(chugel, "_read_mission_record") as read:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    chugel.record_publish_commit("11111111-1111-4111-8111-111111111111", value)
        read.assert_not_called()

    def test_estado_incorrecto_y_terminal_fallan_sin_escribir(self):
        publishing = _mission_at_publishing()
        variants = []
        ci_pending = copy.deepcopy(publishing)
        ci_pending["state"] = "CI_PENDING"
        variants.append(ci_pending)
        unrelated = copy.deepcopy(publishing)
        unrelated["state"] = "INTAKE"
        variants.append(unrelated)
        terminal = copy.deepcopy(publishing)
        terminal["state"] = "CANCELLED"
        variants.append(terminal)
        for record in variants:
            with (
                self.subTest(state=record["state"]),
                mock.patch.object(chugel, "_read_mission_record", return_value=record),
                mock.patch.object(chugel, "_write_mission_record") as write,
                self.assertRaises(ValueError),
            ):
                chugel.record_publish_commit(record["mission_id"], "c" * 40)
            write.assert_not_called()

    def test_publishing_accepts_first_immutable_identity(self):
        record = _mission_at_publishing()
        with mock.patch.object(chugel, "_read_mission_record", return_value=record), \
             mock.patch.object(chugel, "_write_mission_record") as write:
            updated = chugel.record_publish_commit(record["mission_id"], "c" * 40)
        self.assertEqual(updated["publish"]["commit_sha"], "c" * 40)
        write.assert_called_once()

    def test_identidad_duplicada_o_conflictiva_es_inmutable(self):
        record = _mission_at_merge_awaiting_without_publish_identity()
        mid = record["mission_id"]
        chugel.record_publish_commit(mid, "c" * 40)
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        for sha in ("c" * 40, "d" * 40):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                chugel.record_publish_commit(mid, sha)
            self.assertEqual(path.read_bytes(), before)

    def test_merge_authorization_correcta_pasa_y_stale_falla(self):
        record = _mission_at_merge_awaiting_without_publish_identity()
        mid = record["mission_id"]
        sha = "c" * 40
        chugel.record_publish_commit(mid, sha)

        with self.assertRaises(chugel.MissionValidationFailed) as stale:
            chugel.decide_gate(mid, "merge_authorization", _gate_decision(
                approved_for={"head_sha": "d" * 40}
            ))
        self.assertTrue(any(error.code == "STALE_APPROVAL" for error in stale.exception.errors))

        approved = chugel.decide_gate(mid, "merge_authorization", _gate_decision(
            approved_for={"head_sha": sha}
        ))
        self.assertEqual(approved["human_gates"]["merge_authorization"]["status"], "approved")
        merged = chugel.transition(mid, "MERGING", actor="jose", reason="merge authorized")
        self.assertEqual(merged["state"], "MERGING")

    def test_merge_authorization_sin_identidad_publicada_sigue_rechazada(self):
        record = _mission_at_merge_awaiting_without_publish_identity()
        mid = record["mission_id"]

        with self.assertRaises(chugel.MissionValidationFailed) as rejected:
            chugel.decide_gate(mid, "merge_authorization", _gate_decision(
                approved_for={"head_sha": "c" * 40}
            ))

        self.assertTrue(any(error.code == "STALE_APPROVAL" for error in rejected.exception.errors))
        self.assertIsNone(chugel.get_mission(mid)["publish"]["commit_sha"])


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


class PruebaReautorizacionAtomicaDeScope(ChugelTestCase):
    def _prepare(self, *, state="INTAKE", proposal_id="p2"):
        mission = _create_intake_mission("scope atomico")
        mid = mission["mission_id"]
        chugel.decide_gate(
            mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}),
        )
        if state in {"SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED", "BUILDING"}:
            chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="ready")
        if state in {"AUTHORIZED", "BUILDING"}:
            chugel.transition(mid, "AUTHORIZED", actor="jose", reason="approved")
        if state == "BUILDING":
            chugel.record_repository_state(mid, {
                "worktree_path": "/tmp/synthetic", "branch": "local/test",
                "base_sha": "a" * 40, "isolation_confirmed": True,
            })
            chugel.transition(mid, "BUILDING", actor="chugel", reason="build")
        chugel.propose_scope_change(mid, _proposal(proposal_id=proposal_id))
        return mid

    def _scope_decision(self, status="accepted"):
        decision = {
            "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z",
            "status": status,
        }
        if status == "accepted":
            decision["mission_definition_entry"] = _mission_definition_entry()
        return decision

    def _invoke(self, mid, *, scope_status="accepted", gate_status="approved", version=2):
        return chugel.decide_scope_change_and_reauthorize(
            mid, "p2", self._scope_decision(scope_status),
            _gate_decision(status=gate_status, approved_for={"mission_definition_version": version}),
        )

    def test_happy_path_valida_y_escribe_exactamente_una_vez(self):
        mid = self._prepare(state="AUTHORIZED")
        pre_mutation = chugel.get_mission(mid)
        with mock.patch.object(chugel, "_read_mission_record", return_value=pre_mutation), \
             mock.patch.object(chugel, "validate_mission_record", wraps=chugel.validate_mission_record) as validate, \
             mock.patch.object(chugel, "_write_mission_record", wraps=chugel._write_mission_record) as write:
            updated = self._invoke(mid)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(write.call_count, 1)
        self.assertEqual([e["version"] for e in updated["mission_definition_history"]], [1, 2])
        self.assertEqual(updated["human_gates"]["scope_authorization"]["approved_for"], {
            "mission_definition_version": 2,
        })

    def test_status_scope_no_accepted_falla_antes_de_read_write(self):
        missing = object()
        for status in ("rejected", "pending_human_decision", None, True, False, "maybe", missing):
            decision = {"decided_by": "jose"}
            if status is not missing:
                decision["status"] = status
            with self.subTest(status=status), \
                 mock.patch.object(chugel, "_read_mission_record") as read, \
                 mock.patch.object(chugel, "_write_mission_record") as write:
                with self.assertRaises(ValueError):
                    chugel.decide_scope_change_and_reauthorize(
                        "not-even-read", "p2", decision,
                        _gate_decision(approved_for={"mission_definition_version": 2}),
                    )
                read.assert_not_called()
                write.assert_not_called()

    def test_status_gate_no_approved_falla_antes_de_read_write(self):
        missing = object()
        for status in ("rejected", "pending", "not_requested", None, True, False, "maybe", missing):
            gate = _gate_decision(status="rejected")
            if status is not missing:
                gate["status"] = status
            else:
                gate.pop("status")
            gate["decided_by"] = "jose"
            with self.subTest(status=status), \
                 mock.patch.object(chugel, "_read_mission_record") as read, \
                 mock.patch.object(chugel, "_write_mission_record") as write:
                with self.assertRaises(ValueError):
                    chugel.decide_scope_change_and_reauthorize(
                        "not-even-read", "p2", self._scope_decision(), gate,
                    )
                read.assert_not_called()
                write.assert_not_called()

    def test_version_gate_incorrecta_no_escribe(self):
        mid = self._prepare()
        before = chugel._mission_path(mid).read_bytes()
        with self.assertRaises(chugel.MissionValidationFailed):
            self._invoke(mid, version=1)
        self.assertEqual(chugel._mission_path(mid).read_bytes(), before)

    def test_solo_estados_pre_ejecucion_son_elegibles(self):
        for state in ("INTAKE", "SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED"):
            with self.subTest(state=state):
                mid = self._prepare(state=state)
                self.assertEqual(self._invoke(mid)["state"], state)

    def test_building_falla_sin_rebobinar_estado(self):
        mid = self._prepare(state="BUILDING")
        before = chugel._mission_path(mid).read_bytes()
        with self.assertRaises(ValueError):
            self._invoke(mid)
        self.assertEqual(chugel._mission_path(mid).read_bytes(), before)
        self.assertEqual(chugel.get_mission(mid)["state"], "BUILDING")

    def test_evidencia_builder_o_reviewer_impide_reautorizar_scope(self):
        for field, evidence in (
            ("builder_evidence", _builder_evidence(0)),
            ("reviewer_evidence", _reviewer_evidence(0)),
        ):
            with self.subTest(field=field):
                mid = self._prepare()
                record = chugel.get_mission(mid)
                record[field] = [evidence]
                chugel._write_mission_record(record)
                before = chugel._mission_path(mid).read_bytes()
                with self.assertRaises(ValueError):
                    self._invoke(mid)
                self.assertEqual(chugel._mission_path(mid).read_bytes(), before)

    def test_corrective_cycle_no_cero_impide_reautorizar_scope(self):
        mid = self._prepare()
        record = chugel.get_mission(mid)
        record["corrective_cycle_count"] = 1
        before = chugel._mission_path(mid).read_bytes()
        with mock.patch.object(chugel, "_read_mission_record", return_value=record), \
             mock.patch.object(chugel, "_write_mission_record") as write:
            with self.assertRaises(ValueError):
                self._invoke(mid)
            write.assert_not_called()
        self.assertEqual(chugel._mission_path(mid).read_bytes(), before)

    def test_helpers_y_operacion_no_mutan_inputs(self):
        mid = self._prepare()
        scope = self._scope_decision()
        gate = _gate_decision(approved_for={"mission_definition_version": 2})
        scope_before = json.loads(json.dumps(scope))
        gate_before = json.loads(json.dumps(gate))
        self.assertEqual(chugel.decide_scope_change_and_reauthorize(mid, "p2", scope, gate)["state"], "INTAKE")
        self.assertEqual(scope, scope_before)
        self.assertEqual(gate, gate_before)


class PruebaInmutabilidadYUnicidadDePropuestas(ChugelTestCase):
    def test_rechazo_standalone_legitimo_se_preserva(self):
        mission = _create_intake_mission("rechazo")
        mid = mission["mission_id"]
        chugel.propose_scope_change(mid, _proposal())
        updated = chugel.decide_scope_change(mid, "p1", {
            "decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z", "status": "rejected",
        })
        self.assertEqual(updated["proposed_scope_changes"][0]["status"], "rejected")

    def test_decisiones_terminales_no_se_pueden_reescribir_ni_repetir(self):
        for initial, later in (
            ("rejected", "accepted"), ("accepted", "rejected"),
            ("rejected", "rejected"), ("accepted", "accepted"),
        ):
            with self.subTest(initial=initial, later=later):
                mission = _create_intake_mission(f"{initial}-{later}")
                mid = mission["mission_id"]
                chugel.propose_scope_change(mid, _proposal())
                first = {"decided_by": "jose", "decided_at": "2026-08-19T12:30:00Z", "status": initial}
                if initial == "accepted":
                    first["mission_definition_entry"] = _mission_definition_entry()
                chugel.decide_scope_change(mid, "p1", first)
                before = chugel._mission_path(mid).read_bytes()
                second = {"decided_by": "jose", "decided_at": "2026-08-19T12:31:00Z", "status": later}
                if later == "accepted":
                    second["mission_definition_entry"] = _mission_definition_entry()
                with self.assertRaises(ValueError):
                    chugel.decide_scope_change(mid, "p1", second)
                self.assertEqual(chugel._mission_path(mid).read_bytes(), before)

    def test_helper_puro_rechaza_directamente_propuesta_terminal(self):
        mission = _create_intake_mission("helper-terminal")
        record = mission | {"proposed_scope_changes": [_proposal(status="rejected")]}
        decision = {
            "decided_by": "jose", "decided_at": "2026-08-19T12:31:00Z",
            "status": "accepted", "mission_definition_entry": _mission_definition_entry(),
        }
        with self.assertRaises(ValueError):
            chugel._apply_scope_change_decision(record, "p1", decision)

    def test_proposal_id_duplicado_falla_sin_append(self):
        mission = _create_intake_mission("duplicado")
        mid = mission["mission_id"]
        chugel.propose_scope_change(mid, _proposal(proposal_id="same"))
        before = chugel._mission_path(mid).read_bytes()
        with self.assertRaises(ValueError):
            chugel.propose_scope_change(mid, _proposal(proposal_id="same"))
        self.assertEqual(chugel._mission_path(mid).read_bytes(), before)


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

    def test_aprobacion_scope_malformada_no_se_persiste_ni_autoriza_transicion(self):
        mission = _create_intake_mission("algo")
        mid = mission["mission_id"]

        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.decide_gate(mid, "scope_authorization", _gate_decision(
                approved_for={"note": "context only"},
            ))

        preserved = chugel.get_mission(mid)
        self.assertEqual(preserved["human_gates"]["scope_authorization"], _gate_decision(
            status="not_requested",
        ))
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope drafted")
        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "AUTHORIZED", actor="jose", reason="invalid approval")
        with self.assertRaises(chugel.MissionTransitionRejected):
            chugel.transition(mid, "BUILDING", actor="chugel", reason="invalid approval")


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


# --- dispatch_ledger: reserva durable antes de despachar -----------------

def _persisted_builder_evidence(attempt=0, invocation_id="11111111-1111-4111-8111-111111111111",
                                 provider="codex", conversation_id="builder-thread"):
    """A builder_evidence entry carrying enough infrastructure identity for
    reserve_dispatch(role='emma', ...) to accept it -- see chugel.py's
    reserve_dispatch() Emma-independence precheck."""
    evidence = _builder_evidence(attempt=attempt)
    evidence.update({
        "invocation_id": invocation_id,
        "provider": provider,
        "provider_session_id": None,
        "provider_conversation_id": conversation_id,
    })
    return evidence


class DispatchLedgerTestCase(ChugelTestCase):
    """Shared fixtures reaching real BUILDING/REVIEWING states through the
    genuine lifecycle -- no shortcuts, exactly like WiringTestCase's own
    fixtures in tests/test_orchestrator_wiring.py."""

    def _mission_ready_for_emilio(self):
        m = _create_intake_mission("algo")
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree",
            "branch": "overnight/synthetic",
            "base_sha": "b" * 40,
            "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        chugel.decide_gate(mid, "scope_authorization",
            _gate_decision(approved_for={"mission_definition_version": 1}))
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="isolated build starts")
        return mid

    def _mission_ready_for_emma(self, attempt=0):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _persisted_builder_evidence(attempt=attempt))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")
        return mid


class PruebaReserveDispatchEligibilidad(DispatchLedgerTestCase):
    def test_reserva_exitosa_escribe_entrada_reserved(self):
        mid = self._mission_ready_for_emilio()
        record, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        self.assertTrue(invocation_id)
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 1)
        entry = ledger[0]
        self.assertEqual(entry["role"], "emilio")
        self.assertEqual(entry["attempt"], 0)
        self.assertEqual(entry["invocation_id"], invocation_id)
        self.assertEqual(entry["status"], "RESERVED")
        self.assertIsNone(entry["provider"])
        self.assertIsNone(entry["model"])
        self.assertIsNone(entry["result_classification"])
        # Persisted -- not just returned in memory.
        self.assertEqual(chugel.get_mission(mid)["dispatch_ledger"], ledger)

    def test_estado_incorrecto_para_role_attempt_falla_sin_escribir(self):
        mid = self._mission_ready_for_emilio()  # BUILDING, not CORRECTING
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=1)
        self.assertEqual(chugel.get_mission(mid)["dispatch_ledger"], [])

    def test_emma_fuera_de_reviewing_falla(self):
        mid = self._mission_ready_for_emilio()  # BUILDING, not REVIEWING
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emma", attempt=0)

    def test_evidencia_ya_persistida_para_ese_attempt_falla(self):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        # Still BUILDING (state itself is eligible) -- refused because
        # builder_evidence already carries an attempt=0 entry.
        self.assertEqual(chugel.get_mission(mid)["state"], "BUILDING")
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)

    def test_role_invalido_lanza_value_error_sin_tocar_disco(self):
        mid = self._mission_ready_for_emilio()
        with self.assertRaises(ValueError):
            chugel.reserve_dispatch(mid, role="david", attempt=0)
        self.assertEqual(chugel.get_mission(mid)["dispatch_ledger"], [])

    def test_attempt_bool_o_fuera_de_rango_lanza_value_error(self):
        mid = self._mission_ready_for_emilio()
        for bad_attempt in (True, False, None, -1, 2, "0"):
            with self.assertRaises(ValueError, msg=repr(bad_attempt)):
                chugel.reserve_dispatch(mid, role="emilio", attempt=bad_attempt)
        self.assertEqual(chugel.get_mission(mid)["dispatch_ledger"], [])

    def test_emma_sin_identidad_persistida_en_builder_evidence_falla(self):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))  # no persisted identity
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="x")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="x")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="x")
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emma", attempt=0)
        self.assertEqual(chugel.get_mission(mid)["dispatch_ledger"], [])

    def test_emma_con_identidad_persistida_reserva_exitosamente(self):
        mid = self._mission_ready_for_emma(attempt=0)
        record, invocation_id = chugel.reserve_dispatch(mid, role="emma", attempt=0)
        self.assertTrue(invocation_id)
        self.assertEqual(len(record["dispatch_ledger"]), 1)


class PruebaReserveDispatchDuplicadoYConflicto(DispatchLedgerTestCase):
    def test_reserva_duplicada_mientras_la_primera_sigue_viva_falla(self):
        mid = self._mission_ready_for_emilio()
        chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        self.assertEqual(len(chugel.get_mission(mid)["dispatch_ledger"]), 1)

    def test_reserva_conflictiva_tras_in_flight_sin_resultado_falla(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)

    def test_invalid_output_ya_no_bloquea_redespacho(self):
        """Verification Hardening V1, Pillar 1 corrective: invalid_output
        writes no evidence -- identical to failed/timeout/unavailable in
        that respect -- so it was moved into DISPATCH_RETRYABLE_
        CLASSIFICATIONS alongside them (see orchestrator/validator.py's
        own extensive docstring on this constant for the full history:
        before this corrective, a crash between record_dispatch_result()
        and finalize_dispatch() for an invalid_output outcome left the
        mission PERMANENTLY stuck, confirmed by direct reproduction).
        `completed` remains the one classification this can never apply
        to -- see test_resultado_completed_no_finalizado_bloquea_redespacho
        below, unchanged."""
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="invalid_output")
        chugel.reserve_dispatch(mid, role="emilio", attempt=0)  # must not raise

    def test_resultado_completed_no_finalizado_bloquea_redespacho(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        # Deliberately not finalized here -- record_builder_evidence() is
        # the only path that finalizes a "completed" entry.
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)

    def test_resultado_reintentable_permite_reserva_fresca_y_supera_la_previa(self):
        mid = self._mission_ready_for_emilio()
        _, first_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, first_id, provider="codex")
        chugel.record_dispatch_result(mid, first_id, outcome="timeout")

        record, second_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        self.assertNotEqual(first_id, second_id)
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 2)
        first_entry = next(e for e in ledger if e["invocation_id"] == first_id)
        second_entry = next(e for e in ledger if e["invocation_id"] == second_id)
        self.assertEqual(first_entry["status"], "FINALIZED")
        self.assertEqual(second_entry["status"], "RESERVED")

    def test_las_cuatro_clasificaciones_reintentables_permiten_redespacho(self):
        for outcome in ("failed", "timeout", "unavailable", "invalid_output"):
            mid = self._mission_ready_for_emilio()
            _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
            chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
            chugel.record_dispatch_result(mid, invocation_id, outcome=outcome)
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)  # must not raise

    def test_completed_no_se_puede_finalizar_sin_evidencia_o_rechazo_explicito(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.finalize_dispatch(mid, invocation_id)
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emilio", attempt=0)


class PruebaLedgerLifecycleTransiciones(DispatchLedgerTestCase):
    def test_mark_in_flight_requiere_reserved(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex", model="codex-1")
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")

    def test_mark_in_flight_id_desconocido_falla(self):
        mid = self._mission_ready_for_emilio()
        chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.mark_dispatch_in_flight(mid, "no-such-id", provider="codex")

    def test_record_result_requiere_in_flight(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.record_dispatch_result(mid, invocation_id, outcome="completed")

    def test_record_result_dos_veces_falla_la_segunda(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="failed")
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.record_dispatch_result(mid, invocation_id, outcome="failed")

    def test_finalize_requiere_result_recorded(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.finalize_dispatch(mid, invocation_id)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.finalize_dispatch(mid, invocation_id)

    def test_finalize_dos_veces_falla_la_segunda(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="failed")
        chugel.finalize_dispatch(mid, invocation_id)
        with self.assertRaises(chugel.DispatchEntryNotFound):
            chugel.finalize_dispatch(mid, invocation_id)


class PruebaDiagnosticoEstructuradoDeDispatch(DispatchLedgerTestCase):
    """Structured Allow-Listed Diagnostics -- persistence, eligibility,
    and schema enforcement of record_dispatch_result()'s `diagnostic`
    parameter. This design replaced an abandoned free-text
    `diagnostic_detail` + regex-redaction approach (three review rounds
    each found a new secret shape slipping through); these tests prove
    both that real diagnosis-relevant structure survives durably AND
    that nothing outside the schema's closed enum/typed fields can ever
    be persisted -- there is no sanitizer left to test, only the shape
    itself, enforced the same way every other Mission Record field is."""

    def _reserved_in_flight(self, provider="codex"):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider=provider)
        return mid, invocation_id

    def _entry(self, mid, invocation_id):
        record = chugel.get_mission(mid)
        for entry in record["dispatch_ledger"]:
            if entry["invocation_id"] == invocation_id:
                return entry
        raise AssertionError(f"no ledger entry for {invocation_id!r}")

    # --- durable persistence for each eligible outcome ---------------

    def test_invalid_output_persiste_diagnostic_estructurado(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(
            mid, invocation_id, outcome="invalid_output",
            diagnostic={
                "reason_code": "INVALID_OUTPUT_MALFORMED_JSON",
                "exception_type": "JSONDecodeError",
                "json_decode_error_position": 640,
                "output_byte_length": 812,
            },
        )
        entry = self._entry(mid, invocation_id)
        self.assertEqual(entry["diagnostic"]["reason_code"], "INVALID_OUTPUT_MALFORMED_JSON")
        self.assertEqual(entry["diagnostic"]["json_decode_error_position"], 640)
        self.assertEqual(entry["diagnostic"]["output_byte_length"], 812)

    def test_failed_persiste_diagnostic_estructurado(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(
            mid, invocation_id, outcome="failed",
            diagnostic={"reason_code": "FAILED_NONZERO_EXIT", "exit_code": 1, "stderr_byte_length": 0},
        )
        entry = self._entry(mid, invocation_id)
        self.assertEqual(entry["diagnostic"]["reason_code"], "FAILED_NONZERO_EXIT")
        self.assertEqual(entry["diagnostic"]["exit_code"], 1)

    def test_timeout_persiste_diagnostic_estructurado(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(
            mid, invocation_id, outcome="timeout",
            diagnostic={"reason_code": "TIMEOUT_EXCEEDED", "timeout_seconds": 600.0},
        )
        entry = self._entry(mid, invocation_id)
        self.assertEqual(entry["diagnostic"]["reason_code"], "TIMEOUT_EXCEEDED")
        self.assertEqual(entry["diagnostic"]["timeout_seconds"], 600.0)

    def test_unknown_es_un_reason_code_valido_fail_closed(self):
        """The catch-all for a real future failure branch that hasn't
        been given its own reason_code yet -- still zero free text."""
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(
            mid, invocation_id, outcome="failed",
            diagnostic={"reason_code": "UNKNOWN"},
        )
        entry = self._entry(mid, invocation_id)
        self.assertEqual(entry["diagnostic"]["reason_code"], "UNKNOWN")

    # --- eligibility: only failed/timeout/invalid_output ---------------

    def test_completed_nunca_persiste_diagnostic(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(
            mid, invocation_id, outcome="completed",
            diagnostic={"reason_code": "UNKNOWN"},
        )
        entry = self._entry(mid, invocation_id)
        self.assertNotIn("diagnostic", entry)

    def test_diagnostic_none_no_agrega_la_clave(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(mid, invocation_id, outcome="invalid_output")
        entry = self._entry(mid, invocation_id)
        self.assertNotIn("diagnostic", entry)

    def test_diagnostic_vacio_no_agrega_la_clave(self):
        mid, invocation_id = self._reserved_in_flight()
        chugel.record_dispatch_result(mid, invocation_id, outcome="invalid_output", diagnostic={})
        entry = self._entry(mid, invocation_id)
        self.assertNotIn("diagnostic", entry)

    # --- impossibility of persisting non-allow-listed fields/text ------

    def test_texto_arbitrario_en_reason_code_es_rechazado_por_el_schema(self):
        """A reason_code outside the closed enum -- e.g. an adapter bug
        that tried to put a raw error string directly into reason_code --
        must be rejected before anything is written to disk."""
        mid, invocation_id = self._reserved_in_flight()
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_dispatch_result(
                mid, invocation_id, outcome="failed",
                diagnostic={"reason_code": "Authorization: Bearer sk-live-shouldnotpersist"},
            )
        record = chugel.get_mission(mid)
        entry = next(e for e in record["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertEqual(entry["status"], "IN_FLIGHT")
        self.assertNotIn("diagnostic", entry)

    def test_campo_no_allow_listed_es_rechazado_por_additional_properties(self):
        """additionalProperties: false on the diagnostic object itself --
        an adapter cannot smuggle an arbitrary extra key (e.g. a raw
        'stderr_excerpt' string) through, even alongside an otherwise
        valid reason_code."""
        mid, invocation_id = self._reserved_in_flight()
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_dispatch_result(
                mid, invocation_id, outcome="failed",
                diagnostic={
                    "reason_code": "FAILED_NONZERO_EXIT",
                    "exit_code": 1,
                    "stderr_excerpt": "this free-text field does not exist in the schema",
                },
            )
        record = chugel.get_mission(mid)
        entry = next(e for e in record["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertNotIn("diagnostic", entry)

    def test_valor_de_texto_libre_en_exception_type_es_rechazado(self):
        """exception_type is pattern-constrained to look like a bare
        Python class name -- a full exception message (which could carry
        arbitrary interpolated data) does not match that pattern and must
        be rejected, not silently truncated or accepted."""
        mid, invocation_id = self._reserved_in_flight()
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_dispatch_result(
                mid, invocation_id, outcome="invalid_output",
                diagnostic={
                    "reason_code": "INVALID_OUTPUT_UNREADABLE_FILE",
                    "exception_type": "OSError: [Errno 13] Permission denied: '/secret/path'",
                },
            )

    def test_reason_code_de_timeout_exige_result_classification_timeout(self):
        """Cross-field allOf: a TIMEOUT_EXCEEDED reason_code paired with
        result_classification=failed must be rejected -- reason_code and
        result_classification cannot silently disagree."""
        mid, invocation_id = self._reserved_in_flight()
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_dispatch_result(
                mid, invocation_id, outcome="failed",
                diagnostic={"reason_code": "TIMEOUT_EXCEEDED", "timeout_seconds": 60.0},
            )

    def test_reason_code_de_invalid_output_exige_result_classification_invalid_output(self):
        mid, invocation_id = self._reserved_in_flight()
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_dispatch_result(
                mid, invocation_id, outcome="timeout",
                diagnostic={"reason_code": "INVALID_OUTPUT_NOT_JSON_OBJECT"},
            )

    # --- direct schema tests (independent of chugel.py's own gate) -----

    def _mutated_with_entry(self, mid, invocation_id, **entry_overrides):
        record = chugel.get_mission(mid)
        ledger = list(record["dispatch_ledger"])
        idx = next(i for i, e in enumerate(ledger) if e["invocation_id"] == invocation_id)
        entry = dict(ledger[idx])
        entry.update(entry_overrides)
        ledger[idx] = entry
        mutated = dict(record)
        mutated["dispatch_ledger"] = ledger
        return mutated

    def test_schema_rechaza_diagnostic_junto_a_completed(self):
        mid, invocation_id = self._reserved_in_flight()
        mutated = self._mutated_with_entry(
            mid, invocation_id,
            status="RESULT_RECORDED", result_classification="completed",
            diagnostic={"reason_code": "UNKNOWN"},
        )
        result = validator.validate_mission_record(mutated)
        self.assertFalse(result.valid)

    def test_schema_exige_result_classification_cuando_hay_diagnostic(self):
        mid, invocation_id = self._reserved_in_flight()
        record = chugel.get_mission(mid)
        ledger = list(record["dispatch_ledger"])
        idx = next(i for i, e in enumerate(ledger) if e["invocation_id"] == invocation_id)
        entry = dict(ledger[idx])
        entry["status"] = "RESULT_RECORDED"
        entry["diagnostic"] = {"reason_code": "UNKNOWN"}
        del entry["result_classification"]
        ledger[idx] = entry
        mutated = dict(record)
        mutated["dispatch_ledger"] = ledger
        result = validator.validate_mission_record(mutated)
        self.assertFalse(result.valid)

    def test_schema_acepta_diagnostic_completo_para_cada_reason_code_conocido(self):
        """Positive-case coverage for every reason_code this corrective
        introduces, paired with its correct result_classification --
        guards against a future over-tightening of the allOf silently
        breaking the happy path for any one of them."""
        cases = [
            ("timeout", {"reason_code": "TIMEOUT_EXCEEDED", "timeout_seconds": 600.0}),
            ("failed", {"reason_code": "FAILED_NONZERO_EXIT", "exit_code": 1, "stderr_byte_length": 0}),
            ("failed", {"reason_code": "FAILED_UNEXPECTED_EXCEPTION", "exception_type": "RuntimeError"}),
            ("invalid_output", {"reason_code": "INVALID_OUTPUT_NO_OUTPUT_FILE", "output_file_present": False}),
            ("invalid_output", {
                "reason_code": "INVALID_OUTPUT_UNREADABLE_FILE",
                "output_file_present": True, "exception_type": "OSError",
            }),
            ("invalid_output", {
                "reason_code": "INVALID_OUTPUT_MALFORMED_JSON",
                "exception_type": "JSONDecodeError", "json_decode_error_position": 12,
                "output_byte_length": 40,
            }),
            ("invalid_output", {"reason_code": "INVALID_OUTPUT_NOT_JSON_OBJECT", "output_byte_length": 5}),
            ("invalid_output", {"reason_code": "INVALID_OUTPUT_UNRECOGNIZED_RESULT_SHAPE"}),
            ("invalid_output", {"reason_code": "INVALID_OUTPUT_VERDICT_SEVERITY_MISMATCH"}),
            ("invalid_output", {
                "reason_code": "INVALID_OUTPUT_ARTIFACT_COMPUTATION_FAILED",
                "artifact_failure_reason": "NO_UNCOMMITTED_CHANGE",
            }),
            ("invalid_output", {
                "reason_code": "INVALID_OUTPUT_ARTIFACT_COMPUTATION_FAILED",
                "artifact_failure_reason": "GIT_OPERATION_REFUSED",
            }),
            ("invalid_output", {
                "reason_code": "INVALID_OUTPUT_ARTIFACT_COMPUTATION_FAILED",
                "artifact_failure_reason": "OTHER",
            }),
            ("failed", {"reason_code": "UNKNOWN"}),
            ("timeout", {"reason_code": "UNKNOWN"}),
            ("invalid_output", {"reason_code": "UNKNOWN"}),
        ]
        for result_classification, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                mid, invocation_id = self._reserved_in_flight()
                mutated = self._mutated_with_entry(
                    mid, invocation_id,
                    status="RESULT_RECORDED", result_classification=result_classification,
                    diagnostic=diagnostic,
                )
                result = validator.validate_mission_record(mutated)
                self.assertTrue(result.valid, (diagnostic, result.errors))


class PruebaEvidenciaFinalizaLedgerAtomicamente(DispatchLedgerTestCase):
    def test_record_builder_evidence_finaliza_entrada_result_recorded_coincidente(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        evidence = _builder_evidence(attempt=0)
        evidence["invocation_id"] = invocation_id
        evidence["provider"] = "codex"
        evidence["provider_conversation_id"] = "thread-1"
        record = chugel.record_builder_evidence(mid, evidence)
        entry = next(e for e in record["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertEqual(entry["status"], "FINALIZED")

    def test_record_builder_evidence_sin_invocation_id_no_toca_el_ledger(self):
        mid = self._mission_ready_for_emilio()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        # Evidence written for the same attempt but with no matching
        # invocation_id (a caller that never went through reserve_dispatch,
        # or a mismatched id) must leave the RESERVED entry untouched --
        # _finalize_ledger_entry_for_evidence() only ever finalizes an
        # exact invocation_id match.
        record = chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        entry = next(e for e in record["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertEqual(entry["status"], "RESERVED")

    def test_record_reviewer_evidence_finaliza_entrada_result_recorded_coincidente(self):
        mid = self._mission_ready_for_emma(attempt=0)
        _, invocation_id = chugel.reserve_dispatch(mid, role="emma", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="claude")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        evidence = _reviewer_evidence(attempt=0, verdict="PASS")
        evidence["invocation_id"] = invocation_id
        record = chugel.record_reviewer_evidence(mid, evidence)
        entry = next(e for e in record["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertEqual(entry["status"], "FINALIZED")

    def test_validacion_fallida_no_deja_escritura_parcial(self):
        mid = self._mission_ready_for_emilio()
        before = chugel.get_mission(mid)
        with self.assertRaises(chugel.MissionValidationFailed):
            chugel.record_builder_evidence(mid, {"attempt": 0})  # missing required fields
        after = chugel.get_mission(mid)
        self.assertEqual(before, after)


class PruebaRechazoExplicitoDeEvidencia(DispatchLedgerTestCase):
    CODE = "MISSION_EVIDENCE_VALIDATION_FAILED"

    def _completed_emma(self, attempt=0):
        mid = self._mission_ready_for_emma(attempt=attempt)
        _, invocation_id = chugel.reserve_dispatch(mid, role="emma", attempt=attempt)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="claude")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        return mid, invocation_id

    def test_rechazo_es_atomico_preserva_completed_y_habilita_id_nuevo(self):
        mid, first_id = self._completed_emma()
        before_count = len(chugel.get_mission(mid)["dispatch_ledger"])
        rejected = chugel.record_evidence_rejection(
            mid, first_id, role="emma", attempt=0, rejection_code=self.CODE
        )
        first = next(e for e in rejected["dispatch_ledger"] if e["invocation_id"] == first_id)
        self.assertEqual(first["status"], "FINALIZED")
        self.assertEqual(first["result_classification"], "completed")
        self.assertEqual(first["evidence_disposition"], "rejected")
        self.assertEqual(first["evidence_rejection_code"], self.CODE)
        self.assertEqual(rejected["reviewer_evidence"], [])

        fresh, second_id = chugel.reserve_dispatch(mid, role="emma", attempt=0)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(fresh["dispatch_ledger"]), before_count + 1)

    def test_ausencia_de_evidencia_no_infiere_rechazo(self):
        mid, invocation_id = self._completed_emma()
        current = chugel.get_mission(mid)
        entry = next(e for e in current["dispatch_ledger"] if e["invocation_id"] == invocation_id)
        self.assertNotIn("evidence_disposition", entry)
        with self.assertRaises(chugel.DispatchNotEligible):
            chugel.reserve_dispatch(mid, role="emma", attempt=0)

    def test_rechazo_falla_cerrado_para_identidad_estado_y_resultado_incorrectos(self):
        mid, invocation_id = self._completed_emma()
        before = chugel._mission_path(mid).read_bytes()
        bad_calls = (
            dict(invocation_id=str(uuid.uuid4()), role="emma", attempt=0),
            dict(invocation_id=invocation_id, role="emilio", attempt=0),
            dict(invocation_id=invocation_id, role="emma", attempt=1),
        )
        for call in bad_calls:
            with self.assertRaises(chugel.EvidenceRejectionNotEligible):
                chugel.record_evidence_rejection(mid, rejection_code=self.CODE, **call)
            self.assertEqual(chugel._mission_path(mid).read_bytes(), before)

    def test_rechazo_no_puede_coexistir_con_evidencia_persistida(self):
        mid = self._mission_ready_for_emma(attempt=0)
        _, invocation_id = chugel.reserve_dispatch(mid, role="emma", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="claude")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")
        evidence = _reviewer_evidence(attempt=0, verdict="PASS")
        evidence["invocation_id"] = invocation_id
        chugel.record_reviewer_evidence(mid, evidence)
        with self.assertRaises(chugel.EvidenceRejectionNotEligible):
            chugel.record_evidence_rejection(
                mid, invocation_id, role="emma", attempt=0, rejection_code=self.CODE
            )

    def test_codigo_no_allow_listado_falla_sin_escritura(self):
        mid, invocation_id = self._completed_emma()
        before = chugel._mission_path(mid).read_bytes()
        with self.assertRaises(ValueError):
            chugel.record_evidence_rejection(
                mid, invocation_id, role="emma", attempt=0, rejection_code="RAW_PROVIDER_TEXT"
            )
        self.assertEqual(chugel._mission_path(mid).read_bytes(), before)


class PruebaConcurrenciaReservaCrossProceso(DispatchLedgerTestCase):
    """A genuine multi-thread test exercising the real fcntl.flock()
    exclusion inside _mission_lock() -- each thread performs its own
    os.open() of the lock file (mirroring what two independent OS
    processes would each do), so this exercises real kernel-level mutual
    exclusion, not merely Python-level thread scheduling."""

    def test_dos_hilos_compitiendo_por_la_misma_reserva_solo_uno_gana(self):
        import threading

        mid = self._mission_ready_for_emilio()
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def attempt_reserve():
            barrier.wait()
            try:
                _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
                results.append(invocation_id)
            except chugel.DispatchNotEligible as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt_reserve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, "exactly one thread must win the reservation")
        self.assertEqual(len(errors), 1, "the other thread must fail closed, not silently skip")
        ledger = chugel.get_mission(mid)["dispatch_ledger"]
        self.assertEqual(len(ledger), 1, "no lost update -- only one entry was ever written")
        self.assertEqual(ledger[0]["invocation_id"], results[0])


# --- generalized lock: reservation vs. unrelated concurrent mutation -----

class PruebaBloqueoGeneralizadoCrossProceso(DispatchLedgerTestCase):
    """Emma's P2-1 finding (autonomous-runner P2 hardening cycle): the
    original per-mission lock only serialized reserve_dispatch() against
    other reserve_dispatch() calls -- a concurrent, unrelated mutation
    (decide_gate(), transition(), record_repository_state(), etc.) on the
    same mission raced entirely outside any lock, and could silently lose
    either write (a lost update) since every mutator's own
    read-modify-write cycle used the same _read_mission_record() ->
    compute -> _write_mission_record() shape with no serialization
    between DIFFERENT mutators.

    This test spawns two genuinely separate OS processes -- not threads
    in this process -- one calling reserve_dispatch(), the other calling
    the completely unrelated record_repository_state(), against the same
    mission at the same time, synchronized to maximize overlap. Both
    mutations must survive: this is the real regression test proving a
    reservation cannot be silently lost through a concurrent unrelated
    Chugel mutation, now that every public mutator acquires the same
    generalized _mission_lock()."""

    _WORKER = str(Path(__file__).resolve().parent / "_chugel_cross_process_race_worker.py")
    _REPO_ROOT = str(Path(__file__).resolve().parent.parent)

    def _run_race(self, mid, missions_dir, barrier_prefix):
        for suffix in (".0", ".1"):
            Path(barrier_prefix + suffix).unlink(missing_ok=True)
        p_reserve = subprocess.Popen(
            [sys.executable, self._WORKER, self._REPO_ROOT, str(missions_dir),
             mid, "reserve", barrier_prefix, "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        p_mutate = subprocess.Popen(
            [sys.executable, self._WORKER, self._REPO_ROOT, str(missions_dir),
             mid, "mutate", barrier_prefix, "1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        out_reserve, _ = p_reserve.communicate(timeout=10)
        out_mutate, _ = p_mutate.communicate(timeout=10)
        for suffix in (".0", ".1"):
            Path(barrier_prefix + suffix).unlink(missing_ok=True)
        return out_reserve.strip(), out_mutate.strip()

    def test_reserva_y_mutacion_no_relacionada_concurrentes_no_pierden_ninguna_escritura(self):
        mid = self._mission_ready_for_emilio()
        missions_dir = chugel._MISSIONS_DIR
        barrier_prefix = str(Path(tempfile.gettempdir()) / f"chugel_race_{mid}")

        out_reserve, out_mutate = self._run_race(mid, missions_dir, barrier_prefix)

        self.assertTrue(out_reserve.startswith("OK "), out_reserve)
        self.assertTrue(out_mutate.startswith("OK "), out_mutate)

        record = chugel.get_mission(mid)
        # The reservation was not silently lost -- the mutate side's write
        # (record_repository_state(), a completely different top-level
        # field) did not race in between reserve_dispatch()'s read and
        # write and overwrite it with a pre-reservation copy of the record.
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 1, ledger)
        self.assertEqual(ledger[0]["role"], "emilio")
        self.assertEqual(ledger[0]["attempt"], 0)
        self.assertEqual(ledger[0]["status"], "RESERVED")
        # The unrelated mutation was not silently lost either -- the
        # reservation's write did not race in and overwrite it with a
        # pre-mutation copy of the record.
        self.assertTrue(record["repository"]["worktree_path"].startswith("/tmp/race-worktree-"))
        self.assertEqual(record["repository"]["branch"].split("/")[0], "race")

    def test_diez_rondas_repetidas_nunca_pierden_una_escritura(self):
        """A handful of repeated rounds against fresh missions, to guard
        against a race window narrow enough that a single round could
        pass by luck even with the lock generalization reverted."""
        for i in range(5):
            mid = self._mission_ready_for_emilio()
            missions_dir = chugel._MISSIONS_DIR
            barrier_prefix = str(Path(tempfile.gettempdir()) / f"chugel_race_round_{i}_{mid}")
            out_reserve, out_mutate = self._run_race(mid, missions_dir, barrier_prefix)
            self.assertTrue(out_reserve.startswith("OK "), (i, out_reserve))
            self.assertTrue(out_mutate.startswith("OK "), (i, out_mutate))
            record = chugel.get_mission(mid)
            self.assertEqual(len(record["dispatch_ledger"]), 1, (i, record["dispatch_ledger"]))
            self.assertTrue(record["repository"]["worktree_path"].startswith("/tmp/race-worktree-"), i)


# --- M3: cross-mission merge serialization lock ---------------------------

class PruebaBloqueoMergeCrossProceso(ChugelTestCase):
    """M3: chugel.merge_serialization_lock() -- a single, global (not
    per-mission) lock. Thread-based mutual-exclusion proof mirrors
    PruebaConcurrenciaReservaCrossProceso above (each thread performs its
    own os.open() of the lock file, exercising real kernel-level
    fcntl.flock() exclusion, not merely Python thread scheduling);
    crash-safety is proven with a genuine separate OS process, killed
    with SIGKILL while holding the lock."""

    def test_dos_hilos_compitiendo_nunca_se_superponen(self):
        import threading
        import time

        overlaps = []
        order = []
        active = {"count": 0}
        active_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def hold_briefly(label):
            barrier.wait()
            with chugel.merge_serialization_lock():
                with active_lock:
                    active["count"] += 1
                    if active["count"] > 1:
                        overlaps.append(label)
                order.append(("enter", label))
                time.sleep(0.05)
                order.append(("exit", label))
                with active_lock:
                    active["count"] -= 1

        threads = [threading.Thread(target=hold_briefly, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(overlaps, [], "two holders were inside the lock at the same time")
        # Each holder's own enter/exit pair is contiguous -- proves the
        # second thread's os.open()+flock() genuinely blocked until the
        # first released, rather than both merely happening to finish
        # without overlapping by luck.
        first_label = order[0][1]
        second_label = 1 - first_label
        self.assertEqual(order[0], ("enter", first_label))
        self.assertEqual(order[1], ("exit", first_label))
        self.assertEqual(order[2], ("enter", second_label))
        self.assertEqual(order[3], ("exit", second_label))

    def test_kernel_libera_el_lock_si_el_proceso_titular_muere(self):
        """A real, separate OS process acquires the lock, signals that it
        holds it, then is SIGKILLed while still holding it -- simulating
        a genuine crash, not a clean release. A fresh acquire in THIS
        process must then succeed promptly (the flock is not left held
        forever by the dead process), proving the fail-closed guarantee
        chugel.merge_serialization_lock()'s own docstring claims."""
        missions_dir = chugel._MISSIONS_DIR
        repo_root = str(Path(__file__).resolve().parent.parent)
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "import orchestrator.chugel as chugel\n"
            "from pathlib import Path\n"
            "chugel._MISSIONS_DIR = Path(%r)\n"
            "with chugel.merge_serialization_lock():\n"
            "    print('HOLDING', flush=True)\n"
            "    import time; time.sleep(30)\n"
        ) % (repo_root, str(missions_dir))

        holder = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            line = holder.stdout.readline()
            self.assertEqual(line.strip(), "HOLDING", "holder process never confirmed acquiring the lock")

            holder.kill()  # SIGKILL -- no chance to run any cleanup/finally block
            holder.wait(timeout=5)

            # Re-acquire directly in this process (a plain, bounded call
            # is sufficient proof; if the kernel had NOT released the
            # dead holder's flock, this call would hang here instead of
            # returning, and the test would time out rather than pass).
            with chugel.merge_serialization_lock():
                pass
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)
            if holder.stdout is not None:
                holder.stdout.close()


if __name__ == "__main__":
    unittest.main()
