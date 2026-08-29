"""Pruebas para orchestrator/validator.py -- el núcleo determinístico de
Chugel (Zentra Autonomous Engineering V1, Level 2, Increment #4).

No hay LLM, red, subprocess ni I/O involucrado en absoluto: estas pruebas
ejercitan únicamente validate_mission_record(), la única función pública
que importa para el contrato de este módulo -- no los helpers privados
(_check_*), que son detalle de implementación.

Los fixtures de abajo construyen Mission Records completos a mano (no un
JSON Schema real -- ver orchestrator/validator.py, "Scope note", sobre por
qué la validación estructural completa es responsabilidad de un futuro
incremento) para poder probar exactamente los invariantes cruzados que
orchestrator/MISSION_RECORD.md y la revisión de Emma en Increment #3
identificaron como fuera del alcance de JSON Schema."""

import copy
import unittest

from orchestrator.validator import validate_mission_record


ARTIFACT_A = {"mode": "commit", "commit_sha": "b" * 40, "patch_path": None, "patch_sha256": None, "patch_byte_size": None}
ARTIFACT_B = {"mode": "commit", "commit_sha": "c" * 40, "patch_path": None, "patch_sha256": None, "patch_byte_size": None}


def _not_requested_gate():
    return {"status": "not_requested", "requested_at": None, "decided_at": None,
            "decided_by": None, "decision_ref": None, "approved_for": None}


def _minimal_intake_record():
    """La misión más chica posible: recién creada, en INTAKE, sin ninguna
    otra evidencia todavía -- debe ser válida igual."""
    return {
        "schema_version": "1.0.0",
        "mission_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "created_at": "2026-08-19T12:00:00Z",
        "updated_at": "2026-08-19T12:00:00Z",
        "state": "INTAKE",
        "state_reason": "fresh mission",
        "state_history": [
            {"from_state": None, "to_state": "INTAKE", "at": "2026-08-19T12:00:00Z", "actor": "jose", "reason": "new idea"}
        ],
        "intent": {"raw_text": "test idea", "captured_at": "2026-08-19T12:00:00Z"},
        "mission_definition_history": [],
        "proposed_scope_changes": [],
        "human_gates": {
            "scope_authorization": _not_requested_gate(),
            "publish_authorization": _not_requested_gate(),
            "merge_authorization": _not_requested_gate(),
        },
        "repository": {"worktree_path": "/tmp/w", "branch": "pending", "base_sha": "0" * 40, "isolation_confirmed": False},
        "builder_evidence": [],
        "reviewer_evidence": [],
        "corrective_cycle_count": 0,
        "publish": {"commit_sha": None, "pushed_at": None, "pr_url": None, "pr_number": None, "ci_runs": []},
        "merge": {"merge_commit_sha": None, "merged_at": None},
        "deploy": {
            "expected_sha": None, "deploy_confirmed_at": None,
            "health_check": {"checked_at": None, "status_code": None, "body_summary": None},
            "version_check": {"checked_at": None, "status_code": None, "body_summary": None},
        },
        "budget": {
            "configured": None, "consumed": {"unit": "tokens", "amount": 0},
            "per_agent_consumed": {"david": None, "emilio": None, "emma": None}, "exhausted": False,
        },
    }


def _mission_definition_entry(version=1, source="david_intake", based_on=None):
    return {
        "version": version, "outcome": "build a thing", "scope": ["api/thing.py"],
        "non_goals": [], "acceptance_criteria": ["thing works"],
        "source": source, "based_on_proposal_id": based_on,
        "authorized_by": "jose", "authorized_at": "2026-08-19T12:10:00Z",
        "authorization_decision_ref": "audit:scope-1",
    }


def _builder_entry(attempt=0, artifact=None):
    return {
        "attempt": attempt, "invoked_at": "2026-08-19T12:11:00Z",
        "artifact": artifact or ARTIFACT_A,
        "changed_files": [{"path": "api/thing.py", "reason": "implement"}],
        "checks": [{"command": "python3 -m unittest", "working_directory": "/repo", "exit_status": 0, "result": "OK"}],
        "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "git revert",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True, "no_remote_action": True,
            "no_production_access": True, "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": None,
        "conclusion": {"text": "done", "label": "INFERENCE"},
    }


def _reviewer_entry(attempt=0, verdict="PASS", start=None, before=None, findings=None):
    return {
        "attempt": attempt, "invoked_at": "2026-08-19T12:42:00Z",
        "artifact_identity_confirmed_at_start": start or ARTIFACT_A,
        "artifact_identity_confirmed_before_conclusion": before or ARTIFACT_A,
        "rechecked_commands": [], "findings": findings or [], "verdict": verdict,
        "blocked_reason": "blocked for testing" if verdict == "BLOCKED" else None,
    }


def _approved_gate(approved_for):
    return {
        "status": "approved", "requested_at": "2026-08-19T12:05:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
        "decision_ref": "audit:decision-1", "approved_for": approved_for,
    }


def _authorized_record():
    """AUTHORIZED: scope approved, worktree not yet created."""
    record = _minimal_intake_record()
    record["state"] = "AUTHORIZED"
    record["state_history"].append(
        {"from_state": "INTAKE", "to_state": "SCOPE_AWAITING_AUTHORIZATION", "at": "2026-08-19T12:05:00Z", "actor": "david", "reason": "drafted"}
    )
    record["state_history"].append(
        {"from_state": "SCOPE_AWAITING_AUTHORIZATION", "to_state": "AUTHORIZED", "at": "2026-08-19T12:10:00Z", "actor": "jose", "reason": "approved"}
    )
    record["state_reason"] = "scope approved"
    record["mission_definition_history"] = [_mission_definition_entry()]
    record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 1})
    return record


def _built_and_reviewed_pass_record():
    """A full legal happy path: AUTHORIZED -> BUILDING -> VERIFYING ->
    AWAITING_REVIEW -> REVIEWING -> PUBLISH_AWAITING_AUTHORIZATION, verdict PASS."""
    record = _authorized_record()
    record["repository"]["isolation_confirmed"] = True
    for from_s, to_s, actor, reason in [
        ("AUTHORIZED", "BUILDING", "chugel", "worktree created"),
        ("BUILDING", "VERIFYING", "chugel", "handoff produced"),
        ("VERIFYING", "AWAITING_REVIEW", "chugel", "checks ran"),
        ("AWAITING_REVIEW", "REVIEWING", "chugel", "emma invoked"),
        ("REVIEWING", "PUBLISH_AWAITING_AUTHORIZATION", "chugel", "pass verdict"),
    ]:
        record["state_history"].append({"from_state": from_s, "to_state": to_s, "at": "2026-08-19T12:20:00Z", "actor": actor, "reason": reason})
    record["state"] = "PUBLISH_AWAITING_AUTHORIZATION"
    record["state_reason"] = "awaiting publish authorization"
    record["builder_evidence"] = [_builder_entry(0)]
    record["reviewer_evidence"] = [_reviewer_entry(0, verdict="PASS")]
    return record


def _completed_record():
    """Full legal chain through merge/deploy/completion."""
    record = _built_and_reviewed_pass_record()
    for from_s, to_s in [
        ("PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING"),
        ("PUBLISHING", "CI_PENDING"),
        ("CI_PENDING", "MERGE_AWAITING_AUTHORIZATION"),
        ("MERGE_AWAITING_AUTHORIZATION", "MERGING"),
        ("MERGING", "MERGED"),
        ("MERGED", "DEPLOY_PENDING"),
        ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"),
        ("VERIFYING_PRODUCTION", "COMPLETED"),
    ]:
        record["state_history"].append({"from_state": from_s, "to_state": to_s, "at": "2026-08-19T13:00:00Z", "actor": "chugel", "reason": "advance"})
    record["state"] = "COMPLETED"
    record["state_reason"] = "deploy verified"
    record["human_gates"]["publish_authorization"] = _approved_gate({"commit_sha": "b" * 40})
    record["human_gates"]["merge_authorization"] = _approved_gate({"head_sha": "b" * 40, "base_sha": "0" * 40})
    record["publish"] = {"commit_sha": "b" * 40, "pushed_at": "2026-08-19T12:50:00Z", "pr_url": "https://example.test/pr/1", "pr_number": 1,
                          "ci_runs": [{"run_id": "run-1", "conclusion": "success", "checked_at": "2026-08-19T12:55:00Z"}]}
    record["merge"] = {"merge_commit_sha": "d" * 40, "merged_at": "2026-08-19T13:00:00Z"}
    record["deploy"] = {
        "expected_sha": "d" * 40, "deploy_confirmed_at": "2026-08-19T13:05:00Z",
        "health_check": {"checked_at": "2026-08-19T13:05:00Z", "status_code": 200, "body_summary": "ok"},
        "version_check": {"checked_at": "2026-08-19T13:05:00Z", "status_code": 200, "body_summary": "d" * 40},
    }
    return record


def _corrective_cycle_record():
    """Legal corrective cycle: initial review CHANGES_REQUIRED, one bounded
    correction, re-review PASS."""
    record = _built_and_reviewed_pass_record()
    record["reviewer_evidence"] = [_reviewer_entry(0, verdict="CHANGES_REQUIRED", findings=[
        {"id": "F1", "severity": "P2", "summary": "missing test", "file": "x.py", "line_range": "1-2", "category": "testing"}
    ])]
    record["state"] = "CORRECTING"
    record["state_reason"] = "addressing findings"
    record["state_history"][-1] = {"from_state": "REVIEWING", "to_state": "CHANGES_REQUIRED", "at": "2026-08-19T12:30:00Z", "actor": "chugel", "reason": "changes required"}
    record["state_history"].append({"from_state": "CHANGES_REQUIRED", "to_state": "CORRECTING", "at": "2026-08-19T12:31:00Z", "actor": "chugel", "reason": "one bounded cycle"})
    record["corrective_cycle_count"] = 1
    record["builder_evidence"].append(_builder_entry(1, artifact=ARTIFACT_B))
    record["state_history"].append({"from_state": "CORRECTING", "to_state": "VERIFYING", "at": "2026-08-19T12:40:00Z", "actor": "chugel", "reason": "re-verify"})
    record["state_history"].append({"from_state": "VERIFYING", "to_state": "AWAITING_REVIEW", "at": "2026-08-19T12:41:00Z", "actor": "chugel", "reason": "checks ran"})
    record["state_history"].append({"from_state": "AWAITING_REVIEW", "to_state": "REVIEWING", "at": "2026-08-19T12:42:00Z", "actor": "chugel", "reason": "re-review"})
    record["state_history"].append({"from_state": "REVIEWING", "to_state": "PUBLISH_AWAITING_AUTHORIZATION", "at": "2026-08-19T12:50:00Z", "actor": "chugel", "reason": "pass"})
    record["reviewer_evidence"].append(_reviewer_entry(1, verdict="PASS_WITH_NON_BLOCKING_FINDINGS", start=ARTIFACT_B, before=ARTIFACT_B, findings=[
        {"id": "F2", "severity": "P3", "summary": "nit", "file": "x.py", "line_range": "3", "category": "clarity"}
    ]))
    record["state"] = "PUBLISH_AWAITING_AUTHORIZATION"
    record["state_reason"] = "awaiting publish authorization after correction"
    return record


def error_codes(result):
    return sorted(e.code for e in result.errors)


def _set_invocation_identity(entry, *, invocation_id, provider,
                             provider_session_id=None,
                             provider_conversation_id=None):
    entry.update({
        "invocation_id": invocation_id,
        "provider": provider,
        "provider_session_id": provider_session_id,
        "provider_conversation_id": provider_conversation_id,
    })


class PruebaFlujosLegales(unittest.TestCase):
    """Casos de camino feliz -- deben validar limpio, sin ningún error."""

    def test_intake_minimo_es_valido(self):
        result = validate_mission_record(_minimal_intake_record())
        self.assertTrue(result.valid, error_codes(result))

    def test_authorized_es_valido(self):
        result = validate_mission_record(_authorized_record())
        self.assertTrue(result.valid, error_codes(result))

    def test_flujo_build_review_pass_es_valido(self):
        result = validate_mission_record(_built_and_reviewed_pass_record())
        self.assertTrue(result.valid, error_codes(result))

    def test_ciclo_correctivo_legal_es_valido(self):
        result = validate_mission_record(_corrective_cycle_record())
        self.assertTrue(result.valid, error_codes(result))

    def test_flujo_merge_deploy_completion_es_valido(self):
        result = validate_mission_record(_completed_record())
        self.assertTrue(result.valid, error_codes(result))


class PruebaConsistenciaVeredictoSeveridad(unittest.TestCase):
    """Derivado exactamente de docs/zentra/REVIEWER_QA_V1.md -- ver
    orchestrator/validator.py::_check_reviewer_verdict_consistency."""

    def test_pass_con_p0_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["findings"] = [
            {"id": "F1", "severity": "P0", "summary": "critico", "file": None, "line_range": None, "category": "seguridad"}
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_P0", error_codes(result))

    def test_pass_con_p1_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["findings"] = [
            {"id": "F1", "severity": "P1", "summary": "regresion", "file": None, "line_range": None, "category": "correctness"}
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_PASS", error_codes(result))

    def test_pass_with_non_blocking_con_p2_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["verdict"] = "PASS_WITH_NON_BLOCKING_FINDINGS"
        record["reviewer_evidence"][0]["findings"] = [
            {"id": "F1", "severity": "P2", "summary": "mantenibilidad", "file": None, "line_range": None, "category": "maintainability"}
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_NON_BLOCKING", error_codes(result))

    def test_pass_with_non_blocking_sin_findings_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["verdict"] = "PASS_WITH_NON_BLOCKING_FINDINGS"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_NON_BLOCKING", error_codes(result))

    def test_blocked_sin_p0_es_invalido(self):
        for findings in ([], [
            {"id": "F1", "severity": "P2", "summary": "x", "file": None,
             "line_range": None, "category": "correctness"}
        ]):
            with self.subTest(findings=findings):
                record = _built_and_reviewed_pass_record()
                record["reviewer_evidence"][0]["verdict"] = "BLOCKED"
                record["reviewer_evidence"][0]["blocked_reason"] = "blocked"
                record["reviewer_evidence"][0]["findings"] = findings
                result = validate_mission_record(record)
                self.assertFalse(result.valid)
                self.assertIn("VERDICT_SEVERITY_MISMATCH_BLOCKED", error_codes(result))

    def test_changes_required_sin_p1_p2_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["verdict"] = "CHANGES_REQUIRED"
        record["reviewer_evidence"][0]["findings"] = []
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_CHANGES_REQUIRED", error_codes(result))

    def test_pass_con_findings_vacio_sigue_siendo_valido(self):
        record = _built_and_reviewed_pass_record()
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))


class PruebaSecuenciaDeIntentos(unittest.TestCase):
    def test_duplicado_attempt_cero_en_builder_evidence(self):
        record = _built_and_reviewed_pass_record()
        record["builder_evidence"].append(_builder_entry(0))
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("DUPLICATE_ATTEMPT_NUMBER", error_codes(result))

    def test_duplicado_attempt_uno_en_reviewer_evidence(self):
        # Con la capa estructural (Part A) conectada, reviewer_evidence ya
        # está acotado por el schema a maxItems: 2 -- una tercera entrada
        # se rechaza en la capa estructural antes de que la cross-field
        # (DUPLICATE_ATTEMPT_NUMBER) tenga oportunidad de correr.
        record = _corrective_cycle_record()
        record["reviewer_evidence"].append(_reviewer_entry(1, verdict="PASS", start=ARTIFACT_B, before=ARTIFACT_B))
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_MAXITEMS_VIOLATION", error_codes(result))

    def test_intento_correctivo_sin_intento_inicial(self):
        record = _built_and_reviewed_pass_record()
        record["builder_evidence"] = [_builder_entry(1)]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("CORRECTIVE_ATTEMPT_WITHOUT_INITIAL", error_codes(result))


class PruebaConsistenciaCicloCorrectivo(unittest.TestCase):
    def test_count_inconsistente_con_evidencia_de_verdad(self):
        record = _corrective_cycle_record()
        record["corrective_cycle_count"] = 0  # pero sí existe un builder_evidence attempt=1
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("CORRECTIVE_CYCLE_COUNT_INCONSISTENT", error_codes(result))

    def test_count_uno_sin_intento_correctivo_real(self):
        record = _built_and_reviewed_pass_record()
        record["corrective_cycle_count"] = 1
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("CORRECTIVE_CYCLE_COUNT_INCONSISTENT", error_codes(result))

    def test_intento_correctivo_sin_changes_required_previo(self):
        record = _corrective_cycle_record()
        record["reviewer_evidence"][0]["verdict"] = "PASS"
        record["reviewer_evidence"][0]["findings"] = []
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("CORRECTIVE_CYCLE_WITHOUT_TRIGGER", error_codes(result))


class PruebaConsistenciaIdentidadDeArtefacto(unittest.TestCase):
    def test_snapshots_distintos_sin_blocked_es_invalido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["artifact_identity_confirmed_before_conclusion"] = ARTIFACT_B
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("ARTIFACT_IDENTITY_DRIFT_DURING_REVIEW", error_codes(result))

    def test_snapshots_distintos_con_blocked_es_valido(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["artifact_identity_confirmed_before_conclusion"] = ARTIFACT_B
        record["reviewer_evidence"][0]["verdict"] = "BLOCKED"
        record["reviewer_evidence"][0]["findings"] = [{
            "id": "F1", "severity": "P0", "summary": "artifact drifted mid-review",
            "file": None, "line_range": None, "category": "artifact_identity",
        }]
        record["reviewer_evidence"][0]["blocked_reason"] = "artifact drifted mid-review"
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_emma_reviso_un_artefacto_distinto_al_de_emilio(self):
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["artifact_identity_confirmed_at_start"] = ARTIFACT_B
        record["reviewer_evidence"][0]["artifact_identity_confirmed_before_conclusion"] = ARTIFACT_B
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("ARTIFACT_IDENTITY_MISMATCH_WITH_BUILDER", error_codes(result))


class PruebaIdentidadPersistenteDeInvocacion(unittest.TestCase):
    BUILDER_INVOCATION_ID = "11111111-1111-4111-8111-111111111111"
    REVIEWER_INVOCATION_ID = "22222222-2222-4222-8222-222222222222"

    def _record_with_identities(self, *, builder_session="builder-session",
                                reviewer_session="reviewer-session",
                                builder_conversation="builder-conversation",
                                reviewer_conversation="reviewer-conversation"):
        record = _built_and_reviewed_pass_record()
        _set_invocation_identity(
            record["builder_evidence"][0],
            invocation_id=self.BUILDER_INVOCATION_ID,
            provider="codex",
            provider_session_id=builder_session,
            provider_conversation_id=builder_conversation,
        )
        _set_invocation_identity(
            record["reviewer_evidence"][0],
            invocation_id=self.REVIEWER_INVOCATION_ID,
            provider="claude",
            provider_session_id=reviewer_session,
            provider_conversation_id=reviewer_conversation,
        )
        return record

    def test_registro_historico_sin_identidades_sigue_valido(self):
        record = _built_and_reviewed_pass_record()
        self.assertTrue(validate_mission_record(record).valid)

    def test_identidades_null_son_validas(self):
        record = self._record_with_identities(
            builder_session=None,
            reviewer_session=None,
            builder_conversation=None,
            reviewer_conversation=None,
        )
        for field in ("invocation_id", "provider"):
            record["builder_evidence"][0][field] = None
            record["reviewer_evidence"][0][field] = None
        self.assertTrue(validate_mission_record(record).valid)

    def test_identidades_distintas_son_validas(self):
        record = self._record_with_identities()
        self.assertTrue(validate_mission_record(record).valid)

    def test_reviewer_no_puede_reusar_sesion_del_builder_mismo_intento(self):
        record = self._record_with_identities(reviewer_session="builder-session")
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("REVIEWER_REUSED_BUILDER_PROVIDER_SESSION", error_codes(result))

    def test_reviewer_no_puede_reusar_conversacion_del_builder_mismo_intento(self):
        record = self._record_with_identities(
            reviewer_conversation="builder-conversation",
        )
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("REVIEWER_REUSED_BUILDER_PROVIDER_CONVERSATION", error_codes(result))

    def test_null_no_se_compara_como_identidad_compartida(self):
        record = self._record_with_identities(
            builder_session=None,
            reviewer_session=None,
        )
        self.assertNotIn(
            "REVIEWER_REUSED_BUILDER_PROVIDER_SESSION",
            error_codes(validate_mission_record(record)),
        )

    def test_solo_se_compara_el_intento_correspondiente(self):
        record = _corrective_cycle_record()
        for attempt, builder in enumerate(record["builder_evidence"]):
            _set_invocation_identity(
                builder,
                invocation_id=(self.BUILDER_INVOCATION_ID if attempt == 0 else
                               "33333333-3333-4333-8333-333333333333"),
                provider="codex",
                provider_session_id=f"builder-session-{attempt}",
            )
        for attempt, reviewer in enumerate(record["reviewer_evidence"]):
            _set_invocation_identity(
                reviewer,
                invocation_id=(self.REVIEWER_INVOCATION_ID if attempt == 0 else
                               "44444444-4444-4444-8444-444444444444"),
                provider="claude",
                provider_session_id=f"builder-session-{1 - attempt}",
            )
        self.assertTrue(validate_mission_record(record).valid)

    def test_invocation_id_debe_ser_uuid_canonico_o_null(self):
        for bad_value in ("inv-1", "", 1, True, {}):
            with self.subTest(bad_value=bad_value):
                record = self._record_with_identities()
                record["builder_evidence"][0]["invocation_id"] = bad_value
                self.assertFalse(validate_mission_record(record).valid)

    def test_metadatos_de_proveedor_deben_ser_strings_no_vacios_o_null(self):
        for field in ("provider", "provider_session_id", "provider_conversation_id"):
            for bad_value in ("", 1, True, {}):
                with self.subTest(field=field, bad_value=bad_value):
                    record = self._record_with_identities()
                    record["reviewer_evidence"][0][field] = bad_value
                    self.assertFalse(validate_mission_record(record).valid)


class PruebaAprobacionesObsoletas(unittest.TestCase):
    def test_scope_approved_for_exige_version_entera_positiva(self):
        invalid_values = (
            {"note": "context only"},
            {},
            {"wrong_key": 1},
            {"mission_definition_version": "1"},
            {"mission_definition_version": True},
            {"mission_definition_version": -1},
        )
        for approved_for in invalid_values:
            with self.subTest(approved_for=approved_for):
                record = _authorized_record()
                record["human_gates"]["scope_authorization"]["approved_for"] = approved_for
                self.assertFalse(validate_mission_record(record).valid)

    def test_scope_approved_for_version_actual_es_valida(self):
        record = _authorized_record()
        record["human_gates"]["scope_authorization"]["approved_for"] = {
            "mission_definition_version": 1,
        }
        self.assertTrue(validate_mission_record(record).valid)

    def test_merge_approved_for_exige_head_sha_canonico(self):
        invalid_values = (
            {},
            {"note": "context only"},
            {"commit_sha": "b" * 40},
            {"head_sha": "b" * 39},
            {"head_sha": "B" * 40},
            {"head_sha": "not-a-sha"},
        )
        for approved_for in invalid_values:
            with self.subTest(approved_for=approved_for):
                record = _completed_record()
                record["human_gates"]["merge_authorization"]["approved_for"] = approved_for
                self.assertFalse(validate_mission_record(record).valid)

    def test_merge_approved_for_sha_distinto_es_obsoleto(self):
        record = _completed_record()
        record["human_gates"]["merge_authorization"]["approved_for"] = {"head_sha": "e" * 40}
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STALE_APPROVAL", error_codes(result))

    def test_merge_approved_for_sha_actual_es_valido(self):
        record = _completed_record()
        record["human_gates"]["merge_authorization"]["approved_for"] = {"head_sha": "b" * 40}
        self.assertTrue(validate_mission_record(record).valid)

    def test_merge_authorization_obsoleta(self):
        record = _completed_record()
        record["publish"]["commit_sha"] = "e" * 40  # nuevo push después de la aprobación
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STALE_APPROVAL", error_codes(result))

    def test_scope_authorization_obsoleta(self):
        record = _authorized_record()
        record["human_gates"]["scope_authorization"]["approved_for"] = {"mission_definition_version": 2}
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STALE_APPROVAL", error_codes(result))

    def test_aprobacion_malformada_no_respalda_authorized_ni_building(self):
        for state in ("AUTHORIZED", "BUILDING"):
            with self.subTest(state=state):
                record = _authorized_record()
                record["state"] = state
                record["human_gates"]["scope_authorization"]["approved_for"] = {"note": "context only"}
                if state == "BUILDING":
                    record["repository"]["isolation_confirmed"] = True
                    record["state_history"].append({
                        "from_state": "AUTHORIZED", "to_state": "BUILDING",
                        "at": "2026-08-19T12:11:00Z", "actor": "chugel", "reason": "start",
                    })
                self.assertFalse(validate_mission_record(record).valid)


class PruebaAutorizacionDeAlcancePorAgente(unittest.TestCase):
    def test_un_agente_no_puede_autorizar_su_propio_alcance(self):
        # Con la capa estructural (Part A) conectada, mission_definition_
        # history[].authorized_by ya está tipado en el schema como
        # "const": "jose" -- cualquier otro valor se rechaza en la capa
        # estructural, ANTES de llegar a la capa cross-field. Sigue siendo
        # rechazado, solo que con un código distinto (más específico y más
        # temprano) -- la protección real (ningún agente autoriza alcance)
        # sigue intacta, ahora doblemente reforzada.
        record = _authorized_record()
        record["mission_definition_history"][0]["authorized_by"] = "emilio"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_CONST_VIOLATION", error_codes(result))

    def test_versiones_no_monotonicas(self):
        record = _authorized_record()
        record["mission_definition_history"].append(_mission_definition_entry(version=3, source="david_replan", based_on="p1"))
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("MISSION_DEFINITION_VERSION_NOT_MONOTONIC", error_codes(result))

    def test_replan_huerfano_sin_propuesta(self):
        record = _authorized_record()
        record["mission_definition_history"].append(_mission_definition_entry(version=2, source="david_replan", based_on="does-not-exist"))
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCOPE_VERSION_ORPHANED_PROPOSAL", error_codes(result))

    def test_replan_legal_con_propuesta_aceptada(self):
        # José's explicit decision (Increment #4 corrective cycle): un
        # re-plan legítimo requiere que human_gates.scope_authorization
        # también se actualice para reflejar la versión ACTUAL, no solo
        # que exista una nueva entrada en mission_definition_history.
        record = _authorized_record()
        record["proposed_scope_changes"] = [{
            "proposal_id": "p1", "proposed_at": "2026-08-19T12:15:00Z", "proposed_by": "david",
            "label": "INFERENCE", "rationale": "found a related need",
            "diff_against_current_scope": {"added": ["api/other.py"], "removed": []},
            "status": "accepted", "decided_by": "jose", "decided_at": "2026-08-19T12:16:00Z",
            "resulting_mission_definition_version": 2,
        }]
        record["mission_definition_history"].append(_mission_definition_entry(version=2, source="david_replan", based_on="p1"))
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))


class PruebaReplanAutorizacionDeAlcance(unittest.TestCase):
    """Decisión humana explícita de José (Increment #4, ciclo correctivo):
    human_gates.scope_authorization representa autorización para la
    versión de mission_definition ACTUAL/activa, no permanentemente la
    inicial de david_intake. Cubre exactamente los 8 casos pedidos."""

    def _replan_history_entry(self, version=2, based_on="p1"):
        return _mission_definition_entry(version=version, source="david_replan", based_on=based_on)

    def _accepted_proposal(self, proposal_id="p1", decided_by="jose", decided_at="2026-08-19T12:16:00Z", resulting_version=2):
        return {
            "proposal_id": proposal_id, "proposed_at": "2026-08-19T12:15:00Z", "proposed_by": "david",
            "label": "INFERENCE", "rationale": "found a related need",
            "diff_against_current_scope": {"added": ["api/other.py"], "removed": []},
            "status": "accepted", "decided_by": decided_by, "decided_at": decided_at,
            "resulting_mission_definition_version": resulting_version,
        }

    # 1. Initial mission v1 + José approval for v1 -> valid.
    def test_1_v1_inicial_con_aprobacion_de_v1_es_valido(self):
        result = validate_mission_record(_authorized_record())
        self.assertTrue(result.valid, error_codes(result))

    # 2. Legitimate accepted re-plan producing v2 + José approval updated to v2 -> valid.
    def test_2_replan_legitimo_con_gate_actualizado_a_v2_es_valido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        record["mission_definition_history"].append(self._replan_history_entry())
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    # 3. Re-plan produces v2 but scope authorization still points to v1 -> stale.
    def test_3_replan_a_v2_pero_gate_sigue_apuntando_a_v1_es_obsoleto(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        record["mission_definition_history"].append(self._replan_history_entry())
        # human_gates.scope_authorization NO se actualiza -- sigue en v1.
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STALE_APPROVAL", error_codes(result))

    # 4. Re-plan v2 accepted by anyone other than José -> invalid.
    def test_4_propuesta_aceptada_por_alguien_que_no_es_jose(self):
        # Con la capa estructural (Part A) conectada, proposed_scope_
        # changes[].decided_by ya está tipado como anyOf[null, const:
        # "jose"] -- "chugel" se rechaza en la capa estructural, incluso
        # antes de que exista la oportunidad de evaluar la evidencia
        # cross-field. La protección real (nadie más que jose decide)
        # sigue intacta, ahora reforzada en dos capas.
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal(decided_by="chugel")]
        record["mission_definition_history"].append(self._replan_history_entry())
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ANYOF_VIOLATION", error_codes(result))

    # 5. Pending/rejected proposal cannot become active mission definition.
    def test_5a_propuesta_pending_vinculada_a_historial_es_invalida(self):
        record = _authorized_record()
        proposal = self._accepted_proposal()
        proposal["status"] = "pending_human_decision"
        record["proposed_scope_changes"] = [proposal]
        record["mission_definition_history"].append(self._replan_history_entry())
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(
            {"SCOPE_VERSION_PROPOSAL_MISMATCH", "SCHEMA_TYPE_VIOLATION"}
            & set(error_codes(result))
        )

    def test_5b_propuesta_rejected_vinculada_a_historial_es_invalida(self):
        record = _authorized_record()
        proposal = self._accepted_proposal()
        proposal["status"] = "rejected"
        record["proposed_scope_changes"] = [proposal]
        record["mission_definition_history"].append(self._replan_history_entry())
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(
            {"SCOPE_VERSION_PROPOSAL_MISMATCH", "SCHEMA_TYPE_VIOLATION"}
            & set(error_codes(result))
        )

    def test_5c_propuesta_pending_sin_vinculo_a_historial_no_afecta_nada(self):
        record = _authorized_record()
        proposal = self._accepted_proposal(proposal_id="p2")
        proposal["status"] = "pending_human_decision"
        proposal["decided_by"] = None
        proposal["decided_at"] = None
        proposal["resulting_mission_definition_version"] = None
        record["proposed_scope_changes"] = [proposal]
        # mission_definition_history se queda solo en v1 -- la propuesta
        # pendiente nunca se vuelve la definición activa por sí sola.
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    # 6/7/8. Ningún agente puede autorizar un re-plan -- solo jose.
    def test_6_david_no_puede_autorizar_su_propio_replan(self):
        # Con la capa estructural (Part A) conectada, mission_definition_
        # history[].authorized_by ya está tipado como "const": "jose" --
        # "david" se rechaza en la capa estructural.
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        entry = self._replan_history_entry()
        entry["authorized_by"] = "david"
        record["mission_definition_history"].append(entry)
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_CONST_VIOLATION", error_codes(result))

    def test_7_chugel_no_puede_autorizarlo(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        entry = self._replan_history_entry()
        entry["authorized_by"] = "chugel"
        record["mission_definition_history"].append(entry)
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_CONST_VIOLATION", error_codes(result))

    def test_8a_emilio_no_puede_autorizarlo(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        entry = self._replan_history_entry()
        entry["authorized_by"] = "emilio"
        record["mission_definition_history"].append(entry)
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_CONST_VIOLATION", error_codes(result))

    def test_8b_emma_no_puede_autorizarlo(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal()]
        entry = self._replan_history_entry()
        entry["authorized_by"] = "emma"
        record["mission_definition_history"].append(entry)
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_CONST_VIOLATION", error_codes(result))

    # Auto-desafío adicional: un segundo re-plan (v2 -> v3) deja el gate
    # obsoleto apuntando a v2 -- debe fallar cerrado igual que v1 -> v2.
    def test_segundo_replan_deja_obsoleta_la_aprobacion_de_v2(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [
            self._accepted_proposal(proposal_id="p1", resulting_version=2),
            self._accepted_proposal(proposal_id="p2", resulting_version=3),
        ]
        record["mission_definition_history"].append(self._replan_history_entry(version=2, based_on="p1"))
        record["mission_definition_history"].append(self._replan_history_entry(version=3, based_on="p2"))
        # El gate quedó aprobado para v2 (obsoleto -- ahora la versión activa es v3).
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STALE_APPROVAL", error_codes(result))

    def test_segundo_replan_con_gate_actualizado_a_v3_es_valido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [
            self._accepted_proposal(proposal_id="p1", resulting_version=2),
            self._accepted_proposal(proposal_id="p2", resulting_version=3),
        ]
        record["mission_definition_history"].append(self._replan_history_entry(version=2, based_on="p1"))
        record["mission_definition_history"].append(self._replan_history_entry(version=3, based_on="p2"))
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 3})
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))


class PruebaEvidenciaDeAuditoriaDePropuestaAceptada(unittest.TestCase):
    """Emma P2 (Increment #4 corrective cycle): una propuesta 'accepted'
    debe traer su propia evidencia de decisión completa y atribuida a
    jose, y su resulting_mission_definition_version debe corresponder a
    una entrada real de mission_definition_history."""

    def _accepted_proposal(self, **overrides):
        base = {
            "proposal_id": "p1", "proposed_at": "2026-08-19T12:15:00Z", "proposed_by": "david",
            "label": "INFERENCE", "rationale": "found a related need",
            "diff_against_current_scope": {"added": ["api/other.py"], "removed": []},
            "status": "accepted", "decided_by": "jose", "decided_at": "2026-08-19T12:16:00Z",
            "resulting_mission_definition_version": 2,
        }
        base.update(overrides)
        return base

    def test_accepted_sin_decided_by_es_invalido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal(decided_by=None)]
        record["mission_definition_history"].append(
            _mission_definition_entry(version=2, source="david_replan", based_on="p1")
        )
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(
            {"PROPOSAL_ACCEPTED_WITHOUT_JOSE_EVIDENCE", "SCHEMA_CONST_VIOLATION"}
            & set(error_codes(result))
        )

    def test_accepted_sin_decided_at_es_invalido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal(decided_at=None)]
        record["mission_definition_history"].append(
            _mission_definition_entry(version=2, source="david_replan", based_on="p1")
        )
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(
            {"PROPOSAL_ACCEPTED_WITHOUT_JOSE_EVIDENCE", "SCHEMA_TYPE_VIOLATION"}
            & set(error_codes(result))
        )

    def test_accepted_con_version_resultante_sin_entrada_real_es_invalido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._accepted_proposal(resulting_mission_definition_version=2)]
        # Nunca se agrega la entrada v2 real a mission_definition_history.
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("PROPOSAL_ACCEPTED_WITHOUT_RESULTING_VERSION", error_codes(result))


class PruebaInvariantesCanonicosDeDecisionDePropuesta(unittest.TestCase):
    def _proposal(self, status="pending_human_decision", **overrides):
        proposal = {
            "proposal_id": "p1", "proposed_at": "2026-08-19T12:15:00Z",
            "proposed_by": "david", "label": "FACT", "rationale": "change",
            "diff_against_current_scope": {"added": ["x"], "removed": []},
            "status": status, "decided_by": None, "decided_at": None,
            "resulting_mission_definition_version": None,
        }
        proposal.update(overrides)
        return proposal

    def test_proposal_id_duplicado_es_invalido(self):
        record = _minimal_intake_record()
        record["proposed_scope_changes"] = [self._proposal(), self._proposal()]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("DUPLICATE_PROPOSAL_ID", error_codes(result))

    def test_pending_con_metadata_de_decision_es_invalido(self):
        for field, value in (
            ("decided_by", "jose"), ("decided_at", "2026-08-19T12:16:00Z"),
            ("resulting_mission_definition_version", 1),
        ):
            record = _minimal_intake_record()
            record["proposed_scope_changes"] = [self._proposal(**{field: value})]
            with self.subTest(field=field):
                self.assertFalse(validate_mission_record(record).valid)

    def test_rejected_requiere_jose_timestamp_y_resultado_null(self):
        invalid = (
            {}, {"decided_by": "jose"},
            {"decided_at": "2026-08-19T12:16:00Z"},
            {"decided_by": "jose", "decided_at": "2026-08-19T12:16:00Z",
             "resulting_mission_definition_version": 1},
        )
        for overrides in invalid:
            record = _minimal_intake_record()
            record["proposed_scope_changes"] = [self._proposal("rejected", **overrides)]
            with self.subTest(overrides=overrides):
                self.assertFalse(validate_mission_record(record).valid)

    def test_rejected_canonico_es_valido(self):
        record = _minimal_intake_record()
        record["proposed_scope_changes"] = [self._proposal(
            "rejected", decided_by="jose", decided_at="2026-08-19T12:16:00Z",
        )]
        self.assertTrue(validate_mission_record(record).valid)

    def test_accepted_requiere_version_positiva_y_history_correspondiente(self):
        for version in (None, 0, -1, True, "2"):
            record = _minimal_intake_record()
            record["proposed_scope_changes"] = [self._proposal(
                "accepted", decided_by="jose", decided_at="2026-08-19T12:16:00Z",
                resulting_mission_definition_version=version,
            )]
            with self.subTest(version=version):
                self.assertFalse(validate_mission_record(record).valid)

    def test_registro_historico_canonico_aceptado_permanece_valido(self):
        record = _authorized_record()
        record["proposed_scope_changes"] = [self._proposal(
            "accepted", decided_by="jose", decided_at="2026-08-19T12:16:00Z",
            resulting_mission_definition_version=2,
        )]
        record["mission_definition_history"].append(
            _mission_definition_entry(version=2, source="david_replan", based_on="p1")
        )
        record["human_gates"]["scope_authorization"] = _approved_gate({"mission_definition_version": 2})
        self.assertTrue(validate_mission_record(record).valid)


class PruebaFormatCheckerFechaHoraYUri(unittest.TestCase):
    """Corrección final de Increment #4 (hallazgo P2 de Emma): el schema
    canónico declara exactamente dos "format": "date-time" (vía
    #/definitions/timestamp, usado por casi todos los campos *_at) y "uri"
    (solo publish.pr_url) -- confirmado por inspección directa del schema,
    no supuesto. Ambos ahora se aplican de verdad vía jsonschema.
    FormatChecker, sin dependencia nueva, sin red, sin mutar nada."""

    # --- date-time: 1-5 ---

    def test_1_timestamp_canonico_valido_es_aceptado(self):
        record = _minimal_intake_record()
        record["created_at"] = "2026-08-19T12:00:00Z"
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_2_timestamp_con_offset_de_zona_horaria_es_aceptado(self):
        record = _minimal_intake_record()
        record["created_at"] = "2026-08-19T12:00:00+00:00"
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_3_timestamp_basura_obvia_es_rechazado(self):
        record = _minimal_intake_record()
        record["created_at"] = "definitely-not-a-real-timestamp"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_FORMAT_DATE_TIME_VIOLATION", error_codes(result))

    def test_4_fecha_de_calendario_imposible_es_rechazada(self):
        record = _minimal_intake_record()
        record["created_at"] = "2026-02-30T12:00:00Z"  # 30 de febrero no existe
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_FORMAT_DATE_TIME_VIOLATION", error_codes(result))

    def test_5_zona_horaria_malformada_es_rechazada(self):
        record = _minimal_intake_record()
        record["created_at"] = "2026-08-19T12:00:00+25:00"  # offset imposible (>23:59)
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_FORMAT_DATE_TIME_VIOLATION", error_codes(result))

    def test_5b_timestamp_sin_zona_horaria_tambien_se_rechaza(self):
        # datetime.fromisoformat() acepta un datetime "naive" (sin
        # offset), pero el schema exige explícitamente "RFC 3339 UTC
        # timestamp" -- un timestamp sin offset no cumple esa intención,
        # aunque sea sintácticamente parseable por el stdlib.
        record = _minimal_intake_record()
        record["created_at"] = "2026-08-19T12:00:00"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_FORMAT_DATE_TIME_VIOLATION", error_codes(result))

    # --- uri: 6-9 ---

    def test_6_url_de_pr_valida_es_aceptada(self):
        record = _completed_record()
        record["publish"]["pr_url"] = "https://example.test/pr/1"
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_7_otra_variante_de_uri_valida_es_aceptada(self):
        # El schema canónico solo tipa "format": "uri" en publish.pr_url
        # -- no existe un segundo campo de URL de repositorio/deploy en
        # mission_record.schema.json (confirmado por inspección). Esta
        # prueba cubre una segunda forma válida de URI en ese mismo campo
        # (con query string) para no dejar el checker subprobado.
        record = _completed_record()
        record["publish"]["pr_url"] = "https://github.example.test/org/repo/pull/42?tab=files"
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_8_palabras_sueltas_no_son_una_uri(self):
        record = _completed_record()
        record["publish"]["pr_url"] = "not a uri at all, just words"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        # anyOf porque pr_url es anyOf[null, string+format:uri] -- ver
        # "residual limitation" en el handoff sobre por qué el código no
        # es SCHEMA_FORMAT_URI_VIOLATION directamente en este campo.
        self.assertIn("SCHEMA_ANYOF_VIOLATION", error_codes(result))

    def test_9_uri_malformada_sin_esquema_es_rechazada(self):
        record = _completed_record()
        record["publish"]["pr_url"] = "not-a-real-uri-scheme%%%"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ANYOF_VIOLATION", error_codes(result))

    def test_9b_el_checker_de_uri_en_si_mismo_rechaza_lo_esperado(self):
        # Prueba directa del checker (no a través del anyOf) para
        # confirmar que SÍ produce el código específico cuando el campo
        # no está envuelto en anyOf -- ver _check_uri_format.
        from orchestrator.validator import _check_uri_format
        self.assertTrue(_check_uri_format("https://example.test/pr/1"))
        self.assertFalse(_check_uri_format("not a uri at all, just words"))
        self.assertFalse(_check_uri_format("not-a-real-uri-scheme%%%"))
        self.assertFalse(_check_uri_format(""))

    # --- pipeline: 10-13 ---

    def test_10_registro_con_formato_invalido_falla_en_capa_estructural(self):
        record = _built_and_reviewed_pass_record()
        record["created_at"] = "garbage"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))

    def test_11_registro_con_formato_valido_pero_cross_field_invalido_llega_a_esa_capa(self):
        record = _built_and_reviewed_pass_record()
        # Todos los timestamps son válidos; el finding viola PASS -- debe
        # llegar a la capa cross-field, no quedarse en la estructural.
        record["reviewer_evidence"][0]["findings"] = [
            {"id": "F1", "severity": "P2", "summary": "x", "file": None, "line_range": None, "category": "x"}
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_PASS", error_codes(result))
        self.assertFalse(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))

    def test_12_el_registro_de_entrada_no_cambia_con_el_format_checker(self):
        record = _minimal_intake_record()
        record["created_at"] = "garbage-timestamp"
        before = copy.deepcopy(record)
        validate_mission_record(record)
        self.assertEqual(record, before)

    def test_13_resultados_deterministas_entre_llamadas_repetidas(self):
        record = _minimal_intake_record()
        record["created_at"] = "garbage-timestamp"
        record["publish"]["pr_url"] = "also not a uri"
        first = validate_mission_record(record)
        second = validate_mission_record(record)
        self.assertEqual(first.errors, second.errors)


class PruebaCapaEstructuralJSONSchema(unittest.TestCase):
    """Part A (hardening autorizada por José tras el corrective cycle):
    validate_mission_record() ahora es dos capas -- estructural (JSON
    Schema, orchestrator/schemas/mission_record.schema.json vía
    jsonschema.Draft7Validator) seguida de cross-field. Un registro es
    válido solo si AMBAS capas pasan; si la capa estructural encuentra
    algo, la cross-field ni siquiera corre."""

    def test_falta_intent_requerido(self):
        record = _minimal_intake_record()
        del record["intent"]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_REQUIRED_VIOLATION", error_codes(result))

    def test_sha_de_commit_con_forma_invalida(self):
        record = _completed_record()
        record["merge"]["merge_commit_sha"] = "not-a-real-sha"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ANYOF_VIOLATION", error_codes(result))

    def test_campo_desconocido_en_el_nivel_superior(self):
        record = _minimal_intake_record()
        record["campo_inesperado"] = "no debería permitirse"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ADDITIONALPROPERTIES_VIOLATION", error_codes(result))

    def test_schema_version_no_soportada_via_capa_estructural(self):
        record = _minimal_intake_record()
        record["schema_version"] = "2.0.0"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        # El código específico se preserva por continuidad con la capa
        # cross-field que existía antes de este hardening.
        self.assertIn("UNSUPPORTED_SCHEMA_VERSION", error_codes(result))

    def test_tipo_primitivo_incorrecto(self):
        record = _minimal_intake_record()
        record["corrective_cycle_count"] = "0"  # string en vez de int
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_TYPE_VIOLATION", error_codes(result))

    def test_gate_anidado_malformado(self):
        record = _minimal_intake_record()
        record["human_gates"]["merge_authorization"] = {"status": "not_a_real_status"}
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        # Falta requeridos (requested_at, decided_at, ...) Y el status no
        # es un valor del enum -- cualquiera de los dos códigos confirma
        # que la capa estructural lo atrapó.
        self.assertTrue(
            any(code in ("SCHEMA_REQUIRED_VIOLATION", "SCHEMA_ENUM_VIOLATION") for code in error_codes(result)),
            error_codes(result),
        )

    def test_identidad_de_artefacto_malformada(self):
        record = _built_and_reviewed_pass_record()
        record["builder_evidence"][0]["artifact"] = {"mode": "commit"}  # faltan campos requeridos por el modo
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_REQUIRED_VIOLATION", error_codes(result))

    def test_estructuralmente_valido_pero_cross_field_invalido(self):
        # Un registro que satisface el schema al pie de la letra (todos
        # los campos con la forma y el tipo correctos) pero que viola un
        # invariante cross-field (PASS con un finding presente) -- debe
        # llegar hasta la capa cross-field y ser rechazado ahí, no antes.
        record = _built_and_reviewed_pass_record()
        record["reviewer_evidence"][0]["findings"] = [
            {"id": "F1", "severity": "P2", "summary": "x", "file": None, "line_range": None, "category": "x"}
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("VERDICT_SEVERITY_MISMATCH_PASS", error_codes(result))
        # Ningún código SCHEMA_* debería aparecer -- confirma que sí pasó
        # limpio por la capa estructural y falló específicamente en la
        # cross-field.
        self.assertFalse(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))

    def test_registro_completamente_valido_pasa_ambas_capas(self):
        result = validate_mission_record(_completed_record())
        self.assertTrue(result.valid, error_codes(result))

    def test_el_registro_de_entrada_no_se_muta(self):
        record = _built_and_reviewed_pass_record()
        before = copy.deepcopy(record)
        validate_mission_record(record)
        self.assertEqual(record, before)

    def test_el_registro_de_entrada_no_se_muta_ni_cuando_es_estructuralmente_invalido(self):
        record = _minimal_intake_record()
        del record["intent"]
        before = copy.deepcopy(record)
        validate_mission_record(record)
        self.assertEqual(record, before)

    def test_registro_malformado_no_lanza_excepcion_en_capa_estructural(self):
        for bad_input in (None, ["not", "a", "dict"], "a string", 42, {"totally": "wrong"}):
            result = validate_mission_record(bad_input)
            self.assertFalse(result.valid)
            self.assertGreater(len(result.errors), 0)


class PruebaCompletedSinEvidencia(unittest.TestCase):
    def test_completed_sin_merge(self):
        record = _built_and_reviewed_pass_record()
        record["state"] = "COMPLETED"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STATE_EVIDENCE_MISSING", error_codes(result))

    def test_completed_con_merge_pero_sin_verificacion_de_deploy(self):
        record = _completed_record()
        record["deploy"]["deploy_confirmed_at"] = None
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STATE_EVIDENCE_MISSING", error_codes(result))


class PruebaConsistenciaDeHistorialDeEstado(unittest.TestCase):
    def test_state_no_coincide_con_ultimo_state_history(self):
        record = _authorized_record()
        record["state"] = "BUILDING"  # pero state_history[-1].to_state sigue siendo AUTHORIZED
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("STATE_MISMATCH_WITH_HISTORY", error_codes(result))

    def test_transicion_ilegal_en_el_historial(self):
        record = _minimal_intake_record()
        record["state"] = "COMPLETED"
        record["state_history"].append(
            {"from_state": "INTAKE", "to_state": "COMPLETED", "at": "2026-08-19T12:01:00Z", "actor": "chugel", "reason": "salto ilegal"}
        )
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("ILLEGAL_STATE_TRANSITION", error_codes(result))


class PruebaConsistenciaDeGates(unittest.TestCase):
    def test_gate_aprobado_sin_evidencia_de_jose(self):
        # Con la capa estructural (Part A) conectada, un gate "approved"
        # con evidencia nula viola el schema (decided_at/decision_ref/
        # approved_for no pueden ser null bajo el status "approved") --
        # se rechaza en la capa estructural, antes de la cross-field.
        record = _minimal_intake_record()
        record["human_gates"]["merge_authorization"] = {
            "status": "approved", "requested_at": None, "decided_at": None,
            "decided_by": None, "decision_ref": None, "approved_for": None,
        }
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))

    def test_gate_pending_con_decision_ya_registrada_es_contradictorio(self):
        record = _minimal_intake_record()
        record["human_gates"]["scope_authorization"] = {
            "status": "pending", "requested_at": "2026-08-19T12:00:00Z",
            "decided_by": "jose", "decided_at": "2026-08-19T12:05:00Z",
            "decision_ref": "audit:1", "approved_for": {"mission_definition_version": 1},
        }
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("GATE_CONTRADICTORY_METADATA", error_codes(result))

    def test_gate_rejected_con_evidencia_parcial_es_contradictorio(self):
        record = _minimal_intake_record()
        record["human_gates"]["scope_authorization"] = {
            "status": "rejected", "requested_at": "2026-08-19T12:00:00Z",
            "decided_by": "jose", "decided_at": None,
            "decision_ref": None, "approved_for": None,
        }
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("GATE_CONTRADICTORY_METADATA", error_codes(result))

    def test_decided_by_nunca_puede_ser_un_agente(self):
        # Con la capa estructural (Part A) conectada, human_gates.*.
        # decided_by ya está tipado en el schema como anyOf[null, const:
        # "jose"] -- "emilio" se rechaza en la capa estructural.
        record = _minimal_intake_record()
        record["human_gates"]["scope_authorization"]["decided_by"] = "emilio"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))


class PruebaRegistrosMalformados(unittest.TestCase):
    def test_estado_desconocido(self):
        # Con la capa estructural (Part A) conectada, "state" ya está
        # tipado como enum en el schema -- un valor fuera del vocabulario
        # se rechaza en la capa estructural (SCHEMA_ENUM_VIOLATION), antes
        # de que la capa cross-field (UNKNOWN_STATE) llegue a correr.
        record = _minimal_intake_record()
        record["state"] = "DOING_STUFF"
        record["state_history"][-1]["to_state"] = "DOING_STUFF"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ENUM_VIOLATION", error_codes(result))

    def test_registro_no_es_un_objeto(self):
        # El schema exige "type": "object" en la raíz -- una lista se
        # rechaza en la capa estructural, nunca llega a la cross-field.
        result = validate_mission_record(["not", "a", "dict"])
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_TYPE_VIOLATION", error_codes(result))

    def test_registro_none_no_lanza_excepcion(self):
        result = validate_mission_record(None)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_TYPE_VIOLATION", error_codes(result))

    def test_falta_human_gates_por_completo(self):
        # human_gates es un campo requerido en el schema -- su ausencia se
        # rechaza en la capa estructural (SCHEMA_REQUIRED_VIOLATION), un
        # código más específico que el MALFORMED_RECORD genérico que
        # producía la capa cross-field antes de que existiera esta capa.
        record = _minimal_intake_record()
        del record["human_gates"]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_REQUIRED_VIOLATION", error_codes(result))

    def test_schema_version_no_soportada(self):
        record = _minimal_intake_record()
        record["schema_version"] = "2.0.0"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("UNSUPPORTED_SCHEMA_VERSION", error_codes(result))

    def test_registro_profundamente_roto_no_lanza_excepcion(self):
        # Un caso adversarial deliberadamente feo: casi todo con la forma
        # equivocada. El validador debe devolver errores, nunca reventar.
        result = validate_mission_record({"schema_version": None, "state": 123, "human_gates": "not-a-dict"})
        self.assertFalse(result.valid)
        self.assertGreater(len(result.errors), 0)


# --- dispatch_ledger: consistencia cruzada -------------------------------

def _ledger_entry(role="emilio", attempt=0, status="RESERVED", *,
                   invocation_id="11111111-1111-4111-8111-111111111111",
                   provider=None, model=None, result_classification=None,
                   reserved_at="2026-08-19T12:00:00Z", updated_at="2026-08-19T12:00:00Z"):
    return {
        "role": role, "attempt": attempt, "invocation_id": invocation_id,
        "provider": provider, "model": model, "status": status,
        "result_classification": result_classification,
        "reserved_at": reserved_at, "updated_at": updated_at,
    }


class PruebaDispatchLedgerConsistencia(unittest.TestCase):
    def test_ausencia_de_dispatch_ledger_sigue_siendo_valida(self):
        # Backward compatibility: dispatch_ledger is deliberately absent
        # from "required" -- a record predating the ledger must still
        # validate cleanly.
        record = _minimal_intake_record()
        self.assertNotIn("dispatch_ledger", record)
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_lista_vacia_es_valida(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = []
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_entrada_reserved_bien_formada_es_valida(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(status="RESERVED")]
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_entrada_finalizada_completed_sin_disposicion_es_invalida(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(
            status="FINALIZED", provider="codex", model="codex-1",
            result_classification="completed",
        )]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("AMBIGUOUS_COMPLETED_DISPATCH", error_codes(result))

    def test_result_recorded_completed_permanece_no_resuelto(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(
            status="RESULT_RECORDED", provider="codex", model="codex-1",
            result_classification="completed",
        )]
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_finalized_completed_con_rechazo_explicito_es_valido(self):
        record = _minimal_intake_record()
        entry = _ledger_entry(
            status="FINALIZED", provider="codex", model="codex-1",
            result_classification="completed",
        )
        entry["evidence_disposition"] = "rejected"
        entry["evidence_rejection_code"] = "MISSION_EVIDENCE_VALIDATION_FAILED"
        record["dispatch_ledger"] = [entry]
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_ledger_no_es_una_lista(self):
        # The structural (JSON Schema) layer already types dispatch_ledger
        # as an array -- this is rejected there (SCHEMA_TYPE_VIOLATION),
        # before the cross-field checker ever runs. Mirrors this file's
        # own established pattern (see PruebaRegistrosMalformados).
        record = _minimal_intake_record()
        record["dispatch_ledger"] = "not-a-list"
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_TYPE_VIOLATION", error_codes(result))

    def test_entrada_no_es_un_objeto(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = ["not-a-dict"]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_TYPE_VIOLATION", error_codes(result))

    def test_attempt_bool_es_rechazado(self):
        # type(x) is int, not isinstance -- bool is a subclass of int and
        # must be rejected identically to any other malformed attempt.
        # The schema's own enum:[0,1] already rejects every one of these
        # structurally (SCHEMA_ENUM_VIOLATION for True/False/-1/2, or
        # SCHEMA_TYPE_VIOLATION for None/"0") -- the cross-field
        # MALFORMED_DISPATCH_ATTEMPT check is defense-in-depth for a
        # caller that bypasses schema validation (e.g. chugel.py's own
        # reserve_dispatch() precheck), never reachable through
        # validate_mission_record() alone once the schema is wired.
        for bad_attempt in (True, False, None, -1, 2, "0"):
            record = _minimal_intake_record()
            record["dispatch_ledger"] = [_ledger_entry(attempt=bad_attempt)]
            result = validate_mission_record(record)
            self.assertFalse(result.valid, repr(bad_attempt))
            self.assertTrue(
                any(code.startswith("SCHEMA_") for code in error_codes(result)),
                (bad_attempt, error_codes(result)),
            )

    def test_status_desconocido_es_rechazado(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(status="MADE_UP_STATUS")]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("SCHEMA_ENUM_VIOLATION", error_codes(result))

    def test_invocation_id_duplicado_entre_entradas_es_rechazado(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [
            _ledger_entry(role="emilio", attempt=0, status="FINALIZED",
                          provider="codex", model="codex-1", result_classification="failed"),
            _ledger_entry(role="emilio", attempt=1, status="RESERVED"),
        ]
        # Force a duplicate invocation_id across two otherwise-distinct entries.
        record["dispatch_ledger"][1]["invocation_id"] = record["dispatch_ledger"][0]["invocation_id"]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("DUPLICATE_INVOCATION_ID", error_codes(result))

    def test_dos_entradas_vivas_para_el_mismo_role_attempt_es_rechazado(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [
            _ledger_entry(role="emilio", attempt=0, status="RESERVED",
                          invocation_id="11111111-1111-4111-8111-111111111111"),
            _ledger_entry(role="emilio", attempt=0, status="IN_FLIGHT", provider="codex",
                          invocation_id="22222222-2222-4222-8222-222222222222"),
        ]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertIn("CONFLICTING_DISPATCH_RESERVATION", error_codes(result))

    def test_una_finalizada_y_una_viva_para_el_mismo_slot_es_valido(self):
        # A FINALIZED entry never counts toward the "at most one live
        # entry" rule -- exactly what reserve_dispatch()'s own
        # supersession leaves behind after a retryable redispatch.
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [
            _ledger_entry(role="emilio", attempt=0, status="FINALIZED", provider="codex",
                          result_classification="timeout",
                          invocation_id="11111111-1111-4111-8111-111111111111"),
            _ledger_entry(role="emilio", attempt=0, status="RESERVED",
                          invocation_id="22222222-2222-4222-8222-222222222222"),
        ]
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_entradas_vivas_para_slots_distintos_son_validas(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [
            _ledger_entry(role="emilio", attempt=0, status="RESERVED",
                          invocation_id="11111111-1111-4111-8111-111111111111"),
            _ledger_entry(role="emma", attempt=0, status="RESERVED",
                          invocation_id="22222222-2222-4222-8222-222222222222"),
        ]
        result = validate_mission_record(record)
        self.assertTrue(result.valid, error_codes(result))

    def test_reserved_con_provider_no_nulo_es_rechazado_por_el_schema(self):
        # Structural layer (Part A / JSON Schema allOf), not the
        # cross-field checker -- RESERVED requires provider/model/
        # result_classification all null.
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(status="RESERVED", provider="codex")]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))

    def test_result_recorded_sin_result_classification_es_rechazado_por_el_schema(self):
        record = _minimal_intake_record()
        record["dispatch_ledger"] = [_ledger_entry(
            status="RESULT_RECORDED", provider="codex", model="codex-1",
            result_classification=None,
        )]
        result = validate_mission_record(record)
        self.assertFalse(result.valid)
        self.assertTrue(any(code.startswith("SCHEMA_") for code in error_codes(result)), error_codes(result))


if __name__ == "__main__":
    unittest.main()
