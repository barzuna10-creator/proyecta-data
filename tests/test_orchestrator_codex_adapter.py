"""Pruebas para orchestrator/adapters/codex_adapter.py (Incremento #14,
ciclo correctivo -- cierra los hallazgos P1/P2 de Emma).

Ninguna prueba hace una llamada real a Codex, a la red, o a un subprocess.
El paquete real `openai_codex` no está instalado en este entorno -- cada
prueba instala un módulo simulado en sys.modules antes de importar el
adapter.

**Fidelidad de los simulados, verificada contra el paquete real
`openai-codex==0.147.0` instalado en un venv desechable durante este
ciclo correctivo (nunca contra las suposiciones del adapter bajo
prueba):**

- `Codex.thread_start` está definido directamente en el cuerpo de la
  clase simulada -- nunca parcheado con `mock.patch.object(...,
  create=True)`, que es exactamente el patrón que enmascaró los dos
  bugs P1 del ciclo anterior (permitía inventar un método
  `start_thread` que nunca existió en el SDK real).
- `Thread.run` es keyword-only después de `input`, replicando la firma
  real exacta (`run(self, input, *, ..., output_schema=None, ...)`) --
  una llamada posicional como `thread.run(prompt, {"output_schema": s})`
  lanza `TypeError` contra este simulado exactamente como lo haría
  contra el SDK real, así que una regresión de producción a la
  convención de llamada antigua vuelve a fallar aquí también.
- `TurnResult`/`TurnStatus`/`TurnError` reproducen la forma real
  confirmada por introspección (`status` es un enum con valores
  completed/interrupted/failed/in_progress; `error` es `None` o un
  objeto con `.message`).
- `Codex.account()` reproduce la forma real confirmada
  (`GetAccountResponse.account.type` en {"apiKey","chatgpt","amazonBedrock"}).
- `openai_codex.errors` reproduce la taxonomía de excepciones real
  (`CodexError`, `TransportClosedError`, `ServerBusyError`,
  `RetryLimitExceededError`, `ParseError`, `InvalidRequestError`,
  `MethodNotFoundError`, `InvalidParamsError`, `InternalRpcError`),
  confirmada leyendo el código fuente real de `openai_codex/errors.py`."""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import json
import os
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from enum import Enum
from unittest import mock


def _install_fake_openai_codex():
    fake = types.ModuleType("openai_codex")

    class Sandbox:
        read_only = "read_only"
        workspace_write = "workspace_write"
        full_access = "full_access"

    class ApprovalMode(Enum):
        deny_all = "deny_all"
        auto_review = "auto_review"

    class TurnStatus(Enum):
        completed = "completed"
        interrupted = "interrupted"
        failed = "failed"
        in_progress = "inProgress"

    class TurnError:
        def __init__(self, message, additional_details=None, codex_error_info=None):
            self.message = message
            self.additional_details = additional_details
            self.codex_error_info = codex_error_info

    class TurnResult:
        def __init__(self, *, status, final_response=None, error=None, id="turn-1"):
            self.status = status
            self.final_response = final_response
            self.error = error
            self.id = id

    class Thread:
        def __init__(self, *, id=None, turn_result=None, raise_exc=None):
            self.id = id
            self._turn_result = turn_result
            self._raise_exc = raise_exc
            self.run_called_count = 0
            self.last_input = None
            self.last_kwargs = None

        # Keyword-only after `input`, exactly matching the real
        # Thread.run(self, input, *, approval_mode=None, cwd=None,
        # effort=None, model=None, output_schema=None, personality=None,
        # sandbox=None, service_tier=None, summary=None) -> TurnResult
        def run(
            self,
            input,
            *,
            approval_mode=None,
            cwd=None,
            effort=None,
            model=None,
            output_schema=None,
            personality=None,
            sandbox=None,
            service_tier=None,
            summary=None,
        ):
            self.run_called_count += 1
            self.last_input = input
            self.last_kwargs = {"cwd": cwd, "output_schema": output_schema, "sandbox": sandbox}
            if self._raise_exc is not None:
                raise self._raise_exc
            return self._turn_result

    class ApiKeyAccount:
        type = "apiKey"

    class ChatgptAccount:
        type = "chatgpt"

        def __init__(self, email=None, plan_type="plus"):
            self.email = email
            self.plan_type = plan_type

    class AmazonBedrockAccount:
        type = "amazonBedrock"

        def __init__(self, uses_codex_managed_credentials=None):
            self.uses_codex_managed_credentials = uses_codex_managed_credentials

    class Account:
        """Reproduces the real openai_codex.generated.v2_all.Account shape
        exactly: a Pydantic RootModel[ApiKeyAccount | ChatgptAccount |
        AmazonBedrockAccount] wrapper -- the discriminated `.type` field
        lives at `.root.type`, never directly on the wrapper itself.
        Confirmed against the real, installed openai-codex==0.147.0 in a
        disposable venv this corrective cycle: `getattr(account, "type",
        None)` returns None unconditionally on the real class; only
        `account.root.type` returns the real discriminator. This fake
        deliberately has no `.type` attribute at all, so a production
        regression back to reading `account.type` directly fails this
        fake exactly as it silently (but always-refusing) failed against
        the real SDK."""

        def __init__(self, root):
            self.root = root

    class GetAccountResponse:
        def __init__(self, account=None, requires_openai_auth=False):
            self.account = account
            self.requires_openai_auth = requires_openai_auth

    class CodexConfig:
        def __init__(self, *, config_overrides=(), env=None, **kwargs):
            self.config_overrides = config_overrides
            self.env = env

    class Codex:
        instances = []

        def __init__(self, config=None):
            self.config = config
            self.logged_in_with = None
            self.login_api_key_call_count = 0
            self.chatgpt_login_called = False
            self._account_response = GetAccountResponse(account=Account(ApiKeyAccount()))
            self._account_raises = None
            Codex.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login_api_key(self, api_key):
            self.login_api_key_call_count += 1
            self.logged_in_with = api_key

        def login_chatgpt(self, *a, **k):
            self.chatgpt_login_called = True
            raise AssertionError("login_chatgpt() must never be called")

        # Keyword-only after self, matching the real
        # Codex.account(self, *, refresh_token=False) -> GetAccountResponse
        def account(self, *, refresh_token=False):
            if self._account_raises is not None:
                raise self._account_raises
            return self._account_response

        # thread_start is defined directly here -- never patched via
        # create=True. Keyword-only, matching the real signature's shape
        # (a representative subset of its many keyword args).
        def thread_start(
            self,
            *,
            approval_mode=None,
            base_instructions=None,
            config=None,
            cwd=None,
            developer_instructions=None,
            ephemeral=None,
            model=None,
            model_provider=None,
            personality=None,
            sandbox=None,
            service_name=None,
            service_tier=None,
            session_start_source=None,
            thread_source=None,
        ):
            return Thread(id="codex-thread-default")

    fake.Sandbox = Sandbox
    fake.ApprovalMode = ApprovalMode
    fake.Codex = Codex
    fake._OriginalCodex = Codex
    fake.CodexConfig = CodexConfig
    fake.Thread = Thread
    fake.TurnResult = TurnResult
    fake.TurnStatus = TurnStatus
    fake.TurnError = TurnError
    fake.ApiKeyAccount = ApiKeyAccount
    fake.ChatgptAccount = ChatgptAccount
    fake.AmazonBedrockAccount = AmazonBedrockAccount
    fake.Account = Account
    fake.GetAccountResponse = GetAccountResponse
    sys.modules["openai_codex"] = fake

    fake_errors = types.ModuleType("openai_codex.errors")

    class CodexError(Exception):
        pass

    class TransportClosedError(CodexError):
        pass

    class JsonRpcError(CodexError):
        def __init__(self, code=None, message="", data=None):
            super().__init__(f"JSON-RPC error {code}: {message}")
            self.code = code
            self.message = message
            self.data = data

    class CodexRpcError(JsonRpcError):
        pass

    class ParseError(CodexRpcError):
        pass

    class InvalidRequestError(CodexRpcError):
        pass

    class MethodNotFoundError(CodexRpcError):
        pass

    class InvalidParamsError(CodexRpcError):
        pass

    class InternalRpcError(CodexRpcError):
        pass

    class ServerBusyError(CodexRpcError):
        pass

    class RetryLimitExceededError(ServerBusyError):
        pass

    for name, cls in (
        ("CodexError", CodexError),
        ("JsonRpcError", JsonRpcError),
        ("TransportClosedError", TransportClosedError),
        ("CodexRpcError", CodexRpcError),
        ("ParseError", ParseError),
        ("InvalidRequestError", InvalidRequestError),
        ("MethodNotFoundError", MethodNotFoundError),
        ("InvalidParamsError", InvalidParamsError),
        ("InternalRpcError", InternalRpcError),
        ("ServerBusyError", ServerBusyError),
        ("RetryLimitExceededError", RetryLimitExceededError),
    ):
        setattr(fake_errors, name, cls)
    sys.modules["openai_codex.errors"] = fake_errors
    fake.errors = fake_errors

    return fake, fake_errors


class CodexAdapterTestCase(unittest.TestCase):
    _DEFAULT_KEY = object()
    def setUp(self):
        self._fake_sdk, self._fake_errors = _install_fake_openai_codex()
        sys.modules.pop("orchestrator.adapters.codex_adapter", None)
        import orchestrator.adapters.codex_adapter as coa

        importlib.reload(coa)
        self.coa = coa
        self._tmpdir = tempfile.TemporaryDirectory()
        self._worktree = Path(self._tmpdir.name).resolve() / "worktree"
        self._worktree.mkdir()
        self._api_key = "synthetic-codex-dedicated-key"
        self._env_patch = mock.patch.dict(
            os.environ, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, clear=True
        )
        self._env_patch.start()
        for name in self.coa._AMBIENT_CODEX_CREDENTIAL_VARS:
            os.environ.pop(name, None)
        os.environ.pop("CODEX_TRUSTED_HOST_VERIFIED", None)

    def tearDown(self):
        self._env_patch.stop()
        self._tmpdir.cleanup()
        sys.modules.pop("openai_codex.errors", None)
        sys.modules.pop("openai_codex", None)
        sys.modules.pop("orchestrator.adapters.codex_adapter", None)

    def _request(self, agent_role="emilio", attempt=0, task=None):
        return self.coa.AgentInvocationRequest(
            invocation_id="inv-1",
            mission_id="mission-1",
            agent_role=agent_role,
            attempt=attempt,
            task=task or {"repository": {"worktree_path": str(self._worktree)}},
            requested_at="2026-08-19T12:00:00Z",
            requested_fresh_context=(agent_role == "emma"),
        )

    def _patch_thread_start(self, thread):
        return mock.patch.object(self._fake_sdk._OriginalCodex, "thread_start", lambda self, **kw: thread)

    def _adapter(self, *, api_key=_DEFAULT_KEY, timeout_seconds=None):
        raise AssertionError("authenticated execution belongs to the OS-isolated worker tests")


class PruebaCredencialFaltante(CodexAdapterTestCase):
    def test_construccion_productiva_directa_esta_bloqueada(self):
        with self.assertRaises(self.coa.CodexAdapterError):
            self.coa.CodexAdapter(api_key=self._api_key)

    def test_no_hay_capacidad_importable_para_habilitar_constructor(self):
        import orchestrator.provider_credentials as credentials

        self.assertFalse(hasattr(credentials, "_provider_worker_authority"))
        self.assertFalse(hasattr(credentials, "require_adapter_worker_authority"))

    def test_construccion_directa_no_puede_heredar_ninguna_variable_no_aprobada(self):
        obj = object.__new__(self.coa.CodexAdapter)
        obj._api_key = self._api_key
        self.assertFalse(hasattr(obj, "invoke"))

class PruebaFronteraDeConfianzaReal(CodexAdapterTestCase):
    """Incremento #14, ciclo correctivo -- cierra el hallazgo P1 de Emma:
    CODEX_TRUSTED_HOST_VERIFIED (una autoatestación no verificable) fue
    eliminado. La frontera de confianza real ahora es codex.account(),
    verificada después de login_api_key()."""

    def test_ya_no_existe_codex_trusted_host_verified_como_bypass(self):
        """Ninguna función del adapter referencia ya
        CODEX_TRUSTED_HOST_VERIFIED en su bytecode -- verificado a nivel
        de código, no de texto del módulo (cuyo docstring sí menciona el
        nombre en prosa, explicando que fue eliminado). Establecer la
        variable no habilita confianza: el nuevo límite ambiental la rechaza
        como cualquier estado padre no aprobado."""
        for func in (self.coa._verify_api_key_identity_active,):
            self.assertNotIn("CODEX_TRUSTED_HOST_VERIFIED", func.__code__.co_names)
            self.assertNotIn("CODEX_TRUSTED_HOST_VERIFIED", func.__code__.co_consts)

        self.assertFalse(hasattr(self.coa.CodexAdapter, "invoke"))

    def test_adapter_no_define_ni_lee_auth_json_ambiental(self):
        self.assertFalse(hasattr(self.coa, "_CODEX_AUTH_FILE"))
        self.assertFalse(hasattr(self.coa, "_inspect_file_backend"))

    def test_nunca_llama_login_chatgpt(self):
        source = Path(self.coa.__file__).with_name("codex_worker_runtime.py").read_text()
        self.assertNotIn("login_chatgpt", source)


class PruebaCompatibilidadConSDKReal(CodexAdapterTestCase):
    """Pruebas de compatibilidad diseñadas para fallar si el código de
    producción vuelve a usar start_thread (en vez de thread_start),
    output_schema posicional (en vez de keyword), o account.type directo
    (en vez de account.root.type) -- los tres bugs P1 que las pruebas de
    ciclos anteriores no detectaron, cada uno causado por un simulado que
    no reproducía fielmente la forma real del SDK."""

    def test_account_fake_no_expone_type_directamente_requiere_root(self):
        """El simulado Account nunca define .type en su propio cuerpo --
        solo .root.type, exactamente como el RootModel real. Si el código
        de producción alguna vez regresa a leer account.type directamente,
        esta prueba (y la verificación de producción real) lo detecta
        porque getattr(account, "type", None) es siempre None aquí, igual
        que contra el SDK real."""
        account = self._fake_sdk.Account(self._fake_sdk.ApiKeyAccount())
        self.assertIsNone(getattr(account, "type", None))
        self.assertFalse(hasattr(self._fake_sdk.Account, "type"))
        self.assertEqual(account.root.type, "apiKey")

    def test_thread_start_no_start_thread(self):
        """El simulado nunca define start_thread -- si el adapter llama
        codex.start_thread(...), esto lanza AttributeError, igual que
        contra el SDK real."""
        self.assertFalse(hasattr(self._fake_sdk.Codex, "start_thread"))
        self.assertTrue(hasattr(self._fake_sdk.Codex, "thread_start"))

    def test_output_schema_debe_ser_keyword_nunca_posicional(self):
        """thread.run() simulado replica la firma keyword-only real --
        una llamada posicional (la forma del bug P1 anterior) lanza
        TypeError aquí exactamente como lo haría contra el SDK real."""
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response="{}"
            ),
        )
        with self.assertRaises(TypeError):
            thread.run("prompt", {"output_schema": {}})
        # la forma correcta no lanza
        thread.run("prompt", output_schema={})
        self.assertEqual(thread.run_called_count, 1)


class PruebaRestriccionMultiAgente(CodexAdapterTestCase):
    def test_codex_se_construye_con_multi_agent_deshabilitado(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed,
                final_response=json.dumps({"attempt": 0}),
            ),
        )
        with self._patch_thread_start(thread):
            self._adapter(api_key=self._api_key).invoke(self._request())

        instance = self._fake_sdk.Codex.instances[-1]
        self.assertIsNotNone(instance.config)
        home = Path(instance.config.env["CODEX_HOME"])
        self.assertFalse(home.exists())
        self.assertEqual(instance.config.env["OPENAI_API_KEY"], "")
        self.assertEqual(instance.config.config_overrides, ())
        self.assertEqual(instance.login_api_key_call_count, 1)
        self.assertEqual(instance.logged_in_with, self._api_key)
        self.assertNotIn(self._api_key, repr(instance.config.env))

    def test_restriccion_aplica_a_ambos_roles_sin_sandbox_legacy(self):
        for role in ("emilio", "emma"):
            captured = {}

            def fake_thread_start(self_codex, **kwargs):
                captured.update(kwargs)
                return self._fake_sdk.Thread(
                    id=f"t-{role}",
                    turn_result=self._fake_sdk.TurnResult(
                        status=self._fake_sdk.TurnStatus.completed,
                        final_response=json.dumps({"attempt": 0}),
                    ),
                )

            with mock.patch.object(self._fake_sdk.Codex, "thread_start", fake_thread_start):
                self._adapter(api_key=self._api_key).invoke(self._request(agent_role=role))

            self.assertNotIn("sandbox", captured)
            self.assertEqual(captured["approval_mode"], self._fake_sdk.ApprovalMode.deny_all)
            self.assertEqual(self._fake_sdk.Codex.instances[-1].config.config_overrides, ())


class PruebaHomeCodexAislado(CodexAdapterTestCase):
    PROHIBITED_FEATURES = {
        "multi_agent",
        "apps",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "in_app_browser",
        "plugins",
        "plugin_sharing",
        "remote_plugin",
        "enable_mcp_apps",
        "skill_search",
        "skill_mcp_dependency_install",
    }

    def _invoke_and_capture(self, role="emilio", *, raise_exc=None):
        captured = {}

        def fake_thread_start(codex, **kwargs):
            home = Path(codex.config.env["CODEX_HOME"])
            captured["home"] = home
            captured["config"] = tomllib.loads((home / "config.toml").read_text())
            captured["entries"] = sorted(path.name for path in home.iterdir())
            captured["thread_kwargs"] = kwargs
            return self._fake_sdk.Thread(
                id="isolated-thread",
                turn_result=self._fake_sdk.TurnResult(
                    status=self._fake_sdk.TurnStatus.completed,
                    final_response=json.dumps({"attempt": 0}),
                ),
                raise_exc=raise_exc,
            )

        with mock.patch.object(self._fake_sdk.Codex, "thread_start", fake_thread_start):
            result = self._adapter(api_key=self._api_key).invoke(self._request(agent_role=role))
        captured["result"] = result
        return captured

    def test_home_unico_por_invocacion_y_limpiado_sin_mutar_parent(self):
        original = os.environ.get("CODEX_HOME")
        first = self._invoke_and_capture()
        second = self._invoke_and_capture()
        self.assertNotEqual(first["home"], second["home"])
        self.assertFalse(first["home"].exists())
        self.assertFalse(second["home"].exists())
        self.assertEqual(os.environ.get("CODEX_HOME"), original)

    def test_config_exacta_falla_cerrado_sin_capacidades_ambientales(self):
        captured = self._invoke_and_capture()
        config = captured["config"]
        self.assertEqual(config["web_search"], "disabled")
        self.assertFalse(config["allow_login_shell"])
        self.assertFalse(config["agents"]["enabled"])
        self.assertFalse(config["apps"]["_default"]["enabled"])
        self.assertEqual(config["mcp_servers"], {})
        self.assertEqual(config["hooks"], {})
        self.assertEqual(config["skills"]["config"], [])
        self.assertTrue(self.PROHIBITED_FEATURES <= set(config["features"]))
        self.assertTrue(all(config["features"][name] is False for name in self.PROHIBITED_FEATURES))
        self.assertEqual(config["shell_environment_policy"]["inherit"], "core")
        self.assertFalse(config["shell_environment_policy"]["ignore_default_excludes"])
        self.assertNotIn("node_repl", json.dumps(config))
        self.assertNotIn("auth.json", captured["entries"])
        self.assertNotIn("plugins", captured["entries"])
        child_env = self._fake_sdk.Codex.instances[-1].config.env
        self.assertEqual(
            set(child_env),
            {"CODEX_HOME", "HOME", "TMPDIR", "TMP", "TEMP", "OPENAI_API_KEY"},
        )
        self.assertEqual(child_env["OPENAI_API_KEY"], "")
        self.assertEqual(child_env["HOME"], child_env["CODEX_HOME"])
        self.assertEqual(child_env["TMPDIR"], child_env["CODEX_HOME"])
        self.assertEqual(child_env["TMP"], child_env["CODEX_HOME"])
        self.assertEqual(child_env["TEMP"], child_env["CODEX_HOME"])
        self.assertNotIn(self._api_key, child_env.values())

    def test_deny_all_y_sin_sandbox_legacy_para_ambos_roles(self):
        for role in ("emilio", "emma"):
            captured = self._invoke_and_capture(role)
            kwargs = captured["thread_kwargs"]
            self.assertEqual(kwargs["approval_mode"], self._fake_sdk.ApprovalMode.deny_all)
            self.assertNotIn("sandbox", kwargs)
            self.assertEqual(kwargs["cwd"], str(self._worktree.resolve()))

    def test_perfiles_por_rol_solo_worktree_y_sin_red(self):
        for role, access in (("emilio", "write"), ("emma", "read")):
            config = self._invoke_and_capture(role)["config"]
            profile_name = f"zentra-{role}"
            self.assertEqual(config["default_permissions"], profile_name)
            profile = config["permissions"][profile_name]
            self.assertEqual(profile["workspace_roots"], {str(self._worktree.resolve()): True})
            self.assertEqual(profile["filesystem"][":minimal"], "read")
            self.assertEqual(profile["filesystem"][":workspace_roots"]["."], access)
            self.assertFalse(profile["network"]["enabled"])

    def test_worktree_no_canonico_inexistente_archivo_y_symlink_fallan_antes_de_codex(self):
        outside = Path(self._tmpdir.name) / "outside"
        outside.mkdir()
        symlink = Path(self._tmpdir.name) / "link"
        symlink.symlink_to(outside, target_is_directory=True)
        regular = Path(self._tmpdir.name) / "regular.txt"
        regular.write_text("x")
        invalid = (
            "relative/path",
            str(Path(self._tmpdir.name) / "missing"),
            str(regular),
            str(symlink),
            str(self._worktree / ".." / "worktree"),
        )
        before = len(self._fake_sdk.Codex.instances)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(self.coa.CodexAdapterError):
                self._adapter(api_key=self._api_key).invoke(
                    self._request(task={"repository": {"worktree_path": value}})
                )
        self.assertEqual(len(self._fake_sdk.Codex.instances), before)

    def test_home_se_limpia_en_error_de_provider(self):
        captured = self._invoke_and_capture(raise_exc=self._fake_errors.TransportClosedError("x"))
        self.assertEqual(captured["result"].outcome, "unavailable")
        self.assertFalse(captured["home"].exists())

    def test_fallos_de_creacion_config_y_cleanup_fallan_cerrado(self):
        with mock.patch.object(self.coa.tempfile, "mkdtemp", side_effect=OSError("no home")):
            with self.assertRaises(self.coa.CodexAdapterError):
                self._adapter(api_key=self._api_key).invoke(self._request())
        with mock.patch.object(Path, "write_text", side_effect=OSError("no config")):
            with self.assertRaises(self.coa.CodexAdapterError):
                self._adapter(api_key=self._api_key).invoke(self._request())
        captured_home = []
        real_rmtree = self.coa.shutil.rmtree

        def broken_cleanup(path):
            captured_home.append(Path(path))
            raise OSError("cannot clean")

        with mock.patch.object(self.coa.shutil, "rmtree", side_effect=broken_cleanup):
            with self.assertRaises(self.coa.CodexAdapterError):
                self._adapter(api_key=self._api_key).invoke(self._request())
        self.assertTrue(captured_home)
        for path in captured_home:
            real_rmtree(path, ignore_errors=True)


class PruebaCompatibilidadConSDKRealContinuacion(CodexAdapterTestCase):
    def test_adapter_real_invoca_thread_start_con_perfil_y_cwd(self):
        captured = {}

        def fake_thread_start(self_codex, **kwargs):
            captured.update(kwargs)
            return self._fake_sdk.Thread(
                id="t-1",
                turn_result=self._fake_sdk.TurnResult(
                    status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps({"attempt": 0})
                ),
            )

        with mock.patch.object(self._fake_sdk.Codex, "thread_start", fake_thread_start):
            adapter = self._adapter(api_key=self._api_key)
            adapter.invoke(self._request(agent_role="emma"))
        self.assertNotIn("sandbox", captured)
        self.assertEqual(captured.get("approval_mode"), self._fake_sdk.ApprovalMode.deny_all)
        self.assertEqual(captured.get("cwd"), str(self._worktree.resolve()))

    def test_adapter_real_invoca_run_con_output_schema_keyword(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps({"attempt": 0})
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            adapter.invoke(self._request())
        self.assertEqual(thread.run_called_count, 1)
        self.assertIn("handoff_document_ref", thread.last_kwargs["output_schema"]["properties"])


class PruebaInvocacionExitosa(CodexAdapterTestCase):
    def test_json_decode_error_es_invalid_output_nunca_completed(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response="not valid json{{{"
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIsNone(result.evidence)

    def test_thread_run_llamado_exactamente_una_vez_nunca_reintenta(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps(evidence)
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            adapter.invoke(self._request())
        self.assertEqual(thread.run_called_count, 1)


class PruebaEstadoDeTurnoNoCompletado(CodexAdapterTestCase):
    """Incremento #14, ciclo correctivo -- cierra el hallazgo P2 de Emma:
    TurnResult.status/.error ahora se verifican explícitamente."""

    def test_status_failed_es_failed_incluso_con_final_response_parseable(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.failed,
                final_response=json.dumps({"looks": "valid"}),
                error=self._fake_sdk.TurnError("something broke"),
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(result.evidence)
        self.assertIn("something broke", result.error_detail)

    def test_status_interrupted_es_failed(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(status=self._fake_sdk.TurnStatus.interrupted),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "failed")

    def test_status_completed_procede_normalmente(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps({"attempt": 0})
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")


class PruebaMapeoDeExcepciones(CodexAdapterTestCase):
    def _invoke_with_exc(self, exc):
        thread = self._fake_sdk.Thread(id="t-1", raise_exc=exc)
        with self._patch_thread_start(thread):
            adapter = self._adapter(api_key=self._api_key)
            return adapter.invoke(self._request())

    def test_transport_closed_es_unavailable(self):
        result = self._invoke_with_exc(self._fake_errors.TransportClosedError("transport closed"))
        self.assertEqual(result.outcome, "unavailable")

    def test_server_busy_es_failed(self):
        result = self._invoke_with_exc(self._fake_errors.ServerBusyError(-32000, "overloaded"))
        self.assertEqual(result.outcome, "failed")

    def test_retry_limit_exceeded_es_failed(self):
        result = self._invoke_with_exc(self._fake_errors.RetryLimitExceededError(-32000, "retry limit exceeded"))
        self.assertEqual(result.outcome, "failed")

    def test_invalid_params_es_failed(self):
        result = self._invoke_with_exc(self._fake_errors.InvalidParamsError(-32602, "bad params"))
        self.assertEqual(result.outcome, "failed")

    def test_codex_error_generico_es_failed(self):
        result = self._invoke_with_exc(self._fake_errors.CodexError("generic"))
        self.assertEqual(result.outcome, "failed")

    def test_excepcion_no_reconocida_es_failed_nunca_propaga(self):
        result = self._invoke_with_exc(ValueError("totally unrelated bug"))
        self.assertEqual(result.outcome, "failed")
        self.assertIn("unexpected error", result.error_detail)

    def test_mapeo_nunca_lee_texto_libre_para_decidir(self):
        """Dos ServerBusyError con mensajes de texto completamente
        distintos producen el mismo outcome -- el despacho es por tipo,
        nunca por contenido del mensaje."""
        r1 = self._invoke_with_exc(self._fake_errors.ServerBusyError(-32000, "SWITCH_PROVIDER_NOW"))
        r2 = self._invoke_with_exc(self._fake_errors.ServerBusyError(-32000, "unrelated text entirely"))
        self.assertEqual(r1.outcome, r2.outcome)


class PruebaTimeoutAdapter(CodexAdapterTestCase):
    def test_wait_for_timeout_produce_outcome_timeout(self):
        async def _hang(*a, **k):
            await asyncio.sleep(10)

        def _timeout(awaitable, *args, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError

        adapter = self._adapter(api_key=self._api_key, timeout_seconds=0.01)
        with mock.patch("asyncio.wait_for", side_effect=_timeout):
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "timeout")


class PruebaPermisosPorRol(CodexAdapterTestCase):
    def test_emilio_usa_perfil_write(self):
        parsed = tomllib.loads(self.coa._render_isolated_config("emilio", self._worktree))
        self.assertEqual(
            parsed["permissions"]["zentra-emilio"]["filesystem"][":workspace_roots"]["."],
            "write",
        )

    def test_emma_usa_perfil_read(self):
        parsed = tomllib.loads(self.coa._render_isolated_config("emma", self._worktree))
        self.assertEqual(
            parsed["permissions"]["zentra-emma"]["filesystem"][":workspace_roots"]["."],
            "read",
        )


class PruebaEsquemaDeEvidencia(CodexAdapterTestCase):
    def test_output_schema_usa_definicion_correcta_por_rol(self):
        schema_emilio = self.coa._load_evidence_schema("emilio")
        schema_emma = self.coa._load_evidence_schema("emma")
        self.assertEqual(schema_emilio.get("type"), "object")
        self.assertEqual(schema_emma.get("type"), "object")
        self.assertNotIn("$ref", schema_emilio)
        self.assertNotIn("$ref", schema_emma)
        self.assertIn("handoff_document_ref", schema_emilio["properties"])
        self.assertIn("verdict", schema_emma["properties"])

    def test_schema_proyectado_excluye_identidad_de_infraestructura(self):
        infrastructure = {
            "invocation_id", "provider", "provider_session_id",
            "provider_conversation_id",
        }
        for role, entry in (("emilio", "builder_evidence_entry"),
                            ("emma", "reviewer_evidence_entry")):
            with self.subTest(role=role):
                schema = self.coa._load_evidence_schema(role)
                self.assertTrue(infrastructure.isdisjoint(schema["properties"]))
                self.assertFalse(schema["additionalProperties"])


def _find_keys(node, keys):
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                hits.append(key)
            hits.extend(_find_keys(value, keys))
    elif isinstance(node, list):
        for item in node:
            hits.extend(_find_keys(item, keys))
    return hits


def _find_refs(node):
    refs = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                refs.add(value)
            else:
                refs |= _find_refs(value)
    elif isinstance(node, list):
        for item in node:
            refs |= _find_refs(item)
    return refs


def _find_ref_nodes(node):
    nodes = []
    if isinstance(node, dict):
        if "$ref" in node:
            nodes.append(node)
        for value in node.values():
            nodes.extend(_find_ref_nodes(value))
    elif isinstance(node, list):
        for item in node:
            nodes.extend(_find_ref_nodes(item))
    return nodes


class PruebaProyeccionCodex(CodexAdapterTestCase):
    _IDENTITY = {
        "invocation_id", "provider", "provider_session_id",
        "provider_conversation_id",
    }
    _UNSUPPORTED = {
        "if", "then", "else", "allOf", "not",
        "dependentRequired", "dependentSchemas",
    }

    def _canonical_path(self):
        return Path(self.coa.__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"

    def _canonical(self):
        return json.loads(self._canonical_path().read_text(encoding="utf-8"))

    def _expected_reachable_after_identity_sanitization(self, role):
        entry_name = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}[role]
        definitions = copy.deepcopy(self._canonical()["definitions"])
        for name in self._IDENTITY:
            definitions[entry_name]["properties"].pop(name, None)
        seen, frontier = set(), {entry_name}
        while frontier:
            name = frontier.pop()
            if name in seen or name not in definitions:
                continue
            seen.add(name)
            frontier |= {
                ref.rsplit("/", 1)[-1]
                for ref in _find_refs(definitions[name])
                if ref.startswith("#/definitions/")
            }
        return seen - {entry_name}

    def test_ambos_roles_tienen_raiz_inline_y_defs_alcanzables_exactos(self):
        for role in ("emilio", "emma"):
            with self.subTest(role=role):
                schema = self.coa._load_evidence_schema(role)
                self.assertEqual(schema.get("type"), "object")
                self.assertNotIn("$ref", schema)
                self.assertNotIn("definitions", schema)
                self.assertEqual(
                    set(schema["$defs"]),
                    self._expected_reachable_after_identity_sanitization(role),
                )

    def test_refs_solo_defs_sin_colgantes_y_sin_hermanos(self):
        for role in ("emilio", "emma"):
            schema = self.coa._load_evidence_schema(role)
            refs = _find_refs(schema)
            self.assertTrue(refs)
            self.assertTrue(all(ref.startswith("#/$defs/") for ref in refs))
            self.assertEqual(
                {ref.rsplit("/", 1)[-1] for ref in refs} - set(schema["$defs"]),
                set(),
            )
            self.assertTrue(all(set(node) == {"$ref"} for node in _find_ref_nodes(schema)))

    def test_keywords_incompatibles_ausentes_y_restricciones_soportadas_presentes(self):
        for role in ("emilio", "emma"):
            schema = self.coa._load_evidence_schema(role)
            self.assertEqual(_find_keys(schema, self._UNSUPPORTED), [])
            self.assertIn("minLength", _find_keys(schema, {"minLength"}))
            self.assertIn("additionalProperties", _find_keys(schema, {"additionalProperties"}))
            self.assertFalse(schema["additionalProperties"])

    def test_identidad_de_infraestructura_no_aparece_en_ningun_lugar(self):
        for role in ("emilio", "emma"):
            serialized = json.dumps(self.coa._load_evidence_schema(role), sort_keys=True)
            for name in self._IDENTITY:
                self.assertNotIn(name, serialized, msg=f"role={role}, identity={name}")
            self.assertNotIn("nullable_invocation_id", serialized)
            self.assertNotIn("nullable_provider_identity", serialized)

    def test_canonico_conserva_identidad_y_bytes_exactos(self):
        import hashlib

        path = self._canonical_path()
        before = path.read_bytes()
        canonical = json.loads(before)
        for entry_name in ("builder_evidence_entry", "reviewer_evidence_entry"):
            self.assertTrue(self._IDENTITY <= set(canonical["definitions"][entry_name]["properties"]))
        self.coa._load_evidence_schema("emilio")
        self.coa._load_evidence_schema("emma")
        after = path.read_bytes()
        self.assertEqual(after, before)
        self.assertEqual(hashlib.sha256(after).digest(), hashlib.sha256(before).digest())

    def test_schema_corregido_llega_a_thread_run(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed,
                final_response=json.dumps({"attempt": 0}),
            ),
        )
        with self._patch_thread_start(thread):
            self._adapter(api_key=self._api_key).invoke(self._request(agent_role="emilio"))
        schema = thread.last_kwargs["output_schema"]
        self.assertEqual(schema.get("type"), "object")
        self.assertEqual(_find_keys(schema, self._UNSUPPORTED), [])
        serialized = json.dumps(schema)
        self.assertTrue(all(name not in serialized for name in self._IDENTITY))

    def test_helpers_soportan_refs_anidados_y_ciclicos(self):
        definitions = {
            "entry": {"type": "object", "properties": {"x": {"$ref": "#/definitions/a"}}},
            "a": {"anyOf": [{"$ref": "#/definitions/b"}]},
            "b": {"items": {"$ref": "#/definitions/a"}},
            "unused": {"type": "string"},
        }
        self.assertEqual(self.coa._codex_reachable_definitions(definitions, "entry"), {"entry", "a", "b"})

    def test_ref_con_hermanos_arbitrarios_se_reduce_a_ref_only(self):
        node = {
            "outer": [{"$ref": "#/$defs/x", "description": "x", "title": "y", "default": 1}]
        }
        self.assertEqual(
            self.coa._strip_ref_sibling_keywords(node),
            {"outer": [{"$ref": "#/$defs/x"}]},
        )

    def test_strip_unsupported_recursivo_preserva_supported(self):
        node = {
            "allOf": [],
            "properties": {"x": {"if": {}, "then": {}, "minLength": 1}},
            "additionalProperties": False,
        }
        self.assertEqual(
            self.coa._strip_codex_unsupported_keywords(node),
            {"properties": {"x": {"minLength": 1}}, "additionalProperties": False},
        )


class PruebaDependenciasDeclaradas(unittest.TestCase):
    def test_codex_sdk_y_cli_tienen_los_pins_runtime_validados(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
        self.assertIn("openai-codex==0.147.0\n", requirements)
        self.assertIn("openai-codex-cli-bin==0.147.0\n", requirements)


if __name__ == "__main__":
    unittest.main()
