"""ClaudeCliAdapter -- zero-cost subscription-CLI AgentInvoker for Emma.

Implements the same `AgentInvoker` Protocol (orchestrator/agent_invocation.py)
as orchestrator/adapters/claude_adapter.py, but dispatches through the
locally-installed Claude Code CLI binary using its own already-authenticated
claude.ai subscription login (`claude auth status` reporting
`authMethod == "claude.ai"`), instead of the SDK's `ANTHROPIC_API_KEY` +
dedicated `ZENTRA_CLAUDE_API_KEY` path. No Anthropic API key is ever read,
constructed, or required by this module.

**Why this is a separate module, not a mode of claude_adapter.py**: same
reasoning as codex_cli_adapter.py's module docstring. claude_adapter.py's
isolation model constructs a brand-new `CLAUDE_CONFIG_DIR`/`HOME` per
invocation and injects `ANTHROPIC_API_KEY` explicitly into that isolated
child environment -- this is fundamentally incompatible with subscription
auth, since the claude.ai session lives in the user's *existing* config
directory (default `~/.claude`), and overriding `CLAUDE_CONFIG_DIR`/`HOME`
the way claude_adapter.py does would make that session invisible to the
child process. Claude Code's own documented `--bare` mode makes this
tension explicit: it states "Anthropic auth is strictly ANTHROPIC_API_KEY
or apiKeyHelper via --settings (OAuth and keychain are never read)" --
i.e. `--bare` is *definitionally* incompatible with subscription auth.
This adapter therefore never uses `--bare` and never overrides
`CLAUDE_CONFIG_DIR`/`HOME`; it authenticates by not touching the existing
config directory at all, and instead confines the invocation through
command-line flags only: `--add-dir` (worktree scope, combined with the
process `cwd`), `--allowedTools` (hard allow-list, matching the roles'
existing `_ALLOWED_TOOLS` -- Emma gets read-only tools), `--permission-mode
dontAsk` (the identical choice claude_adapter.py's own SDK-based
`_build_worker_options()` already makes), and `--strict-mcp-config` (no
external MCP servers).

Corrective cycle #3 (runtime discovery from the authorized real
zero-cost pilot): the prior version also passed `--max-budget-usd 0`,
intended as a second, defense-in-depth spend guard on top of the
authentication check below. The real, installed Claude CLI rejects this
outright: `claude --help` documents `--max-budget-usd <amount>` as
"Maximum dollar amount to spend on API calls (only works with --print)"
and requires a positive value -- the installed CLI's own error is
explicit: `--max-budget-usd must be a positive number greater than 0`.
Zero is not a legal value, so the prior command line failed before the
model ever received the task. This flag's own documented meaning already
shows it was never actually load-bearing for this adapter's zero-cost
guarantee: it caps *API-call* spend, and this adapter's invariant is that
no API-key path is ever reachable at all (`_verify_claude_subscription_login()`
below, requiring `authMethod == "claude.ai"`, refuses before every single
dispatch) -- there is no API billing account for a dollar cap to bound in
the first place. This fix removes `--max-budget-usd` outright and
introduces no positive budget in its place; zero-additional-cost remains
enforced entirely by subscription-auth-only routing (the mandatory
`authMethod == "claude.ai"` check, re-verified before every dispatch) and
by this module never constructing, reading, or accepting an
`ANTHROPIC_API_KEY` anywhere on this path -- not by any dollar-amount CLI
flag.

**Disclosed, accepted limitations relative to claude_adapter.py**:
1. No equivalent of the SDK adapter's `_native_filesystem_guard()`
   `PreToolUse` hook exists here -- that hook is Python-SDK-specific
   machinery with no direct CLI-flag equivalent. This adapter instead
   relies on Claude Code CLI's own native workspace-boundary enforcement
   (the product's built-in behavior of confining file tools to `cwd` plus
   any `--add-dir` paths, which is why `--add-dir` exists as an *expansion*
   flag rather than a restriction flag). This is weaker than a
   custom-written guard in the sense that it trusts the CLI product's own
   boundary rather than re-deriving it, and stronger in the sense that it
   is the CLI's real, maintained, product-level sandboxing rather than a
   hand-rolled approximation of it.
2. Global user-level configuration (`~/.claude/CLAUDE.md`, any
   organization-level settings) may still be visible to the CLI process,
   since `CLAUDE_CONFIG_DIR` is never overridden. `--strict-mcp-config`
   removes MCP-server inheritance specifically; there is no broader
   "--ignore-user-config" equivalent found for Claude Code CLI at the time
   of this implementation (unlike Codex's `--ignore-user-config`).

**Unverified against a live invocation**: this module has been exercised
only against fake CLI executables in
tests/test_orchestrator_claude_cli_adapter.py -- the exact JSON envelope
`--print --output-format json` actually produces has not been empirically
confirmed (no real provider task has been executed under this
authorization). `_extract_structured_result()` below therefore tries
several plausible extraction shapes, in a fixed priority order, each
labeled ASSUMPTION, and fails closed (`invalid_output`) if none match --
this is a disclosed, concrete residual risk for the first real pilot.

**Corrective cycle #1 (Emma's P1 finding, independence-check integrity)**:
the prior version of this module set `provider_conversation_id =
request.invocation_id` on every completed result -- identical defect and
identical reasoning to codex_cli_adapter.py's own corrective-cycle note
(see that module's docstring for the full explanation and the empirical
verification performed). This version instead attempts to capture a
genuine, provider-reported session id via `_extract_session_id()` below,
scanning the same top-level envelope `_extract_structured_result()`
parses from `--output-format json` for one of a small set of plausible
key names (`session_id`, `conversation_id`) -- labeled ASSUMPTION, since
the real envelope's exact shape is unconfirmed without a live invocation.
If none of those keys are found, `provider_conversation_id` is left
`None` -- never synthesized from `invocation_id`, `mission_id`,
`attempt`, or any other orchestrator-owned value. A `None` identity here
is never worked around: if this adapter's output is ever the *builder*
side of a review (e.g. Claude used for Emilio via the router's own
failover policy), `chugel.reserve_dispatch()`'s own precheck for the
matching reviewer dispatch correctly refuses until a real identity is
captured -- the safe, fail-closed default in every role this adapter can
play.

**Corrective cycle #5 (runtime discovery from the second authorized real
zero-cost pilot retry)**: when Codex's primary dispatch failed (a
separate, since-fixed defect -- see codex_cli_adapter.py's own
corrective-cycle #5 note), the router's failover policy sent Emilio's
task to this adapter instead. Because this module -- unlike
codex_cli_adapter.py after its own corrective cycle #4 -- still let the
model self-report its own `artifact` when `agent_role == "emilio"`, two
bad outcomes were observed across two real runs: once the model reported
a structurally invalid artifact that failed Chugel's schema validation
outright, and once it reported a schema-*valid* but semantically false
one -- `mode: "commit"` with `commit_sha` set to the pre-existing base
commit (not a real new commit; its own narrative even said no commit was
made). The second case is the more dangerous of the two: it would have
been silently accepted as evidence of real work that never happened.

This version applies the identical principle codex_cli_adapter.py's
corrective cycle #4 established: for `agent_role == "emilio"`, `artifact`
is stripped from the schema handed to the model entirely (it cannot even
attempt to report one -- `additionalProperties: false` on
`builder_evidence_entry` makes this structurally enforced, not just
policy), and this adapter computes a genuine `mode: "patch"` artifact
itself from `git add -N` / `git diff --binary` / `git reset` run directly
against the real worktree after `claude -p` exits -- never a commit,
never inside the model's own tool sandbox, never trusting anything the
model claimed. See `_compute_uncommitted_patch_artifact()` below
(deliberately duplicated from codex_cli_adapter.py's function of the same
name, per this module's existing no-cross-adapter-import convention --
see the module-level note above -- not a divergence risk, since it is
pure `git`-subprocess logic with no adapter-specific behavior in it).
Emma (`agent_role == "emma"`) is completely unaffected: her schema never
had an `artifact` property to begin with, and this new code path is
gated strictly on `agent_role == "emilio"`."""

from __future__ import annotations

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

DEFAULT_TIMEOUT_SECONDS = 300.0

# Deliberately duplicated from orchestrator/adapters/claude_adapter.py, not
# imported: that module imports the real `claude_agent_sdk` package at
# module level, which this CLI-only adapter has no need for and must not
# require -- the whole point of a zero-cost CLI adapter is to work in an
# environment that has the Claude Code CLI installed but not the paid-API
# SDK package. Duplication risk is low: this is pure JSON-Schema
# projection logic derived only from the canonical schema file and small
# static tuples, not credential or trust-boundary logic.

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mission_record.schema.json"
_EVIDENCE_ENTRY_NAME = {"emilio": "builder_evidence_entry", "emma": "reviewer_evidence_entry"}
_INFRASTRUCTURE_IDENTITY_FIELDS = frozenset(
    {"invocation_id", "provider", "provider_session_id", "provider_conversation_id"}
)
_PROVIDER_UNSUPPORTED_KEYWORDS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "if", "then", "else"}
)
_ALLOWED_TOOLS = {
    "emilio": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
    "emma": ["Read", "Glob", "Grep"],
}


class ClaudeCliAdapterError(Exception):
    """Pre-invocation fail-closed refusal specific to the subscription-CLI
    path -- never raised for a provider-side outcome."""


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
    """Identical logic to claude_adapter.py's own function of the same
    name, duplicated here (see module-level note above), except for the
    `artifact` exclusion added in corrective cycle #5 (see module
    docstring) -- that divergence is deliberate and disclosed.

    Corrective cycle #5: for `agent_role == "emilio"`, `artifact` is also
    excluded from `properties` (and, below, from `required`) -- the model
    can no longer report its own artifact identity at all; this adapter
    computes a genuine one itself after execution (see
    `_compute_uncommitted_patch_artifact()` and its call site in
    `invoke()`)."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        full_schema = json.load(f)
    entry_name = _EVIDENCE_ENTRY_NAME[agent_role]
    entry = dict(full_schema["definitions"][entry_name])
    excluded_fields = _INFRASTRUCTURE_IDENTITY_FIELDS
    if agent_role == "emilio":
        excluded_fields = excluded_fields | {"artifact"}
    entry["properties"] = {
        name: value
        for name, value in entry["properties"].items()
        if name not in excluded_fields
    }
    if agent_role == "emilio":
        required = entry.get("required")
        if isinstance(required, list) and "artifact" in required:
            entry["required"] = [name for name in required if name != "artifact"]
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


def _resolve_authorized_worktree(raw_path: object) -> Path:
    """Identical logic to claude_adapter.py's own function of the same
    name, duplicated here (see module-level note above)."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ClaudeCliAdapterError("repository.worktree_path must be a non-empty absolute path")
    supplied = Path(raw_path)
    if not supplied.is_absolute():
        raise ClaudeCliAdapterError("repository.worktree_path must be absolute")
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ClaudeCliAdapterError("repository.worktree_path must resolve to an existing directory") from exc
    if resolved != supplied.absolute() or not resolved.is_dir():
        raise ClaudeCliAdapterError("repository.worktree_path must be a canonical, non-symlinked directory")
    return resolved


def _discover_claude_cli(explicit_path: str | None) -> str:
    """Locate the `claude` executable -- `shutil.which()` only. Unlike
    Codex (which ships inside a desktop app bundle not always on PATH),
    Claude Code CLI installers place it on PATH as their normal behavior,
    so no fallback bundle-location list is needed here. Fails closed if
    not found."""
    if explicit_path:
        if not Path(explicit_path).is_file():
            raise ClaudeCliAdapterError(f"explicit claude CLI path does not exist: {explicit_path!r}")
        return explicit_path
    found = shutil.which("claude")
    if found:
        return found
    raise ClaudeCliAdapterError("claude CLI executable could not be located on PATH")


def _verify_claude_subscription_login(cli_path: str) -> None:
    """Fail closed unless `claude auth status` -- the CLI's own official,
    documented status command -- confirms an active claude.ai subscription
    login. Never inspects ~/.claude.json or any other credential file;
    never extracts or logs a token. Only structural fields (`loggedIn`,
    `authMethod`) are inspected -- `email`/`orgId`/`orgName`, also present
    in this command's output, are never read or repeated by this
    function."""
    try:
        result = subprocess.run(
            [cli_path, "auth", "status"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudeCliAdapterError(f"could not query claude auth status: {exc!r}") from exc
    try:
        status = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ClaudeCliAdapterError(f"claude auth status did not return parseable JSON: {exc}") from exc
    if not isinstance(status, dict) or not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
        raise ClaudeCliAdapterError(
            "claude auth status does not confirm an active claude.ai subscription "
            "login -- refusing to dispatch under the zero-cost subscription CLI "
            f"adapter (authMethod={status.get('authMethod') if isinstance(status, dict) else None!r})"
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _result(request: AgentInvocationRequest, outcome: str, error_detail: str | None,
            evidence: dict | None = None, conversation_id: str | None = None) -> AgentInvocationResult:
    return AgentInvocationResult(
        invocation_id=request.invocation_id, outcome=outcome, provider="claude",
        model=None, responded_at=_now(), fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=conversation_id,
        evidence=evidence, error_detail=error_detail,
    )


# Candidate key names for a genuine Claude-reported session identity in
# the top-level `--output-format json` envelope. ASSUMPTION (unconfirmed
# without a live invocation): the real envelope may use a different name
# or not expose one to `-p` at all. Checked in this fixed priority order;
# the first present string value wins. Never invents a value when none of
# these keys are found.
_CLAUDE_SESSION_ID_KEYS = ("session_id", "conversation_id")


def _extract_session_id(raw_stdout: str) -> str | None:
    """Scan the top-level parsed `--output-format json` envelope (not the
    extracted evidence object -- that must never carry infrastructure
    identity) for a genuine, provider-reported session id. Returns None
    (never a guess) if the envelope doesn't parse as an object, or none of
    `_CLAUDE_SESSION_ID_KEYS` are present with a non-empty string value."""
    try:
        parsed = json.loads(raw_stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    for key in _CLAUDE_SESSION_ID_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_structured_result(raw_stdout: str) -> dict | None:
    """ASSUMPTION (undocumented at the time of writing, unverified against
    a live invocation -- see module docstring): tries, in order, (1) the
    top-level parsed object directly, if it is a dict and not itself an
    envelope (heuristically: does not carry a `type`/`subtype` field
    Claude Code's own event/result envelopes are known to use elsewhere),
    (2) a top-level `"result"` field that is itself a dict, (3) a
    top-level `"result"` field that is a string which itself parses as a
    JSON object. Returns None (never raises) if none apply -- the caller
    turns that into `invalid_output`, never a guess."""
    try:
        parsed = json.loads(raw_stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and "type" not in parsed and "subtype" not in parsed:
        return parsed
    if isinstance(parsed, dict):
        result_field = parsed.get("result")
        if isinstance(result_field, dict):
            return result_field
        if isinstance(result_field, str):
            try:
                nested = json.loads(result_field)
            except json.JSONDecodeError:
                return None
            if isinstance(nested, dict):
                return nested
    return None


_GIT_DIFF_TIMEOUT_SECONDS = 30.0


def _compute_uncommitted_patch_artifact(worktree: Path) -> tuple[dict | None, str | None]:
    """Corrective cycle #5: deliberately duplicated from
    codex_cli_adapter.py's function of the same name (see this module's
    docstring for why -- pure `git`-subprocess logic, no adapter-specific
    behavior, so duplicating it carries none of the SDK-dependency risk
    the module-level note above is about). Computes a genuine `mode:
    "patch"` artifact for `agent_role == "emilio"` from whatever the
    model's edits actually left on disk -- never a commit, never trusting
    anything the model claimed. `git add -N -- .` (intent-to-add,
    unstaged content) then `git diff --binary` (raw bytes) then `git
    reset` (drops the intent-to-add entries; the working tree is never
    touched) -- run directly by this adapter's own trusted code, not
    through any of Claude's own tool sandbox. An empty diff is a genuine
    failure (the model claimed completed work but left nothing real
    behind), never synthesized into a fake non-empty artifact.

    Returns `(artifact, None)` on success or `(None, error_detail)` on
    failure -- exactly one is non-None.

    Disclosed, accepted limitation (identical to codex_cli_adapter.py's
    own disclosure): the patch file lives in its own directory under
    `trusted_system_temp_root()`, not cleaned up here, so a later Emma
    dispatch (or a human) can still read `patch_path` after this call
    returns. This accumulates directories over many missions/attempts --
    a known, disclosed follow-up, not silently omitted."""
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
            "claude -p reported completed work, but no uncommitted change exists "
            "in the worktree to capture as a patch artifact"
        )
    artifact_dir_raw = tempfile.mkdtemp(prefix="zentra-claude-cli-artifact-", dir=trusted_system_temp_root())
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


class ClaudeCliAdapter:
    """AgentInvoker dispatching to the Claude Code CLI's existing
    claude.ai subscription login. See module docstring for the full
    trust-boundary disclosure. `cli_path`, if omitted, is auto-discovered."""

    __slots__ = ("_cli_path", "_timeout_seconds")

    def __init__(self, *, cli_path: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._cli_path = _discover_claude_cli(cli_path)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ClaudeCliAdapterError("timeout_seconds must be a positive number")
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"ClaudeCliAdapter(cli_path={self._cli_path!r})"

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        if not isinstance(request, AgentInvocationRequest):
            raise ClaudeCliAdapterError("ClaudeCliAdapter requires an AgentInvocationRequest")
        if request.agent_role not in _ALLOWED_TOOLS:
            raise ClaudeCliAdapterError(f"unsupported Claude agent role: {request.agent_role!r}")

        repository = request.task.get("repository") or {}
        worktree = _resolve_authorized_worktree(repository.get("worktree_path"))

        # Fail closed on authentication mismatch before spawning anything.
        _verify_claude_subscription_login(self._cli_path)

        try:
            prompt = json.dumps(request.task)
            tools = _ALLOWED_TOOLS[request.agent_role]
            schema = _load_evidence_schema(request.agent_role)

            command = [
                self._cli_path, "-p",
                "--output-format", "json",
                "--permission-mode", "dontAsk",
                "--add-dir", str(worktree),
                "--allowedTools", ",".join(tools),
                "--strict-mcp-config",
                "--json-schema", json.dumps(schema),
            ]

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
                return _result(request, "timeout", "claude -p timed out")
            except OSError as exc:
                return _result(request, "unavailable", f"could not launch claude CLI: {exc!r}")

            if process.returncode != 0:
                return _result(
                    request, "failed",
                    f"claude -p exited with code {process.returncode}: "
                    f"{stderr.decode('utf-8', 'replace').strip()[:2000]}",
                )

            raw_stdout = stdout.decode("utf-8", "replace")
            evidence = _extract_structured_result(raw_stdout)
            if evidence is None:
                return _result(request, "invalid_output", "claude -p output did not match any recognized structured-result shape")

            # Corrective cycle #5: `artifact` is never taken from the
            # model (stripped from the schema in _load_evidence_schema()
            # for agent_role == "emilio", so the model cannot even
            # include one) -- this adapter computes the genuine one
            # itself from what actually changed on disk. Emma
            # (agent_role == "emma") never reaches this branch.
            if request.agent_role == "emilio":
                artifact, artifact_error = _compute_uncommitted_patch_artifact(worktree)
                if artifact_error is not None:
                    return _result(request, "invalid_output", artifact_error)
                evidence["artifact"] = artifact

            # provider_conversation_id: only a genuine, provider-reported
            # session id (or None, if the envelope carried none) -- never
            # request.invocation_id or any other orchestrator-owned value
            # (Emma's P1 corrective cycle -- see module docstring). A None
            # here is not swallowed or worked around anywhere.
            session_id = _extract_session_id(raw_stdout)
            return _result(request, "completed", None, evidence=evidence, conversation_id=session_id)
        except ClaudeCliAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 -- never let an unrecognized
            # exception propagate uncaught out of invoke() (Emma's secondary
            # finding, matching PROVIDER_INTEGRATION_V1.md section 10's
            # existing requirement, already honored by claude_adapter.py's
            # own _map_exception_to_outcome()). No credential exists in this
            # adapter's memory to leak; the exception's own repr is the only
            # content included.
            return _result(request, "failed", f"unexpected error: {exc!r}")
