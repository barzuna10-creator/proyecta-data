"""CodexAdapter -- orchestrator/adapters/codex_adapter.py

Implements the `AgentInvoker` Protocol (`orchestrator/agent_invocation.py`)
for Codex, per `orchestrator/PROVIDER_ROUTER_V1.md` sections 5-7 and
`orchestrator/PROVIDER_INTEGRATION_V1.md` sections 1, 3-11 (both already
committed, both already independently reviewed by Emma). As with
claude_adapter.py, nothing in this repository invokes this module yet --
the Chugel-to-adapter wiring is out of scope for this increment.

**Corrective cycle (Increment #14, closing Emma's P1/P2 findings) --
verified this time against the actual installed `openai-codex==0.147.0`
package in a disposable venv, not against fake-module assumptions or
inherited claims:**

1. **`codex.start_thread()` did not exist -- fixed to `codex.thread_start()`**,
   confirmed by direct introspection of the real `Codex` class (`dir()`
   lists `thread_start`, not `start_thread`). The prior increment's choice
   to preserve an older committed-but-wrong FACT over its own fresher,
   correct research was itself the error; this correction trusts direct
   inspection over any prior written claim, including this project's own.
2. **`thread.run(prompt, {"output_schema": schema})` used an invalid
   calling convention -- fixed to `thread.run(prompt, output_schema=schema)`**.
   The real `Thread.run()` signature is
   `run(self, input, *, approval_mode=None, cwd=None, effort=None,
   model=None, output_schema=None, personality=None, sandbox=None,
   service_tier=None, summary=None) -> TurnResult` -- everything after
   `input` is keyword-only; a second positional argument raises `TypeError`,
   confirmed by binding the real signature directly.
3. **`CODEX_TRUSTED_HOST_VERIFIED=1` (an unverifiable self-attestation
   environment variable) is removed entirely, not redesigned as a
   different flag.** This increment's fresh research found a real,
   SDK-native, programmatic mechanism that was missed previously:
   `Codex.account(self, *, refresh_token=False) -> GetAccountResponse`,
   confirmed by direct introspection to return a `GetAccountResponse`
   whose `.account` field is `Account | None`. This lets the adapter ask
   the SDK itself, after calling `login_api_key()`, what identity is
   *actually* active -- not which storage backend exists, not an
   operator's unverifiable claim, but the resolved authentication identity
   the SDK itself will use for the next call. `invoke()` now:
   a. calls `login_api_key(api_key)`;
   b. calls `account()` and requires the effective account type (see
      point 8 below for the exact, corrected access path) to be `"apiKey"`
      -- **this is the exact fail-closed check that closes the documented
      GitHub issue #2733/#3286 bug** ("switching to API-key auth does not
      take effect while a ChatGPT login is simultaneously active"): if
      that bug is present on the execution host, `login_api_key()` will
      have silently not taken effect, and `account()` will report the
      ChatGPT identity instead, which this adapter now detects and
      refuses on, before ever starting a thread;
   c. if `account()` itself raises, if no account is reported, or if the
      reported account is anything other than a confirmed `apiKey`-typed
      identity, the adapter fails closed -- "cannot reliably determine"
      and "conflicting credential found" are both refused, per
      `PROVIDER_INTEGRATION_V1.md` section 3's unchanged requirement.
   The adapter no longer examines any ambient `~/.codex/auth.json` backend.
   Every invocation instead gives the child app-server a fresh isolated
   CODEX_HOME and authenticates it only through `login_api_key()`. The
   post-login `account()` check remains the decisive trust boundary.
4. **Fake-module test fidelity**: the fake `Codex`/`Thread` classes in
   `tests/test_orchestrator_codex_adapter.py` are now written to match the
   *real* signatures exactly (verified against the installed package,
   see that file's own module docstring) -- `thread_start` (not
   `start_thread`) is defined directly on the fake class body, never
   patched in via `mock.patch.object(..., create=True)`; `Thread.run`
   only accepts `output_schema` as a keyword argument, matching the real
   keyword-only signature, so a production regression back to the old
   positional call would raise `TypeError` in the fake exactly as it would
   against the real SDK.
5. **`TurnResult.status`/`.error` are now read explicitly.** Confirmed via
   introspection: `TurnResult.status` is a real `TurnStatus` enum
   (`completed`, `interrupted`, `failed`, `in_progress`), and `.error` is a
   `TurnError | None` carrying a `message` field. A `status` other than
   `TurnStatus.completed` is now mapped to `outcome="failed"` deterministically
   (never `"completed"`) with `.error.message` folded into `error_detail`
   (free text, never read by any decision logic) -- previously the adapter
   only inspected `final_response` and never checked `status`/`error` at
   all, meaning a non-`completed` turn that happened to still carry
   *something* JSON-parseable in `final_response` could have been
   misreported as `"completed"`.
6. **`cwd` is now passed to `thread_start()`**, scoping the created thread
   to the mission's worktree exactly as `claude_adapter.py` already did --
   previously omitted here despite the real SDK accepting it directly,
   an asymmetry with the Claude adapter this correction removes.
7. **Exception mapping now dispatches on `openai-codex`'s own real,
   closed exception taxonomy** (`openai_codex.errors`: `CodexError` base,
   `TransportClosedError`, `ServerBusyError`/`RetryLimitExceededError`,
   `ParseError`/`InvalidRequestError`/`MethodNotFoundError`/
   `InvalidParamsError`/`InternalRpcError`, confirmed by reading the real
   module source directly) instead of matching substrings of an
   exception's string representation. No branch of this mapping ever
   reads `error_detail`'s content to decide an outcome -- the taxonomy
   dispatch is entirely by exception *type*.

**Second corrective cycle (Increment #14, closing Emma's independent
re-review P1 finding):**

8. **`account.type` was read directly and never existed -- fixed to
   `account.root.type`.** Direct introspection of the real, installed
   `openai-codex==0.147.0` package found `GetAccountResponse.account` is
   typed `Account | None`, and `Account` is a Pydantic
   `RootModel[ApiKeyAccount | ChatgptAccount | AmazonBedrockAccount]`
   (`class Account(RootModel[...]): root: ApiKeyAccount | ChatgptAccount |
   AmazonBedrockAccount`) -- the discriminated `.type` field lives at
   `account.root.type`, never directly on `account`. Confirmed empirically:
   `getattr(account, "type", None)` returns `None` unconditionally;
   `account.root.type` returns the real value (`"apiKey"`/`"chatgpt"`/
   `"amazonBedrock"`). The prior version of `_verify_api_key_identity_active()`
   read `account.type`, which was always `None`, so the check always
   refused -- fail-safe (it never let an unverified invocation through),
   but never actually verified anything either; CodexAdapter could not
   complete any real invocation regardless of the true credential state.
   `_verify_api_key_identity_active()` now reads `account.root.type`
   defensively (`getattr` at every step, never an unguarded attribute
   access), with an explicit `account is None` refusal and an explicit
   refusal when `.root`/`.type` cannot be resolved -- every one of
   "absent," "unparseable," "ChatGPT," "Bedrock," and "anything else"
   fails closed identically. The corresponding fake `Account`/
   `GetAccountResponse`/`ApiKeyAccount`/`ChatgptAccount`/
   `AmazonBedrockAccount` classes in
   `tests/test_orchestrator_codex_adapter.py` were rewritten to reproduce
   this exact `RootModel`/`.root` indirection -- the prior fakes stored
   `.type` directly on the account object, which is precisely the fidelity
   gap that let this bug ship undetected the first time.

Remaining disclosures, still true after these corrections:

- **Thread-creation and `run()` naming is now confirmed by direct
  introspection of the exact pinned `openai-codex==0.147.0` package**, not
  by preferring one written source over another -- this closes the
  ambiguity the prior increment could only disclose, not resolve.
- **`max_retries=0`/timeout, resolved (Increment #14, independently
  confirmed by Emma's second review, not reopened here)**: no confirmed
  constructor kwarg for either was found on `openai-codex`'s real,
  introspected API, but the actual call chain this adapter exercises
  (`Thread.run()` -> `Thread.turn()` -> `Client.turn_start()` ->
  `Client.request()` -> `Client._request_raw()`) was traced directly and
  confirmed to be a single JSON-RPC send/wait/return with no retry loop of
  any kind. A separate, real `retry_on_overload()`/
  `request_with_retry_on_overload()` mechanism does exist in the package
  (`openai_codex/retry.py`), but is never called from `thread_start`,
  `turn_start`, or any code path this adapter uses -- confirmed by
  locating every call site of `request_with_retry_on_overload` in the
  installed package (only its own definition and an async wrapper, no
  caller on this adapter's path). This adapter still additionally enforces
  its own timeout via `asyncio.wait_for()` and never itself retries a
  failed call, as a second, independent layer -- but the underlying
  concern (a hidden SDK-level retry silently turning one Chugel-authorized
  invocation into multiple provider attempts) is resolved, not merely
  bounded, on the specific path this adapter uses.
- **Isolated-home capability confinement**: every invocation validates one
  canonical non-symlink worktree, creates a private CODEX_HOME containing
  only infrastructure-owned configuration, disables ambient integrations,
  selects a role-specific permission profile, denies approval escalation,
  and removes the home on every exit path. Legacy `sandbox=` presets are not
  passed because Codex 0.147.0 gives them precedence over permission profiles.
  Emilio receives worktree read/write; Emma receives worktree read-only; both
  receive no command network. Codex may bootstrap its bundled `.system`
  skills inside the isolated home, but no ambient/user skills are inherited
  and skill search/dependency installation remain disabled.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from openai_codex.errors import (
    CodexError,
    InternalRpcError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    ParseError,
    ServerBusyError,
    TransportClosedError,
)

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult
from orchestrator.provider_credentials import (
    ProviderCredentialError,
    require_minimized_worker_environment,
    trusted_system_temp_root,
    validate_invocation_temp_directory,
    validate_dedicated_key,
)

DEFAULT_TIMEOUT_SECONDS = 300.0

_AMBIENT_CODEX_CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_ACCESS_TOKEN",
)


def _verify_no_ambient_codex_credentials() -> None:
    found = [name for name in _AMBIENT_CODEX_CREDENTIAL_VARS if name in os.environ]
    if found:
        raise CodexAdapterError(
            "refusing ambient Codex credential variable name(s): "
            + ", ".join(found)
        )

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"

_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}

# The real "completed" enum member's value, per openai_codex.generated.v2_all.TurnStatus.
_TURN_STATUS_COMPLETED = "completed"

_INFRASTRUCTURE_EVIDENCE_FIELDS = frozenset(
    {"invocation_id", "provider", "provider_session_id", "provider_conversation_id"}
)

_CODEX_UNSUPPORTED_KEYWORDS = frozenset(
    {"if", "then", "else", "allOf", "not", "dependentRequired", "dependentSchemas"}
)

_ROLE_WORKTREE_ACCESS = {"emilio": "write", "emma": "read"}

_DISABLED_CODEX_FEATURES = (
    "multi_agent",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "in_app_browser",
    "plugins",
    "plugin_sharing",
    "remote_plugin",
    "enable_mcp_apps",
    "skill_search",
    "skill_mcp_dependency_install",
)


class CodexAdapterError(Exception):
    """Raised only for a pre-invocation fail-closed refusal (missing
    credential, unresolved credential-backend trust) -- never for a
    provider-side outcome, which is always reported via a returned
    AgentInvocationResult, never an exception."""


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


def _codex_refs_in(node: object) -> set[str]:
    """Collect canonical ``#/definitions/...`` targets without mutation."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/definitions/"):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _codex_refs_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _codex_refs_in(item)
    return found


def _codex_reachable_definitions(definitions: dict, entry_name: str) -> set[str]:
    """Return the transitive definition closure, including ``entry_name``."""
    seen: set[str] = set()
    frontier = {entry_name}
    while frontier:
        name = frontier.pop()
        if name in seen or name not in definitions:
            continue
        seen.add(name)
        frontier |= _codex_refs_in(definitions[name])
    return seen


def _rewrite_definitions_refs_to_defs(node: object) -> object:
    """Return a copy with canonical definition refs rewritten for Codex."""
    if isinstance(node, dict):
        rewritten = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/definitions/"):
                rewritten[key] = "#/$defs/" + value.rsplit("/", 1)[-1]
            else:
                rewritten[key] = _rewrite_definitions_refs_to_defs(value)
        return rewritten
    if isinstance(node, list):
        return [_rewrite_definitions_refs_to_defs(item) for item in node]
    return node


def _strip_codex_unsupported_keywords(node: object) -> object:
    """Return a copy without keywords unsupported by Codex structured output."""
    if isinstance(node, dict):
        return {
            key: _strip_codex_unsupported_keywords(value)
            for key, value in node.items()
            if key not in _CODEX_UNSUPPORTED_KEYWORDS
        }
    if isinstance(node, list):
        return [_strip_codex_unsupported_keywords(item) for item in node]
    return node


def _strip_ref_sibling_keywords(node: object) -> object:
    """Return a copy where every ``$ref`` node contains only ``$ref``."""
    if isinstance(node, dict):
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        return {key: _strip_ref_sibling_keywords(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_strip_ref_sibling_keywords(item) for item in node]
    return node


def _load_evidence_schema(agent_role: str) -> dict:
    """Build a Codex-only projection while leaving the canonical schema intact.

    Infrastructure-owned identity is removed before reachability analysis,
    ensuring neither those properties nor identity-only definitions can be
    requested from the model. Canonical validation and trusted identity
    injection remain the responsibility of the PR #16/#17 layers.
    """
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        canonical_schema = json.load(f)

    full_schema = copy.deepcopy(canonical_schema)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    definitions = full_schema["definitions"]
    properties = definitions[entry_name]["properties"]
    for field_name in _INFRASTRUCTURE_EVIDENCE_FIELDS:
        properties.pop(field_name, None)

    reachable = _codex_reachable_definitions(definitions, entry_name)
    projected = {
        "$schema": full_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        **definitions[entry_name],
        "$defs": {
            name: definition
            for name, definition in definitions.items()
            if name in reachable and name != entry_name
        },
    }
    projected = _rewrite_definitions_refs_to_defs(projected)
    projected = _strip_codex_unsupported_keywords(projected)
    return _strip_ref_sibling_keywords(projected)


def _verify_api_key_identity_active(codex: object) -> None:
    """The decisive trust boundary (module docstring point 3). Must be
    called only after login_api_key() has already been called on `codex`.
    Fails closed if the SDK's own account() call cannot be made, if no
    account is reported, if the reported account cannot be interpreted, or
    if the effective identity is anything other than a confirmed
    apiKey-typed identity -- this is the real, programmatic check that
    closes the documented login_api_key()-does-not-override-an-active-
    ChatGPT-session bug.

    Corrective cycle (Increment #14, closing Emma's second-round P1
    finding): `GetAccountResponse.account` is `Account | None`, and the
    real, installed `openai-codex==0.147.0` package defines `Account` as a
    Pydantic `RootModel[ApiKeyAccount | ChatgptAccount |
    AmazonBedrockAccount]` -- confirmed by direct introspection
    (`class Account(RootModel[...]): root: ApiKeyAccount | ChatgptAccount
    | AmazonBedrockAccount`) and empirically
    (`getattr(account, "type", None)` returns `None` always;
    `account.root.type` returns the real discriminator). The prior version
    of this function read `account.type` directly, which is never present
    on the wrapper -- it always returned `None`, so this check always
    refused, regardless of the real credential state. It never permitted
    an unverified invocation through (fail-safe), but it also never
    verified anything. This version reads `account.root.type`, with an
    explicit `account is None` check and an explicit guard against a
    reported account whose `.root` cannot be interpreted, both failing
    closed rather than raising an unhandled `AttributeError`."""
    try:
        response = codex.account()
    except Exception as exc:  # noqa: BLE001 -- any failure here must fail closed
        raise CodexAdapterError(
            "codex.account() could not be called to confirm the active credential "
            f"identity -- refusing to proceed without confirmed API-key-only "
            f"authentication: {exc!r}"
        ) from exc

    account = getattr(response, "account", None)
    if account is None:
        raise CodexAdapterError(
            "codex.account() reports no active account (account is None) -- "
            "refusing to invoke without confirmed API-key-only authentication."
        )

    root = getattr(account, "root", None)
    account_type = getattr(root, "type", None)
    if account_type != "apiKey":
        raise CodexAdapterError(
            f"codex.account() reports the active identity type is {account_type!r} "
            f"(root={root!r}), not 'apiKey' -- refusing to invoke. This is the exact "
            "failure mode PROVIDER_INTEGRATION_V1.md section 3 exists to catch: "
            "login_api_key() does not guarantee API-key authentication actually took "
            "effect if a ChatGPT (or another non-API-key) session was already active "
            "on this host."
        )


def _validate_worktree_path(raw_path: object) -> Path:
    """Return one existing canonical directory, rejecting ambiguity.

    A relative path, ``..`` spelling, missing/non-directory target, or any
    symlink component is refused before an isolated home or SDK client exists.
    The exact resolved path is both the thread cwd and sole runtime workspace
    root in the role permission profile.
    """
    if not isinstance(raw_path, str) or not raw_path:
        raise CodexAdapterError("repository.worktree_path must be a non-empty string")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise CodexAdapterError("repository.worktree_path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CodexAdapterError(f"repository.worktree_path cannot be resolved: {exc!r}") from exc
    if candidate != resolved:
        raise CodexAdapterError(
            "repository.worktree_path must already be canonical and contain no symlink or traversal"
        )
    if not resolved.is_dir():
        raise CodexAdapterError("repository.worktree_path must resolve to a directory")
    return resolved


def _render_isolated_config(agent_role: str, worktree: Path) -> str:
    """Render the complete infrastructure-owned config for one invocation.

    ``json.dumps`` produces a valid TOML basic string for the canonical path.
    The empty MCP/hooks/skills tables are defense in depth; isolation from the
    user's normal config comes from the fresh CODEX_HOME itself. Codex 0.147.0
    may bootstrap its bundled ``skills/.system`` directory in that home. Those
    runtime-owned skills are not ambient/user skills; search and dependency
    installation remain disabled below.
    """
    if agent_role not in _ROLE_WORKTREE_ACCESS:
        raise CodexAdapterError(f"unsupported Codex agent role: {agent_role!r}")
    profile = f"zentra-{agent_role}"
    quoted_worktree = json.dumps(str(worktree))
    feature_lines = "\n".join(f"{name} = false" for name in _DISABLED_CODEX_FEATURES)
    return f'''default_permissions = "{profile}"
web_search = "disabled"
allow_login_shell = false

[features]
{feature_lines}

[agents]
enabled = false

[apps._default]
enabled = false

[shell_environment_policy]
inherit = "core"
ignore_default_excludes = false

[hooks]

[mcp_servers]

[skills]
config = []

[permissions.{profile}.workspace_roots]
{quoted_worktree} = true

[permissions.{profile}.filesystem]
":minimal" = "read"

[permissions.{profile}.filesystem.":workspace_roots"]
"." = "{_ROLE_WORKTREE_ACCESS[agent_role]}"

[permissions.{profile}.network]
enabled = false
'''


@contextmanager
def _isolated_codex_home(agent_role: str, worktree: Path):
    """Create, validate, yield, and reliably remove a private CODEX_HOME."""
    try:
        home = Path(tempfile.mkdtemp(
            prefix="zentra-codex-home-", dir=trusted_system_temp_root()
        ))
        home.chmod(0o700)
        home = validate_invocation_temp_directory(home)
        config_path = home / "config.toml"
        config_text = _render_isolated_config(agent_role, worktree)
        config_path.write_text(config_text, encoding="utf-8")
        config_path.chmod(0o600)
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        if parsed.get("default_permissions") != f"zentra-{agent_role}":
            raise CodexAdapterError("generated isolated Codex config failed validation")
        if set(home.iterdir()) != {config_path}:
            raise CodexAdapterError("isolated CODEX_HOME contains unexpected pre-launch state")
    except CodexAdapterError:
        if "home" in locals() and home.exists():
            try:
                shutil.rmtree(home)
            except Exception as cleanup_exc:  # noqa: BLE001
                raise CodexAdapterError(
                    f"isolated CODEX_HOME setup failed and cleanup also failed: {cleanup_exc!r}"
                ) from cleanup_exc
        raise
    except Exception as exc:  # noqa: BLE001 -- setup must fail closed
        if "home" in locals() and home.exists():
            try:
                shutil.rmtree(home)
            except Exception as cleanup_exc:  # noqa: BLE001
                raise CodexAdapterError(
                    "could not create isolated CODEX_HOME and could not remove its residue: "
                    f"setup={exc!r}, cleanup={cleanup_exc!r}"
                ) from cleanup_exc
        raise CodexAdapterError(f"could not create isolated CODEX_HOME: {exc!r}") from exc

    try:
        yield home
    finally:
        try:
            shutil.rmtree(home)
        except Exception as exc:  # noqa: BLE001 -- residual home is a security failure
            raise CodexAdapterError(
                f"could not safely remove isolated CODEX_HOME {home}: {exc!r}"
            ) from exc


def _map_turn_status_to_outcome(status, error) -> tuple[str, str] | None:
    """Returns None if status is the real 'completed' value (proceed to
    parse final_response); otherwise returns (outcome, error_detail) for
    an immediate non-completed result -- module docstring point 5."""
    status_value = getattr(status, "value", status)
    if status_value == _TURN_STATUS_COMPLETED:
        return None
    error_message = getattr(error, "message", None) if error is not None else None
    return "failed", f"Codex turn ended with status={status_value!r}, error={error_message!r}"


def _map_exception_to_outcome(exc: Exception) -> tuple[str, str]:
    """Dispatches on openai-codex's own real, closed exception taxonomy
    (module docstring point 7) -- never on substring-matching an
    exception's free-text message."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout", "adapter-level asyncio.wait_for timeout exceeded"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_output", f"final_response was not valid JSON: {exc}"
    if isinstance(exc, TransportClosedError):
        return "unavailable", str(exc)
    if isinstance(exc, ServerBusyError):  # includes RetryLimitExceededError
        return "failed", str(exc)
    if isinstance(
        exc, (ParseError, InvalidRequestError, MethodNotFoundError, InvalidParamsError, InternalRpcError)
    ):
        return "failed", str(exc)
    if isinstance(exc, CodexError):
        return "failed", str(exc)
    # Never let an unrecognized exception propagate uncaught out of
    # invoke() (PROVIDER_INTEGRATION_V1.md section 10's explicit
    # requirement).
    return "failed", f"unexpected error: {exc!r}"


class CodexAdapter:
    """Parent-facing tombstone.

    Authenticated execution moved to the exec-only child runtime.  Keeping the
    name provides a fail-closed compatibility surface while deliberately
    exposing no ``invoke`` method for ``object.__new__`` to recover.
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        raise CodexAdapterError(
            "authenticated CodexAdapter construction is available only inside "
            "the isolated provider worker"
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
