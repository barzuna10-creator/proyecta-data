"""Pruebas para orchestrator/adapters/claude_cli_adapter.py -- adaptador
zero-cost basado en el CLI de Claude Code y su sesión claude.ai (Pro) ya
autenticada.

Ninguna prueba invoca el binario real `claude`: cada prueba escribe un
script Python ejecutable que se hace pasar por el CLI."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from orchestrator.adapters.claude_cli_adapter import (
    ClaudeCliAdapter,
    ClaudeCliAdapterError,
    _EMMA_VERDICT_GRAMMAR,
    _REVIEWER_VERDICT_SEVERITY_MISMATCH,
    _reviewer_verdict_severity_mismatch,
)
from orchestrator.agent_invocation import AgentInvocationRequest


_FAKE_CLAUDE_TEMPLATE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json
    import sys

    AUTH_STATUS = {auth_status}
    MODE = {mode!r}

    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps(AUTH_STATUS))
        sys.exit(0)

    stdin_data = sys.stdin.read()  # prompt, consumed but not required by the fake
    evidence = {{
        "attempt": 0, "invoked_at": "2026-08-25T21:05:00Z",
        "artifact_identity_confirmed_at_start": {{"mode": "commit", "commit_sha": "a" * 40,
            "patch_path": None, "patch_sha256": None, "patch_byte_size": None}},
        "artifact_identity_confirmed_before_conclusion": {{"mode": "commit", "commit_sha": "a" * 40,
            "patch_path": None, "patch_sha256": None, "patch_byte_size": None}},
        "rechecked_commands": [], "findings": [], "verdict": "PASS", "blocked_reason": None,
    }}
    if MODE == "success_bare":
        print(json.dumps(evidence))
        sys.exit(0)
    if MODE == "success_result_dict":
        print(json.dumps({{"type": "result", "subtype": "success", "result": evidence}}))
        sys.exit(0)
    if MODE == "success_with_session_id":
        print(json.dumps({{"type": "result", "subtype": "success",
                            "session_id": "genuine-claude-session-99", "result": evidence}}))
        sys.exit(0)
    if MODE == "success_result_string":
        print(json.dumps({{"type": "result", "subtype": "success", "result": json.dumps(evidence)}}))
        sys.exit(0)
    if MODE == "attempt_6_verdict_mismatch":
        evidence["findings"] = [
            {{"id": "F4", "severity": "P2"}},
            {{"id": "F5", "severity": "P2"}},
            {{"id": "F6", "severity": "P2"}},
            {{"id": "F7", "severity": "P3"}},
            {{"id": "F8", "severity": "P3"}},
            {{"id": "F9", "severity": "P3"}},
        ]
        evidence["verdict"] = "PASS_WITH_NON_BLOCKING_FINDINGS"
        print(json.dumps(evidence))
        sys.exit(0)
    if MODE == "attempt_7_verdict_mismatch":
        evidence["attempt"] = 1
        evidence["findings"] = [
            {{"id": "F1", "severity": "P2"}},
            {{"id": "F2", "severity": "P3"}},
            {{"id": "F3", "severity": "P3"}},
        ]
        evidence["verdict"] = "PASS_WITH_NON_BLOCKING_FINDINGS"
        print(json.dumps({{"type": "result", "subtype": "success", "result": evidence}}))
        sys.exit(0)
    if MODE == "malformed_json":
        print("{{not valid json")
        sys.exit(0)
    if MODE == "unrecognized_envelope":
        print(json.dumps({{"type": "result", "subtype": "success", "something_else": 1}}))
        sys.exit(0)
    if MODE == "nonzero_exit":
        sys.stderr.write("fake claude failure\\n")
        sys.exit(1)
    if MODE == "hang":
        import time
        time.sleep(30)
        sys.exit(0)
    sys.exit(1)
''')


def _write_fake_claude(tmp_dir: Path, *, auth_status: dict, mode: str) -> str:
    script_path = tmp_dir / "fake_claude.py"
    script_path.write_text(
        _FAKE_CLAUDE_TEMPLATE.format(auth_status=repr(auth_status), mode=mode),
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


def _request(worktree: Path, agent_role="emma", attempt=0) -> AgentInvocationRequest:
    return AgentInvocationRequest(
        invocation_id="33333333-3333-4333-8333-333333333333",
        mission_id="44444444-4444-4444-8444-444444444444",
        agent_role=agent_role, attempt=attempt,
        task={"mission_definition": {"outcome": "x"}, "repository": {"worktree_path": str(worktree)}},
        requested_at="2026-08-25T20:00:00Z", requested_fresh_context=True,
    )


_SUBSCRIPTION_AUTH = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}
_API_KEY_AUTH = {"loggedIn": True, "authMethod": "apiKey"}
_LOGGED_OUT_AUTH = {"loggedIn": False, "authMethod": None}


class ClaudeCliAdapterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name).resolve()
        self._worktree = self._tmp / "worktree"
        self._worktree.mkdir()
        # Corrective cycle #5: emilio-role invocations now compute a real
        # `git diff` after the fake CLI exits, so the worktree must be a
        # genuine git repository with a starting commit -- exactly what a
        # real Mission Record's worktree already is.
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(cmd, cwd=str(self._worktree), check=True, capture_output=True)
        (self._worktree / "README.md").write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self._worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "initial commit"],
            cwd=str(self._worktree), check=True, capture_output=True,
        )

    def tearDown(self):
        self._tmpdir.cleanup()


class PruebaAutenticacionSubscripcion(ClaudeCliAdapterTestCase):
    def test_credenciales_y_routing_ambientales_no_llegan_a_ningun_subprocess(self):
        script_path = self._tmp / "fake_claude_environment.py"
        capture_path = self._tmp / "captured_environment.json"
        prohibited = (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "AWS_PROFILE", "BEDROCK_ENDPOINT_URL", "GOOGLE_APPLICATION_CREDENTIALS",
            "VERTEX_REGION",
        )
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, os, sys
            prohibited = {prohibited!r}
            captured = {{"phase": "auth" if sys.argv[1:3] == ["auth", "status"] else "dispatch",
                         "leaked": sorted(name for name in prohibited if name in os.environ),
                         "profile_preserved": os.environ.get("CLAUDE_CONFIG_DIR") == "trusted-profile"}}
            with open({str(capture_path)!r}, "a", encoding="utf-8") as f:
                f.write(json.dumps(captured) + "\\n")
            if sys.argv[1:3] == ["auth", "status"]:
                print(json.dumps({_SUBSCRIPTION_AUTH!r}))
                sys.exit(0)
            sys.stdin.read()
            print(json.dumps({{"attempt": 0, "invoked_at": "2026-08-25T21:05:00Z",
                "artifact_identity_confirmed_at_start": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "artifact_identity_confirmed_before_conclusion": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "rechecked_commands": [], "findings": [], "verdict": "PASS", "blocked_reason": None}}))
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        ambient = {name: "must-not-leak" for name in prohibited}
        ambient["CLAUDE_CONFIG_DIR"] = "trusted-profile"
        with unittest.mock.patch.dict(os.environ, ambient):
            result = ClaudeCliAdapter(cli_path=str(script_path)).invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        captures = [json.loads(line) for line in capture_path.read_text().splitlines()]
        self.assertEqual([item["phase"] for item in captures], ["auth", "dispatch"])
        self.assertTrue(all(item["leaked"] == [] for item in captures))
        self.assertTrue(all(item["profile_preserved"] for item in captures))


class PruebaContratoVeredictoSeveridad(unittest.TestCase):
    @staticmethod
    def _evidence(verdict, severities):
        return {
            "verdict": verdict,
            "findings": [
                {"id": f"F{index}", "severity": severity}
                for index, severity in enumerate(severities, 1)
            ],
        }

    def test_payload_real_intento_6_es_rechazado_sin_corregir_veredicto(self):
        evidence = self._evidence(
            "PASS_WITH_NON_BLOCKING_FINDINGS",
            ["P2", "P2", "P2", "P3", "P3", "P3"],
        )
        original = json.loads(json.dumps(evidence))
        self.assertTrue(_reviewer_verdict_severity_mismatch(evidence))
        self.assertEqual(evidence, original)
        self.assertEqual(
            _REVIEWER_VERDICT_SEVERITY_MISMATCH,
            "REVIEWER_VERDICT_SEVERITY_MISMATCH",
        )

    def test_combinaciones_canonicas_validas(self):
        valid = (
            self._evidence("PASS", []),
            self._evidence("PASS_WITH_NON_BLOCKING_FINDINGS", ["P3"]),
            self._evidence("PASS_WITH_NON_BLOCKING_FINDINGS", ["P3", "P3"]),
            self._evidence("CHANGES_REQUIRED", ["P1"]),
            self._evidence("CHANGES_REQUIRED", ["P2"]),
            self._evidence("CHANGES_REQUIRED", ["P1", "P2", "P3"]),
            self._evidence("BLOCKED", ["P0"]),
            self._evidence("BLOCKED", ["P0", "P1", "P2", "P3"]),
        )
        for evidence in valid:
            with self.subTest(verdict=evidence["verdict"]):
                self.assertFalse(_reviewer_verdict_severity_mismatch(evidence))

    def test_toda_combinacion_no_canonica_falla_cerrada(self):
        invalid = (
            self._evidence("BLOCKED", []),
            self._evidence("BLOCKED", ["P2"]),
            self._evidence("PASS_WITH_NON_BLOCKING_FINDINGS", []),
            self._evidence("PASS_WITH_NON_BLOCKING_FINDINGS", ["P2", "P3"]),
            self._evidence("CHANGES_REQUIRED", []),
            self._evidence("CHANGES_REQUIRED", ["P3"]),
            self._evidence("PASS", ["P3"]),
            self._evidence("PASS", ["P0"]),
        )
        for evidence in invalid:
            with self.subTest(verdict=evidence["verdict"], findings=evidence["findings"]):
                self.assertTrue(_reviewer_verdict_severity_mismatch(evidence))


class PruebaContratoVeredictoSeveridadEnAdapter(ClaudeCliAdapterTestCase):
    def test_intento_6_devuelve_invalid_output_antes_de_chugel(self):
        cli = _write_fake_claude(
            self._tmp,
            auth_status=_SUBSCRIPTION_AUTH,
            mode="attempt_6_verdict_mismatch",
        )
        result = ClaudeCliAdapter(cli_path=cli).invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertEqual(result.error_detail, _REVIEWER_VERDICT_SEVERITY_MISMATCH)
        self.assertIsNone(result.evidence)
        self.assertEqual(result.diagnostic, {"reason_code": "INVALID_OUTPUT_VERDICT_SEVERITY_MISMATCH"})

    def test_intento_7_recibe_gramatica_y_sigue_fallando_cerrado_si_la_contradice(self):
        script_path = self._tmp / "fake_claude_attempt_7.py"
        capture_path = self._tmp / "captured_attempt_7_prompt.txt"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            if sys.argv[1:3] == ["auth", "status"]:
                print(json.dumps({_SUBSCRIPTION_AUTH!r}))
                sys.exit(0)
            prompt = sys.stdin.read()
            with open({str(capture_path)!r}, "w", encoding="utf-8") as f:
                f.write(prompt)
            evidence = {{
                "attempt": 1, "invoked_at": "2026-08-29T01:02:11Z",
                "artifact_identity_confirmed_at_start": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "artifact_identity_confirmed_before_conclusion": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "rechecked_commands": [],
                "findings": [
                    {{"id":"F1","severity":"P2"}},
                    {{"id":"F2","severity":"P3"}},
                    {{"id":"F3","severity":"P3"}},
                ],
                "verdict": "PASS_WITH_NON_BLOCKING_FINDINGS", "blocked_reason": None,
            }}
            print(json.dumps({{"type":"result","subtype":"success","result":evidence}}))
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        result = ClaudeCliAdapter(cli_path=str(script_path)).invoke(
            _request(self._worktree, attempt=1)
        )
        prompt = json.loads(capture_path.read_text(encoding="utf-8"))
        grammar = prompt["_zentra_reviewer_verdict_grammar"]
        for rule in (
            "severity P0, verdict MUST be BLOCKED",
            "severity P1 or P2", "verdict MUST be CHANGES_REQUIRED",
            "every finding has severity P3", "PASS is allowed only when findings is empty",
        ):
            self.assertIn(rule, grammar)
        self.assertEqual(result.outcome, "invalid_output")
        self.assertEqual(result.error_detail, _REVIEWER_VERDICT_SEVERITY_MISMATCH)
        self.assertIsNone(result.evidence)

    def test_login_claude_ai_confirmado_permite_invocar(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "claude")
        self.assertIsNotNone(result.evidence)

    def test_login_api_key_es_rechazado_antes_de_invocar(self):
        cli = _write_fake_claude(self._tmp, auth_status=_API_KEY_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        with self.assertRaises(ClaudeCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_logged_out_es_rechazado(self):
        cli = _write_fake_claude(self._tmp, auth_status=_LOGGED_OUT_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        with self.assertRaises(ClaudeCliAdapterError):
            adapter.invoke(_request(self._worktree))


class PruebaExtraccionDeResultadoEstructurado(ClaudeCliAdapterTestCase):
    def test_objeto_top_level_directo(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.evidence["verdict"], "PASS")

    def test_result_como_dict(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_result_dict")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.evidence["verdict"], "PASS")

    def test_result_como_string_json_anidado(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_result_string")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.evidence["verdict"], "PASS")

    def test_sobre_no_reconocido_es_invalid_output(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="unrecognized_envelope")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertEqual(result.diagnostic["reason_code"], "INVALID_OUTPUT_UNRECOGNIZED_RESULT_SHAPE")
        self.assertIsInstance(result.diagnostic["output_byte_length"], int)


class PruebaFalloCerrado(ClaudeCliAdapterTestCase):
    def test_json_malformado_es_invalid_output(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="malformed_json")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertEqual(result.diagnostic["reason_code"], "INVALID_OUTPUT_UNRECOGNIZED_RESULT_SHAPE")

    def test_exit_no_cero_es_failed(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="nonzero_exit")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "failed")
        self.assertIn("fake claude failure", result.error_detail)
        self.assertEqual(result.diagnostic["reason_code"], "FAILED_NONZERO_EXIT")
        self.assertIsInstance(result.diagnostic["exit_code"], int)
        self.assertIsInstance(result.diagnostic["stderr_byte_length"], int)
        self.assertNotIn("fake claude failure", json.dumps(result.diagnostic))

    def test_timeout_es_timeout(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="hang")
        adapter = ClaudeCliAdapter(cli_path=cli, timeout_seconds=1)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "timeout")
        self.assertEqual(result.diagnostic["reason_code"], "TIMEOUT_EXCEEDED")
        self.assertEqual(result.diagnostic["timeout_seconds"], 1.0)


class PruebaConfinamientoDeWorktreeYHerramientas(ClaudeCliAdapterTestCase):
    def test_worktree_relativo_falla_antes_de_invocar(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        request = AgentInvocationRequest(
            invocation_id="x", mission_id="y", agent_role="emma", attempt=0,
            task={"repository": {"worktree_path": "relative/path"}},
            requested_at="2026-08-25T20:00:00Z", requested_fresh_context=True,
        )
        with self.assertRaises(ClaudeCliAdapterError):
            adapter.invoke(request)

    def test_emma_recibe_solo_herramientas_de_lectura(self):
        script_path = self._tmp / "fake_claude_capture.py"
        capture_path = self._tmp / "captured_argv.json"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["auth", "status"]:
                print(json.dumps({_SUBSCRIPTION_AUTH!r}))
                sys.exit(0)
            with open({str(capture_path)!r}, "w") as f:
                json.dump(args, f)
            sys.stdin.read()
            print(json.dumps({{"attempt": 0, "invoked_at": "x",
                "artifact_identity_confirmed_at_start": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "artifact_identity_confirmed_before_conclusion": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "rechecked_commands": [], "findings": [], "verdict": "PASS", "blocked_reason": None}}))
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        adapter = ClaudeCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree, agent_role="emma"))
        self.assertEqual(result.outcome, "completed")
        captured = json.loads(capture_path.read_text())
        idx = captured.index("--allowedTools")
        tools = captured[idx + 1].split(",")
        self.assertEqual(set(tools), {"Read", "Glob", "Grep"})
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Bash", tools)
        self.assertIn("--add-dir", captured)
        idx_dir = captured.index("--add-dir")
        self.assertEqual(captured[idx_dir + 1], str(self._worktree))


class PruebaDescubrimientoDeCli(unittest.TestCase):
    def test_ruta_explicita_inexistente_falla(self):
        with self.assertRaises(ClaudeCliAdapterError):
            ClaudeCliAdapter(cli_path="/no/existe/claude")

    def test_timeout_invalido_falla(self):
        with self.assertRaises(ClaudeCliAdapterError):
            ClaudeCliAdapter(cli_path=__file__, timeout_seconds=0)
        with self.assertRaises(ClaudeCliAdapterError):
            ClaudeCliAdapter(cli_path=__file__, timeout_seconds=True)


# --- Emma's P1 corrective cycle: genuine provider identity only ----------

class PruebaIdentidadDeProveedorGenuina(ClaudeCliAdapterTestCase):
    """Emma's finding: `provider_conversation_id` must never be
    synthesized from `invocation_id` (or any other orchestrator-owned
    value) -- it must be `None` unless the CLI's own `--output-format
    json` envelope genuinely reports a session id."""

    def test_session_id_genuino_presente_es_capturado(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_with_session_id")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider_conversation_id, "genuine-claude-session-99")

    def test_session_id_ausente_permanece_none(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertIsNone(result.provider_conversation_id)

    def test_invocation_id_nunca_se_usa_como_sustituto(self):
        request = _request(self._worktree)

        cli_present = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_with_session_id")
        result_present = ClaudeCliAdapter(cli_path=cli_present).invoke(request)
        self.assertNotEqual(result_present.provider_conversation_id, request.invocation_id)

        cli_absent = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        result_absent = ClaudeCliAdapter(cli_path=cli_absent).invoke(request)
        self.assertIsNone(result_absent.provider_conversation_id)
        self.assertNotEqual(result_absent.provider_conversation_id, request.invocation_id)


class PruebaExcepcionesNoSePropagan(ClaudeCliAdapterTestCase):
    def test_excepcion_arbitraria_durante_el_despacho_no_se_propaga(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        with unittest.mock.patch(
            "orchestrator.adapters.claude_cli_adapter._load_evidence_schema",
            side_effect=ValueError("synthetic injected failure -- proves the catch-all works"),
        ):
            result = adapter.invoke(_request(self._worktree))
        self.assertNotEqual(result.outcome, "completed")
        self.assertIsNone(result.evidence)
        self.assertIn("synthetic injected failure", result.error_detail)

    def test_pre_invocacion_sigue_lanzando_no_se_traga(self):
        cli = _write_fake_claude(self._tmp, auth_status=_API_KEY_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        with self.assertRaises(ClaudeCliAdapterError):
            adapter.invoke(_request(self._worktree))


class PruebaSinPresupuestoDeGastoNiRespaldoDeApiKey(ClaudeCliAdapterTestCase):
    """Corrective cycle #3: the real, installed Claude CLI rejects
    `--max-budget-usd 0` outright (it requires a positive value), so the
    prior command line failed before the model ever received the task.
    This class proves the fix does not merely swap `0` for some other
    number -- it removes the flag entirely -- and that the properties it
    was never actually providing (subscription-only auth, no API-key
    fallback) remain intact through some other mechanism."""

    def _captured_argv(self, mode="success_bare"):
        script_path = self._tmp / "fake_claude_capture_budget.py"
        capture_path = self._tmp / "captured_argv_budget.json"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["auth", "status"]:
                print(json.dumps({_SUBSCRIPTION_AUTH!r}))
                sys.exit(0)
            with open({str(capture_path)!r}, "w") as f:
                json.dump(args, f)
            sys.stdin.read()
            print(json.dumps({{"attempt": 0, "invoked_at": "x",
                "artifact_identity_confirmed_at_start": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "artifact_identity_confirmed_before_conclusion": {{"mode":"commit","commit_sha":"a"*40,
                    "patch_path":None,"patch_sha256":None,"patch_byte_size":None}},
                "rechecked_commands": [], "findings": [], "verdict": "PASS", "blocked_reason": None}}))
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        adapter = ClaudeCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree, agent_role="emma"))
        self.assertEqual(result.outcome, "completed")
        return json.loads(capture_path.read_text())

    def test_comando_no_incluye_max_budget_usd(self):
        captured = self._captured_argv()
        self.assertNotIn("--max-budget-usd", captured)

    def test_ningun_valor_de_presupuesto_positivo_es_introducido(self):
        """Not just the flag name -- no numeric budget value of any kind
        (the old "0", or any replacement like "1"/"5") appears anywhere
        in the constructed command line."""
        captured = self._captured_argv()
        budget_like = {"0", "1", "5", "10", "0.01", "0.0"}
        self.assertFalse(set(captured) & budget_like)

    def test_auth_claude_ai_sigue_siendo_obligatoria(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")

    def test_respaldo_de_api_key_sigue_siendo_imposible(self):
        cli = _write_fake_claude(self._tmp, auth_status=_API_KEY_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        with self.assertRaises(ClaudeCliAdapterError):
            adapter.invoke(_request(self._worktree))


# --- Corrective cycle #5: trustworthy artifact handling when Claude ------
# --- acts as Emilio's fallback provider ----------------------------------

class PruebaEsquemaDeEmilioExcluyeArtifact(unittest.TestCase):
    """Unit-level check directly on _load_evidence_schema(), independent
    of any subprocess."""

    def test_emilio_no_puede_reportar_artifact(self):
        from orchestrator.adapters.claude_cli_adapter import _load_evidence_schema
        schema = _load_evidence_schema("emilio")
        self.assertNotIn("artifact", schema["properties"])
        self.assertNotIn("artifact", schema["required"])

    def test_emma_no_se_ve_afectada(self):
        from orchestrator.adapters.claude_cli_adapter import _load_evidence_schema
        schema = _load_evidence_schema("emma")
        self.assertNotIn("artifact", schema["properties"])  # never had one to begin with


def _emilio_builder_evidence_without_artifact() -> dict:
    return {
        "attempt": 0, "invoked_at": "2026-08-25T21:05:00Z",
        "changed_files": [{"path": "pilot_file.py", "reason": "fake"}],
        "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {"no_existing_work_altered": True, "no_main_change": True,
            "no_remote_action": True, "no_production_access": True,
            "no_protected_path_change": True, "complete_diff_inspected": True},
        "handoff_document_ref": None, "conclusion": {"text": "x", "label": "FACT"},
    }


class PruebaArtefactoDePatchGenuinoParaEmilioFallback(ClaudeCliAdapterTestCase):
    """Corrective cycle #5: reproduces both real pilot failures when
    routing sent Emilio's task to Claude (the router's failover policy) --
    once a structurally invalid model-reported artifact, once a
    schema-valid but semantically false one (`mode: "commit"` echoing the
    pre-existing base commit, not real new work). These tests prove the
    fix: the model can no longer report `artifact` at all, and this
    adapter computes a genuine one itself via `git diff`."""

    def _write_fake_claude_no_artifact(self, *, write_real_file: bool, evidence_extra: dict | None = None) -> str:
        evidence = _emilio_builder_evidence_without_artifact()
        if evidence_extra:
            evidence.update(evidence_extra)
        script_path = self._tmp / "fake_claude_no_artifact.py"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["auth", "status"]:
                print(json.dumps({_SUBSCRIPTION_AUTH!r}))
                sys.exit(0)
            sys.stdin.read()
            if {write_real_file!r}:
                with open("pilot_file.py", "w") as pf:
                    pf.write("# genuine change written by the fake model\\n")
            print(json.dumps({evidence!r}))
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return str(script_path)

    def test_patch_real_es_computado_y_es_estructuralmente_valido(self):
        import hashlib
        cli = self._write_fake_claude_no_artifact(write_real_file=True)
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree, agent_role="emilio"))
        self.assertEqual(result.outcome, "completed")
        artifact = result.evidence["artifact"]
        self.assertEqual(artifact["mode"], "patch")
        self.assertIsNone(artifact["commit_sha"])
        patch_bytes = Path(artifact["patch_path"]).read_bytes()
        self.assertTrue(len(patch_bytes) > 0)
        self.assertEqual(artifact["patch_byte_size"], len(patch_bytes))
        self.assertEqual(artifact["patch_sha256"], hashlib.sha256(patch_bytes).hexdigest())
        self.assertIn(b"pilot_file.py", patch_bytes)
        head = subprocess.run(
            ["git", "-C", str(self._worktree), "log", "--oneline"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        self.assertEqual(len(head), 1, "no new commit should have been created")

    def test_ninguna_modificacion_real_es_invalid_output(self):
        cli = self._write_fake_claude_no_artifact(write_real_file=False)
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree, agent_role="emilio"))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIn("no uncommitted change", result.error_detail)
        self.assertEqual(result.diagnostic, {
            "reason_code": "INVALID_OUTPUT_ARTIFACT_COMPUTATION_FAILED",
            "artifact_failure_reason": "NO_UNCOMMITTED_CHANGE",
        })

    def test_commit_sha_falso_o_preexistente_reportado_por_el_modelo_es_ignorado(self):
        """Reproduces the real pilot's second, more dangerous failure
        mode directly: even if a future/misbehaving CLI ignored the
        schema and echoed back the pre-existing base commit sha as if it
        were the artifact identity, this adapter must never use it --
        the model cannot even include `artifact` in its own JSON output
        schema, but this proves the adapter's own logic doesn't trust a
        stray one either."""
        base_sha_result = subprocess.run(
            ["git", "-C", str(self._worktree), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        base_sha = base_sha_result.stdout.strip()
        stray_artifact = {
            "mode": "commit", "commit_sha": base_sha,
            "patch_path": None, "patch_sha256": None, "patch_byte_size": None,
        }
        cli = self._write_fake_claude_no_artifact(
            write_real_file=True, evidence_extra={"artifact": stray_artifact},
        )
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree, agent_role="emilio"))
        self.assertEqual(result.outcome, "completed")
        artifact = result.evidence["artifact"]
        self.assertNotEqual(artifact, stray_artifact)
        self.assertEqual(artifact["mode"], "patch")
        self.assertNotEqual(artifact["commit_sha"], base_sha)
        self.assertIsNone(artifact["commit_sha"])
        self.assertIsNotNone(artifact["patch_sha256"])

    def test_git_diff_no_altera_el_arbol_de_trabajo(self):
        cli = self._write_fake_claude_no_artifact(write_real_file=True)
        adapter = ClaudeCliAdapter(cli_path=cli)
        adapter.invoke(_request(self._worktree, agent_role="emilio"))
        status = subprocess.run(
            ["git", "-C", str(self._worktree), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(status.strip(), "?? pilot_file.py")


class PruebaEmmaNoComputaArtefactoDePatch(ClaudeCliAdapterTestCase):
    """Emma (read-only reviewer role) never produces artifact identity --
    this fix must not attempt git-diff computation for her role at all."""

    def test_emma_no_dispara_computo_de_patch(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        adapter = ClaudeCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree, agent_role="emma"))
        self.assertEqual(result.outcome, "completed")
        self.assertNotIn("artifact", result.evidence)


if __name__ == "__main__":
    unittest.main()
