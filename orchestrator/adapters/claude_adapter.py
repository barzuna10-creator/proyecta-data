"""ClaudeAdapter -- orchestrator/adapters/claude_adapter.py

Implements the `AgentInvoker` Protocol (`orchestrator/agent_invocation.py`)
for Claude, per `orchestrator/PROVIDER_ROUTER_V1.md` sections 4/6/7 and
`orchestrator/PROVIDER_INTEGRATION_V1.md` sections 2, 4-8, 10-11 (both
already committed, both already independently reviewed by Emma). This
module performs a real Claude Agent SDK call when actually invoked. No
test in this increment calls it against a real provider, and nothing else
in this repository imports or calls it yet -- the wiring connecting
Chugel's state transitions to this adapter is out of scope for this
increment, per Increment #14 Discovery's finding that it does not exist
and per `orchestrator/CHUGEL_V1.md`'s own Evolution Path, which names that
wiring as a separate, not-yet-designed architectural decision.

Disclosures against the committed design, verified fresh for this
increment (2026-08-19) against live PyPI/documentation sources rather than
only inherited from Increment #10's research:

1. **Exception taxonomy differs from what `PROVIDER_INTEGRATION_V1.md`
   section 10's table assumed.** That table was written against the raw
   `anthropic` HTTP client's exception classes (`APIConnectionError`,
   `RateLimitError`, etc.). Live verification this increment found that
   `claude-agent-sdk` communicates with a bundled Claude Code CLI
   subprocess, not a directly-held HTTP client, and exposes its own
   taxonomy instead: `ClaudeSDKError` (base), `CLINotFoundError`,
   `CLIConnectionError`, `ProcessError` (carries `exit_code`/`stderr`),
   `ResultError` (a `ProcessError` subclass carrying `subtype`/
   `terminal_reason`/`api_error_status`), `CLIJSONDecodeError`. This
   module maps against *this* real taxonomy, not the design document's
   assumed one -- see `_map_exception_to_outcome()` below for the mapping
   and its own disclosed uncertainty about `terminal_reason`'s exact
   values (this project's own research could not enumerate them
   authoritatively).
2. **`max_retries=0` and the explicit timeout (sections 4-5) are not
   client-constructor keyword arguments here** -- because the SDK is
   subprocess-mediated, both are set via `ClaudeAgentOptions.env`
   (`CLAUDE_CODE_MAX_RETRIES="0"`, `API_TIMEOUT_MS=<ms>`), the documented
   mechanism this SDK actually exposes. An `asyncio.wait_for()` wrapper is
   used as a second, adapter-level backstop in case the env-configured
   timeout does not surface as expected -- disclosed defense-in-depth,
   not a claim that the mechanism was independently verified end-to-end
   (no real call was made this increment).
3. **Structured evidence uses `ClaudeAgentOptions(output_format={"type":
   "json_schema", "schema": ...})`**, not the "strict tool" mechanism
   `PROVIDER_ROUTER_V1.md` section 4 described as an open question to
   reconfirm before implementation. Live documentation this increment
   found `output_format` as the current, directly-documented mechanism;
   the resulting structured payload is read from
   `ResultMessage.structured_output`.
4. **Emma's `allowed_tools` excludes `Bash` entirely**, not merely a
   "bounded, read-only" variant of it. `PROVIDER_INTEGRATION_V1.md`
   section 7 anticipated "whatever bounded command-execution tool the SDK
   exposes for read-only rerun checks" without confirming one exists; this
   increment's research found no SDK-native read-only-scoped command tool
   distinct from full `Bash`. Per `PROVIDER_ROUTER_V1.md` Acceptance
   Criteria item 5 ("An implementation that grants Emma write-capable
   tools, even if she is merely instructed not to use them, does not
   satisfy this criterion"), the safe reading is to omit `Bash` for Emma
   rather than grant it under an unverified "she'll only use it read-only"
   assumption. This is more restrictive than the design's own aspirational
   text, disclosed here rather than silently narrowed.
5. **`model` and the exact `ClaudeAgentOptions` field for selecting it**
   are carried as an adapter constructor default
   (`DEFAULT_MODEL` below), not independently verified against live
   documentation this increment (search results did not surface this
   field's exact current name with certainty) -- labeled ASSUMPTION.
6. **`ANTHROPIC_API_KEY` is never read into a Python variable by this
   adapter.** The adapter only checks the key's *presence* in
   `os.environ` before ever constructing a client, and fails closed if
   absent -- the bundled CLI subprocess inherits the parent process
   environment on its own, so the adapter never needs to hold, forward,
   or format the credential value itself.

**Corrective cycle (Increment #14, closing Emma's P2 findings) -- verified
this time against the actual installed `claude-agent-sdk==0.2.141`
package in a disposable venv:**

7. **`ResultError.terminal_reason` does exist as a real instance
   attribute** -- my prior review of my own prior code (as Emma, the
   preceding independent review) concluded it did not, based on
   inspecting only `ResultError.__init__`'s *parameters*
   (`message, data=None, exit_code=None`). Direct inspection of the
   installed package's source this cycle found the real constructor body
   derives `self.terminal_reason` (and `.subtype`, `.errors`, `.result`,
   `.api_error_status`, `.session_id`) from the `data` dict argument --
   the attribute is real, just not a constructor keyword. **What actually
   needed correcting was different from what was previously diagnosed**:
   the confirmed real `terminal_reason` values, per the installed
   package's own docstring, are `"completed"`, `"max_turns"`,
   `"aborted_streaming"`, `"aborted_tools"` -- **none of which represent a
   timeout condition** (the prior code's `"timeout" in reason or "stall"
   in reason` substring guess could never have matched any real value).
   Since `ResultError` only ever fires when `is_error` was already `True`
   (see point 8), and none of its real terminal reasons distinguish a
   timeout from any other terminal condition, this adapter now maps every
   `ResultError` to `"failed"` unconditionally -- correct per
   `PROVIDER_ROUTER_V1.md` section 3's own principle that only `unavailable`/
   `timeout`/`failed` are meaningfully distinct, and a real timeout is
   already caught independently by this adapter's own `asyncio.wait_for()`
   backstop. `terminal_reason`/`subtype`/`api_error_status` are still
   folded into `error_detail` for audit, never read by any decision logic.
8. **`ResultMessage.is_error`/`.errors`/`.api_error_status` are now read
   explicitly on a message captured via the normal `receive_response()`
   loop**, not just relied upon indirectly via `structured_output`'s
   presence. The installed package's own source documents that the CLI
   "ends a failed run by emitting a `result` message with `is_error: true`
   ... and then exiting non-zero," which this SDK converts into a raised
   `ResultError` rather than a plain yielded message for that case --
   meaning `receive_response()` should not normally hand this adapter an
   `is_error=True` message without also raising. This adapter no longer
   relies on that inference alone: if a captured `ResultMessage` ever does
   have `is_error` set, it is mapped to `outcome="failed"` deterministically,
   never `"completed"`, regardless of whether `structured_output` happens
   to be non-empty -- defense-in-depth against exactly the failure mode
   Emma's review named, verified-safe-either-way rather than assumed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path

from claude_agent_sdk import (
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ProcessError,
    ResultError,
    ResultMessage,
)

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult

DEFAULT_MODEL = "claude-sonnet-5"  # ASSUMPTION -- see module docstring, point 5.
DEFAULT_TIMEOUT_SECONDS = 300.0

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"

_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}

# See module docstring point 4 -- Emma never receives Bash, only Emilio does.
_ALLOWED_TOOLS = {
    "emilio": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
    "emma": ["Read", "Glob", "Grep"],
}


class ClaudeAdapterError(Exception):
    """Raised only for a pre-invocation fail-closed refusal (e.g. missing
    credential) -- never for a provider-side outcome, which is always
    reported via a returned AgentInvocationResult, never an exception."""


def _load_evidence_schema(agent_role: str) -> dict:
    """Returns a self-contained JSON Schema for the role's evidence entry,
    keeping the full `definitions` map so internal `$ref`s (e.g. to
    `artifact_identity`, `check_result`) resolve correctly -- this
    duplicates the schema-loading logic Codex's adapter also needs
    (see codex_adapter.py); no shared module is introduced for two small,
    self-contained call sites, matching this increment's file-scope
    authorization (adapters only, no new shared package module)."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        full_schema = json.load(f)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    definitions = copy.deepcopy(full_schema["definitions"])
    properties = definitions[entry_name]["properties"]
    for field_name in (
        "invocation_id", "provider", "provider_session_id", "provider_conversation_id",
    ):
        properties.pop(field_name, None)
    return {
        "$schema": full_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        "definitions": definitions,
        "$ref": f"#/definitions/{entry_name}",
    }


def _map_exception_to_outcome(exc: Exception) -> tuple[str, str]:
    """Returns (outcome, error_detail). See module docstring point 1 for
    why this maps against claude-agent-sdk's own CLI/process exception
    taxonomy rather than raw anthropic HTTP client exceptions."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout", "adapter-level asyncio.wait_for timeout exceeded"
    if isinstance(exc, CLINotFoundError):
        return "unavailable", str(exc)
    if isinstance(exc, CLIConnectionError):
        return "unavailable", str(exc)
    if isinstance(exc, CLIJSONDecodeError):
        return "invalid_output", str(exc)
    if isinstance(exc, ResultError):
        # Confirmed real terminal_reason values (installed-package
        # docstring): "completed", "max_turns", "aborted_streaming",
        # "aborted_tools" -- none represent a timeout, so every ResultError
        # maps to "failed" unconditionally (see module docstring point 7).
        # A real timeout is caught independently by asyncio.wait_for().
        reason = getattr(exc, "terminal_reason", None)
        subtype = getattr(exc, "subtype", None)
        status = getattr(exc, "api_error_status", None)
        return "failed", f"{exc} (terminal_reason={reason!r}, subtype={subtype!r}, api_error_status={status!r})"
    if isinstance(exc, ProcessError):
        return "failed", str(exc)
    if isinstance(exc, ClaudeSDKError):
        return "failed", str(exc)
    # Never let an unrecognized exception propagate uncaught out of
    # invoke() (PROVIDER_INTEGRATION_V1.md section 10's explicit
    # requirement) -- an unexpected error is still a "failed" outcome,
    # never a crash the caller must itself guard against.
    return "failed", f"unexpected error: {exc!r}"


class ClaudeAdapter:
    """Implements AgentInvoker for Claude. Constructs a brand-new
    ClaudeSDKClient for every invoke() call and discards it at the end of
    that call -- never holds one as instance state between calls
    (PROVIDER_ROUTER_V1.md section 6)."""

    def __init__(self, *, model: str = DEFAULT_MODEL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        if "ANTHROPIC_API_KEY" not in os.environ:
            raise ClaudeAdapterError(
                "ANTHROPIC_API_KEY is not set in the process environment -- refusing to "
                "construct a Claude client. No fallback to an ambient CLI login state is "
                "ever attempted (PROVIDER_INTEGRATION_V1.md section 2)."
            )

        try:
            result_message = asyncio.run(
                asyncio.wait_for(self._run(request), timeout=self._timeout_seconds)
            )
        except Exception as exc:  # noqa: BLE001 -- deliberate: see _map_exception_to_outcome docstring
            outcome, error_detail = _map_exception_to_outcome(exc)
            return AgentInvocationResult(
                invocation_id=request.invocation_id,
                outcome=outcome,
                provider="claude",
                model=self._model,
                responded_at=_now(),
                fresh_context_attested=True,
                provider_session_id=None,
                provider_conversation_id=None,
                evidence=None,
                error_detail=error_detail,
            )

        structured_output = getattr(result_message, "structured_output", None)
        session_id = getattr(result_message, "session_id", None)

        # Module docstring point 8 -- defense-in-depth: never report
        # "completed" for a message the SDK itself flagged as an error,
        # even though the SDK is documented to normally raise ResultError
        # for this case rather than yield it as a plain message.
        if getattr(result_message, "is_error", False):
            errors = getattr(result_message, "errors", None)
            api_error_status = getattr(result_message, "api_error_status", None)
            return AgentInvocationResult(
                invocation_id=request.invocation_id,
                outcome="failed",
                provider="claude",
                model=self._model,
                responded_at=_now(),
                fresh_context_attested=True,
                provider_session_id=session_id,
                provider_conversation_id=None,
                evidence=None,
                error_detail=f"ResultMessage.is_error was True (errors={errors!r}, api_error_status={api_error_status!r})",
            )

        if structured_output is None:
            return AgentInvocationResult(
                invocation_id=request.invocation_id,
                outcome="invalid_output",
                provider="claude",
                model=self._model,
                responded_at=_now(),
                fresh_context_attested=True,
                provider_session_id=session_id,
                provider_conversation_id=None,
                evidence=None,
                error_detail="ResultMessage.structured_output was empty/None despite no exception",
            )

        return AgentInvocationResult(
            invocation_id=request.invocation_id,
            outcome="completed",
            provider="claude",
            model=self._model,
            responded_at=_now(),
            fresh_context_attested=True,
            provider_session_id=session_id,
            provider_conversation_id=None,
            evidence=structured_output,
            error_detail=None,
        )

    async def _run(self, request: AgentInvocationRequest) -> ResultMessage:
        options = self._build_options(request)
        last_result: ResultMessage | None = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(json.dumps(request.task))
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    last_result = message
        if last_result is None:
            raise ResultError("no ResultMessage received before the response stream ended")
        return last_result

    def _build_options(self, request: AgentInvocationRequest) -> ClaudeAgentOptions:
        repository = request.task.get("repository") or {}
        worktree_path = repository.get("worktree_path")
        timeout_ms = str(int(self._timeout_seconds * 1000))
        return ClaudeAgentOptions(
            model=self._model,
            cwd=worktree_path,
            allowed_tools=_ALLOWED_TOOLS[request.agent_role],
            output_format={
                "type": "json_schema",
                "schema": _load_evidence_schema(request.agent_role),
            },
            env={
                "CLAUDE_CODE_MAX_RETRIES": "0",
                "API_TIMEOUT_MS": timeout_ms,
            },
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
