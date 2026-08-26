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
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    fake.ClaudeSDKError = ClaudeSDKError
    fake.CLINotFoundError = CLINotFoundError
    fake.CLIConnectionError = CLIConnectionError
    fake.ProcessError = ProcessError
    fake.ResultError = ResultError
    fake.CLIJSONDecodeError = CLIJSONDecodeError
    fake.ResultMessage = ResultMessage
    fake.ClaudeAgentOptions = ClaudeAgentOptions
    fake.HookMatcher = HookMatcher
    fake.ClaudeSDKClient = None  # replaced per-test via mock.patch
    sys.modules["claude_agent_sdk"] = fake
    return fake


class ClaudeAdapterTestCase(unittest.TestCase):
    def setUp(self):
        self._fake_sdk = _install_fake_claude_agent_sdk()
        sys.modules.pop("orchestrator.adapters.claude_adapter", None)
        import orchestrator.adapters.claude_adapter as ca

        importlib.reload(ca)
        self.ca = ca
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.worktree = root / "worktree"
        self.worktree.mkdir()
        self.worktree = self.worktree.resolve()
        self._api_key = "synthetic-claude-dedicated-key"
        self._env_patch = mock.patch.dict(
            os.environ, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, clear=True
        )
        self._env_patch.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    def tearDown(self):
        self._env_patch.stop()
        self._tmpdir.cleanup()
        sys.modules.pop("claude_agent_sdk", None)
        sys.modules.pop("orchestrator.adapters.claude_adapter", None)

    def _request(self, agent_role="emma", attempt=0, task=None):
        return self.ca.AgentInvocationRequest(
            invocation_id="inv-1",
            mission_id="mission-1",
            agent_role=agent_role,
            attempt=attempt,
            task=task or {"repository": {"worktree_path": str(self.worktree)}},
            requested_at="2026-08-19T12:00:00Z",
            requested_fresh_context=(agent_role == "emma"),
        )

    def _adapter(self, **kwargs):
        raise AssertionError("authenticated execution belongs to the OS-isolated worker tests")

    def _options(self, role="emma", task=None):
        with self.ca._isolated_claude_config_dir() as config_dir:
            return self.ca._build_worker_options(
                self._request(agent_role=role, task=task), config_dir,
                api_key=self._api_key, model=self.ca.DEFAULT_MODEL,
                timeout_seconds=self.ca.DEFAULT_TIMEOUT_SECONDS,
            )


class PruebaCredencialFaltante(ClaudeAdapterTestCase):
    """Corrective migration (stale-test disposition):
    `test_emilio_direct_key_permanece_rechazado_antes_del_cliente` was
    removed as `SAFE_TO_REMOVE` -- `ClaudeParentTombstoneTests.
    test_parent_object_new_has_no_authenticated_execution_method` (in
    tests/test_orchestrator_claude_worker_runtime.py) already proves
    something stronger: there is no `invoke` method at all to reach an
    SDK client through, for any role.
    `test_sdk_construction_boundary_observa_solo_worker_canonico` and
    `test_credencial_no_puede_entrar_en_request_output_o_error` were
    migrated to `ClaudeWorkerRuntimeTests` (same file above), mirroring
    the exact patterns already proven for Codex in
    tests/test_orchestrator_codex_worker_runtime.py."""

    def test_construccion_productiva_directa_esta_bloqueada(self):
        with self.assertRaises(self.ca.ClaudeAdapterError):
            self.ca.ClaudeAdapter(api_key=self._api_key)

    def test_construccion_directa_no_puede_heredar_ninguna_variable_no_aprobada(self):
        obj = object.__new__(self.ca.ClaudeAdapter)
        obj._api_key = self._api_key
        self.assertFalse(hasattr(obj, "invoke"))


class PruebaMapeoDeExcepciones(ClaudeAdapterTestCase):
    """Corrective migration (stale-test disposition): rewritten against
    `_map_exception_to_outcome()` directly -- a pure, standalone function
    imported once in setUp (`self.ca`), with no SDK/worker dependency.
    `claude_worker_runtime.py`'s own `except Exception as exc:` clause
    dispatches every real exception through this exact function, so
    testing it in isolation (plus the real worker's `mode="exception"`
    and `mode="is_error"` cases in `ClaudeWorkerRuntimeTests`, which prove
    the dispatch is actually wired up end to end) is strictly equivalent
    coverage to invoking a real (mocked) adapter per exception type."""

    def test_cli_not_found_es_unavailable(self):
        outcome = self.ca._map_exception_to_outcome(self._fake_sdk.CLINotFoundError("no cli"))
        self.assertEqual(outcome[0], "unavailable")

    def test_cli_connection_error_es_unavailable(self):
        outcome = self.ca._map_exception_to_outcome(self._fake_sdk.CLIConnectionError("conn refused"))
        self.assertEqual(outcome[0], "unavailable")

    def test_cli_json_decode_error_es_invalid_output(self):
        outcome = self.ca._map_exception_to_outcome(self._fake_sdk.CLIJSONDecodeError("bad json"))
        self.assertEqual(outcome[0], "invalid_output")

    def test_result_error_generico_es_failed(self):
        outcome = self.ca._map_exception_to_outcome(
            self._fake_sdk.ResultError("boom", data={"terminal_reason": "max_turns"})
        )
        self.assertEqual(outcome[0], "failed")

    def test_result_error_con_cualquier_terminal_reason_real_es_failed_nunca_timeout(self):
        """Los valores reales confirmados de terminal_reason ("completed",
        "max_turns", "aborted_streaming", "aborted_tools") nunca
        representan un timeout -- ResultError siempre mapea a "failed";
        un timeout real se captura de forma independiente vía
        asyncio.wait_for()."""
        for reason in ("max_turns", "aborted_streaming", "aborted_tools", None):
            outcome = self.ca._map_exception_to_outcome(
                self._fake_sdk.ResultError("boom", data={"terminal_reason": reason})
            )
            self.assertEqual(outcome[0], "failed", msg=repr(reason))

    def test_result_error_incluye_terminal_reason_y_api_error_status_en_error_detail(self):
        outcome = self.ca._map_exception_to_outcome(
            self._fake_sdk.ResultError(
                "boom", data={"terminal_reason": "aborted_tools", "api_error_status": 529, "subtype": "error_during_execution"}
            )
        )
        self.assertIn("aborted_tools", outcome[1])
        self.assertIn("529", outcome[1])

    def test_process_error_generico_es_failed(self):
        outcome = self.ca._map_exception_to_outcome(self._fake_sdk.ProcessError("crashed", exit_code=1))
        self.assertEqual(outcome[0], "failed")

    def test_excepcion_no_reconocida_nunca_propaga_es_failed(self):
        outcome = self.ca._map_exception_to_outcome(ValueError("totally unrelated bug"))
        self.assertEqual(outcome[0], "failed")
        self.assertIn("unexpected error", outcome[1])

    def test_ningun_outcome_de_provider_lanza_excepcion_fuera_de_invoke(self):
        """The pure function itself never raises for any of this exact
        exception set -- it always returns a (outcome, detail) tuple.
        Structural proof (matching the original intent) that invoke()'s
        `except Exception as exc: ... = helpers._map_exception_to_outcome(exc)`
        clause can never itself raise while mapping any of these."""
        for exc in (
            self._fake_sdk.CLINotFoundError("x"),
            self._fake_sdk.CLIConnectionError("x"),
            self._fake_sdk.CLIJSONDecodeError("x"),
            self._fake_sdk.ResultError("x"),
            self._fake_sdk.ProcessError("x"),
            RuntimeError("x"),
        ):
            try:
                outcome = self.ca._map_exception_to_outcome(exc)
            except Exception as e:  # pragma: no cover - fail loudly if this ever happens
                self.fail(f"_map_exception_to_outcome() let {type(exc).__name__} propagate as {type(e).__name__}")
            self.assertIsInstance(outcome, tuple)
            self.assertEqual(len(outcome), 2)

    def test_wait_for_timeout_produce_outcome_timeout(self):
        """Formerly the standalone PruebaTimeoutAdapter class -- folded in
        here because asyncio.TimeoutError is just one more branch of the
        same pure dispatch function."""
        outcome = self.ca._map_exception_to_outcome(asyncio.TimeoutError())
        self.assertEqual(outcome[0], "timeout")


class PruebaPermisosPorRol(ClaudeAdapterTestCase):
    def test_emilio_recibe_bash_edit_write(self):
        options = self._options("emilio")
        self.assertIn("Bash", options.allowed_tools)
        self.assertIn("Edit", options.allowed_tools)
        self.assertIn("Write", options.allowed_tools)

    def test_emma_nunca_recibe_bash_edit_write(self):
        options = self._options("emma")
        self.assertNotIn("Bash", options.allowed_tools)
        self.assertNotIn("Edit", options.allowed_tools)
        self.assertNotIn("Write", options.allowed_tools)
        self.assertIn("Read", options.allowed_tools)

    def test_output_format_usa_el_schema_del_rol_correcto(self):
        options_emilio = self._options("emilio")
        options_emma = self._options("emma")
        emilio = options_emilio.output_format["schema"]
        emma = options_emma.output_format["schema"]
        self.assertIn("handoff_document_ref", emilio["properties"])
        self.assertNotIn("verdict", emilio["properties"])
        self.assertIn("verdict", emma["properties"])
        self.assertIn("findings", emma["properties"])

    def test_schema_proyectado_excluye_identidad_de_infraestructura(self):
        infrastructure = {
            "invocation_id", "provider", "provider_session_id",
            "provider_conversation_id",
        }
        for role in ("emilio", "emma"):
            with self.subTest(role=role):
                schema = self.ca._load_evidence_schema(role)
                self.assertTrue(infrastructure.isdisjoint(schema["properties"]))
                self.assertFalse(schema["additionalProperties"])
                serialized = json.dumps(schema)
                for field in infrastructure:
                    self.assertNotIn(field, serialized)

    def test_proyeccion_anthropic_tiene_raiz_inline_refs_resueltos_y_keywords_compatibles(self):
        unsupported = {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "if", "then", "else"}

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key, value
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)

        for role in ("emilio", "emma"):
            schema = self.ca._load_evidence_schema(role)
            self.assertEqual(schema["type"], "object")
            self.assertNotIn("$ref", schema)
            keys = {key for key, _ in walk(schema)}
            self.assertTrue(unsupported.isdisjoint(keys))
            refs = {
                value.rsplit("/", 1)[-1]
                for key, value in walk(schema)
                if key == "$ref" and isinstance(value, str) and value.startswith("#/definitions/")
            }
            self.assertTrue(refs.issubset(schema["definitions"]))


class PruebaConfinamientoClaude(ClaudeAdapterTestCase):
    def _options(self, role="emilio", task=None):
        return super()._options(role, task)

    def test_cwd_es_el_path_canonico_resuelto(self):
        options = self._options()
        self.assertEqual(options.cwd, self.worktree.resolve())

    def test_worktree_relativo_inexistente_archivo_y_symlink_son_rechazados(self):
        regular_file = self.worktree.parent / "not-a-directory"
        regular_file.write_text("x")
        link = self.worktree.parent / "worktree-link"
        link.symlink_to(self.worktree, target_is_directory=True)
        cases = [
            "relative/worktree",
            str(self.worktree.parent / "missing"),
            str(regular_file.resolve()),
            str(link),
            None,
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                task = {"repository": {"worktree_path": raw}}
                with self.assertRaises(self.ca.ClaudeAdapterError):
                    self._options(task=task)

    def test_worktree_invalido_se_rechaza_antes_de_construir_cliente(self):
        """Corrective migration (stale-test disposition): rewritten
        against `_resolve_authorized_worktree()` directly -- a pure
        function with no SDK/worker dependency. `_build_worker_options()`
        (the only place `claude_worker_runtime.py` ever constructs a real
        `ClaudeAgentOptions`, strictly before any `ClaudeSDKClient` can
        exist) calls this function first and propagates its exception
        unchanged, so proving it raises here is structurally equivalent
        to proving no client is ever reached -- there is no `options`
        object for a client construction to depend on until this
        succeeds."""
        with self.assertRaises(self.ca.ClaudeAdapterError):
            self.ca._resolve_authorized_worktree("relative/worktree")

    def test_tools_y_allowed_tools_son_exactos_por_rol(self):
        expected = {
            "emilio": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
            "emma": ["Read", "Glob", "Grep"],
        }
        for role, tools in expected.items():
            with self.subTest(role=role):
                options = self._options(role)
                self.assertEqual(options.tools, tools)
                self.assertEqual(options.allowed_tools, tools)
        self.assertTrue({"Bash", "Edit", "Write"}.isdisjoint(self._options("emma").tools))

    def test_superficie_auxiliar_esta_cerrada(self):
        options = self._options()
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.mcp_servers, {})
        self.assertEqual(options.skills, [])
        self.assertEqual(options.plugins, [])
        self.assertEqual(options.add_dirs, [])
        self.assertEqual(options.permission_mode, "dontAsk")
        self.assertEqual(options.setting_sources, [])

    def test_sandbox_fail_closed_no_bypass_sin_red_ni_paths_extra(self):
        sandbox = self._options().sandbox
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertTrue(sandbox["autoAllowBashIfSandboxed"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["excludedCommands"], [])
        self.assertEqual(sandbox["filesystem"]["denyRead"], [self.worktree.anchor])
        self.assertEqual(sandbox["filesystem"]["allowRead"], [str(self.worktree)])
        self.assertEqual(sandbox["filesystem"]["allowWrite"], [])
        self.assertEqual(sandbox["filesystem"]["denyWrite"], [])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])
        self.assertEqual(sandbox["network"]["allowUnixSockets"], [])
        self.assertFalse(sandbox["network"]["allowAllUnixSockets"])
        self.assertFalse(sandbox["network"]["allowLocalBinding"])

    def test_settings_entregados_solo_contienen_politica_infra(self):
        payload = json.loads(self._options().settings)
        self.assertEqual(set(payload), {"permissions"})
        self.assertNotIn(self._api_key, json.dumps(payload))
        self.assertNotIn("sandbox", payload)  # SDK merges the infrastructure-owned options.sandbox.

    def test_credencial_solo_en_env_del_cli_y_home_personal_no_se_usa(self):
        options = self._options("emma")
        self.assertEqual(options.env["ANTHROPIC_API_KEY"], self._api_key)
        self.assertEqual(options.env["HOME"], options.env["CLAUDE_CONFIG_DIR"])
        self.assertNotEqual(options.env["HOME"], os.path.expanduser("~"))
        self.assertEqual(
            set(options.env),
            {
                "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "HOME",
                "TMPDIR", "TMP", "TEMP",
                "CLAUDE_CODE_MAX_RETRIES", "API_TIMEOUT_MS",
            },
        )
        self.assertEqual(options.env["TMPDIR"], options.env["CLAUDE_CONFIG_DIR"])
        self.assertEqual(options.env["TMP"], options.env["CLAUDE_CONFIG_DIR"])
        self.assertEqual(options.env["TEMP"], options.env["CLAUDE_CONFIG_DIR"])

    def test_guard_nativo_niega_absoluto_traversal_y_symlink_escape(self):
        outside = self.worktree.parent / "outside.txt"
        outside.write_text("outside")
        link = self.worktree / "outside-link"
        link.symlink_to(outside)
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        cases = [
            ("Read", {"file_path": str(outside)}),
            ("Read", {"file_path": "../outside.txt"}),
            ("Read", {"file_path": "outside-link"}),
            ("Write", {"file_path": str(outside), "content": "x"}),
            ("Edit", {"file_path": "../outside.txt"}),
            ("Glob", {"path": "../"}),
            ("Grep", {"path": str(self.worktree)}),
        ]
        for tool, tool_input in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                result = asyncio.run(guard({"tool_name": tool, "tool_input": tool_input}, "id", None))
                self.assertEqual(
                    result["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_guard_nativo_permite_paths_relativos_internos_incluso_destino_nuevo(self):
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        for tool, field in (("Read", "file_path"), ("Edit", "file_path"),
                            ("Write", "file_path"), ("Grep", "path")):
            result = asyncio.run(
                guard({"tool_name": tool, "tool_input": {field: "subdir/new.txt"}}, "id", None)
            )
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_glob_pattern_reproduce_los_tres_bypasses_confirmados_por_emma(self):
        outside_dir = self.worktree.parent / "glob-outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "sentinel.txt"
        outside_file.write_text("outside")
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        patterns = [
            str(outside_file),
            "../glob-outside/*",
            str(outside_dir / "*"),
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                result = asyncio.run(
                    guard({"tool_name": "Glob", "tool_input": {"pattern": pattern}}, "id", None)
                )
                self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_glob_valida_path_y_pattern_independientemente(self):
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        cases = [
            ({"pattern": "**/*.py"}, "allow"),
            ({"path": "subdir", "pattern": "*.py"}, "allow"),
            ({"path": "subdir", "pattern": "../outside-*"}, "deny"),
            ({"path": "../", "pattern": "*.py"}, "deny"),
            ({"path": "../", "pattern": "../outside-*"}, "deny"),
            ({"path": "subdir"}, "deny"),
            ({"pattern": None}, "deny"),
            ({"pattern": True}, "deny"),
        ]
        for tool_input, expected in cases:
            with self.subTest(tool_input=tool_input):
                result = asyncio.run(
                    guard({"tool_name": "Glob", "tool_input": tool_input}, "id", None)
                )
                self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], expected)

    def test_glob_pattern_niega_escape_por_symlink(self):
        outside_dir = self.worktree.parent / "glob-symlink-outside"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("outside")
        (self.worktree / "glob-outside-link").symlink_to(outside_dir, target_is_directory=True)
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        result = asyncio.run(
            guard(
                {
                    "tool_name": "Glob",
                    "tool_input": {"pattern": "glob-outside-link/*.txt"},
                },
                "id",
                None,
            )
        )
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_guard_nativo_niega_input_malformado_de_herramienta_con_path_requerido(self):
        guard = self._options().hooks["PreToolUse"][0].hooks[0]
        for tool in ("Read", "Edit", "Write"):
            for tool_input in ({}, {"file_path": None}, {"file_path": True}):
                result = asyncio.run(guard({"tool_name": tool, "tool_input": tool_input}, "id", None))
                self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")


class PruebaSinReintentoAutonomoNiSesionCompartida(ClaudeAdapterTestCase):
    """Corrective migration (stale-test disposition):
    `test_adapter_no_retiene_estado_de_cliente_entre_llamadas` was removed
    as `SAFE_TO_REMOVE` -- structurally guaranteed more strongly by the
    one-shot-subprocess worker architecture itself
    (`RealProviderWorkerBoundaryTests.test_claude_real_child_crosses_os_boundary`:
    a fresh OS process every invocation cannot retain any attribute-level
    state between calls at all). `test_cada_invoke_construye_un_cliente_nuevo`
    was migrated to `ClaudeWorkerRuntimeTests.
    test_cada_invoke_real_construye_un_worker_nuevo` (two sequential real
    child invocations, distinct worker PIDs)."""

    def test_max_retries_cero_y_timeout_explicito_en_env(self):
        """Rewritten against `_build_worker_options()` directly -- a pure
        function with no SDK/worker dependency."""
        with self.ca._isolated_claude_config_dir() as config_dir:
            options = self.ca._build_worker_options(
                self._request(), config_dir, api_key=self._api_key,
                model=self.ca.DEFAULT_MODEL, timeout_seconds=42.0,
            )
        self.assertEqual(options.env["CLAUDE_CODE_MAX_RETRIES"], "0")
        self.assertEqual(options.env["API_TIMEOUT_MS"], "42000")


class PruebaDependenciaDeclarada(unittest.TestCase):
    def test_claude_sdk_tiene_el_pin_runtime_validado(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
        self.assertIn("claude-agent-sdk==0.2.141\n", requirements)


if __name__ == "__main__":
    unittest.main()
