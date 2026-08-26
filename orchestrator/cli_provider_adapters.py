"""Construction boundary for the zero-cost subscription-CLI adapters.

Mirrors orchestrator/provider_credentials.py's `build_provider_adapters()`
shape exactly, so a caller can swap between the API-key-based adapter set
and the subscription-CLI adapter set by changing which construction
function it calls -- nothing else in the orchestrator (Chugel, wiring,
agent_invocation, autonomous_runner, durable dispatch, attempt budgets,
human gates) needs to know or care which one is in use, since both
produce a plain `{"codex": AgentInvoker, "claude": AgentInvoker}` mapping
matching the same Protocol.

Per the authorized role assignment for this cycle -- Emilio (builder) on
Codex, Emma (reviewer) on Claude -- this module intentionally returns
exactly `{"codex": CodexCliAdapter(...), "claude": ClaudeCliAdapter(...)}`,
the identical two provider names `orchestrator/provider_router.py`'s
`DEFAULT_PROVIDER_CONFIG` already routes "emilio"/"emma" to. No change to
provider_router.py, wiring.py, agent_invocation.py, chugel.py, or
autonomous_runner.py was necessary or made -- this is the narrowest
possible integration point, matching how `build_provider_adapters()`
(the API-key path) already plugs into the exact same `adapters` parameter
`run_mission()`/`run_emilio_attempt()`/`run_emma_attempt()` accept.

Unlike `build_provider_adapters()`, this module never has any credential
to construct -- both adapters authenticate through their own CLI's
already-active product session at invocation time, verified fresh on
every call via each CLI's official status command. Nothing here reads
`ZENTRA_CODEX_API_KEY`/`ZENTRA_CLAUDE_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or any other credential-shaped value."""

from __future__ import annotations

from orchestrator.adapters.claude_cli_adapter import ClaudeCliAdapter
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter


def build_cli_subscription_adapters(
    *,
    codex_cli_path: str | None = None,
    claude_cli_path: str | None = None,
    codex_timeout_seconds: float | None = None,
    claude_timeout_seconds: float | None = None,
) -> dict[str, object]:
    """Construct the two subscription-CLI adapters. Each `cli_path`, if
    omitted, is auto-discovered by the adapter itself (PATH lookup, plus
    known bundle locations for Codex). Raises whichever adapter's own
    `*CliAdapterError` if its CLI cannot be located -- this function
    performs no authentication check itself; each adapter verifies its
    own CLI's login status fresh on every `invoke()` call, not once here,
    since a session could change between construction and dispatch."""
    codex_kwargs: dict[str, object] = {}
    if codex_cli_path is not None:
        codex_kwargs["cli_path"] = codex_cli_path
    if codex_timeout_seconds is not None:
        codex_kwargs["timeout_seconds"] = codex_timeout_seconds

    claude_kwargs: dict[str, object] = {}
    if claude_cli_path is not None:
        claude_kwargs["cli_path"] = claude_cli_path
    if claude_timeout_seconds is not None:
        claude_kwargs["timeout_seconds"] = claude_timeout_seconds

    return {
        "codex": CodexCliAdapter(**codex_kwargs),
        "claude": ClaudeCliAdapter(**claude_kwargs),
    }
