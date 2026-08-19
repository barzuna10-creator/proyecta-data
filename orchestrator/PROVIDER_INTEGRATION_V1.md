# Provider Integration V1 — Design

Design only. No code exists yet. `orchestrator/PROVIDER_INTEGRATION_V1.md` is
the only file this increment creates. `AGENTS.md`, `agents/AGENT_STANDARD.md`,
both agent `CONTRACT.md` files, `orchestrator/MISSION_RECORD.md`,
`orchestrator/CHUGEL_V1.md`, `orchestrator/AGENT_INVOCATION_V1.md`,
`orchestrator/PROVIDER_ROUTER_V1.md`, `orchestrator/chugel.py`,
`orchestrator/validator.py`, and `orchestrator/state_machine.py` were all
re-read fresh for this design and are unmodified. This document does not
reopen or redesign `PROVIDER_ROUTER_V1.md`'s architecture — it is the
adapter-level implementation specification that architecture's `ClaudeAdapter`
and `CodexAdapter` sections (§4–5) already anticipated needing, filled in
with facts verified against current official sources for Increment #10.

## FACT / INFERENCE / ASSUMPTION / INTENT discipline

Per `agents/AGENT_STANDARD.md` §9. Every claim below sourced from live
research performed for Increment #10 is marked **FACT**, with its source
cited. Anything not independently verified this increment, but inherited
from prior increments' own research, is marked accordingly.

## 1. Canonical Codex package — resolved, not assumed

**FACT, verified by direct inspection of the official source repository and
PyPI, not inferred from a plausible-sounding name**:

- **`openai-codex`** is the canonical, first-party Python SDK. Verified via:
  (a) `github.com/openai/codex`'s own `sdk/python` directory README states
  "Install the SDK: `pip install openai-codex`"; (b) the PyPI listing for
  `openai-codex` (version `0.147.0`, released August 18, 2026) lists its
  homepage as `github.com/openai/codex` and its author as OpenAI directly.
- **`openai-codex-sdk`** (a distinct PyPI package, version `0.1.11`, released
  January 19, 2026) is **explicitly ruled out** — its PyPI listing has no
  homepage link back to `github.com/openai/codex`, and its sole maintainer
  is an individual account (`tomasroda`), not an OpenAI-controlled
  publishing identity. Despite superficially OpenAI-adjacent metadata, this
  is treated as an unofficial or third-party wrapper package, not the
  canonical SDK, and must not be depended on.
- **Decision for implementation**: depend on `openai-codex` only. This
  resolves the open question `PROVIDER_ROUTER_V1.md` §18 and Increment #10's
  own Discovery both flagged as unresolved.

## 2. `ClaudeAdapter` authentication — explicit API credentials only

- Constructs the Claude client (via `claude-agent-sdk`, the package
  `PROVIDER_ROUTER_V1.md` §4 already assumed, confirmed by name in
  Increment #10 Discovery) with credentials read **explicitly** from
  `os.environ["ANTHROPIC_API_KEY"]` — never relying on an ambient
  CLI-level login state, a cached credentials file, or any other implicit
  source. If the environment variable is absent, the adapter fails closed
  at construction time (raises before ever attempting an invocation), never
  silently falling back to an interactively-authenticated session that
  might exist on the host.
- **No consumer-vs-API conflation risk was found for the Claude Agent SDK
  specifically** in Increment #10's research (unlike Codex, below) — but
  the same explicit-credential discipline is applied uniformly as a matter
  of caution, not because equivalent documented evidence of an analogous
  default-preference risk exists for Claude.

## 3. `CodexAdapter` authentication — explicit API credentials, never ambient ChatGPT consumer authentication

**This is the single most safety-relevant finding from Increment #10's
research, and this section exists specifically to close it.**

**Corrected in the Increment #10 bounded corrective cycle**, closing
Emma's independent-review finding P1: the previous version of this section
treated "call `codex.login_api_key(...)` explicitly" as sufficient on its
own to guarantee API-key authentication. **It is not**, and this section no
longer claims otherwise.

**FACT, re-verified for this correction**: the `openai-codex` SDK supports
three authentication methods — ChatGPT browser login, device-code login,
and API-key login (`codex.login_api_key(...)`, confirmed to exist directly
in the SDK's own README). **FACT, from currently-open reports against the
official `openai/codex` repository (GitHub issues #2733 and #3286)**:
switching to API-key authentication has been reported **not to take effect
while a ChatGPT (Team/Plus/Pro) login is simultaneously active** on the
same host — a caller can invoke the API-key login path and still have the
SDK continue using the ChatGPT-authenticated identity underneath, because
the conflicting credential state was never removed, only a new one added
alongside it. **This means calling `codex.login_api_key()` is a necessary
step but is explicitly not, by itself, sufficient as the trust boundary**
this system needs — a mitigation that merely calls the "right" method
without addressing the underlying conflicting-credential-state bug is not
meaningfully stronger than the unguarded default this section exists to
close.

**Design rule, mandatory, revised**: `CodexAdapter` must treat any
pre-existing ChatGPT-authenticated Codex state on the execution host as a
**conflict that must be detected and resolved before a real invocation is
ever attempted** — not merely as a default to override by also calling a
different method alongside it. Specifically:

1. Read `os.environ["OPENAI_API_KEY"]` explicitly — fail closed (raise, no
   invocation attempted) if absent, exactly as `ClaudeAdapter` does for
   `ANTHROPIC_API_KEY`. (Unchanged from the prior version of this section.)
2. **Corrected in this second corrective cycle, closing Emma's re-review
   finding that the original step 2 checked only one of several
   documented credential-storage backends and could not, on its own,
   prove the absence of a conflicting consumer credential.** Codex's own
   documented authentication model supports **more than one credential
   storage backend**, selected by a documented configuration setting
   (referenced in current research as `cli_auth_credentials_store`,
   accepting values including `file`, `keyring`, and `auto`) — **FACT,
   independently corroborated across two separate research passes for
   this document, but still treated here as evidence to verify, not as a
   permanently-fixed API**, exactly the same epistemic status the
   configuration-key subsection below already gives comparable findings:
   - `file` mode: an on-disk credential file (referenced in current
     research as `~/.codex/auth.json`).
   - `keyring` mode: an OS-level credential store (referenced in current
     research as appearing, on macOS, in Keychain Access under a service
     name referenced as "Codex Auth").
   - `auto` mode: resolves to one of the above by a mechanism this
     document does not claim to know precisely.
   **None of these specific names — the config setting's own name, its
   accepted values, the file path, or the keychain service name — are
   asserted here as permanently canonical.** They are the best
   currently-available evidence of what the credential-storage surface
   looks like, subject to the same implementation-time re-verification
   this document already requires for the package name and the
   authentication-preference config key.
3. **Before** ever attempting a real invocation, the adapter must:
   a. **Determine which credential-storage backend is actually active**
      on the execution host, using whatever mechanism the exact pinned
      `openai-codex` version and its documented configuration actually
      expose for this (reading the relevant configuration value, e.g.
      `cli_auth_credentials_store`, from wherever the pinned version
      documents it living) — never assumed, always determined.
   b. **Inspect that specific active backend** — and only a backend whose
      identity was actually determined in step (a), never a guessed or
      default-assumed one — for the presence of a ChatGPT-derived or
      other consumer credential alongside (or instead of) the API key.
   c. **If a conflicting credential is found in the active backend, the
      adapter must fail closed** — raise before constructing any thread
      or making any request.
   **Fail closed, not merely on a found conflict, but on any of the
   following, all equally disqualifying**:
   - the active backend cannot be reliably determined;
   - the determined backend cannot be reliably inspected (e.g. a keyring
     API that itself errors, is unavailable, or requires interactive
     unlock in a non-interactive/headless execution context);
   - a conflicting consumer credential is found in the active backend;
   - for any other reason, API-only authentication cannot be established
     deterministically before the invocation would otherwise proceed.
   **The absence of the file-based store specifically (e.g.
   `~/.codex/auth.json` not existing) must never, on its own, be treated
   as proof of an API-key-only host** — per the epistemic requirement
   above, that absence only rules out the `file` backend; if the active
   backend is `keyring` (or `auto` resolved to `keyring`), a conflicting
   credential could be present there regardless of whether the file
   exists at all, and the adapter must have actually determined and
   checked the *active* backend, not merely the file, before concluding
   anything.
4. **Never silently delete, modify, mutate, log out, migrate, or
   otherwise repair a user's existing consumer credential to resolve this
   conflict — in either storage backend, file or keyring/keychain
   alike.** If the detected conflict needs remediation (removing or
   relocating the conflicting credential from whichever backend it was
   actually found in, so the execution host is left in an
   API-key-only state), that remediation is an explicit,
   separately-authorized operational action a human performs
   deliberately on the execution host — never something `CodexAdapter`
   code does automatically, silently, or as a side effect of an
   invocation attempt, and never something this design treats as
   equivalent regardless of which backend the credential happened to be
   stored in. This mirrors this project's existing, unrelated discipline
   of never automatically resolving a conflict it discovers (e.g.
   `chugel.py` never auto-repairs a corrupt Mission Record; it fails
   closed and reports).
5. Only once the adapter has confirmed — by actually determining the
   active backend and checking it (steps 2–3), never by assumption or by
   checking only the file-based backend regardless of which is active —
   that the execution host's Codex authentication state is API-key-only,
   does it proceed to explicitly call `codex.login_api_key(api_key)` and
   attempt the invocation.
6. **Never** invoke, reference, or depend on `codex.login_chatgpt()` or any
   device-code login path anywhere in the adapter's code — those methods
   exist in the SDK for interactive/consumer use cases this system does not
   have and must not accidentally exercise. (Unchanged.)
7. **`CodexAdapter` must never fall back to consumer ChatGPT
   authentication under any circumstance**, including as an implicit
   consequence of failing to determine the active backend, failing to
   inspect it reliably, or otherwise failing to establish an API-only
   condition (steps 2–3) — the only two legal outcomes of this
   authentication sequence are "a
   confirmed API-key-only invocation proceeds" or "the adapter fails
   closed and nothing is attempted." There is no third path where a
   ChatGPT-authenticated call is allowed to silently go through.

**Why this matters, restated plainly, and preserved from the prior
version**: without this, a Chugel-driven Codex invocation running on a
host where a human has previously logged into Codex CLI with their
personal ChatGPT account could silently consume that consumer
subscription's capacity instead of the dedicated, billed API key — exactly
the conflation the authorizing instruction explicitly warned against, and
exactly the scenario that produced the earlier interruption this whole
provider-diversification initiative was motivated by (Increment #8
finding 12, restated in `PROVIDER_ROUTER_V1.md`'s closing section).
**Zentra's provider routing is based on API products and their own
quotas, never on Claude.ai or ChatGPT consumer subscriptions** — this
principle, already stated in `PROVIDER_ROUTER_V1.md`'s closing section,
is what this section's revised, fail-closed design now actually enforces,
rather than merely asserting.

### Configuration-key ambiguity — corrective addition (Increment #10 corrective cycle, closing Emma's finding P2)

**The prior version of this section cited a specific configuration key
(`preferred_auth_method`) as the mechanism controlling which
authentication method the SDK prefers. This claim is withdrawn as
unverified.** Re-checking during this correction, research surfaced two
candidate key names in circulation — `preferred_auth_method` (referenced
in GitHub issue discussion and CLI usage examples) and `forced_login_method`
(referenced as "official documentation" by a separate source) — and a
direct fetch of the `openai-codex` SDK's own README, the most
authoritative source available in this Discovery, documented neither key
at all, only the three login methods themselves
(`login_chatgpt()`/`login_chatgpt_device_code()`/`login_api_key()`).
**This document does not know, and does not claim to know, the exact
current canonical configuration key for controlling authentication
preference or for enforcing API-key-only mode at the SDK/config level.**

**Design rule**: whichever key (if any) turns out to be current and
authoritative must be verified by direct inspection of the exact,
version-pinned `openai-codex` release selected for implementation — the
same discipline `PROVIDER_ROUTER_V1.md` §18 and this document's own §1
already apply to the package-name question, extended here to its
authentication configuration surface. **No adapter code may be written
against an assumed or undocumented config key.** If, at implementation
time, direct inspection of the pinned SDK version does not turn up a
documented, reliable mechanism to deterministically guarantee API-key-only
authentication at the SDK/config level, the design does not fall back to
trusting `login_api_key()` alone (per the withdrawn claim above) — it
instead relies entirely on the host-level detection-and-fail-closed
mechanism already specified above (steps 2–3: determine the actually-active
credential-storage backend, inspect that backend specifically — never
only the file-based one by default — and fail closed on any conflicting
entry or any inability to determine/inspect the active backend reliably,
regardless of whether a config key exists to additionally request "prefer
API key"). **The determine-then-inspect-then-fail-closed mechanism is the
actual trust boundary this design relies on; any SDK/config-level
preference key, if one turns out to exist and work reliably, is
defense-in-depth on top of it, never a substitute for it — and the
mechanism itself is stated at the same "verify against the pinned
version, do not assume" epistemic level as this configuration-key
question, not asserted with false certainty either.**

## 4. `max_retries=0` — deterministic, explicit, both providers

**FACT, both providers, verified via official SDK documentation**: both the
underlying `anthropic` client (used internally by `claude-agent-sdk`) and
the `openai` client family **auto-retry twice by default**, with
exponential backoff, on connection errors, `408`/`409`/`429`, and `5xx`
responses.

**Design rule, mandatory, both adapters**: both clients must be constructed
with **`max_retries=0`** explicitly — never left at the SDK default.
**Reasoning, restated from `PROVIDER_ROUTER_V1.md` §15's "no autonomous
retry" principle**: if the underlying SDK silently retries a rate-limited
or errored call twice before ever returning control to the adapter, Chugel
never actually sees the first failure — the SDK has already performed
retry behavior indistinguishable from the very "autonomous provider
retry" `PROVIDER_ROUTER_V1.md` explicitly forbids, just one layer lower
than where that document was written to reason about it. Setting
`max_retries=0` makes each Chugel-authorized invocation attempt correspond
to **exactly one** real HTTP call to the provider — the only shape
consistent with `select_adapter()`'s own model of "one attempt in,
one `outcome` out."

## 5. Explicit invocation timeouts — both providers

**FACT**: both SDKs' default timeouts (Anthropic: ~10 minutes in current
versions; OpenAI: 10 minutes total, 5-second connect) are far longer than
any reasonable single Chugel-authorized attempt should be allowed to hang
for.

**Design rule**: both adapters set an explicit, shorter `timeout` at client
construction — a specific number of seconds is **not fixed by this
document** (it depends on realistic task complexity, which varies by
mission and is not something this design should hardcode), but the
requirement is that it is always an explicit value the adapter sets, never
the SDK's multi-minute default left unconfigured. When this timeout is
exceeded, the underlying SDK raises a timeout exception, which the
adapter's exception-mapping layer (section 9) converts to
`AgentInvocationResult.outcome = "timeout"`.

## 6. Fresh-session/thread creation and identifier capture

Restating `PROVIDER_ROUTER_V1.md` §6's single rule, now with the exact
verified mechanism per provider:

- **Claude**: every `invoke()` call issues a bare `query()` (or constructs
  a fresh `ClaudeSDKClient` instance used for exactly one call, then
  discarded) with **no** `resume` or `continue_conversation` option set —
  **FACT, verified**: omitting both is sufficient and necessary to
  guarantee a new session; setting neither is not an ambiguous or
  provider-inferred default, it is the documented mechanism for "start
  fresh." `session_id` is read from the `ResultMessage.session_id` field,
  confirmed "present on every result regardless of success or error" —
  meaning even a failed/timed-out Claude invocation still yields a
  `session_id` the adapter can capture for `provider_session_id`.
- **Codex**: every `invoke()` call issues `codex.start_thread()` — **FACT,
  verified** — never `resumeThread(threadId)`/thread-object reuse for a new
  invocation. The created thread's `threadId` becomes
  `provider_conversation_id`. If a Codex call fails/times out before a
  thread is successfully created at all, `provider_conversation_id` is
  `None` for that attempt (unlike Claude, where a session ID exists even
  on failure) — this asymmetry is noted here explicitly so a future
  implementer does not assume identical availability across providers.
- **Both**: no adapter instance holds a live client/thread-manager object
  as its own persistent state between `invoke()` calls — each call
  constructs what it needs and lets it go out of scope at the end of that
  call, exactly as `PROVIDER_ROUTER_V1.md` §6 already requires.

## 7. Role-specific permissions — Emilio write/execute vs. Emma read/rerun-only

**FACT, Codex, verified**: the `openai-codex` SDK exposes named sandbox
presets directly usable for this requirement: `Sandbox.read_only` ("Read
files without allowing writes"), `Sandbox.workspace_write` ("Read files
and write inside the workspace and configured writable roots"), and
`Sandbox.full_access`. **This confirms `PROVIDER_ROUTER_V1.md` §5's
role-permission requirement is concretely implementable with a named,
first-class SDK primitive, not a workaround**:

- `agent_role == "emilio"`: thread created with `Sandbox.workspace_write`,
  scoped to the worktree path in `task`'s `repository` fields.
- `agent_role == "emma"`: thread created with `Sandbox.read_only` — **no
  write capability exists on the thread at all**, satisfying
  `PROVIDER_ROUTER_V1.md` Acceptance Criteria item 5's explicit standard
  ("not merely instructed not to use them").

**For Claude**: the Agent SDK's tool-permission mechanism (`allowed_tools`
on `ClaudeAgentOptions`, already visible in the official session-management
examples Increment #10 fetched, e.g. `allowed_tools=["Read", "Edit",
"Glob", "Grep"]`) is the equivalent mechanism:

- `agent_role == "emilio"`: `allowed_tools` includes file-editing and
  command-execution tools (`Read`, `Edit`, `Write`, `Bash`/equivalent),
  scoped to the worktree.
- `agent_role == "emma"`: `allowed_tools` includes only read/inspection
  and safe-rerun tools (`Read`, `Glob`, `Grep`, and whatever bounded
  command-execution tool the SDK exposes for read-only rerun checks) —
  **`Edit`/`Write`/any file-mutating tool is never included in the list
  passed to Emma's session**, the same "absent, not merely discouraged"
  standard as Codex's `Sandbox.read_only`.

**Both providers satisfy the requirement identically in kind**: the
restriction is applied at session/thread-construction time, as an
allow-list the underlying agent loop is given, never as an instruction the
agent is asked to voluntarily respect.

## 8. Structured-output mapping into existing Builder/Reviewer evidence formats

**FACT, Claude, verified — corrects Increment #8/#9's "beta" finding**:
Claude's structured-outputs feature reached **General Availability**; the
beta header is no longer required. Mechanism: `output_config.format` (a
`json_schema`-typed format) for a direct structured response, or `strict:
true` on a tool definition for guaranteed tool-call-argument conformance.
Both use grammar-constrained sampling — the model's output is constrained
at generation time, not merely validated after the fact. **Adapter design**:
define one strict tool per role (`record_builder_evidence` /
`record_reviewer_evidence`, arbitrary internal names, never exposed outside
the adapter) whose `input_schema` is exactly `builder_evidence_entry` /
`reviewer_evidence_entry` from `orchestrator/schemas/mission_record.schema.json`
— unchanged, no new format invented, exactly as `PROVIDER_ROUTER_V1.md` §9
already established. The tool call's arguments, once received, **are
already a parsed structure** (not a string requiring further parsing) —
handed to `AgentInvocationResult.evidence` directly.

**FACT, Codex, verified**: `openai-codex`'s `thread.run(prompt,
{"output_schema": schema})` accepts a JSON Schema directly and constrains
the model's output to conform. **One material difference from Claude,
found in this Discovery and specified here precisely because it changes
adapter behavior**: Codex's structured result is returned **as a string**
(`turn.final_response`, described in official examples as JSON text,
not a pre-parsed Python object) — see section 9 below for the exact
handling this requires before the result can become
`AgentInvocationResult.evidence`.

## 9. Codex JSON-string parsing and validation before evidence can be recorded

**New, explicit handling required — Codex's structured output is not
already a dict the way Claude's tool-call arguments are.**

`CodexAdapter`'s `invoke()` method, after a Codex turn completes
successfully:

1. Takes the raw string result (`turn.final_response` or equivalent).
2. Attempts `json.loads()` on it inside a `try`/`except`.
3. **On a `json.JSONDecodeError`** (the string is not valid JSON at all —
   a genuine provider/model failure to conform, distinct from a schema
   mismatch): the adapter itself sets `outcome = "invalid_output"` and
   `evidence = None` — this is **not** treated as `"completed"`, because a
   string that doesn't even parse as JSON cannot become a `dict` for
   Chugel core to validate at all. `error_detail` carries the parse error
   message (free text, never read by any decision logic, per
   `PROVIDER_ROUTER_V1.md` §12's unchanged principle).
4. **On successful parse**, the adapter sets `outcome = "completed"` and
   `evidence` = the parsed dict — **unmodified, no reshaping** — handed to
   Chugel core exactly as `PROVIDER_ROUTER_V1.md` §9 already specifies.
   **The adapter never itself calls `validate_mission_record()` or any
   schema check** — that remains Chugel core's job, via the unmodified
   `record_builder_evidence()`/`record_reviewer_evidence()`, exactly as
   already designed. A successfully-JSON-parsed but schema-non-conformant
   dict (e.g., Codex's `output_schema` constraint was imperfectly honored,
   or the schema itself was translated imprecisely into Codex's
   `output_schema` parameter) is still caught downstream by the existing,
   unmodified `validate_mission_record()` — this parsing step is a
   **necessary precondition** for evidence to ever reach that check, not a
   substitute for it.
5. **This step does not exist for `ClaudeAdapter`** — its strict-tool
   mechanism already returns parsed arguments, so there is no analogous
   string-to-dict step; stated here explicitly so a future implementer
   does not add unnecessary parsing logic to the Claude path by mistaken
   symmetry with Codex's.

## 10. Provider error → `AgentInvocationResult.outcome` mapping

One mapping table, both adapters, using each provider's own exception
taxonomy where it differs, converging on the same five-value
`PROVIDER_ROUTER_V1.md`/`AGENT_INVOCATION_V1.md` enum:

| Condition | Claude (via `anthropic`/`claude-agent-sdk` exceptions) | Codex (via `openai-codex` exceptions) | `outcome` |
|---|---|---|---|
| Connection refused / DNS failure / network unreachable | `APIConnectionError` (or equivalent) | Equivalent connection-level exception | `"unavailable"` |
| Explicit client-side timeout exceeded (section 5) | Timeout exception from the configured `timeout` | Same | `"timeout"` |
| `429` rate-limit / quota exhausted | `RateLimitError` | Equivalent rate-limit exception | `"failed"` (per `PROVIDER_ROUTER_V1.md` §3's deliberate choice not to special-case quota as its own enum value) |
| `5xx` server error | `InternalServerError`/`APIStatusError` | Equivalent | `"failed"` |
| `401`/`403` authentication failure (including the case where section 3's explicit-API-key enforcement itself fails, e.g. an invalid key) | `AuthenticationError`/`PermissionDeniedError` | Equivalent | `"failed"` — an auth failure is not failover-eligible in a way that would help (the fallback provider has its own, separately-configured credentials, so a genuine auth misconfiguration on one provider doesn't get "fixed" by trying the other, but the existing `outcome` enum has no dedicated non-eligible-for-failover distinction beyond `"invalid_output"`; this is flagged as a known, minor imprecision inherited from the existing enum, not solved by this document, and not blocking — an auth failure still correctly writes no evidence and still correctly surfaces via `error_detail` for a human to notice and fix the credential, whether or not the router happens to also try the other, unaffected provider) |
| Successful call, JSON-schema-conformant result | N/A (Claude: parsed tool arguments) | N/A (Codex: JSON parses, section 9) | `"completed"` |
| Successful call, result doesn't conform to the requested structure | Should not occur under strict/grammar-constrained sampling per Anthropic's own documentation, but if the SDK itself raises a schema-violation error, map here | JSON parse failure (section 9) | `"invalid_output"` |

**Every branch of this table is implemented inside a single `try`/`except`
block in each adapter's `invoke()` method — no exception is ever allowed to
propagate uncaught out of `invoke()`**, per `PROVIDER_ROUTER_V1.md` §4/§5's
existing requirement, restated here with the concrete exception types this
Discovery verified exist.

## 11. Credential handling — no secrets in Mission Records or logs

Restating `PROVIDER_ROUTER_V1.md` §17, made concrete for this document's
level of detail:

- `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are read from the process
  environment by their respective adapters **only**, at client-construction
  time, held only in local variables/client-object internal state for the
  duration of one `invoke()` call, never assigned to any field that could
  reach `AgentInvocationRequest`, `AgentInvocationResult`, or any
  Mission Record structure.
- **`error_detail`** (section 10, free text from an exception message) must
  never itself be permitted to contain a raw API key — this is a property
  of how the SDKs themselves format their own exception messages (neither
  officially documented to embed the credential value in error text), not
  something this adapter design can independently guarantee beyond
  choosing not to concatenate the key into any string itself. No adapter
  code in this design ever constructs an error message by hand that
  includes the credential value.
- **Logging**: this document does not design a logging subsystem (none
  exists in this architecture yet), but states the same principle any
  future logging addition must honor: neither adapter ever logs the
  credential value itself, whether at debug, info, or error level.
- This section adds, stores, references, or implies **zero** actual
  credential values, matching the explicit constraint on this increment.

## 12. Exact future dependencies (not added this increment)

Named precisely, so an implementation increment's own dependency-addition
step is unambiguous and requires no further research:

- `claude-agent-sdk` (Python, PyPI) — for `ClaudeAdapter`.
- `openai-codex` (Python, PyPI, version `0.147.0` confirmed current as of
  this Discovery — an implementation increment should re-check for a newer
  version at that time, not assume this exact pin is still latest) — for
  `CodexAdapter`. **Not** `openai-codex-sdk` (section 1).
- Both packages require **Python 3.10+** — this repository's own minimum
  supported Python version was not re-verified in this increment (carried
  forward as an open item from `PROVIDER_ROUTER_V1.md` §18) and must be
  confirmed compatible before either dependency is actually added.
- No other new dependency is anticipated — `select_adapter()`,
  `ProviderConfig`, and the exception-mapping logic in this document all
  use only Python stdlib (`json`, `os`, `dataclasses`), matching every
  other module in this architecture's stated design discipline.

## Preserved routing policy — unchanged, restated for completeness

Exactly as authorized and as `PROVIDER_ROUTER_V1.md` §2 already specifies,
**not modified by this document**:

```python
DEFAULT_PROVIDER_CONFIG = ProviderConfig(roles={
    "emilio": RoleProviderPolicy(primary="codex", fallback="claude"),
    "emma":   RoleProviderPolicy(primary="claude", fallback="codex"),
})
```

Nothing in this document's adapter-level detail changes this policy, the
`select_adapter()` algorithm, the `invocation_log[]` proposal, `INV-LOG-1`,
`produced_by_invocation_id`, or any other element of the already-reviewed
`PROVIDER_ROUTER_V1.md` architecture — this document is purely additive,
filling in the "how" beneath decisions that document already made.

## Explicit non-goals (this increment)

- No adapter or router code implemented.
- No dependency actually added to `requirements.txt`.
- No credential added, stored, or referenced by value.
- No real provider call made — every fact above is sourced from
  documentation/repository inspection, not from exercising either SDK.
- No modification to `PROVIDER_ROUTER_V1.md`, `AGENT_INVOCATION_V1.md`,
  `CHUGEL_V1.md`, `MISSION_RECORD.md`, the schema, `validator.py`,
  `state_machine.py`, `chugel.py`, `AGENTS.md`, or any agent `CONTRACT.md`.
- No David, no CLI, no Jarvis UI, no GitHub/Render automation, no Budget
  Governor.
