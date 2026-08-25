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


class ClaudeParentTombstoneTests(legacy.ClaudeAdapterTestCase):
    def test_parent_object_new_has_no_authenticated_execution_method(self):
        obj = object.__new__(self.ca.ClaudeAdapter)
        obj._api_key = "synthetic-parent-key"
        obj._model = "synthetic-model"
        obj._timeout_seconds = 1
        self.assertFalse(hasattr(obj, "invoke"))


if __name__ == "__main__":
    unittest.main()
