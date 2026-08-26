"""Corrective #7 regression tests -- closes the "API_USAGE_STILL_REACHABLE"
finding from the subscription-only-guarantee investigation.

Proves that `orchestrator.wiring._select_and_dispatch()` (the single
chokepoint both `run_emilio_attempt()` and `run_emma_attempt()` funnel
every dispatch through) refuses to invoke any adapter that is not a
genuine `CodexCliAdapter`/`ClaudeCliAdapter` instance -- an API-key-backed
`ProviderWorkerInvoker`, a legacy `CodexAdapter`/`ClaudeAdapter` tombstone,
or an arbitrary hand-rolled object satisfying the `AgentInvoker` Protocol
structurally -- all fail closed with `UnapprovedAdapterType`, before
`mark_invocation_dispatched()` or `adapter.invoke()` ever run.

No test here invokes a real Codex/Claude CLI, network, or authenticated
subprocess. Where a real `CodexCliAdapter`/`ClaudeCliAdapter` instance is
needed to prove the *positive* case, its constructor is given
`sys.executable` as `cli_path` (any existing file satisfies the
constructor's file-existence check; the path is never executed), and
`invoke()` is patched at the *class* level (both adapters use
`__slots__`, so instance-level attribute patching is not possible) --
the real subscription-CLI subprocess/login path is never reached.

Fixture helpers below mirror tests/test_orchestrator_wiring.py exactly
(same mission-lifecycle sequence via chugel.py, no shortcuts)."""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import orchestrator.agent_invocation as ai
import orchestrator.chugel as chugel
import orchestrator.wiring as wiring
from orchestrator.adapters.claude_cli_adapter import ClaudeCliAdapter
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter
from orchestrator.cli_provider_adapters import build_cli_subscription_adapters
from orchestrator.provider_worker import ProviderWorkerInvoker

import tests.test_orchestrator_codex_adapter as legacy_codex
import tests.test_orchestrator_claude_adapter as legacy_claude


# --- fixtures: misión real vía chugel.py, idénticas a test_orchestrator_wiring.py --

def _mission_definition_payload(authorized_by="jose"):
    return {
        "outcome": "ship the thing", "scope": ["do the thing"], "non_goals": [],
        "acceptance_criteria": ["it works"], "authorized_by": authorized_by,
        "authorized_at": "2026-08-19T12:00:00Z", "authorization_decision_ref": "ref-intake-1",
    }


def _scope_gate_approval():
    return {
        "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
        "decision_ref": "ref-scope-1", "approved_for": {"mission_definition_version": 1},
    }


def _artifact_commit(sha=None):
    return {
        "mode": "commit", "commit_sha": sha or ("a" * 40),
        "patch_path": None, "patch_sha256": None, "patch_byte_size": None,
    }


def _builder_evidence(attempt=0, artifact=None, persisted=False, provider="codex"):
    evidence = {
        "attempt": attempt, "invoked_at": "2026-08-19T12:00:00Z",
        "artifact": artifact or _artifact_commit(),
        "changed_files": [{"path": "SENTINEL_CHANGED_FILE.py", "reason": "SENTINEL_REASON"}],
        "checks": [{"command": "SENTINEL_CHECK_COMMAND", "working_directory": "/tmp",
                     "exit_status": 0, "result": "SENTINEL_CHECK_RESULT"}],
        "skipped_checks": [], "risks": ["SENTINEL_RISK"],
        "assumptions": [{"text": "SENTINEL_ASSUMPTION", "label": "ASSUMPTION"}],
        "rollback_notes": "SENTINEL_ROLLBACK_NOTES",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True,
            "no_remote_action": True, "no_production_access": True,
            "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": "SENTINEL_HANDOFF_REF",
        "conclusion": {"text": "SENTINEL_CONCLUSION", "label": "FACT"},
    }
    if persisted:
        evidence.update({
            "invocation_id": "11111111-1111-4111-8111-111111111111",
            "provider": provider, "provider_session_id": None,
            "provider_conversation_id": "builder-thread",
        })
    return evidence


def _reviewer_evidence(attempt=0, artifact=None, verdict="PASS"):
    art = artifact or _artifact_commit()
    return {
        "attempt": attempt, "invoked_at": "2026-08-19T12:05:00Z",
        "artifact_identity_confirmed_at_start": art,
        "artifact_identity_confirmed_before_conclusion": art,
        "rechecked_commands": [], "findings": [], "verdict": verdict,
        "blocked_reason": "boom" if verdict == "BLOCKED" else None,
    }


def _result_fields(outcome="completed", evidence=None, provider="codex", model="codex-1",
                    fresh_context_attested=True, provider_session_id=None,
                    provider_conversation_id=None, error_detail=None):
    return dict(
        outcome=outcome, evidence=evidence, provider=provider, model=model,
        fresh_context_attested=fresh_context_attested,
        provider_session_id=provider_session_id,
        provider_conversation_id=provider_conversation_id, error_detail=error_detail,
    )


class SubscriptionOnlyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_ready_for_emilio(self):
        m = chugel.create_mission("subscription-only enforcement test", _mission_definition_payload())
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": "/tmp/synthetic-worktree", "branch": "overnight/synthetic",
            "base_sha": "b" * 40, "isolation_confirmed": True,
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


class _ArbitraryFakeAdapter:
    """Satisfies the AgentInvoker Protocol structurally (a callable
    `invoke(request)`), but is not a subclass of CodexCliAdapter or
    ClaudeCliAdapter at all -- exactly the shape of a hand-rolled fake or
    an unaudited caller-written custom adapter."""

    def __init__(self):
        self.invoke = mock.Mock(
            side_effect=AssertionError("must never be called -- rejected before invocation")
        )


class PruebaRechazoDeAdaptadoresNoAprobados(SubscriptionOnlyTestCase):
    def test_provider_worker_invoker_es_rechazado_antes_de_invocar(self):
        """The real API-key-backed worker proxy -- exactly what
        `build_provider_adapters()` would hand back -- must never reach
        `invoke()` through the autonomous dispatch path. The placeholder
        key below satisfies `validate_dedicated_key()`'s syntactic rules
        only (non-empty, stripped, no control chars); it is never a real
        credential and no network call is ever reachable from this test."""
        mid = self._mission_ready_for_emilio()
        pwi = ProviderWorkerInvoker(provider="codex", api_key="synthetic-fake-key-never-used")
        with mock.patch.object(
            ProviderWorkerInvoker, "invoke",
            side_effect=AssertionError("must never be called -- rejected before invocation"),
        ):
            with self.assertRaises(wiring.UnapprovedAdapterType):
                wiring.run_emilio_attempt(mid, 0, adapters={"codex": pwi, "claude": pwi})

    def test_legacy_codex_adapter_tombstone_es_rechazado(self):
        """A `CodexAdapter` instance constructed via the same
        `object.__new__` bypass this codebase's own tombstone tests use."""
        legacy_codex._install_fake_openai_codex()
        import orchestrator.adapters.codex_adapter as coa
        obj = object.__new__(coa.CodexAdapter)
        object.__setattr__(obj, "_api_key", "synthetic-parent-key")
        object.__setattr__(obj, "_timeout_seconds", 1)
        mid = self._mission_ready_for_emilio()
        with self.assertRaises(wiring.UnapprovedAdapterType):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": obj, "claude": obj})

    def test_legacy_claude_adapter_tombstone_es_rechazado(self):
        legacy_claude._install_fake_claude_agent_sdk()
        import orchestrator.adapters.claude_adapter as ca
        obj = object.__new__(ca.ClaudeAdapter)
        object.__setattr__(obj, "_api_key", "synthetic-parent-key")
        object.__setattr__(obj, "_model", "synthetic-model")
        object.__setattr__(obj, "_timeout_seconds", 1)
        mid = self._mission_ready_for_emma()
        with self.assertRaises(wiring.UnapprovedAdapterType):
            wiring.run_emma_attempt(mid, 0, adapters={"codex": obj, "claude": obj})

    def test_adaptador_arbitrario_con_invoke_es_rechazado(self):
        mid = self._mission_ready_for_emilio()
        fake = _ArbitraryFakeAdapter()
        with self.assertRaises(wiring.UnapprovedAdapterType):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": fake, "claude": fake})
        fake.invoke.assert_not_called()

    def test_rechazo_ocurre_antes_de_marcar_in_flight(self):
        """UnapprovedAdapterType fires strictly before
        mark_invocation_dispatched(): the reservation stays exactly at
        RESERVED, never IN_FLIGHT -- no evidence of a rejected attempt
        ever reaches the durable dispatch ledger as a real dispatch."""
        mid = self._mission_ready_for_emilio()
        fake = _ArbitraryFakeAdapter()
        with self.assertRaises(wiring.UnapprovedAdapterType):
            wiring.run_emilio_attempt(mid, 0, adapters={"codex": fake, "claude": fake})
        ledger = chugel.get_mission(mid)["dispatch_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["status"], "RESERVED")


class PruebaAceptacionDeAdaptadoresCliReales(SubscriptionOnlyTestCase):
    """Positive proof: real CodexCliAdapter/ClaudeCliAdapter instances are
    still accepted, and dispatch proceeds normally. `invoke()` is patched
    at the class level (both adapters declare `__slots__`, so per-instance
    patching is impossible) -- the real CLI/subprocess/login path is
    never reached."""

    def test_codex_cli_adapter_real_es_aceptado(self):
        mid = self._mission_ready_for_emilio()
        adapter = CodexCliAdapter(cli_path=sys.executable)
        evidence = _builder_evidence(0)

        def fake_invoke(self, request):
            return ai.AgentInvocationResult(
                invocation_id=request.invocation_id, responded_at="2026-08-19T12:30:00Z",
                **_result_fields(evidence=evidence),
            )

        with mock.patch.object(CodexCliAdapter, "invoke", fake_invoke):
            outcome = wiring.run_emilio_attempt(mid, 0, adapters={"codex": adapter, "claude": adapter})
        self.assertEqual(outcome.result.outcome, "completed")
        self.assertEqual(outcome.routing_decision.adapter_name, "codex")

    def test_claude_cli_adapter_real_es_aceptado(self):
        mid = self._mission_ready_for_emma()
        adapter = ClaudeCliAdapter(cli_path=sys.executable)
        evidence = _reviewer_evidence(0)

        def fake_invoke(self, request):
            return ai.AgentInvocationResult(
                invocation_id=request.invocation_id, responded_at="2026-08-19T12:30:00Z",
                **_result_fields(evidence=evidence, provider="claude", model="claude-1"),
            )

        with mock.patch.object(ClaudeCliAdapter, "invoke", fake_invoke):
            outcome = wiring.run_emma_attempt(mid, 0, adapters={"codex": adapter, "claude": adapter})
        self.assertEqual(outcome.result.outcome, "completed")
        self.assertEqual(outcome.routing_decision.adapter_name, "claude")

    def test_build_cli_subscription_adapters_mapping_es_aceptado(self):
        """The exact function real pilots use to construct `adapters` --
        proves its output is never itself rejected by the new boundary."""
        adapters = build_cli_subscription_adapters(
            codex_cli_path=sys.executable, claude_cli_path=sys.executable,
        )
        self.assertIsInstance(adapters["codex"], CodexCliAdapter)
        self.assertIsInstance(adapters["claude"], ClaudeCliAdapter)
        mid = self._mission_ready_for_emilio()
        evidence = _builder_evidence(0)

        def fake_invoke(self, request):
            return ai.AgentInvocationResult(
                invocation_id=request.invocation_id, responded_at="2026-08-19T12:30:00Z",
                **_result_fields(evidence=evidence),
            )

        with mock.patch.object(CodexCliAdapter, "invoke", fake_invoke):
            outcome = wiring.run_emilio_attempt(mid, 0, adapters=adapters)
        self.assertEqual(outcome.result.outcome, "completed")


class PruebaFailoverPreservadoEntreAdaptadoresCliReales(SubscriptionOnlyTestCase):
    """Preserves DEFAULT_PROVIDER_CONFIG's codex<->claude provider-name
    failover: a retryable failure on the primary still routes to the
    fallback provider name, and both resolved adapters -- being real
    CodexCliAdapter/ClaudeCliAdapter instances -- pass the new boundary
    identically, exactly as before this corrective cycle."""

    def test_codex_no_disponible_recurre_a_claude_ambos_aprobados(self):
        mid = self._mission_ready_for_emilio()
        codex_adapter = CodexCliAdapter(cli_path=sys.executable)
        claude_adapter = ClaudeCliAdapter(cli_path=sys.executable)
        evidence = _builder_evidence(0)

        def failing_codex(self, request):
            return ai.AgentInvocationResult(
                invocation_id=request.invocation_id, responded_at="2026-08-19T12:00:00Z",
                **_result_fields(outcome="unavailable", error_detail="synthetic unavailable"),
            )

        def succeeding_claude(self, request):
            return ai.AgentInvocationResult(
                invocation_id=request.invocation_id, responded_at="2026-08-19T12:01:00Z",
                **_result_fields(provider="claude", model="claude-1", evidence=evidence),
            )

        adapters = {"codex": codex_adapter, "claude": claude_adapter}
        with mock.patch.object(CodexCliAdapter, "invoke", failing_codex), \
             mock.patch.object(ClaudeCliAdapter, "invoke", succeeding_claude):
            first = wiring.run_emilio_attempt(mid, 0, adapters=adapters)
            self.assertEqual(first.result.outcome, "unavailable")
            self.assertEqual(first.routing_decision.adapter_name, "codex")
            second = wiring.run_emilio_attempt(
                mid, 0, adapters=adapters, prior_attempts=(first.attempt_record,),
            )
        self.assertEqual(second.routing_decision.adapter_name, "claude")
        self.assertEqual(second.result.outcome, "completed")


if __name__ == "__main__":
    unittest.main()
