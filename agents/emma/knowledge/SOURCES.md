# Zentra QA Knowledge Source Registry

This registry lists evidence families Emma may assess during QA onboarding
and review. Listing a document does not endorse its accuracy or freshness.
Assessment must add owner/authority, last verified date, covered claims,
conflicts, and replacement/supersession notes.

| Evidence family | Candidate sources currently present | Initial status |
|---|---|---|
| Runtime and implementation under review | `api/`, root Python modules, `database/` migrations, external frontend repository when authorized | Unassessed |
| Tests and quality | `tests/`, `QA_REPORT.md`, release checklists | Unassessed |
| Regression and incident history | Prior incident/investigation documents (e.g. memory/production incident write-ups), Git history for reviewed paths | Unassessed |
| Reliability and release | `PRODUCTION_READINESS_REVIEW.md`, release-candidate documents | Unassessed |
| Security review evidence | Security-focused review/audit documents when present and in scope | Unassessed |
| Product direction relevant to acceptance criteria | Explicitly human-approved product vision documents | Context only; human approval/freshness unknown and José owns intent |
| Builder handoffs | `docs/zentra/HANDOFF_TEMPLATE.md` instances produced per task | Authoritative for the specific reviewed task once validated |
| Deployment/infrastructure | `DEPLOYMENT.md`, `render.yaml`, related source | Protected/read-only unless separately authorized |
| Repository history | Git commits relevant to a claim under review | Consult when source evolution or regression timing matters |

## Registry entry requirements

For each source added or assessed, record:

- path or stable reference;
- evidence type (`runtime`, `test`, `source`, `migration`, `technical doc`,
  `product intent`, `handoff`, or `history`);
- claims it supports;
- authority/approval status;
- last verified date and verifier;
- known conflicts or stale sections; and
- status: `authoritative`, `supporting`, `historical`, `stale`, `conflicted`,
  or `unassessed`.

The registry points to evidence; it does not duplicate it.
