# Zentra V1 Handoff — Mission #002, Bounded Corrective Cycle

This is the one bounded corrective cycle following Emma's `CHANGES
REQUIRED` review of Mission #002 (see `docs/zentra/HANDOFF_MISSION_002.md`
for the original mission handoff — this document covers only the
correction, not the full mission again).

## Authorized task

Fix exactly the one P1 finding from Emma's review, nothing else:

> `eliminar_plano()` does not clear the new processing state, which can
> let a late callback resurrect a deleted plano, and leave the user
> locked by `plano_estado='procesando'`.

Required correction, as specified by José:

- [x] invalidate/clear `plano_procesamiento_id`;
- [x] clear/update `plano_estado` consistently on delete;
- [x] ensure any late callback becomes a no-op;
- [x] ensure the user can start a new analysis immediately after deletion;
- [x] add regression tests for delete-during-processing and late-result
      resurrection.

Explicitly out of scope for this cycle (per instruction): Emma's two P3
notes (JSONResponse raw-serialization test coverage; frontend polling
not retrying a transient network error). Neither was touched.

## What changed

**`api/repositorio_proyectos.py` — `eliminar_plano()` only.** The
existing `UPDATE` that already cleared `plano_nombre_archivo`,
`plano_analisis`, `plano_fecha_analisis` now also clears `plano_estado`,
`plano_procesamiento_id`, and `plano_error_mensaje` to `NULL`, in the
same statement. No new query, no new transaction, no locking added —
the existing single `UPDATE ... WHERE id = ?` was already atomic per
SQLite's own per-statement guarantee, and reasoned through both possible
orderings against a concurrent callback/timeout write (see below) to
confirm no lock was needed.

**Why this closes both parts of the finding, mechanically:**

- **Resurrection:** `_completar_analisis_plano()` and
  `_manejar_timeout_analisis()` both guard their writes with `WHERE id =
  ? AND plano_procesamiento_id = ?` (`_manejar_timeout_analisis` also adds
  `AND plano_estado = 'procesando'`). Once `eliminar_plano()` sets
  `plano_procesamiento_id` to `NULL`, neither guard can match the old
  token again — `rowcount` becomes `0`, the write is a no-op, by
  construction of guards that already existed before this fix. Nothing
  new needed to be built to make the late write inert; clearing the
  token was sufficient.
- **Lockout:** the per-user concurrency check in
  `iniciar_analisis_plano()` is `SELECT COUNT(*) ... WHERE propietario_id
  = ? AND plano_estado = 'procesando'`. Once `plano_estado` is `NULL`
  again, that row no longer counts — the guard clears in the same
  instant the delete commits, with no dependency on the orphaned job
  ever finishing.

**Race ordering considered, not assumed:** could a concurrent
callback/timeout write interleave with `eliminar_plano()`'s own write in
a way that leaves an inconsistent result? Both orderings were traced:
- Delete commits first → the later callback/timeout write's `WHERE`
  clause no longer matches (token now `NULL`) → no-op. Final state:
  cleared, correct.
- Callback/timeout commits first (writes `plano_analisis`/`plano_estado`
  back in, using the still-valid token, microseconds before the delete
  runs) → `eliminar_plano()`'s own `UPDATE` is unconditional on current
  state (`WHERE id = ?` only, no token guard) — it clears everything
  regardless of what was just written. Final state: cleared, correct.

Both orderings converge on the same correct outcome without needing
`BEGIN IMMEDIATE` or any new lock — SQLite's own single-writer
serialization of the two independent single-statement `UPDATE`s is
sufficient. No watchdog-timer cancellation was added in
`eliminar_plano()` either: the existing guard already makes a stale
timer's eventual firing a safe no-op (verified by
`test_timeout_tardio_no_resucita_un_plano_borrado`, which also asserts
the executor recycle path is correctly skipped for an already-deleted
attempt), so a dangling `threading.Timer` doing nothing for up to 120s
is a resource-tidiness matter, not a correctness gap — and outside the
literal scope of what was requested.

**`tests/test_analizar_plano.py`:**
- One test added to the existing `PruebaEliminarPlano` class (already
  a "plano is `'listo'`" scenario): confirms `plano_estado` and
  `plano_error_mensaje` are also cleared, not just the three original
  columns.
- New class `PruebaEliminarPlanoDuranteProcesando` (5 tests): delete
  mid-flight clears state and token; a late callback with the old token
  no longer resurrects the plano; a late watchdog timeout with the old
  token is a no-op *and* does not attempt to recycle the executor
  (verified via an explicit `assert_not_called()` on
  `_reciclar_executor_planos`); a new analysis can start immediately
  after deletion in the same project; and in a different project.

## Repository state

- Worktree path: `/Users/joseandresbarzuna/proyecta-data-worktrees/mission-002-plan-stability`
  (same worktree as the original mission — continued, not recreated)
- Branch: `mission/002-plan-processing-stability`
- Base for this corrective commit: `b780534` (the commit Emma reviewed)
- Head SHA: `149f43d892b13c57be52f2e8fb64587df6d55f6d`
- Final status: clean, no uncommitted changes.

## Review artifact identity

- Mode: `immutable commit`
- Commit: `149f43d892b13c57be52f2e8fb64587df6d55f6d` (backend only — this
  correction did not touch the frontend repository, which is unaffected
  and unchanged since Emma's original review of `1e7764d`)
- Builder revalidation immediately before this handoff: `git status
  --short --branch` clean, `git log -1` matches, `git diff --stat b780534
  HEAD` shows exactly the two files listed above.

## Changed files

| File | Reason |
|---|---|
| `api/repositorio_proyectos.py` | `eliminar_plano()`: clear `plano_estado`, `plano_procesamiento_id`, `plano_error_mensaje` alongside the three pre-existing columns. |
| `tests/test_analizar_plano.py` | New `PruebaEliminarPlanoDuranteProcesando` class (5 tests) + one added test on existing `PruebaEliminarPlano`. |

No other file touched. Frontend (`proyecta-web`) untouched — the P1
finding was entirely a backend state-machine gap.

## Tests and checks executed

| Command | Working directory | Exit status | Exact result |
|---|---|---:|---|
| `python3 -m unittest tests.test_analizar_plano tests.test_agregar_plano_estado -v` | backend worktree | 0 | `Ran 38 tests` / `OK` (32 pre-existing + 6 new) |
| `python3 scripts/ci/run_hermetic_tests.py` | fresh sparse-checkout mirror (excludes tracked `database/proyecta.db`, matches CI's own isolation; corrective changes copied in from the committed worktree) | 0 | `Ran 767 tests` / `OK` (761 pre-existing + 6 new) |
| `git diff --check` | backend worktree | 0 | clean |
| `python3 scripts/zentra_verify.py --expected-base 878b57e... --allow <8 paths>` | backend worktree | 0 | `ZENTRA VERIFY: PASS` |

## Skipped or unavailable checks

| Check | Reason | Residual risk |
|---|---|---|
| None | This correction is backend-only and fully covered by the hermetic suite + `zentra_verify.py`. | N/A |

## Risks

- None new. This correction only tightens an existing write path;
  it does not introduce new state, new endpoints, or new concurrency
  primitives.

## Assumptions

- Same assumptions as the original mission handoff — unchanged.

## Rollback notes

`git revert 149f43d` on `mission/002-plan-processing-stability` removes
exactly this correction, restoring the pre-correction `eliminar_plano()`
(and, correspondingly, reintroducing the P1 gap) without affecting any
other commit on this branch.

## Safety confirmation

- [x] No existing user checkout or user work was reset, cleaned,
      stashed, deleted, or overwritten.
- [x] No direct change was made to `main`.
- [x] No push, pull request, merge, or deployment occurred.
- [x] No production data, database, secret, credential, or infrastructure
      was accessed or modified (all tests ran against ephemeral temp
      SQLite files).
- [x] No protected or out-of-scope file changed — confirmed by
      `zentra_verify.py` and by the two-file diffstat above.
- [x] The complete diff was inspected.
- [x] Review identity is an authorized immutable commit (`149f43d`),
      revalidated immediately before this handoff.

## Builder conclusion

The one P1 finding is addressed at its root cause (the missing state
clear), not patched around it — traced both possible write orderings
against a concurrent callback/timeout to confirm no race was
introduced, and added tests that directly exercise the two failure
modes Emma named (resurrection, lockout) rather than only testing the
new code's happy path. Full suite remains green (767/767). This is
evidence for Emma's one allotted re-review, not a claim that the
correction is itself certified — per `/AGENTS.md`, if a re-review still
finds a blocking issue, this escalates to a human rather than continuing
indefinitely.
