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
   (`DEFAULT_MODEL` below). **Updated (Increment #16 HTTP 400 corrective
   cycle, 2026-08-20): `"claude-sonnet-5"` is now confirmed, not merely
   assumed** -- verified against current official Anthropic documentation
   (the models-overview page's comparison table), which lists it verbatim
   as both the Claude API ID and Claude API alias for Claude Sonnet 5, a
   dateless pinned-snapshot identifier (the naming convention Anthropic
   uses starting with the Claude 4.6 generation). The prior HTTP 400 this
   increment diagnosed was traced to the structured-output schema shape
   (see point 11 below), not to this model identifier -- `DEFAULT_MODEL`
   is unchanged.
6. **`ANTHROPIC_API_KEY` is never read into a Python variable by this
   adapter.** The adapter only checks the key's *presence* in
   `os.environ` before ever constructing a client, and fails closed if
   absent -- the bundled CLI subprocess inherits the parent process
   environment on its own, so the adapter never needs to hold, forward,
   or format the credential value itself.
   **Superseded, see point 9 below.**

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

**Scoped credential increment (Increment #16 follow-up) -- closes the
discovered conflict between this project's own architectural principle
("Zentra's provider routing is based on API products and their own
quotas, never on Claude.ai... consumer subscriptions",
`PROVIDER_ROUTER_V1.md`'s closing section) and the operational fact that
a plain `ANTHROPIC_API_KEY` environment variable set for a real pilot is
not reliably visible to a nested SDK invocation running under a Claude
Code host session (that session withholds this specific variable name
from spawned subprocesses -- confirmed empirically, see the Increment #16
credential-boundary Discovery):**

9. **`ANTHROPIC_API_KEY` env-var presence is no longer this adapter's
   auth signal at all -- removed, not merely relaxed.** The constructor
   now requires `claude_settings_path` (no default): the path to a
   dedicated, isolated `ClaudeAgentOptions(settings=...)` file whose
   `apiKeyHelper` key names a script that resolves the real Anthropic API
   key at Claude-CLI-subprocess-invocation time (from macOS Keychain, per
   the Increment #16 Discovery's recommended machine-local setup -- not
   created or touched by this module or this increment). `_build_options()`
   also sets `setting_sources=[]`, so this SDK invocation never reads a
   user's normal `~/.claude/settings.json`/project/local settings --
   confirmed by direct inspection of the installed
   `claude-agent-sdk==0.2.141` source: `setting_sources=[]` is documented
   as "SDK isolation mode," and `settings=<path>` is a separate,
   always-loaded "flag settings" layer with the highest priority,
   independent of `setting_sources`.
10. **This adapter never reads, executes, or reveals the credential
    itself, at any point.** `_verify_claude_settings_file()` performs
    structural validation only -- the settings file exists, parses as a
    JSON object, and names a non-empty, existing, executable
    `apiKeyHelper` script -- and fails closed (`ClaudeAdapterError`) on
    any of those conditions, before `ClaudeAgentOptions` is ever
    constructed. It never opens, executes, or captures output from the
    referenced helper script -- invoking it (and thereby touching the
    real key) remains entirely the Claude CLI subprocess's own
    responsibility at actual call time, not this adapter's, matching the
    "no API key may appear in... source code... logs, or command output"
    requirement.

**Corrective cycle (Increment #16 follow-up, closing Emma's P1 finding) --
verified against the actual installed `claude-agent-sdk==0.2.141`
`_internal/transport/subprocess_cli.py`, not merely inferred:**

11. **A pre-invocation environment check now refuses to proceed if
    `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is present in this
    process's own environment.** Emma's independent review found, and I
    confirmed by direct source inspection, that the SDK spawns the Claude
    CLI subprocess with `{**dict(os.environ), **options.env}` -- i.e. the
    subprocess inherits this process's *entire* environment, and
    `ClaudeAgentOptions.env` (which this adapter only uses for
    `CLAUDE_CODE_MAX_RETRIES`/`API_TIMEOUT_MS`) never neutralizes either
    variable. Per Anthropic's documented authentication precedence,
    `ANTHROPIC_AUTH_TOKEN` (rank 2) and `ANTHROPIC_API_KEY` (rank 3) both
    outrank `apiKeyHelper` (rank 4) -- so either variable, if ambient in
    this process for any reason, would silently override the dedicated,
    isolated credential source point 9 above exists to guarantee, with no
    error and no adapter-level awareness. `_verify_no_ambient_credential_env()`
    closes this: it checks *presence only* (`"ANTHROPIC_API_KEY" in
    os.environ` / `"ANTHROPIC_AUTH_TOKEN" in os.environ`) and raises
    `ClaudeAdapterError` naming which variable(s) were found -- it never
    reads, prints, logs, copies, or unsets either variable's value, and
    never touches the process environment at all. This check runs before
    `_verify_claude_settings_file()`, so an ambient credential is refused
    even before the settings-file/helper structure is inspected.

**Corrective cycle (Increment #16 HTTP 400 follow-up) -- closes the
structured-output schema violation a real authentication smoke test
uncovered after point 11 was fixed, confirmed against current official
Anthropic structured-outputs documentation and against the exact
`mission_record.schema.json` content, not merely inferred:**

12. **`_load_evidence_schema()` now returns a provider-facing projection
    of the canonical schema, not the canonical schema itself.** The prior
    version inlined the *entire* `definitions` map from
    `mission_record.schema.json` verbatim into `output_format.schema` --
    including `minLength`/`minimum` string/numeric constraints and
    `if`/`then`/`else` conditional schemas, none of which Anthropic's
    structured-outputs feature supports (confirmed via its current
    documentation), and including definitions never referenced by the
    requested entry at all (e.g. `human_gate`, `http_check`,
    `mission_definition_version`, `actor` were sent even when requesting
    `reviewer_evidence_entry`, which references none of them). A real
    smoke test with this schema produced an HTTP 400 immediately after
    the Increment #16 credential fix resolved the prior 401 -- consistent
    with a request-shape rejection, not an authentication failure.
    `_load_evidence_schema()` now: (a) computes the transitive `$ref`
    reachability closure starting from the requested entry name and
    retains only those definitions -- valid by construction, since every
    `$ref` inside the closure necessarily resolves to something also
    inside the closure; and (b) recursively strips exactly the documented
    unsupported keywords (`minLength`, `maxLength`, `minimum`, `maximum`,
    `multipleOf`, `if`, `then`, `else`) from a fresh, independently-parsed
    copy of the schema, never mutating any shared object or the file on
    disk. Supported keywords (`$ref`, `pattern`, `enum`, `anyOf`, `allOf`,
    `additionalProperties: false`, etc.) are preserved unchanged.
    `orchestrator/validator.py`'s `Draft7Validator`, loaded independently
    from the same file at its own import time, is untouched by this
    function and remains the sole structural-enforcement layer before any
    evidence is persisted (`orchestrator/chugel.py`'s
    `record_builder_evidence()`/`record_reviewer_evidence()`) -- the
    constraints stripped from the provider-facing copy were never
    enforced by Anthropic's API in the first place (per its own
    documentation) and continue to be fully enforced canonically. This
    module's own `mission_record.schema.json` read is a fresh
    `json.load()` on every call, entirely disjoint from `validator.py`'s
    separately-loaded object -- there is no code path by which this
    projection could affect canonical validation.

**Corrective cycle (Increment #16, final HTTP 400 follow-up) -- closes a
second, distinct root cause the point-12 fix alone did not resolve,
confirmed against the literal Anthropic API error text recovered from a
real smoke test's own local CLI session transcript, not inferred:**

13. **The provider-facing schema's root now IS the requested entry's own
    schema body, not a `$ref` pointing at it.** Point 12's fix (correct on
    its own terms) still left the projection's root as
    `{"$schema": ..., "definitions": {...}, "$ref": "#/definitions/<entry>"}`
    -- every documented Anthropic structured-output example instead shows
    an inline `{"type": "object", "properties": {...}, "required": [...],
    "additionalProperties": false}` directly at the root. A real smoke
    test after the point-12 fix still returned HTTP 400; reading that
    invocation's own local session transcript (a pre-existing artifact
    the CLI itself writes, not a new request) surfaced the literal
    Anthropic error: `"API Error: 400 tools.10.custom.input_schema.type:
    Field required"` -- confirming the CLI wraps `output_format.schema`
    as a tool's `input_schema`, and Anthropic's tool-schema validation
    requires `type` directly at that root, not resolved via `$ref`.
    `_load_evidence_schema()` now spreads the requested entry's own
    definition (`type`/`properties`/`required`/`additionalProperties`/
    etc., whatever it already declares) directly into the returned
    dict's top level, and `definitions` retains only the *other*
    definitions still needed by internal `$ref`s from that root entry's
    own properties -- the entry itself is no longer referenced by `$ref`
    at all, so it no longer belongs in `definitions`. `_PROVIDER_
    UNSUPPORTED_KEYWORDS` stripping (point 12) is applied to this whole
    structure, root included, exactly as before.
"""

from __future__ import annotations

import asyncio
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

DEFAULT_MODEL = "claude-sonnet-5"  # confirmed against live docs -- see module docstring, point 5.
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


# Documented (module docstring point 12) as unsupported by Anthropic's
# structured-outputs feature -- stripped from the provider-facing schema
# projection only, never from the canonical mission_record.schema.json.
_PROVIDER_UNSUPPORTED_KEYWORDS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "if", "then", "else"}
)


def _refs_in(node: object) -> set[str]:
    """Returns every `#/definitions/<name>` target `$ref`d anywhere inside
    `node`, recursively. Read-only -- never mutates `node`."""
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
    """Returns the transitive closure of definition names reachable from
    `entry_name` via `$ref`, including `entry_name` itself. Every `$ref`
    inside the closure necessarily resolves to something also inside the
    closure, by construction."""
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
    """Returns a new structure with every key in
    `_PROVIDER_UNSUPPORTED_KEYWORDS` removed, recursively -- never mutates
    `node` itself, so the caller's own fresh, independently-parsed copy
    (and, transitively, the canonical schema file and
    `orchestrator/validator.py`'s own separately-loaded object) are
    unaffected regardless of aliasing. Supported keywords (`$ref`,
    `pattern`, `enum`, `anyOf`, `allOf`, `additionalProperties`, etc.) are
    passed through unchanged."""
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
    """Returns a provider-facing *projection* of the role's evidence entry
    -- not the canonical schema itself (module docstring points 12-13).
    Reads a fresh, independent copy of `mission_record.schema.json` on
    every call (never cached, never shared with
    `orchestrator/validator.py`'s own separately-loaded canonical schema
    object). The requested entry's own schema body (`type`, `properties`,
    `required`, `additionalProperties`, and any other keywords already
    present on it) is promoted to the *root* of the returned dict -- not
    left behind a root-level `$ref` -- because Anthropic's structured-
    output tool-schema validation requires `type` at the schema root
    directly, not resolved indirectly (module docstring point 13; the
    confirmed real-world symptom of not doing this was
    `tools.N.custom.input_schema.type: Field required`). `definitions`
    retains only the *other* definitions the entry's own `$ref`-
    reachability closure needs (never the entry itself, which is no
    longer referenced by `$ref` at all once promoted to the root) --
    dropping unreferenced definitions such as
    `human_gate`/`http_check`/`mission_definition_version`/`actor` for a
    `reviewer_evidence_entry` request. `_PROVIDER_UNSUPPORTED_KEYWORDS`
    is recursively stripped from the whole projection, root included. The
    canonical file on disk, and `orchestrator/validator.py`'s independent
    `Draft7Validator`, are never touched -- see module docstring point 12
    for why this projection cannot weaken canonical enforcement. This
    duplicates the schema-loading logic Codex's adapter also needs (see
    codex_adapter.py); no shared module is introduced for two small,
    self-contained call sites, matching this increment's file-scope
    authorization (adapters only, no new shared package module)."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        full_schema = json.load(f)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    reachable = _reachable_definitions(full_schema["definitions"], entry_name)
    other_definitions = {
        name: definition
        for name, definition in full_schema["definitions"].items()
        if name in reachable and name != entry_name
    }
    projected = {
        "$schema": full_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        **full_schema["definitions"][entry_name],
        "definitions": other_definitions,
    }
    return _strip_provider_unsupported_keywords(projected)


def _verify_claude_settings_file(path: Path) -> None:
    """Fail-closed structural check only (module docstring point 10) --
    never reads, executes, or reveals the credential itself. Confirms the
    dedicated, isolated SDK settings file exists, parses as a JSON object,
    and declares a non-empty `apiKeyHelper` pointing at an existing,
    executable script. Never invokes the helper script itself."""
    if not path.is_file():
        raise ClaudeAdapterError(
            f"Claude settings file not found at {path} -- refusing to invoke "
            "without a confirmed, dedicated apiKeyHelper configuration."
        )
    try:
        with open(path, encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeAdapterError(
            f"Claude settings file at {path} could not be read/parsed: {exc!r}"
        ) from exc
    if not isinstance(content, dict):
        raise ClaudeAdapterError(
            f"Claude settings file at {path} does not contain a JSON object"
        )
    helper = content.get("apiKeyHelper")
    if not isinstance(helper, str) or not helper.strip():
        raise ClaudeAdapterError(
            f"Claude settings file at {path} has no non-empty 'apiKeyHelper' key "
            "-- refusing to invoke without a confirmed credential-helper configuration."
        )
    helper_path = Path(helper)
    if not helper_path.is_file() or not os.access(helper_path, os.X_OK):
        raise ClaudeAdapterError(
            f"Claude settings file at {path} references apiKeyHelper {helper!r}, "
            "which does not exist or is not executable -- refusing to invoke "
            "without a confirmed, runnable credential helper."
        )


_AMBIENT_CREDENTIAL_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _verify_no_ambient_credential_env() -> None:
    """Fail-closed check only (module docstring point 11) -- presence
    only, never reads/prints/logs/copies/unsets either variable's value.
    Refuses if `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is present in
    this process's own environment: both outrank `apiKeyHelper` in
    Claude Code's documented authentication precedence, so either one
    being ambient would silently bypass the dedicated, isolated
    credential source this adapter exists to enforce."""
    found = [name for name in _AMBIENT_CREDENTIAL_ENV_VARS if name in os.environ]
    if found:
        raise ClaudeAdapterError(
            "Refusing to invoke: "
            + " and ".join(found)
            + " present in the adapter process environment -- this would "
            "outrank the dedicated apiKeyHelper settings path in Claude "
            "Code's authentication precedence. Unset it/them from this "
            "process's environment before invoking; this adapter never "
            "reads or reports the value(s) themselves."
        )


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

    def __init__(
        self,
        *,
        claude_settings_path: str | Path,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._settings_path = Path(claude_settings_path)
        self._model = model
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        _verify_no_ambient_credential_env()
        _verify_claude_settings_file(self._settings_path)

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
            settings=str(self._settings_path),
            setting_sources=[],
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
