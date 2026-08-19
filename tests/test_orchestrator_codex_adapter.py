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
import importlib
import inspect
import json
import os
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from unittest import mock


def _install_fake_openai_codex():
    fake = types.ModuleType("openai_codex")

    class Sandbox:
        read_only = "read_only"
        workspace_write = "workspace_write"
        full_access = "full_access"

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

    class Codex:
        instances = []

        def __init__(self, config=None):
            self.logged_in_with = None
            self.chatgpt_login_called = False
            self._account_response = GetAccountResponse(account=Account(ApiKeyAccount()))
            self._account_raises = None
            Codex.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login_api_key(self, api_key):
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
    fake.Codex = Codex
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
    def setUp(self):
        self._fake_sdk, self._fake_errors = _install_fake_openai_codex()
        sys.modules.pop("orchestrator.adapters.codex_adapter", None)
        import orchestrator.adapters.codex_adapter as coa

        importlib.reload(coa)
        self.coa = coa
        self._tmpdir = tempfile.TemporaryDirectory()
        self._auth_file_patch = mock.patch.object(
            self.coa, "_CODEX_AUTH_FILE", Path(self._tmpdir.name) / "auth.json"
        )
        self._auth_file_patch.start()
        self._env_patch = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-codex-test-value"})
        self._env_patch.start()
        os.environ.pop("CODEX_TRUSTED_HOST_VERIFIED", None)

    def tearDown(self):
        self._env_patch.stop()
        self._auth_file_patch.stop()
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
            task=task or {"repository": {"worktree_path": "/tmp/worktree"}},
            requested_at="2026-08-19T12:00:00Z",
            requested_fresh_context=(agent_role == "emma"),
        )

    def _patch_thread_start(self, thread):
        return mock.patch.object(self._fake_sdk.Codex, "thread_start", lambda self, **kw: thread)


class PruebaCredencialFaltante(CodexAdapterTestCase):
    def test_sin_openai_api_key_lanza(self):
        del os.environ["OPENAI_API_KEY"]
        adapter = self.coa.CodexAdapter()
        with self.assertRaises(self.coa.CodexAdapterError):
            adapter.invoke(self._request())


class PruebaFronteraDeConfianzaReal(CodexAdapterTestCase):
    """Incremento #14, ciclo correctivo -- cierra el hallazgo P1 de Emma:
    CODEX_TRUSTED_HOST_VERIFIED (una autoatestación no verificable) fue
    eliminado. La frontera de confianza real ahora es codex.account(),
    verificada después de login_api_key()."""

    def test_account_apikey_permite_la_invocacion(self):
        turn = self._fake_sdk.TurnResult(
            status=self._fake_sdk.TurnStatus.completed,
            final_response=json.dumps({"attempt": 0}),
        )
        with self._patch_thread_start(self._fake_sdk.Thread(id="t-1", turn_result=turn)):
            adapter = self.coa.CodexAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")

    def test_account_chatgpt_bloquea_pese_a_login_api_key_exitoso(self):
        """El bug documentado (GitHub #2733/#3286): login_api_key() no
        siempre reemplaza una sesión ChatGPT activa. account() revela
        esto -- y el adapter debe rechazar la invocación."""

        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_response = self._fake_sdk.GetAccountResponse(
                account=self._fake_sdk.Account(
                    self._fake_sdk.ChatgptAccount(email="jose@example.com")
                )
            )
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex):
            adapter = self.coa.CodexAdapter()
            with self.assertRaises(self.coa.CodexAdapterError):
                adapter.invoke(self._request())

    def test_account_bedrock_bloquea(self):
        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_response = self._fake_sdk.GetAccountResponse(
                account=self._fake_sdk.Account(self._fake_sdk.AmazonBedrockAccount())
            )
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex):
            adapter = self.coa.CodexAdapter()
            with self.assertRaises(self.coa.CodexAdapterError):
                adapter.invoke(self._request())

    def test_account_none_bloquea(self):
        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_response = self._fake_sdk.GetAccountResponse(account=None)
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex):
            adapter = self.coa.CodexAdapter()
            with self.assertRaises(self.coa.CodexAdapterError):
                adapter.invoke(self._request())

    def test_account_root_malformado_falla_cerrado(self):
        """Un .root que no expone .type (forma inesperada/corrupta) debe
        fallar cerrado, nunca lanzar un AttributeError sin manejar ni,
        peor, ser tratado como apiKey."""

        class _RootSinType:
            pass

        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_response = self._fake_sdk.GetAccountResponse(
                account=self._fake_sdk.Account(_RootSinType())
            )
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex):
            adapter = self.coa.CodexAdapter()
            with self.assertRaises(self.coa.CodexAdapterError):
                adapter.invoke(self._request())

    def test_verificacion_no_depende_de_account_type_directo(self):
        """La verificación de producción debe leer account.root.type, no
        account.type -- un Account cuyo wrapper no expone .type
        directamente (la forma real del SDK) debe seguir funcionando
        correctamente cuando root.type == 'apiKey'."""
        account = self._fake_sdk.Account(self._fake_sdk.ApiKeyAccount())
        self.assertFalse(hasattr(account, "type"))
        self.assertEqual(account.root.type, "apiKey")

        turn = self._fake_sdk.TurnResult(
            status=self._fake_sdk.TurnStatus.completed,
            final_response=json.dumps({"attempt": 0}),
        )

        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_response = self._fake_sdk.GetAccountResponse(account=account)
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex), self._patch_thread_start(
            self._fake_sdk.Thread(id="t-1", turn_result=turn)
        ):
            adapter = self.coa.CodexAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")

    def test_account_que_lanza_excepcion_bloquea(self):
        def make_codex(config=None):
            codex = self._fake_sdk.Codex(config=config)
            codex._account_raises = RuntimeError("network blip calling account()")
            return codex

        with mock.patch.object(self.coa, "Codex", side_effect=make_codex):
            adapter = self.coa.CodexAdapter()
            with self.assertRaises(self.coa.CodexAdapterError):
                adapter.invoke(self._request())

    def test_ya_no_existe_codex_trusted_host_verified_como_bypass(self):
        """Ninguna función del adapter referencia ya
        CODEX_TRUSTED_HOST_VERIFIED en su bytecode -- verificado a nivel
        de código, no de texto del módulo (cuyo docstring sí menciona el
        nombre en prosa, explicando que fue eliminado). Estableciendo la
        variable a '1' no debe tener ningún efecto sobre el resultado."""
        for func in (
            self.coa.CodexAdapter.invoke,
            self.coa.CodexAdapter._run,
            self.coa._verify_codex_host_trust_or_raise,
            self.coa._verify_api_key_identity_active,
        ):
            self.assertNotIn("CODEX_TRUSTED_HOST_VERIFIED", func.__code__.co_names)
            self.assertNotIn("CODEX_TRUSTED_HOST_VERIFIED", func.__code__.co_consts)

        os.environ["CODEX_TRUSTED_HOST_VERIFIED"] = "1"
        self.coa._CODEX_AUTH_FILE.write_text(json.dumps({"chatgpt_account_id": "abc123"}))
        adapter = self.coa.CodexAdapter()
        with self.assertRaises(self.coa.CodexAdapterError):
            adapter.invoke(self._request())
        os.environ.pop("CODEX_TRUSTED_HOST_VERIFIED", None)

    def test_archivo_auth_json_con_marcador_chatgpt_bloquea_como_prechequeo_barato(self):
        self.coa._CODEX_AUTH_FILE.write_text(json.dumps({"chatgpt_account_id": "abc123"}))
        adapter = self.coa.CodexAdapter()
        with self.assertRaises(self.coa.CodexAdapterError):
            adapter.invoke(self._request())

    def test_nunca_llama_login_chatgpt(self):
        for func in (self.coa.CodexAdapter.invoke, self.coa.CodexAdapter._run):
            self.assertNotIn("login_chatgpt", func.__code__.co_names)


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

    def test_adapter_real_invoca_thread_start_con_sandbox_y_cwd(self):
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
            adapter = self.coa.CodexAdapter()
            adapter.invoke(self._request(agent_role="emma", task={"repository": {"worktree_path": "/tmp/wt"}}))
        self.assertEqual(captured.get("sandbox"), self._fake_sdk.Sandbox.read_only)
        self.assertEqual(captured.get("cwd"), "/tmp/wt")

    def test_adapter_real_invoca_run_con_output_schema_keyword(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps({"attempt": 0})
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self.coa.CodexAdapter()
            adapter.invoke(self._request())
        self.assertEqual(thread.run_called_count, 1)
        self.assertIn("builder_evidence_entry", json.dumps(thread.last_kwargs["output_schema"]))


class PruebaInvocacionExitosa(CodexAdapterTestCase):
    def test_completed_con_evidencia_y_thread_id(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        thread = self._fake_sdk.Thread(
            id="codex-thread-abc",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response=json.dumps(evidence)
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self.coa.CodexAdapter()
            result = adapter.invoke(self._request())

        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "codex")
        self.assertEqual(result.evidence, evidence)
        self.assertEqual(result.provider_conversation_id, "codex-thread-abc")
        self.assertIsNone(result.provider_session_id)
        self.assertEqual(result.invocation_id, "inv-1")

    def test_json_decode_error_es_invalid_output_nunca_completed(self):
        thread = self._fake_sdk.Thread(
            id="t-1",
            turn_result=self._fake_sdk.TurnResult(
                status=self._fake_sdk.TurnStatus.completed, final_response="not valid json{{{"
            ),
        )
        with self._patch_thread_start(thread):
            adapter = self.coa.CodexAdapter()
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
            adapter = self.coa.CodexAdapter()
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
            adapter = self.coa.CodexAdapter()
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
            adapter = self.coa.CodexAdapter()
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
            adapter = self.coa.CodexAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")


class PruebaMapeoDeExcepciones(CodexAdapterTestCase):
    def _invoke_with_exc(self, exc):
        thread = self._fake_sdk.Thread(id="t-1", raise_exc=exc)
        with self._patch_thread_start(thread):
            adapter = self.coa.CodexAdapter()
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

        adapter = self.coa.CodexAdapter(timeout_seconds=0.01)
        with mock.patch.object(adapter, "_run", side_effect=_hang):
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "timeout")


class PruebaPermisosPorRol(CodexAdapterTestCase):
    def test_emilio_usa_workspace_write(self):
        self.assertEqual(self.coa._SANDBOX["emilio"], "workspace_write")

    def test_emma_usa_read_only(self):
        self.assertEqual(self.coa._SANDBOX["emma"], "read_only")


class PruebaEsquemaDeEvidencia(CodexAdapterTestCase):
    def test_output_schema_usa_definicion_correcta_por_rol(self):
        schema_emilio = self.coa._load_evidence_schema("emilio")
        schema_emma = self.coa._load_evidence_schema("emma")
        self.assertIn("builder_evidence_entry", schema_emilio["$ref"])
        self.assertIn("reviewer_evidence_entry", schema_emma["$ref"])
        self.assertIn("definitions", schema_emilio)


if __name__ == "__main__":
    unittest.main()
