# P0-01 — Deployment checklist RC-1

## Pre-deployment gate

- [ ] Backend release-safety tests pass.
- [ ] Complete backend suite passes.
- [ ] Frontend TypeScript, ESLint, production build and Playwright pass.
- [ ] Both release diffs contain only P0-01 and release-safety changes.
- [ ] Render uses one worker and the expected persistent `DATABASE_PATH`.
- [ ] A production-like staging copy completes startup with a verified schema.
- [ ] A recoverable database backup exists outside the active SQLite file.
- [ ] The backup opens successfully and `PRAGMA integrity_check` returns `ok`.
- [ ] Pre-deployment counts and representative legacy quotation totals are recorded.

## Backend rollout

1. Enable the agreed maintenance/read-only window.
2. Deploy the backend before the frontend.
3. Do not route traffic until application startup completes.
4. Confirm the log contains `RESUMEN 14/14 migraciones aplicadas -- esquema verificado`.
5. Confirm there is no `STARTUP_ESQUEMA_INVALIDO`,
   `REGISTRO_ESQUEMA_INCONSISTENTE`, `MIGRACION_FALLIDA`, `no such column`,
   `no such table` or recurring `database is locked`.
6. Query `migraciones_aplicadas` and confirm all 14 registered names exist.
7. Verify actual tables, columns, indexes and canonical conversion rows.
8. Run `PRAGMA integrity_check` and require `ok`.
9. Compare representative version-1 projects and approved quotations against
   their pre-deployment totals.

## Frontend rollout

10. Deploy the new frontend only after the backend gate passes.
11. Create a new project and confirm calculation version 2.
12. Validate unit, m, m², L, kg, gallon, pound and divisible presentations.
13. Validate values immediately below, exactly on and immediately above a
    presentation boundary.
14. Confirm zero/missing price remains editable but blocks approval.
15. Confirm insufficient manual coverage requires acknowledgement.
16. Approve a valid quotation and compare project, print, share, purchase
    order, purchases and cost-control totals.
17. Exercise concurrent edit/approval; the snapshot must match the state that
    passed validation.
18. Remove maintenance only after every smoke test passes.

## Immediate rollback triggers

- Any registry/schema mismatch or incomplete migration summary.
- Any SQLite integrity failure.
- Any changed version-1 quotation total.
- Any difference between quotation, approved snapshot, purchase order or cost
  control.
- Any approval of a `REQUIRES_REVIEW` line.
- Any repeated schema, lock or core-project 5xx error.

## Rollback procedure

1. Re-enable maintenance and stop writes.
2. Roll back the frontend first.
3. Roll back backend code without dropping additive P0-01 columns or tables.
4. Do not physically reverse the SQLite schema during the incident.
5. Restore the verified backup only for corruption or unintended data changes.
6. Identify projects created as version 2 before reopening the legacy backend.
