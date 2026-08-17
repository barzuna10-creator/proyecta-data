# Zentra Knowledge Source Registry

This registry lists evidence families Emilio must assess during Product
Onboarding. Listing a document does not endorse its accuracy or freshness.
Onboarding must add owner/authority, last verified date, covered claims,
conflicts, and replacement/supersession notes.

| Evidence family | Candidate sources currently present | Initial status |
|---|---|---|
| Runtime and implementation | `api/`, root Python modules, `database/` migrations, external frontend repository when authorized | Unassessed |
| Tests and quality | `tests/`, `QA_REPORT.md`, release checklists | Unassessed |
| Product direction | `ESTRATEGIA_PRODUCTO.md`, `ROADMAP.md`, `POSICIONAMIENTO_INICIAL.md` | Unassessed; human approval/freshness unknown |
| Platform architecture | `ARQUITECTURA_PLATAFORMA_INTEGRAL.md`, module-specific architecture documents | Unassessed |
| Core journeys | `FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md`, `COMPRAS.md`, `CONTROL_DE_COSTOS.md`, quotation and project-flow documents | Unassessed |
| Catalog/search/matching | crawler, equivalence, enrichment, reranking, and similar-product documents plus their code/tests | Unassessed |
| UX and product audits | `EXPERIENCIA_USUARIO.md`, UX/audit/revision documents | Unassessed |
| Reliability and release | `PRODUCTION_READINESS_REVIEW.md`, release-candidate documents, incident investigations | Unassessed |
| Deployment/infrastructure | `DEPLOYMENT.md`, `render.yaml`, related source and reviews | Protected/read-only unless separately authorized |
| Repository history | Git commits relevant to a claim | Consult when source evolution matters |

## Registry entry requirements

For each source added or assessed, record:

- path or stable reference;
- evidence type (`runtime`, `test`, `source`, `migration`, `technical doc`,
  `product intent`, or `history`);
- claims it supports;
- authority/approval status;
- last verified date and verifier;
- known conflicts or stale sections; and
- status: `authoritative`, `supporting`, `historical`, `stale`, `conflicted`, or
  `unassessed`.

The registry points to evidence; it does not duplicate it.
