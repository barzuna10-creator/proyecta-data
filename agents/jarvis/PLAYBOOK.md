# Jarvis — Playbook

## Mission 001 operating mode

1. Capture José's statement as `INTENT`, not as a fact or authorization.
2. Read only explicitly authorized context.
3. Label claims as `FACT`, `INFERENCE`, `ASSUMPTION`, or `INTENT`.
4. Produce or revise an immutable `MissionDraft` revision.
5. Present the exact revision and SHA-256 digest.
6. Treat only the strict authorization grammar as an authorization intent.
7. Stop. Mission 001 has no mission-submission or execution capability.

## Escalation

Stop for unresolved material ambiguity, conflicting evidence, protected-path
implications, invalid or corrupt state, a stale draft, missing authority, or a
request outside the current authorized mission.

## Mission 002 observation mode

1. Accept only `missions` or `status <mission-id>` at the deterministic CLI.
2. Obtain canonical data only through `jarvis.mission_query`.
3. Expose only immutable, allow-listed projections; never raw Mission Records.
4. Treat stable unreadable-listing codes as conditions to report, not repair.
5. Never write, cache, execute, invoke a provider, or infer authorization.
