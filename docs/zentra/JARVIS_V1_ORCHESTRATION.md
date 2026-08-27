# Jarvis Mission 004 — Autonomous End-to-End Orchestration

Grounded exactly in `agents/jarvis/CONTRACT.md` section 22 and
`agents/jarvis/PLAYBOOK.md`'s two Mission 004 sections; this document is a
handoff summary, not an independent source of authority.

## What this mission adds

- Jarvis's first write path to Chugel, through exactly three narrow,
  disclosed seams: `jarvis/mission_write.py` (gate decisions, mission
  creation, `BLOCKED` resume — all gate-state-guarded and attribution-checked
  on top of Chugel's own enforcement), `jarvis/mission_coordinator.py` (drives
  the already-existing `orchestrator.autonomous_runner` pipeline plus the two
  new publish/merge executors), and `jarvis/mission_context.py` (the only
  additional module permitted to search trusted knowledge for this purpose).
- Two new, general orchestrator modules — `orchestrator/publish_executor.py`
  and `orchestrator/merge_executor.py` — that close the previously-unautomated
  gap between `PUBLISH_AWAITING_AUTHORIZATION` and `MERGED`. Neither is
  Jarvis-specific; they sit alongside `orchestrator/chugel.py`/`wiring.py`/
  `autonomous_runner.py` as infrastructure any caller with the matching
  human-gate authorization can drive.
- `orchestrator/publish_identity_repair.py`, which restores a `publish.commit_sha`
  lost to a crash only by comparing a live GitHub read against the durable,
  independently-reviewed `builder_evidence[attempt].artifact` for the attempt
  whose review carries the literal verdict `PASS` — never by trusting the live
  read alone.
- Three new evidence-recording operations on `orchestrator/chugel.py`
  (`record_publish_pr`, `record_ci_run`, `record_merge_commit`), added to that
  module directly rather than a separate `orchestrator/publish_evidence.py`
  file — a disclosed, deliberate deviation from the original implementation
  plan's file list, made because these operations need the same private
  lock/read/write primitives `record_publish_commit()` already uses, and
  Chugel is already documented as "the sole canonical mission, evidence,
  gate, and state layer." Reaching into those primitives from a second module
  would have weakened that boundary rather than respected it.

## What this mission does not add

Production deploy automation (`DEPLOY_PENDING`/`VERIFYING_PRODUCTION`/
`COMPLETED` remain untouched), any change to Chugel's `TRANSITIONS` table, any
new adapter type, any relaxation of the subscription-CLI-only or Emma-
independence guarantees, and any mechanism letting Jarvis satisfy
`decided_by == "jose"` other than a direct, current-turn relay. José remains
required at exactly three authorization gates (`scope_authorization`,
`publish_authorization`, `merge_authorization`) plus an explicit confirmation
to resume any `BLOCKED` mission — Mission 004 automates the Emilio/Emma relay
and the publish/merge mechanics between those points, never the human
decisions themselves.
