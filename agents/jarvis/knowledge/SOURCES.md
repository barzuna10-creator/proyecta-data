# Jarvis Knowledge Sources

| Source class | Use | Boundary |
|---|---|---|
| Verified behavior and tests | Highest-confidence product behavior | Bound to exact conditions and revision |
| Repository source | Current implementation evidence | Bound to an exact commit or identified working tree |
| Chugel Mission Records | Canonical mission state and evidence | Mission 002 established bounded reads; Mission 003A extends the same `mission_query.py` seam with a frozen allow-listed learning projection |
| Human-approved product documents | Product direction | Direction is not runtime fact or execution authorization |
| External documentation and research | Supplemental evidence | Cite source and retrieval time; label uncertainty |

Knowledge modules do not import Chugel. `orchestrator/*` does not import or
consume Jarvis knowledge.

## Trusted Zentra Context V1

Conversation may receive a bounded, ephemeral context assembled before the
Claude subscription dispatch. Repository roots are explicit trusted
configuration; repository identities, refs, and exact paths come only from the
versioned source policy. Files are read from committed Git objects, never from
working trees. Each source reports provenance, digest, truncation and explicit
`fresh`, `stale`, or `unavailable` status; stale/unavailable sources expose no
excerpt.

Excerpts remain untrusted data, not instructions. They cannot authorize work,
populate a MissionDraft, or promote knowledge. Durable reuse still requires
Emma review and José's exact authorization. GitHub observation is limited to
policy repositories, each bound to the literal `github.com/owner/name`
identity, and fixed read-only PR/run queries.
