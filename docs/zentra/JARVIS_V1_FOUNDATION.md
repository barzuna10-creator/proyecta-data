# Jarvis V1 Foundation — Mission 001

## Scope

This document defines the inert Phase 0 boundary. Jarvis is a reasoning and
coordination agent, while all authority-bearing behavior remains outside this
increment. Nothing here submits a mission, invokes Chugel, starts an agent,
uses a provider, or changes product/deployment state.

## Authority boundary

A `MissionDraft` is an immutable, non-authoritative proposal. Schema validity
means only that its structure and local invariants are acceptable. Its digest
identifies exact bytes. Neither condition authorizes anything.

The exact command

```text
AUTHORIZE JARVIS MISSION DRAFT <uuid> REVISION <positive-integer> SHA256 <lowercase-64-hex>
```

may be parsed into an `AuthorizationIntent` only. In Phase 0 that intent has
the persisted effect `none_phase_0`: it is not a José attribution, Chugel gate
decision, Mission Record, mission submission, or execution permission.

## MissionDraft

The canonical schema is `jarvis/schemas/mission_draft.schema.json`. Drafts
carry intent, a proposed mission definition, evidence, risks, open questions,
and optional commit-bound repository context. They structurally exclude
Mission Record state, human gates, authorization fields, provider selection,
and execution instructions.

Every change creates the next integer revision. Once displayed with a digest,
a revision is immutable. Authorization intent is current only when draft ID,
latest revision, algorithm, and digest all match and no open questions remain.

## Evidence semantics

- `FACT`: directly observed and supported by at least one cited source.
- `INFERENCE`: derived from cited evidence, with an explicit inferential gap.
- `ASSUMPTION`: temporarily unverified, stating how it can be resolved.
- `INTENT`: a cited human statement; never proof of behavior or authorization.

Evidence identifiers are unique within a draft. Dependencies must resolve,
must not cite themselves, and must form an acyclic graph. Repository-file and
Git-commit evidence is bound to a canonical full commit SHA.

Every evidence source's `observed_at` is a real UTC calendar timestamp at
whole-second precision, exactly `YYYY-MM-DDTHH:MM:SSZ`. Validation is explicit
and deterministic: timezone offsets, fractional seconds, malformed shapes,
impossible calendar dates, and leap seconds are rejected. JSON Schema format
checking is supplemental and is not trusted as the enforcement boundary.

## Canonicalization

Only validated drafts can be canonicalized. All strings must already be NFC;
floats and non-finite numbers are forbidden. Objects are serialized with UTF-8,
sorted keys, no ASCII escaping, compact separators, no BOM, and no trailing
newline. Array order is preserved. SHA-256 is computed over exactly those
bytes and rendered as lowercase hexadecimal. The digest is held in a separate
envelope and never hashes itself.

## Storage

`FileJarvisStore` requires an explicit root outside the repository in normal
use. It stores only immutable draft envelopes and append-only authorization
intents. Paths are derived exclusively from validated UUIDs and revisions;
symlinks are refused; writes are owner-only and atomically published without
overwriting an existing revision.

This store never contains or mirrors canonical mission states, human gates,
dispatch results, provider output, or execution status. Losing it cannot alter
a canonical mission. Chugel remains the sole canonical mission store.

## Deferred capabilities

Conversational reasoning, context retrieval, research execution, provider
invocation, mission submission, worktree creation, autonomous-runner
integration, web UI, daemons, GitHub automation, deployment, David, and a
Research agent are all explicitly deferred.
