# Zentra QA Knowledge Map

This directory is Emma's navigation layer to Zentra quality evidence. It
should stay small: reference authoritative sources, record freshness and
contradictions, and summarize only what is needed to find the truth. It must
not copy large documents or become a second product specification.

## Evidence hierarchy

For current implementation and quality state:

1. verified runtime behavior;
2. tests (existing and independently rerun);
3. current source code and migrations;
4. current technical documentation; and
5. older documentation.

For product and business direction, when it is relevant to judging an
acceptance criterion:

1. explicit current human-approved product vision;
2. accepted principles and RFCs;
3. roadmap documents; and
4. Emma inference.

Every assertion in the knowledge map has exactly one explicit label:

- **FACT:** current technical or quality behavior verified directly against
  the evidence hierarchy. A FACT cites its source, verification method, and
  verification date. Documentation alone cannot establish a current FACT when
  stronger evidence is available but has not been checked.
- **INFERENCE:** a reasoned conclusion derived from one or more cited FACTS.
  An INFERENCE records the reasoning chain, material counter-evidence, and an
  honest confidence level. It cannot claim verified behavior or human intent.
- **ASSUMPTION:** an unverified premise being used temporarily because
  necessary evidence or human confirmation is missing. An ASSUMPTION names
  what would confirm or reject it, who can resolve it, and the risk of being
  wrong. It must never be presented as a settled finding.
- **INTENT:** a current technical or product requirement explicitly approved
  by the authorized human decision-maker. An INTENT cites the approving
  decision, authority, and date. A roadmap, RFC, repeated behavior, or Emma
  recommendation is not INTENT unless its current approval is established.

## Category changes

Categories do not promote automatically through repetition, age, confidence,
or convenience. A category changes only when new evidence or explicit human
confirmation satisfies the destination category's requirements. The change
must preserve the previous label, cite the resolving evidence or decision,
and record the date and reason.

When sources conflict, record the contradiction, try to resolve it with
stronger evidence, and ask José when product intent remains ambiguous.

## Map sections

QA onboarding will maintain a concise map covering:

- Zentra's known failure modes and regression history;
- risky modules and their prior defect classes;
- test architecture, coverage gaps, and test-quality patterns;
- security boundaries and data-integrity constraints relevant to review;
- database/migration behavior and known migration risks;
- CI behavior, flaky checks, and known environmental limitations;
- known recurring bugs and their root causes;
- product-critical user journeys used to judge real-world impact; and
- evidence sources and their freshness.

`SOURCES.md` is the source registry. Future section files should be created
only when the onboarding evidence proves one consolidated map is no longer
usable.

## QA onboarding and review use

Authorized QA onboarding or a specific review:

1. inspects available backend, frontend, schema/migrations, tests, CI,
   history, architecture, security, and relevant product evidence in scope;
2. maps known risky areas, regression history, and test gaps;
3. identifies stale or conflicting documentation and important gaps;
4. labels every mapped assertion as FACT, INFERENCE, ASSUMPTION, or INTENT
   and supplies the evidence, reasoning, confirmation requirement, or
   approving decision required by that label;
5. records category changes and contradictions, and produces a confirmation
   queue for ASSUMPTIONS or ambiguous INTENT requiring José's decision;
6. produces an evidence-based review finding or report; and
7. proposes reviewed updates to this map and `SOURCES.md`.

Repository/code/tests outrank stale documentation. This map does not grant
Emma product-management authority and must be refreshed when stronger or
newer evidence conflicts with it.

## Readiness gate

QA readiness requires approved identity and safety, authorized evidence
access, and documented or resolved major contradictions relevant to the
review at hand. Emma may inspect and learn, but does not make product or
business decisions. Ambiguous product intent required to judge a finding goes
to José.

## Maintenance

After an independent review, ask whether the outcome invalidated the map.
Update affected FACT entries in the same reviewed change when appropriate,
citing evidence and freshness. Never silently alter product direction or
José-approved principles. Periodic staleness checks should target claims
whose sources or implementation have changed, not rewrite the map for
activity's sake.
