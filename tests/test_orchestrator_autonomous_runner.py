"""Real-persistence tests for orchestrator/autonomous_runner.py (durable
dispatch corrective cycle #4).

Every test in this file drives an actual Chugel Mission Record file in a
temporary directory through the real wiring.py / agent_invocation.py /
chugel.py call chain -- create_mission(), reserve_dispatch(),
mark_dispatch_in_flight(), record_dispatch_result(), finalize_dispatch(),
record_builder_evidence()/record_reviewer_evidence(), transition() are all
the genuine functions, never mocked. Only provider execution is
fake/deterministic (a hand-built AgentInvoker, _FakeAdapter/
_NeverCalledAdapter below) -- no real provider, no real credentials.

This replaces the prior all-mock version of this file, which asserted only
on unittest.mock call counts against orchestrator.autonomous_runner.chugel
and never touched a real Mission Record file -- exactly the persistence
coverage gap this corrective cycle's authorization calls out ("Replace/
mock-only restart tests where they falsely claim persistence coverage").
Restart/crash scenarios are simulated by calling the real chugel dispatch
primitives directly to leave a ledger entry at a specific lifecycle status
(RESERVED / IN_FLIGHT / RESULT_RECORDED), then calling run_mission() fresh
-- exactly what a second process would observe after a real crash, since
this coordinator holds no in-memory state of its own (see
orchestrator/autonomous_runner.py's own module docstring)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter
from orchestrator.autonomous_runner import (
    _durable_attempt_counts,
    _emilio_schema_attempt,
    _emma_schema_attempt,
    run_mission,
)


# --- fixtures: real Chugel missions, no shortcuts -------------------------

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
    return {"mode": "commit", "commit_sha": sha or ("a" * 40),
            "patch_path": None, "patch_sha256": None, "patch_byte_size": None}


def _builder_evidence(attempt=0, conclusion_text="ok"):
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": _artifact_commit(),
        "changed_files": [{"path": "a.py", "reason": "x"}],
        "checks": [{"command": "true", "working_directory": "/tmp", "exit_status": 0, "result": "ok"}],
        "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True, "no_remote_action": True,
            "no_production_access": True, "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": None,
        "conclusion": {"text": conclusion_text, "label": "FACT"},
    }


def _default_findings_for_verdict(verdict):
    # CHANGES_REQUIRED requires at least one P1/P2 finding -- validator.py's
    # VERDICT_SEVERITY_MISMATCH_CHANGES_REQUIRED cross-field check.
    if verdict == "CHANGES_REQUIRED":
        return [{"id": "f1", "severity": "P1", "summary": "bug", "file": "a.py",
                  "line_range": "1-2", "category": "correctness"}]
    return []


def _reviewer_evidence(attempt=0, verdict="PASS", findings=None):
    art = _artifact_commit()
    return {
        "attempt": attempt,
        "invoked_at": "2026-08-19T12:05:00Z",
        "artifact_identity_confirmed_at_start": art,
        "artifact_identity_confirmed_before_conclusion": art,
        "rechecked_commands": [],
        "findings": findings if findings is not None else _default_findings_for_verdict(verdict),
        "verdict": verdict,
        "blocked_reason": "boom" if verdict == "BLOCKED" else None,
    }


def _emilio_completed_template(attempt=0, conversation_id="builder-thread"):
    return dict(
        outcome="completed", model="codex-1", fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=conversation_id,
        evidence=_builder_evidence(attempt=attempt), error_detail=None, provider="codex",
    )


def _emma_completed_template(attempt=0, verdict="PASS", conversation_id="reviewer-thread"):
    return dict(
        outcome="completed", model="claude-1", fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=conversation_id,
        evidence=_reviewer_evidence(attempt=attempt, verdict=verdict), error_detail=None, provider="claude",
    )


def _failed_template(provider):
    return dict(
        outcome="failed", model=None, fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=None,
        evidence=None, error_detail="boom", provider=provider,
    )


class _FakeAdapter(CodexCliAdapter):
    """Deterministic AgentInvoker -- never touches a real provider. Always
    echoes request.invocation_id back (exactly what a correct real adapter
    must do), and raises AssertionError if invoked more times than the
    test supplied templates for -- so a test that expects exactly N real
    dispatches fails loudly on an unexpected N+1th call, rather than
    silently reusing a stale template.

    Corrective #7: subclasses `CodexCliAdapter` (arbitrarily -- see the
    identical note on `_StubAdapter` in tests/test_orchestrator_wiring.py)
    purely so `wiring._select_and_dispatch()`'s new
    `isinstance(adapter, _SUBSCRIPTION_ONLY_ADAPTER_TYPES)` check accepts
    it. `__init__` is fully overridden and never calls
    `CodexCliAdapter.__init__` -- no real CLI dependency of any kind."""

    def __init__(self, templates):
        self._templates = list(templates)
        self.calls: list[ai.AgentInvocationRequest] = []

    def invoke(self, request: ai.AgentInvocationRequest) -> ai.AgentInvocationResult:
        self.calls.append(request)
        if not self._templates:
            raise AssertionError(
                f"adapter invoked more times than this test authorized "
                f"(request={request!r})"
            )
        fields = dict(self._templates.pop(0))
        return ai.AgentInvocationResult(
            invocation_id=request.invocation_id,
            responded_at="2026-08-19T12:30:00Z",
            **fields,
        )


class _NeverCalledAdapter:
    """For every restart/crash scenario where run_mission() must make
    zero provider calls -- any invoke() call at all is the test failure,
    not a particular wrong argument."""

    def invoke(self, request: ai.AgentInvocationRequest) -> ai.AgentInvocationResult:
        raise AssertionError(
            f"provider must never be invoked for this restart scenario, but was "
            f"called with {request!r}"
        )


class AutonomousRunnerRealPersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_authorized(self):
        """Reaches AUTHORIZED -- run_mission() itself performs the
        AUTHORIZED -> BUILDING transition automatically, exactly like a
        fresh mission a human just authorized (never an automatic human
        gate approval -- that gate was already explicitly decided above,
        by _scope_gate_approval())."""
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
        return mid

    def _mission_building(self):
        mid = self._mission_authorized()
        chugel.transition(mid, "BUILDING", actor="chugel", reason="build starts")
        return mid

    def _mission_publish_awaiting_authorization(self):
        """Real pass-path pipeline via run_mission() itself, reused as a
        fixture for tests that need PUBLISH_AWAITING_AUTHORIZATION as a
        starting point rather than an assertion."""
        mid = self._mission_authorized()
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        }
        run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(chugel.get_mission(mid)["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        return mid

    def _mission_merge_awaiting_authorization(self):
        mid = self._mission_publish_awaiting_authorization()
        chugel.transition(mid, "PUBLISHING", actor="jose", reason="publish authorized")
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
        return mid


# --- full pipeline, real persistence end to end ---------------------------

class PruebaPipelineCompletoConPersistenciaReal(AutonomousRunnerRealPersistenceTestCase):
    def test_authorized_hasta_publish_awaiting_authorization_camino_pass(self):
        mid = self._mission_authorized()
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="PASS")]),
        }
        result = run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "PUBLISH_AWAITING_AUTHORIZATION")

        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 2)
        for entry in ledger:
            self.assertEqual(entry["status"], "FINALIZED")
            self.assertEqual(entry["result_classification"], "completed")
        emilio_entries = [e for e in ledger if e["role"] == "emilio"]
        emma_entries = [e for e in ledger if e["role"] == "emma"]
        self.assertEqual(len(emilio_entries), 1)
        self.assertEqual(len(emma_entries), 1)
        self.assertEqual(len(adapters["codex"].calls), 1)
        self.assertEqual(len(adapters["claude"].calls), 1)

    def test_corrective_cycle_persiste_resume_y_despacha_emma_fresca(self):
        """Two SEPARATE run_mission() calls -- the second simulating a
        fresh process after a restart -- driven purely by persisted
        state, no in-memory continuation. The first call's budget is
        deliberately exhausted right after Emma's CHANGES_REQUIRED
        verdict transitions the mission into CORRECTING, so the second
        call resumes CHANGES_REQUIRED-derived CORRECTING state exactly as
        a real restart would, and must independently derive that Emilio's
        next dispatch is schema attempt=1 (never a locally-remembered
        counter) and that Emma's re-review is also attempt=1 (derived
        from the persisted attempt=1 builder_evidence entry, never from
        the corrected evidence being "fresh" in memory)."""
        mid = self._mission_authorized()
        first_round_adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=0, verdict="CHANGES_REQUIRED")]),
        }
        first_result = run_mission(mid, first_round_adapters, max_total_attempts=2)
        self.assertEqual(first_result.status, "HUMAN_ACTION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "CORRECTING")
        self.assertEqual(len(first_round_adapters["codex"].calls), 1)
        self.assertEqual(len(first_round_adapters["claude"].calls), 1)

        # Simulated restart: a brand new adapters mapping, no shared
        # in-memory state with the first call at all.
        second_round_adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=1)]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=1, verdict="PASS")]),
        }
        second_result = run_mission(mid, second_round_adapters, max_total_attempts=4)
        self.assertEqual(second_result.status, "AUTHORIZATION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        self.assertEqual(record["corrective_cycle_count"], 1)

        builder_attempts = sorted(e["attempt"] for e in record["builder_evidence"])
        reviewer_attempts = sorted(e["attempt"] for e in record["reviewer_evidence"])
        self.assertEqual(builder_attempts, [0, 1])
        self.assertEqual(reviewer_attempts, [0, 1])

        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 4)
        for entry in ledger:
            self.assertEqual(entry["status"], "FINALIZED")
        emilio_attempt_1_entries = [e for e in ledger if e["role"] == "emilio" and e["attempt"] == 1]
        self.assertEqual(len(emilio_attempt_1_entries), 1)
        # All four dispatches across both calls carry distinct invocation
        # ids -- the second call never reused anything from the first.
        self.assertEqual(len({e["invocation_id"] for e in ledger}), 4)

    def test_attempt_1_completed_con_evidencia_rechazada_se_recupera_solo_tras_disposicion(self):
        """Regression for real dispatch 6: Emma returned
        PASS_WITH_NON_BLOCKING_FINDINGS with a P2, so the provider result was
        completed but canonical evidence validation rejected it."""
        mid = self._mission_authorized()
        first = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([
                _emma_completed_template(attempt=0, verdict="CHANGES_REQUIRED")
            ]),
        }
        self.assertEqual(run_mission(mid, first, max_total_attempts=2).state, "CORRECTING")

        p2 = [{"id": "f6", "severity": "P2", "summary": "real mismatch",
               "file": "src/App.tsx", "line_range": "1-2", "category": "correctness"}]
        invalid_review = _emma_completed_template(
            attempt=1, verdict="PASS_WITH_NON_BLOCKING_FINDINGS"
        )
        invalid_review["evidence"]["findings"] = p2
        second = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=1)]),
            "claude": _FakeAdapter([invalid_review]),
        }
        with self.assertRaises(chugel.MissionValidationFailed):
            run_mission(
                mid, second, max_total_attempts=4,
                max_builder_attempts=2, max_reviewer_attempts=2,
            )

        stranded = chugel.get_mission(mid)
        self.assertEqual(stranded["state"], "REVIEWING")
        self.assertEqual(len(stranded["reviewer_evidence"]), 1)
        rejected = stranded["dispatch_ledger"][-1]
        self.assertEqual(rejected["role"], "emma")
        self.assertEqual(rejected["attempt"], 1)
        self.assertEqual(rejected["status"], "FINALIZED")
        self.assertEqual(rejected["result_classification"], "completed")
        self.assertEqual(rejected["evidence_disposition"], "rejected")

        third = {
            "codex": _FakeAdapter([]),
            "claude": _FakeAdapter([_emma_completed_template(attempt=1, verdict="PASS")]),
        }
        result = run_mission(
            mid, third, max_total_attempts=5,
            max_builder_attempts=2, max_reviewer_attempts=3,
        )
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        self.assertEqual(len(final["dispatch_ledger"]), 5)
        retry = final["dispatch_ledger"][-1]
        self.assertNotEqual(retry["invocation_id"], rejected["invocation_id"])
        self.assertEqual(retry["attempt"], 1)


# --- restart / crash semantics: zero automatic redispatch -----------------

class PruebaRestartTrasReservaSinCompletar(AutonomousRunnerRealPersistenceTestCase):
    """Simulates a crash by calling the real chugel dispatch primitives
    directly to leave a ledger entry at a specific lifecycle status, then
    calling run_mission() fresh -- this coordinator holds no in-memory
    dispatch state, so this is indistinguishable from a genuine restart
    of a separate process."""

    def test_reserved_unicamente_no_hace_llamadas_y_reporta_human_action(self):
        mid = self._mission_building()
        chugel.reserve_dispatch(mid, role="emilio", attempt=0)  # crash right here
        adapters = {"codex": _NeverCalledAdapter()}
        # max_total_attempts=2: the durable budget (Emma's P2-2 fix) already
        # counts the crash-simulated reservation above as one consumed
        # attempt -- a budget of 1 would report "total attempt budget
        # exhausted" before ever reaching reserve_dispatch()'s own refusal,
        # which is not what this test means to exercise.
        result = run_mission(mid, adapters, max_total_attempts=2)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertIn("dispatch not eligible", result.reason)

    def test_in_flight_unicamente_no_hace_llamadas(self):
        mid = self._mission_building()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")  # crash right here
        adapters = {"codex": _NeverCalledAdapter()}
        result = run_mission(mid, adapters, max_total_attempts=1)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")

    def test_result_recorded_completed_sin_finalizar_no_hace_llamadas(self):
        """A "completed" classification is not in DISPATCH_RETRYABLE_
        CLASSIFICATIONS -- reserve_dispatch() must refuse a fresh
        redispatch here, identically to an unresolved reservation. This
        is the exact window between record_dispatch_result() and
        record_builder_evidence()'s atomic finalization -- a crash here
        must never be treated as license to guess the outcome and retry."""
        mid = self._mission_building()
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, invocation_id, outcome="completed")  # crash right here
        adapters = {"codex": _NeverCalledAdapter()}
        result = run_mission(mid, adapters, max_total_attempts=1)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")

    def test_result_recorded_reintentable_permite_una_reserva_fresca_y_avanza(self):
        """The one case where a restart IS allowed to make a fresh
        dispatch -- a durably-recorded retryable outcome (failed/timeout/
        unavailable) authorizes exactly one fresh attempt with a fresh
        invocation_id, never reusing the crashed reservation's identity."""
        mid = self._mission_building()
        _, stale_invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, stale_invocation_id, provider="codex")
        chugel.record_dispatch_result(mid, stale_invocation_id, outcome="timeout")  # crash right here

        adapters = {"codex": _FakeAdapter([_emilio_completed_template(attempt=0)])}
        # max_total_attempts=2: one already durably consumed by the
        # crash-simulated reservation above, one for the fresh retry this
        # test expects to succeed.
        result = run_mission(mid, adapters, max_total_attempts=2, max_builder_attempts=2)
        self.assertEqual(len(adapters["codex"].calls), 1)
        self.assertNotEqual(adapters["codex"].calls[0].invocation_id, stale_invocation_id)

        record = chugel.get_mission(mid)
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 2)
        stale_entry = next(e for e in ledger if e["invocation_id"] == stale_invocation_id)
        self.assertEqual(stale_entry["status"], "FINALIZED")
        self.assertEqual(stale_entry["result_classification"], "timeout")
        fresh_entry = next(e for e in ledger if e["invocation_id"] != stale_invocation_id)
        self.assertEqual(fresh_entry["status"], "FINALIZED")
        self.assertEqual(fresh_entry["result_classification"], "completed")

    def test_provider_fallo_normal_deja_registro_de_resultado_reintentable(self):
        """The ordinary (non-crash) retry path -- run_mission() itself
        drives the second attempt in the same call, since builder budget
        allows it -- still goes through the exact same durable
        reservation/result-recording machinery, not a separate code
        path. select_adapter()'s own failover policy routes the retry to
        the fallback provider (claude) after a failed codex attempt --
        both adapters must be present for this ordinary retry to
        succeed."""
        mid = self._mission_building()
        fallback_template = dict(_emilio_completed_template(attempt=0))
        fallback_template["provider"] = "claude"
        adapters = {
            "codex": _FakeAdapter([_failed_template("codex")]),
            "claude": _FakeAdapter([fallback_template]),
        }
        result = run_mission(mid, adapters, max_total_attempts=2, max_builder_attempts=2)
        self.assertEqual(len(adapters["codex"].calls), 1)
        self.assertEqual(len(adapters["claude"].calls), 1)
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "VERIFYING")
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 2)
        self.assertTrue(all(e["status"] == "FINALIZED" for e in ledger))


# --- no automatic action at human gates / terminal states -----------------

class PruebaSinLlamadasEnGatesYEstadosTerminales(AutonomousRunnerRealPersistenceTestCase):
    """Every state reached below is reached via real chugel.transition()
    calls against the canonical TRANSITIONS table (orchestrator/
    validator.py) -- never a synthetic single-field record, so these
    exercise the exact same validate_mission_record()/can_transition()
    gating a real crash-restart would encounter."""

    def test_gates_humanos_hacen_cero_llamadas(self):
        adapters = {"codex": _NeverCalledAdapter(), "claude": _NeverCalledAdapter()}

        # SCOPE_AWAITING_AUTHORIZATION: reachable directly from INTAKE.
        scope_mid = _create_intake_mission("scope-gate")["mission_id"]
        chugel.transition(scope_mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        result = run_mission(scope_mid, adapters)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED", "SCOPE_AWAITING_AUTHORIZATION")

        publish_mid = self._mission_publish_awaiting_authorization()
        result = run_mission(publish_mid, adapters)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED", "PUBLISH_AWAITING_AUTHORIZATION")

        merge_mid = self._mission_merge_awaiting_authorization()
        result = run_mission(merge_mid, adapters)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED", "MERGE_AWAITING_AUTHORIZATION")

    def test_estados_terminales_hacen_cero_llamadas(self):
        adapters = {"codex": _NeverCalledAdapter(), "claude": _NeverCalledAdapter()}

        failed_mid = self._mission_building()
        chugel.transition(failed_mid, "FAILED", actor="chugel", reason="unrecoverable build failure")
        result = run_mission(failed_mid, adapters)
        self.assertEqual(result.status, "TERMINAL_FAILURE", "FAILED")
        self.assertEqual(result.attempts, 0)

        cancelled_mid = _create_intake_mission("cancel-me")["mission_id"]
        chugel.transition(cancelled_mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        chugel.transition(cancelled_mid, "CANCELLED", actor="jose", reason="no longer needed")
        result = run_mission(cancelled_mid, adapters)
        self.assertEqual(result.status, "TERMINAL_FAILURE", "CANCELLED")

        blocked_mid = _create_intake_mission("blocked-me")["mission_id"]
        chugel.transition(blocked_mid, "BLOCKED", actor="jose", reason="waiting on external input")
        result = run_mission(blocked_mid, adapters)
        self.assertEqual(result.status, "TERMINAL_FAILURE", "BLOCKED")

    def test_authorized_transiciona_solo_a_building_nunca_aprueba_un_gate(self):
        """AUTHORIZED -> BUILDING is the one automatic transition this
        coordinator performs on its own -- it must never be mistaken for,
        or extended to, automatically approving any human_gates entry."""
        mid = self._mission_authorized()
        record_before = chugel.get_mission(mid)
        self.assertEqual(record_before["human_gates"]["publish_authorization"]["status"], "not_requested")
        adapters = {"codex": _FakeAdapter([_emilio_completed_template(attempt=0)])}
        run_mission(mid, adapters, max_total_attempts=1)
        record_after = chugel.get_mission(mid)
        self.assertEqual(record_after["human_gates"]["publish_authorization"]["status"], "not_requested")
        self.assertEqual(record_after["human_gates"]["scope_authorization"]["status"], "approved")


# --- attempt/deadline budgets ----------------------------------------------

class PruebaPresupuestosYDeadline(AutonomousRunnerRealPersistenceTestCase):
    def test_max_builder_attempts_acota_los_reintentos(self):
        mid = self._mission_building()
        adapters = {"codex": _FakeAdapter([_failed_template("codex")])}
        result = run_mission(mid, adapters, max_builder_attempts=1, max_total_attempts=4)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(len(adapters["codex"].calls), 1)

    def test_max_total_attempts_acota_a_traves_de_roles(self):
        mid = self._mission_building()
        adapters = {
            "codex": _FakeAdapter([_emilio_completed_template(attempt=0)]),
            "claude": _FakeAdapter([]),
        }
        result = run_mission(mid, adapters, max_total_attempts=1)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(adapters["claude"].calls), 0)

    def test_deadline_agotado_no_hace_llamadas(self):
        mid = self._mission_building()
        adapters = {"codex": _NeverCalledAdapter()}
        result = run_mission(mid, adapters, deadline=0.0)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertIn("deadline", result.reason)

    def test_presupuesto_de_reviewer_se_agota_cuando_claude_esta_caido_y_el_guard_bloquea_codex(self):
        """Emilio construyó con codex (real, un solo dispatch). Claude
        falla realmente en el primer intento de Emma; el segundo intento,
        vía el failover ya configurado, sería enrutado al mismo proveedor
        que el builder -- el guard de independencia lo intercepta antes
        de invocar el adapter de codex. Ambos intentos cuentan para el
        presupuesto durable de reviewer_attempts exactamente igual que
        cualquier otro intento real -- nunca una aprobación silenciosa
        con el mismo proveedor, nunca un loop infinito."""
        mid = self._mission_building()
        # codex_builder's own template list has exactly one entry, for
        # Emilio's real dispatch -- if the guard ever failed to intercept
        # Emma's failover and let a second, real codex call through, this
        # same object would raise IndexError on an empty pop(0), failing
        # the test loudly rather than silently succeeding.
        codex_builder = _FakeAdapter([_emilio_completed_template(attempt=0)])
        claude_down = _FakeAdapter([_failed_template("claude")])

        result = run_mission(
            mid,
            {"codex": codex_builder, "claude": claude_down},
            max_total_attempts=4, max_builder_attempts=2, max_reviewer_attempts=2,
        )

        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        self.assertIn("reviewer attempt budget exhausted", result.reason)
        self.assertEqual(len(codex_builder.calls), 1)
        self.assertEqual(len(claude_down.calls), 1)
        self.assertEqual(chugel.get_mission(mid)["reviewer_evidence"], [])

        ledger = chugel.get_mission(mid)["dispatch_ledger"]
        emma_entries = [e for e in ledger if e["role"] == "emma"]
        self.assertEqual(len(emma_entries), 2)
        self.assertEqual(emma_entries[0]["provider"], "claude")
        self.assertEqual(emma_entries[0]["result_classification"], "failed")
        self.assertEqual(emma_entries[1]["provider"], "codex")
        self.assertEqual(emma_entries[1]["result_classification"], "unavailable")

    def test_limites_invalidos_son_rechazados_antes_de_cualquier_llamada(self):
        # False is falsy-zero (bool is an int subclass) and is correctly
        # caught by the same `<= 0` check as an explicit 0 -- True is a
        # valid (if minimal) budget of 1 and is deliberately not included
        # here, since run_mission() has no separate bool-rejection check
        # for these budget parameters (unlike the schema's own `attempt`
        # field, which does reject bool explicitly).
        mid = self._mission_building()
        for kwargs in (
            {"max_total_attempts": 0}, {"max_builder_attempts": 0}, {"max_reviewer_attempts": 0},
            {"max_total_attempts": False},
        ):
            with self.assertRaises(ValueError, msg=repr(kwargs)):
                run_mission(mid, {"codex": _NeverCalledAdapter()}, **kwargs)


# --- presupuestos durables: sobreviven un restart real ---------------------

class PruebaPresupuestosDurablesSobrevivenRestart(AutonomousRunnerRealPersistenceTestCase):
    """Emma's P2-2 finding (autonomous-runner P2 hardening cycle): the
    prior local `builder`/`reviewer`/`total` counters reset to zero on
    every fresh run_mission() call, so a human/operator who simply
    restarted the runner process got a fresh budget every time --
    repeated restarts could accumulate unbounded real provider dispatches
    even with a small configured budget.

    Each test below makes TWO genuinely separate run_mission() calls --
    each with its own fresh local Python state (no shared variables, no
    carried-over counters, exactly what two separate process invocations
    would look like) -- against real persisted Mission Records, and
    proves the SECOND call's behavior is bounded by what the FIRST call
    already durably consumed, not by its own fresh-looking local budget."""

    def test_segundo_run_mission_no_recibe_presupuesto_fresco(self):
        mid = self._mission_building()

        # First "process invocation": exactly one real dispatch, then this
        # call's own total budget of 1 stops it -- an ordinary single-call
        # budget exhaustion, nothing restart-specific yet.
        first_adapters = {"codex": _FakeAdapter([_failed_template("codex")])}
        first_result = run_mission(mid, first_adapters, max_total_attempts=1, max_builder_attempts=5)
        self.assertEqual(first_result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(len(first_adapters["codex"].calls), 1)
        self.assertEqual(_durable_attempt_counts(chugel.get_mission(mid)), (1, 1, 0))

        # Second "process invocation" -- a brand new call, fresh local
        # variables, max_builder_attempts=1 looks like a full fresh budget
        # of 1 if read naively. It must NOT make a fresh dispatch: the
        # mission already durably consumed 1 of its 1-attempt builder
        # budget in the first call, and that fact lives only in the
        # Mission Record, never in any variable this call could reset.
        second_adapters = {"codex": _NeverCalledAdapter()}
        second_result = run_mission(mid, second_adapters, max_total_attempts=5, max_builder_attempts=1)
        self.assertEqual(second_result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(second_result.reason, "builder attempt budget exhausted")

    def test_tres_restarts_sucesivos_nunca_exceden_el_presupuesto_acumulado(self):
        """Three separate run_mission() calls, each simulating a fresh
        restart, against a mission-wide budget of exactly 1 builder
        attempt. Only the first call may make a real dispatch; the second
        and third must make zero (_NeverCalledAdapter enforces this) --
        if a bug granted each call its own fresh-looking budget, the
        second/third calls would each attempt one more real dispatch."""
        mid = self._mission_building()

        first_adapters = {"codex": _FakeAdapter([_failed_template("codex")])}
        first_result = run_mission(mid, first_adapters, max_total_attempts=10, max_builder_attempts=1)
        self.assertEqual(len(first_adapters["codex"].calls), 1)
        self.assertEqual(first_result.reason, "builder attempt budget exhausted")

        for round_index in range(2):
            adapters = {"codex": _NeverCalledAdapter()}
            result = run_mission(mid, adapters, max_total_attempts=10, max_builder_attempts=1)
            self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED", round_index)
            self.assertEqual(result.reason, "builder attempt budget exhausted", round_index)

        self.assertEqual(_durable_attempt_counts(chugel.get_mission(mid)), (1, 1, 0))

    def test_presupuesto_total_durable_cuenta_ambos_roles_a_traves_de_restarts(self):
        mid = self._mission_authorized()

        first_adapters = {"codex": _FakeAdapter([_emilio_completed_template(attempt=0)])}
        first_result = run_mission(mid, first_adapters, max_total_attempts=1)
        self.assertEqual(first_result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(_durable_attempt_counts(chugel.get_mission(mid)), (1, 1, 0))

        # A restarted process with max_total_attempts=1 must see the
        # mission-wide total already at 1 and refuse Emma's dispatch too
        # -- the durable total spans both roles, exactly like the
        # original local `total` counter did within one call.
        second_adapters = {"claude": _NeverCalledAdapter()}
        second_result = run_mission(mid, second_adapters, max_total_attempts=1)
        self.assertEqual(second_result.status, "HUMAN_ACTION_REQUIRED")
        self.assertEqual(second_result.reason, "total attempt budget exhausted")

    def test_entrada_reserved_sin_resolver_cuenta_de_inmediato_no_es_gratis(self):
        """An unresolved RESERVED entry (the crash-before-launch window)
        must count toward budget the instant it is durably reserved --
        never treated as "free" merely because its execution outcome is
        still unknown. Otherwise a repeated crash-and-restart cycle could
        manufacture unlimited effective budget by never letting any
        attempt resolve."""
        mid = self._mission_building()
        chugel.reserve_dispatch(mid, role="emilio", attempt=0)  # crash right here, never resolved
        self.assertEqual(_durable_attempt_counts(chugel.get_mission(mid)), (1, 1, 0))

        adapters = {"codex": _NeverCalledAdapter()}
        result = run_mission(mid, adapters, max_total_attempts=1, max_builder_attempts=1)
        self.assertEqual(result.status, "HUMAN_ACTION_REQUIRED")
        # Whichever check fires first (durable budget or ledger
        # eligibility -- both correctly refuse here), zero further real
        # dispatches must occur; _NeverCalledAdapter already enforces that.
        self.assertTrue(
            result.reason in ("total attempt budget exhausted", "builder attempt budget exhausted")
            or result.reason.startswith("dispatch not eligible"),
            result.reason,
        )


# --- derivación pura del schema attempt ------------------------------------

class PruebaDerivacionDeSchemaAttempt(unittest.TestCase):
    def test_emilio_schema_attempt_building_es_0_correcting_es_1(self):
        self.assertEqual(_emilio_schema_attempt("BUILDING"), 0)
        self.assertEqual(_emilio_schema_attempt("CORRECTING"), 1)

    def test_emma_schema_attempt_deriva_de_evidencia_persistida_no_de_un_contador(self):
        self.assertEqual(_emma_schema_attempt({"builder_evidence": []}), 0)
        self.assertEqual(_emma_schema_attempt({"builder_evidence": [{"attempt": 0}]}), 0)
        self.assertEqual(_emma_schema_attempt({"builder_evidence": [{"attempt": 0}, {"attempt": 1}]}), 1)
        self.assertEqual(_emma_schema_attempt({"builder_evidence": [{"attempt": 1}]}), 1)


if __name__ == "__main__":
    unittest.main()
