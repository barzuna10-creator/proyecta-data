import os
import importlib
import inspect
import struct
import subprocess
import unittest
from dataclasses import asdict
from unittest import mock

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult
from orchestrator.provider_credentials import trusted_worker_environment
from orchestrator.provider_worker import (
    ProviderWorkerError,
    ProviderWorkerInvoker,
    _decode_frame,
    _encode_frame,
    _freeze_child_environment,
    _request_from_dict,
    _result_from_dict,
)


def _request():
    return AgentInvocationRequest(
        invocation_id="inv-worker-1",
        mission_id="mission-worker-1",
        agent_role="emilio",
        attempt=0,
        task={"repository": {"worktree_path": "/private/tmp/worktree"}},
        requested_at="2026-08-24T00:00:00Z",
        requested_fresh_context=False,
    )


def _result(provider="codex"):
    return AgentInvocationResult(
        invocation_id="inv-worker-1",
        outcome="completed",
        provider=provider,
        model=None,
        responded_at="2026-08-24T00:00:01Z",
        fresh_context_attested=True,
        provider_session_id=None,
        provider_conversation_id="conversation-1",
        evidence={"attempt": 0},
        error_detail=None,
    )


class _Process:
    def __init__(self, output, returncode=0):
        self.output = output
        self.returncode = returncode
        self.killed = False
        self.communicated = 0

    def communicate(self, payload=None, timeout=None):
        self.communicated += 1
        return self.output, b""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class ProviderWorkerBoundaryTests(unittest.TestCase):
    KEY = "synthetic-worker-key-never-log"

    def _invoke_with_process(self, process, provider="codex"):
        with mock.patch("orchestrator.provider_worker.subprocess.Popen", return_value=process) as popen:
            result = ProviderWorkerInvoker(provider=provider, api_key=self.KEY).invoke(_request())
        return result, popen

    def test_popen_recibe_env_construido_y_ninguna_credencial(self):
        output = _encode_frame({"result": asdict(_result())})
        process = _Process(output)
        hostile = {
            "SENTINEL_PARENT_SECRET": "never-child",
            "PATH": "/attacker/bin",
            "HOME": "/attacker/home",
            "TMPDIR": "/attacker/tmp",
            "HTTP_PROXY": "http://attacker.invalid",
            "SSL_CERT_FILE": "/attacker/ca",
            "PYTHONPATH": "/attacker/python",
            "DYLD_INSERT_LIBRARIES": "/attacker/lib",
            "OPENAI_API_KEY": "ambient",
        }
        with mock.patch.dict(os.environ, hostile, clear=True):
            result, popen = self._invoke_with_process(process)
        self.assertEqual(result, _result())
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"], trusted_worker_environment())
        self.assertNotIn(self.KEY, repr(kwargs))
        self.assertNotIn(self.KEY, " ".join(popen.call_args.args[0]))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(process.communicated, 1)

    def test_imports_parent_no_exponen_constructor_autenticado(self):
        import orchestrator.provider_credentials as credentials
        import orchestrator.provider_worker as worker

        self.assertFalse(hasattr(credentials, "_provider_worker_authority"))
        self.assertFalse(hasattr(credentials, "require_adapter_worker_authority"))
        self.assertFalse(hasattr(worker, "child_main"))
        self.assertFalse(hasattr(worker, "_child_main"))

    def test_mutacion_parent_despues_de_construir_env_no_cambia_worker(self):
        output = _encode_frame({"result": asdict(_result())})
        captured = {}

        def construct(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            os.environ["SENTINEL_AFTER_WORKER_CREATION"] = "never-worker"
            return _Process(output)

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "orchestrator.provider_worker.subprocess.Popen", side_effect=construct
        ):
            ProviderWorkerInvoker(provider="codex", api_key=self.KEY).invoke(_request())
        self.assertEqual(captured["env"], trusted_worker_environment())
        self.assertNotIn("SENTINEL_AFTER_WORKER_CREATION", captured["env"])

    def test_cada_invoke_crea_y_reapea_un_worker_nuevo(self):
        processes = [
            _Process(_encode_frame({"result": asdict(_result())})),
            _Process(_encode_frame({"result": asdict(_result())})),
        ]
        with mock.patch("orchestrator.provider_worker.subprocess.Popen", side_effect=processes) as popen:
            proxy = ProviderWorkerInvoker(provider="codex", api_key=self.KEY)
            proxy.invoke(_request())
            proxy.invoke(_request())
        self.assertEqual(popen.call_count, 2)
        self.assertEqual([p.communicated for p in processes], [1, 1])

    def test_timeout_mata_y_reapea(self):
        process = _Process(b"")
        process.communicate = mock.Mock(
            side_effect=[subprocess.TimeoutExpired("worker", 1), (b"", b"")]
        )
        with mock.patch("orchestrator.provider_worker.subprocess.Popen", return_value=process):
            with self.assertRaises(ProviderWorkerError):
                ProviderWorkerInvoker(provider="codex", api_key=self.KEY).invoke(_request())
        self.assertTrue(process.killed)
        self.assertEqual(process.communicate.call_count, 2)

    def test_cancelacion_mata_y_reapea_sin_convertirla_en_resultado(self):
        process = _Process(b"")
        process.returncode = None
        process.communicate = mock.Mock(
            side_effect=[KeyboardInterrupt(), (b"", b"")]
        )
        with mock.patch("orchestrator.provider_worker.subprocess.Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                ProviderWorkerInvoker(provider="codex", api_key=self.KEY).invoke(_request())
        self.assertTrue(process.killed)
        self.assertEqual(process.communicate.call_count, 2)

    def test_salida_malformada_truncada_duplicada_o_exit_no_cero_falla(self):
        cases = [
            _Process(b""),
            _Process(struct.pack("!I", 10) + b"{}"),
            _Process(_encode_frame({"result": asdict(_result()), "extra": {}})),
            _Process(b"", returncode=9),
        ]
        for process in cases:
            with self.subTest(output=process.output), mock.patch(
                "orchestrator.provider_worker.subprocess.Popen", return_value=process
            ), self.assertRaises(ProviderWorkerError):
                ProviderWorkerInvoker(provider="codex", api_key=self.KEY).invoke(_request())

    def test_serializacion_y_limites_fallan_cerrado(self):
        with self.assertRaises(ProviderWorkerError):
            _encode_frame({"bad": object()})
        with self.assertRaises(ProviderWorkerError):
            _decode_frame(struct.pack("!I", 4 * 1024 * 1024 + 1))
        malformed = asdict(_request())
        malformed["attempt"] = True
        with self.assertRaises(ProviderWorkerError):
            _request_from_dict(malformed)
        with self.assertRaises(ProviderWorkerError):
            _result_from_dict(asdict(_result("claude")), _request(), "codex")
        malformed_result = asdict(_result())
        malformed_result["fresh_context_attested"] = 1
        with self.assertRaises(ProviderWorkerError):
            _result_from_dict(malformed_result, _request(), "codex")

    def test_credencial_no_aparece_en_repr_errores_env_argv_o_resultado(self):
        proxy = ProviderWorkerInvoker(provider="codex", api_key=self.KEY)
        self.assertNotIn(self.KEY, repr(proxy))
        process = _Process(b"", returncode=8)
        with mock.patch("orchestrator.provider_worker.subprocess.Popen", return_value=process):
            with self.assertRaises(ProviderWorkerError) as rejected:
                proxy.invoke(_request())
        self.assertNotIn(self.KEY, str(rejected.exception))


class ProviderWorkerChildTests(unittest.TestCase):
    def test_worker_congela_env_antes_de_constructores_y_sdk_copy(self):
        original = os.environ
        canonical = trusted_worker_environment()
        try:
            os.environ = dict(canonical)
            _freeze_child_environment()
            self.assertEqual(os.environ.copy(), canonical)
            for point in ("adapter constructor", "SDK constructor", "__enter__", "connect", "before SDK copy"):
                with self.subTest(point=point), self.assertRaises(ProviderWorkerError):
                    os.environ[f"SENTINEL_{point.replace(' ', '_')}"] = "never-provider"
            self.assertEqual(os.environ.copy(), canonical)
        finally:
            os.environ = original

    def test_dispatcher_importado_es_inerte_y_sin_factory(self):
        runtime = importlib.import_module("orchestrator.provider_worker_runtime")
        self.assertFalse({"invoke", "create_codex", "create_claude", "get_authority"} & set(dir(runtime)))
        self.assertNotIn("adapter_factories", inspect.getsource(runtime))

    def test_child_rechaza_input_inesperado_sin_construir_adapter(self):
        with self.assertRaises(ProviderWorkerError):
            _request_from_dict({"unexpected": True})


if __name__ == "__main__":
    unittest.main()
