"""Pruebas para orchestrator/adapters/codex_cli_adapter.py -- adaptador
zero-cost basado en el CLI de Codex y su sesión ChatGPT ya autenticada.

Ninguna prueba invoca el binario real `codex`: cada prueba escribe un
script Python ejecutable (shebang + chmod +x) que se hace pasar por el
CLI, y se lo pasa al adaptador vía `cli_path=`. Esto ejerce el mismo
límite de proceso real (subprocess.Popen, stdin/stdout/stderr reales,
exit codes reales) sin depender de que `codex` esté instalado ni de
ninguna sesión ChatGPT real."""

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

from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter, CodexCliAdapterError
from orchestrator.agent_invocation import AgentInvocationRequest


_FAKE_CODEX_TEMPLATE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json
    import sys

    LOGIN_STATUS_STDOUT = {login_status_stdout!r}
    LOGIN_STATUS_STDERR = {login_status_stderr!r}
    LOGIN_RETURNCODE = {login_returncode!r}
    EXEC_MODE = {exec_mode!r}

    args = sys.argv[1:]
    if args and args[0] == "login" and args[1:2] == ["status"]:
        if LOGIN_STATUS_STDOUT:
            print(LOGIN_STATUS_STDOUT)
        if LOGIN_STATUS_STDERR:
            sys.stderr.write(LOGIN_STATUS_STDERR + "\\n")
        sys.exit(LOGIN_RETURNCODE)

    if args and args[0] == "exec":
        stdin_data = sys.stdin.read()  # prompt, consumed but not required by the fake
        out_path = None
        for i, a in enumerate(args):
            if a == "-o" and i + 1 < len(args):
                out_path = args[i + 1]
        if EXEC_MODE in ("success", "success_with_thread_id"):
            if EXEC_MODE == "success_with_thread_id":
                print(json.dumps({{"type": "thread.started", "thread_id": "genuine-codex-thread-42"}}))
            # Corrective cycle #4: a real file change, so the adapter's own
            # `git diff`-based artifact computation (run after this process
            # exits) has genuine content to capture -- not an empty patch.
            with open("pilot_file.py", "w") as pf:
                pf.write("# fake codex change\\n")
            evidence = {{
                "attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                "changed_files": [{{"path": "pilot_file.py", "reason": "fake"}}],
                "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
                "rollback_notes": "none",
                "safety_confirmation": {{
                    "no_existing_work_altered": True, "no_main_change": True,
                    "no_remote_action": True, "no_production_access": True,
                    "no_protected_path_change": True, "complete_diff_inspected": True,
                }},
                "handoff_document_ref": None,
                "conclusion": {{"text": "fake completed", "label": "FACT"}},
            }}
            with open(out_path, "w") as f:
                json.dump(evidence, f)
            sys.exit(0)
        if EXEC_MODE == "malformed_json":
            with open(out_path, "w") as f:
                f.write("{{not valid json")
            sys.exit(0)
        if EXEC_MODE == "no_output_file":
            sys.exit(0)
        if EXEC_MODE == "nonzero_exit":
            sys.stderr.write("fake codex exec failure\\n")
            sys.exit(1)
        if EXEC_MODE == "hang":
            import time
            time.sleep(30)
            sys.exit(0)
        sys.exit(1)

    sys.exit(1)
''')


def _write_fake_codex(tmp_dir: Path, *, exec_mode: str,
                       login_status: str = "", login_status_stdout: str | None = None,
                       login_status_stderr: str = "", login_returncode: int = 0) -> str:
    """`login_status` is a convenience alias for `login_status_stdout`
    (kept for the many pre-existing call sites that only ever needed a
    stdout-only fake, from before corrective cycle #2 added stderr/
    returncode control) -- if `login_status_stdout` is given explicitly,
    it wins."""
    stdout_text = login_status_stdout if login_status_stdout is not None else login_status
    script_path = tmp_dir / "fake_codex.py"
    script_path.write_text(
        _FAKE_CODEX_TEMPLATE.format(
            login_status_stdout=stdout_text, login_status_stderr=login_status_stderr,
            login_returncode=login_returncode, exec_mode=exec_mode,
        ),
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


def _request(worktree: Path, agent_role="emilio", attempt=0) -> AgentInvocationRequest:
    return AgentInvocationRequest(
        invocation_id="11111111-1111-4111-8111-111111111111",
        mission_id="22222222-2222-4222-8222-222222222222",
        agent_role=agent_role, attempt=attempt,
        task={"mission_definition": {"outcome": "x"}, "repository": {"worktree_path": str(worktree)}},
        requested_at="2026-08-25T20:00:00Z", requested_fresh_context=False,
    )


class CodexCliAdapterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        # macOS: /var/folders/... is itself a symlink to /private/var/folders/...
        # -- resolve once up front so every path built from self._tmp is
        # already canonical, matching what _validate_worktree_path() requires.
        self._tmp = Path(self._tmpdir.name).resolve()
        self._worktree = self._tmp / "worktree"
        self._worktree.mkdir()
        # Corrective cycle #4: emilio-role invocations now compute a real
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


class PruebaAutenticacionChatGPT(CodexCliAdapterTestCase):
    def test_login_chatgpt_confirmado_permite_invocar(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider, "codex")
        self.assertIsNotNone(result.evidence)

    def test_login_api_key_es_rechazado_antes_de_invocar(self):
        """El requisito central de $0 adicional: si el login activo es una
        API key (o cualquier cosa que no sea ChatGPT), el adaptador se
        niega antes de siquiera intentar `codex exec` -- nunca gasta."""
        cli = _write_fake_codex(
            self._tmp, login_status="Logged in using an API key - sk-proj-***XXXX", exec_mode="success"
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_login_desconocido_es_rechazado(self):
        cli = _write_fake_codex(self._tmp, login_status="Not logged in", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))


class PruebaLoginStatusEnAmbosStreams(CodexCliAdapterTestCase):
    """Corrective cycle #2 (real-pilot runtime discovery, before any
    provider task was executed): the real, installed Codex CLI writes
    `codex login status`'s confirmation to stderr, not stdout
    (returncode 0, stdout '', stderr 'Logged in using ChatGPT\\n'). These
    tests independently exercise both streams, plus every fail-closed
    boundary the corrective cycle's requirements list explicitly."""

    def test_chatgpt_en_stderr_es_aceptado(self):
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="", login_status_stderr="Logged in using ChatGPT", login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")

    def test_chatgpt_en_stdout_sigue_siendo_aceptado(self):
        """No debe perderse soporte para una versión del CLI que emita el
        estado por stdout en vez de stderr."""
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="Logged in using ChatGPT", login_status_stderr="", login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")

    def test_api_key_en_stderr_es_rechazado(self):
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="", login_status_stderr="Logged in using an API key - sk-proj-***XXXX",
            login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_ambos_streams_vacios_es_rechazado(self):
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="", login_status_stderr="", login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_comando_de_estado_con_codigo_no_cero_es_rechazado(self):
        """No debe aceptarse solo porque el texto esperado aparezca en
        algún stream -- un returncode != 0 debe fallar cerrado incluso si,
        por alguna razón adversarial, el texto esperado estuviera presente."""
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="Logged in using ChatGPT", login_status_stderr="", login_returncode=1,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_estado_desconocido_en_cualquier_stream_es_rechazado(self):
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="", login_status_stderr="something completely unexpected",
            login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))

    def test_returncode_cero_solo_no_basta_sin_el_texto_esperado(self):
        """Requisito explícito del ciclo correctivo: nunca aceptar
        autenticación únicamente porque returncode == 0 -- aquí el
        proceso sale limpio y con salida no vacía, pero el texto exacto
        esperado no aparece en ningún stream."""
        cli = _write_fake_codex(
            self._tmp, exec_mode="success",
            login_status_stdout="status: ok", login_status_stderr="ready", login_returncode=0,
        )
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))


class PruebaFalloCerrado(CodexCliAdapterTestCase):
    def test_json_malformado_es_invalid_output(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="malformed_json")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIsNone(result.evidence)
        # Structured Allow-Listed Diagnostics: reason_code identifies the
        # branch; json_decode_error_position/output_byte_length are safe
        # ints, never the raw (malformed) output text itself.
        self.assertEqual(result.diagnostic["reason_code"], "INVALID_OUTPUT_MALFORMED_JSON")
        self.assertIsInstance(result.diagnostic["json_decode_error_position"], int)
        self.assertIsInstance(result.diagnostic["output_byte_length"], int)

    def test_sin_archivo_de_salida_es_invalid_output(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="no_output_file")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertEqual(result.diagnostic, {
            "reason_code": "INVALID_OUTPUT_NO_OUTPUT_FILE",
            "output_file_present": False,
        })

    def test_exit_no_cero_es_failed(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="nonzero_exit")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "failed")
        self.assertIn("fake codex exec failure", result.error_detail)
        # The durable diagnostic carries only the exit code and a byte
        # COUNT of stderr -- never the stderr content itself. The real
        # stderr text (present in the ephemeral error_detail above) must
        # not appear anywhere in the durable diagnostic dict.
        self.assertEqual(result.diagnostic["reason_code"], "FAILED_NONZERO_EXIT")
        self.assertIsInstance(result.diagnostic["exit_code"], int)
        self.assertIsInstance(result.diagnostic["stderr_byte_length"], int)
        self.assertNotIn("fake codex exec failure", json.dumps(result.diagnostic))

    def test_timeout_es_timeout(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="hang")
        adapter = CodexCliAdapter(cli_path=cli, timeout_seconds=1)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "timeout")
        self.assertEqual(result.diagnostic["reason_code"], "TIMEOUT_EXCEEDED")
        self.assertEqual(result.diagnostic["timeout_seconds"], 1.0)


class PruebaConfinamientoDeWorktree(CodexCliAdapterTestCase):
    def test_worktree_relativo_falla_antes_de_invocar(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        request = _request(self._worktree)
        request = AgentInvocationRequest(
            invocation_id=request.invocation_id, mission_id=request.mission_id,
            agent_role=request.agent_role, attempt=request.attempt,
            task={"repository": {"worktree_path": "relative/path"}},
            requested_at=request.requested_at, requested_fresh_context=request.requested_fresh_context,
        )
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(request)

    def test_worktree_inexistente_falla_antes_de_invocar(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        request = _request(self._tmp / "no-existe")
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(request)

    def test_comando_pasa_el_worktree_exacto_a_C_y_add_dir(self):
        """Verifica indirectamente el confinamiento: capturamos los argv
        reales que el fake recibió, escribiéndolos a un archivo aparte."""
        script_path = self._tmp / "fake_codex_capture.py"
        capture_path = self._tmp / "captured_argv.json"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            with open({str(capture_path)!r}, "w") as f:
                json.dump(args, f)
            sys.stdin.read()
            with open("pilot_file.py", "w") as pf:
                pf.write("# fake codex change\\n")
            out_path = args[args.index("-o") + 1]
            with open(out_path, "w") as f:
                json.dump({{"attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                            "changed_files": [{{"path": "pilot_file.py", "reason": "fake"}}],
                            "checks": [], "skipped_checks": [], "risks": [],
                            "assumptions": [], "rollback_notes": "none",
                            "safety_confirmation": {{"no_existing_work_altered": True, "no_main_change": True,
                                "no_remote_action": True, "no_production_access": True,
                                "no_protected_path_change": True, "complete_diff_inspected": True}},
                            "handoff_document_ref": None, "conclusion": {{"text": "x", "label": "FACT"}}}}, f)
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        adapter = CodexCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        captured = json.loads(capture_path.read_text())
        self.assertIn(str(self._worktree), captured)
        self.assertIn("--add-dir", captured)
        idx = captured.index("--add-dir")
        self.assertEqual(captured[idx + 1], str(self._worktree))
        idx_cd = captured.index("-C")
        self.assertEqual(captured[idx_cd + 1], str(self._worktree))
        self.assertIn("--disable", captured)
        disable_values = [captured[i + 1] for i, a in enumerate(captured) if a == "--disable"]
        self.assertIn("multi_agent", disable_values)


class PruebaSinFlagsDeAprobacionNoSoportadosNiPeligrosos(CodexCliAdapterTestCase):
    """Corrective cycle #3: the real, installed Codex CLI has no `-a`/
    `--ask-for-approval` flag on `codex exec` at all (`codex exec --help`
    confirms this) -- the prior command line failed outright with
    'unexpected argument -a found' before the model ever received the
    task. This class proves the fix removes `-a` cleanly, never
    introduces the CLI's real unsafe escape hatch
    (`--dangerously-bypass-approvals-and-sandbox`) as a replacement, and
    that every other safety control (sandbox mode, worktree confinement,
    multi_agent disable, agents.enabled=false) survives untouched."""

    def _captured_argv(self):
        script_path = self._tmp / "fake_codex_capture_approval.py"
        capture_path = self._tmp / "captured_argv_approval.json"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            with open({str(capture_path)!r}, "w") as f:
                json.dump(args, f)
            sys.stdin.read()
            with open("pilot_file.py", "w") as pf:
                pf.write("# fake codex change\\n")
            out_path = args[args.index("-o") + 1]
            with open(out_path, "w") as f:
                json.dump({{"attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                            "changed_files": [{{"path": "pilot_file.py", "reason": "fake"}}],
                            "checks": [], "skipped_checks": [], "risks": [],
                            "assumptions": [], "rollback_notes": "none",
                            "safety_confirmation": {{"no_existing_work_altered": True, "no_main_change": True,
                                "no_remote_action": True, "no_production_access": True,
                                "no_protected_path_change": True, "complete_diff_inspected": True}},
                            "handoff_document_ref": None, "conclusion": {{"text": "x", "label": "FACT"}}}}, f)
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        adapter = CodexCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        return json.loads(capture_path.read_text())

    def test_comando_no_incluye_a(self):
        captured = self._captured_argv()
        self.assertNotIn("-a", captured)

    def test_comando_no_incluye_bypass_de_aprobaciones_y_sandbox(self):
        captured = self._captured_argv()
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", captured)
        self.assertNotIn("--dangerously-bypass-hook-trust", captured)
        self.assertNotIn("--approve-for-me", captured)

    def test_controles_de_seguridad_soportados_permanecen_presentes(self):
        captured = self._captured_argv()
        self.assertIn("-s", captured)
        self.assertIn("workspace-write", captured)
        self.assertIn("-C", captured)
        self.assertIn("--add-dir", captured)
        self.assertIn("--disable", captured)
        disable_values = [captured[i + 1] for i, a in enumerate(captured) if a == "--disable"]
        self.assertIn("multi_agent", disable_values)
        self.assertIn("-c", captured)
        idx_c = captured.index("-c")
        self.assertEqual(captured[idx_c + 1], "agents.enabled=false")


class PruebaDescubrimientoDeCli(unittest.TestCase):
    def test_ruta_explicita_inexistente_falla(self):
        with self.assertRaises(CodexCliAdapterError):
            CodexCliAdapter(cli_path="/no/existe/codex")

    def test_timeout_invalido_falla(self):
        with self.assertRaises(CodexCliAdapterError):
            CodexCliAdapter(cli_path=__file__, timeout_seconds=0)
        with self.assertRaises(CodexCliAdapterError):
            CodexCliAdapter(cli_path=__file__, timeout_seconds=True)


# --- Emma's P1 corrective cycle: genuine provider identity only ----------

class PruebaIdentidadDeProveedorGenuina(CodexCliAdapterTestCase):
    """Emma's finding: `provider_conversation_id` must never be
    synthesized from `invocation_id` (or any other orchestrator-owned
    value) -- it must be `None` unless the CLI's own `--json` event
    stream genuinely reports a thread/session id."""

    def test_thread_id_genuino_presente_es_capturado(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT",
                                 exec_mode="success_with_thread_id")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.provider_conversation_id, "genuine-codex-thread-42")

    def test_thread_id_ausente_permanece_none(self):
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        self.assertIsNone(result.provider_conversation_id)

    def test_invocation_id_nunca_se_usa_como_sustituto(self):
        """Ni cuando hay un thread id genuino, ni cuando no lo hay, el
        invocation_id de la orquestación puede aparecer como
        provider_conversation_id -- eso es exactamente el defecto P1
        que este ciclo corrige."""
        request = _request(self._worktree)

        cli_present = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT",
                                          exec_mode="success_with_thread_id")
        result_present = CodexCliAdapter(cli_path=cli_present).invoke(request)
        self.assertNotEqual(result_present.provider_conversation_id, request.invocation_id)

        cli_absent = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        result_absent = CodexCliAdapter(cli_path=cli_absent).invoke(request)
        self.assertIsNone(result_absent.provider_conversation_id)
        self.assertNotEqual(result_absent.provider_conversation_id, request.invocation_id)


class PruebaExcepcionesNoSePropagan(CodexCliAdapterTestCase):
    def test_excepcion_arbitraria_durante_el_despacho_no_se_propaga(self):
        """Cualquier excepción no anticipada dentro del límite de
        despacho/parseo debe convertirse en un outcome no completado,
        nunca propagar fuera de invoke() (requisito existente de
        PROVIDER_INTEGRATION_V1.md sección 10, ya honrado por los
        adaptadores basados en SDK)."""
        cli = _write_fake_codex(self._tmp, login_status="Logged in using ChatGPT", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        with unittest.mock.patch(
            "orchestrator.adapters.codex_cli_adapter._load_evidence_schema",
            side_effect=ValueError("synthetic injected failure -- proves the catch-all works"),
        ):
            result = adapter.invoke(_request(self._worktree))
        self.assertNotEqual(result.outcome, "completed")
        self.assertIsNone(result.evidence)
        self.assertIn("synthetic injected failure", result.error_detail)

    def test_pre_invocacion_sigue_lanzando_no_se_traga(self):
        """Los refusals fail-closed previos a la invocación (worktree
        inválido, login no confirmado) deben seguir siendo excepciones
        reales -- el catch-all no debe convertirlos en un
        AgentInvocationResult silencioso."""
        cli = _write_fake_codex(self._tmp, login_status="Logged in using an API key", exec_mode="success")
        adapter = CodexCliAdapter(cli_path=cli)
        with self.assertRaises(CodexCliAdapterError):
            adapter.invoke(_request(self._worktree))


# --- Corrective cycle #4: real defects from the zero-cost pilot retry ----

class PruebaEsquemaExcluyeArtifactYFijaAttempt(unittest.TestCase):
    """Unit-level checks directly on _load_evidence_schema(), independent
    of any subprocess -- confirms the schema handed to the model can no
    longer admit the ambiguity the real pilot demonstrated."""

    def test_emilio_no_puede_reportar_artifact(self):
        from orchestrator.adapters.codex_cli_adapter import _load_evidence_schema
        schema = _load_evidence_schema("emilio", attempt=0)
        self.assertNotIn("artifact", schema["properties"])
        self.assertNotIn("artifact", schema["required"])

    def test_emma_no_se_ve_afectada_por_la_exclusion_de_artifact(self):
        from orchestrator.adapters.codex_cli_adapter import _load_evidence_schema
        schema = _load_evidence_schema("emma", attempt=0)
        self.assertNotIn("artifact", schema["properties"])  # never had one to begin with

    def test_attempt_se_fija_a_const_para_el_valor_solicitado(self):
        from orchestrator.adapters.codex_cli_adapter import _load_evidence_schema
        schema_0 = _load_evidence_schema("emilio", attempt=0)
        schema_1 = _load_evidence_schema("emilio", attempt=1)
        self.assertEqual(schema_0["properties"]["attempt"], {"type": "integer", "const": 0})
        self.assertEqual(schema_1["properties"]["attempt"], {"type": "integer", "const": 1})

    def test_attempt_const_tambien_se_aplica_al_esquema_de_emma(self):
        from orchestrator.adapters.codex_cli_adapter import _load_evidence_schema
        schema = _load_evidence_schema("emma", attempt=1)
        self.assertEqual(schema["properties"]["attempt"], {"type": "integer", "const": 1})

    def test_attempt_const_declara_tipo_entero_requerido_por_el_backend_real(self):
        """Corrective cycle #5: a bare `{"const": N}` (no `"type"` key) was
        rejected by the real model backend behind `codex exec
        --output-schema` with 'schema must have a 'type' key' -- every
        real dispatch failed outright. This regression pins the fix:
        `"type": "integer"` must always accompany `"const"`."""
        from orchestrator.adapters.codex_cli_adapter import _load_evidence_schema
        for role in ("emilio", "emma"):
            for attempt in (0, 1):
                schema = _load_evidence_schema(role, attempt=attempt)
                attempt_property = schema["properties"]["attempt"]
                self.assertEqual(attempt_property.get("type"), "integer")
                self.assertEqual(attempt_property.get("const"), attempt)


class PruebaArtefactoDePatchGenuinoDesdeGitDiff(CodexCliAdapterTestCase):
    """Corrective cycle #4, requirement 1 (runtime discovery from the
    authorized real zero-cost pilot retry): the real Codex CLI produced
    genuinely correct code but could not create a git commit from inside
    `-s workspace-write` ("sandbox denied creation of .git/index.lock"),
    so its own reported `artifact` was structurally invalid (`mode:
    "patch"` with every field null) -- exactly what caused Chugel's
    schema validation to reject the evidence. These tests reproduce that
    scenario against the fix: the model can no longer even report
    `artifact` (see PruebaEsquemaExcluyeArtifactYFijaAttempt above), and
    this adapter computes a genuine one itself via `git diff`, never a
    commit, never a sandbox change."""

    def _write_fake_codex_no_artifact(self, *, write_real_file: bool, filename: str = "pilot_file.py") -> str:
        script_path = self._tmp / "fake_codex_no_artifact.py"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            sys.stdin.read()
            if {write_real_file!r}:
                with open({filename!r}, "w") as pf:
                    pf.write("# genuine change written by the fake model\\n")
            out_path = args[args.index("-o") + 1]
            evidence = {{"attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                "changed_files": [{{"path": {filename!r}, "reason": "fake"}}],
                "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
                "rollback_notes": "none",
                "safety_confirmation": {{"no_existing_work_altered": True, "no_main_change": True,
                    "no_remote_action": True, "no_production_access": True,
                    "no_protected_path_change": True, "complete_diff_inspected": True}},
                "handoff_document_ref": None, "conclusion": {{"text": "x", "label": "FACT"}}}}
            with open(out_path, "w") as f:
                json.dump(evidence, f)
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return str(script_path)

    def test_patch_real_es_computado_y_es_estructuralmente_valido(self):
        import hashlib
        cli = self._write_fake_codex_no_artifact(write_real_file=True)
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        artifact = result.evidence["artifact"]
        self.assertEqual(artifact["mode"], "patch")
        self.assertIsNone(artifact["commit_sha"])
        self.assertIsNotNone(artifact["patch_path"])
        patch_bytes = Path(artifact["patch_path"]).read_bytes()
        self.assertTrue(len(patch_bytes) > 0)
        self.assertEqual(artifact["patch_byte_size"], len(patch_bytes))
        self.assertEqual(artifact["patch_sha256"], hashlib.sha256(patch_bytes).hexdigest())
        self.assertIn(b"pilot_file.py", patch_bytes)
        # No commit was ever created -- the sandbox boundary this fix
        # preserves. HEAD stays exactly where the initial commit left it.
        head = subprocess.run(
            ["git", "-C", str(self._worktree), "log", "--oneline"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        self.assertEqual(len(head), 1, "no new commit should have been created")

    def test_ninguna_modificacion_real_es_invalid_output(self):
        """The model claims changed_files but never actually writes
        anything -- this must fail closed, never fabricate a non-empty
        patch for work that was never really done. Real live incident:
        this exact branch is the confirmed root cause of a real Mission
        B stall during M1 Live Acceptance Validation -- Structured
        Allow-Listed Diagnostics exists specifically to make this
        diagnosable durably, which the free-text-only predecessor design
        could not do (the raw error_detail was never persisted anywhere,
        by design -- see chugel.py's record_dispatch_result() docstring)."""
        cli = self._write_fake_codex_no_artifact(write_real_file=False)
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIn("no uncommitted change", result.error_detail)
        self.assertEqual(result.diagnostic, {
            "reason_code": "INVALID_OUTPUT_ARTIFACT_COMPUTATION_FAILED",
            "artifact_failure_reason": "NO_UNCOMMITTED_CHANGE",
        })

    def test_git_diff_no_altera_el_arbol_de_trabajo_ni_dejar_staging_pendiente(self):
        cli = self._write_fake_codex_no_artifact(write_real_file=True)
        adapter = CodexCliAdapter(cli_path=cli)
        adapter.invoke(_request(self._worktree))
        status = subprocess.run(
            ["git", "-C", str(self._worktree), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        # The new file remains untracked (exactly what the model itself
        # left behind) -- "git add -N" + "git reset" leaves no staged
        # entries and does not commit or discard anything.
        self.assertEqual(status.strip(), "?? pilot_file.py")

    def test_artifact_reportado_por_el_modelo_es_ignorado(self):
        """Defense in depth: even if some future/misbehaving CLI ignored
        the schema and included a stray `artifact` key anyway, this
        adapter must still use its own computed identity, never the
        model's."""
        script_path = self._tmp / "fake_codex_stray_artifact.py"
        script_path.write_text(textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            sys.stdin.read()
            with open("pilot_file.py", "w") as pf:
                pf.write("# genuine change\\n")
            out_path = args[args.index("-o") + 1]
            evidence = {"attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                "artifact": {"mode": "patch", "commit_sha": None,
                             "patch_path": None, "patch_sha256": None, "patch_byte_size": None},
                "changed_files": [{"path": "pilot_file.py", "reason": "fake"}],
                "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
                "rollback_notes": "none",
                "safety_confirmation": {"no_existing_work_altered": True, "no_main_change": True,
                    "no_remote_action": True, "no_production_access": True,
                    "no_protected_path_change": True, "complete_diff_inspected": True},
                "handoff_document_ref": None, "conclusion": {"text": "x", "label": "FACT"}}
            with open(out_path, "w") as f:
                json.dump(evidence, f)
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        adapter = CodexCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "completed")
        artifact = result.evidence["artifact"]
        self.assertIsNotNone(artifact["patch_path"])
        self.assertIsNotNone(artifact["patch_sha256"])
        self.assertIsNotNone(artifact["patch_byte_size"])


class PruebaEmmaNoComputaArtefactoDePatch(CodexCliAdapterTestCase):
    """Emma (read-only reviewer role) never produces artifact identity --
    this fix must not attempt git-diff computation for her role at all."""

    def test_emma_no_dispara_computo_de_patch(self):
        script_path = self._tmp / "fake_codex_emma.py"
        script_path.write_text(textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            sys.stdin.read()
            out_path = args[args.index("-o") + 1]
            evidence = {"attempt": 0, "invoked_at": "2026-08-25T21:00:00Z",
                "artifact_identity_confirmed_at_start": {"mode": "commit", "commit_sha": "a" * 40,
                    "patch_path": None, "patch_sha256": None, "patch_byte_size": None},
                "artifact_identity_confirmed_before_conclusion": {"mode": "commit", "commit_sha": "a" * 40,
                    "patch_path": None, "patch_sha256": None, "patch_byte_size": None},
                "rechecked_commands": [], "findings": [], "verdict": "PASS", "blocked_reason": None}
            with open(out_path, "w") as f:
                json.dump(evidence, f)
            sys.exit(0)
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        adapter = CodexCliAdapter(cli_path=str(script_path))
        result = adapter.invoke(_request(self._worktree, agent_role="emma"))
        self.assertEqual(result.outcome, "completed")
        self.assertNotIn("artifact", result.evidence)


class PruebaDiagnosticoDeFalloDeTurno(CodexCliAdapterTestCase):
    """Corrective cycle #5, requirement 4: reproduces the real pilot's
    diagnostic gap -- `codex exec --json` reported a `turn.failed`/`error`
    event on stdout (with `process.returncode` sometimes 0, sometimes
    nonzero) while stderr was empty, and the adapter's own error_detail
    was completely uninformative. These tests prove the provider's own
    safe error message now reaches error_detail on both paths."""

    def _write_fake_codex_turn_failure(self, *, exit_code: int, write_output_file: bool) -> str:
        script_path = self._tmp / "fake_codex_turn_failure.py"
        script_path.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if args[:2] == ["login", "status"]:
                print("Logged in using ChatGPT")
                sys.exit(0)
            sys.stdin.read()
            print(json.dumps({{"type": "thread.started", "thread_id": "t-1"}}))
            print(json.dumps({{"type": "turn.started"}}))
            print(json.dumps({{"type": "error",
                "message": "invalid_request_error: schema must have a \\'type\\' key"}}))
            print(json.dumps({{"type": "turn.failed",
                "error": {{"message": "invalid_request_error: schema must have a \\'type\\' key"}}}}))
            if {write_output_file!r}:
                out_path = args[args.index("-o") + 1]
                with open(out_path, "w") as f:
                    f.write("{{}}")
            sys.exit({exit_code!r})
        '''), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return str(script_path)

    def test_fallo_de_turno_con_salida_no_cero_se_incluye_en_error_detail(self):
        cli = self._write_fake_codex_turn_failure(exit_code=1, write_output_file=False)
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "failed")
        self.assertIn("schema must have a", result.error_detail)

    def test_fallo_de_turno_con_salida_cero_y_sin_archivo_se_incluye_en_error_detail(self):
        """The exact real-world shape: returncode 0, no last-message file,
        the actionable reason sitting only in the --json stdout stream."""
        cli = self._write_fake_codex_turn_failure(exit_code=0, write_output_file=False)
        adapter = CodexCliAdapter(cli_path=cli)
        result = adapter.invoke(_request(self._worktree))
        self.assertEqual(result.outcome, "invalid_output")
        self.assertIn("schema must have a", result.error_detail)

    def test_extraccion_directa_de_mensaje_de_fallo_de_turno(self):
        from orchestrator.adapters.codex_cli_adapter import _extract_turn_failure_message
        lines = [
            '{"type": "thread.started", "thread_id": "t-1"}',
            '{"type": "error", "message": "boom: first"}',
            '{"type": "turn.failed", "error": {"message": "boom: final"}}',
        ]
        self.assertEqual(_extract_turn_failure_message(lines), "boom: final")

    def test_extraccion_retorna_none_sin_eventos_de_fallo(self):
        from orchestrator.adapters.codex_cli_adapter import _extract_turn_failure_message
        lines = ['{"type": "thread.started", "thread_id": "t-1"}', "not json at all"]
        self.assertIsNone(_extract_turn_failure_message(lines))

    def test_extraccion_nunca_expone_el_evento_crudo_ni_otros_campos(self):
        """Only the `message` string is ever extracted -- never the raw
        event, never a `prompt`/`task`/credential-shaped field some other
        event might carry."""
        from orchestrator.adapters.codex_cli_adapter import _extract_turn_failure_message
        lines = [json.dumps({
            "type": "error", "message": "safe message",
            "prompt": "the full original prompt text, never to be surfaced",
            "api_key": "sk-should-never-appear",
        })]
        result = _extract_turn_failure_message(lines)
        self.assertEqual(result, "safe message")
        self.assertNotIn("api_key", result)
        self.assertNotIn("sk-should-never-appear", result)


if __name__ == "__main__":
    unittest.main()
