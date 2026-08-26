"""Integration test: the zero-cost CLI adapters (CodexCliAdapter,
ClaudeCliAdapter) plugged into the real, unmodified orchestrator stack --
chugel, wiring, agent_invocation, autonomous_runner -- through real
Mission Record persistence, including a genuine restart (two separate
run_mission() calls sharing nothing but the on-disk Mission Record).

Fake CLI executables stand in for `codex`/`claude`; no real provider is
ever invoked. This proves durable dispatch, restart safety, and durable
attempt budgets hold identically when the adapters are these new
subscription-CLI ones, not just the abstract fakes used elsewhere."""

from __future__ import annotations

import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import orchestrator.chugel as chugel
from orchestrator.adapters.claude_cli_adapter import ClaudeCliAdapter
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter
from orchestrator.autonomous_runner import run_mission


def _git_init_worktree(worktree: Path) -> None:
    """Corrective cycle #4: emilio-role CLI dispatches now compute a real
    `git diff` after the fake CLI exits, so every worktree these tests use
    must be a genuine git repository with a starting commit -- exactly
    what a real Mission Record's worktree already is."""
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=str(worktree), check=True, capture_output=True)
    (worktree / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(worktree), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=str(worktree), check=True, capture_output=True)


_FAKE_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, sys
    args = sys.argv[1:]
    if args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
        sys.exit(0)
    sys.stdin.read()
    # A genuine provider-native thread id, emitted on the --json event
    # stream (stdout) -- exactly what _extract_thread_id() looks for.
    # Distinct per fixture instance (see __THREAD_ID__) so a real restart
    # test can prove attempt 0 and attempt 1 never share an identity.
    print(json.dumps({"type": "thread.started", "thread_id": "__THREAD_ID__"}))
    # Corrective cycle #4: a real file change (never a git commit --
    # the CLI's own `-s workspace-write` sandbox refuses that), so the
    # adapter's own git-diff-based artifact computation has genuine
    # content to capture instead of the model reporting one itself.
    with open("pilot_file.py", "w") as pf:
        pf.write("attempt __ATTEMPT__\\n")
    out_path = args[args.index("-o") + 1]
    with open(out_path, "w") as f:
        json.dump({
            "attempt": __ATTEMPT__, "invoked_at": "2026-08-25T21:10:00Z",
            "changed_files": [{"path": "pilot_file.py", "reason": "cli pilot"}],
            "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
            "rollback_notes": "none",
            "safety_confirmation": {"no_existing_work_altered": True, "no_main_change": True,
                "no_remote_action": True, "no_production_access": True,
                "no_protected_path_change": True, "complete_diff_inspected": True},
            "handoff_document_ref": None,
            "conclusion": {"text": "attempt __ATTEMPT__ via fake codex CLI", "label": "FACT"},
        }, f)
    sys.exit(0)
''')

_FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, sys
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}))
        sys.exit(0)
    task = json.loads(sys.stdin.read())
    # Corrective cycle #4: the builder's real artifact is now `mode:
    # "patch"` with a genuine sha256/byte-size (computed by
    # CodexCliAdapter via git diff), not a fixed "mode: commit" fake --
    # Emma's own task already carries that exact artifact
    # (build_emma_invocation_request() copies builder_evidence[attempt]
    # ["artifact"] into her task), so this fake echoes it back rather than
    # hardcoding a stale identity that would no longer match and trip
    # ARTIFACT_IDENTITY_MISMATCH_WITH_BUILDER.
    artifact_identity = task["artifact"]
    # A genuine provider-native session id at the top level of the
    # envelope, alongside "result" -- exactly what _extract_session_id()
    # looks for, and structured so _extract_structured_result() correctly
    # extracts only the nested "result" object as evidence (uncontaminated
    # by session_id/type/subtype).
    print(json.dumps({
        "type": "result", "subtype": "success", "session_id": "__SESSION_ID__",
        "result": {
            "attempt": __ATTEMPT__, "invoked_at": "2026-08-25T21:11:00Z",
            "artifact_identity_confirmed_at_start": artifact_identity,
            "artifact_identity_confirmed_before_conclusion": artifact_identity,
            "rechecked_commands": [], "findings": __FINDINGS__,
            "verdict": __VERDICT__, "blocked_reason": None,
        },
    }))
    sys.exit(0)
''')


def _write_fake_codex_cli(tmp_dir: Path, name: str, attempt: int, thread_id: str | None = None) -> str:
    thread_id = thread_id or f"fake-codex-thread-{name}"
    content = _FAKE_CODEX.replace("__ATTEMPT__", str(attempt)).replace("__THREAD_ID__", thread_id)
    path = tmp_dir / name
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _write_fake_claude_cli(tmp_dir: Path, name: str, attempt: int, findings: list, verdict: str,
                           session_id: str | None = None) -> str:
    import json as _json
    session_id = session_id or f"fake-claude-session-{name}"
    content = (
        _FAKE_CLAUDE
        .replace("__ATTEMPT__", str(attempt))
        .replace("__FINDINGS__", _json.dumps(findings))
        .replace("__VERDICT__", _json.dumps(verdict))
        .replace("__SESSION_ID__", session_id)
    )
    path = tmp_dir / name
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class PruebaAdaptadoresCliEnRunMissionReal(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name).resolve()
        self._missions_dir = self._tmp / "missions"
        self._worktree = self._tmp / "worktree"
        self._worktree.mkdir()
        _git_init_worktree(self._worktree)
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = self._missions_dir

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_authorized(self):
        m = chugel.create_mission("cli-adapter integration test", {
            "outcome": "prove CLI adapters work through the real stack",
            "scope": ["cli adapter integration"], "non_goals": [],
            "acceptance_criteria": ["mission reaches PUBLISH_AWAITING_AUTHORIZATION"],
            "authorized_by": "jose", "authorized_at": "2026-08-25T20:00:00Z",
            "authorization_decision_ref": "cli-adapter-test-1",
        })
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": str(self._worktree), "branch": "cli-adapter-test",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="x")
        chugel.decide_gate(mid, "scope_authorization", {
            "status": "approved", "requested_at": "2026-08-25T19:59:00Z",
            "decided_at": "2026-08-25T19:59:30Z", "decided_by": "jose",
            "decision_ref": "r", "approved_for": {"mission_definition_version": 1},
        })
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="x")
        return mid

    def test_pipeline_completo_con_adaptadores_cli_reales_del_stack(self):
        """Camino feliz completo -- attempt 0 Emilio(codex CLI) -> attempt 0
        Emma(claude CLI) PASS -> PUBLISH_AWAITING_AUTHORIZATION -- usando
        el stack real (chugel/wiring/agent_invocation/autonomous_runner)
        sin ninguna modificación, solo estos dos adaptadores nuevos."""
        mid = self._mission_authorized()
        codex_cli = _write_fake_codex_cli(self._tmp, "codex0.py", attempt=0)
        claude_cli = _write_fake_claude_cli(self._tmp, "claude0.py", attempt=0, findings=[], verdict="PASS")
        adapters = {
            "codex": CodexCliAdapter(cli_path=codex_cli),
            "claude": ClaudeCliAdapter(cli_path=claude_cli),
        }
        result = run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 2)
        self.assertTrue(all(e["status"] == "FINALIZED" for e in ledger))
        self.assertTrue(all(e["result_classification"] == "completed" for e in ledger))
        # Human gate never auto-approved.
        self.assertEqual(record["human_gates"]["publish_authorization"]["status"], "not_requested")

    def test_restart_real_entre_dos_llamadas_separadas_a_run_mission(self):
        """CHANGES_REQUIRED -> CORRECTING persistido, luego una segunda
        llamada a run_mission() completamente separada (adaptadores
        nuevos, nada compartido) retoma correctamente y llega a PASS --
        sin ningún redespacho duplicado."""
        mid = self._mission_authorized()

        codex_cli_0 = _write_fake_codex_cli(self._tmp, "codex0.py", attempt=0)
        claude_cli_cr = _write_fake_claude_cli(
            self._tmp, "claude_cr.py", attempt=0,
            findings=[{"id": "f1", "severity": "P1", "summary": "bug",
                       "file": "pilot_file.py", "line_range": "1-2", "category": "correctness"}],
            verdict="CHANGES_REQUIRED",
        )
        first_adapters = {
            "codex": CodexCliAdapter(cli_path=codex_cli_0),
            "claude": ClaudeCliAdapter(cli_path=claude_cli_cr),
        }
        first_result = run_mission(mid, first_adapters, max_total_attempts=2)
        self.assertEqual(first_result.status, "HUMAN_ACTION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "CORRECTING")
        self.assertEqual(len(record["dispatch_ledger"]), 2)

        # Genuinely separate adapters/CLI fixtures for the "restarted" call.
        codex_cli_1 = _write_fake_codex_cli(self._tmp, "codex1.py", attempt=1)
        claude_cli_pass = _write_fake_claude_cli(self._tmp, "claude_pass.py", attempt=1, findings=[], verdict="PASS")
        second_adapters = {
            "codex": CodexCliAdapter(cli_path=codex_cli_1),
            "claude": ClaudeCliAdapter(cli_path=claude_cli_pass),
        }
        second_result = run_mission(mid, second_adapters, max_total_attempts=10)
        self.assertEqual(second_result.status, "AUTHORIZATION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        self.assertEqual(record["corrective_cycle_count"], 1)

        ledger = record["dispatch_ledger"]
        self.assertEqual(len(ledger), 4)
        self.assertEqual(len({e["invocation_id"] for e in ledger}), 4, "no duplicate dispatch across restart")
        self.assertTrue(all(e["status"] == "FINALIZED" for e in ledger))


# --- Emma's P1 corrective cycle: independence semantics through the real stack ---

class PruebaSemanticaDeIndependenciaConAdaptadoresCli(unittest.TestCase):
    """Reproduces, through the real chugel/agent_invocation/wiring stack
    (not a synthetic unit-level call), both directions of independence
    enforcement now that the CLI adapters report genuine provider
    identity instead of a vacuous invocation_id substitute."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name).resolve()
        self._missions_dir = self._tmp / "missions"
        self._worktree = self._tmp / "worktree"
        self._worktree.mkdir()
        _git_init_worktree(self._worktree)
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = self._missions_dir

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _mission_authorized(self):
        m = chugel.create_mission("independence semantics test", {
            "outcome": "prove independence enforcement with genuine CLI identity",
            "scope": ["independence semantics"], "non_goals": [],
            "acceptance_criteria": ["StaleSessionReused fires on genuine reuse"],
            "authorized_by": "jose", "authorized_at": "2026-08-25T20:00:00Z",
            "authorization_decision_ref": "independence-test-1",
        })
        mid = m["mission_id"]
        chugel.record_repository_state(mid, {
            "worktree_path": str(self._worktree), "branch": "independence-test",
            "base_sha": "b" * 40, "isolation_confirmed": True,
        })
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="x")
        chugel.decide_gate(mid, "scope_authorization", {
            "status": "approved", "requested_at": "2026-08-25T19:59:00Z",
            "decided_at": "2026-08-25T19:59:30Z", "decided_by": "jose",
            "decision_ref": "r", "approved_for": {"mission_definition_version": 1},
        })
        chugel.transition(mid, "AUTHORIZED", actor="jose", reason="x")
        return mid

    def test_misma_identidad_de_proveedor_entre_builder_y_reviewer_es_rechazada(self):
        """Emilio (Codex) reporta thread_id "shared-identity-collision" y
        Emma (Claude) reporta el MISMO valor como session_id -- una
        colisión genuina de identidad, sin importar que los providers
        sean distintos. _check_persisted_builder_independence() debe
        detectarlo y StaleSessionReused debe propagar -- no un
        RunnerResult silencioso."""
        import orchestrator.agent_invocation as ai

        mid = self._mission_authorized()
        shared_identity = "shared-identity-collision"
        codex_cli = _write_fake_codex_cli(self._tmp, "codex_collide.py", attempt=0, thread_id=shared_identity)
        claude_cli = _write_fake_claude_cli(
            self._tmp, "claude_collide.py", attempt=0, findings=[], verdict="PASS",
            session_id=shared_identity,
        )
        adapters = {
            "codex": CodexCliAdapter(cli_path=codex_cli),
            "claude": ClaudeCliAdapter(cli_path=claude_cli),
        }
        with self.assertRaises(ai.StaleSessionReused):
            run_mission(mid, adapters, max_total_attempts=4)

        # Builder evidence was written (Emilio's own dispatch is legitimate
        # and unrelated to the collision); reviewer evidence must NOT have
        # been written -- the independence check fires strictly before any
        # reviewer_evidence write.
        record = chugel.get_mission(mid)
        self.assertEqual(len(record["builder_evidence"]), 1)
        self.assertEqual(record["reviewer_evidence"], [])

    def test_identidades_de_proveedor_distintas_son_aceptadas(self):
        """Control case: distinct genuine identities for Emilio and Emma
        complete normally -- the same pipeline the corrected adapters must
        still support for the ordinary case."""
        mid = self._mission_authorized()
        codex_cli = _write_fake_codex_cli(self._tmp, "codex_distinct.py", attempt=0, thread_id="thread-A")
        claude_cli = _write_fake_claude_cli(
            self._tmp, "claude_distinct.py", attempt=0, findings=[], verdict="PASS",
            session_id="session-B",
        )
        adapters = {
            "codex": CodexCliAdapter(cli_path=codex_cli),
            "claude": ClaudeCliAdapter(cli_path=claude_cli),
        }
        result = run_mission(mid, adapters, max_total_attempts=4)
        self.assertEqual(result.status, "AUTHORIZATION_REQUIRED")
        record = chugel.get_mission(mid)
        self.assertEqual(record["state"], "PUBLISH_AWAITING_AUTHORIZATION")
        self.assertEqual(len(record["reviewer_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
