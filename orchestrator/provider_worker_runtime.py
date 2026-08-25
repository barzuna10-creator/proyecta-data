"""Exec-only provider worker dispatcher.

Normal import performs no dispatch and exposes no authenticated construction
surface.  ``provider_worker.py`` is the sole launcher of this script.

The ``runpy`` hand-off below is intentionally performed only after this file
has been exec'd as a fresh child by ``ProviderWorkerInvoker``. Arbitrary
Python execution in a compromised trusted parent is outside Zentra's threat
model; the supported API has only the worker-backed authenticated route.
"""

if __name__ == "__main__":
    import runpy
    import sys
    from dataclasses import asdict
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from orchestrator.agent_invocation import AgentInvocationResult
    from orchestrator.provider_credentials import validate_dedicated_key
    from orchestrator.provider_worker import (
        _HEADER,
        _MAX_FRAME_BYTES,
        _PROVIDERS,
        _decode_frame,
        _encode_frame,
        _freeze_child_environment,
        _request_from_dict,
        _result_from_dict,
    )

    def child_main() -> int:
        try:
            _freeze_child_environment()
            raw = sys.stdin.buffer.read(_MAX_FRAME_BYTES + _HEADER.size + 1)
            payload = _decode_frame(raw)
            if frozenset(payload) != {"provider", "api_key", "request"}:
                return 70
            provider = payload["provider"]
            if provider not in _PROVIDERS:
                return 70
            request = _request_from_dict(payload["request"])
            key = validate_dedicated_key(payload["api_key"], provider=provider)
            runtime_path = (
                Path(__file__).resolve().parent / "adapters" / f"{provider}_worker_runtime.py"
            )
            namespace = runpy.run_path(
                str(runtime_path), run_name="__main__",
                init_globals={"_ZENTRA_CHILD_REQUEST": request,
                              "_ZENTRA_CHILD_API_KEY": key},
            )
            raw_result = namespace.get("_ZENTRA_CHILD_RESULT")
            if not isinstance(raw_result, AgentInvocationResult):
                return 70
            result = _result_from_dict(asdict(raw_result), request, provider)
            sys.stdout.buffer.write(_encode_frame({"result": asdict(result)}))
            sys.stdout.buffer.flush()
            return 0
        except BaseException:
            return 70

    if sys.argv == [sys.argv[0], "--child"]:
        raise SystemExit(child_main())
    raise SystemExit(64)
