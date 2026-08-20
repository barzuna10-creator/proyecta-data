"""Pruebas para orchestrator/adapters/claude_adapter.py (Incremento #16
follow-up -- autenticación de Claude acotada vía apiKeyHelper/Keychain).

Ninguna prueba hace una llamada real a Claude, a la red, o a un
subprocess. El paquete real `claude_agent_sdk` no está instalado en este
entorno -- cada prueba instala un módulo simulado (stand-in) en
sys.modules ANTES de importar el adapter. Ningún fixture de este archivo
toca Keychain, `~/.claude/`, o cualquier ruta real fuera de un directorio
temporal -- el "apiKeyHelper" de prueba es un script desechable que
imprime el literal `fake-test-key-never-real`, nunca una clave real ni
nada con forma de clave real.

**Fidelidad del simulado, verificada contra el paquete real
`claude-agent-sdk==0.2.141` instalado en un venv desechable**: el ciclo
anterior construyó `ResultError` con un constructor
`__init__(self, message="", terminal_reason=None, api_error_status=None,
subtype=None)` que **no existe en el SDK real** -- el constructor real es
`ResultError(message, data=None, exit_code=None)`, y
`.terminal_reason`/`.subtype`/`.api_error_status`/`.errors`/`.result`/
`.session_id` son atributos derivados internamente de `data` (un dict), no
parámetros de palabra clave directos. El simulado aquí replica esa forma
real."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import stat
import sys
import tempfile
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
        # Corrective cycle (Increment #16, closing Emma's P2 finding):
        # the real claude-agent-sdk==0.2.141 ClaudeAgentOptions is a
        # strict @dataclass with exactly these 48 field names (confirmed
        # by direct inspection of the installed package in a disposable
        # venv, via dataclasses.fields()) -- an unknown kwarg raises
        # TypeError there. This fake now does the same, so a future
        # field-name regression (e.g. a typo in `settings`/`setting_sources`)
        # fails a test instead of passing silently.
        _REAL_FIELDS = frozenset(
            {
                "tools", "allowed_tools", "system_prompt", "mcp_servers",
                "strict_mcp_config", "permission_mode", "continue_conversation",
                "resume", "session_id", "max_turns", "max_budget_usd",
                "disallowed_tools", "model", "fallback_model", "betas",
                "permission_prompt_tool_name", "cwd", "cli_path", "settings",
                "add_dirs", "env", "extra_args", "max_buffer_size",
                "debug_stderr", "stderr", "can_use_tool", "hooks", "user",
                "include_partial_messages", "include_hook_events",
                "forward_subagent_text", "fork_session", "resume_session_at",
                "resume_drops_turn", "agents", "setting_sources", "skills",
                "sandbox", "plugins", "max_thinking_tokens", "thinking",
                "effort", "output_format", "enable_file_checkpointing",
                "session_store", "session_store_flush", "load_timeout_ms",
                "task_budget",
            }
        )

        def __init__(self, **kwargs):
            unknown = set(kwargs) - self._REAL_FIELDS
            if unknown:
                raise TypeError(
                    f"ClaudeAgentOptions() got unexpected keyword argument(s) "
                    f"not present on the real claude-agent-sdk==0.2.141 "
                    f"dataclass: {sorted(unknown)!r}"
                )
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

        # A genuinely valid, disposable settings file + fake, non-secret
        # "apiKeyHelper" script -- never a real key, never a real path.
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmpdir.name)
        self._helper_path = tmp_path / "fake-api-key-helper.sh"
        self._helper_path.write_text("#!/bin/sh\necho fake-test-key-never-real\n")
        self._helper_path.chmod(self._helper_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self._valid_settings_path = tmp_path / "claude-settings.json"
        self._valid_settings_path.write_text(json.dumps({"apiKeyHelper": str(self._helper_path)}))

    def tearDown(self):
        self._tmpdir.cleanup()
        sys.modules.pop("claude_agent_sdk", None)
        sys.modules.pop("orchestrator.adapters.claude_adapter", None)

    def _adapter(self, **kwargs):
        kwargs.setdefault("claude_settings_path", self._valid_settings_path)
        return self.ca.ClaudeAdapter(**kwargs)

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


class PruebaConfiguracionDeCredenciales(ClaudeAdapterTestCase):
    """Incremento #16 follow-up -- cierra la brecha ANTHROPIC_API_KEY:
    la autenticación ahora pasa por un archivo de configuración dedicado
    y aislado (ClaudeAgentOptions(settings=..., setting_sources=[])) cuyo
    apiKeyHelper resuelve la clave real en tiempo de invocación del CLI --
    este adapter nunca lee, ejecuta, ni revela la credencial."""

    def test_claude_settings_path_es_obligatorio(self):
        with self.assertRaises(TypeError):
            self.ca.ClaudeAdapter()  # sin claude_settings_path

    def test_archivo_de_settings_ausente_falla_cerrado(self):
        missing = Path(self._tmpdir.name) / "no-existe.json"
        adapter = self.ca.ClaudeAdapter(claude_settings_path=missing)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_archivo_de_settings_json_malformado_falla_cerrado(self):
        bad = Path(self._tmpdir.name) / "bad.json"
        bad.write_text("{not valid json")
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_archivo_de_settings_no_es_objeto_json_falla_cerrado(self):
        bad = Path(self._tmpdir.name) / "array.json"
        bad.write_text(json.dumps(["not", "an", "object"]))
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_sin_api_key_helper_falla_cerrado(self):
        bad = Path(self._tmpdir.name) / "no-helper.json"
        bad.write_text(json.dumps({"otraCosa": "x"}))
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_api_key_helper_vacio_falla_cerrado(self):
        bad = Path(self._tmpdir.name) / "empty-helper.json"
        bad.write_text(json.dumps({"apiKeyHelper": "   "}))
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_api_key_helper_apunta_a_ruta_inexistente_falla_cerrado(self):
        bad = Path(self._tmpdir.name) / "dangling-helper.json"
        bad.write_text(json.dumps({"apiKeyHelper": str(Path(self._tmpdir.name) / "no-existe.sh")}))
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_api_key_helper_no_ejecutable_falla_cerrado(self):
        non_exec_helper = Path(self._tmpdir.name) / "not-executable.sh"
        non_exec_helper.write_text("#!/bin/sh\necho fake-test-key-never-real\n")
        non_exec_helper.chmod(0o600)  # deliberately not executable
        bad = Path(self._tmpdir.name) / "non-exec-helper.json"
        bad.write_text(json.dumps({"apiKeyHelper": str(non_exec_helper)}))
        adapter = self.ca.ClaudeAdapter(claude_settings_path=bad)
        with self.assertRaises(self.ca.ClaudeAdapterError):
            adapter.invoke(self._request())

    def test_settings_valido_procede_a_la_invocacion(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(structured_output=evidence, session_id="s-1")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self._adapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")

    def test_options_lleva_settings_y_setting_sources_vacio(self):
        adapter = self._adapter()
        options = adapter._build_options(self._request())
        self.assertEqual(options.settings, str(self._valid_settings_path))
        self.assertEqual(options.setting_sources, [])

    def test_verificacion_nunca_ejecuta_ni_lee_el_helper(self):
        """La verificación estructural solo comprueba existencia/permiso
        de ejecución del script -- nunca lo invoca ni captura su salida.
        Reemplazamos el script por uno que, si se ejecutara, escribiría
        un centinela a un archivo; confirmamos que el centinela nunca
        aparece tras invoke()."""
        sentinel_path = Path(self._tmpdir.name) / "sentinel.txt"
        helper = Path(self._tmpdir.name) / "tattletale-helper.sh"
        helper.write_text(f"#!/bin/sh\ntouch {sentinel_path}\necho fake-test-key-never-real\n")
        helper.chmod(0o700)
        settings = Path(self._tmpdir.name) / "tattletale-settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": str(helper)}))

        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(structured_output=evidence, session_id="s-1")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self.ca.ClaudeAdapter(claude_settings_path=settings)
            adapter.invoke(self._request())
        self.assertFalse(sentinel_path.exists(), "adapter executed the apiKeyHelper script itself")

    def test_ninguna_credencial_aparece_en_la_fuente_del_modulo(self):
        """El adapter nunca contiene un literal con forma de clave real
        (sk-ant-...) en su propio código fuente. (Nota: el nombre de
        variable `ANTHROPIC_API_KEY` puede aparecer legítimamente en el
        docstring del módulo como contexto histórico -- eso no es una
        credencial, es documentación de por qué cambió el diseño.)"""
        import inspect

        source = inspect.getsource(self.ca)
        self.assertNotIn("sk-ant-", source)


class PruebaSinCredencialAmbiental(ClaudeAdapterTestCase):
    """Incremento #16, ciclo correctivo -- cierra el hallazgo P1 de Emma:
    ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN ambientales en el entorno del
    proceso adaptador superan en precedencia a apiKeyHelper (documentado
    oficialmente), así que su sola presencia debe rechazarse antes de
    cualquier invocación -- sin leer, imprimir, registrar, copiar, ni
    modificar el valor, solo su presencia."""

    def _patched_env(self, **env_vars):
        return mock.patch.dict(os.environ, env_vars, clear=False)

    def test_anthropic_api_key_presente_rechaza_antes_de_invocar(self):
        with self._patched_env(ANTHROPIC_API_KEY="placeholder-never-real"):
            adapter = self._adapter()
            with self.assertRaises(self.ca.ClaudeAdapterError) as ctx:
                adapter.invoke(self._request())
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertNotIn("placeholder-never-real", str(ctx.exception))

    def test_anthropic_auth_token_presente_rechaza_antes_de_invocar(self):
        with self._patched_env(ANTHROPIC_AUTH_TOKEN="placeholder-never-real"):
            adapter = self._adapter()
            with self.assertRaises(self.ca.ClaudeAdapterError) as ctx:
                adapter.invoke(self._request())
        self.assertIn("ANTHROPIC_AUTH_TOKEN", str(ctx.exception))
        self.assertNotIn("placeholder-never-real", str(ctx.exception))

    def test_ambas_presentes_rechaza(self):
        with self._patched_env(
            ANTHROPIC_API_KEY="placeholder-never-real-1",
            ANTHROPIC_AUTH_TOKEN="placeholder-never-real-2",
        ):
            adapter = self._adapter()
            with self.assertRaises(self.ca.ClaudeAdapterError) as ctx:
                adapter.invoke(self._request())
        message = str(ctx.exception)
        self.assertIn("ANTHROPIC_API_KEY", message)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", message)
        self.assertNotIn("placeholder-never-real-1", message)
        self.assertNotIn("placeholder-never-real-2", message)

    def test_ninguna_presente_con_settings_validos_sigue_invocando(self):
        """El camino de invocación existente (settings/helper válidos, sin
        credenciales ambientales) sigue siendo alcanzable -- este chequeo
        no bloquea el caso legítimo."""
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", os.environ)
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(structured_output=evidence, session_id="s-1")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self._adapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")

    def test_rechazo_no_ejecuta_ni_lee_el_helper_ni_llama_al_cliente(self):
        """El rechazo ocurre antes de _verify_claude_settings_file() y
        antes de construir cualquier ClaudeSDKClient -- el helper (que
        dejaría un centinela si se ejecutara) nunca se toca, y el cliente
        simulado (que fallaría la prueba si se construyera) tampoco."""
        sentinel_path = Path(self._tmpdir.name) / "sentinel-ambient.txt"
        helper = Path(self._tmpdir.name) / "tattletale-ambient-helper.sh"
        helper.write_text(f"#!/bin/sh\ntouch {sentinel_path}\necho fake-test-key-never-real\n")
        helper.chmod(0o700)
        settings = Path(self._tmpdir.name) / "tattletale-ambient-settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": str(helper)}))

        def _must_not_be_called(**kw):
            self.fail("ClaudeSDKClient was constructed despite an ambient credential")

        with self._patched_env(ANTHROPIC_API_KEY="placeholder-never-real"):
            with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=_must_not_be_called):
                adapter = self.ca.ClaudeAdapter(claude_settings_path=settings)
                with self.assertRaises(self.ca.ClaudeAdapterError):
                    adapter.invoke(self._request())
        self.assertFalse(sentinel_path.exists(), "adapter executed the apiKeyHelper script despite an ambient credential")

    def test_ningun_valor_de_credencial_aparece_en_la_excepcion_ni_en_repr(self):
        """El mensaje de la excepción, y repr()/str() de la propia
        excepción, nunca contienen el valor real -- solo el nombre de la
        variable."""
        secret_shaped_value = "sk-ant-api03-totally-fake-should-never-leak-anywhere"
        with self._patched_env(ANTHROPIC_API_KEY=secret_shaped_value):
            adapter = self._adapter()
            with self.assertRaises(self.ca.ClaudeAdapterError) as ctx:
                adapter.invoke(self._request())
        self.assertNotIn(secret_shaped_value, str(ctx.exception))
        self.assertNotIn(secret_shaped_value, repr(ctx.exception))


def _find_keys(node, keys):
    """Test-local, independent tree walker (deliberately not reusing the
    adapter's own `_strip_provider_unsupported_keywords`/`_refs_in`, so
    this assertion doesn't become circular) -- returns every occurrence of
    any key in `keys` found anywhere in `node`."""
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                hits.append(k)
            hits.extend(_find_keys(v, keys))
    elif isinstance(node, list):
        for item in node:
            hits.extend(_find_keys(item, keys))
    return hits


def _find_refs(node):
    """Test-local, independent `$ref` collector (see `_find_keys` above --
    deliberately not reusing the adapter's own `_refs_in`)."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/definitions/"):
                found.add(v.rsplit("/", 1)[-1])
            else:
                found |= _find_refs(v)
    elif isinstance(node, list):
        for item in node:
            found |= _find_refs(item)
    return found


class PruebaProyeccionDeEsquemaParaProveedor(ClaudeAdapterTestCase):
    """Incremento #16, ciclos correctivos del HTTP 400 -- cierra los dos
    hallazgos: `_load_evidence_schema()` ahora devuelve una proyección
    apta para el proveedor (sin palabras clave no soportadas por
    Anthropic, sin definiciones no alcanzables, con la propia entrada
    solicitada promovida a la raíz -- `type: object` directo, no detrás
    de un `$ref` -- confirmado contra el error real de la API:
    `tools.N.custom.input_schema.type: Field required`), sin tocar jamás
    el archivo canónico `mission_record.schema.json` ni debilitar la
    validación canónica en `orchestrator/validator.py`."""

    _UNSUPPORTED_KEYWORDS = {
        "minLength", "maxLength", "minimum", "maximum", "multipleOf",
        "if", "then", "else",
    }

    def _canonical_schema_path(self):
        return (
            Path(self.ca.__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"
        )

    def _canonical_definitions(self):
        with open(self._canonical_schema_path(), encoding="utf-8") as f:
            return json.load(f)["definitions"]

    def _expected_reachable(self, entry_name):
        """Independent reachability computation (not calling the
        adapter's own `_reachable_definitions`), so this test doesn't
        just restate the production logic back at itself."""
        definitions = self._canonical_definitions()
        seen = set()
        frontier = {entry_name}
        while frontier:
            name = frontier.pop()
            if name in seen or name not in definitions:
                continue
            seen.add(name)
            frontier |= _find_refs(definitions[name])
        return seen

    def test_esquema_de_reviewer_no_contiene_palabras_clave_no_soportadas(self):
        schema = self.ca._load_evidence_schema("emma")
        hits = _find_keys(schema, self._UNSUPPORTED_KEYWORDS)
        self.assertEqual(hits, [])

    def test_esquema_de_builder_no_contiene_palabras_clave_no_soportadas(self):
        schema = self.ca._load_evidence_schema("emilio")
        hits = _find_keys(schema, self._UNSUPPORTED_KEYWORDS)
        self.assertEqual(hits, [])

    def test_definiciones_de_reviewer_son_exactamente_su_cierre_alcanzable_menos_la_propia_entrada(self):
        schema = self.ca._load_evidence_schema("emma")
        expected = self._expected_reachable("reviewer_evidence_entry") - {"reviewer_evidence_entry"}
        self.assertEqual(set(schema["definitions"].keys()), expected)
        # confirma que el cierre real (sin la propia entrada) es un
        # subconjunto propio del total -- si esto alguna vez deja de
        # cumplirse, la prueba de "no se envía todo" perdería sentido y
        # debe revisarse.
        self.assertLess(len(expected), len(self._canonical_definitions()))

    def test_esquema_de_builder_tiene_las_mismas_propiedades(self):
        schema = self.ca._load_evidence_schema("emilio")
        expected = self._expected_reachable("builder_evidence_entry") - {"builder_evidence_entry"}
        self.assertEqual(set(schema["definitions"].keys()), expected)
        self.assertLess(len(expected), len(self._canonical_definitions()))
        self.assertEqual(_find_keys(schema, self._UNSUPPORTED_KEYWORDS), [])

    def test_todo_ref_interno_resuelve_dentro_de_la_proyeccion(self):
        """Los `$ref` ahora pueden aparecer en cualquier parte del
        esquema (no solo dentro de `definitions`), porque la propia
        entrada solicitada -- con sus `properties` propias, que sí
        contienen `$ref`s -- ahora vive en la raíz, no detrás de un
        `$ref` de nivel superior."""
        for role in ("emma", "emilio"):
            schema = self.ca._load_evidence_schema(role)
            referenced = _find_refs(schema)
            dangling = referenced - set(schema["definitions"].keys())
            self.assertEqual(dangling, set(), msg=f"role={role}")

    def test_raiz_de_reviewer_es_type_object_no_ref(self):
        """Incremento #16, ciclo correctivo final del HTTP 400 -- prueba
        de regresión dirigida al fallo real confirmado:
        `API Error: 400 tools.10.custom.input_schema.type: Field required`.
        La raíz del esquema debe declarar `type` directamente (satisfaciendo
        el requisito mínimo estructural de `input_schema.type`), no dejarlo
        detrás de un `$ref` de nivel superior."""
        schema = self.ca._load_evidence_schema("emma")
        self.assertEqual(schema.get("type"), "object")
        self.assertNotIn("$ref", schema)
        self.assertIn("properties", schema)
        self.assertIn("required", schema)
        self.assertIn("additionalProperties", schema)

    def test_raiz_de_builder_es_type_object_no_ref(self):
        schema = self.ca._load_evidence_schema("emilio")
        self.assertEqual(schema.get("type"), "object")
        self.assertNotIn("$ref", schema)
        self.assertIn("properties", schema)
        self.assertIn("required", schema)
        self.assertIn("additionalProperties", schema)

    def test_la_propia_entrada_no_aparece_dentro_de_definitions(self):
        reviewer_schema = self.ca._load_evidence_schema("emma")
        self.assertNotIn("reviewer_evidence_entry", reviewer_schema["definitions"])
        builder_schema = self.ca._load_evidence_schema("emilio")
        self.assertNotIn("builder_evidence_entry", builder_schema["definitions"])

    def test_archivo_canonico_es_identico_antes_y_despues_de_la_proyeccion(self):
        path = self._canonical_schema_path()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.ca._load_evidence_schema("emma")
        self.ca._load_evidence_schema("emilio")
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_keywords_soportados_se_preservan(self):
        """`$ref`, `pattern`, `enum`, `anyOf`, `allOf`, y
        `additionalProperties` no deben eliminarse -- solo las palabras
        clave explícitamente no soportadas."""
        schema = self.ca._load_evidence_schema("emma")
        supported_present = _find_keys(
            schema, {"$ref", "pattern", "enum", "anyOf", "allOf", "additionalProperties"}
        )
        self.assertTrue(supported_present, "expected at least one supported keyword to survive projection")
        self.assertIn("pattern", supported_present)
        self.assertIn("anyOf", supported_present)
        self.assertIn("additionalProperties", supported_present)

    def test_output_format_de_la_invocacion_real_usa_la_proyeccion(self):
        """`_build_options()` -- el único llamador real de
        `_load_evidence_schema()` -- expone la proyección filtrada, no el
        esquema canónico completo, en el `output_format` que de verdad se
        envía al SDK, incluyendo la raíz `type: object` (no un `$ref`)."""
        adapter = self._adapter()
        options = adapter._build_options(self._request(agent_role="emma"))
        schema = options.output_format["schema"]
        hits = _find_keys(schema, self._UNSUPPORTED_KEYWORDS)
        self.assertEqual(hits, [])
        self.assertEqual(schema.get("type"), "object")
        self.assertNotIn("$ref", schema)
        self.assertEqual(
            set(schema["definitions"].keys()),
            self._expected_reachable("reviewer_evidence_entry") - {"reviewer_evidence_entry"},
        )


class PruebaInvocacionExitosa(ClaudeAdapterTestCase):
    def test_completed_con_evidencia_y_session_id(self):
        evidence = {"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}
        result_msg = self._fake_sdk.ResultMessage(structured_output=evidence, session_id="claude-session-abc")
        fake_client_factory = lambda **kw: _FakeClient(messages=[result_msg], **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self._adapter()
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
            adapter = self._adapter()
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
            adapter = self._adapter()
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
            adapter = self._adapter()
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "completed")


class PruebaMapeoDeExcepciones(ClaudeAdapterTestCase):
    def _invoke_with_exc(self, exc):
        fake_client_factory = lambda **kw: _FakeClient(raise_exc=exc, **kw)
        with mock.patch.object(self.ca, "ClaudeSDKClient", side_effect=fake_client_factory):
            adapter = self._adapter()
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

        adapter = self._adapter(timeout_seconds=0.01)
        with mock.patch.object(adapter, "_run", side_effect=_hang):
            result = adapter.invoke(self._request())
        self.assertEqual(result.outcome, "timeout")


class PruebaPermisosPorRol(ClaudeAdapterTestCase):
    def test_emilio_recibe_bash_edit_write(self):
        adapter = self._adapter()
        options = adapter._build_options(self._request(agent_role="emilio"))
        self.assertIn("Bash", options.allowed_tools)
        self.assertIn("Edit", options.allowed_tools)
        self.assertIn("Write", options.allowed_tools)

    def test_emma_nunca_recibe_bash_edit_write(self):
        adapter = self._adapter()
        options = adapter._build_options(self._request(agent_role="emma"))
        self.assertNotIn("Bash", options.allowed_tools)
        self.assertNotIn("Edit", options.allowed_tools)
        self.assertNotIn("Write", options.allowed_tools)
        self.assertIn("Read", options.allowed_tools)

    def test_output_format_usa_el_schema_del_rol_correcto(self):
        """Incremento #16, ciclo correctivo final -- desde que la propia
        entrada se promovió a la raíz del esquema (sin `$ref` ni nombre
        de definición visible), la distinción por rol ya no puede
        verificarse buscando el literal "builder_evidence_entry"/
        "reviewer_evidence_entry" en el JSON serializado -- se verifica
        en cambio con campos `required` que son exclusivos de cada
        entrada real (ver orchestrator/schemas/mission_record.schema.json)."""
        adapter = self._adapter()
        options_emilio = adapter._build_options(self._request(agent_role="emilio"))
        options_emma = adapter._build_options(self._request(agent_role="emma"))
        emilio_json = json.dumps(options_emilio.output_format)
        emma_json = json.dumps(options_emma.output_format)
        self.assertIn("handoff_document_ref", emilio_json)
        self.assertNotIn("handoff_document_ref", emma_json)
        self.assertIn("verdict", emma_json)
        self.assertIn("blocked_reason", emma_json)
        self.assertNotIn("blocked_reason", emilio_json)


class PruebaSinReintentoAutonomoNiSesionCompartida(ClaudeAdapterTestCase):
    def test_max_retries_cero_y_timeout_explicito_en_env(self):
        adapter = self._adapter(timeout_seconds=42.0)
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
            adapter = self._adapter()
            adapter.invoke(self._request())
            adapter.invoke(self._request())

        self.assertEqual(len(constructed), 2)
        self.assertIsNot(constructed[0], constructed[1])

    def test_adapter_no_retiene_estado_de_cliente_entre_llamadas(self):
        adapter = self._adapter()
        self.assertFalse(hasattr(adapter, "_client"))
        self.assertFalse(hasattr(adapter, "client"))


if __name__ == "__main__":
    unittest.main()
