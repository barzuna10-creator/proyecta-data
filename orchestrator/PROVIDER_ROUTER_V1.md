# Provider Router V1 — Design

Design only. No code exists yet. `orchestrator/PROVIDER_ROUTER_V1.md` is the
only file this increment creates. `AGENTS.md`, `agents/AGENT_STANDARD.md`,
both agent `CONTRACT.md` files, `orchestrator/MISSION_RECORD.md`,
`orchestrator/CHUGEL_V1.md`, `orchestrator/AGENT_INVOCATION_V1.md`,
`orchestrator/chugel.py`, `orchestrator/validator.py`, and
`orchestrator/state_machine.py` were all re-read fresh for this design and
are unmodified. This document builds directly on `AGENT_INVOCATION_V1.md`
(already committed, already independently reviewed by Emma) and the
Increment #8 Discovery findings — it does not re-derive `AgentInvoker`,
`AgentInvocationRequest`/`Result`, or the four-signal fresh-context model;
it extends them with a deterministic routing layer and two concrete
provider adapter designs.

## Product framing, stated once so it governs every section below

**Emilio and Emma are Zentra roles, defined entirely by their `CONTRACT.md`
files. Claude and Codex/OpenAI are interchangeable execution providers, not
identities.** Nothing in this document couples a role to a provider
permanently — the initial default configuration below (Emilio→Codex,
Emma→Claude) is exactly that: a *default policy value*, changeable by
editing configuration, never a hardcoded assumption baked into
`AgentInvoker`, `AgentInvocationRequest`/`Result`, or any request-builder
function. A future increment could reconfigure both roles onto the same
provider, swap them, or add a third provider, without touching any of the
role-contract-derived allow-lists in `AGENT_INVOCATION_V1.md` §6–7.

## 1. `ProviderRouter` interface and deterministic selection algorithm

`ProviderRouter` is Chugel-core code — not a provider adapter, not an
`AgentInvoker` implementation itself, and it never makes a network call.
Its only job: given an invocation attempt already authorized by a caller,
decide **which** concrete `AgentInvoker` handles it.

```python
@dataclass(frozen=True)
class RoutingDecision:
    adapter_name: str        # "claude" | "codex" -- matches AgentInvocationResult.provider
    reason: str              # closed enum, see below -- never free text

def select_adapter(
    agent_role: str,             # "emilio" | "emma"
    attempt: int,                # 0 or 1
    config: ProviderConfig,      # section 2
    prior_attempts: tuple[AttemptRecord, ...],  # section 10 -- this
                                                 # invocation's own attempt
                                                 # history, oldest first
) -> RoutingDecision:
    ...
```

**Algorithm, entirely deterministic, no branch reads free text**:

0. **(Corrective addition, Increment #9 corrective cycle, closing Emma's
   finding P2-1)** Before anything else: if *any* entry in `prior_attempts`
   already has `outcome == "completed"`, **raise** — this attempt slot is
   already fulfilled, and authorizing a further routing decision for it is
   never legitimate regardless of how the caller arrived at this call. See
   section 12a for the full scenario this closes and why this check exists
   at the routing layer, not only as a downstream write-time refusal.
1. Look up `config.roles[agent_role].primary` — this is the starting
   candidate.
2. If `prior_attempts` is empty (this is the first try for this exact
   role/attempt slot), return the primary. `reason = "primary_default"`.
3. If `prior_attempts` is non-empty (and step 0 found no completed entry),
   examine only the **most recent** entry's `outcome`:
   - If it is in the failover-eligible set (section 3) **and**
     `config.roles[agent_role].failover_enabled` is `True` **and** the
     most recent entry's `adapter_name` is still `config.roles[agent_role].primary`
     (i.e., we haven't already failed over once): return
     `config.roles[agent_role].fallback`. `reason =
     "failover_after_" + outcome` (e.g. `"failover_after_unavailable"`).
   - If the most recent entry's `adapter_name` is already the fallback
     (i.e., **both** configured providers have now been tried and failed
     for this attempt slot): return the primary again, but set
     `reason = "both_providers_exhausted_retry_primary"` — this is not a
     third provider, it is a deliberate, auditable "we're out of
     fallbacks, one more attempt on the original" signal, never a silent
     infinite alternation (see section 15 for why this never becomes a
     loop: `select_adapter` is called once per caller-authorized attempt,
     never by the router itself).
   - If the most recent entry's `outcome` is **not** failover-eligible
     (e.g. `"invalid_output"` — a provider bug, not an availability
     problem) and `config.roles[agent_role].failover_on_invalid_output`
     is `False` (the default, see section 3): return the **same** adapter
     that just failed. `reason = "same_provider_retry"` — a caller
     choosing to retry a non-availability failure on the same provider is
     a legitimate, separate decision from failover, and this function
     never conflates the two.
4. `select_adapter` never consults `error_detail`'s text content anywhere
   in this algorithm — only the closed `outcome` enum value (section 3),
   `agent_role`, `attempt`, and `config`. This satisfies requirement 3
   structurally: there is no code path that could parse free text to
   route, because no free-text field is ever passed into the function's
   decision logic at all (`prior_attempts` entries carry `outcome` as an
   enum and `error_detail` as an opaque string the function signature
   does not even need to reference).

`select_adapter` is a pure function: same inputs, same output, every
time — no I/O, no randomness, no clock read. Given the same `config` and
`prior_attempts`, it is exhaustively testable by enumeration.

## 2. `ProviderConfig` representation

```python
@dataclass(frozen=True)
class RoleProviderPolicy:
    primary: str                        # "claude" | "codex"
    fallback: str                       # "claude" | "codex", must differ from primary
    failover_enabled: bool = True
    failover_on_invalid_output: bool = False  # see section 3 -- deliberately
                                               # off by default

@dataclass(frozen=True)
class ProviderConfig:
    roles: dict[str, RoleProviderPolicy]  # keys: "emilio", "emma"
```

**Default configuration, as authorized**:

```python
DEFAULT_PROVIDER_CONFIG = ProviderConfig(roles={
    "emilio": RoleProviderPolicy(primary="codex", fallback="claude"),
    "emma":   RoleProviderPolicy(primary="claude", fallback="codex"),
})
```

This is a plain, human-editable configuration value — a constant in code,
or (future, not designed here) an external config file. It is never
inferred, never LLM-produced, and changing it never requires touching
`AgentInvoker`, the envelopes, or either adapter's internals — this is the
concrete proof of the "default policy, not permanent coupling" framing
above: swapping `emilio`'s `primary`/`fallback` values is a one-line
config edit, not a redesign.

## 3. Exact failover-eligible outcomes

From `AgentInvocationResult.outcome`'s existing five-value enum
(`AGENT_INVOCATION_V1.md` §4, unchanged):

| `outcome` | Failover-eligible by default? | Reasoning |
|---|---|---|
| `"unavailable"` | **Yes** | Provider/service unreachable — exactly the resilience case this increment exists for |
| `"timeout"` | **Yes** | No response in time — plausibly transient or provider-side load, not a content problem |
| `"failed"` | **Yes** | Provider ran but errored — includes quota/rate-limit exhaustion (see below); treated as availability-class by default |
| `"invalid_output"` | **No** (configurable via `failover_on_invalid_output`, default `False`) | This means the provider *answered* but didn't conform to the requested schema — plausibly a prompt/schema-construction bug in *our own* adapter code for that provider, which the *other* provider might coincidentally not trigger, but silently routing around a structural bug by switching providers risks masking a real defect. Default is same-provider retry or human escalation, not silent failover; a human may explicitly enable cross-provider failover for this outcome via config if experience shows it's warranted |
| `"completed"` | N/A — not a failure, no failover question arises |

**Quota exhaustion and rate limiting are not separate `outcome` values.**
They surface through the *same* adapter-level exception handling that
already produces `"failed"` or `"unavailable"` (`AGENT_INVOCATION_V1.md`
§11's existing table) — an adapter catches a provider SDK's
rate-limit/quota exception and maps it to `"failed"` (the provider
responded, but with an error) or `"unavailable"` (couldn't even reach the
service), exactly like any other provider-side error. This is a
deliberate design choice: adding a sixth `outcome` value specifically for
"quota" would require the router (and everything downstream) to special-case
it, when the *routing consequence* — try the fallback — is identical to
`"failed"`/`"unavailable"`'s. The enum stays exactly as
`AGENT_INVOCATION_V1.md` already defined it; no envelope change is
proposed here.

## 4. `ClaudeAdapter` architecture

`orchestrator/adapters/claude_adapter.py` (not created this increment),
implementing `AgentInvoker`:

- Uses the **Claude Agent SDK** (Python), not the raw Messages API
  directly — both roles need multi-turn tool use (file reads, command
  execution), which the raw stateless API doesn't provide on its own.
- **Constructs a fresh `ClaudeSDKClient` (or a fresh `query()` call with
  no `resume`) for every single invocation** — never reuses a client
  instance across roles, across attempts, or across a retry/failover.
  This is the adapter-level enforcement of the concrete pitfall Increment
  #8 identified: `ClaudeSDKClient` silently continues the same session
  across repeated `.query()` calls on one instance, so the adapter's
  `invoke()` method must construct-and-discard a client per call, never
  hold one as adapter state.
- Serializes `AgentInvocationRequest.task` (already allow-list-clean, per
  `AGENT_INVOCATION_V1.md` §6–7 — the adapter never reaches back into the
  Mission Record itself) into the SDK's `system`/initial-prompt
  parameters — no other content is ever added to what Chugel core
  supplied in `task`.
- Uses a **strict tool** (Claude's grammar-constrained structured-output
  mechanism) whose input schema is exactly `builder_evidence_entry` or
  `reviewer_evidence_entry` (whichever `agent_role` requests) — the tool
  call's arguments, once received, are handed to Chugel core as
  `AgentInvocationResult.evidence` verbatim; the adapter does no
  reshaping. **Open question flagged, not resolved here** (per Increment
  #8's disclosed limitation): whether this feature is still beta-gated
  for the currently-active model line must be reconfirmed against
  Anthropic's live documentation immediately before implementation, not
  assumed stable from this design's research date.
- Captures `session_id` from the SDK's result and sets
  `AgentInvocationResult.provider_session_id` to it;
  `provider_conversation_id` is left `None` for Claude (the SDK doesn't
  distinguish a separate "conversation" concept from "session").
- Sets `provider = "claude"`, `model` = whatever specific model string the
  SDK result reports.
- Catches every SDK/HTTP exception (timeout, rate-limit, auth failure,
  connection failure) and maps it to the appropriate `outcome` — never
  lets an exception propagate uncaught out of `invoke()`.
- Reads `ANTHROPIC_API_KEY` from the process environment only (section 17).
- **Role-specific tool/workspace permissions — corrective addition
  (Increment #9 corrective cycle, closing Emma's finding P2-4)**:
  `agent_role` (already present on every `AgentInvocationRequest`, section
  4 of `AGENT_INVOCATION_V1.md`) determines which tool/permission profile
  the adapter grants the Agent SDK session, **not** a value the adapter
  infers from `task` content or leaves to the SDK's own default:
  - `agent_role == "emilio"`: full Builder write/execute permissions
    *within the authorized task scope* — file edits and command execution
    inside the isolated worktree named in `task`'s `repository` fields,
    matching `agents/emilio/CONTRACT.md` §3's already-existing "real,
    bounded write/execute authority within an isolated worktree." The
    adapter grants nothing beyond what that worktree scope already
    permits — no elevated or unscoped filesystem/network access.
  - `agent_role == "emma"`: **read and rerun-check permissions only — no
    write/edit capability of any kind, structurally**, matching
    `agents/emma/CONTRACT.md` §7's Allowed Tools ("rerunning safe,
    relevant checks... may not edit implementation files"). The adapter
    must configure the Agent SDK session (tool allow-list, filesystem
    permission mode, or equivalent mechanism the SDK exposes) so that
    write/edit tools are never offered to Emma's session in the first
    place — this is a *session-construction-time* restriction, not a
    request Emma's own reasoning is trusted to decline. Emma must never
    be able to modify the artifact she is reviewing, by construction, the
    same "cannot, not merely does not" standard this project already
    applies to `human_gates` access (INV-3).
  - This permission distinction is orthogonal to, and does not weaken,
    any allow-list/independence control already specified — it governs
    what the underlying agent session is *capable of doing to the
    filesystem*, not what data reaches it (`AGENT_INVOCATION_V1.md` §6–7
    still govern that, unchanged).

## 5. `CodexAdapter` architecture

`orchestrator/adapters/codex_adapter.py` (not created this increment),
implementing the same `AgentInvoker` Protocol, structurally parallel to
`ClaudeAdapter`:

- Uses the **official Codex Python SDK** living in the `openai/codex`
  repository's `sdk/python` (its exact current PyPI package name/version
  must be pinned against that repository directly at implementation
  time — Increment #8 found several similarly-named packages and did not
  resolve which is canonical; this is a named open dependency question,
  section 18).
- **Creates a fresh Codex thread for every invocation, via the
  thread-creation call, never `codex-reply()`/thread-continuation for a
  new invocation** — the same "construct-and-discard, never reuse a
  session handle" discipline as `ClaudeAdapter`, applied to Codex's
  thread model instead of Claude's client/session model.
- Serializes `task` into the thread's initial prompt/turn content — same
  allow-list-clean-input guarantee as the Claude adapter, since `task`
  itself is identical regardless of which adapter consumes it.
- Structured evidence: either via the dedicated Codex SDK's own
  structured-output support (Increment #8 found this exists but didn't
  fully characterize its shape) or, if that proves insufficiently precise
  at implementation time, via the general OpenAI Agents SDK's
  Pydantic-model-backed function-tool mechanism, using a Pydantic model
  generated from `builder_evidence_entry`/`reviewer_evidence_entry` —
  **this choice is explicitly deferred to the implementation increment**,
  not decided here, since it depends on characteristics of the dedicated
  SDK's structured-output feature that weren't fully verified in
  Discovery.
- Captures the created thread's `threadId` and sets
  `AgentInvocationResult.provider_conversation_id` to it;
  `provider_session_id` is left `None` for Codex (mirroring Claude's
  `provider_conversation_id: None` — each provider populates whichever of
  the two identifier fields matches its own terminology, leaving the
  other `None`, exactly as `AGENT_INVOCATION_V1.md` §4 already anticipated:
  "either or both may be `None`").
- Sets `provider = "codex"`, `model` = whatever the SDK reports.
- Same exception-to-`outcome` mapping discipline as `ClaudeAdapter`.
- Reads `OPENAI_API_KEY` from the process environment only.
- **Role-specific tool/workspace permissions — corrective addition
  (Increment #9 corrective cycle, closing Emma's finding P2-4), applied
  symmetrically with `ClaudeAdapter` above**: `agent_role` determines the
  thread's approval-policy/sandbox configuration at creation time — Codex's
  own product design already exposes an approval-policy/sandbox-mode
  concept (Increment #8 Discovery), which the adapter must set
  accordingly, never leaving it at a permissive default regardless of
  role:
  - `agent_role == "emilio"`: workspace-write-enabled sandbox mode, full
    file-edit/command-execute permissions within the isolated worktree,
    matching `agents/emilio/CONTRACT.md` §3 exactly as in the Claude
    adapter's case.
  - `agent_role == "emma"`: **read-only sandbox mode, no workspace-write
    permission granted at thread-creation time** — she may run rerun
    checks (read-only commands) but the thread itself is never given
    write access to the worktree, matching `agents/emma/CONTRACT.md` §7.
    This is enforced identically to `ClaudeAdapter`'s case: a
    session-construction-time restriction, never a runtime request the
    agent is merely asked not to make.
  - Same orthogonality note as `ClaudeAdapter`: this governs filesystem
    capability, not what data reaches the session — `AGENT_INVOCATION_V1.md`
    §6–7's allow-lists are unaffected and unchanged.

## 6. Exact fresh-session/thread creation rules (both providers)

One rule, stated once, applying identically to both adapters — the two
architecture sections above each restate it in their own provider's
vocabulary, but it is a single principle, not two separate ones:

**Every `invoke()` call constructs a brand-new session/thread object and
discards it at the end of that call. No adapter instance ever holds a
live session/thread handle as its own state between calls. A retry or
failover is, from the adapter's perspective, indistinguishable from any
other fresh invocation — it never "resumes" anything, regardless of
whether the immediately preceding attempt (on the same or a different
provider) succeeded, failed, or timed out.**

This is what makes the fresh-context guarantee for Emma (and the
same-discipline-but-not-independence-required treatment for Emilio) hold
regardless of which provider handles a given invocation, and regardless
of whether that invocation is a first attempt, a same-provider retry, or
a cross-provider failover.

## 7. Emma independence across same-provider and cross-provider execution

**Same-provider execution** (e.g., Emma on Claude after Emilio also ran on
Claude, per the default config's fallback path, or a future config where
both roles share a primary): full reliance on `AGENT_INVOCATION_V1.md`'s
existing INV-1 through INV-2a — the allow-list (INV-1), the
`fresh_context_attested` requirement (INV-2), and the session-ID
cross-check (INV-2a) using the *same* provider's identifier field
(`provider_session_id` for two Claude invocations, `provider_conversation_id`
for two Codex invocations).

**Cross-provider execution** (the default configuration's common case —
Emilio on Codex, Emma on Claude): INV-1 through INV-4 apply identically
(they are provider-independent Chugel-core properties). INV-2a's
same-provider comparison becomes **structurally inapplicable in the
straightforward sense** — Emilio's `provider_conversation_id` (a Codex
thread ID) and Emma's `provider_session_id` (a Claude session ID) are
values from two different identifier namespaces belonging to two
different companies' infrastructure; comparing them for equality is not
meaningful the way comparing two Claude session IDs against each other
is. This is not a weakening — as Increment #8 found, **cross-provider
pairing removes the specific accidental-reuse failure mode INV-2a exists
to catch, because there is no shared session/thread infrastructure at all
between a Codex thread and a Claude session for the same adapter code to
accidentally conflate.** INV-2a's check, when applicable (same-provider
case), remains exactly as specified; when the two roles are on different
providers, that specific mechanical check has nothing to compare, and the
independence property it protects is instead structurally guaranteed by
provider separation itself. **INV-5's residual gap is narrower in the
cross-provider case** (no shared provider infrastructure to distrust) but
**not eliminated** — each individual adapter's own honesty (did the
adapter correctly build Emma's request from nothing but the allow-listed
`task`, with zero contamination from whatever it separately did for
Emilio) remains unverifiable by Chugel core, exactly as INV-5 already
states, regardless of provider pairing.

## 8. INV-1 through INV-5 and INV-2a — enforcement responsibility matrix

| Invariant | Enforced by | Provider-dependent? |
|---|---|---|
| INV-1 (allow-list) | Chugel core's request-builder functions (`AGENT_INVOCATION_V1.md` §6–7, unchanged) | No — identical regardless of provider or router |
| INV-2 (`fresh_context_attested` required) | Chugel core's response-consumer (`AGENT_INVOCATION_V1.md` §8, unchanged) | No |
| INV-2a (session-ID cross-check) | Chugel core's response-consumer, reading `invocation_log[]` entries (section 10) | Meaningful only when both compared invocations share a provider (section 7); inapplicable, not violated, cross-provider |
| INV-3 (no `human_gates` access) | Chugel core's response-consumer calling only `record_builder_evidence()`/`record_reviewer_evidence()` (unchanged `chugel.py`) | No |
| INV-4 (no `state`/`state_history` access) | Same as INV-3 | No |
| INV-5 (residual: session honestly isolated, adapter honest) | **Not enforced — adapter-trusted, per-adapter** | Each adapter (`ClaudeAdapter`, `CodexAdapter`) is independently a source of this residual risk; adding a second provider does not remove the gap, it adds a second instance of the same category of gap, one per adapter |

**The router itself introduces no new invariant and no new gap.**
`select_adapter()` never touches `task`, never touches `human_gates`,
never touches `state`, and never sets `fresh_context_attested` or any
identifier field — it only chooses which adapter's `invoke()` a caller's
already-constructed request goes to. Every invariant above is exactly as
strong (or exactly as limited) with the router in place as without it.

## 9. Structured evidence production using the existing schemas

Restating `AGENT_INVOCATION_V1.md` §4's design insight, now confirmed to
hold for both providers per Increment #8: **no new output format is
invented for either provider.** Both adapters produce
`AgentInvocationResult.evidence` conforming to the same, unmodified
`builder_evidence_entry`/`reviewer_evidence_entry` shapes already in
`orchestrator/schemas/mission_record.schema.json`. The *mechanism* differs
per provider (Claude's strict tool / grammar-constrained sampling vs.
Codex's structured-output or Pydantic-model-backed function tool — section
4–5), but the *target shape* is identical, and Chugel core's
response-consumer (`record_builder_evidence()`/`record_reviewer_evidence()`,
unchanged) never knows or cares which adapter produced the dict it
receives — it validates the same way regardless, via the same unmodified
`validate_mission_record()`.

## 10. `invocation_log[]` proposal — multiple provider attempts per logical slot

**Still a proposal, not a schema change made by this document** — a
refinement of the need `AGENT_INVOCATION_V1.md` §10 already flagged,
extended per Increment #8 finding 9 (a failed-then-failed-over attempt
sequence must be representable, not just a single attempt record).
**Wording clarification (Increment #9 corrective cycle)**: `AttemptRecord`
(section 1's `prior_attempts` parameter type) *is* this same shape — one
`invocation_log[]` entry — not a separate, differently-shaped structure;
`select_adapter()` receives exactly the entries below for the current
`logical_attempt_group`, including `error_detail` (added to the shape
below, previously implied by section 1's text but missing from this
example).

```json
{
  "invocation_log": [
    {
      "invocation_id": "<uuid>",
      "mission_id": "<uuid>",
      "agent_role": "emilio",
      "attempt": 0,
      "logical_attempt_group": "<uuid, shared across every invocation_id
                                  tried for this same builder_evidence
                                  attempt=0 slot>",
      "provider": "codex",
      "model": "<string>",
      "provider_session_id": null,
      "provider_conversation_id": "<codex threadId>",
      "requested_fresh_context": true,
      "fresh_context_attested": true,
      "requested_at": "<RFC3339>",
      "responded_at": "<RFC3339>",
      "outcome": "unavailable",
      "error_detail": "<free text, never read by routing or validation logic>",
      "routing_reason": "primary_default",
      "resulted_in_evidence": false
    },
    {
      "invocation_id": "<different uuid>",
      "mission_id": "<same mission_id>",
      "agent_role": "emilio",
      "attempt": 0,
      "logical_attempt_group": "<same group id as above>",
      "provider": "claude",
      "model": "<string>",
      "provider_session_id": "<claude session_id>",
      "provider_conversation_id": null,
      "requested_fresh_context": false,
      "fresh_context_attested": true,
      "requested_at": "<RFC3339>",
      "responded_at": "<RFC3339>",
      "outcome": "completed",
      "error_detail": null,
      "routing_reason": "failover_after_unavailable",
      "resulted_in_evidence": true
    }
  ]
}
```

**`logical_attempt_group`** is the new field this refinement adds beyond
what §10 originally sketched — a stable identifier shared by every
invocation attempted for one `builder_evidence`/`reviewer_evidence`
`attempt` slot, letting an auditor reconstruct "these N invocation_log
entries were all attempts at producing attempt=0's evidence, and entry K
is the one that actually succeeded" without relying on timestamp
ordering alone. `provider`/`model`/`provider_session_id`/
`provider_conversation_id` remain **metadata only** — `AGENT_INVOCATION_V1.md`
§12's principle ("never authority") applies identically here; nothing in
`validate_mission_record()` or `can_transition()` would ever read this
array to decide anything, matching the same treatment `decision_ref`
already receives.

### INV-LOG-1 — corrective addition (Increment #9 corrective cycle, closing Emma's finding P2-2)

**Promoted from a passing mention to an explicit, named, mandatory
invariant**: at most one entry within a given `logical_attempt_group` may
ever have `resulted_in_evidence == true`. This is stated here as a
first-class requirement, not a parenthetical, for exactly the reason
Emma's review named: leaving it as a passing note risked it being read as
optional or aspirational rather than load-bearing.

- **This invariant must be deterministically enforced before Provider
  Router V1's implementation can be considered complete.** "Deterministic
  enforcement" means a cross-field check — structurally the same category
  as `validator.py`'s existing `_check_*` functions
  (`_check_attempt_sequencing`, `_check_corrective_cycle_consistency`,
  etc.) — that rejects any `invocation_log[]` state where more than one
  entry in the same `logical_attempt_group` claims `resulted_in_evidence:
  true`, the same way `validate_mission_record()` already rejects a
  `builder_evidence[]` array with two entries claiming the same `attempt`
  number.
- **Where this enforcement lives is a schema/validator question, not a
  router question** — `select_adapter()`'s own step 0 (section 1,
  corrective addition above) provides a *routing-time* defense against
  the specific case of a caller re-authorizing an attempt after one
  already completed, but that is a distinct layer from this invariant,
  which governs the *persisted record's own internal consistency*
  regardless of how it was produced. Both layers are required; neither
  substitutes for the other (the same "belt and suspenders, never rely on
  one layer alone" reasoning as section 12a).
- **Not designed in code here, and not implemented this increment** —
  this section states the requirement precisely enough that a future
  schema/validator increment has an unambiguous target, exactly as
  `AGENT_INVOCATION_V1.md` §10 already did for `invocation_log[]`'s
  original, less-specified form. See the Acceptance Criteria section
  (below) for this invariant restated as a certifiable completion
  condition.

### Structural link from evidence entries to `invocation_log[]` — corrective addition (Increment #9 corrective cycle, closing Emma's finding P2-3)

**Future schema requirement, specified here, not implemented**: each
`builder_evidence_entry`/`reviewer_evidence_entry` (the existing,
unmodified shapes in `orchestrator/schemas/mission_record.schema.json`)
needs one new optional field —

```json
"produced_by_invocation_id": { "anyOf": [{ "type": "null" }, { "type": "string", "minLength": 1 }] }
```

— set to the `invocation_id` of the specific `invocation_log[]` entry
that produced this evidence (i.e., the one entry in the corresponding
`logical_attempt_group` whose `resulted_in_evidence` is `true`). `null`
is the correct value for any evidence entry recorded before this field
existed, or for evidence recorded through a path that never went through
`invocation_log[]` at all (e.g., evidence hand-transcribed by a human
today, without any agent invocation infrastructure yet) — this field is
never required, only present when there is a real invocation to point to.
**Why this closes the gap Emma identified**: without it, `builder_evidence[]`
and `invocation_log[]` are only correlated by `attempt` number and
timestamp proximity — enough for a human skimming the record, not enough
for a future cross-field check to *verify* that the evidence actually
came from the invocation the log claims produced it. With
`produced_by_invocation_id` present, a future validator addition can
assert `builder_evidence[attempt].produced_by_invocation_id` equals the
`invocation_id` of the `invocation_log[]` entry in the matching
`logical_attempt_group` with `resulted_in_evidence: true` — turning a
loose, human-inferred correlation into a checkable one. **Like
`invocation_log[]` itself, this remains a proposal for a future,
separately-authorized schema change — nothing in this increment adds this
field to the actual schema file.**

**This remains, as in Increment #7, out of this increment's authorized
scope to actually add to the schema** — flagged again, now with the
concrete shape above (both `invocation_log[]` itself and the
`produced_by_invocation_id` link), for whichever future increment is
authorized to make the schema change.

## 11. Audit representation — summary

Every field named in your requirement 11 is already present in section
10's proposed shape: `provider`, `model`, `invocation_id`,
`provider_session_id`/`provider_conversation_id` (session/thread ID,
whichever the provider populates), `outcome` (failure classification),
`requested_at`/`responded_at` (timestamps). `routing_reason` (section 1)
is additionally captured so an auditor can see *why* the router chose
what it chose, not just what it chose — itself a closed enum value,
never free text, so this audit field is as safe to read/display as
`outcome` is.

## 12. Deterministic failover after quota/rate-limit/timeout/unavailability

Fully specified by sections 1 and 3 together: any of `"unavailable"`,
`"timeout"`, or `"failed"` (which subsumes quota/rate-limit exhaustion,
per section 3's reasoning) is failover-eligible by default; `select_adapter`
consults only the enum value and configuration, never free text; a second
failure after failover returns to the primary with an explicit
`"both_providers_exhausted"`-class reason rather than alternating forever
(closing the loop-risk your requirement 15 names, detailed next).

## 12a. Late/duplicate provider results — corrective addition (Increment #9 corrective cycle)

Closing Emma's independent-review finding P2-1: two specific adversarial
scenarios were previously safe only by inheritance from the unmodified
validator, never explicitly designed or tested. Both are now specified
directly, and a single, general rule (below) is stated so no future
variant of "two results for one logical attempt" needs its own bespoke
handling.

**The general rule**: within one `logical_attempt_group` (section 10), at
most one invocation may ever have its `evidence` passed to
`record_builder_evidence()`/`record_reviewer_evidence()`. This is not new
policy — it is a restatement of `AGENT_INVOCATION_V1.md`'s existing,
unmodified attempt-sequencing enforcement (`_check_attempt_sequencing`,
`DUPLICATE_ATTEMPT_NUMBER`) applied to the specific case of *multiple
provider attempts* aimed at the same `builder_evidence`/`reviewer_evidence`
`attempt` slot: the underlying schema-level cap (one entry per `attempt`
value, `maxItems: 2` total) makes a second write for an already-filled
slot structurally impossible regardless of which provider, or how many
providers, produced a `"completed"` result for that slot. This document
adds no new enforcement mechanism here — it makes explicit, and tests,
that the *existing* mechanism is what protects these two scenarios,
closing the gap between "the property holds" and "this document says so
and proves it."

**Scenario: provider returns late after timeout.** Sequence: Chugel core
builds a request for `builder_evidence` attempt 0, routes it to the
primary, the primary exceeds the adapter's timeout (`outcome: "timeout"`,
nothing written, per section 13), the caller then authorizes a **new**
invocation attempt (new `invocation_id`, per section 6's construct-and-discard
rule) that routes to the fallback, which returns `outcome: "completed"`
and its evidence is written. If the *original* (timed-out) provider call
now completes in the background and its result is delivered to whatever
code is still holding a reference to the original request/response pair:
that result's `invocation_id` matches only the **original**, already-abandoned
request — never the fallback's. **Design rule, stated explicitly**: a
caller must discard the original `AgentInvocationRequest`/pending-result
handle once it has consumed a `"timeout"` outcome for it; nothing in
Chugel core keeps that handle alive or reachable after the timeout branch
is taken. If a caller nonetheless mishandles this and attempts to feed
the late result into the response-consumer for `builder_evidence` attempt
0 *after* the fallback's evidence was already written for that same
attempt: the write is refused by the unmodified `DUPLICATE_ATTEMPT_NUMBER`
check (the attempt slot is filled), regardless of the late result's own
apparent validity. **The design fails closed here by inheritance, and
this section now says so explicitly rather than leaving it implicit.**

**Scenario: primary returns `"completed"`, caller subsequently attempts
fallback anyway.** This is a caller-discipline error (per section 15,
neither the router nor an adapter ever decides on its own to try a
second provider — only a caller authorizes each attempt), not a
router/adapter defect, but the *consequence* must still be fail-closed
regardless of the caller's mistake. Sequence: primary succeeds, its
evidence is written for the attempt slot (attempt slot now filled); a
confused caller nonetheless calls `select_adapter` again and authorizes
the fallback for what it believes is still an open attempt. Two
sub-cases: (a) the fallback adapter itself is well-behaved and its result
is fed to the response-consumer — refused by the same
`DUPLICATE_ATTEMPT_NUMBER` check, nothing overwritten; (b) **stronger
rule, new in this corrective cycle**: `select_adapter` itself, given
`prior_attempts` that already contains an entry with `outcome ==
"completed"` for this exact role/attempt, must refuse to return a
routing decision at all — raising rather than silently routing to
anything — since authorizing a second real attempt at an already-fulfilled
slot is never a legitimate routing question, and detecting this at the
routing layer (before ever reaching a provider) is strictly cheaper and
clearer than relying solely on the evidence-write-time refusal
downstream. This is an explicit strengthening of section 1's algorithm,
not a new mechanism layered beside it: step 3 there already inspects
`prior_attempts`' most recent entry; this corrective cycle adds the
precondition that step 3 (and the whole function) first checks whether
*any* entry in `prior_attempts` already has `outcome == "completed"`, and
if so, raises immediately, before evaluating any of the failover-branch
logic.

**A completed invocation can never be silently replaced by a fallback
result**, restated as the single guarantee both scenarios above serve:
once `resulted_in_evidence` would be `true` for one entry in a
`logical_attempt_group` (section 10), no other entry in that group can
ever also become `true` — enforced at two independent layers (the router
refusing to authorize a further attempt once it sees a completed one in
its own history, and the unmodified schema-level write protection as the
backstop if the router layer is ever bypassed or its `prior_attempts`
input is stale) — belt and suspenders, matching this project's existing
discipline of never relying on exactly one layer of protection alone.

## 13. Non-completed invocation never writes evidence

Unchanged from `AGENT_INVOCATION_V1.md` §11 and restated here because it
is the exact property that makes failover safe (Increment #8 finding 7):
`outcome != "completed"` never reaches
`record_builder_evidence()`/`record_reviewer_evidence()` — this is true
identically whether the failed attempt was on Claude, Codex, or either in
sequence. The router introduces no new write path and does not change
this property in any way.

## 14. No provider switch mid-invocation

`select_adapter()` is called **once**, before `AgentInvoker.invoke()` is
called, for a given `invocation_id`. There is no code path where a
single `invocation_id`'s request is sent to one adapter and its response
awaited from another, and no code path where two adapters are raced
against each other for one invocation_id. A "switch" is always, and only,
a **new** invocation attempt (new `invocation_id`, freshly built request,
per section 6's construct-and-discard rule) that happens to route
differently — never a change of provider within one still-open call.

## 15. No autonomous retry, by router or adapters

`select_adapter()` is a function a **caller** invokes once per
authorized attempt (mirroring `AGENT_INVOCATION_V1.md` §5's invocation
lifecycle exactly — this document adds a routing *decision* as step 2a
between "caller decides to invoke" and "caller calls the request
builder," it does not add a new autonomous step). Neither `select_adapter`
nor either adapter's `invoke()` ever calls itself, calls the other
adapter, or calls the request-builder/response-consumer functions —
matching `AGENT_INVOCATION_V1.md` §14's four independent reasons this
architecture cannot loop, all of which apply unchanged with the router
in place: no invocation is triggered by a prior invocation's own
response; no Chugel-core function calls `AgentInvoker.invoke()` from
inside a response-consumer; `chugel.py`'s operations remain single
synchronous calls; the schema's hard caps bound the total possible
evidence-producing attempts regardless of how many failed/failover
attempts preceded them.

## 16. Router and adapters cannot read or mutate `human_gates`

Neither `ProviderRouter` nor either adapter imports, calls, or has any
reference to `chugel.decide_gate()`/`chugel.decide_scope_change()` (the
only two functions in the system capable of changing a gate — unchanged
`chugel.py`). `select_adapter()`'s entire input surface (section 1) is
`agent_role`, `attempt`, `config`, and `prior_attempts` — none of which is
or contains any part of `human_gates`. This is the same structural
"cannot, not merely does not" guarantee `AGENT_INVOCATION_V1.md` INV-3
already establishes, extended to cover the router as an additional piece
of code with no path to that data at all.

## 17. Credential boundary — no credentials added or stored by this document

`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are read from the process
environment by their respective adapters only — never by
`ProviderRouter`, never by Chugel core, never written to, read from, or
referenced by any Mission Record field, `ProviderConfig` value, or this
design document itself. This document adds, stores, references, or
implies **zero** actual credential values — consistent with your explicit
instruction and with this repository's existing `.gitignore`/`AGENTS.md`
treatment of secret-like files. Which specific environment-variable
names, secrets-manager integration, or credential-rotation policy to use
is an implementation-increment decision, not a design-document one; this
section states only the boundary (adapters read env vars, nothing else
ever touches credentials), not the mechanism.

## 18. Dependency/package questions to resolve before implementation

Named explicitly, none resolved here:

- **The canonical Codex Python package.** Increment #8 found
  `codex-sdk-python`, `codex-sdk-py`, `codex-sdk`, and `openai-codex` as
  distinct PyPI listings. The `openai/codex` GitHub repository's own
  `sdk/python` directory is the authoritative source to check against —
  whichever PyPI package corresponds to that directory's actual releases
  is the one to depend on; this must be confirmed by direct inspection at
  implementation time, not assumed from a plausible-sounding name.
- **Claude structured-outputs beta status** for the model line actually in
  use at implementation time (Increment #8, item 5) — whether the beta
  header is still required, or whether the feature has reached general
  availability, changes nothing about this design's shape but must be
  confirmed before the `ClaudeAdapter`'s structured-output mechanism is
  finalized.
- **Codex's structured-output precision**, per section 5 — whether the
  dedicated Codex SDK's own structured-output support is sufficient, or
  whether the general OpenAI Agents SDK's Pydantic-backed function-tool
  mechanism is needed instead, is an open implementation question this
  design deliberately does not resolve.
- **Minimum supported Python version reconciliation** — the OpenAI Agents
  SDK requires Python 3.10+; this repository's own minimum Python version
  was not re-verified in this Discovery and should be checked for
  compatibility before adding either provider's SDK as a dependency.

None of these block this design document's completion; all block the
implementation increment that would follow it.

## 19. Testing strategy for a future implementation

Extending `AGENT_INVOCATION_V1.md` §18's existing test list (still
unimplemented) with router- and multi-provider-specific cases, all
provider-call-free (stub `AgentInvoker`s, no network, matching every
existing test file's convention):

- **Provider outage**: a stub Codex adapter always returning
  `outcome="unavailable"` for Emilio's primary attempt; assert
  `select_adapter` returns Claude (the configured fallback) on the next
  attempt, with `routing_reason == "failover_after_unavailable"`.
- **Quota exhaustion**: same shape, stub adapter returning `outcome="failed"`
  with an `error_detail` string mentioning a rate-limit/quota phrase —
  assert the routing decision is identical to any other `"failed"` case,
  proving `error_detail`'s content plays no role (a test that
  deliberately varies `error_detail`'s text across otherwise-identical
  cases and asserts the routing decision never changes is the strongest
  possible proof of requirement 3).
- **Same-session contamination**: a stub Claude adapter that
  (incorrectly, simulating a buggy adapter) returns the *same*
  `provider_session_id` for both an Emilio and a subsequent Emma
  invocation on the same mission; assert the response-consumer refuses
  per INV-2a regardless of `fresh_context_attested`, exactly as
  `AGENT_INVOCATION_V1.md` §18 already specifies — re-run here with the
  router in the loop to prove the router's presence doesn't disturb this
  existing enforcement.
- **Malformed provider output**: a stub adapter returning
  `outcome="completed"` with a schema-invalid `evidence` dict; assert
  `record_builder_evidence()`/`record_reviewer_evidence()` refuses via
  the unmodified `validate_mission_record()`, and that this refusal
  behaves identically regardless of which adapter/provider produced it.
- **Interrupted invocation**: a stub adapter that raises partway through
  a simulated call (mirroring `chugel.py`'s own atomic-write crash tests);
  assert no `AgentInvocationResult` is ever partially constructed —
  either a complete result with a valid `outcome` is returned, or an
  exception propagates to the caller, never a half-populated object.
- **Fallback success**: primary fails with a failover-eligible outcome,
  fallback returns `outcome="completed"` with valid `evidence`; assert
  the resulting Mission Record is indistinguishable in its
  `builder_evidence`/`reviewer_evidence` content from one produced by the
  primary succeeding on the first try — only `invocation_log[]` (once it
  exists) differs, never the schema-validated evidence arrays themselves.
- **Both providers unavailable**: primary and fallback both return
  `outcome="unavailable"` in sequence; assert `select_adapter`'s third
  call returns the primary again with
  `routing_reason == "both_providers_exhausted_retry_primary"`, and that
  nothing about this loops — the test explicitly calls `select_adapter`
  a bounded, caller-driven number of times and asserts the function never
  calls itself or any adapter.
- **Cross-provider independence** (new, specific to this increment): an
  end-to-end test with Emilio on a stub Codex adapter and Emma on a stub
  Claude adapter (the default configuration), asserting INV-1 through
  INV-4 all hold exactly as they do in the single-provider corrective-cycle
  test already specified in `AGENT_INVOCATION_V1.md` §18, and that
  `provider_session_id`/`provider_conversation_id` are populated/`None`
  in the pattern section 5 specifies (each adapter populates only its own
  identifier field).
- **Late response after timeout (corrective addition, Increment #9
  corrective cycle, closing Emma's finding P2-1)**: a stub adapter whose
  `invoke()` returns `outcome="timeout"` for the first call; the fallback
  then returns `outcome="completed"` and its evidence is written; a
  *third*, separate call simulating the late-arriving original response
  (a `"completed"` result carrying the **first** call's now-abandoned
  `invocation_id`) is then constructed and fed to the response-consumer
  directly — assert it is refused via `DUPLICATE_ATTEMPT_NUMBER` (the
  attempt slot is already filled), and that the on-disk Mission Record is
  byte-identical before and after this refused attempt.
- **Completed-then-fallback-attempted-anyway (corrective addition,
  Increment #9 corrective cycle, closing Emma's finding P2-1)**: primary
  returns `outcome="completed"`, evidence is written; `select_adapter` is
  then called again with `prior_attempts` containing that completed
  entry — assert it **raises** (section 1's step 0), before any adapter
  is even selected, let alone invoked; separately, assert that if a test
  bypasses the router and constructs a second real `AgentInvocationResult`
  for the same attempt slot directly, the response-consumer still refuses
  it via the same `DUPLICATE_ATTEMPT_NUMBER` check as the previous test —
  proving both the routing-layer and the write-time-layer defenses named
  in section 12a independently hold.
- **`resulted_in_evidence` uniqueness (corrective addition, Increment #9
  corrective cycle, closing Emma's finding P2-2)**: once INV-LOG-1 (section
  10) is actually implemented in a future schema/validator increment, a
  hand-constructed `invocation_log[]` with two entries in the same
  `logical_attempt_group` both claiming `resulted_in_evidence: true` must
  be rejected by that future validator addition — specified here as the
  test this future increment must include, not run against anything that
  exists yet.
- **`produced_by_invocation_id` linkage (corrective addition, Increment #9
  corrective cycle, closing Emma's finding P2-3)**: once the schema field
  proposed in section 10 exists, a test asserting a `builder_evidence[]`
  entry's `produced_by_invocation_id` matches the `invocation_id` of the
  one `invocation_log[]` entry in its `logical_attempt_group` with
  `resulted_in_evidence: true`, and a negative test asserting a mismatched
  pair is rejected — again specified for the future increment that adds
  this field, not runnable today.
- **Role-specific tool permissions (corrective addition, Increment #9
  corrective cycle, closing Emma's finding P2-4)**: a stub/mock Agent SDK
  session inspection asserting that an Emma-role invocation's session was
  constructed with write/edit tools absent from its allow-list (not merely
  "granted but unused") for both `ClaudeAdapter` and `CodexAdapter`, and
  that an Emilio-role invocation's session has them present, scoped to the
  worktree named in `task`.

## 20. Cost/usage observability hooks for a future Budget Governor

**Not implementing a Budget Governor here** (explicitly out of scope, per
your instruction and `CHUGEL_V1.md`'s own prior non-goal). What this
design provides *for* a future one, without building it: section 10's
`invocation_log[]` proposal already carries `provider`/`model`/
`requested_at`/`responded_at` for every attempt, success or failure — the
minimum raw material a future Budget Governor would need to compute
per-provider, per-role, or per-mission consumption, since token/cost
figures themselves are not something Chugel core or either adapter
computes or stores in this design (Increment #8's cost estimates were
illustrative only, not a measured or persisted value anywhere in this
architecture). A future Budget Governor would read `invocation_log[]`
entries as its own input, external to and unmodified by anything in this
document — exactly the same "metadata now, a future consumer reads it
later" relationship `MISSION_RECORD.md` already established for
`decision_ref`.

## 21. Acceptance criteria for a future implementation increment

**New section, corrective addition (Increment #9 corrective cycle, closing
Emma's finding P2-5)** — comparable in rigor to `CHUGEL_V1.md` §22 and
`AGENT_INVOCATION_V1.md` §22, consolidating every prerequisite and
invariant scattered through this document into one certifiable checklist.
A future implementation increment (`ProviderRouter`, `ClaudeAdapter`,
`CodexAdapter` — still without any real provider call authorized by that
increment's own text unless separately stated) is complete only when:

1. `select_adapter()` is a pure function exactly as specified in section
   1, including **step 0's completed-entry check** — verified by the
   "Completed-then-fallback-attempted-anyway" test (section 19).
2. `select_adapter()` never reads `error_detail`'s content — verified by
   the section 19 test that varies `error_detail`'s text across
   otherwise-identical cases and asserts the routing decision never
   changes.
3. `ProviderConfig`/`RoleProviderPolicy` match section 2's shape exactly,
   with the authorized default (`emilio`: primary `codex`, fallback
   `claude`; `emma`: primary `claude`, fallback `codex`) as the actual
   default value, changeable by configuration alone.
4. Both adapters implement the construct-and-discard fresh-session/thread
   rule from section 6 with no exception — verified by a test asserting no
   adapter instance holds session/thread state as an attribute between
   `invoke()` calls.
5. **Both adapters implement role-specific tool/workspace permissions
   exactly as section 4/5's corrective additions specify** — Emilio gets
   write/execute within worktree scope, Emma gets read/rerun-only with no
   write capability granted at session-construction time — verified by
   the "Role-specific tool permissions" test (section 19). **An
   implementation that grants Emma write-capable tools, even if she is
   merely instructed not to use them, does not satisfy this criterion.**
6. Neither the router nor either adapter ever calls
   `chugel.decide_gate()`/`chugel.decide_scope_change()`/`chugel.transition()`
   — verified by direct code review (grep for these call sites remains a
   legitimate mechanical check, per `AGENT_INVOCATION_V1.md` §22's
   precedent).
7. `provider`/`model`/`error_detail`/`routing_reason` are read by nothing
   beyond logging/pass-through/audit display — no decision path anywhere
   branches on their content.
8. **INV-LOG-1 (section 10) is deterministically enforced** — this
   criterion cannot be satisfied by the router/adapter implementation
   alone; it requires the accompanying schema/validator increment (still
   separately authorized, not this one) to have landed and its own test
   (section 19, "`resulted_in_evidence` uniqueness") to pass. **Provider
   Router V1 is not complete without this**, even if the router/adapter
   code itself is otherwise finished — this is a deliberate, explicit
   cross-increment dependency, stated here so it is never silently
   dropped.
9. The `produced_by_invocation_id` linkage (section 10) exists and is
   verified per its own section 19 test — same cross-increment dependency
   note as point 8.
10. Every scenario in section 12a (late response after timeout,
    completed-then-fallback-attempted-anyway) has a passing test per
    section 19, with the Mission Record shown byte-identical before and
    after each refused attempt.
11. The full existing Chugel test suite continues to pass unmodified,
    plus every new test named in section 19.
12. No file outside the new router/adapter module(s), their tests, and
    the separately-authorized schema/validator changes for points 8–9 is
    touched; `chugel.py`, `validator.py`, `state_machine.py`, and every
    agent `CONTRACT.md` remain byte-identical unless a schema change was
    itself separately authorized.
13. No new dependency is added beyond what was explicitly authorized at
    implementation-authorization time, and the canonical Codex Python
    package question (section 18) is resolved by direct inspection of the
    `openai/codex` repository before being pinned, not assumed.
14. The attempt-pinning policy (below) is implemented as **Option B**
    (independent per-attempt routing), per José's explicit decision.
15. An independent Emma review confirms all of the above from a fresh
    reading of the actual diff, not from the Builder's handoff narrative.

## Attempt-pinning policy — decided by José (Increment #9 corrective cycle); analysis preserved below

**The question that was resolved**: must a corrective-cycle attempt=1 invocation use the
same provider its attempt=0 counterpart used, or is each attempt routed
independently by `select_adapter` with no memory of the other?

**Option A — pin attempt=1 to attempt=0's provider** (unless a failover
condition is independently triggered for attempt=1 itself):
- *Consistency*: the same model/provider evaluates the corrective build
  as evaluated the original — arguably more coherent if a provider has
  systematic stylistic or capability differences that could otherwise
  make a corrective build look like a totally different author's work
  from attempt to attempt.
- *Independence*: no effect on Emma's independence either way — INV-1
  through INV-2a govern the Emilio↔Emma relationship, not
  attempt-0↔attempt-1 for the same role.
- *Failover*: requires an explicit carve-out (a provider outage during
  attempt=0 that already triggered a failover shouldn't force attempt=1
  onto the now-known-unavailable original primary) — adds a small amount
  of extra state tracking (remembering attempt=0's *effective* provider,
  not just the configured primary).
- *Auditability*: simpler to reason about after the fact ("this mission's
  Emilio work was entirely on Codex, except where it visibly failed
  over") — one fewer axis of variation to explain in a retrospective.

**Option B — route each attempt independently, always starting from
`config`'s primary**:
- *Consistency*: none assumed or guaranteed — attempt=1 could land on a
  different provider than attempt=0 even with no failover having
  occurred on either, purely because... it wouldn't, actually: with no
  failover triggered, `select_adapter` always returns the primary for a
  fresh (empty `prior_attempts`) call, so in the *no-failure* case Option
  A and Option B produce **identical** behavior — the difference only
  manifests when attempt=0 experienced a failover.
- *Independence*: same as Option A, no effect.
- *Failover*: simpler — no extra state, `select_adapter` for attempt=1
  starts exactly as attempt=0 did, from `config`'s primary, regardless of
  what happened on attempt=0.
- *Auditability*: arguably *more* transparent in one sense (each attempt's
  routing is fully explained by `config` + that attempt's own
  `prior_attempts`, nothing borrowed from a sibling attempt's history) but
  *less* predictable in another (a reader must check both attempts'
  `invocation_log[]` independently to know the full picture, rather than
  inferring attempt=1 from attempt=0).

**Decision (José, Increment #9 corrective cycle): Option B — independent
per-attempt routing — is approved as the V1 default policy.** The
original recommendation and analysis are preserved below unedited, as the
record of the reasoning behind this decision: Option B requires no
additional state, produces identical behavior to Option A in the common
no-failure case (the two options only diverge in the already-unusual
case where attempt=0 needed a failover at all), and keeps
`select_adapter`'s input surface exactly as specified in section 1
(`prior_attempts` scoped to *this* invocation attempt's own history, not
reaching across to a sibling attempt) — adding cross-attempt memory would
be a real complexity increase for a benefit (provider consistency across
a corrective cycle) that isn't obviously load-bearing for anything this
system currently guarantees. Emma's independent review of this decision
noted one added nuance not in the original analysis: under Option B, if
attempt=0 required a failover, attempt=1 still starts at the (just-failed)
primary before falling back again — a bounded latency/attempt cost, not a
safety issue, and not sufficient on its own to change this decision. If a
future need for Option A's consistency guarantee emerges, the only
design-level change required is widening `select_adapter`'s
`prior_attempts` parameter to also accept attempt=0's effective-provider
record as an additional input, which would not disturb anything else in
this document — this reversibility is preserved even though Option B is
now the settled V1 policy, not merely a placeholder recommendation.

## Consumer subscription limits vs. API-provider quota — explicitly not conflated

Restating Increment #8 finding 12 as a binding design principle for this
document, since it directly motivated this whole increment: **the
interruption that occurred earlier in the session that produced this
research was a `claude.ai`-style consumer-product tool-usage limit — a
different resource pool from an Anthropic or OpenAI *API* key's own
rate/quota limits.** Nothing in `ProviderConfig`, `select_adapter`, or
either adapter's design is built around, references, or attempts to
detect a consumer-subscription-style limit — both adapters are designed
exclusively against the **API** products (`ANTHROPIC_API_KEY`/
`OPENAI_API_KEY`, pay-as-you-go or provisioned-tier billing), which have
their own, separate rate-limit/quota semantics (RPM/TPM ceilings, section
6 of the Increment #8 report) surfaced through ordinary SDK exceptions —
exactly what `ClaudeAdapter`/`CodexAdapter`'s exception-to-`outcome`
mapping (sections 4–5) already handles. A future adapter implementation
must not be designed, tested, or reasoned about as if it could ever
encounter a `claude.ai`/`chatgpt.com` consumer-app session limit — that
failure mode belongs to a different product entirely and has no bearing
on this architecture.
