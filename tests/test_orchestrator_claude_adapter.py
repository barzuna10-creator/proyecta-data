"""Pruebas para orchestrator/adapters/claude_adapter.py (Incremento #14,
ciclo correctivo -- cierra los hallazgos P2 de Emma).

Ninguna prueba hace una llamada real a Claude, a la red, o a un
subprocess. El paquete real `claude_agent_sdk` no está instalado en este
entorno -- cada prueba instala un módulo simulado (stand-in) en
sys.modules ANTES de importar el adapter, siguiendo la técnica estándar
para probar código que depende de un SDK pesado sin instalarlo.

**Fidelidad del simulado, verificada contra el paquete real
`claude-agent-sdk==0.2.141` instalado en un venv desechable durante este
ciclo correctivo**: el ciclo anterior construyó `ResultError` con un
constructor `__init__(self, message="", terminal_reason=None,
api_error_status=None, subtype=None)` que **no existe en el SDK real** --
el constructor real es `ResultError(message, data=None, exit_code=None)`,
y `.terminal_reason`/`.subtype`/`.api_error_status`/`.errors`/`.result`/
`.session_id` son atributos derivados internamente de `data` (un dict), no
parámetros de palabra clave directos. El simulado aquí replica exactamente
esa forma real -- construyendo un `ResultError` de prueba ahora requiere
pasar `data={"terminal_reason": ..., ...}`, exactamente como contra el
SDK real."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


def _install_fake_claude_agent_sdk():
    """Crea y registra un módulo `claude_agent_sdk` simulado con la misma
    forma (nombres de clases/excepciones) que la documentación en vivo
    verificada para este incremento describe."""
    fake = types.ModuleType("claude_agent_sdk")

    class ClaudeSDKError(Exception):
        pass

    class CLINotFoundError(ClaudeSDKError):
        pass

    class CLIConnectionError(ClaudeSDKError):
        pass

    class ProcessError(ClaudeSDKError):
        def __init__(self, message, exit_code=None, stderr=None):
            self.exit_code = exit_code
            self.stderr = stderr
            if exit_code is not None:
                message = f"{message} (exit code: {exit_code})"
            if stderr:
                message = f"{message}\nError output: {stderr}"
            super().__init__(message)

    class ResultError(ProcessError):
        # Matches the real constructor exactly: message/data/exit_code
        # only -- terminal_reason/subtype/api_error_status/errors/result/
        # session_id are derived from `data`, never passed directly.
        def __init__(self, message, data=None, exit_code=None):
            data = data if isinstance(data, dict) else {}
            self.data = data
            self.subtype = data.get("subtype")
            self.errors = data.get("errors") or []
            self.result = data.get("result")
            self.api_error_status = data.get("api_error_status")
            self.terminal_reason = data.get("terminal_reason")
            self.session_id = data.get("session_id")
            super().__init__(message, exit_code=exit_code)

    class CLIJSONDecodeError(ClaudeSDKError):
        pass

    class ResultMessage:
        def __init__(self, structured_output=None, session_id=None, is_error=False, errors=None, api_error_status=None):
            self.structured_output = structured_output
            self.session_id = session_id
            self.is_error = is_error
            self.errors = errors
            self.api_error_status = api_error_status

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake.ClaudeSDKError = ClaudeSDKError
    fake.CLINotFoundError = CLINotFoundError
    fake.CLIConnectionError = CLIConnectionError
    fake.ProcessError = ProcessError
    fake.ResultError = ResultError
    fake.CLIJSONDecodeError = CLIJSONDecodeError
    fake.ResultMessage = ResultMessage
    fake.ClaudeAgentOptions = ClaudeAgentOptions
    fake.ClaudeSDKClient = None  # replaced per-test via mock.patch
    sys.modules["claude_agent_sdk"] = fake
    return fake


class _FakeClient:
    """Async context-manager stand-in for ClaudeSDKClient, configurable
    per test with a sequence of messages to yield or an exception to
    raise."""

    def __init__(self, *, messages=None, raise_exc=None, options=None):
        self._messages = messages or []
        self._raise_exc = raise_exc
        self.options = options
        self.queried_with = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def query(self, prompt):
        self.queried_with = prompt

    async def receive_response(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        for m in self._messages:
            yield m


class ClaudeAdapterTestCase(unittest.TestCase):
    def setUp(self):
        self._fake_sdk = _install_fake_claude_agent_sdk()
        sys.modules.pop("orchestrator.adapters.claude_adapter", None)
        import orchestrator.adapters.claude_adapter as ca

        importlib.reload(ca)
        self.ca = ca
        self._env_patch = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-value"})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        sys.modules.pop("claude_agent_sdk", None)
        sys.modules.pop("orchestrator.adapters.claude_adapter", None)

    def _request(self, agent_role="emilio", attempt=0, task=None):
        return self.ca.AgentInvocationRequest(
            invocation_id="inv-1",
            mission_id="mission-1",
            agent_role=agent_role,
            attempt=attempt,
            task=task or {"repository": {"worktree_path": "/tmp/worktree"}},
            requested_at="2026-08-19T12:00:00Z",
            requested_fresh_context=(agent_role == "emma"),
        )


class PruebaCredencialFaltante(ClaudeAdapterTestCase):
    def test_sin_anthropic_api_key_lanza_antes_de_construir_cliente(self):
        del os.environ["ANTHROPIC_API_KEY"]
        adapter = self.ca.ClaudeAdapter()
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_nunca_lee_el_valor_de_la_api_key_en_una_variable(self):
        """El adapter solo verifica presencia -- nunca asigna
        os.environ['ANTHROPIC_API_KEY'] a ninguna variable local ni la
        pasa a ningún constructor explícitamente (el subprocess CLI
        hereda el entorno por sí mismo)."""
        import inspect

        source = inspect.getsource(self.ca)
        self.assertNotIn('os.environ["ANTHROPIC_API_KEY"]', source)
        self.assertIn('"ANTHROPIC_API_KEY" not in os.environ', source)


class PruebaInvocacionExitosa(ClaudeAdapterTestCase):
    def test_completed_con_evidencia_y_session_id(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(structured_output=evidence, session_id="claude-session-abc")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter()
            result = adapter.invoke(self._request())

        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "claude")
        self.assertEqual(result.evidence, evidence)
        self.assertEqual(result.provider_session_id, "claude-session-abc")
        self.assertIsNone(result.provider_conversation_id)
        self.assertEqual(result.invocation_id, "inv-1")
        self.assertTrue(result.fresh_context_attested)

    def test_structured_output_ausente_es_invalid_output(self):
        result_msg = self._fake_sdk.ResultMessage(structured_output=None, session_id="s-1")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIsNone(result.evidence)


class PruebaIsErrorEnResultMessage(ClaudeAdapterTestCase):
    """Incremento #14, ciclo correctivo -- cierra el hallazgo P2 de Emma:
    ResultMessage.is_error/.errors/.api_error_status ahora se verifican
    explícitamente, defensa en profundidad independiente de si
    structured_output resulta estar poblado."""

    def test_is_error_true_es_failed_incluso_con_structured_output_poblado(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(
            structured_output=evidence, session_id="s-1", is_error=True, errors=["boom"], api_error_status=529
        )
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(result.evidence)
        self.assertIn("529", result.error_detail)

    def test_is_error_false_procede_normalmente(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(
            structured_output=evidence, session_id="s-1", is_error=False
        )
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")


class PruebaMapeoDeExcepciones(ClaudeAdapterTestCase):
    def _invoke_with_exc(self, exc):
        fake_client_factory = lambda **kw: _FakeClient(raise_exc=exc, **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter()
            return adapter.invoke(self._request())

    def test_cli_not_found_es_unavailable(self):
        result = self._invoke_with_exc(self._fake_sdk.CLINotFoundError("no cli"))
        self.assertEqual(result.outcome, "unavailable")
        self.assertIsNone(result.evidence)

    def test_cli_connection_error_es_unavailable(self):
        result = self._invoke_with_exc(self._fake_sdk.CLIConnectionError("conn refused"))
        self.assertEqual(result.outcome, "unavailable")

    def test_cli_json_decode_error_es_invalid_output(self):
        result = self._invoke_with_exc(self._fake_sdk.CLIJSONDecodeError("bad json"))
        self.assertEqual(result.outcome, "invalid_output")

    def test_result_error_generico_es_failed(self):
        result = self._invoke_with_exc(
            self._fake_sdk.ResultError("boom", data={"terminal_reason": "max_turns"})
        )
        self.assertEqual(result.outcome, "failed")

    def test_result_error_con_cualquier_terminal_reason_real_es_failed_nunca_timeout(self):
        """Los valores reales confirmados de terminal_reason ("completed",
        "max_turns", "aborted_streaming", "aborted_tools") nunca
        representan un timeout -- ResultError siempre mapea a "failed";
        un timeout real se captura de forma independiente vía
        asyncio.wait_for()."""
        for reason in ("max_turns", "aborted_streaming", "aborted_tools", None):
            result = self._invoke_with_exc(
                self._fake_sdk.ResultError("boom", data={"terminal_reason": reason})
            )
            self.assertEqual(result.outcome, "failed", msg=repr(reason))

    def test_result_error_incluye_terminal_reason_y_api_error_status_en_error_detail(self):
        result = self._invoke_with_exc(
            self._fake_sdk.ResultError(
                "boom", data={"terminal_reason": "aborted_tools", "api_error_status": 529, "subtype": "error_during_execution"}
            )
        )
        self.assertIn("aborted_tools", result.error_detail)
        self.assertIn("529", result.error_detail)

    def test_process_error_generico_es_failed(self):
        result = self._invoke_with_exc(self._fake_sdk.ProcessError("crashed", exit_code=1))
        self.assertEqual(result.outcome, "failed")

    def test_excepcion_no_reconocida_nunca_propaga_es_failed(self):
        result = self._invoke_with_exc(ValueError("totally unrelated bug"))
        self.assertEqual(result.outcome, "failed")
        self.assertIn("unexpected error", result.error_detail)

    def test_ningun_outcome_de_provider_lanza_excepcion_fuera_de_invoke(self):
        for exc in (
            self._fake_sdk.CLINotFoundError("x"),
            self._fake_sdk.CLIConnectionError("x"),
            self._fake_sdk.CLIJSONDecodeError("x"),
            self._fake_sdk.ResultError("x"),
            self._fake_sdk.ProcessError("x"),
            RuntimeError("x"),
        ):
            try:
                self._invoke_with_exc(exc)
            except Exception as e:  # pragma: no cover - fail loudly if this ever happens
                self.fail(f"invoke() let {type(exc).__name__} propagate as {type(e).__name__}")


class PruebaTimeoutAdapter(ClaudeAdapterTestCase):
    def test_wait_for_timeout_produce_outcome_timeout(self):
        async def _hang(*a, **k):
            await asyncio.sleep(10)

        adapter = self.ca.ClaudeAdapter(timeout_seconds=0.01)
        with mock.patch.object(adapter, "_run", side_effect=_hang):
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "timeout")


class PruebaPermisosPorRol(ClaudeAdapterTestCase):
    def test_emilio_recibe_bash_edit_write(self):
        adapter = self.ca.ClaudeAdapter()
        options = adapter._build_options(self._request(agent_role="emilio"))
        self.assertIn("Bash", options.allowed_tools)
        self.assertIn("Edit", options.allowed_tools)
        self.assertIn("Write", options.allowed_tools)

    def test_emma_nunca_recibe_bash_edit_write(self):
        adapter = self.ca.ClaudeAdapter()
        options = adapter._build_options(self._request(agent_role="emma"))
        self.assertNotIn("Bash", options.allowed_tools)
        self.assertNotIn("Edit", options.allowed_tools)
        self.assertNotIn("Write", options.allowed_tools)
        self.assertIn("Read", options.allowed_tools)

    def test_output_format_usa_el_schema_del_rol_correcto(self):
        adapter = self.ca.ClaudeAdapter()
        options_emilio = adapter._build_options(self._request(agent_role="emilio"))
        options_emma = adapter._build_options(self._request(agent_role="emma"))
        self.assertIn("builder_evidence_entry", json.dumps(options_emilio.output_format))
        self.assertIn("reviewer_evidence_entry", json.dumps(options_emma.output_format))

    def test_schema_proyectado_excluye_identidad_de_infraestructura(self):
        infrastructure = {
            "invocation_id", "provider", "provider_session_id",
            "provider_conversation_id",
        }
        for role, entry in (("emilio", "builder_evidence_entry"),
                            ("emma", "reviewer_evidence_entry")):
            with self.subTest(role=role):
                schema = self.ca._load_evidence_schema(role)
                definition = schema["definitions"][entry]
                self.assertTrue(infrastructure.isdisjoint(definition["properties"]))
                self.assertFalse(definition["additionalProperties"])


class PruebaSinReintentoAutonomoNiSesionCompartida(ClaudeAdapterTestCase):
    def test_max_retries_cero_y_timeout_explicito_en_env(self):
        adapter = self.ca.ClaudeAdapter(timeout_seconds=42.0)
        options = adapter._build_options(self._request())
        self.assertEqual(options.env["CLAUDE_CODE_MAX_RETRIES"], "0")
        self.assertEqual(options.env["API_TIMEOUT_MS"], "42000")

    def test_cada_invoke_construye_un_cliente_nuevo(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        constructed = []

        def factory(**kw):
            client = _FakeClient(messages=[self._fake_sdk.ResultMessage(structured_output=evidence)], **kw)
            constructed.append(client)
            return client

        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=factory):
            adapter = self.ca.ClaudeAdapter()
            adapter.invoke(self._request())
            adapter.invoke(self._request())

        self.assertEqual(len(constructed), 2)
        self.assertIsNot(constructed[0], constructed[1])

    def test_adapter_no_retiene_estado_de_cliente_entre_llamadas(self):
        adapter = self.ca.ClaudeAdapter()
        self.assertFalse(hasattr(adapter, "_client"))
        self.assertFalse(hasattr(adapter, "client"))


class PruebaDependenciaDeclarada(unittest.TestCase):
    def test_claude_sdk_tiene_el_pin_runtime_validado(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
        self.assertIn("claude-agent-sdk==0.2.141\n", requirements)


if __name__ == "__main__":
    unittest.main()
