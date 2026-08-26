"""One-shot provider process boundary.

The parent launches this file with an environment constructed from constants,
then transfers exactly one credential/request pair over a private stdin pipe.
Provider SDKs therefore copy the already-isolated worker environment; mutable
state in the orchestration process is never their parent environment.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult
from orchestrator.provider_credentials import (
    ProviderCredentialError,
    trusted_worker_environment,
    validate_dedicated_key,
)

_MAX_FRAME_BYTES = 4 * 1024 * 1024
_HEADER = struct.Struct("!I")
_PROVIDERS = frozenset({"codex", "claude"})
_REQUEST_FIELDS = frozenset(AgentInvocationRequest.__dataclass_fields__)
_RESULT_FIELDS = frozenset(AgentInvocationResult.__dataclass_fields__)
_OUTCOMES = frozenset({"completed", "failed", "timeout", "invalid_output", "unavailable"})
_DISPATCHER_PATH = Path(__file__).resolve().with_name("provider_worker_runtime.py")
_OS_RUNTIME_ADDITIONS = frozenset({"__CF_USER_TEXT_ENCODING"})


class ProviderWorkerError(Exception):
    """Worker protocol/lifecycle failure; never contains credential material."""


class _FrozenWorkerEnvironment(dict[str, str]):
    """Canonical mapping SDKs may copy but no in-worker code may mutate."""

    def _deny(self, *args, **kwargs):
        raise ProviderWorkerError("provider worker environment is immutable")

    __setitem__ = _deny
    __delitem__ = _deny
    clear = _deny
    pop = _deny
    popitem = _deny
    setdefault = _deny
    update = _deny
    __ior__ = _deny


def _freeze_child_environment() -> None:
    observed = dict(os.environ)
    # macOS injects this process-local locale hint after exec even when Popen
    # receives an explicit three-entry env.  It is not copied from the parent;
    # discard the OS addition before comparing and freezing the canonical map.
    for name in _OS_RUNTIME_ADDITIONS:
        observed.pop(name, None)
    if observed != trusted_worker_environment():
        raise ProviderWorkerError("provider worker did not start with its canonical environment")
    os.environ = _FrozenWorkerEnvironment(trusted_worker_environment())


def _encode_frame(payload: dict[str, Any]) -> bytes:
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderWorkerError("provider worker payload is not serializable") from None
    if not body or len(body) > _MAX_FRAME_BYTES:
        raise ProviderWorkerError("provider worker payload exceeds the protocol limit")
    return _HEADER.pack(len(body)) + body


def _decode_frame(data: bytes) -> dict[str, Any]:
    if len(data) < _HEADER.size:
        raise ProviderWorkerError("provider worker response was truncated")
    (size,) = _HEADER.unpack(data[: _HEADER.size])
    body = data[_HEADER.size :]
    if not size or size > _MAX_FRAME_BYTES or len(body) != size:
        raise ProviderWorkerError("provider worker response has an invalid frame")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderWorkerError("provider worker response is malformed") from None
    if not isinstance(payload, dict):
        raise ProviderWorkerError("provider worker response must be an object")
    return payload


def _request_from_dict(value: Any) -> AgentInvocationRequest:
    if not isinstance(value, dict) or frozenset(value) != _REQUEST_FIELDS:
        raise ProviderWorkerError("provider worker request shape is invalid")
    request = AgentInvocationRequest(**value)
    if (
        not isinstance(request.invocation_id, str)
        or not request.invocation_id
        or not isinstance(request.mission_id, str)
        or not request.mission_id
        or request.agent_role not in ("emilio", "emma")
        or type(request.attempt) is not int
        or request.attempt not in (0, 1)
        or not isinstance(request.task, dict)
        or not isinstance(request.requested_at, str)
        or type(request.requested_fresh_context) is not bool
    ):
        raise ProviderWorkerError("provider worker request values are invalid")
    return request


def _result_from_dict(value: Any, request: AgentInvocationRequest, provider: str) -> AgentInvocationResult:
    if not isinstance(value, dict) or frozenset(value) != _RESULT_FIELDS:
        raise ProviderWorkerError("provider worker result shape is invalid")
    result = AgentInvocationResult(**value)
    if (
        result.invocation_id != request.invocation_id
        or result.provider != provider
        or result.outcome not in _OUTCOMES
        or not isinstance(result.responded_at, str)
        or type(result.fresh_context_attested) is not bool
        or (result.model is not None and not isinstance(result.model, str))
        or (result.provider_session_id is not None and not isinstance(result.provider_session_id, str))
        or (result.provider_conversation_id is not None and not isinstance(result.provider_conversation_id, str))
        or (result.evidence is not None and not isinstance(result.evidence, dict))
        or (result.error_detail is not None and not isinstance(result.error_detail, str))
        or (result.outcome == "completed") != (result.evidence is not None)
    ):
        raise ProviderWorkerError("provider worker result identity is invalid")
    return result


def _worker_environment() -> dict[str, str]:
    """Construct a new dict exclusively from infrastructure constants."""
    return trusted_worker_environment()


class ProviderWorkerInvoker:
    """AgentInvoker proxy that owns one dedicated credential in memory."""

    __slots__ = ("_provider", "_api_key", "_timeout_seconds")

    def __init__(self, *, provider: str, api_key: str, timeout_seconds: float = 600.0) -> None:
        if provider not in _PROVIDERS:
            raise ProviderWorkerError("unsupported provider worker")
        try:
            key = validate_dedicated_key(api_key, provider=provider)
        except ProviderCredentialError:
            raise ProviderWorkerError("dedicated provider credential is invalid") from None
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ProviderWorkerError("provider worker timeout is invalid")
        self._provider = provider
        self._api_key = key
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"ProviderWorkerInvoker(provider={self._provider!r}, credential=<redacted>)"

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        if not isinstance(request, AgentInvocationRequest):
            raise ProviderWorkerError("provider worker requires AgentInvocationRequest")
        payload = _encode_frame(
            {"provider": self._provider, "api_key": self._api_key, "request": asdict(request)}
        )
        command = [sys.executable, "-I", str(_DISPATCHER_PATH), "--child"]
        environment = _worker_environment()
        process = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
            output, _ = process.communicate(payload, timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
                process.communicate()
            raise ProviderWorkerError("provider worker timed out") from None
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
            raise
        if process.returncode != 0:
            raise ProviderWorkerError("provider worker failed")
        response = _decode_frame(output)
        if frozenset(response) != {"result"}:
            raise ProviderWorkerError("provider worker returned an unexpected response")
        return _result_from_dict(response["result"], request, self._provider)
