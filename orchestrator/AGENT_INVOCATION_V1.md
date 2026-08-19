# Agent Invocation Architecture V1 — Design

This document is Discovery + Design only for Increment #7. No code exists
yet. `orchestrator/AGENT_INVOCATION_V1.md` is the only file this increment
creates. `AGENTS.md`, `agents/AGENT_STANDARD.md`, `agents/emilio/CONTRACT.md`,
`agents/emma/CONTRACT.md`, `orchestrator/MISSION_RECORD.md`,
`orchestrator/CHUGEL_V1.md`, `orchestrator/chugel.py`,
`orchestrator/validator.py`, and `orchestrator/state_machine.py` were all
re-read fresh from the repository for this design and are unmodified.

## FACT / INFERENCE / ASSUMPTION / INTENT discipline

Per `agents/AGENT_STANDARD.md` section 9, every non-obvious claim below is
labeled. Unlabeled statements are direct restatements of already-committed
source documents (`AGENTS.md`, the agent contracts, `CHUGEL_V1.md`,
`MISSION_RECORD.md`, the schema, and `chugel.py` as it exists at base commit
`f4831ec37549bdb264702b6105cfa3b7ec488aaa`), verified by direct reading, not
inference.

## The central question, answered first

**Can this architecture technically guarantee Emma's independence?**

**No — not fully, not in V1, and this document does not pretend otherwise.**
This is stated up front because your question demands a direct answer
before any design detail, and because a design document that buried this
answer inside its middle sections would itself be a form of the exact
failure mode this system exists to prevent (a confident-sounding
architecture quietly overclaiming a safety property it cannot deliver).

Two distinct things are commonly conflated as "Emma's independence," and
they have very different guarantee levels:

1. **Structural independence — what Emma receives.** This *can* be fully
   guaranteed by deterministic code, and this design guarantees it:
   Chugel's request-construction code for Emma is a strict allow-list (never
   a deny-list) over Mission Record fields. It is *structurally incapable*
   of including Emilio's `conclusion`, `assumptions`, `risks`, or
   `rollback_notes` in what it sends Emma, because the code that builds
   Emma's request never reads those fields at all — not "chooses not to,"
   *cannot*, the same way `record_builder_evidence()` today is structurally
   incapable of touching `human_gates` because no line of that function's
   code ever assigns to that key. This is a FACT about what deterministic
   code can guarantee, not an ASSUMPTION.

2. **Runtime independence — whether the underlying agent session is
   actually fresh.** This *cannot* be guaranteed by Chugel's deterministic
   core alone, because Chugel does not currently, and does not in this
   design, ever originate an agent session itself. **FACT, verified by
   reading `chugel.py`, `CHUGEL_V1.md`, and both agent contracts fresh**:
   no code in this repository today starts, manages, or terminates an LLM
   provider session. Every Emilio/Emma turn in this entire project so far
   has been a human (José) driving a chat session with an assistant
   occupying that role. Whether "a fresh context" actually happened is
   presently a fact about human/process discipline, not a fact any code
   can inspect. **ASSUMPTION, flagged explicitly**: even once a real
   provider adapter exists, whether *that adapter's* "new session" call
   actually produces a context with zero information leakage from a prior
   session is a property of the provider's own infrastructure, which no
   code in `orchestrator/` can verify from the outside — it can be
   *required* of the adapter and *attested* by the adapter, but not
   *proven* by Chugel.

**Consequently, this design fails closed on the runtime-independence gap
using a multi-signal model, not a single boolean** — corrected in this
revision (Increment #7 bounded corrective cycle, closing Emma's
independent-review finding P1-A that a lone self-attested boolean is
ceremonial: it refuses only an adapter honest enough to write `False`,
and provides zero resistance to a lazy or dishonest adapter that always
writes `True`). Four distinct things, never collapsed into one field:

1. **Chugel requesting freshness** (`requested_fresh_context`) — Chugel
   core's own, code-enforced statement of intent when it builds an Emma
   invocation. This half genuinely *is* guaranteed, the same way section
   7's allow-lists are: Chugel core is the only writer of this field, and
   it is always `True` for Emma, never adapter-settable.
2. **The adapter attesting freshness** (`fresh_context_attested`) — kept,
   but now explicitly one signal among several, never sufficient alone.
3. **Objective, cross-checkable provider metadata**
   (`provider_session_id` / `provider_conversation_id`) — opaque
   identifiers the adapter reports, which Chugel core *can* mechanically
   compare across invocations for the same mission without trusting the
   adapter's own judgment about what they mean. This is new in this
   revision and is the actual strengthening: it turns "no code in Chugel
   core can detect the lie" into "code can detect the single most likely
   form of it — a session literally reused — whenever a provider exposes
   these identifiers at all."
4. **The residual, unverifiable provider-level guarantee** — that the
   identifiers themselves aren't fabricated, and that a *different*
   session-ID value doesn't secretly carry leaked context through some
   channel these fields don't capture. Section "Fresh-context
   requirements" states this residual gap explicitly, precisely so it is
   never overclaimed away by the presence of signals 1–3.

Section "Fresh-context requirements" below specifies this mechanism
precisely, and section "Emma independence invariants" states everything
this design *can* guarantee in full, so the boundary between guaranteed
and merely-required is never blurred.

## 1. Architecture overview

Four layers, each with a narrow, single responsibility:

```
 caller (human today; a future orchestration script later)
        |
        v
 +-------------------------+
 |  Chugel core (this doc) |  <- deterministic, no network, no LLM
 |  - request builders     |
 |  - response consumers   |
 |  - AgentInvoker protocol|
 +-------------------------+
        |  (Protocol call, no concrete provider knowledge)
        v
 +-------------------------+
 |  AgentInvoker adapter   |  <- NOT built this increment
 |  (provider-specific)    |
 +-------------------------+
        |
        v
 +-------------------------+
 |  Claude / Codex / other |  <- NOT built this increment
 +-------------------------+
```

Chugel core never imports, names, or special-cases a specific provider
anywhere. It depends on exactly one abstraction (`AgentInvoker`, section 3)
and two structured envelope shapes (section 4) that are provider-neutral by
construction — they describe *what Emilio/Emma must produce*, never *how a
provider produces it*.

## 2. Trust boundaries

Extending `orchestrator/CHUGEL_V1.md` section 2's existing trust-boundary
model with one new boundary this increment introduces:

- **Chugel core ↔ AgentInvoker adapter**: the adapter is untrusted input,
  exactly as every other caller is already untrusted to Chugel's core
  (`CHUGEL_V1.md` section 2: "Chugel does not assume any caller-supplied
  dict is well-formed, safe, or honest merely because of who or what claims
  to have produced it"). An adapter's response is validated against the
  structured response envelope (section 4) before Chugel's core does
  anything with it; a response that doesn't conform is rejected exactly
  like a malformed Mission Record already is.
- **AgentInvoker adapter ↔ provider**: not this increment's concern to
  design in full (no adapter exists), but the adapter is the trust boundary
  between Chugel and an external network service — no code in Chugel core
  ever talks to a provider directly, so a provider's own bugs, prompt
  injection attempts embedded in repository content the agent reads, or
  service outages can only ever reach Chugel core as a structured response
  (or a timeout/error), never as raw provider output Chugel core has to
  parse or trust.
- **Human ↔ Chugel core**: unchanged from `CHUGEL_V1.md` — a human still
  triggers every invocation explicitly; this increment does not add any
  path for Chugel to decide on its own that an invocation should happen.

## 3. Provider abstraction: `AgentInvoker`

A single `typing.Protocol` (stdlib, no dependency), living in Chugel core,
that every provider adapter must satisfy:

```python
class AgentInvoker(Protocol):
    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        ...
```

Chugel core's request-builder functions (section 4) construct an
`AgentInvocationRequest`; Chugel core's response-consumer functions
(section 4) accept only an `AgentInvocationResult`. Nothing in Chugel core
ever imports a concrete adapter — the concrete adapter is chosen by
whatever caller wires the system together (a human running a script, later
a thin orchestration layer), injected into whatever function needs to
invoke an agent, exactly the same dependency-injection shape
`orchestrator/validator.py`/`orchestrator/state_machine.py` already use
relative to `chugel.py` (chugel.py calls their public functions without
knowing their internals).

**Why a `Protocol` and not an abstract base class**: no inheritance
requirement, no shared base-class state, no risk of a provider adapter
accidentally inheriting behavior it shouldn't — structural typing only,
matching this module's existing "small, deterministic, no framework"
character (`CHUGEL_V1.md` section 1).

**This increment does not implement any concrete `AgentInvoker`.** A future,
separately-authorized increment implements `ClaudeAgentInvoker`,
`CodexAgentInvoker`, etc., each living under a new `orchestrator/adapters/`
package (not created this increment), each solely responsible for the
provider-specific mechanics of actually starting a session, sending the
request content, parsing the raw provider response into the structured
envelope, and setting `fresh_context_attested` honestly.

## 4. Structured request/response envelopes

**Design insight, stated explicitly because it materially shrinks this
increment's scope**: the schema *already* defines exactly the structured
output Emilio and Emma must produce — `builder_evidence_entry` and
`reviewer_evidence_entry` in `orchestrator/schemas/mission_record.schema.json`,
unchanged since Increment #3/#4 and already independently reviewed. This
increment does not invent a new output format for either agent. It defines
the *envelope around* producing exactly those existing shapes — the
request that asks for one, and the wrapper that carries the response
(success or failure) back to Chugel core.

### `AgentInvocationRequest` (provider-neutral, one shape for every role)

```python
@dataclass(frozen=True)
class AgentInvocationRequest:
    invocation_id: str          # UUID, generated by Chugel core, never the adapter
    mission_id: str
    agent_role: str             # "emilio" | "emma" | (future) "david"
    attempt: int                # 0 or 1, mirrors builder_evidence/reviewer_evidence attempt
    task: dict                  # allow-listed fields only -- see per-role builders below
    requested_at: str           # RFC 3339 UTC, set by Chugel core
    requested_fresh_context: bool  # corrective addition (Increment #7 corrective cycle) --
                                    # always True when agent_role == "emma", set only by
                                    # Chugel core's request builder, never by a caller or
                                    # adapter; always False for "emilio" (he has no
                                    # independence requirement, see section 8/9). This is
                                    # signal 1 of the four-signal model in "The central
                                    # question" -- Chugel's own statement of intent, the one
                                    # half of freshness Chugel core can itself guarantee.
```

`task` is never a free-text prompt string Chugel core constructs — it is a
structured dict whose *content* an adapter is responsible for turning into
whatever a specific provider's actual input format requires (a prompt, a
tool-call payload, a structured API request body). Chugel core does not
know or care how an adapter turns `task` into provider input; it only
guarantees `task`'s *content* is exactly the allow-listed data appropriate
to `agent_role` (sections 6–7 below specify the allow-list per role
precisely).

### `AgentInvocationResult` (provider-neutral, carries outcome + optional evidence)

```python
@dataclass(frozen=True)
class AgentInvocationResult:
    invocation_id: str              # must match the request it answers
    outcome: str                    # "completed" | "failed" | "timeout" | "invalid_output" | "unavailable"
    provider: str | None            # e.g. "claude", "codex" -- metadata only, see section 12
    model: str | None               # e.g. a specific model identifier -- metadata only
    responded_at: str               # RFC 3339 UTC, set by the adapter
    fresh_context_attested: bool    # see section 8/9 -- mandatory, never defaulted True,
                                     # signal 2 of 4 -- adapter's own claim, insufficient alone
    provider_session_id: str | None       # corrective addition (Increment #7 corrective
                                           # cycle) -- opaque identifier the adapter reports
                                           # for the session it used, if the provider exposes
                                           # one; signal 3 of 4. Never interpreted as, or
                                           # containing, conversation content -- see section 8.
    provider_conversation_id: str | None  # same as above, for a provider that distinguishes
                                           # "conversation" from "session" as separate
                                           # concepts; either or both may be None (section 8
                                           # specifies the no-identifier case explicitly).
    evidence: dict | None           # present only if outcome == "completed"; must already
                                     # match builder_evidence_entry / reviewer_evidence_entry
                                     # shape for the requested agent_role
    error_detail: str | None        # present only if outcome != "completed"; free text,
                                     # never read by any decision logic (section 14)
```

Chugel core's response-consumer function for a given `agent_role`:

1. Confirms `result.invocation_id == request.invocation_id` (refuses a
   mismatched response outright — this is the first, cheapest defense
   against a caller accidentally wiring up a stale or wrong response).
2. For `agent_role == "emma"`, runs the full fresh-context check in
   section 8 — not `fresh_context_attested` alone: confirms
   `request.requested_fresh_context is True` (always true by construction,
   checked anyway as a defensive assertion against a corrupted request
   object), confirms `result.fresh_context_attested is True`, and, when a
   comparable prior identifier exists (section 8), confirms
   `result.provider_session_id`/`provider_conversation_id` do **not**
   equal Emilio's recorded identifier for the same mission — refusing
   unconditionally, regardless of `fresh_context_attested`'s value, if
   they match.
3. Branches on `outcome` (section 11 specifies every branch).
4. On `"completed"`, passes `result.evidence` **unmodified, as a single
   opaque dict** into the existing, unmodified
   `chugel.record_builder_evidence()` / `chugel.record_reviewer_evidence()`
   — which already validates it via the unmodified
   `validate_mission_record()` before ever writing anything
   (`CHUGEL_V1.md` sections 5 and 11, unchanged by this design). The
   response-consumer function does not itself re-implement or duplicate
   any schema validation — it is a thin adapter from
   `AgentInvocationResult` to the already-existing Chugel operation,
   nothing more.

## 5. Invocation lifecycle

For a single invocation, in order, every step synchronous, no retries or
polling inside Chugel core itself (section 15):

1. Caller (human, or a future thin script) decides an invocation is
   authorized — Chugel core never decides this on its own (unchanged from
   `CHUGEL_V1.md` section 1's non-responsibility list).
2. Caller calls the role-specific request builder (e.g.
   `build_emilio_invocation_request(mission_id, attempt, ...)` — reads the
   current Mission Record via the existing, unmodified
   `chugel.get_mission()`, and constructs an `AgentInvocationRequest` using
   only the allow-listed fields for that role (sections 6–7).
3. Caller passes the request to whichever concrete `AgentInvoker` it has
   wired up; that adapter performs the actual provider call (out of this
   increment's scope) and returns an `AgentInvocationResult`.
4. Caller passes the result to the matching response-consumer function
   (section 4, step 4 above), which either writes the evidence via the
   existing, unmodified `chugel.py` operations, or — on any non-`"completed"`
   outcome — writes nothing to the Mission Record's schema-validated arrays
   at all (section 11).
5. The caller (human or script) decides the next step based on the
   now-current Mission Record state, exactly as already happens today —
   this design adds no autonomous continuation (section 15).

No step in this lifecycle is triggered by a previous step's own output
without a caller back in the loop between them — this is the mechanism
that satisfies "avoid autonomous loops" (section 15), stated once here and
not repeated as a separate bolt-on rule.

## 6. Representing an Emilio invocation

`build_emilio_invocation_request(mission_id, attempt, task_override=None)`:

- Reads the mission via `chugel.get_mission()`.
- `task` is built from an allow-list of exactly:
  - `mission_definition_history[-1]` (the current authorized scope —
    `outcome`, `scope`, `non_goals`, `acceptance_criteria` fields only;
    never the `authorized_by`/`authorized_at`/`authorization_decision_ref`
    attribution fields, which are audit metadata Emilio has no use for and
    should never see reproduced back at him as if it were his own claim);
  - `repository` (worktree/branch/base-SHA — what he needs to actually
    work in the isolated worktree, per `AGENTS.md`'s Required preflight);
  - if `attempt == 1` (the corrective build): the **cited findings only**
    from `reviewer_evidence[0].findings` (Emma's attempt-0 findings that
    triggered the corrective cycle) — never her `verdict` enum value
    reproduced as if it were an instruction, never anything from
    `reviewer_evidence[0].rechecked_commands` beyond what's needed to
    understand a finding, and never any of *his own* prior
    `builder_evidence[0]` fields (his attempt-0 `conclusion`/`assumptions`
    are not fed back to him as "context" — the corrective cycle addresses
    Emma's findings against the current artifact, not a summary of his own
    prior reasoning, matching `agents/emilio/CONTRACT.md` section 5:
    "during a corrective cycle, only the specific findings Reviewer/QA
    cited — never an open invitation to make unrelated changes").
- `agent_role = "emilio"`, `attempt` as given by the caller (must match
  what `record_builder_evidence()` would accept next — Chugel core does not
  duplicate that check, `validate_mission_record()` already enforces it
  when the resulting evidence is eventually recorded).

## 7. Representing an Emma invocation

`build_emma_invocation_request(mission_id, attempt)`:

- Reads the mission via `chugel.get_mission()`.
- `task` is built from an allow-list of exactly:
  - `mission_definition_history[-1]`'s content fields (the authorized
    acceptance criteria she is reviewing against — same allow-list as
    Emilio's, same exclusion of attribution metadata);
  - `builder_evidence[attempt].artifact` (the artifact identity to
    review — commit SHA or patch identity);
  - **corrective addition (Increment #7 corrective cycle, closing Emma's
    independent-review finding P1-B)**: `builder_evidence[attempt].changed_files`,
    `builder_evidence[attempt].checks`, and
    `builder_evidence[attempt].handoff_document_ref` — reconciling this
    allow-list against `agents/emma/CONTRACT.md` section 5's own Inputs
    text, re-read fresh for this correction: "Emma requires, before
    reading the implementation: the authorized task and acceptance
    criteria; exact base and head SHAs; the review artifact's mode and
    identity...; **the complete changed-file list and diff; the
    Builder's handoff and command evidence**; and applicable repository
    instructions... If any required input is absent... she returns
    BLOCKED." The prior version of this document excluded these three
    fields outright, which — read literally against her own contract's
    mandatory escalation rule — would have left every invocation missing
    inputs she is contractually owed, forcing her to `BLOCKED` before
    ever reaching independent review. This correction restores them, and
    draws the actual boundary her contract draws: **evidence vs.
    conclusion, not "everything Emilio produced" vs. "only artifact
    identity."** `changed_files` (path/reason pairs) and `checks`
    (command/exit_status/result — raw, factual command output) are
    evidence: what Emilio touched and what he ran, neither claiming
    anything was correct. `handoff_document_ref` is a **pointer to
    evidence, not authority** — Emma may follow it to read the full
    human-readable handoff per `docs/zentra/HANDOFF_TEMPLATE.md`, but
    **must independently validate anything material she finds through
    it**, exactly as she must independently validate `changed_files`/
    `checks` rather than trust them at face value — the pointer changes
    what she has *access to*, never what she is permitted to *conclude
    from prose alone*. This is the same distinction her contract itself
    draws in the very next sentence: "Emma receives the artifact and the
    authorized task/acceptance criteria — never the Builder's narrative
    conclusion or self-assessment **as a substitute for either**" — the
    prohibition is on treating narrative as a *substitute* for her own
    derivation, not on her ever seeing evidence her contract names as
    required input;
  - `repository` (so she can locate the same worktree/artifact Emilio
    worked in, to independently inspect and rerun checks herself, per her
    Allowed Tools);
  - if `attempt == 1` (the re-review after one corrective cycle): her
    **own** `reviewer_evidence[0]` findings (what she found last time, so
    she can verify the cited findings were actually addressed) — this is
    Emma's own prior work, not Emilio's narrative, and is exactly what
    `docs/zentra/REVIEWER_QA_V1.md`'s re-review procedure already requires
    her to check against; it is not a substitute for independent
    re-derivation on the current artifact, only the reference point for
    "were the findings I raised actually fixed."
- `agent_role = "emma"`, `attempt` as given.
- **Explicitly, by omission, still never included** — this boundary is
  unchanged by the correction above, which added evidence categories,
  never narrative ones: `builder_evidence[*].conclusion` (his own
  summary, explicitly "not approval" per
  `agents/emilio/CONTRACT.md` section 12), `builder_evidence[*].risks`,
  `builder_evidence[*].assumptions`, `builder_evidence[*].safety_confirmation`
  (his own self-assessment of his own safety compliance), and
  `builder_evidence[*].rollback_notes` — every one of these is Emilio's
  own judgment *about* the evidence, not the evidence itself, and none
  of them is named in `agents/emma/CONTRACT.md` section 5's Inputs list
  — also still never included: any prior `reviewer_evidence[*].verdict`/
  `findings` from a *different* attempt number than the one being
  reviewed now.

## 8. Fresh-context requirements

**Corrected in the Increment #7 bounded corrective cycle**, closing
Emma's independent-review finding P1-A. The single-boolean gate this
section previously specified is replaced by a multi-signal model — the
boolean remains, but it is no longer the only thing checked, and it is
explicitly documented as insufficient by itself.

### Signal 1: `requested_fresh_context` (Chugel-set, code-enforced)

- `AgentInvocationRequest.requested_fresh_context` is set by Chugel core's
  own request-builder function, **always `True`** when
  `agent_role == "emma"`, **always `False`** when `agent_role ==
  "emilio"` (he has no independence requirement — same asymmetry as
  before, now expressed as Chugel's own stated intent rather than only as
  a response-time check).
- No caller, adapter, or any other code path can set or override this
  field on a request Chugel core builds — it is not a parameter the
  request-builder functions in sections 6–7 accept from their caller.
  This is the one half of "freshness" Chugel core can actually guarantee,
  because Chugel core is the only writer.

### Signal 2: `fresh_context_attested` (adapter-claimed, required but insufficient alone)

- `AgentInvocationResult.fresh_context_attested` remains a **required**
  field (no default) — missing it is a construction-time error.
- The response-consumer for `agent_role == "emma"` still refuses
  (raises, writes nothing) if it is not the literal `True` — exact
  identity check, no truthiness coercion, matching this project's
  existing `decided_by == HUMAN_DECIDER` discipline.
- **This flag is never set by Chugel core** — only the adapter may set
  it, and Chugel core has no code path that could set it on the
  adapter's behalf.
- **Explicitly, and this is the correction's whole point: this signal
  alone is not sufficient.** A response with `fresh_context_attested ==
  True` still must pass signal 3's check below before being accepted for
  `agent_role == "emma"`.

### Signal 3: `provider_session_id` / `provider_conversation_id` (objective, cross-checkable, code-enforced when available)

- These are opaque strings — Chugel core never parses, interprets, or
  extracts meaning from their content, only compares them for equality.
  They are metadata identifying *which* session was used, never a
  container for *what was said in* that session — adding them introduces
  no new channel for Emilio's narrative to reach Emma, since an
  identifier token carries no conversation content by construction.
- **Deterministic comparison rule**: when building an Emma
  response-consumer call for a given `mission_id` and `attempt`, Chugel
  core looks up the `provider_session_id`/`provider_conversation_id`
  **Chugel core itself recorded** from the immediately preceding Emilio
  invocation for that same mission (see section 10 — this requires the
  `invocation_log[]` concept identified there, or an equivalent
  in-memory/caller-supplied record for the pre-schema-change period; this
  document does not assume the log exists yet, see the "no usable
  identifier" case below). If Emma's result reports a
  `provider_session_id` (or `provider_conversation_id`) **equal** to
  Emilio's recorded value, the response-consumer **refuses
  unconditionally** — regardless of `fresh_context_attested`'s value,
  regardless of `requested_fresh_context` having correctly been `True` on
  the request. A `True` attestation can never override a detected literal
  match.
- **Retry case**: if a *prior* Emma invocation attempt (for the same
  mission/attempt) failed or timed out and Chugel core recorded *that*
  attempt's `provider_session_id` (when the provider exposed one), a
  retry's response is compared against **both** Emilio's identifier and
  the prior failed Emma attempt's identifier — a retry must not silently
  reuse a session from a previous failed/timed-out Emma invocation either,
  not only guard against reusing Emilio's.
- **When the provider exposes no usable session/conversation identifier**
  (both fields `None`): the comparison is **skipped, not treated as a
  pass** — Chugel core cannot compare what does not exist. In this case,
  signal 2 (`fresh_context_attested`) is the *only* signal available, and
  the residual gap (signal 4, below) is at its widest — this is stated
  here explicitly rather than silently falling back to "no identifiers,
  so nothing to check, proceed." The response-consumer still proceeds in
  this case (there being no stronger check available does not itself
  justify refusing an otherwise-valid response), but this is a materially
  weaker guarantee than when identifiers are available, and any future
  adapter implementation should treat "provider exposes no session
  identifier" as a reason to prefer a different provider or a different
  integration approach for Emma's invocations specifically, not as an
  acceptable steady state.
- **What this signal does NOT prove**: two *different*-looking identifier
  values do not mathematically prove a fresh context. This check detects
  **accidental** reuse (the same session handle literally passed twice,
  the most likely real-world failure mode for a hastily-written or buggy
  adapter) — it cannot defeat a **deliberately dishonest** adapter that
  fabricates two distinct-looking identifiers while actually feeding the
  same underlying context to both calls. This limitation is stated here
  precisely so it is never mistaken for a stronger guarantee than it is.

### Signal 4: the residual, unverifiable guarantee (never claimed as solved)

- That the provider's own infrastructure genuinely isolates one session
  from another when the adapter asks for a new one, and that the adapter
  is not lying about the identifiers themselves — remains entirely
  outside what any of signals 1–3 can verify. This is INV-5 in section 9,
  unchanged in substance by this correction: the correction narrows the
  practical exposure (an accidental-reuse bug is now caught), it does not
  close the theoretical gap (a determined, dishonest adapter still is not
  detectable by Chugel core alone).

## 9. Emma independence invariants

Exactly what this design *can* claim, no more:

- **INV-1 (code-enforced, structural — corrected, Increment #7 corrective
  cycle)**: Emma's request can never contain Emilio's `conclusion`,
  `assumptions`, `risks`, `rollback_notes`, or `safety_confirmation` — his
  own judgment about his work, for any attempt, ever — enforced by the
  allow-list in section 7 being the only code path that populates `task`,
  with no other function anywhere touching it. **Narrowed correctly, not
  weakened**: `handoff_document_ref` was previously listed here as
  excluded; it is now allow-listed (section 7) because `agents/emma/CONTRACT.md`
  section 5 names "the Builder's handoff" as required input — INV-1 now
  draws the line exactly where her contract draws it (evidence Emma may
  access vs. Emilio's own conclusions she may never treat as a
  substitute for her own), not one document-category short of it.
- **INV-2 (code-enforced, structural)**: an Emma response missing or
  failing `fresh_context_attested is True` is refused before any evidence
  is written — enforced by section 8's response-consumer check.
- **INV-2a (code-enforced, structural — corrective addition, Increment #7
  corrective cycle)**: whenever Chugel core has a recorded
  `provider_session_id`/`provider_conversation_id` for the preceding
  Emilio invocation (or a prior failed/timed-out Emma attempt) on the same
  mission, an Emma response reporting an identical identifier is refused
  **unconditionally**, even when `fresh_context_attested is True` — this
  is a strictly stronger check than INV-2 alone provides, and closes the
  specific gap Emma's independent review identified: a lazy or dishonest
  adapter could previously satisfy the only check (a boolean) with zero
  resistance. **This invariant is conditional on the provider exposing a
  comparable identifier at all** (section 8) — it does not apply, and
  provides no protection, when no such identifier exists.
- **INV-3 (code-enforced, structural)**: nothing in the invocation layer
  can set `human_gates.*` — the response-consumer functions call only
  `record_builder_evidence()`/`record_reviewer_evidence()`, which are
  themselves (unmodified, already-reviewed) structurally incapable of
  touching `human_gates` (`chugel.py`, unchanged).
- **INV-4 (code-enforced, structural)**: nothing in the invocation layer
  can set `state`/`state_history` — evidence recording and state
  transition remain fully separate operations (`chugel.transition()`
  unchanged, never called from within a response-consumer function).
- **INV-5 (adapter-trusted, NOT code-enforced — the honest gap)**: that
  the adapter's session for Emma is actually, technically isolated from
  Emilio's — depends entirely on the adapter's own honesty and the
  provider's actual session-isolation behavior. **This is the one
  invariant this document cannot claim is guaranteed**, and any summary
  of this design that omits INV-5's caveat is misrepresenting it.

## 10. Mission Record implications

**No schema change is authorized or made by this increment.** The
`builder_evidence_entry`/`reviewer_evidence_entry` shapes already carry
everything a response's `evidence` field needs to satisfy. What this
increment identifies, but does not implement, as a genuine future need:

- A place to record `invocation_id`, `provider`, `model`,
  `provider_session_id`/`provider_conversation_id` (corrective addition,
  Increment #7 corrective cycle — this is what section 8's cross-mission
  comparison actually reads back for the "preceding Emilio invocation"
  case; without this persisted, a caller/adapter would have to keep that
  identifier in memory across calls itself, which is fragile across a
  process restart between invocations), `requested_at`/`responded_at`,
  and `outcome` for **every** invocation attempt, including failed/
  timeout/invalid ones that never produce a
  `builder_evidence`/`reviewer_evidence` entry at all. Today's schema has
  no array for this — `builder_evidence[]`/`reviewer_evidence[]` only ever
  hold *successful, schema-valid* evidence, `maxItems: 2` each, and a
  failed invocation attempt (timeout, provider outage) must not consume
  one of those two precious slots or the schema's own attempt-sequencing
  invariants would be violated by a hole in the sequence.
- **ASSUMPTION, flagged for a future increment's explicit decision**: the
  natural fix is a new, separate, unbounded-length array (e.g.
  `invocation_log[]`) recording every attempt — success or failure — with
  `provider`/`model` as pure metadata fields the schema marks explicitly
  as **never read by `validate_mission_record()` or `can_transition()`
  for any decision** (the same "metadata, never authority" principle
  `MISSION_RECORD.md` already applies to `decision_ref`). This is not
  designed in detail here because it requires a schema change, which is
  out of this increment's authorized scope — flagged as the first concrete
  schema-design question for whichever increment actually builds a
  concrete `AgentInvoker`.
- Until that exists, a failed/timeout/invalid invocation attempt is
  **not persisted in the Mission Record at all** by this design — it is
  the caller's (human's, or a future script's) responsibility to notice
  and decide what to do next, exactly as a human already notices today
  when an assistant's chat turn doesn't produce a usable result. This is
  a real, disclosed limitation of V1's invocation architecture, not a
  silent gap.

## 11. Failure / retry semantics

Every `AgentInvocationResult.outcome` value and what Chugel core's
response-consumer does with it:

| `outcome` | Meaning | Chugel core's action |
|---|---|---|
| `"completed"` | Provider returned a structurally valid `evidence` payload | Pass `evidence` unmodified to `record_builder_evidence()`/`record_reviewer_evidence()`; that function's own `validate_mission_record()` call is the real gate — a `"completed"` outcome with an `evidence` payload that still fails schema validation is refused there, exactly like any other invalid mutation |
| `"failed"` | Provider ran but produced no usable result (its own error) | Nothing written to `builder_evidence`/`reviewer_evidence`; `error_detail` is available to the caller for logging/escalation but is never read by any Chugel decision logic |
| `"timeout"` | No response within the adapter's own timeout | Same as `"failed"` — nothing written; Chugel core has no timeout logic of its own (that is entirely the adapter's responsibility, since only the adapter knows what "too long" means for its provider) |
| `"invalid_output"` | Provider responded, but `evidence` doesn't parse as a dict, or lacks required top-level shape the adapter itself could check before returning | Same as `"failed"` — nothing written. (Note: even an `outcome` of `"completed"` still passes through full `validate_mission_record()` before writing, so `"invalid_output"` is a courtesy the adapter can give early; it is not Chugel core's only defense) |
| `"unavailable"` | Provider/service could not be reached at all | Same as `"failed"` — nothing written |

**Retry semantics**: Chugel core has none. A caller who receives any
non-`"completed"` outcome and wants to retry does so by calling the
*same* role-specific request builder again — since nothing was written on
failure, this does not consume an `attempt` slot, does not increment
`corrective_cycle_count`, and is indistinguishable in the Mission Record
from the invocation having simply not happened yet. This is a deliberate
consequence of section 10's disclosed gap (failures aren't persisted) —
it means retries are cheap and safe *precisely because* failures leave no
trace to conflict with, at the cost of no audit trail for how many times
an invocation was attempted before succeeding (the same trade-off named
explicitly in section 10, not a new one introduced here).

## 12. Preventing free prose from becoming authority

Restating and extending `MISSION_RECORD.md` Design Principle 3 and
`CHUGEL_V1.md` section 1's existing non-responsibility ("interpret,
summarize, or generate any free-text field... never reads them for a
decision") for this specific new surface:

- `AgentInvocationResult.error_detail` is free text. No code anywhere in
  Chugel core branches on its content — the *only* thing that ever decides
  Chugel core's behavior on a failure is the `outcome` enum value, a closed
  set of five literal strings.
- `provider` and `model` (section 4) are free-ish text (a `str | None`),
  but are explicitly documented, here and wherever they are eventually
  added to a schema (section 10), as **metadata only** — never compared,
  branched on, or used to decide anything about whether an invocation
  succeeded, whether evidence is valid, or what state a mission may
  transition to. A future implementation that added `if provider ==
  "claude": ...` logic anywhere in a decision path would be a direct
  violation of this principle and should be treated as a defect exactly
  as severe as reading `state_reason` to decide a transition would be
  today.
- The **only** field of an `AgentInvocationResult` any decision logic ever
  reads is `outcome` (a 5-value enum) and `fresh_context_attested` (a
  boolean, checked only for identity to `True`). Everything else is either
  passed through opaquely (`evidence`, immediately handed to the existing,
  unmodified schema validator) or never read by decision logic at all
  (`error_detail`, `provider`, `model`).

## 13. Corrective-cycle semantics

The existing one-bounded-corrective-cycle model
(`AGENTS.md` "Bounded correction and escalation";
`orchestrator/validator.py`'s `maxItems: 2` /
`corrective_cycle_count maximum: 1`) is **not changed** by this design —
it is mapped onto four invocations in a fixed sequence, each a separate,
human-triggered step per section 5's lifecycle:

1. **Emilio, attempt 0** (`build_emilio_invocation_request(mid, 0)`) →
   `record_builder_evidence()`.
2. **Emma, attempt 0** (`build_emma_invocation_request(mid, 0)`) →
   `record_reviewer_evidence()`. If verdict is `CHANGES_REQUIRED`
   (existing, unmodified `_check_reviewer_verdict_consistency` still
   governs what verdicts are legal with what findings) →
3. **Emilio, attempt 1** (`build_emilio_invocation_request(mid, 1)`,
   receiving *only* Emma's attempt-0 cited findings per section 6) →
   `record_builder_evidence()` — this is the existing
   `corrective_cycle_count` atomic-set behavior (`chugel.py`, unchanged)
   firing exactly as it already does today when a human manually drives
   this step.
4. **Emma, attempt 1** (`build_emma_invocation_request(mid, 1)`, a
   **fresh** invocation per section 9's invariants, receiving the current
   artifact plus her own attempt-0 findings for cross-check per section 7)
   → `record_reviewer_evidence()`. Whatever verdict this produces is
   terminal for this mission's review cycle — the schema's `maxItems: 2`
   makes a third attempt structurally unrepresentable, so this design adds
   no separate enforcement of "only one cycle," it inherits the existing
   one.

No invocation in this sequence is ever triggered by the *response* of the
previous one — each is a separate call from the caller's own decision
(section 5), even when that caller is, in practice, immediately deciding
"the verdict was CHANGES_REQUIRED, so now invoke Emilio again." The
decision to proceed remains outside Chugel core in every case.

## 14. Avoiding autonomous loops

Four independent reasons this architecture cannot become an autonomous
loop, stated together because they are complementary, not redundant:

1. **No invocation is ever triggered by a prior invocation's own
   response** (section 5) — every step requires a fresh call from a
   caller that is not Chugel core itself.
2. **No function in Chugel core calls `AgentInvoker.invoke()` from inside
   a response-consumer** — request-building, invoking, and
   response-consuming are three separate functions a caller sequences
   itself; none of the three calls another of the three.
3. **`chugel.py`'s existing operations remain single synchronous calls**
   (`CHUGEL_V1.md` section 1: "retry, poll, or run in a loop" is already
   an explicit non-responsibility) — this design adds no new operation
   that violates that, since request-building and response-consuming are
   thin wrappers around the same existing, unmodified operations.
4. **The schema's own hard caps** (`maxItems: 2`, `corrective_cycle_count
   maximum: 1`) make a runaway invocation sequence structurally
   unrepresentable even if some future bug tried to loop — the fourth
   invocation in section 13's sequence is the last one any valid Mission
   Record can ever hold for one mission, independent of any invocation-layer
   discipline holding up on its own.

## 15. Preserving José's human gates

Unchanged from `CHUGEL_V1.md` sections 9–10, restated for this new
surface: no `AgentInvocationRequest`, `AgentInvocationResult`, request
builder, or response consumer defined in this document ever reads,
writes, or references `human_gates` in any way. `decide_gate()` and
`decide_scope_change()` remain the *only* two functions in the entire
system capable of changing a gate's status, and neither is called from
anywhere in this design. An agent's structured response — however it
scores, whatever verdict it returns — cannot, by construction, become a
gate approval; only a literal, separately-authorized `decide_gate()` call
carrying José's own attribution can.

## 16. David compatibility

`AgentInvocationRequest.agent_role` is a string, not a closed enum baked
into the dataclass's type — `"emilio"` and `"emma"` are the two values this
increment defines builders for, but the shape itself does not assume only
two roles will ever exist. Adding David later means: a new
`build_david_invocation_request()` following the exact same allow-list
pattern (reading `agents/david/CONTRACT.md`, once it exists, to determine
David's own Inputs/Outputs per `agents/AGENT_STANDARD.md` sections 5–6,
exactly as sections 6–7 above were derived from Emilio's and Emma's
existing contracts), and a response-consumer that maps a valid David
response onto whatever Mission Record operation his role needs (most
likely `chugel.propose_scope_change()`, already generic enough per
`CHUGEL_V1.md`'s own Evolution Path section to accept a David-originated
proposal without modification). **No change to `AgentInvoker`,
`AgentInvocationRequest`, or `AgentInvocationResult` is anticipated to be
needed for David** — the shapes are already role-generic. This is an
INFERENCE, not a FACT, since David's `CONTRACT.md` does not exist yet and
could in principle need something this design did not anticipate; it is
recorded here as the current best expectation, not a guarantee.

## 17. Security considerations

- **No new secret-handling surface.** `AgentInvocationRequest`/`Result` are
  pure data; provider credentials belong entirely to a concrete adapter
  (not built this increment) and never pass through Chugel core.
- **Prompt injection is an adapter/provider concern, not Chugel core's** —
  Chugel core never constructs a natural-language prompt string at all
  (section 4: `task` is structured data, not prose); whatever an adapter
  does with that data to build actual provider input, and how it defends
  against injected content the agent might read from repository files, is
  entirely the adapter's designed responsibility, out of this increment's
  scope, and must be addressed explicitly when a concrete adapter is
  designed.
- **`evidence` from a `"completed"` result is untrusted input**, exactly
  like every other Mission Record mutation — it is validated by the
  unmodified `validate_mission_record()` before being written, never
  trusted merely because it came from a `"completed"` outcome. A
  compromised or buggy adapter that fabricates a fake `"completed"` result
  with a plausible-looking `evidence` payload is caught by the same
  schema/cross-field checks that already catch a human's typo — this is
  not new protection this document adds, it is the existing protection
  correctly continuing to apply to a new input source.
- **`invocation_id` mismatch detection** (section 4, step 1) is a cheap
  first-line defense against response/request confusion, not a security
  boundary against a malicious adapter — a malicious adapter could easily
  fabricate a matching `invocation_id`. This is stated so the check is not
  mistaken for stronger protection than it is.

## 18. Testing strategy for a future implementation

Not run this increment (design only), specified for whoever implements it:

- **Allow-list completeness (corrected, Increment #7 corrective cycle)**:
  for every field `builder_evidence_entry`/`reviewer_evidence_entry`
  defines, a test asserting whether or not it reaches Emma's `task` —
  explicitly enumerating the still-forbidden fields (`conclusion`,
  `assumptions`, `risks`, `rollback_notes`, `safety_confirmation`) and
  asserting each one, if present in a constructed test Mission Record
  with an obviously-identifying sentinel value, never appears anywhere in
  the resulting `AgentInvocationRequest` for Emma (e.g. serialize the
  request to a string and assert the sentinel value is absent); **and**,
  symmetrically, a test asserting `changed_files`, `checks`, and
  `handoff_document_ref` — populated with their own sentinel values in
  the test fixture — **do** appear in Emma's `task`, so a future
  accidental re-narrowing of the allow-list back toward the pre-correction
  shape is caught as a regression, not just an accidental leak in the
  other direction.
- **`fresh_context_attested` refusal**: a response with the field missing,
  `False`, `None`, `0`, or `"true"` (string) for `agent_role == "emma"`
  must all be refused, with nothing written to the Mission Record.
- **Session/conversation identity comparison (corrective addition,
  Increment #7 corrective cycle)**: a response with `fresh_context_attested
  == True` **and** `provider_session_id` (or `provider_conversation_id`)
  identical to the value Chugel core recorded for the preceding Emilio
  invocation on the same mission must still be refused — proving INV-2a
  actually overrides a `True` attestation rather than merely
  supplementing it. Symmetrically: a response with `fresh_context_attested
  == True` and a *different* `provider_session_id` must be accepted (once
  section 11's other checks pass) — proving the comparison doesn't
  over-refuse when the signal is genuinely clean. A third case: both
  `provider_session_id` fields `None` (no usable identifier) must proceed
  on `fresh_context_attested` alone, neither refused nor treated as a
  stronger pass than a matched-and-distinct identifier would provide.
- **Emilio asymmetry**: the same `False` value for `agent_role ==
  "emilio"` must **not** be refused — proving the asymmetry in section 9
  is intentional and tested, not an oversight either direction.
- **`invocation_id` mismatch**: a result whose `invocation_id` doesn't
  match its request is refused before touching `evidence` at all.
- **Every `outcome` branch** (section 11's table) tested for "nothing
  written" except `"completed"`, and `"completed"` tested for both a
  schema-valid `evidence` (written) and a schema-invalid one (refused via
  the existing, unmodified `validate_mission_record()` — no duplicate
  validation logic in the invocation layer itself, tested to make sure of
  that too — e.g. a schema-invalid `evidence` should fail with the exact
  same `ValidationError` codes `chugel.py`'s existing tests already
  exercise, not a different, invocation-layer-specific error).
- **Corrective-cycle sequence** (section 13): the four-invocation sequence
  driven end-to-end against a real (temp-directory-isolated) `chugel.py`
  Mission Record, using a fake in-test `AgentInvoker` stub (never a real
  provider — no network in tests, matching every existing test file's
  convention), asserting the resulting Mission Record matches exactly what
  a human manually driving the same four steps today would produce.
- **No autonomous continuation**: a stub `AgentInvoker` whose `invoke()`
  implementation calls back into the request-builder/response-consumer
  functions (simulating a buggy adapter trying to self-chain) — assert
  this is possible to write (nothing stops the *adapter* from doing
  something unwise) but that Chugel core's own functions never do it
  themselves, i.e. a static/structural check (or a test asserting no
  Chugel-core function's own call graph includes a call to
  `AgentInvoker.invoke`) rather than a runtime behavioral one, since the
  guarantee here is "Chugel core doesn't", not "nothing anywhere could
  possibly loop."

## 19. Explicit non-goals (this increment)

Restated in one place, all deliberate:

- No `agents/chugel/` directory, identity, or `CONTRACT.md` — Chugel
  remains ordinary deterministic code (`agents/AGENT_STANDARD.md`'s
  exclusion, unchanged).
- No Claude API, OpenAI/Codex API, or any concrete `AgentInvoker`
  implementation.
- No subprocess execution of any kind.
- No GitHub, Git, CI, or Render automation.
- No Budget Governor.
- No David implementation (`agents/david/` not created).
- No CLI.
- No Jarvis UI or any other human-facing interface beyond what already
  exists (a human driving this via direct function calls/chat, as today).
- No Mission Record schema change (section 10's `invocation_log[]` idea is
  identified, not designed in detail or implemented).
- No change to `AGENTS.md`, any agent `CONTRACT.md`,
  `orchestrator/validator.py`, or `orchestrator/state_machine.py`.

## 20. What capabilities are actually needed vs. what exists today

**FACT, verified by reading this repository fresh for this increment**:
none of the following exist anywhere in this codebase today:

- A programmatic way to start a new, isolated LLM provider session/thread
  and receive structured (not free-text-only) output back.
- Any Claude API or OpenAI/Codex API client, credential, or configuration.
- Any timeout/cancellation mechanism for a long-running or hung external
  call.
- Any retry/backoff policy implementation.
- Any mechanism to verify, from outside a provider's own infrastructure,
  that a claimed "fresh session" is actually isolated from a prior one.

**What a concrete adapter implementation increment would need to add**
(not built here): the actual API client for at least one provider; a
session-management strategy that can honestly set
`fresh_context_attested`; a way to turn this design's structured `task`
dict into that provider's actual input format (a prompt, a tool schema, or
similar); a way to parse that provider's actual output back into the
`evidence` shape (or determine it doesn't conform and return
`"invalid_output"`); and its own timeout/retry policy, entirely contained
within the adapter, invisible to Chugel core.

## 21. Core determinism vs. adapter responsibility

Belongs in Chugel core (`orchestrator/`, no network, no provider
knowledge, testable with a stub `AgentInvoker`):

- `AgentInvoker` Protocol definition.
- `AgentInvocationRequest` / `AgentInvocationResult` dataclasses.
- The role-specific request builders (`build_emilio_invocation_request`,
  `build_emma_invocation_request`, and later `build_david_invocation_request`)
  and their allow-lists.
- The response-consumer functions, including every check in sections 4, 8,
  and 11.
- All invocation-lifecycle bookkeeping that is itself deterministic:
  `invocation_id` generation (`uuid.uuid4()`, same pattern
  `chugel.create_mission()` already uses), `requested_at` timestamping
  (same `_now()` pattern already in `chugel.py`).

Belongs in a future adapter package (`orchestrator/adapters/`, not created
this increment, one module per provider):

- The actual network call to a specific provider's API.
- Provider-specific authentication/credential handling.
- Turning a `task` dict into that provider's actual prompt/input format.
- Parsing that provider's raw output into the `evidence` shape, or
  detecting it does not conform and returning `"invalid_output"`.
- Session/thread creation and the honest determination of
  `fresh_context_attested`.
- Provider-specific timeout, retry, and backoff policy.
- Anything at all that depends on which provider is behind the call — if a
  future change to Chugel core would need an `if provider == "..."`
  branch, that change belongs in an adapter instead, not in core.

## 22. Acceptance criteria for a future implementation increment

When a future increment actually implements `AgentInvoker`,
`AgentInvocationRequest`/`Result`, the role-specific request builders, and
the response consumers (still without any concrete provider adapter,
which would be a separate, later increment again), it is complete only
when:

1. `AgentInvoker` is defined as a `typing.Protocol`; Chugel core imports
   no concrete provider anywhere.
2. Every allow-list in sections 6–7 is implemented as a positive
   enumeration (never a field-removal/deny-list), verified by the
   allow-list-completeness tests in section 18, **and** section 7's
   corrected allow-list (`changed_files`, `checks`, `handoff_document_ref`
   included; `conclusion`, `assumptions`, `risks`, `rollback_notes`,
   `safety_confirmation` still excluded) matches
   `agents/emma/CONTRACT.md` section 5's Inputs text exactly — **an
   implementation is not complete merely because it excludes everything
   Emilio produced; it is complete only when it excludes exactly his
   judgment/self-assessment and nothing else her contract names as
   required input** (corrected, Increment #7 corrective cycle, closing
   finding P1-B).
3. **(Corrected, Increment #7 corrective cycle, closing finding P1-A)**
   An implementation is **not** certifiable as complete merely because
   `fresh_context_attested == True` is checked. All of the following
   must hold together:
   - `AgentInvocationRequest.requested_fresh_context` is set by Chugel
     core's own request-builder, always `True` for `agent_role == "emma"`,
     never settable by a caller or adapter;
   - `AgentInvocationResult.fresh_context_attested` is a required
     (no-default) field, and the Emma response-consumer refuses on
     anything other than the literal `True`;
   - `AgentInvocationResult.provider_session_id`/`provider_conversation_id`
     exist on the envelope, and, whenever Chugel core has a recorded
     comparable identifier from the preceding Emilio invocation (or a
     prior failed/timed-out Emma attempt) on the same mission, the
     response-consumer refuses **unconditionally** on an exact match,
     regardless of `fresh_context_attested`'s value;
   - the no-usable-identifier case (both fields `None`) is handled by
     explicitly proceeding on signal 2 alone, never by silently treating
     absence as a passing comparison;
   - all of the above verified by the tests in section 18, including the
     specific adversarial case of a response with `fresh_context_attested
     == True` and a `provider_session_id` identical to Emilio's recorded
     one, which must still be refused.
4. No function in the new code ever calls
   `chugel.decide_gate()`/`chugel.decide_scope_change()`/`chugel.transition()`
   — evidence recording and every other Chugel operation remain fully
   separate, verified by direct code review (grep for these three call
   sites is a legitimate mechanical check here, given how small and
   auditable this surface is meant to stay).
5. `outcome` is the only field of `AgentInvocationResult` (besides the
   identity/attestation checks in points 3–4 above) that any decision
   logic branches on; `provider`/`model`/`error_detail` are read by
   nothing beyond logging/pass-through, verified by the section 18 test
   asserting no decision path depends on their content.
6. The corrective-cycle sequence test (section 18) passes end-to-end
   against a stub `AgentInvoker`, producing a Mission Record identical to
   one a human would produce driving the same four steps manually today.
7. No file outside the new invocation-layer module(s) and their tests is
   touched; `chugel.py`, `validator.py`, `state_machine.py`, the schema,
   and every agent `CONTRACT.md` remain byte-identical.
8. No new dependency is added unless explicitly authorized (the design
   above needs none — `typing.Protocol`, `dataclasses`, `uuid`, `datetime`
   are all stdlib).
9. An independent Emma review confirms all of the above from a fresh
   reading of the actual diff.

## 23. Evolution path — toward real Claude/Codex, and later David

Consistent with `CHUGEL_V1.md`'s own Evolution Path section: this is a
dependency map, not a numbered roadmap, and nothing here commits to
building any specific piece next.

**This increment (design only) → a first concrete `AgentInvoker`.**
Depends on: this design being authorized; a decision about which provider
to integrate first (Claude, given this very system already runs on it, is
the natural first candidate, but that choice is not made here); resolving
section 20's capability gaps (session management, credential handling,
timeout policy) for that one provider; and a schema decision on section
10's `invocation_log[]` question, since a real adapter will produce real
failures worth recording.

**A first concrete adapter → automated Emilio invocation specifically.**
Depends on the above, plus `agents/emilio/CONTRACT.md` section 16 already
being satisfied by construction (invocation-by-Chugel changes only who
presses "go," never his authority) — no new authorization needed from his
contract, only the adapter existing.

**Automated Emilio invocation → automated Emma invocation specifically.**
Depends on the above, **and** on section 9's INV-5 gap being addressed as
concretely as the chosen provider allows — at minimum, a documented,
audited procedure for how the adapter actually guarantees session
isolation for Emma specifically, reviewed with the explicit adversarial
case "the adapter lied about `fresh_context_attested`" in mind, before
this is ever trusted for a real mission. This is, and should remain, the
single most carefully gated step in this whole evolution path, matching
`agents/emma/CONTRACT.md` section 16's own framing of her independence as
"the single most safety-critical clause in this contract."

**A second provider (Codex or other) → true provider-agnosticism proven
in practice, not just in design.** Depends on the first adapter's actual
shape revealing whether this document's `AgentInvoker` abstraction was
correctly scoped — the real test of "provider-agnostic" is successfully
adding a second, structurally different provider without needing to
change `AgentInvoker`, `AgentInvocationRequest`, or `AgentInvocationResult`
themselves; if that turns out to require a change, this document's
abstraction boundary was wrong and should be revisited honestly rather
than patched around.

**David — depends on `agents/david/CONTRACT.md` existing first** (not
created by this or any prior increment), and, per section 16, is expected
but not guaranteed to need no changes to this design's core shapes.

**Higher autonomy levels for any agent — depends on all of the above, and
on explicit human approval per that agent's own `CONTRACT.md`**, exactly
as `CHUGEL_V1.md`'s own Evolution Path already states: nothing about
building invocation infrastructure itself advances any agent's autonomy
level (`agents/emilio/CONTRACT.md` / `agents/emma/CONTRACT.md` section 3)
— it only makes it *possible* for José to grant one later, with actual
evidence behind that decision.
