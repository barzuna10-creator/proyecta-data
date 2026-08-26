# Jarvis V1 — Mission 002 Observation Boundary

Mission 002 is a deterministic, read-only observation layer. It does not add a
reasoning adapter or conversational interpretation.

## Surface

- `missions` lists canonical Mission Record candidates.
- `status <mission-id>` returns one immutable status projection.
- Any other command shape is rejected by the CLI parser.

## Canonical candidate and listing contract

A candidate filename is exactly `<mission_id>.json`, where `mission_id`
full-matches the V1 Mission Record schema pattern. Unrelated names, temporary
files, directories, and non-regular entries are ignored. A canonical symlink
is reported only as unreadable so its target is never followed.

Each listing contains exactly `mission_id`, `readable`, `state`, `bucket`,
`updated_at`, and `error_code`. Stable error codes are `MISSION_RECORD_CORRUPT`,
`MISSION_RECORD_INVALID`, and `MISSION_PATH_UNSAFE`. A listing never contains
intent, evidence, findings, gates, provider data, dispatch data, secrets, or a
raw/arbitrary record field.

## Status projection allow-list

After Chugel validates a record, Jarvis copies only:

- mission ID, state, update timestamp, current definition version, and bounded
  corrective-cycle count, plus its deterministic bucket;
- repository branch, base SHA, and isolation-confirmed flag (never worktree
  path);
- each named gate's status only;
- Emilio attempt plus the schema-defined conclusion label/text;
- Emma attempt, structured verdict, and schema-defined finding fields; and
- a human-action name derived only from a gate-waiting or blocked state.

Every projection is composed of frozen dataclasses and tuples. It retains no
reference to the input record. There is no raw-data or future-field extension
point. Intent, state reason/history, gate decision details, provider identity
or output, invocation/dispatch ledger, checks, artifacts, publication URLs,
deployment data, budgets, and unknown fields are excluded.

Every canonical state is explicitly classified as `running`,
`waiting_on_jose`, `blocked`, or `terminal`; unknown states fail closed.
`BLOCKED` is the only blocked bucket. The three authorization-wait states wait
on José. `COMPLETED`, `FAILED`, `CANCELLED`, and `ROLLED_BACK` are terminal;
all other canonical states are running. A status lookup failure returns only a
stable concise CLI error code and a non-zero result, never internal record data.

## Trust and read-only boundaries

`jarvis/mission_query.py` is the only Jarvis production module permitted to
import Chugel, and it calls only `list_missions` and `get_mission`. Chugel
remains canonical and authoritative. Jarvis does not repair corrupt state,
maintain an index/cache, or write canonical or secondary state.

Mission 002 operations invoke no provider, subprocess, network, Git, runner,
adapter, or mutation API. Tests snapshot canonical bytes before and after
queries, enforce the import/call boundary with AST checks, and exercise
malformed names, directories, symlinks, invalid/corrupt records, projection
detachment, and deterministic CLI grammar.
