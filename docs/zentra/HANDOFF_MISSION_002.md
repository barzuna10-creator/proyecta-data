# Zentra V1 Handoff — Mission #002

## Authorized task

Plan Processing Stability (Master Roadmap NOW #4). Per José's Build
Authorization:

- Watchdog: 120 seconds (`TIMEOUT_ANALISIS_SEGUNDOS`), as approved.
- Backend and frontend both in scope for this single mission.
- No window where the current (unmigrated) frontend breaks.
- Deploy-compatibility sequencing designed explicitly, not assumed.
- Empirically verify what `ProcessPoolExecutor` can/cannot guarantee when
  recycling a worker after timeout — no unverified guarantees documented.
- Do not implement any post-deploy webhook/scheduled smoke test (separate,
  deferred scope — untouched here).
- No push, PR, merge, or deploy without separate authorization.

## Acceptance criteria

- [x] `POST /proyectos/{id}/plano?asincronico=1` responds in seconds
      (never blocked by the analysis itself), with 202 and
      `plano_estado='procesando'`.
- [x] `POST /proyectos/{id}/plano` (no query param — old/unmigrated
      clients) preserves the exact prior synchronous 200 contract, now
      with a real ceiling (120s) instead of an unbounded wait.
- [x] No `plano_estado='procesando'` can persist orphaned past a process
      restart — verified via `recuperar_analisis_interrumpidos()` and its
      dedicated tests.
- [x] No stale callback/timeout from a replaced upload attempt can
      overwrite a newer attempt's state — verified via the
      `plano_procesamiento_id` guard and a dedicated race test.
- [x] A second upload attempt by the same user while one is in flight
      (any of their projects) returns 429, never silently queued —
      verified at both the repository and router level.
- [x] No error message exposes the real exception, file paths, or
      internal detail — verified directly (`assertNotIn`).
- [x] Frontend (`proyecta-web`) always requests the async mode; its
      public `subirPlano()` signature/return type is unchanged, so the
      existing call site in `PlanoEdificio.tsx` needed no changes.
- [x] `ProcessPoolExecutor.shutdown()`'s actual (non-)guarantee on an
      already-running child is empirically verified, not assumed — see
      Empirical Findings below.

## Repository state

### Backend — `proyecta-data`

- Worktree path: `/Users/joseandresbarzuna/proyecta-data-worktrees/mission-002-plan-stability`
- Branch: `mission/002-plan-processing-stability`
- Base SHA: `878b57e339261afe60b231855f39abe9ec90abfb` (current `origin/main`
  at mission start, confirmed via `git fetch origin` immediately before
  creating the worktree)
- Head SHA: `37fe719c66b45dec648e01ab05c1ed3495510264`
- Final status: clean, one commit ahead of base, no uncommitted changes.

### Frontend — `proyecta-web` (separate repository, own remote)

- Worktree path: `/Users/joseandresbarzuna/proyecta-web-worktrees/mission-002-plan-stability`
- Branch: `mission/002-plan-processing-stability`
- Base SHA: `fd43404d70a5c92dc7f46ea7e0bb018e3878369d` (current `origin/main`
  of `barzuna10-creator/Proyecta`, confirmed via `git fetch origin`
  before creating the worktree — the repo's previously-checked-out branch,
  `integracion/presupuestos-inteligentes`, was already fully merged into
  this `origin/main`, so it was not used as the base)
- Head SHA: `1e7764d59c1acacb8b7cc4d69ab4e24070966cc8`
- Final status: clean, one commit ahead of base, no uncommitted changes.

**These are two independent repositories with no shared history** —
this handoff and Emma's review necessarily cover two separate artifacts,
not one combined diff.

## Review artifact identity

- Mode: `immutable commit`, one per repository.
- Backend commit: `37fe719c66b45dec648e01ab05c1ed3495510264` (on
  `proyecta-data`).
- Frontend commit: `1e7764d59c1acacb8b7cc4d69ab4e24070966cc8` (on
  `proyecta-web`, remote `barzuna10-creator/Proyecta`).
- Builder revalidation immediately before this handoff: both worktrees
  re-checked with `git status --short --branch` (clean) and `git diff
  --stat <base> HEAD` (matches each commit's own diffstat exactly).

## Changed files

### Backend

| File | Reason |
|---|---|
| `api/repositorio_proyectos.py` | Core of the mission: `iniciar_analisis_plano()` (async submit + DB state machine, replaces `analizar_plano()`), `analizar_plano_sincrono()` (compatibility wrapper, same contract as before), `_completar_analisis_plano()` (the one persistence path, shared by both modes via `add_done_callback`), watchdog (`_armar_watchdog`/`_manejar_timeout_analisis`), executor recycle (`_reciclar_executor_planos`, empirically verified — see below), `recuperar_analisis_interrumpidos()` (startup sweep), `AnalisisPlanoEnCurso` exception, `plano_procesamiento_id` stripped from `obtener_proyecto()`'s output. |
| `api/routers/proyectos.py` | `subir_plano()`: new `asincronico` query param dispatches between the two modes; `AnalisisPlanoEnCurso` → 429; async mode → 202 via `JSONResponse`. |
| `api/main.py` | Startup sequence gains `recuperar_analisis_interrumpidos()` (wrapped in its own `try/except`, matching the existing backup-loop's fault-isolation pattern — see Regression Found below); shutdown handler now calls `apagar_executor_planos()` instead of holding a stale direct reference to `_EXECUTOR_PLANOS` (which `_reciclar_executor_planos()` can reassign at runtime). |
| `database/agregar_plano_estado.py` (new) | Additive migration: `plano_estado`, `plano_error_mensaje`, `plano_procesamiento_id` on `proyectos`, with idempotent backfill (`plano_estado='listo'` for rows with a pre-existing `plano_analisis`). |
| `database/migraciones.py` | Registers the new migration, after `agregar_plano_proyecto` (direct column dependency) and `agregar_compras` (last in file order, no urgency). |
| `tests/test_analizar_plano.py` | Rewritten: sync-compatibility tests (renamed target, same assertions, plus a new error-path test), full async-flow tests, watchdog tests, startup-recovery tests, and new router-dispatch tests (429/202/200 mapping). |
| `tests/test_agregar_plano_estado.py` (new) | Migration idempotency and backfill correctness. |

### Frontend (`proyecta-web`)

| File | Reason |
|---|---|
| `app/lib/proyectosApi.ts` | `subirPlano()`: now requests `?asincronico=1`, polls `GET /proyectos/{id}` (unchanged endpoint, reused) every 2s (max 90 attempts / 180s, a client-side safety net above the backend's own 120s ceiling) until `plano_estado` leaves `'procesando'`. Public signature/return type unchanged. |
| `app/types/proyecto.ts` | `Proyecto` type gains `plano_estado`/`plano_error_mensaje`, matching the existing raw-snake_case field convention (no camelCase transform layer in this codebase). |

## Compatibility / deploy sequencing (no broken window)

Backend is dual-mode by design, not by deploy timing:

1. **Deploy backend first.** Old (unmigrated) frontend never sends
   `?asincronico=1`, so it keeps getting the exact prior synchronous 200
   contract — unaffected by this deploy. The only behavior change for
   unmigrated clients is a strictly-positive one: a genuinely stuck
   analysis now eventually returns an error (120s ceiling) instead of
   hanging forever.
2. **Deploy frontend second**, whenever convenient. It starts sending
   `?asincronico=1` and gets the new 202-and-poll behavior, fully
   protected from the 502/timeout risk this mission exists to fix.
3. No coordinated/simultaneous deploy is required, and no intermediate
   state exists where either side is broken — verified by design (the
   dispatch is a per-request query param, not a version negotiation that
   could itself fail).

**Honest limitation, stated explicitly, not hidden:** until the frontend
deploys, unmigrated clients are still exposed to the original Risk D
(large uploads can still exceed a proxy timeout, since the request is
still synchronous for them) — this mission's guarantee is "no regression,
no broken window," not "Risk D is instantly eliminated for every client at
the moment the backend deploys." Full protection requires both sides
deployed.

## Empirical findings on worker recycling (as required — not assumed)

Ran a standalone, throwaway script (not part of the codebase) directly
against this machine's Python 3.14 before writing the watchdog:

1. `ProcessPoolExecutor.shutdown(wait=False, cancel_futures=True)`
   returns instantly (confirmed: 0.000s) but **does not kill an
   already-running child process** — confirmed the child PID was still
   alive 1 second after `shutdown()` returned, and the associated future
   was neither cancelled nor done.
2. Tracking the child's real OS PID (via the executor's internal,
   non-public `_processes` attribute) and sending `SIGKILL` directly
   **does** reliably kill it — confirmed dead immediately after.
3. A freshly-constructed replacement `ProcessPoolExecutor` works
   immediately, with no interference from the killed process.

**Consequence for the implementation:** `_reciclar_executor_planos()`
does not rely on `shutdown()` alone. It reads `_processes` via
`getattr(executor, "_processes", {})` (defensive against a future Python
version renaming/removing this private attribute — if absent, the
SIGKILL step is skipped but a fresh executor is still built, so new work
still gets accepted), sends `SIGKILL` to each PID, then replaces the
module-level `_EXECUTOR_PLANOS`. This is documented in the code and here
as **best-effort, dependent on a non-public CPython attribute** — not
promised as a guarantee the standard library itself makes.

## Regression found and fixed during Build Mode

Initial implementation added an unconditional call to
`recuperar_analisis_interrumpidos()` inside `_bucle_arranque_en_segundo_
plano()`. The full hermetic suite caught this: two pre-existing tests in
`tests/test_respaldar_db.py` (`PruebaObservabilidadScheduler`) call that
same function directly against a minimal temp DB with no `proyectos`
table, and the new unconditional call raised `sqlite3.OperationalError:
no such table: proyectos`, uncaught — which would have also silently
killed the entire background thread in production if `proyectos`
migrations hadn't finished for any reason, **preventing the backup loop
sequenced right after it from ever starting**. Fixed by wrapping the call
in its own `try/except Exception: _logger.exception(...)`, matching the
exact fault-isolation pattern the backup loop itself already uses two
lines below. Re-ran the full suite after the fix: clean.

## Tests and checks executed

| Command | Working directory | Exit status | Exact result |
|---|---|---:|---|
| `python3 -m unittest tests.test_analizar_plano tests.test_agregar_plano_estado -v` | backend worktree | 0 | `Ran 32 tests` / `OK` |
| `python3 scripts/ci/run_hermetic_tests.py` (1st run, before the regression fix) | sparse-checkout mirror (excludes tracked `database/proyecta.db`, mirrors CI's own isolation) | 1 | `Ran 761 tests` / `FAILED (errors=2)` — both in `test_respaldar_db.py`, see Regression Found above |
| `python3 scripts/ci/run_hermetic_tests.py` (2nd run, after the fix) | same mirror | 0 | `Ran 761 tests` / `OK` |
| `git diff --check` (backend) | backend worktree | 0 | clean |
| `python3 scripts/zentra_verify.py --expected-base 878b57e... --allow <7 backend paths>` | backend worktree | 0 | `ZENTRA VERIFY: PASS` |
| `npm install` | frontend worktree | 0 | dependencies installed (fresh worktree, no `node_modules` carried over) |
| `npx eslint app/lib/proyectosApi.ts app/types/proyecto.ts app/components/proyecto/PlanoEdificio.tsx` | frontend worktree | 0 | no findings |
| `npm run build` (`next build`, includes full TypeScript check) | frontend worktree | 0 | compiled successfully, TypeScript check passed, all 10 routes generated |
| `git diff --check` (frontend) | frontend worktree | 0 | clean |

## Skipped or unavailable checks

| Check | Reason | Residual risk |
|---|---|---|
| `scripts/zentra_verify.py` against the frontend repo | The script is `proyecta-data`-specific (reads that repo's own protected-path list, e.g. `render.yaml`) and has no equivalent in `proyecta-web`. | Low — frontend changes were independently verified via lint + full TypeScript build instead, and are two small, fully-reviewed files. |
| Live end-to-end verification against production (repeating the Risk D reproduction with the new async flow) | Deploying either side requires separate authorization not yet granted (`No push, PR, merge ni deploy sin autorización separada`). | None yet — this is expected, deferred to post-authorization; the original 502 reproduction from discovery remains the baseline this fix targets. |
| Real multi-second concurrent-worker test (two genuinely simultaneous plano uploads against a real `ProcessPoolExecutor`) | All tests mock `_EXECUTOR_PLANOS` (matching this codebase's existing convention — a real analysis takes ~10s+ and needs a real 100MB+ PDF). The concurrency *guard* (429) is fully tested; the underlying single-worker queuing behavior is unchanged from the already-verified `BLOQUEO_PLANOS_PROCESSPOOL.md` work. | Low — no code path between submission and the pool itself was touched. |

## Risks

- The reliance on `ProcessPoolExecutor._processes` (private API) could
  break on a future Python upgrade — mitigated with `getattr(...,
  default={})` so a missing attribute degrades to "skip the SIGKILL, still
  recycle the pool" rather than crashing the watchdog handler itself, but
  this should be re-verified if the Python version changes.
- Until the frontend is deployed, unmigrated clients remain exposed to
  the original Risk D for large uploads (see Compatibility section) —
  this is a known, explicit, bounded limitation, not an oversight.
- The exact 120s watchdog value is a product decision already made by
  José; if real-world large-plano analyses (near the 300MB ceiling) turn
  out to routinely exceed it, that's a tuning question for after
  deployment, not a design flaw — `TIMEOUT_ANALISIS_SEGUNDOS` is a single
  named constant, trivial to adjust.

## Assumptions

- `_procesar_plano_pdf` always returns a plain dict on success (never
  `None`) — matches its pre-existing contract via `construir_analisis_
  plano()`, unchanged by this mission.
- Render's proxy/timeout behavior itself (the original, still-unresolved
  question from `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md` §6) doesn't
  need to be fully characterized for this design to work — the fix makes
  the HTTP request finish in seconds regardless of what that ceiling
  turns out to be.

## Rollback notes

Each repository can be reverted independently and safely:

- Backend: `git revert 37fe719` on `mission/002-plan-processing-stability`
  removes the async flow, watchdog, migration, and tests as a single
  unit, restoring the exact prior `analizar_plano()` behavior. No other
  commit on this branch or `origin/main` is affected.
- Frontend: `git revert 1e7764d` on the same-named branch in `proyecta-web`
  restores the prior blocking `subirPlano()`. No other commit affected.
- These are independent — reverting one does not require reverting the
  other, though only "both new" or "both old" combinations were verified;
  "new frontend + old backend" was not tested (the new frontend would
  send `?asincronico=1` to an old backend that ignores unknown query
  params and returns its old synchronous 200 response — functionally
  compatible by FastAPI's default behavior, but not explicitly verified
  in this mission).

## Safety confirmation

- [x] No existing user checkout or user work was reset, cleaned,
      stashed, deleted, or overwritten (both worktrees created fresh from
      each repo's `origin/main`).
- [x] No direct change was made to `main` in either repository.
- [x] No push, pull request, merge, or deployment occurred.
- [x] No production data, database, secret, credential, or infrastructure
      was accessed or modified during implementation (all tests ran
      against ephemeral temp SQLite files; the empirical
      `ProcessPoolExecutor` verification ran in an isolated `/tmp` scratch
      script, fully deleted afterward). The earlier production probe that
      established Risk D (105MB upload, 502 at 43s) was performed and
      fully cleaned up (test account, test project deleted) during
      **discovery**, in a prior authorized step — not during this Build
      Mode.
- [x] No protected or out-of-scope file changed — `render.yaml`,
      `.github/workflows/`, `database/*.db`, and auth/purchasing files
      were untouched, confirmed via `zentra_verify.py` (backend) and by
      the diffstat listing above (both repos, exactly the files named in
      the mission scope).
- [x] The complete diff was inspected in both repositories.
- [x] Review identity is two authorized immutable commits (one per
      repository), each revalidated immediately before this handoff.

## Builder conclusion

Both the backend async-processing mechanism and the frontend polling
client are implemented, tested (761 backend tests + full frontend
lint/build, all passing), and designed so neither deploy can break the
other. The one genuine regression the full suite caught (an unhandled
exception path that could have silently killed the backup-loop thread)
was found and fixed before this handoff, not left for review to catch.
The empirical verification José required is documented with its actual
findings, including the real limitation (`shutdown()` alone doesn't kill
a running worker) rather than an assumed guarantee. This is evidence for
independent review, not a claim of correctness — Emma's review remains
required before any deploy authorization is sought.
