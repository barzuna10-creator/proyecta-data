"""Codex authenticated-runtime assertions owned by the real child harness."""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import tests.test_orchestrator_codex_adapter as legacy
from tests.test_orchestrator_provider_worker_runtime import RealChildHarnessMixin


class CodexWorkerRuntimeTests(RealChildHarnessMixin, unittest.TestCase):
    def _invoke(self, role="emilio", *, mode=None):
        return self.invoke_real_child("codex", role, mode=mode)

    def test_authenticated_sdk_is_reached_only_in_real_child(self):
        result, trace, parent_pid, worker_pid = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(parent_pid, worker_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["run_count"], 1)
        self.assertEqual(trace["login_count"], 1)
        self.assertEqual(result.provider_conversation_id, "fake-codex-conversation")

    def test_deny_all_worktree_and_structured_output_reach_sdk(self):
        _, trace, _, _ = self._invoke()
        start = trace["thread_start"]
        self.assertIn("deny_all", start["approval_mode"])
        self.assertEqual(Path(start["cwd"]), self.worktree)
        schema = trace["output_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        serialized = json.dumps(schema, sort_keys=True)
        for name in ("invocation_id", "provider", "provider_session_id",
                     "provider_conversation_id"):
            self.assertNotIn(name, serialized)

    def test_isolated_home_and_capability_policy_reach_sdk(self):
        _, trace, _, _ = self._invoke()
        config = trace["config_text"]
        feature_block = config.split("[features]\n", 1)[1].split("\n\n", 1)[0]
        feature_values = dict(
            line.split(" = ", 1) for line in feature_block.splitlines() if " = " in line
        )
        self.assertIn("multi_agent", feature_values)
        self.assertEqual(feature_values["multi_agent"], "false")
        for setting in (
            "multi_agent = false", "[agents]\nenabled = false",
            'web_search = "disabled"', "allow_login_shell = false",
        ):
            self.assertIn(setting, config)
        for feature in (
            "apps", "browser_use", "browser_use_external",
            "browser_use_full_cdp_access", "computer_use", "in_app_browser",
            "plugins", "plugin_sharing", "remote_plugin", "enable_mcp_apps",
            "skill_search", "skill_mcp_dependency_install",
        ):
            self.assertIn(feature, config)
        home = Path(trace["codex_home"])
        self.assertFalse(home.exists())
        self.assertEqual(set(trace["child_env_names"]),
                         {"CODEX_HOME", "HOME", "OPENAI_API_KEY", "TEMP", "TMP", "TMPDIR"})

    def test_parent_environment_mutation_cannot_reach_sdk(self):
        with mock.patch.dict(os.environ, {"HOSTILE_PARENT_SECRET": "x"}, clear=False):
            _, trace, _, _ = self._invoke()
        self.assertNotIn("HOSTILE_PARENT_SECRET", trace["worker_env"])

    def test_sdk_boundary_observes_only_canonical_worker_environment(self):
        hostile = {
            "SENTINEL_PARENT_SECRET": "never-child",
            "OPENAI_API_KEY": "ambient-never-read",
            "HOME": "/attacker/home",
            "CODEX_HOME": "/attacker/codex",
            "HTTP_PROXY": "http://attacker.invalid",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            result, trace, parent_pid, worker_pid = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(worker_pid, parent_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["worker_env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        self.assertEqual(trace["child_env_names"], ["CODEX_HOME", "HOME", "OPENAI_API_KEY", "TEMP", "TMP", "TMPDIR"])
        for name in hostile:
            self.assertNotIn(name, trace["worker_env"])

    def test_ambient_openai_key_is_not_fallback_or_child_input(self):
        ambient = "ambient-never-read"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ambient}, clear=False):
            result, trace, _, _ = self._invoke()
        self.assertEqual(result.outcome, "completed")
        self.assertNotIn("OPENAI_API_KEY", trace["worker_env"])
        self.assertNotIn(ambient, json.dumps(trace, sort_keys=True))
        self.assertEqual(trace["login_count"], 1)

    def test_no_importable_authority_or_ambient_auth_json(self):
        import orchestrator.provider_credentials as credentials
        self.assertFalse(hasattr(credentials, "_provider_worker_authority"))
        self.assertFalse(hasattr(credentials, "require_adapter_worker_authority"))
        _, trace, _, _ = self._invoke()
        self.assertNotIn("auth.json", trace["config_text"])

    def test_invalid_explicit_credentials_fail_before_worker_or_sdk(self):
        from orchestrator.provider_worker import ProviderWorkerError, ProviderWorkerInvoker
        values = (None, "", "   ", True, 1, b"key", "bad\nkey")
        for value in values:
            with self.subTest(value=repr(value)), mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": "ambient-never-read"}, clear=False
            ), mock.patch("orchestrator.provider_worker.subprocess.Popen") as popen:
                with self.assertRaises(ProviderWorkerError) as rejected:
                    ProviderWorkerInvoker(provider="codex", api_key=value)
                popen.assert_not_called()
                self.assertNotIn("ambient-never-read", str(rejected.exception))
                if isinstance(value, str) and value:
                    self.assertNotIn(value, str(rejected.exception))

    def test_credential_never_enters_request_output_or_error(self):
        from orchestrator.provider_worker import ProviderWorkerError, _encode_frame
        contaminated = self._request("emilio", mode="normal")
        contaminated.task["secret"] = self.KEY
        with self.assertRaises(ProviderWorkerError) as rejected:
            self._invoke_request(contaminated)
        self.assertNotIn(self.KEY, str(rejected.exception))
        for mode in ("echo_secret", "raise_secret"):
            result = self._invoke("emilio", mode=mode)[0]
            self.assertIn(result.outcome, ("invalid_output", "failed"))
            self.assertIsNone(result.evidence)
            self.assertNotIn(self.KEY, result.error_detail or "")

    def _invoke_request(self, request):
        from orchestrator.provider_worker import ProviderWorkerInvoker
        import orchestrator.provider_worker as worker
        old = worker._DISPATCHER_PATH
        try:
            worker._DISPATCHER_PATH = self.root / "orchestrator" / "provider_worker_runtime.py"
            return ProviderWorkerInvoker(provider="codex", api_key=self.KEY).invoke(request)
        finally:
            worker._DISPATCHER_PATH = old

    def test_noncompleted_turn_and_provider_error_are_mapped(self):
        failed, _, _, _ = self._invoke(mode="failed")
        unavailable, _, _, _ = self._invoke(mode="exception")
        self.assertEqual(failed.outcome, "failed")
        self.assertIsNone(failed.evidence)
        self.assertEqual(unavailable.outcome, "failed")
        self.assertIsNone(unavailable.evidence)

    def test_invalid_provider_json_is_invalid_output(self):
        result, _, _, _ = self._invoke(mode="invalid_json")
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIsNone(result.evidence)


class CodexParentTombstoneTests(legacy.CodexAdapterTestCase):
    def test_parent_object_new_has_no_authenticated_execution_method(self):
        obj = object.__new__(self.coa.CodexAdapter)
        obj._api_key = "synthetic-parent-key"
        obj._timeout_seconds = 1
        self.assertFalse(hasattr(obj, "invoke"))


class CodexAuthenticationAssertionTests(RealChildHarnessMixin, unittest.TestCase):
    """Pure account-discriminator checks migrated from the old adapter tests.

    These checks do not construct an SDK; authenticated execution remains
    covered by the real-child tests above.
    """

    def setUp(self):
        RealChildHarnessMixin.setUp(self)
        legacy._install_fake_openai_codex()
        import orchestrator.adapters.codex_adapter as adapter
        self.adapter = adapter

    def tearDown(self):
        RealChildHarnessMixin.tearDown(self)

    def _codex(self, account):
        class Response:
            pass
        class Fake:
            def account(self):
                response = Response(); response.account = account; return response
        return Fake()

    def _account(self, value):
        class Root:
            type = value
        class Account:
            root = Root()
        return Account()

    def test_api_key_root_is_accepted(self):
        self.adapter._verify_api_key_identity_active(self._codex(self._account("apiKey")))

    def test_non_api_key_roots_fail_closed(self):
        for value in ("amazonBedrock", "chatgpt", None):
            with self.subTest(value=value), self.assertRaises(self.adapter.CodexAdapterError):
                self.adapter._verify_api_key_identity_active(self._codex(self._account(value)))

    def test_missing_account_and_account_exception_fail_closed(self):
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(None))
        class Broken:
            def account(self):
                raise RuntimeError("synthetic account failure")
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(Broken())

    def test_malformed_root_fails_closed(self):
        class Account:
            root = object()
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(Account()))

    def test_account_apikey_permite_la_invocacion(self):
        self.adapter._verify_api_key_identity_active(self._codex(self._account("apiKey")))

    def test_account_bedrock_bloquea(self):
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(self._account("amazonBedrock")))

    def test_account_chatgpt_bloquea_pese_a_login_api_key_exitoso(self):
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(self._account("chatgpt")))

    def test_account_none_bloquea(self):
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(None))

    def test_account_que_lanza_excepcion_bloquea(self):
        class Broken:
            def account(self):
                raise RuntimeError("synthetic account failure")
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(Broken())

    def test_account_root_malformado_falla_cerrado(self):
        class Account:
            root = object()
        with self.assertRaises(self.adapter.CodexAdapterError):
            self.adapter._verify_api_key_identity_active(self._codex(Account()))

    def test_adapter_no_define_ni_lee_auth_json_ambiental(self):
        self.assertFalse(hasattr(self.adapter, "AUTH_JSON"))
        import inspect
        source = inspect.getsource(self.adapter)
        self.assertNotIn("Path.home()", source)

    def test_nunca_llama_login_chatgpt(self):
        self.assertFalse(hasattr(self.adapter, "login_chatgpt"))

    def test_verificacion_no_depende_de_account_type_directo(self):
        root_type = type("Root", (), {"type": "apiKey"})
        class Wrapped:
            type = "chatgpt"
            root = root_type()
        self.adapter._verify_api_key_identity_active(self._codex(Wrapped()))

    def test_root_model_account_verification_survives_real_worker(self):
        result, trace, parent_pid, worker_pid = self.invoke_real_child("codex", "emilio")
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(parent_pid, worker_pid)
        account = self._account("apiKey")
        self.assertFalse(hasattr(account, "type"))
        self.assertEqual(account.root.type, "apiKey")
        self.assertEqual(trace["login_count"], 1)

    def test_completed_evidence_and_thread_identity_survive_worker(self):
        result, trace, parent_pid, worker_pid = self.invoke_real_child(
            "codex", "emilio", mode="completed_identity"
        )
        expected = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "codex")
        self.assertEqual(result.evidence, expected)
        self.assertEqual(result.provider_conversation_id, "fake-codex-conversation")
        self.assertIsNone(result.provider_session_id)
        self.assertEqual(result.invocation_id, "inv-real-child-emilio")
        self.assertNotEqual(parent_pid, worker_pid)
        self.assertEqual(trace["run_count"], 1)

    def test_config_exacta_falla_cerrado_sin_capacidades_ambientales(self):
        _, trace, _, _ = RealChildHarnessMixin.invoke_real_child(self, "codex", "emilio")
        self.assertEqual(set(trace["worker_env"]), {"PATH", "LANG", "LC_ALL"})

    def test_completed_con_evidencia_y_thread_id(self):
        result, trace, _, _ = RealChildHarnessMixin.invoke_real_child(self, "codex", "emilio")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.evidence, {"attempt": 0})
        self.assertEqual(result.provider_conversation_id, "fake-codex-conversation")
        self.assertEqual(trace["run_count"], 1)

    def test_codex_se_construye_con_multi_agent_deshabilitado(self):
        _, trace, _, _ = RealChildHarnessMixin.invoke_real_child(self, "codex", "emilio")
        self.assertIn("multi_agent = false", trace["config_text"])


if __name__ == "__main__":
    unittest.main()
