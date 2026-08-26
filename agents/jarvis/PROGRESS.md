# Jarvis — Progress

Mission 001 establishes Jarvis's contract and inert Phase 0 foundation. This
file records no claim that conversational intake, research execution, mission
submission, provider invocation, or autonomous coordination exists.

Mission 002 adds deterministic read-only mission listing and bounded status
projection. It does not add free-text interpretation, reasoning, mission
submission, execution, providers, caching, or autonomous coordination.

Mission 003A implements the Trusted Knowledge Core for independent review. It
adds no execution, reasoning, provider, freshness, search, or ranking path.

Mission 003B adds deterministic repository freshness resolution (a single,
narrowly scoped subprocess call, confined to `jarvis/repository_freshness.py`)
and deterministic `knowledge search` eligibility filtering and ranking. It
does not add free-text interpretation, reasoning, recommendation, decision
records, providers, autonomous promotion, mission submission, execution, or
any capability outside deterministic read-only retrieval.

Mission 004 adds Jarvis's first write path to Chugel (`jarvis/mission_write.py`,
gate-state-guarded, never a fabricated attribution) and a coordinator
(`jarvis/mission_coordinator.py`) that drives the already-existing
`orchestrator.autonomous_runner` build/review/corrective pipeline together
with two new, general orchestrator modules (`orchestrator/publish_executor.py`,
`orchestrator/merge_executor.py`) that push, open/reuse a pull request, poll
CI within a bounded timeout, and merge via `--merge` only. It adds
`orchestrator/publish_identity_repair.py`, which restores a missing publish
identity only against the durable, independently-reviewed
`builder_evidence` artifact for the `PASS`-verdict attempt, never from a
live GitHub read alone. It adds a human-confirmed, never-automatic resume
path out of `BLOCKED` restricted to the four publish/merge-pipeline states.
It does not add production deploy automation, any change to Chugel's
`TRANSITIONS` table, any new adapter type, or any relaxation of the
subscription-CLI-only or Emma-independence guarantees already in place. José
remains required at exactly three authorization gates plus any `BLOCKED`
confirmation; Mission 004 automates only the Emilio/Emma relay and the
publish/merge mechanics between those points, never the human decisions
themselves.
