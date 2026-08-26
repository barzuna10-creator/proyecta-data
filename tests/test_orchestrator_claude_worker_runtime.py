"""Claude authenticated-runtime assertions owned by the real child harness."""

import os
import unittest
from pathlib import Path
from unittest import mock

import tests.test_orchestrator_claude_adapter as legacy
from tests.test_orchestrator_provider_worker_runtime import RealChildHarnessMixin


class ClaudeWorkerRuntimeTests(RealChildHarnessMixin, unittest.TestCase):
    def _invoke(self):
        return self.invoke_real_child("claude", "emma")

    def test_authenticated_sdk_is_reached_only_in_real_child(self):
        result, trace, parent_pid, worker_pid = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(parent_pid, worker_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["query_count"], 1)
        self.assertEqual(result.provider_session_id, "fake-claude-session")

    def test_completed_evidence_full_shape_survives_worker(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_completed_con_evidencia_y_session_id` -- same full-shape
        assertion, now proven via the real cross-process worker instead
        of an in-process mock."""
        result, _, _, _ = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "claude")
        self.assertEqual(result.evidence, {"attempt": 0, "verdict": "PASS"})
        self.assertEqual(result.provider_session_id, "fake-claude-session")
        self.assertIsNone(result.provider_conversation_id)
        self.assertEqual(result.invocation_id, "inv-real-child-emma")
        self.assertTrue(result.fresh_context_attested)

    def test_structured_output_none_is_invalid_output(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_structured_output_ausente_es_invalid_output` -- a
        ResultMessage genuinely arrives (unlike `mode="empty"`, which
        never yields one at all) but with `structured_output=None`."""
        result = self.invoke_real_child("claude", "emma", mode="structured_output_none")[0]
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIsNone(result.evidence)

    def test_is_error_true_is_failed_even_with_structured_output_populated(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_is_error_true_es_failed_incluso_con_structured_output_poblado`
        -- the shared fake's `mode="is_error"` branch now populates
        `structured_output` with a real dict (see
        tests/test_orchestrator_provider_worker_runtime.py), proving
        `is_error=True` wins unconditionally rather than merely because
        structured_output happened to be empty."""
        result = self.invoke_real_child("claude", "emma", mode="is_error")[0]
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(result.evidence)
        self.assertIn("500", result.error_detail)

    def test_cada_invoke_real_construye_un_worker_nuevo(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_cada_invoke_construye_un_cliente_nuevo` -- two independent
        real-child invocations, each its own OS process (a stronger
        guarantee than "two distinct in-process mock objects")."""
        _, _, _, worker_pid_1 = self._invoke()
        _, _, _, worker_pid_2 = self._invoke()
        self.assertNotEqual(worker_pid_1, worker_pid_2)

    def test_credential_never_enters_request_output_or_error(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_credencial_no_puede_entrar_en_request_output_o_error` --
        ported from the exact analog already proven for Codex
        (`CodexWorkerRuntimeTests.test_credential_never_enters_request_output_or_error`)."""
        from orchestrator.provider_worker import ProviderWorkerError
        contaminated = self._request("emma", mode="normal")
        contaminated.task["secret"] = self.KEY
        with self.assertRaises(ProviderWorkerError) as rejected:
            self._invoke_request(contaminated)
        self.assertNotIn(self.KEY, str(rejected.exception))
        for mode in ("echo_secret", "raise_secret"):
            result = self.invoke_real_child("claude", "emma", mode=mode)[0]
            self.assertIn(result.outcome, ("invalid_output", "failed"))
            self.assertIsNone(result.evidence)
            self.assertNotIn(self.KEY, result.error_detail or "")

    def _invoke_request(self, request):
        from orchestrator.provider_worker import ProviderWorkerInvoker
        import orchestrator.provider_worker as worker
        old = worker._DISPATCHER_PATH
        try:
            worker._DISPATCHER_PATH = self.root / "orchestrator" / "provider_worker_runtime.py"
            return ProviderWorkerInvoker(provider="claude", api_key=self.KEY).invoke(request)
        finally:
            worker._DISPATCHER_PATH = old

    def test_sdk_boundary_observes_only_canonical_worker_environment(self):
        """Corrective migration (stale-test disposition) of the legacy
        `test_sdk_construction_boundary_observa_solo_worker_canonico` --
        ported from the exact analog already proven for Codex
        (`CodexWorkerRuntimeTests.test_sdk_boundary_observes_only_canonical_worker_environment`),
        asserting the full exact canonical env set rather than only the
        absence of one hostile variable."""
        hostile = {
            "SENTINEL_PARENT_SECRET": "never-child",
            "ANTHROPIC_API_KEY": "ambient-never-read",
            "HOME": "/attacker/home",
            "CLAUDE_CONFIG_DIR": "/attacker/claude",
            "HTTP_PROXY": "http://attacker.invalid",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            result, trace, parent_pid, worker_pid = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(worker_pid, parent_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["worker_env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        for name in hostile:
            self.assertNotIn(name, trace["worker_env"])

    def test_emma_tools_and_auxiliary_surfaces_reach_sdk(self):
        _, trace, _, _ = self._invoke()
        self.assertEqual(trace["tools"], ["Read", "Glob", "Grep"])
        self.assertEqual(trace["allowed_tools"], ["Read", "Glob", "Grep"])
        self.assertTrue({"Bash", "Edit", "Write"}.isdisjoint(trace["tools"]))
        self.assertEqual(trace["permission_mode"], "dontAsk")
        self.assertTrue(trace["strict_mcp_config"])
        self.assertEqual(trace["mcp_servers"], {})
        self.assertEqual(trace["skills"], [])
        self.assertEqual(trace["plugins"], [])
        self.assertEqual(trace["add_dirs"], [])
        self.assertEqual(trace["setting_sources"], [])

    def test_sandbox_filesystem_network_and_isolated_config_reach_sdk(self):
        _, trace, _, _ = self._invoke()
        sandbox = trace["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["excludedCommands"], [])
        self.assertEqual(sandbox["filesystem"]["allowRead"], [str(self.worktree)])
        self.assertEqual(sandbox["filesystem"]["allowWrite"], [])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])
        self.assertFalse(sandbox["network"]["allowLocalBinding"])
        self.assertEqual(Path(trace["cwd"]), self.worktree)
        self.assertTrue(trace["credential_present"])
        config_dir = Path(trace["claude_config_dir"])
        self.assertFalse(config_dir.exists())

    def test_parent_environment_mutation_cannot_reach_sdk(self):
        with mock.patch.dict(os.environ, {"HOSTILE_PARENT_SECRET": "x"}, clear=False):
            _, trace, _, _ = self._invoke()
        self.assertNotIn("HOSTILE_PARENT_SECRET", trace["worker_env"])

    def test_ambient_claude_credentials_are_rejected_without_exposure(self):
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            with self.subTest(name=name), mock.patch.dict(
                os.environ, {name: "ambient-never-read"}, clear=False
            ):
                result, trace, _, _ = self._invoke()
                self.assertEqual(result.outcome, "completed")
                self.assertNotIn(name, trace["worker_env"])
                self.assertNotIn("ambient-never-read", str(trace))

    def test_config_dir_is_cleaned_after_provider_error(self):
        result, trace, _, _ = self.invoke_real_child("claude", "emma", mode="exception")
        self.assertEqual(result.outcome, "unavailable")
        self.assertFalse(Path(trace["claude_config_dir"]).exists())

    def test_invalid_explicit_claude_credentials_fail_before_worker(self):
        from orchestrator.provider_worker import ProviderWorkerError, ProviderWorkerInvoker
        for value in (None, "", "   ", True, 1, "bad\nkey"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ProviderWorkerError):
                    ProviderWorkerInvoker(provider="claude", api_key=value)

    def test_result_error_and_transport_error_precedence(self):
        failed = self.invoke_real_child("claude", "emma", mode="is_error")[0]
        unavailable = self.invoke_real_child("claude", "emma", mode="exception")[0]
        self.assertEqual(failed.outcome, "failed")
        self.assertIsNone(failed.evidence)
        self.assertEqual(unavailable.outcome, "unavailable")
        self.assertIsNone(unavailable.evidence)

    def test_missing_result_message_is_failed_closed(self):
        result = self.invoke_real_child("claude", "emma", mode="empty")[0]
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(result.evidence)

    def test_emilio_role_is_rejected_before_client_construction(self):
        """Corrective coverage restoration (Emma P1 finding on the stale-
        test migration): `claude_worker_runtime.py`'s own production
        guard --

            if request.agent_role != "emma":
                raise ClaudeAdapterError(
                    "direct-key Claude authentication is authorized only "
                    "for Emma; Emilio-through-Claude remains fail-closed"
                )

        -- is the very first check in the child's `__main__` block,
        strictly before `_isolated_claude_config_dir()`,
        `_build_worker_options()`, or `ClaudeSDKClient` are ever reached.
        Proven here via the real worker/runtime harness (a genuine
        subprocess crossing the OS process boundary), not a parent-side
        authenticated-adapter seam: the guard's `ClaudeAdapterError` is
        raised inside the real child process; `provider_worker_runtime.py`'s
        own `except BaseException: return 70` turns that into a nonzero
        child exit code, which `ProviderWorkerInvoker.invoke()` turns into
        `ProviderWorkerError` in the parent -- never a silent success, and
        never routed through any pre-worker parent-side check."""
        from orchestrator.provider_worker import ProviderWorkerError
        with self.assertRaises(ProviderWorkerError):
            self.invoke_real_child("claude", "emilio")
        # The fake ClaudeSDKClient.__init__ is the only thing that ever
        # writes sdk-trace.json -- its absence is direct, real-process
        # proof the client was never constructed, let alone used.
        self.assertFalse((self.root / "sdk-trace.json").exists())


class ClaudeParentTombstoneTests(legacy.ClaudeAdapterTestCase):
    def test_parent_object_new_has_no_authenticated_execution_method(self):
        obj = object.__new__(self.ca.ClaudeAdapter)
        obj._api_key = "synthetic-parent-key"
        obj._model = "synthetic-model"
        obj._timeout_seconds = 1
        self.assertFalse(hasattr(obj, "invoke"))


if __name__ == "__main__":
    unittest.main()
