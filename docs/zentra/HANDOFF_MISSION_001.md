# Zentra V1 Handoff — Mission #001

## Authorized task

Production health/deploy guard (Master Roadmap NOW #1). Per José's Build
Authorization message:

- Implement `GET /health`: verify DB connectivity, verify `productos` has
  more than zero rows, return 200 when healthy / 503 when DB unreachable or
  catalog empty, leak no DB paths/SQL/exceptions/stack traces/product or
  customer data, be side-effect-free.
- Implement `GET /version`: never touch the database, expose only safe
  deployment/version identity, use a commit SHA only from an independently
  verified runtime source, otherwise `"unknown"`.
- Catalog readiness threshold: exactly `COUNT(productos) > 0` — no higher
  arbitrary floor.
- `render.yaml`: narrowly-scoped, explicitly authorized change limited to
  `healthCheckPath: /health` only. No other Render/deployment config change
  authorized.
- No webhook / scheduled smoke test / post-deploy GitHub Actions pipeline
  (explicitly deferred, not implemented).
- No `httpx` added. No Plan Reader, purchasing, auth, or production-data
  changes.

## Acceptance criteria

- [x] `GET /health` returns `200 {"status": "ok", ...}` when DB reachable and
      `productos` has ≥1 row.
- [x] `GET /health` returns `503` with a non-leaking body when the DB is
      unreachable.
- [x] `GET /health` returns `503` with a non-leaking body when `productos`
      has exactly zero rows.
- [x] `GET /version` returns commit/version identity without touching the
      database, and works even when `/health` would report degraded.
- [x] Tests cover all four states above plus the non-leakage assertion,
      passing in the existing direct-call convention (no `httpx` added).
- [x] `render.yaml` change limited to exactly one line (`healthCheckPath:
      /health`) plus an explanatory comment — nothing else in the file
      touched.
- [x] No changes to Plan Reader, purchasing, auth, or production data.

## Repository state

- Worktree path: `/Users/joseandresbarzuna/proyecta-data-worktrees/mission-001-health-guard`
- Branch: `mission/001-production-health-guard`
- Base SHA: `5b61642c134ae4d8120397b32ad3bc3c7baeef5b` (current `origin/main`
  at mission start, confirmed via `git fetch origin` immediately before
  creating the worktree)
- Head SHA: `cb7962b9024a1e37c7ddd3f9952f8b036a2b3dbb`
- Initial status: clean worktree, `git status --short --branch` showed no
  drift from `origin/main` at creation.
- Final status: clean, one commit ahead of the recorded base
  (`## mission/001-production-health-guard...origin/main [ahead 1]`), no
  uncommitted changes.

## Review artifact identity

- Mode: `immutable commit`
- Immutable commit SHA: `cb7962b9024a1e37c7ddd3f9952f8b036a2b3dbb`
- Builder verification time: immediately after commit, same session.
- Builder revalidation result immediately before handoff: `git status
  --short --branch` clean, `git log -1` matches the recorded head SHA
  exactly, `git diff --stat` against base SHA matches the commit's own
  diffstat exactly (3 files, 237 insertions, 0 deletions).

## Changed files

| File | Reason |
|---|---|
| `api/main.py` | Added `GET /health` (DB connectivity + non-empty-catalog check, 200/503) and `GET /version` (deployment identity, DB-independent). |
| `render.yaml` | Added `healthCheckPath: /health` only — narrowly-scoped, explicit human authorization for this exact single-line change. |
| `tests/test_main.py` (new) | 8 tests: healthy state, empty catalog, unreachable DB, non-leakage assertion, `/version` DB-independence, `/version` with/without `RENDER_GIT_COMMIT`, `/version` working while `/health` is degraded. |

## Tests and checks executed

| Command | Working directory | Exit status | Exact result |
|---|---|---:|---|
| `python3 -m unittest tests.test_main -v` | mission worktree, `PYTHONPATH=.`, `DATABASE_PATH=/tmp/zentra-mission-001-check/proyecta.db` | 0 | `Ran 8 tests in 0.005s` / `OK` |
| `python3 scripts/ci/run_hermetic_tests.py` | separate sparse-checkout mirror worktree (excludes tracked `database/proyecta.db`, mirroring CI's own isolation exactly — mission files copied in uncommitted, never touching the mirror's tracked state), `PYTHONPATH=.`, fresh temp `DATABASE_PATH` | 0 | `Ran 739 tests in 115.092s` / `OK` (includes all 8 new `test_main` tests, each individually reporting `ok`) |
| `git diff --check` (working tree, then `--cached`) | mission worktree | 0 / 0 | clean, no whitespace errors |
| `python3 scripts/zentra_verify.py --expected-base 5b61642c134ae4d8120397b32ad3bc3c7baeef5b --allow "api/main.py" --allow "tests/test_main.py" --allow "render.yaml"` | mission worktree | **1** | `FAIL: deployment/infrastructure file changed: render.yaml` / `ZENTRA VERIFY: FAIL (1 violation(s))`. This is the tool's **unconditional** infra-path guard (`INFRA_EXACT = {"render.yaml", "vercel.json", "procfile"}` in `scripts/zentra_verify.py`) — it has no `--allow` override by design, so it fails on *any* `render.yaml` change regardless of authorization context. This is the single expected violation, exactly matching the one explicitly human-authorized change, and no other. |

## Skipped or unavailable checks

| Check | Reason | Residual risk |
|---|---|---|
| Live Render health-check behavior (probe interval, restart-on-failure semantics, rollout gating) | Cannot be exercised from this repository/worktree — Render platform behavior, not code. | Unverified until an actual deploy exercises `healthCheckPath`; the endpoint's own logic is fully covered by unit tests. |
| End-to-end HTTP request against a running `uvicorn` process | No `httpx`/`TestClient` in the dependency set, and adding one was explicitly out of scope. Endpoints tested via direct function call, matching every other router test in this codebase. | Low — both endpoint functions are plain synchronous functions with no FastAPI-request-context dependency (no `Depends()`, no `Request` param), so direct-call testing exercises the exact same code path an HTTP request would. |

## Risks

- `scripts/zentra_verify.py` will always report the `render.yaml` infra-guard
  as `FAIL` for this diff by design — Emma/José should treat that specific,
  singular violation as expected and already reviewed here (full diff
  reproduced below), not as a surprise or an unauthorized change.
- `RENDER_GIT_COMMIT` availability at runtime was verified against Render's
  own published documentation (independently fetched this session), not
  against this specific live service instance — if Render's actual behavior
  differs from documented behavior, `/version` will report `"commit":
  "unknown"` rather than fail, which is the intended safe fallback either way.

## Assumptions

- `productos` table always exists in a correctly-migrated deployment; a
  missing table (schema drift, not just an empty table) is treated as a
  `database: "error"` state by `/health` rather than a distinct third state,
  since both are exceptions caught by the same broad `except`. This matches
  the two-state (`database`/`catalog`) model specified in the authorization.
- The mission's `--workers 1` single-worker deployment means `/health`
  shares the process with all other traffic — a pre-existing architectural
  constraint, not something this mission changes or needs to change.

## Rollback notes

Revert commit `cb7962b9024a1e37c7ddd3f9952f8b036a2b3dbb` on branch
`mission/001-production-health-guard` (e.g. `git revert cb7962b`) to remove
`GET /health`, `GET /version`, their tests, and the `render.yaml`
`healthCheckPath` line as a single unit, without affecting any other commit
on this branch or on `origin/main`. No rollback was performed as part of
this handoff.

## Safety confirmation

- [x] No existing user checkout or user work was reset, cleaned, stashed,
      deleted, or overwritten.
- [x] No direct change was made to `main` (all work on isolated branch
      `mission/001-production-health-guard`, created from a fresh worktree).
- [x] No push, pull request, merge, or deployment occurred.
- [x] No production data, database, secret, credential, or infrastructure
      was accessed or modified (all tests ran against ephemeral temp SQLite
      files; `render.yaml` is deploy *configuration*, not live
      infrastructure or production data).
- [ ] **No protected or out-of-scope file changed** — **not fully true**:
      `render.yaml` (a protected path under `/AGENTS.md`) *was* changed,
      under José's explicit, narrowly-scoped authorization for exactly
      `healthCheckPath: /health` and nothing else in that file. The complete
      diff is one addition (5 lines: 1 blank-adjacent comment block + 1
      config line):
      ```diff
      +    # MASTER_ROADMAP.md, NOW #1: sin esto, Render no tiene forma de saber
      +    # si la instancia realmente puede servir tráfico (base conectada,
      +    # catálogo con productos) más allá de que el proceso siga vivo -- ver
      +    # api/main.py GET /health.
      +    healthCheckPath: /health
      ```
      No other line in `render.yaml` changed. Flagging this explicitly for
      Emma's direct attention rather than checking the box.
- [x] The complete diff was inspected (all three changed files read in full
      before and after edit).
- [x] Review identity is an authorized immutable commit
      (`cb7962b9024a1e37c7ddd3f9952f8b036a2b3dbb`), revalidated immediately
      before this handoff.

## Builder conclusion

`GET /health` and `GET /version` are implemented per the authorized
acceptance criteria, with the exact `COUNT(productos) > 0` threshold
specified (no arbitrary floor), a non-leaking failure body (verified by an
explicit test asserting the DB path and exception-class strings never appear
in the response), and `/version` independently verified to use Render's
documented `RENDER_GIT_COMMIT` runtime variable with an explicit `"unknown"`
fallback rather than an invented value. All 739 tests in the full hermetic
suite pass, including the 8 new tests. `git diff --check` is clean. The one
`render.yaml` line is exactly the single authorized change, confirmed by
direct diff inspection above.

`scripts/zentra_verify.py` reports one `FAIL` — the tool's own unconditional
infrastructure-file guard on `render.yaml` — which has no override flag by
design and therefore fires on this authorized change exactly as it would on
any other `render.yaml` edit. This is not a scope or safety violation being
hidden; it is reported in full here for Emma's independent judgment, per
`/AGENTS.md`'s instruction that "passing tests do not override a scope,
safety, or evidence violation" — the inverse also holds: a tool's automatic
fail on an explicitly authorized, narrowly-scoped, fully-inspected change is
not itself proof of a problem, but it is not this Builder's place to
suppress, reinterpret, or self-certify past it. This is evidence for
independent review, not an approval.
