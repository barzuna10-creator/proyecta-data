"""Pruebas para orchestrator/wiring.py (Incremento #15 -- capa de
integración controlada, un solo paso por llamada).

Ninguna prueba invoca un LLM, red o subprocess. Cada AgentInvoker es un
stub determinista construido a mano; ningún adapter real
(claude_agent_sdk/openai_codex) se importa ni se instala en este entorno."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel
import orchestrator.provider_router as router
import orchestrator.wiring as wiring


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


def _scope_gate_approval():
    return {
        "status": "approved",
        "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z",
        "decided_by": "jose",
        "decision_ref": "ref-scope-1",
        "approved_for": {"mission_definition_version": 1},
    }


def _artifact_commit(sha=None):
    return {
        "mode": "commit",
        "commit_sha": sha or ("a" * 40),
        "patch_path": None,
        "patch_sha256": None,
        "patch_byte_size": None,
    }


def _builder_evidence(attempt=0, artifact=None, conclusion_text="SENTINEL_CONCLUSION",
                      persisted=False, provider="codex", session_id=None,
                      conversation_id="builder-thread"):
    evidence = {
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
            "no_existing_work_altered": True, "no_main_change": True,
            "no_remote_action": True, "no_production_access": True,
            "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": "SENTINEL_HANDOFF_REF",
        "conclusion": {"text": conclusion_text, "label": "FACT"},
    }
    if persisted:
        evidence.update({
            "invocation_id": "11111111-1111-4111-8111-111111111111",
            "provider": provider,
            "provider_session_id": session_id,
            "provider_conversation_id": conversation_id,
        })
    return evidence


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


def _result_fields(
    outcome="completed",
    evidence=None,
    provider="codex",
    model="codex-1",
    fresh_context_attested=True,
    provider_session_id=None,
    provider_conversation_id=None,
    error_detail=None,
    invocation_id=None,
):
    """A template dict for _StubAdapter -- never a complete
    AgentInvocationResult on its own, since a real adapter always echoes
    back whichever invocation_id the request it actually received
    carried, never one a test fixture guessed in advance."""
    return dict(
        outcome=outcome, evidence=evidence, provider=provider, model=model,
        fresh_context_attested=fresh_context_attested,
        provider_session_id=provider_session_id,
        provider_conversation_id=provider_conversation_id,
        error_detail=error_detail, invocation_id=invocation_id,
    )


class _StubAdapter:
    """Deterministic, hand-built AgentInvoker -- never touches a real
    provider. Echoes the real request.invocation_id it actually received
    into its response by default (exactly what any correct real adapter
    must do), unless a template explicitly overrides invocation_id (used
    only by the invocation-ID-mismatch tests). Records every call it
    receives so tests can assert exactly one invocation happened, and
    exactly what request it saw."""

    def __init__(self, templates):
        self._templates = list(templates) if isinstance(templates, list) else [templates]
        self.calls: list[ai.AgentInvocationRequest] = []

    def invoke(self, request: ai.AgentInvocationRequest) -> ai.AgentInvocationResult:
        self.calls.append(request)
        fields = dict(self._templates.pop(0))
        invocation_id = fields.pop("invocation_id", None) or request.invocation_id
        return ai.AgentInvocationResult(
            invocation_id=invocation_id,
            responded_at="2026-08-19T12:30:00Z",
            **fields,
        )


class WiringTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

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
        chugel.decide_gate(mid, "scope_authorization", _scope_gate_approval())
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")
        chugel.transition(mid, "BUILDING", actor="chugel", reason="isolated build starts")
        return mid

    def _mission_ready_for_emma(self, attempt=0):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=attempt, persisted=True))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")
        return mid


# --- despacho correcto por rol -------------------------------------------

class PruebaDespachoDeAdapter(WiringTestCase):
    def test_emilio_despacha_a_codex_por_configuracion_default(self):
        mid = self._mission_ready_for_emilio()
        claude_stub = _StubAdapter([])
        codex_stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(
            mid, 0, adapters={"codex": codex_stub, "claude": claude_stub},
        )
        self.assertEqual(outcome.routing_decision.adapter_name, "codex")
        self.assertEqual(claude_stub.calls, [])

    def test_emma_despacha_a_claude_por_configuracion_default(self):
        mid = self._mission_ready_for_emma()
        codex_stub = _StubAdapter([])
        claude_stub = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        outcome = wiring.run_emma_attempt(
            mid, 0, adapters={"codex": codex_stub, "claude": claude_stub},
        )
        self.assertEqual(outcome.routing_decision.adapter_name, "claude")
        self.assertEqual(codex_stub.calls, [])

    def test_config_personalizada_invierte_el_despacho(self):
        mid = self._mission_ready_for_emilio()
        swapped = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(primary="claude", fallback="codex"),
            "emma": router.RoleProviderPolicy(primary="codex", fallback="claude"),
        })
        claude_stub = _StubAdapter([_result_fields(provider="claude", evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(
            mid, 0,
            adapters={"claude": claude_stub, "codex": _StubAdapter([])},
            config=swapped,
        )
        self.assertEqual(outcome.routing_decision.adapter_name, "claude")


# --- exactamente una invocación por llamada -------------------------------

class PruebaUnaSolaInvocacion(WiringTestCase):
    def test_run_emilio_attempt_invoca_exactamente_una_vez(self):
        mid = self._mission_ready_for_emilio()
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub, "claude": _StubAdapter([])})
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(outcome.result.outcome, "completed")


class PruebaAutorizacionAntesDeInvocar(WiringTestCase):
    def test_emilio_no_se_invoca_desde_intake(self):
        mid = _create_intake_mission("sin autorización")["mission_id"]
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])

        with self.assertRaises(ai.InvocationNotAuthorized):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})

        self.assertEqual(stub.calls, [])
        self.assertEqual(chugel.get_mission(mid)["builder_evidence"], [])

    def test_emilio_no_reinvoca_un_intento_ya_persistido(self):
        mid = self._mission_ready_for_emilio()
        first = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        wiring.run_emilio_attempt(mid, 0, adapters={"codex": first})
        duplicate = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])

        with self.assertRaises(ai.InvocationNotAuthorized):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": duplicate})

        self.assertEqual(duplicate.calls, [])

    def test_emma_no_reinvoca_un_intento_ya_persistido(self):
        mid = self._mission_ready_for_emma()
        first = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        wiring.run_emma_attempt(mid, 0, adapters={"claude": first})
        duplicate = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])

        with self.assertRaises(ai.InvocationNotAuthorized):
            wiring.run_emma_attempt(
                mid, 0, adapters={"claude": duplicate},
            )

        self.assertEqual(duplicate.calls, [])

    def test_attempt_bool_o_fuera_de_rango_no_llega_al_provider(self):
        mid = self._mission_ready_for_emilio()
        for attempt in (None, False, True, -1, 2, "0"):
            with self.subTest(attempt=attempt):
                stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
                with self.assertRaises(ai.InvocationNotAuthorized):
                    wiring.run_emilio_attempt(mid, attempt, adapters={"codex": stub})
                self.assertEqual(stub.calls, [])

    def test_emilio_attempt_1_no_despacha_desde_building(self):
        mid = self._mission_ready_for_emilio()
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(1))])
        with self.assertRaises(ai.InvocationNotAuthorized):
            wiring.run_emilio_attempt(mid, 1, adapters={"codex": stub})
        self.assertEqual(stub.calls, [])

    def test_emma_no_despacha_fuera_de_reviewing(self):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _builder_evidence(0, persisted=True))
        stub = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        with self.assertRaises(ai.InvocationNotAuthorized):
            wiring.run_emma_attempt(mid, 0, adapters={"claude": stub})
        self.assertEqual(stub.calls, [])

    def test_run_emma_attempt_invoca_exactamente_una_vez(self):
        mid = self._mission_ready_for_emma()
        stub = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        outcome = wiring.run_emma_attempt(
            mid, 0, adapters={"claude": stub, "codex": _StubAdapter([])},
        )
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(outcome.result.outcome, "completed")


# --- evidencia escrita exactamente una vez --------------------------------

class PruebaEscrituraDeEvidencia(WiringTestCase):
    def test_emilio_completado_escribe_builder_evidence_una_vez(self):
        mid = self._mission_ready_for_emilio()
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})
        self.assertIsNotNone(outcome.updated_mission)
        self.assertEqual(len(outcome.updated_mission["builder_evidence"]), 1)
        on_disk = chugel.get_mission(mid)
        self.assertEqual(len(on_disk["builder_evidence"]), 1)

    def test_emma_completada_escribe_reviewer_evidence_una_vez(self):
        mid = self._mission_ready_for_emma()
        stub = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        outcome = wiring.run_emma_attempt(mid, 0, adapters={"claude": stub})
        self.assertIsNotNone(outcome.updated_mission)
        self.assertEqual(len(outcome.updated_mission["reviewer_evidence"]), 1)
        on_disk = chugel.get_mission(mid)
        self.assertEqual(len(on_disk["reviewer_evidence"]), 1)

    def test_outcomes_no_completados_no_escriben_nada(self):
        mid = self._mission_ready_for_emilio()
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        for bad_outcome in ("failed", "timeout", "unavailable", "invalid_output"):
            stub = _StubAdapter([_result_fields(outcome=bad_outcome, evidence=None, error_detail="boom")])
            outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})
            self.assertIsNone(outcome.updated_mission, msg=bad_outcome)
            self.assertEqual(path.read_bytes(), before, msg=bad_outcome)

    def test_emma_outcomes_no_completados_no_escriben_nada(self):
        mid = self._mission_ready_for_emma()
        path = chugel._mission_path(mid)
        before = path.read_bytes()

        for bad_outcome in ("failed", "timeout", "unavailable", "invalid_output"):
            stub = _StubAdapter([_result_fields(provider="claude", outcome=bad_outcome,
                                                 evidence=None, error_detail="boom")])
            outcome = wiring.run_emma_attempt(
                mid, 0, adapters={"claude": stub},
            )
            self.assertIsNone(outcome.updated_mission, msg=bad_outcome)
            self.assertEqual(path.read_bytes(), before, msg=bad_outcome)

    def test_emma_mismo_provider_sin_identidad_no_completada_sigue_reintentable(self):
        for provider in ("claude", "codex"):
            for bad_outcome in ("failed", "timeout", "unavailable", "invalid_output"):
                with self.subTest(provider=provider, outcome=bad_outcome):
                    mid = self._mission_ready_for_emilio()
                    chugel.record_builder_evidence(mid, _builder_evidence(
                        0, persisted=True, provider=provider,
                        session_id=("builder-session" if provider == "claude" else None),
                        conversation_id=("builder-thread" if provider == "codex" else None),
                    ))
                    chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
                    chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
                    chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")
                    before = chugel._mission_path(mid).read_bytes()
                    templates = [_result_fields(
                        provider=provider, outcome=bad_outcome, evidence=None,
                        provider_session_id=None, provider_conversation_id=None,
                        error_detail="provider did not complete",
                    )] * 2
                    stub = _StubAdapter(templates)
                    config = router.ProviderConfig(roles={
                        "emma": router.RoleProviderPolicy(
                            primary=provider,
                            fallback=("codex" if provider == "claude" else "claude"),
                        ),
                    })

                    first = wiring.run_emma_attempt(
                        mid, 0, adapters={provider: stub}, config=config,
                    )
                    second = wiring.run_emma_attempt(
                        mid, 0, adapters={provider: stub}, config=config,
                    )

                    self.assertIsNone(first.updated_mission)
                    self.assertIsNone(second.updated_mission)
                    self.assertEqual(first.attempt_record.outcome, bad_outcome)
                    self.assertEqual(second.attempt_record.outcome, bad_outcome)
                    self.assertEqual(len(stub.calls), 2)
                    self.assertEqual(chugel._mission_path(mid).read_bytes(), before)


# --- fallo cerrado por invocation_id --------------------------------------

class PruebaInvocationIdMismatch(WiringTestCase):
    def test_emilio_id_incorrecto_lanza_y_no_escribe(self):
        mid = self._mission_ready_for_emilio()
        path = chugel._mission_path(mid)
        before = path.read_bytes()
        stub = _StubAdapter([_result_fields(
            evidence=_builder_evidence(0), invocation_id="un-id-completamente-distinto",
        )])
        with self.assertRaises(ai.InvocationIdMismatch):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})
        self.assertEqual(path.read_bytes(), before)

    def test_emma_id_incorrecto_lanza_y_no_escribe(self):
        mid = self._mission_ready_for_emma()
        path = chugel._mission_path(mid)
        before = path.read_bytes()
        stub = _StubAdapter([_result_fields(
            provider="claude", evidence=_reviewer_evidence(0), invocation_id="otro-id-distinto",
        )])
        with self.assertRaises(ai.InvocationIdMismatch):
            wiring.run_emma_attempt(mid, 0, adapters={"claude": stub})
        self.assertEqual(path.read_bytes(), before)


# --- Emma recibe solo su allow-list -- confirmado a través del wiring ----

class PruebaAllowListDeEmmaViaWiring(WiringTestCase):
    def test_request_visto_por_el_stub_no_contiene_juicio_de_emilio(self):
        mid = self._mission_ready_for_emma()
        stub = _StubAdapter([_result_fields(provider="claude", evidence=_reviewer_evidence(0))])
        wiring.run_emma_attempt(mid, 0, adapters={"claude": stub})

        self.assertEqual(len(stub.calls), 1)
        seen_task = json.dumps(stub.calls[0].task)
        for sentinel in (
            "SENTINEL_CONCLUSION", "SENTINEL_RISK", "SENTINEL_ASSUMPTION", "SENTINEL_ROLLBACK_NOTES",
        ):
            self.assertNotIn(sentinel, seen_task, f"{sentinel} leaked into what the adapter actually received")
        self.assertIn("SENTINEL_CHANGED_FILE.py", seen_task)
        self.assertTrue(stub.calls[0].requested_fresh_context)


# --- identidad persistida de Emilio gobierna la independencia de Emma ----

_EMMA_CODEX_CONFIG = router.ProviderConfig(roles={
    "emma": router.RoleProviderPolicy(primary="codex", fallback="claude"),
})


class PruebaFrescuraDeEmmaViaWiring(WiringTestCase):
    def _ejecutar_emilio_y_abrir_revision(self, mid, *, session_id=None,
                                          conversation_id=None, provider="claude"):
        config = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(primary=provider, fallback=("codex" if provider == "claude" else "claude")),
        })
        stub = _StubAdapter([_result_fields(
            provider=provider, provider_session_id=session_id, provider_conversation_id=conversation_id,
            evidence=_builder_evidence(0),
        )])
        wiring.run_emilio_attempt(mid, 0, adapters={provider: stub}, config=config)
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")

    def test_restart_resume_usa_identidad_persistida_y_detecta_reuso(self):
        mid = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            mid, conversation_id="codex-thread-real", provider="codex",
        )
        reloaded = chugel.get_mission(mid)
        self.assertEqual(reloaded["builder_evidence"][0]["provider_conversation_id"],
                         "codex-thread-real")
        emma_stub = _StubAdapter([_result_fields(
            provider="codex", provider_conversation_id="codex-thread-real",
            evidence=_reviewer_evidence(0),
        )])
        with self.assertRaises(ai.StaleSessionReused):
            wiring.run_emma_attempt(mid, 0, adapters={"codex": emma_stub},
                                    config=_EMMA_CODEX_CONFIG)

    def test_mismo_session_id_es_rechazado(self):
        mid = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            mid, session_id="claude-session-SAME", provider="claude",
        )
        emma_stub = _StubAdapter([_result_fields(
            provider="claude", provider_session_id="claude-session-SAME",
            evidence=_reviewer_evidence(0),
        )])
        with self.assertRaises(ai.StaleSessionReused):
            wiring.run_emma_attempt(mid, 0, adapters={"claude": emma_stub})

    def test_identidad_distinta_es_aceptada(self):
        mid = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            mid, session_id="claude-session-builder", provider="claude",
        )
        emma_stub = _StubAdapter([_result_fields(
            provider="claude", provider_session_id="claude-session-reviewer",
            evidence=_reviewer_evidence(0),
        )])
        outcome = wiring.run_emma_attempt(mid, 0, adapters={"claude": emma_stub})
        self.assertIsNotNone(outcome.updated_mission)

    def test_builder_historico_sin_identidad_no_despacha(self):
        mid = self._mission_ready_for_emilio()
        chugel.record_builder_evidence(mid, _builder_evidence(0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")
        emma_stub = _StubAdapter([_result_fields(
            provider="claude", provider_session_id="reviewer",
            evidence=_reviewer_evidence(0),
        )])
        with self.assertRaises(ai.PersistedBuilderIdentityUnavailable):
            wiring.run_emma_attempt(mid, 0, adapters={"claude": emma_stub})
        self.assertEqual(emma_stub.calls, [])

    def test_otra_mision_no_participa_en_la_comparacion(self):
        other = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            other, conversation_id="other-thread", provider="codex",
        )
        target = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            target, conversation_id="target-thread", provider="codex",
        )
        emma_stub = _StubAdapter([_result_fields(
            provider="codex", provider_conversation_id="other-thread",
            evidence=_reviewer_evidence(0),
        )])
        outcome = wiring.run_emma_attempt(target, 0, adapters={"codex": emma_stub},
                                          config=_EMMA_CODEX_CONFIG)
        self.assertIsNotNone(outcome.updated_mission)

    def test_otro_attempt_no_participa_en_la_comparacion(self):
        mid = self._mission_ready_for_emilio()
        self._ejecutar_emilio_y_abrir_revision(
            mid, conversation_id="attempt-zero-thread", provider="codex",
        )
        reviewer0 = _StubAdapter([_result_fields(
            provider="claude", provider_session_id="review-zero",
            evidence=_reviewer_evidence(
                0, verdict="CHANGES_REQUIRED",
                findings=[{"id": "f1", "severity": "P1", "summary": "fix",
                           "file": "x.py", "line_range": "1", "category": "bug"}],
            ),
        )])
        wiring.run_emma_attempt(mid, 0, adapters={"claude": reviewer0})
        chugel.transition(mid, "CHANGES_REQUIRED", actor="chugel", reason="review")
        chugel.transition(mid, "CORRECTING", actor="jose", reason="correct")
        builder1 = _StubAdapter([_result_fields(
            provider="codex", provider_conversation_id="attempt-one-thread",
            evidence=_builder_evidence(1),
        )])
        wiring.run_emilio_attempt(mid, 1, adapters={"codex": builder1})
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")

        reviewer1 = _StubAdapter([_result_fields(
            provider="codex", provider_conversation_id="attempt-zero-thread",
            evidence=_reviewer_evidence(1),
        )])
        outcome = wiring.run_emma_attempt(mid, 1, adapters={"codex": reviewer1},
                                          config=_EMMA_CODEX_CONFIG)
        self.assertIsNotNone(outcome.updated_mission)


# --- sin mutación de gates, sin avance automático de estado ---------------

class PruebaSinEfectosSecundariosNoAutorizados(WiringTestCase):
    def test_human_gates_no_cambia(self):
        mid = self._mission_ready_for_emilio()
        before = chugel.get_mission(mid)["human_gates"]
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})
        after = chugel.get_mission(mid)["human_gates"]
        self.assertEqual(before, after)

    def test_state_no_avanza_automaticamente(self):
        mid = self._mission_ready_for_emilio()
        before_state = chugel.get_mission(mid)["state"]
        stub = _StubAdapter([_result_fields(evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})
        self.assertEqual(before_state, "BUILDING")
        self.assertEqual(outcome.updated_mission["state"], "BUILDING")
        self.assertEqual(chugel.get_mission(mid)["state"], "BUILDING")

    def test_wiring_nunca_importa_decide_gate_ni_transition(self):
        """A nivel de bytecode, no de texto del docstring (que sí
        menciona ambos nombres en prosa, explicando precisamente que
        nunca se llaman)."""
        for func in (
            wiring.run_emilio_attempt, wiring.run_emma_attempt, wiring._select_and_dispatch,
        ):
            self.assertNotIn("decide_gate", func.__code__.co_names)
            self.assertNotIn("transition", func.__code__.co_names)
        self.assertNotIn("chugel", dir(wiring))


# --- sin reintento/failover autónomo tras un fallo ------------------------

class PruebaSinReintentoAutonomo(WiringTestCase):
    def test_fallo_de_proveedor_no_dispara_una_segunda_invocacion(self):
        mid = self._mission_ready_for_emilio()
        codex_stub = _StubAdapter([_result_fields(outcome="unavailable", error_detail="down")])
        claude_stub = _StubAdapter([])  # nunca debe ser llamado
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": codex_stub, "claude": claude_stub})
        self.assertEqual(len(codex_stub.calls), 1)
        self.assertEqual(len(claude_stub.calls), 0)
        self.assertEqual(outcome.result.outcome, "unavailable")
        self.assertIsNone(outcome.updated_mission)

    def test_attempt_record_del_fallo_esta_disponible_para_un_siguiente_llamado_autorizado(self):
        """El wiring nunca decide por sí mismo intentar el fallback --
        pero sí entrega el AttemptRecord que un llamador humano usaría
        para autorizar explícitamente esa siguiente llamada."""
        mid = self._mission_ready_for_emilio()
        codex_stub = _StubAdapter([_result_fields(outcome="unavailable", error_detail="down")])
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": codex_stub})
        self.assertEqual(outcome.attempt_record.outcome, "unavailable")
        self.assertEqual(outcome.attempt_record.provider, "codex")

        # Un segundo, distinto, explícitamente-autorizado-por-el-llamador
        # run_emilio_attempt() usando ese AttemptRecord sí resulta en
        # failover -- pero es una llamada separada, nunca automática.
        claude_stub = _StubAdapter([_result_fields(provider="claude", evidence=_builder_evidence(0))])
        second_outcome = wiring.run_emilio_attempt(
            mid, 0, adapters={"claude": claude_stub},
            prior_attempts=(outcome.attempt_record,),
        )
        self.assertEqual(second_outcome.routing_decision.adapter_name, "claude")
        self.assertEqual(second_outcome.routing_decision.reason, "failover_after_unavailable")


# --- selección de adapter desconocida/malformada falla cerrado -----------

class PruebaAdapterDesconocidoFallaCerrado(WiringTestCase):
    def test_adapters_incompleto_lanza_unknown_adapter_selected(self):
        mid = self._mission_ready_for_emilio()
        with self.assertRaises(wiring.UnknownAdapterSelected):
            wiring.run_emilio_attempt(mid, 0, adapters={"claude": _StubAdapter([])})

    def test_adapters_vacio_lanza(self):
        mid = self._mission_ready_for_emilio()
        with self.assertRaises(wiring.UnknownAdapterSelected):
            wiring.run_emilio_attempt(mid, 0, adapters={})

    def test_ningun_invoke_ocurre_cuando_el_adapter_es_desconocido(self):
        mid = self._mission_ready_for_emilio()
        claude_stub = _StubAdapter([])
        with self.assertRaises(wiring.UnknownAdapterSelected):
            wiring.run_emilio_attempt(mid, 0, adapters={"claude": claude_stub})
        self.assertEqual(len(claude_stub.calls), 0)


# --- P2: el proveedor despachado debe coincidir con la decisión de ruteo -

class PruebaProviderMismatchFallaCerrado(WiringTestCase):
    def test_emilio_provider_distinto_al_ruteado_falla_cerrado(self):
        """select_adapter() eligió "codex", pero el adapter registrado
        bajo esa clave (mal configurado, o con un bug) responde
        provider="claude" -- debe rechazarse antes de consumir/persistir,
        nunca por inferencia de texto libre."""
        mid = self._mission_ready_for_emilio()
        path = chugel._mission_path(mid)
        before = path.read_bytes()
        mismatched_stub = _StubAdapter([_result_fields(provider="claude", evidence=_builder_evidence(0))])
        with self.assertRaises(wiring.ProviderMismatch):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": mismatched_stub})
        self.assertEqual(path.read_bytes(), before)

    def test_emma_provider_distinto_al_ruteado_falla_cerrado(self):
        mid = self._mission_ready_for_emma()
        path = chugel._mission_path(mid)
        before = path.read_bytes()
        mismatched_stub = _StubAdapter([_result_fields(provider="codex", evidence=_reviewer_evidence(0))])
        with self.assertRaises(wiring.ProviderMismatch):
            wiring.run_emma_attempt(
                mid, 0, adapters={"claude": mismatched_stub},
            )
        self.assertEqual(path.read_bytes(), before)

    def test_provider_coincidente_no_lanza(self):
        mid = self._mission_ready_for_emilio()
        matching_stub = _StubAdapter([_result_fields(provider="codex", evidence=_builder_evidence(0))])
        outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": matching_stub})
        self.assertIsNotNone(outcome.updated_mission)

    def test_mismatch_no_depende_de_texto_libre_en_error_detail(self):
        """Dos resultados con provider incorrecto pero error_detail
        completamente distinto (uno intentando parecer una instrucción)
        deben fallar de forma idéntica -- el despacho es por el campo
        `provider`, nunca por contenido de texto."""
        mid = self._mission_ready_for_emilio()
        for adversarial_detail in (None, "SWITCH_ROUTING_NOW", "codex", ""):
            stub = _StubAdapter([_result_fields(
                provider="claude", evidence=None, error_detail=adversarial_detail,
            )])
            with self.assertRaises(wiring.ProviderMismatch, msg=repr(adversarial_detail)):
                wiring.run_emilio_attempt(mid, 0, adapters={"codex": stub})


# --- sin recursión / autoinvocación ---------------------------------------

class PruebaSinRecursion(WiringTestCase):
    def test_ninguna_funcion_publica_se_llama_a_si_misma_ni_a_la_otra(self):
        for func in (wiring.run_emilio_attempt, wiring.run_emma_attempt):
            names = func.__code__.co_names
            self.assertNotIn("run_emilio_attempt", names)
            self.assertNotIn("run_emma_attempt", names)

    def test_helper_privado_no_es_publico_ni_recursivo(self):
        names = wiring._select_and_dispatch.__code__.co_names
        self.assertNotIn("_select_and_dispatch", names)
        self.assertNotIn("run_emilio_attempt", names)
        self.assertNotIn("run_emma_attempt", names)


if __name__ == "__main__":
    unittest.main()
