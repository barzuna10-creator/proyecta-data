"""CodexCliAdapter -- zero-cost subscription-CLI AgentInvoker for Emilio.

Implements the same `AgentInvoker` Protocol (orchestrator/agent_invocation.py)
as orchestrator/adapters/codex_adapter.py, but dispatches through the
locally-installed Codex CLI binary using its own already-authenticated
ChatGPT (subscription) product login, instead of the SDK's `login_api_key()`
+ dedicated `ZENTRA_CODEX_API_KEY` path. No OpenAI API key is ever read,
constructed, or required by this module -- additional provider spend for
this adapter is $0, by construction: there is no credential this adapter
could bill against even if it wanted to.

**Why this is a separate module, not a mode of codex_adapter.py**: the two
adapters have materially different trust boundaries and must not be
conflated. codex_adapter.py's isolation model (a brand-new, empty
`CODEX_HOME` per invocation, authenticated fresh via `login_api_key()`)
is fundamentally incompatible with subscription auth -- the ChatGPT login
lives in the user's *existing*, persistent `CODEX_HOME` (default
`~/.codex`), not something this process can recreate from nothing without
either (a) copying credential files, which is explicitly prohibited, or
(b) performing an interactive login itself, which this adapter never does.
This module therefore authenticates by *not overriding* `CODEX_HOME` at
all -- the child process finds the existing ChatGPT session exactly the
way an interactive `codex` invocation would -- and instead confines the
invocation entirely through command-line flags: `-C`/`--add-dir` (worktree
scope), `-s` (sandbox policy), `--ignore-user-config` (Codex's own flag
to skip loading `$CODEX_HOME/config.toml` while auth still uses
`CODEX_HOME` -- confirmed via `codex exec --help`), `--ignore-rules`, and
explicit `--disable <feature>` flags mirroring codex_adapter.py's own
`_DISABLED_CODEX_FEATURES` (intentionally duplicated here, not imported --
see the module-level note below explaining why -- so the two adapters
must be kept in sync by hand if the disabled-feature set ever changes,
never silently by import).

Corrective cycle #3 (runtime discovery from the authorized real zero-cost
pilot): the prior version also passed `-a never`, intending to suppress
interactive approval prompts (there is no TTY on this path). The real,
installed Codex CLI (`codex-cli 0.148.0-alpha.15`) has no `-a`/
`--ask-for-approval` flag on `codex exec` at all -- `codex exec --help`
confirms this empirically -- so the prior command line failed outright
before the model ever received the task (`error: unexpected argument
'-a' found`). No replacement approval flag is needed: `codex exec` is
documented by this CLI itself as "Run Codex non-interactively" -- it is
already the non-interactive entry point, and `-s <mode>` alone (already
present) governs what the model-generated commands may do without any
interactive-approval concept in the picture. This fix removes `-a never`
outright and adds nothing in its place -- specifically not
`--dangerously-bypass-approvals-and-sandbox` (this CLI's actual unsafe
escape hatch, confirmed via the same `--help` output, and never used
here) and not `--approve-for-me` (a different behavior: routing approvals
through automatic review, not simply "there is no approval concept to
begin with" -- unnecessary since `exec` already has none). Sandbox mode
(`-s`), worktree confinement (`-C`/`--add-dir`), `--disable
<feature>`-based multi-agent suppression, and `-c agents.enabled=false`
are all unchanged by this fix.

**Disclosed, accepted limitation relative to codex_adapter.py**: because
this adapter never replaces `CODEX_HOME`, it cannot guarantee the same
from-nothing isolation the API-key adapter provides -- e.g. any global
Codex CLI update state or cached model list in the user's real
`CODEX_HOME` is still present (though `--ignore-user-config` blocks the
base config.toml, and every `--disable`/`-c` flag below still applies on
top). This is an inherent, structural consequence of using a product
login rather than a dedicated credential, not an oversight, and is
disclosed here per this codebase's existing convention (see
codex_adapter.py's own module docstring) rather than silently narrowed.

**Fail-closed authentication check**: before every single dispatch, this
adapter calls the CLI's own `codex login status` (its official,
documented status command -- the identical mechanism used during the
read-only zero-cost-architecture discovery this module implements) and
requires the reported status to say "Logged in using ChatGPT". If the
active login is ever an API key (or anything else), this adapter refuses
the dispatch before spawning `codex exec` at all -- this is the guard
against silently incurring metered API spend if the host's login state
ever changes. This module never reads `~/.codex/auth.json` or any other
credential file directly.

**Unverified against a live invocation**: this module has been exercised
only against fake CLI executables in tests/test_orchestrator_codex_cli_adapter.py
-- the exact shape of `codex exec`'s `-o`/`--output-last-message` file
content and its process exit-code semantics on a genuine ChatGPT-authenticated
run have not been empirically confirmed (no real provider task has been
executed under this authorization). This is a disclosed, concrete residual
risk for the first real pilot, not something this module claims certainty
about.

**Corrective cycle #1 (Emma's P1 finding, independence-check integrity)**:
the prior version of this module set `provider_conversation_id =
request.invocation_id` on every completed result. `invocation_id` is
orchestrator-owned (`chugel.reserve_dispatch()` generates it via
`uuid.uuid4()` and guarantees it is unique across the entire dispatch
ledger, independent of anything the provider actually did) -- using it as
a stand-in for provider identity made
`agent_invocation.py::_check_persisted_builder_independence()`
structurally incapable of ever detecting real provider-session reuse
between Emilio and Emma: two orchestrator-generated UUIDs can never
collide, so the one comparison that check performs could never fire,
regardless of what the real Codex/Claude sessions actually did. Verified
empirically (not just by inspection) by driving the real
`require_eligible_invocation()` -> `build_*_invocation_request()` ->
`consume_*_result()` chain and confirming the independence check never
raised.

This version instead attempts to capture a genuine, provider-reported
identity via `_extract_thread_id()` below, scanning `codex exec --json`'s
JSONL event stream for one of a small set of plausible key names
(`thread_id`, `session_id`, `conversation_id`) -- labeled ASSUMPTION,
since the real Codex CLI's exact event schema for this is unconfirmed
without a live invocation (ASSUMPTION, see codex_adapter.py's own
similarly-labeled uncertainties elsewhere in this codebase). If no such
key is found in any parsed event, `provider_conversation_id` is left
`None` -- never synthesized from `invocation_id`, `mission_id`, `attempt`,
or any other orchestrator-owned value. A `None` identity here means
`chugel.reserve_dispatch()`'s own precheck (requiring at least one of
`provider_session_id`/`provider_conversation_id` on the matching builder
entry) will correctly refuse Emma's dispatch until a real identity is
captured -- this is the safe, fail-closed default, not a workaround to be
routed around.

**Corrective cycle #2 (runtime discovery from the authorized minimal real
pilot, before any provider task was ever executed)**:
`_verify_chatgpt_subscription_login()` now inspects both `stdout` and
`stderr` of `codex login status`, not `stdout` only -- the real, installed
Codex CLI writes its status line to stderr, which the prior version's
stdout-only check could never see, making it structurally unable to
accept a genuine ChatGPT session through the real CLI at all. See that
function's own docstring for the full empirical finding and the exact,
still-fail-closed semantics this correction preserves.

**Corrective cycle #4 (runtime discovery from the authorized real
zero-cost pilot retry, after cycles #1-#3 landed)**: with defects #1-#3
fixed, a real Emilio/Codex dispatch genuinely completed -- correct,
passing code -- but two further real defects surfaced:

1. The model's own reported `artifact` was structurally invalid (`mode:
   "patch"` with every field null), because `git commit` inside `-s
   workspace-write` is refused by the sandbox itself ("sandbox denied
   creation of .git/index.lock"). See `_compute_uncommitted_patch_artifact()`
   and its call site in `invoke()`: the model can no longer even report
   `artifact` (stripped from its schema by `_load_evidence_schema()`),
   and this adapter computes a genuine `mode: "patch"` identity itself,
   via plain `git` commands run directly by this adapter's own trusted
   code (never inside the model's sandbox, never a commit, never a
   relaxed sandbox mode) -- from whatever the model's edits actually left
   on disk.
2. The model's own reported `"attempt"` field did not match the request
   (it guessed `1` for a request that was actually attempt `0`). See
   `_load_evidence_schema()`'s `attempt` parameter: the schema now
   constrains `attempt` to `{"const": <the exact requested value>}`,
   so `codex exec`'s own schema validation rejects any other value before
   it ever reaches this adapter. `agent_invocation.py`'s existing
   `AttemptNumberMismatch` fail-closed check is unchanged and untouched --
   this fix prevents the mismatch from occurring, it does not relax the
   check that catches it.

Neither fix touches sandbox mode, worktree confinement, `multi_agent`
suppression, `agents.enabled=false`, authentication, or independence
enforcement -- see this module's other corrective-cycle notes above,
all still intact.

**Corrective cycle #5 (runtime discovery from a second authorized real
zero-cost pilot retry, after cycle #4 landed)**: with the artifact/attempt
fixes from cycle #4 in place, the real Codex dispatch failed again --
every single time, not intermittently -- with an uninformative `codex
exec exited with code 1: ` (empty stderr). Two further fixes:

1. `_load_evidence_schema()`'s `attempt` override shipped in cycle #4 as
   a bare `{"const": attempt}`. The real model backend behind `codex exec
   --output-schema` requires every property to also declare a `"type"`
   key -- confirmed empirically via a raw replay of this adapter's exact
   command line, which surfaced the real 400 error: "schema must have a
   'type' key." Fixed to `{"type": "integer", "const": attempt}` -- the
   `const` constraint is exactly as authoritative and exactly as narrow
   as before; `AttemptNumberMismatch` in `agent_invocation.py` is
   completely untouched.
2. See `_extract_turn_failure_message()`: the real 400 error above was
   never visible in stderr at all -- it was a `"type": "error"`/
   `"turn.failed"` event on the `--json` stdout stream, with
   `process.returncode` sometimes 0 and sometimes nonzero depending on
   how the failure occurred. This function extracts only that event's own
   `message` field (the provider's own safe, user-facing error text) and
   appends it to `error_detail` on both the nonzero-exit and
   no-output-file paths -- never the raw event, never the prompt, never
   any credential or auth material, none of which this function reads."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from orchestrator.agent_invocation import AgentInvocationRequest, AgentInvocationResult
from orchestrator.provider_credentials import (
    trusted_system_temp_root,
    validate_invocation_temp_directory,
)

DEFAULT_TIMEOUT_SECONDS = 600.0

_ROLE_SANDBOX_MODE = {"emilio": "workspace-write", "emma": "read-only"}
_ROLE_WORKTREE_ACCESS = {"emilio": "write", "emma": "read"}

_KNOWN_CLI_LOCATIONS = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "/Applications/ChatGPT 2.app/Contents/Resources/codex",
)

# Deliberately duplicated from orchestrator/adapters/codex_adapter.py, not
# imported: that module imports the real `openai_codex` SDK package at
# module level, which this CLI-only adapter has no need for and must not
# require -- the whole point of a zero-cost CLI adapter is to work in an
# environment that has the Codex CLI installed but not the paid-API SDK
# package. Duplication risk is low: this is pure JSON-Schema projection
# logic derived only from the canonical schema file and small static
# tuples, not credential or trust-boundary logic.

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"
_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}
_INFRASTRUCTURE_EVIDENCE_FIELDS = frozenset(
    {"invocation_id", "provider", "provider_session_id", "provider_conversation_id"}
)
_CODEX_UNSUPPORTED_KEYWORDS = frozenset(
    {"if", "then", "else", "allOf", "not", "dependentRequired", "dependentSchemas"}
)
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


class CodexCliAdapterError(Exception):
    """Pre-invocation fail-closed refusal specific to the subscription-CLI
    path (CLI not found, login status not confirmed ChatGPT, malformed
    constructor arguments, invalid worktree) -- never raised for a
    provider-side outcome, which is always reported via a returned
    AgentInvocationResult."""


def _codex_refs_in(node: object) -> set[str]:
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
    if isinstance(node, dict):
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        return {key: _strip_ref_sibling_keywords(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_strip_ref_sibling_keywords(item) for item in node]
    return node


def _load_evidence_schema(agent_role: str, *, attempt: int) -> dict:
    """Build a Codex-only projection while leaving the canonical schema
    intact -- identical logic to codex_adapter.py's own function of the
    same name, duplicated here (see module-level note above), except for
    the `attempt` parameter added in corrective cycle #4 (see below) --
    that divergence is deliberate and disclosed, not an oversight; the two
    copies' shared JSON-schema-projection logic otherwise remains in sync.

    Corrective cycle #4, requirement 2 (runtime discovery from the
    authorized real zero-cost pilot retry): the canonical schema's
    `attempt` property is `{"enum": [0, 1]}` -- valid for either schema
    attempt, since the same `builder_evidence_entry` definition is reused
    for both. Nothing in the prior version of this function told the
    model which of the two values this *particular* invocation actually
    requires; the model has to guess, and a real dispatch demonstrated it
    can guess wrong (it reported `"attempt": 1` for a request that was
    actually attempt 0). `agent_invocation.py`'s own
    `_augmented_completed_evidence()` already fails closed on exactly this
    (`AttemptNumberMismatch` when `evidence["attempt"] != request.attempt`)
    -- that check is correct and is left completely unchanged here. This
    fix instead prevents the guess from ever being wrong in the first
    place: the schema handed to the model via `--output-schema` now
    constrains `attempt` to `{"const": attempt}` -- the exact, authoritative
    value for this invocation -- so `codex exec`'s own schema validation
    of the model's structured output rejects any other value before it
    ever reaches this adapter, let alone `agent_invocation.py`. This is
    strictly narrowing (a `{"const": N}` accepts a subset of what
    `{"enum": [0, 1]}` accepted), never a relaxation, and touches no other
    property.

    Corrective cycle #5 (runtime discovery from the second authorized
    real zero-cost pilot retry): the cycle #4 fix above shipped as a bare
    `{"const": attempt}`, with no `"type"` key. Plain JSON Schema permits
    that (`const` alone fully determines the value, `type` is redundant
    information), but the real model backend behind `codex exec
    --output-schema` is stricter than plain JSON Schema validation -- it
    rejected *every* real dispatch outright with a 400 error: "Invalid
    schema for response_format 'codex_output_schema': In
    context=('properties', 'attempt'), schema must have a 'type' key."
    (confirmed empirically via a raw replay of this adapter's exact
    command line against the real installed CLI). This fix adds
    `"type": "integer"` alongside the existing `"const": attempt` --
    `{"type": "integer", "const": attempt}` -- satisfying the backend's
    stricter requirement while the `const` constraint remains exactly as
    authoritative and exactly as narrow as before. `AttemptNumberMismatch`
    in `agent_invocation.py` remains completely unchanged.

    Corrective cycle #4, requirement 1: for `agent_role == "emilio"`, the
    `artifact` property (and its `required` entry) is also stripped from
    the schema handed to the model -- see this module's `invoke()` and
    `_compute_uncommitted_patch_artifact()` for why: the model cannot
    reliably report its own artifact identity from inside
    `-s workspace-write` (it cannot create a real git commit there), so
    this adapter computes the real one itself, after the model's process
    exits, instead of trusting a model-reported value."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        canonical_schema = json.load(f)

    full_schema = copy.deepcopy(canonical_schema)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    definitions = full_schema["definitions"]
    properties = definitions[entry_name]["properties"]
    for field_name in _INFRASTRUCTURE_EVIDENCE_FIELDS:
        properties.pop(field_name, None)
    if "attempt" in properties:
        properties["attempt"] = {"type": "integer", "const": attempt}
    if agent_role == "emilio":
        properties.pop("artifact", None)
        required = definitions[entry_name].get("required")
        if isinstance(required, list) and "artifact" in required:
            required.remove("artifact")

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


def _validate_worktree_path(raw_path: object) -> Path:
    """Identical logic to codex_adapter.py's own function of the same
    name, duplicated here (see module-level note above)."""
    if not isinstance(raw_path, str) or not raw_path:
        raise CodexCliAdapterError("repository.worktree_path must be a non-empty string")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise CodexCliAdapterError("repository.worktree_path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CodexCliAdapterError(f"repository.worktree_path cannot be resolved: {exc!r}") from exc
    if candidate != resolved:
        raise CodexCliAdapterError(
            "repository.worktree_path must already be canonical and contain no symlink or traversal"
        )
    if not resolved.is_dir():
        raise CodexCliAdapterError("repository.worktree_path must resolve to a directory")
    return resolved


def _discover_codex_cli(explicit_path: str | None) -> str:
    """Locate the `codex` executable without assuming a fixed install
    location -- `shutil.which()` first (the normal case once installed
    on PATH), then a small set of known macOS bundle locations (the
    Codex CLI ships inside the ChatGPT desktop app and is not always
    placed on PATH by that installer). Fails closed if none is found."""
    if explicit_path:
        if not Path(explicit_path).is_file():
            raise CodexCliAdapterError(f"explicit codex CLI path does not exist: {explicit_path!r}")
        return explicit_path
    found = shutil.which("codex")
    if found:
        return found
    for candidate in _KNOWN_CLI_LOCATIONS:
        if Path(candidate).is_file():
            return candidate
    raise CodexCliAdapterError(
        "codex CLI executable could not be located on PATH or in known install locations"
    )


_CHATGPT_LOGIN_STATUS_TEXT = "Logged in using ChatGPT"


def _verify_chatgpt_subscription_login(cli_path: str) -> None:
    """Fail closed unless `codex login status` -- the CLI's own official,
    documented status command -- confirms a ChatGPT (subscription) login.
    Never inspects ~/.codex/auth.json or any other credential file; never
    extracts or logs a token. If the reported status is an API key (or
    anything else), refuses before `codex exec` is ever spawned -- the
    hard boundary keeping this adapter's real-world provider spend at $0
    regardless of what the host's login state later becomes.

    Corrective cycle #2 (runtime discovery from the authorized minimal
    real pilot, before any provider task was executed): the real,
    installed Codex CLI writes its status line to **stderr**, not stdout
    (`codex login status` -> returncode 0, stdout '', stderr 'Logged in
    using ChatGPT\\n') -- confirmed empirically, not merely inferred; a
    terminal's normal display of a foreground process interleaves both
    streams, which is why manual interactive testing did not reveal this.
    The prior version of this function inspected `result.stdout` only,
    so it could never accept a genuine ChatGPT login through this real
    CLI at all -- it did not weaken the check, but it made the intended
    "accept a real ChatGPT session" branch structurally unreachable.

    This version checks both streams (either one carrying the exact
    expected status text is accepted -- CLI versions that emit it on
    stdout instead of stderr, or vice versa, both still work), but this
    is strictly additive, not a loosening: `returncode == 0` is checked
    explicitly and is necessary but never sufficient on its own (a
    zero-exit status command with output matching neither stream still
    refuses); the exact status text is still required verbatim in
    whichever stream carries it; a nonzero exit, empty output on both
    streams, an API-key status, or any unrecognized status text all
    still fail closed identically to before."""
    try:
        result = subprocess.run(
            [cli_path, "login", "status"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexCliAdapterError(f"could not query codex login status: {exc!r}") from exc
    stdout_text = (result.stdout or "").strip()
    stderr_text = (result.stderr or "").strip()
    if result.returncode != 0:
        raise CodexCliAdapterError(
            f"codex login status exited with code {result.returncode} -- refusing "
            f"to dispatch (stdout={stdout_text!r}, stderr={stderr_text!r})"
        )
    if _CHATGPT_LOGIN_STATUS_TEXT not in stdout_text and _CHATGPT_LOGIN_STATUS_TEXT not in stderr_text:
        raise CodexCliAdapterError(
            "codex login status does not confirm an active ChatGPT subscription "
            "login -- refusing to dispatch under the zero-cost subscription CLI "
            f"adapter (stdout={stdout_text!r}, stderr={stderr_text!r})"
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Candidate key names for a genuine Codex-reported thread/session identity
# inside a `codex exec --json` JSONL event object. ASSUMPTION (unconfirmed
# without a live invocation): the real event schema may use a different
# name, nest it under a sub-object, or not expose one to `exec` at all.
# Checked in this fixed priority order; the first present string value
# wins. Never invents a value when none of these keys are found.
_CODEX_THREAD_ID_KEYS = ("thread_id", "session_id", "conversation_id")


def _extract_thread_id(json_lines: list[str]) -> str | None:
    """Scan `codex exec --json`'s JSONL stdout for a genuine, provider-
    reported thread/session identity. Returns None (never a guess, never
    derived from anything orchestrator-owned) if no line parses as an
    object carrying one of `_CODEX_THREAD_ID_KEYS`."""
    for line in json_lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for key in _CODEX_THREAD_ID_KEYS:
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
    return None


_TURN_FAILURE_EVENT_TYPES = frozenset({"error", "turn.failed"})
_TURN_FAILURE_MESSAGE_MAX_CHARS = 2000


def _extract_turn_failure_message(json_lines: list[str]) -> str | None:
    """Corrective cycle #5, requirement 4: `codex exec --json` can report
    a model-turn failure (e.g. a structured-output/schema rejection from
    the provider backend) as a `"type": "error"` or `"type": "turn.failed"`
    event on **stdout**, with `process.returncode == 0` and no stderr
    output at all -- confirmed empirically: a real dispatch failed this
    way, and the adapter's prior `error_detail` was an uninformative
    'codex exec exited with code 1: ' (empty stderr), while the real,
    actionable reason ("schema must have a 'type' key") was sitting in
    this JSONL stream the whole time, unread.

    Returns the last matching event's own `message` string field (an
    `"error"` event's top-level `message`, or a `"turn.failed"` event's
    nested `error.message`) -- never the raw event object, never any
    other field. Only ever surfaces text the provider itself already
    generated as its own safe, user-facing error message; never the
    original prompt, `request.task`, environment variables, or any
    credential/auth material, none of which this function ever reads.
    Returns None (never a guess) if no such event is found. Truncated to
    `_TURN_FAILURE_MESSAGE_MAX_CHARS`, matching this module's existing
    stderr-truncation convention elsewhere."""
    message: str | None = None
    for line in json_lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") not in _TURN_FAILURE_EVENT_TYPES:
            continue
        if event["type"] == "error":
            candidate = event.get("message")
        else:
            error_obj = event.get("error")
            candidate = error_obj.get("message") if isinstance(error_obj, dict) else None
        if isinstance(candidate, str) and candidate:
            message = candidate
    return message[:_TURN_FAILURE_MESSAGE_MAX_CHARS] if message is not None else None


_GIT_DIFF_TIMEOUT_SECONDS = 30.0


def _compute_uncommitted_patch_artifact(worktree: Path) -> tuple[dict | None, str | None]:
    """Corrective cycle #4, requirement 1 (runtime discovery from the
    authorized real zero-cost pilot retry): Emilio's real dispatch
    completed with genuinely correct, passing code, but the model's own
    reported `artifact` was structurally invalid (`mode: "patch"` with
    every one of `patch_path`/`patch_sha256`/`patch_byte_size` null) --
    because `git commit` inside `-s workspace-write` was refused by the
    sandbox itself ("sandbox denied creation of .git/index.lock"). The
    schema's `artifact_identity` definition (mode "commit" or "patch")
    requires either a real commit or a real, fully-populated patch
    identity -- there is no third option, and the model has no way to
    produce either one from inside this sandbox on its own.

    Rather than relaxing the sandbox (never `danger-full-access`, never
    `--dangerously-bypass-approvals-and-sandbox`) or asking the model to
    attempt a commit it structurally cannot make, this adapter computes a
    genuine `mode: "patch"` artifact itself, entirely outside the model's
    sandboxed process, after `codex exec` has already exited: it runs
    plain `git` commands directly (not through the CLI's sandbox at all --
    this is the same trust level this adapter already has to write
    `output_schema.json`/read `last_message.json` in `tmp_dir`, not a new
    privilege), reflecting only what the model's own file edits actually
    left on disk -- never anything the model merely claimed.

    `git add -N -- .` (intent-to-add, staging no content -- only makes
    new/untracked files visible to `git diff`) followed by `git diff
    --binary` (raw bytes, so a binary file added by the model is captured
    correctly) followed by `git reset` (drops the intent-to-add index
    entries; the working tree is never touched by any of these three
    commands) -- the same technique already used elsewhere in this
    project to freeze an uncommitted patch's identity without creating a
    commit. If the resulting diff is empty, that is treated as a genuine
    failure (the model claimed completed work but left no real change
    behind), not synthesized into a fake non-empty artifact.

    Returns `(artifact, None)` on success or `(None, error_detail)` on
    failure -- exactly one is non-None, mirroring `_result()`'s own
    outcome/error_detail shape so callers can return a "failed" result
    directly from the error_detail string without inventing a second
    error-reporting convention.

    Disclosed, accepted limitation: the patch file this writes lives in
    its own directory under `trusted_system_temp_root()`, deliberately
    *not* the per-invocation `tmp_dir` `invoke()` already deletes in its
    `finally` block -- a `mode: "commit"` artifact's `commit_sha` survives
    forever in the repository's object store; a `mode: "patch"` artifact
    needs the same durability so a later Emma dispatch (or a human) can
    still read `patch_path`, so this function intentionally does not
    clean up after itself. This adapter has no mission-scoped durable
    artifact storage location to write into instead (no such location
    exists anywhere in this codebase today, for any adapter) -- inventing
    one is out of this corrective cycle's authorized scope ("fix only the
    two defects demonstrated"). Over many missions/attempts this
    accumulates directories under the system temp root; that is a known,
    disclosed follow-up, not silently omitted."""
    try:
        add_result = subprocess.run(
            ["git", "-C", str(worktree), "add", "-N", "--", "."],
            capture_output=True, timeout=_GIT_DIFF_TIMEOUT_SECONDS,
        )
        if add_result.returncode != 0:
            return None, (
                "git add -N failed while computing the uncommitted patch artifact: "
                + add_result.stderr.decode("utf-8", "replace").strip()[:2000]
            )
        diff_result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--binary"],
            capture_output=True, timeout=_GIT_DIFF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"could not compute the uncommitted patch artifact via git: {exc!r}"
    finally:
        subprocess.run(
            ["git", "-C", str(worktree), "reset"],
            capture_output=True, timeout=_GIT_DIFF_TIMEOUT_SECONDS,
        )
    if diff_result.returncode != 0:
        return None, (
            "git diff failed while computing the uncommitted patch artifact: "
            + diff_result.stderr.decode("utf-8", "replace").strip()[:2000]
        )
    patch_bytes = diff_result.stdout
    if not patch_bytes:
        return None, (
            "codex exec reported completed work, but no uncommitted change exists "
            "in the worktree to capture as a patch artifact"
        )
    artifact_dir_raw = tempfile.mkdtemp(prefix="zentra-codex-cli-artifact-", dir=trusted_system_temp_root())
    artifact_dir = Path(artifact_dir_raw)
    artifact_dir.chmod(0o700)
    artifact_dir = validate_invocation_temp_directory(artifact_dir)
    patch_path = artifact_dir / "artifact.patch"
    patch_path.write_bytes(patch_bytes)
    patch_path.chmod(0o600)
    artifact = {
        "mode": "patch",
        "commit_sha": None,
        "patch_path": str(patch_path),
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "patch_byte_size": len(patch_bytes),
    }
    return artifact, None


def _result(request: AgentInvocationRequest, outcome: str, error_detail: str | None,
            evidence: dict | None = None, conversation_id: str | None = None) -> AgentInvocationResult:
    return AgentInvocationResult(
        invocation_id=request.invocation_id, outcome=outcome, provider="codex",
        model=None, responded_at=_now(), fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=conversation_id,
        evidence=evidence, error_detail=error_detail,
    )


class CodexCliAdapter:
    """AgentInvoker dispatching to the Codex CLI's existing ChatGPT
    subscription login. See module docstring for the full trust-boundary
    disclosure. `cli_path`, if omitted, is auto-discovered."""

    __slots__ = ("_cli_path", "_timeout_seconds")

    def __init__(self, *, cli_path: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._cli_path = _discover_codex_cli(cli_path)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise CodexCliAdapterError("timeout_seconds must be a positive number")
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"CodexCliAdapter(cli_path={self._cli_path!r})"

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        if not isinstance(request, AgentInvocationRequest):
            raise CodexCliAdapterError("CodexCliAdapter requires an AgentInvocationRequest")
        if request.agent_role not in _ROLE_WORKTREE_ACCESS:
            raise CodexCliAdapterError(f"unsupported Codex agent role: {request.agent_role!r}")

        repository = request.task.get("repository") or {}
        worktree = _validate_worktree_path(repository.get("worktree_path"))

        # Fail closed on authentication mismatch before spawning anything.
        _verify_chatgpt_subscription_login(self._cli_path)

        prompt = json.dumps(request.task)

        tmp_dir_raw = tempfile.mkdtemp(prefix="zentra-codex-cli-", dir=trusted_system_temp_root())
        tmp_dir = Path(tmp_dir_raw)
        try:
            try:
                tmp_dir.chmod(0o700)
                tmp_dir = validate_invocation_temp_directory(tmp_dir)
                schema_path = tmp_dir / "output_schema.json"
                schema_path.write_text(
                    json.dumps(_load_evidence_schema(request.agent_role, attempt=request.attempt)),
                    encoding="utf-8",
                )
                schema_path.chmod(0o600)
                last_message_path = tmp_dir / "last_message.json"

                command = [
                    self._cli_path, "exec",
                    "-C", str(worktree),
                    "--add-dir", str(worktree),
                    "-s", _ROLE_SANDBOX_MODE[request.agent_role],
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--json",
                    "--output-schema", str(schema_path),
                    "-o", str(last_message_path),
                ]
                for feature in _DISABLED_CODEX_FEATURES:
                    command += ["--disable", feature]
                command += ["-c", "agents.enabled=false", "-"]

                process = None
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=str(worktree), close_fds=True, start_new_session=True,
                    )
                    stdout, stderr = process.communicate(prompt.encode("utf-8"), timeout=self._timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    return _result(request, "timeout", "codex exec timed out")
                except OSError as exc:
                    return _result(request, "unavailable", f"could not launch codex CLI: {exc!r}")

                stdout_lines = stdout.decode("utf-8", "replace").splitlines()

                if process.returncode != 0:
                    detail = (
                        f"codex exec exited with code {process.returncode}: "
                        f"{stderr.decode('utf-8', 'replace').strip()[:2000]}"
                    )
                    turn_failure = _extract_turn_failure_message(stdout_lines)
                    if turn_failure is not None:
                        detail += f" | provider turn failure: {turn_failure}"
                    return _result(request, "failed", detail)

                # `--json` events (stdout) are scanned only for a genuine
                # provider thread/session id -- see _extract_thread_id()'s
                # own ASSUMPTION disclosure. The schema-constrained final
                # answer is read from `-o` (--output-last-message), exactly
                # as before -- these are two independent outputs of the same
                # invocation, not a fallback of one for the other.
                thread_id = _extract_thread_id(stdout_lines)

                if not last_message_path.exists():
                    detail = "codex exec produced no last-message output file"
                    turn_failure = _extract_turn_failure_message(stdout_lines)
                    if turn_failure is not None:
                        detail += f" (provider turn failure: {turn_failure})"
                    return _result(request, "invalid_output", detail)
                try:
                    raw = last_message_path.read_text(encoding="utf-8")
                except OSError as exc:
                    return _result(request, "invalid_output", f"could not read codex exec output: {exc!r}")
                try:
                    evidence = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return _result(request, "invalid_output", f"codex exec final message was not valid JSON: {exc}")
                if not isinstance(evidence, dict):
                    return _result(request, "invalid_output", "codex exec final message was not a JSON object")

                # Corrective cycle #4, requirement 1: `artifact` is never
                # taken from the model (stripped from the schema in
                # _load_evidence_schema() for agent_role == "emilio", so the
                # model cannot even include one) -- this adapter computes
                # the genuine one itself from what actually changed on disk.
                if request.agent_role == "emilio":
                    artifact, artifact_error = _compute_uncommitted_patch_artifact(worktree)
                    if artifact_error is not None:
                        return _result(request, "invalid_output", artifact_error)
                    evidence["artifact"] = artifact

                # provider_conversation_id: only a genuine, provider-reported
                # thread id (or None, if `--json` carried none) -- never
                # request.invocation_id or any other orchestrator-owned
                # value (Emma's P1 corrective cycle -- see module docstring).
                # A None here is not swallowed or worked around anywhere:
                # chugel.reserve_dispatch()'s own precheck will correctly
                # refuse Emma's dispatch until a real identity is captured.
                return _result(request, "completed", None, evidence=evidence, conversation_id=thread_id)
            except CodexCliAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001 -- never let an unrecognized
                # exception propagate uncaught out of invoke() (Emma's
                # secondary finding, matching PROVIDER_INTEGRATION_V1.md
                # section 10's existing requirement, already honored by
                # codex_adapter.py's own _map_exception_to_outcome()). No
                # credential exists in this adapter's memory to leak; the
                # exception's own repr is the only content included.
                return _result(request, "failed", f"unexpected error: {exc!r}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
