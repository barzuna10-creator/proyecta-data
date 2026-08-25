"""ClaudeAdapter -- orchestrator/adapters/claude_adapter.py

Implements the `AgentInvoker` Protocol (`orchestrator/agent_invocation.py`)
for Claude. Each invocation uses a fresh SDK client, a dedicated explicit
API key in an invocation-scoped isolated config environment, an
infrastructure-owned provider schema projection, exact
role tools, native-file-tool path guards, and an OS-enforced Bash sandbox.
Tests use SDK fakes only and never invoke a provider.

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
4. **Emma's `tools` and `allowed_tools` exclude `Bash` entirely**, not merely a
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
6. **Ambient Anthropic/OAuth credentials are refused.** The adapter checks
   their names only and never reads their values. A validated explicit key is
   accepted only for Emma, placed only in the Claude CLI child options, and
   paired with a fresh temporary `CLAUDE_CONFIG_DIR`/`HOME`; Emilio direct-key
   mode remains fail-closed because Emilio has Bash capability.

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
import glob
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ClaudeSDKError,
    HookMatcher,
    ProcessError,
    ResultError,
    ResultMessage,
)

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult
from orchestrator.provider_credentials import (
    ProviderCredentialError,
    require_minimized_worker_environment,
    trusted_system_temp_root,
    validate_invocation_temp_directory,
    validate_dedicated_key,
)

DEFAULT_MODEL = "claude-sonnet-5"  # ASSUMPTION -- see module docstring, point 5.
DEFAULT_TIMEOUT_SECONDS = 300.0

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"

_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}

# See module docstring point 4 -- Emma never receives Bash, only Emilio does.
_ALLOWED_TOOLS = {
    "emilio": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
    "emma": ["Read", "Glob", "Grep"],
}

_INFRASTRUCTURE_IDENTITY_FIELDS = frozenset(
    {"invocation_id", "provider", "provider_session_id", "provider_conversation_id"}
)
_NATIVE_FILESYSTEM_TOOLS = frozenset({"Read", "Edit", "Write", "Glob", "Grep"})
_NATIVE_PATH_FIELD = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Glob": "path",
    "Grep": "path",
}
_PROVIDER_UNSUPPORTED_KEYWORDS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "if", "then", "else"}
)


class ClaudeAdapterError(Exception):
    """Raised only for a pre-invocation fail-closed refusal (e.g. missing
    credential) -- never for a provider-side outcome, which is always
    reported via a returned AgentInvocationResult, never an exception."""


def _contains_secret(node: object, secret: str) -> bool:
    if isinstance(node, str):
        return secret in node
    if isinstance(node, dict):
        return any(_contains_secret(key, secret) or _contains_secret(value, secret) for key, value in node.items())
    if isinstance(node, (list, tuple)):
        return any(_contains_secret(value, secret) for value in node)
    return False


def _redact_secret(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>")


def _refs_in(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/definitions/"):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _refs_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _refs_in(item)
    return found


def _reachable_definitions(definitions: dict, entry_name: str) -> set[str]:
    seen: set[str] = set()
    frontier = {entry_name}
    while frontier:
        name = frontier.pop()
        if name in seen or name not in definitions:
            continue
        seen.add(name)
        frontier |= _refs_in(definitions[name])
    return seen


def _strip_provider_unsupported_keywords(node: object) -> object:
    if isinstance(node, dict):
        return {
            key: _strip_provider_unsupported_keywords(value)
            for key, value in node.items()
            if key not in _PROVIDER_UNSUPPORTED_KEYWORDS
        }
    if isinstance(node, list):
        return [_strip_provider_unsupported_keywords(item) for item in node]
    return node


def _load_evidence_schema(agent_role: str) -> dict:
    """Build a provider-compatible copy without weakening the canonical schema."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        full_schema = json.load(f)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    entry = dict(full_schema["definitions"][entry_name])
    entry["properties"] = {
        name: value
        for name, value in entry["properties"].items()
        if name not in _INFRASTRUCTURE_IDENTITY_FIELDS
    }
    projection_definitions = dict(full_schema["definitions"])
    projection_definitions[entry_name] = entry
    reachable = _reachable_definitions(projection_definitions, entry_name)
    projected = {
        "$schema": full_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        **entry,
        "definitions": {
            name: definition
            for name, definition in projection_definitions.items()
            if name in reachable and name != entry_name
        },
    }
    return _strip_provider_unsupported_keywords(projected)


_AMBIENT_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def _verify_no_ambient_credential_env() -> None:
    found = [name for name in _AMBIENT_CREDENTIAL_ENV_VARS if name in os.environ]
    if found:
        raise ClaudeAdapterError(
            "Refusing to invoke because ambient credential variable(s) are present: "
            + ", ".join(found)
            + ". Values were not read; ambient authentication is forbidden."
        )


@contextmanager
def _isolated_claude_config_dir():
    """Yield a private, invocation-scoped Claude home and remove it safely."""
    config_dir: Path | None = None
    setup_error: BaseException | None = None
    try:
        config_dir = Path(tempfile.mkdtemp(
            prefix="zentra-claude-config-", dir=trusted_system_temp_root()
        )).resolve()
        config_dir.chmod(0o700)
        config_dir = validate_invocation_temp_directory(config_dir)
        yield config_dir
    except BaseException as exc:
        setup_error = exc
        raise
    finally:
        if config_dir is not None:
            try:
                shutil.rmtree(config_dir)
            except BaseException as cleanup_exc:
                if setup_error is not None:
                    raise ClaudeAdapterError(
                        "Claude isolated config setup/invocation failed and cleanup also failed"
                    ) from cleanup_exc
                raise ClaudeAdapterError(
                    "Claude isolated config cleanup failed"
                ) from cleanup_exc
            if config_dir.exists():
                raise ClaudeAdapterError(
                    "Claude isolated config cleanup did not remove the directory"
                )


def _resolve_authorized_worktree(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ClaudeAdapterError("repository.worktree_path must be a non-empty absolute path")
    supplied = Path(raw_path)
    if not supplied.is_absolute():
        raise ClaudeAdapterError("repository.worktree_path must be absolute")
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ClaudeAdapterError("repository.worktree_path must resolve to an existing directory") from exc
    if resolved != supplied.absolute() or not resolved.is_dir():
        raise ClaudeAdapterError("repository.worktree_path must be a canonical, non-symlinked directory")
    return resolved


def _native_path_is_confined(raw_path: object, worktree: Path) -> bool:
    if raw_path in (None, ""):
        return True
    if not isinstance(raw_path, str):
        return False
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    try:
        resolved = (worktree / candidate).resolve(strict=False)
        resolved.relative_to(worktree)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _glob_pattern_is_confined(raw_pattern: object, base_path: Path, worktree: Path) -> bool:
    """Validate Glob.pattern separately from its optional path.

    Lexical checks reject absolute/traversal/malformed patterns. Resolving
    every current match additionally catches a relative pattern that reaches
    outside through a symlink. This is deliberately independent of Glob.path
    validation: omission of path never exempts pattern from confinement.
    """
    if not isinstance(raw_pattern, str) or not raw_pattern or "\x00" in raw_pattern:
        return False
    pattern = Path(raw_pattern)
    if pattern.is_absolute() or ".." in pattern.parts:
        return False
    try:
        base_path.resolve(strict=False).relative_to(worktree)
        for match in glob.iglob(
            str(base_path / raw_pattern), recursive=True, include_hidden=True
        ):
            Path(match).resolve(strict=False).relative_to(worktree)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _native_filesystem_guard(worktree: Path):
    async def guard(input_data: dict[str, Any], _tool_use_id: str | None, _context: Any) -> dict:
        tool_name = input_data.get("tool_name")
        if tool_name not in _NATIVE_FILESYSTEM_TOOLS:
            return {}
        tool_input = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            allowed = False
        elif tool_name == "Glob":
            raw_path = tool_input.get("path")
            path_allowed = _native_path_is_confined(raw_path, worktree)
            base_path = worktree if raw_path in (None, "") else worktree / raw_path
            pattern_allowed = _glob_pattern_is_confined(
                tool_input.get("pattern"), base_path, worktree
            )
            allowed = path_allowed and pattern_allowed
        else:
            field = _NATIVE_PATH_FIELD[tool_name]
            raw_path = tool_input.get(field)
            allowed = not (
                tool_name in {"Read", "Edit", "Write"} and raw_path in (None, "")
            ) and _native_path_is_confined(raw_path, worktree)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": (
                    "native filesystem path is confined to the authorized worktree"
                    if allowed
                    else "native filesystem path escapes or ambiguously addresses the authorized worktree"
                ),
            }
        }

    return guard


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


def _build_worker_options(
    request: AgentInvocationRequest,
    config_dir: Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ClaudeAgentOptions:
    """Pure configuration projection used only after child-side validation."""
    repository = request.task.get("repository") or {}
    worktree_path = _resolve_authorized_worktree(repository.get("worktree_path"))
    if not config_dir.is_absolute() or not config_dir.is_dir():
        raise ClaudeAdapterError("Claude isolated config directory is invalid")
    timeout_ms = str(int(timeout_seconds * 1000))
    tools = list(_ALLOWED_TOOLS[request.agent_role])
    sandbox = {
        "enabled": True, "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
        "excludedCommands": [],
        "filesystem": {"denyRead": [worktree_path.anchor],
                       "allowRead": [str(worktree_path)], "allowWrite": [],
                       "denyWrite": []},
        "network": {"allowedDomains": [], "allowUnixSockets": [],
                    "allowAllUnixSockets": False, "allowLocalBinding": False},
    }
    return ClaudeAgentOptions(
        model=model, cwd=worktree_path, tools=tools, allowed_tools=tools,
        output_format={"type": "json_schema", "schema": _load_evidence_schema(request.agent_role)},
        env={"ANTHROPIC_API_KEY": api_key, "CLAUDE_CONFIG_DIR": str(config_dir),
             "HOME": str(config_dir), "TMPDIR": str(config_dir),
             "TMP": str(config_dir), "TEMP": str(config_dir),
             "CLAUDE_CODE_MAX_RETRIES": "0", "API_TIMEOUT_MS": timeout_ms},
        settings=json.dumps({"permissions": {"allow": tools, "ask": [], "deny": []}}),
        setting_sources=[], sandbox=sandbox, permission_mode="dontAsk",
        strict_mcp_config=True, mcp_servers={}, skills=[], plugins=[], add_dirs=[],
        hooks={"PreToolUse": [HookMatcher(matcher="Read|Edit|Write|Glob|Grep",
                                           hooks=[_native_filesystem_guard(worktree_path)])]},
    )


class ClaudeAdapter:
    """Parent-facing fail-closed tombstone with no SDK-reaching methods."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        raise ClaudeAdapterError(
            "authenticated ClaudeAdapter construction is available only inside "
            "the isolated provider worker"
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
