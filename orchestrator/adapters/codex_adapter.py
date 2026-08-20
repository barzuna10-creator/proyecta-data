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
   The file-based backend check (`_inspect_file_backend()`) is retained as
   a cheap, early, defense-in-depth pre-check (catches an obviously
   conflicting on-disk credential before ever touching the SDK's login
   flow) but is no longer the sole or decisive trust boundary -- `account()`
   is. No credential is ever deleted, logged out, or modified by this
   adapter, in either check. **This closes the gap the prior increment
   could not honestly close** -- no dependency beyond the two already
   authorized (`openai-codex`) was needed; the mechanism existed in the
   SDK's own public API and was missed, not absent.
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
- **Sandbox presets** (`Sandbox.workspace_write` for Emilio,
  `Sandbox.read_only` for Emma) reconfirmed by direct introspection this
  correction (`Sandbox.read_only`/`Sandbox.workspace_write`/
  `Sandbox.full_access` all present).

**Corrective cycle (Increment #16, Codex structured-output HTTP 400) --
closes the confirmed real failure a bounded isolated smoke test produced
after Emilio's dedicated Keychain-backed OpenAI credential path was
provisioned and authenticated successfully (`account().root.type ==
"apiKey"` was confirmed active): `invalid_request_error /
invalid_json_schema: Invalid schema for response_format
'codex_output_schema': In context=(), 'if' is not permitted.` --
confirmed against current official OpenAI Structured Outputs
documentation, not assumed identical to Claude's already-fixed
equivalent (`claude_adapter.py`'s own `_load_evidence_schema()`), which
uses a materially different unsupported-keyword set:**

9. **`_load_evidence_schema()` now returns a provider-facing projection**,
   not the canonical schema verbatim. Root cause: the prior version
   inlined all 13 canonical definitions unfiltered, with the requested
   entry left behind a root-level `$ref` and no `type` at the root --
   OpenAI's own documentation states the root schema object "must be an
   object, and not use anyOf" (no root-level indirection permitted),
   matching Anthropic's equivalent requirement. This adapter now: (a)
   computes the `$ref`-reachability closure from the requested entry and
   retains only those definitions; (b) promotes the requested entry's own
   schema body (`type`, `properties`, `required`, `additionalProperties`,
   etc.) directly to the returned dict's root, so the entry is no longer
   referenced via `$ref` at all and no longer belongs in the definitions
   container; (c) uses `$defs` as the definitions container key, not
   `definitions` -- OpenAI's own documentation and every example use
   `$defs` exclusively; every retained `$ref` is rewritten from
   `#/definitions/<name>` to `#/$defs/<name>` accordingly, so nothing is
   left dangling after the rename.
10. **The stripped-keyword set is Codex-specific, deliberately different
    from Claude's.** OpenAI's structured-outputs documentation lists
    `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`, `then`,
    `else` as universally unsupported (`allOf` is *not* on Anthropic's
    list, and Anthropic does support it) -- these are the only keywords
    `_CODEX_UNSUPPORTED_KEYWORDS` strips. Critically, `minLength`,
    `maxLength`, `minimum`, `maximum`, `multipleOf`, `pattern`, `format`,
    `minItems`, `maxItems` are documented as unsupported *only for
    fine-tuned models* -- Codex uses standard models, so these remain
    fully supported here and are deliberately preserved, unlike the
    equivalent (universal, not fine-tuned-only) restriction Claude's
    adapter enforces. Copying Claude's stripped-keyword set onto Codex
    would have both under-stripped (missed `allOf`) and over-stripped
    (dropped constraints OpenAI actually honors for this path) --
    disclosed here rather than assumed identical.
11. **No shared helper module was introduced.** This increment's own
    authorization is explicit that the correction stay Codex-specific and
    bounded -- the reachability/promotion/stripping logic is duplicated
    locally in this file rather than factored out alongside
    `claude_adapter.py`'s equivalent, matching this file's own long-
    standing stated reason for not sharing `_load_evidence_schema()`
    itself.
12. **Canonical enforcement is unaffected, by the same structural
    argument already established for `claude_adapter.py`.** This module
    has never imported `orchestrator.validator` or `orchestrator.chugel`,
    and reads `mission_record.schema.json` fresh via its own `json.load()`
    on every call -- there is no code path by which this projection could
    affect `orchestrator/validator.py`'s independently-loaded
    `Draft7Validator`, which remains the sole structural-enforcement layer
    before any evidence is persisted.

**Corrective cycle (Increment #16, Codex `$ref` sibling-keyword HTTP 400)
-- closes a second, distinct real failure a bounded isolated smoke test
produced after points 9-12 above were already applied and authentication
again confirmed `apiKey`: `invalid_request_error / invalid_json_schema:
Invalid schema for response_format 'codex_output_schema':
context=('properties', 'conclusion'), $ref cannot have keywords
{'description'}.` -- confirmed against the exact reconstructed
provider-facing schema, not assumed:**

13. **Every `$ref` node in the returned projection now has `$ref` as its
    only key.** Root cause, confirmed by direct reconstruction: the
    canonical `builder_evidence_entry.properties.conclusion` node is
    `{"description": "...", "$ref": "#/$defs/labeled_claim"}` --
    OpenAI's structured-outputs feature rejects any sibling keyword
    alongside `$ref` (the real error's `{'description'}` set-literal
    phrasing is itself evidence the underlying rule is general, not
    specific to `description` -- official documentation is silent on this
    exact point, so this fix deliberately does **not** special-case
    `description`: `_strip_ref_sibling_keywords()` removes *every* other
    key from any node that has `$ref`, whatever that key's name is,
    leaving the `$ref` value itself untouched). Enumerating every `$ref`
    node reachable from both `builder_evidence_entry` and
    `reviewer_evidence_entry` in the canonical schema found exactly one
    node with any sibling at all (the `conclusion` node above) -- the
    reviewer path currently has none, consistent with Emma's own real
    Claude smoke test already having succeeded, but this fix applies
    uniformly to both roles so a future canonical-schema edit can't
    silently reintroduce the same class of failure undetected.
14. **This pass runs last**, after reachability pruning, root promotion,
    the `definitions`-to-`$defs` `$ref` rewrite, and
    `_strip_codex_unsupported_keywords()` -- none of those three earlier
    passes touch a `$ref` node's sibling keys, so ordering among them is
    commutative, but running this pass last makes it the final,
    authoritative guarantee that every `$ref` node in what's actually
    returned has no siblings, regardless of what any earlier pass left
    behind.
15. **Stripping a sibling `description` changes only provider-facing
    annotation, never validation semantics.** `description` (and any
    other keyword this pass might remove) is a pure JSON Schema
    documentation/metadata keyword in every draft -- it has never
    affected structural validation. The referenced definition's own
    `type`/`required`/etc. constraints are carried entirely by the `$ref`
    target itself, untouched by this pass. `orchestrator/validator.py`'s
    independent `Draft7Validator` is unaffected for the same structural
    reason as points 9-12 above.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from openai_codex import Codex, Sandbox
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

DEFAULT_TIMEOUT_SECONDS = 300.0

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"

_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}

_SANDBOX = {"emilio": "workspace_write", "emma": "read_only"}

_CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"

# The real "completed" enum member's value, per openai_codex.generated.v2_all.TurnStatus.
_TURN_STATUS_COMPLETED = "completed"


class CodexAdapterError(Exception):
    """Raised only for a pre-invocation fail-closed refusal (missing
    credential, unresolved credential-backend trust) -- never for a
    provider-side outcome, which is always reported via a returned
    AgentInvocationResult, never an exception."""


# Documented (module docstring points 9-10) as unsupported by OpenAI's
# structured-outputs feature for standard (non-fine-tuned) models --
# deliberately NOT the same set as claude_adapter.py's
# _PROVIDER_UNSUPPORTED_KEYWORDS. Stripped from the provider-facing
# projection only, never from the canonical mission_record.schema.json.
_CODEX_UNSUPPORTED_KEYWORDS = frozenset(
    {"if", "then", "else", "allOf", "not", "dependentRequired", "dependentSchemas"}
)


def _codex_refs_in(node: object) -> set[str]:
    """Returns every `#/definitions/<name>` target `$ref`d anywhere inside
    `node`, recursively. Read-only -- never mutates `node`. Looks for the
    canonical file's own `#/definitions/...` pointer form, since that is
    what's still present before this function's caller rewrites anything."""
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
    """Returns the transitive closure of definition names reachable from
    `entry_name` via `$ref`, including `entry_name` itself."""
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
    """Returns a new structure with every `$ref` value rewritten from
    `#/definitions/<name>` to `#/$defs/<name>` -- never mutates `node`.
    Module docstring point 9(c): OpenAI's documented container key is
    `$defs`, not `definitions`."""
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
    """Returns a new structure with every key in
    `_CODEX_UNSUPPORTED_KEYWORDS` removed, recursively -- never mutates
    `node` itself. Supported constructs (`$ref`, `anyOf`, `enum`,
    `additionalProperties`, and, for Codex specifically, `minLength`/
    `minimum`/`pattern`/etc. -- see module docstring point 10) pass
    through unchanged."""
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
    """Returns a new structure where every dict containing `$ref` has
    `$ref` as its *only* key -- every other sibling key on that same node
    is removed, whatever its name (module docstring point 13; not
    special-cased to `description`). The `$ref` value itself is never
    modified. Never mutates `node`."""
    if isinstance(node, dict):
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        return {key: _strip_ref_sibling_keywords(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_strip_ref_sibling_keywords(item) for item in node]
    return node


def _load_evidence_schema(agent_role: str) -> dict:
    """Returns a provider-facing *projection* of the role's evidence
    entry, apt for OpenAI's structured-outputs feature -- not the
    canonical schema itself (module docstring points 9-13). Reads a
    fresh, independent copy of `mission_record.schema.json` on every call
    (never cached, never shared with `orchestrator/validator.py`'s own
    separately-loaded canonical schema object). Restricts the definitions
    container to exactly the `$ref`-reachability closure from the
    requested entry, excluding the entry itself (which is promoted to the
    returned dict's root instead of staying behind a `$ref`), renames the
    container from `definitions` to `$defs` and rewrites every retained
    `$ref` accordingly, recursively strips `_CODEX_UNSUPPORTED_KEYWORDS`
    -- a deliberately different set from `claude_adapter.py`'s equivalent
    (module docstring point 10) -- and finally, as the last pass (module
    docstring point 14), strips every sibling key from any `$ref` node so
    `$ref` is always that node's sole key. The canonical file on disk, and
    `orchestrator/validator.py`'s independent `Draft7Validator`, are never
    touched (module docstring points 12, 15). This duplicates
    schema-projection logic conceptually similar to claude_adapter.py's,
    kept local and Codex-specific per this increment's own authorization
    (module docstring point 11) rather than factored into a shared
    module."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        full_schema = json.load(f)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    reachable = _codex_reachable_definitions(full_schema["definitions"], entry_name)
    other_definitions = {
        name: definition
        for name, definition in full_schema["definitions"].items()
        if name in reachable and name != entry_name
    }
    projected = {
        "$schema": full_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        **full_schema["definitions"][entry_name],
        "$defs": other_definitions,
    }
    projected = _rewrite_definitions_refs_to_defs(projected)
    projected = _strip_codex_unsupported_keywords(projected)
    return _strip_ref_sibling_keywords(projected)


def _inspect_file_backend() -> str | None:
    """Cheap, early, defense-in-depth pre-check only -- see module
    docstring point 3. Returns None if the file-based credential store is
    absent or looks exclusively like an API-key credential; otherwise
    returns a string describing why it looks conflicting/unverifiable.
    ASSUMPTION: the exact schema of ~/.codex/auth.json was not
    independently confirmed against the real installed SDK -- this is a
    best-effort, conservative heuristic, not a verified parser, and is no
    longer this adapter's decisive trust boundary."""
    if not _CODEX_AUTH_FILE.exists():
        return None
    try:
        with open(_CODEX_AUTH_FILE, encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return f"~/.codex/auth.json exists but could not be read/parsed: {exc!r}"
    if not isinstance(content, dict):
        return "~/.codex/auth.json exists but its content is not a JSON object"
    suspicious_markers = ("chatgpt", "oauth", "access_token", "refresh_token", "id_token")
    serialized_keys = " ".join(str(k).lower() for k in content.keys())
    if any(marker in serialized_keys for marker in suspicious_markers):
        return (
            "~/.codex/auth.json contains a field name suggestive of a "
            "ChatGPT/OAuth-derived credential, not an API-key-only credential"
        )
    return None


def _verify_api_key_identity_active(codex: Codex) -> None:
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


def _verify_codex_host_trust_or_raise() -> None:
    """Cheap pre-check only, run before touching the SDK's login flow at
    all -- see module docstring point 3. The decisive check is
    _verify_api_key_identity_active(), run later, after login_api_key()."""
    file_backend_issue = _inspect_file_backend()
    if file_backend_issue is not None:
        raise CodexAdapterError(
            f"Codex file-based credential backend check failed closed: {file_backend_issue}"
        )


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
    """Implements AgentInvoker for Codex. Constructs a brand-new thread
    for every invoke() call via thread_start() -- never
    resumeThread()/thread reuse (PROVIDER_ROUTER_V1.md section 6)."""

    def __init__(self, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        if "OPENAI_API_KEY" not in os.environ:
            raise CodexAdapterError(
                "OPENAI_API_KEY is not set in the process environment -- refusing to "
                "construct a Codex client."
            )
        _verify_codex_host_trust_or_raise()

        try:
            outcome, evidence, error_detail, thread_id = asyncio.run(
                asyncio.wait_for(self._run(request), timeout=self._timeout_seconds)
            )
        except CodexAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 -- see _map_exception_to_outcome docstring
            outcome, error_detail = _map_exception_to_outcome(exc)
            evidence, thread_id = None, None

        return AgentInvocationResult(
            invocation_id=request.invocation_id,
            outcome=outcome,
            provider="codex",
            model=None,
            responded_at=_now(),
            fresh_context_attested=True,
            provider_session_id=None,
            provider_conversation_id=thread_id,
            evidence=evidence,
            error_detail=error_detail,
        )

    async def _run(self, request: AgentInvocationRequest) -> tuple[str, dict | None, str | None, str | None]:
        """Returns (outcome, evidence, error_detail, thread_id). Never
        raises for a legitimate non-"completed" turn outcome -- only for a
        genuine transport/protocol exception, which invoke() maps via
        _map_exception_to_outcome()."""
        sandbox_name = _SANDBOX[request.agent_role]
        repository = request.task.get("repository") or {}
        worktree_path = repository.get("worktree_path")

        with Codex() as codex:
            codex.login_api_key(os.environ["OPENAI_API_KEY"])
            _verify_api_key_identity_active(codex)

            thread = codex.thread_start(sandbox=getattr(Sandbox, sandbox_name), cwd=worktree_path)
            thread_id = getattr(thread, "id", None)
            schema = _load_evidence_schema(request.agent_role)
            result = thread.run(json.dumps(request.task), output_schema=schema)

            non_completed = _map_turn_status_to_outcome(
                getattr(result, "status", None), getattr(result, "error", None)
            )
            if non_completed is not None:
                outcome, error_detail = non_completed
                return outcome, None, error_detail, thread_id

            evidence = json.loads(result.final_response)
            return "completed", evidence, None, thread_id


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
