"""Real-process characterization for the provider worker boundary."""

import importlib
import inspect
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.agent_invocation import AgentInvocationRequest
from orchestrator.provider_credentials import trusted_worker_environment
from orchestrator.provider_worker import ProviderWorkerInvoker


_CODEX_FAKE = r'''
import json, os
from pathlib import Path
from enum import Enum
TRACE = Path(__file__).resolve().parents[1] / "sdk-trace.json"
class ApprovalMode(Enum): deny_all = "deny_all"; auto_review = "auto_review"
class CodexConfig:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class _AccountRoot: type = "apiKey"
class _Account: root = _AccountRoot()
class _AccountResponse: account = _Account()
class _Status: value = "completed"
class _FailedStatus: value = "failed"
class _Turn:
    status = _Status(); error = None
    def __init__(self, response): self.final_response = response
class _Thread:
    id = "fake-codex-conversation"
    def __init__(self, owner, kwargs): self.owner=owner; self.kwargs=kwargs
    def run(self, prompt, *, output_schema=None, **kwargs):
        trace=self.owner.trace; trace["run_count"]=trace.get("run_count",0)+1
        trace["output_schema"]=output_schema; trace["thread_start"]=self.kwargs
        trace["config_text"]=(Path(self.owner.config.env["CODEX_HOME"])/"config.toml").read_text()
        trace["codex_home"]=self.owner.config.env["CODEX_HOME"]
        TRACE.write_text(json.dumps(trace, default=str))
        mode=json.loads(prompt).get("_synthetic_test_mode")
        if mode == "completed_identity":
            self.id = "codex-thread-abc"
            return _Turn(json.dumps({"attempt": 0, "conclusion": {"text": "x", "label": "FACT"}}))
        if mode == "exception":
            from .errors import ServerBusyError
            raise ServerBusyError("synthetic server busy")
        if mode == "failed":
            turn=_Turn(None); turn.status=_FailedStatus(); turn.error="synthetic failed"; return turn
        if mode == "invalid_json": return _Turn("not-json")
        if mode == "echo_secret": return _Turn(json.dumps({"attempt": 0, "text": "synthetic-worker-key-never-log"}))
        if mode == "raise_secret":
            raise RuntimeError("provider echoed synthetic-worker-key-never-log")
        return _Turn(json.dumps({"attempt": 0}))
class Codex:
    def __init__(self, *, config):
        self.config=config
        self.trace={"sdk_pid":os.getpid(), "worker_env":dict(os.environ),
                    "child_env_names":sorted(config.env), "child_env":dict(config.env),
                    "login_count":0}
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def login_api_key(self,key): self.trace["login_count"] += 1
    def account(self): return _AccountResponse()
    def thread_start(self, **kwargs): return _Thread(self, kwargs)
'''

_CODEX_ERRORS = r'''
class CodexError(Exception): pass
class TransportClosedError(CodexError): pass
class ServerBusyError(CodexError): pass
class RetryLimitExceededError(ServerBusyError): pass
class ParseError(CodexError): pass
class InvalidRequestError(CodexError): pass
class MethodNotFoundError(CodexError): pass
class InvalidParamsError(CodexError): pass
class InternalRpcError(CodexError): pass
'''

_CLAUDE_FAKE = r'''
import json, os
from pathlib import Path
TRACE = Path(__file__).resolve().parents[1] / "sdk-trace.json"
class ClaudeSDKError(Exception): pass
class CLINotFoundError(ClaudeSDKError): pass
class CLIConnectionError(ClaudeSDKError): pass
class CLIJSONDecodeError(ClaudeSDKError): pass
class ProcessError(ClaudeSDKError): pass
class ResultError(ClaudeSDKError): pass
class HookMatcher:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class ClaudeAgentOptions:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class ResultMessage:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class ClaudeSDKClient:
    def __init__(self, *, options):
        self.options=options; self.prompt=None
        self.trace={"sdk_pid":os.getpid(), "worker_env":dict(os.environ),
                    "option_env_names":sorted(options.env),
                    "credential_present":bool(options.env.get("ANTHROPIC_API_KEY")),
                    "tools":options.tools, "allowed_tools":options.allowed_tools,
                    "permission_mode":options.permission_mode,
                    "strict_mcp_config":options.strict_mcp_config,
                    "mcp_servers":options.mcp_servers, "skills":options.skills,
                    "plugins":options.plugins, "add_dirs":options.add_dirs,
                    "sandbox":options.sandbox, "cwd":str(options.cwd),
                    "claude_config_dir":options.env["CLAUDE_CONFIG_DIR"],
                    "setting_sources":options.setting_sources}
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
    async def query(self,prompt): self.prompt=prompt
    async def receive_response(self):
        self.trace["query_count"]=1
        TRACE.write_text(json.dumps(self.trace, default=str))
        mode=json.loads(self.prompt).get("_synthetic_test_mode")
        if mode == "exception": raise CLIConnectionError("synthetic disconnected")
        if mode == "is_error":
            # Defense-in-depth case: structured_output is genuinely
            # populated (not None) alongside is_error=True -- proves
            # claude_worker_runtime.py's `if is_error: outcome, evidence =
            # "failed", None` branch wins unconditionally, never because
            # structured_output merely happened to be empty.
            yield ResultMessage(structured_output={"attempt":0,"conclusion":{"text":"x","label":"FACT"}},
                                session_id="fake-claude-session",
                                is_error=True, errors=["synthetic"], api_error_status=500); return
        if mode == "empty": return
        if mode == "structured_output_none":
            yield ResultMessage(structured_output=None, session_id="fake-claude-session", is_error=False); return
        if mode == "echo_secret":
            yield ResultMessage(structured_output={"attempt":0,"text":"synthetic-worker-key-never-log"},
                                session_id="fake-claude-session", is_error=False); return
        if mode == "raise_secret":
            raise RuntimeError("provider echoed synthetic-worker-key-never-log")
        yield ResultMessage(structured_output={"attempt":0,"verdict":"PASS"},
                            session_id="fake-claude-session", is_error=False)
'''


class RealChildHarnessMixin:
    KEY = "synthetic-worker-key-never-log"

    def setUp(self):
        self._sandbox = tempfile.TemporaryDirectory()
        self.root = Path(self._sandbox.name).resolve()
        source_root = Path(__file__).resolve().parents[1]
        shutil.copytree(source_root / "orchestrator", self.root / "orchestrator")
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.root / "openai_codex").mkdir()
        (self.root / "openai_codex" / "__init__.py").write_text(_CODEX_FAKE)
        (self.root / "openai_codex" / "errors.py").write_text(_CODEX_ERRORS)
        (self.root / "claude_agent_sdk").mkdir()
        (self.root / "claude_agent_sdk" / "__init__.py").write_text(_CLAUDE_FAKE)

    def tearDown(self):
        self._sandbox.cleanup()

    def _request(self, role, *, mode=None):
        task = {"repository":{"worktree_path":str(self.worktree)}}
        if mode is not None:
            task["_synthetic_test_mode"] = mode
        return AgentInvocationRequest(
            invocation_id=f"inv-real-child-{role}", mission_id="mission-real-child",
            agent_role=role, attempt=0,
            task=task,
            requested_at="2026-08-25T00:00:00Z",
            requested_fresh_context=(role == "emma"),
        )

    def invoke_real_child(self, provider, role, *, mode=None):
        import orchestrator.provider_worker as worker
        dispatcher = self.root / "orchestrator" / "provider_worker_runtime.py"
        parent_pid = os.getpid(); worker_pid = None
        real_popen = worker.subprocess.Popen
        def launch(*args, **kwargs):
            nonlocal worker_pid
            process = real_popen(*args, **kwargs)
            worker_pid = process.pid
            os.environ["SENTINEL_AFTER_POPEN"] = "parent-only"
            return process
        try:
            with mock.patch.object(worker, "_DISPATCHER_PATH", dispatcher), mock.patch.object(
                worker.subprocess, "Popen", side_effect=launch
            ):
                result = ProviderWorkerInvoker(provider=provider, api_key=self.KEY).invoke(
                    self._request(role, mode=mode)
                )
        finally:
            os.environ.pop("SENTINEL_AFTER_POPEN", None)
        trace = json.loads((self.root / "sdk-trace.json").read_text())
        return result, trace, parent_pid, worker_pid


class RealProviderWorkerBoundaryTests(RealChildHarnessMixin, unittest.TestCase):
    def test_codex_real_child_crosses_os_boundary(self):
        result, trace, parent_pid, worker_pid = self.invoke_real_child("codex", "emilio")
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(worker_pid, parent_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["worker_env"], trusted_worker_environment())
        self.assertNotIn("SENTINEL_AFTER_POPEN", trace["worker_env"])
        self.assertEqual(trace["login_count"], 1)

    def test_claude_real_child_crosses_os_boundary(self):
        result, trace, parent_pid, worker_pid = self.invoke_real_child("claude", "emma")
        self.assertEqual(result.outcome, "completed")
        self.assertNotEqual(worker_pid, parent_pid)
        self.assertEqual(trace["sdk_pid"], worker_pid)
        self.assertEqual(trace["worker_env"], trusted_worker_environment())
        self.assertNotIn("SENTINEL_AFTER_POPEN", trace["worker_env"])

    def test_imports_are_inert(self):
        for name in ("orchestrator.provider_worker_runtime",
                     "orchestrator.adapters.codex_worker_runtime",
                     "orchestrator.adapters.claude_worker_runtime"):
            module = importlib.import_module(name)
            self.assertFalse({"invoke","run_with_api_key","create_codex",
                              "create_claude","get_authority"} & set(dir(module)))

    def test_launcher_targets_dispatcher(self):
        import inspect, orchestrator.provider_worker as worker
        source=inspect.getsource(worker.ProviderWorkerInvoker.invoke)
        self.assertIn("_DISPATCHER_PATH", source)
        self.assertNotIn("Path(__file__).resolve()", source)

    def test_positive_proof_is_real_subprocess_not_parent_runpy(self):
        source = inspect.getsource(RealChildHarnessMixin.invoke_real_child)
        self.assertNotIn("runpy", source)
        self.assertIn("subprocess", source)

    def test_supported_surface_has_worker_backed_provider_route(self):
        root = Path(__file__).resolve().parents[1]
        production = "\n".join(
            (root / "orchestrator" / name).read_text()
            for name in ("wiring.py", "agent_invocation.py", "provider_credentials.py")
        )
        self.assertIn("ProviderWorkerInvoker", production)
        self.assertNotIn("Codex(", production)
        self.assertNotIn("ClaudeSDKClient(", production)


if __name__ == "__main__":
    unittest.main()
