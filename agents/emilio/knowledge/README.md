# Zentra Product Knowledge Map

This directory is Emilio's navigation layer to Zentra evidence. It should stay
small: reference authoritative sources, record freshness and contradictions,
and summarize only what is needed to find the truth. It must not copy large
documents or become a second product specification.

## Evidence hierarchy

For current implementation:

1. verified runtime behavior;
2. tests;
3. current source code and migrations;
4. current technical documentation; and
5. older documentation.

For product direction:

1. explicit current human-approved product vision;
2. accepted principles and RFCs;
3. roadmap documents; and
4. Emilio inference.

Every assertion in the knowledge map has exactly one explicit label:

- **FACT:** current behavior or state verified directly against the evidence
  hierarchy. A FACT cites its source, verification method, and verification
  date. Documentation alone cannot establish a current implementation FACT when
  stronger evidence is available but has not been checked.
- **INFERENCE:** a reasoned conclusion derived from one or more cited FACTS.
  An INFERENCE records the reasoning chain, material counter-evidence, and an
  honest confidence level. It cannot claim verified behavior or human intent.
- **ASSUMPTION:** an unverified premise being used temporarily because necessary
  evidence or human confirmation is missing. An ASSUMPTION names what would
  confirm or reject it, who can resolve it, and the risk of being wrong. It must
  never be presented as settled product knowledge.
- **INTENT:** current product direction explicitly approved by the authorized
  human decision-maker. An INTENT cites the approving decision, authority, and
  date. A roadmap, RFC, repeated behavior, or Emilio recommendation is not
  INTENT unless its current approval is established.

## Category changes

Categories do not promote automatically through repetition, age, confidence,
or convenience. A category changes only when new evidence or explicit human
confirmation satisfies the destination category's requirements. The change
must preserve the previous label, cite the resolving evidence or decision, and
record the date and reason.

Examples: an ASSUMPTION may become FACT after direct verification, or INTENT
after explicit product approval; an INFERENCE may become FACT only when its
claim is directly verified. A FACT whose evidence is obsolete must be marked
stale and reclassified rather than kept as current truth. INTENT may be changed
or superseded only by an authorized human decision. None of these transitions
converts Emilio's judgment into product truth by default.

When sources conflict, record the contradiction, try to resolve it with
stronger evidence, and ask José when product intent remains ambiguous.

## Map sections

Product Onboarding will populate a concise map covering:

- what Zentra is, target users, value proposition, and product stage;
- product direction and what Zentra should not become;
- core user journeys;
- technical architecture and external boundaries;
- current capabilities, partial implementations, limitations, risks, UX
  friction, and test gaps;
- product principles; and
- canonical terminology.

`SOURCES.md` is the source registry. Future section files should be created only
when the onboarding evidence proves one consolidated map is no longer usable.

## Separate Product Onboarding task

Emilio is not fully ready for autonomous Discovery Mode until a separately
authorized, read-only, independently reviewed onboarding task:

1. inspects available backend, frontend, schema/migrations, tests, CI, history,
   architecture, product, UX, security, infrastructure, and roadmap evidence;
2. maps architecture, capabilities, and core user journeys;
3. identifies stale or conflicting documentation and important gaps;
4. labels every mapped assertion as FACT, INFERENCE, ASSUMPTION, or INTENT and
   supplies the evidence, reasoning, confirmation requirement, or approving
   decision required by that label;
5. records category changes and contradictions, and produces a confirmation
   queue for ASSUMPTIONS or ambiguous INTENT requiring José's decision;
6. produces an onboarding report; and
7. proposes reviewed updates to this map and `SOURCES.md`.

This identity task creates the mechanism only; it does not perform onboarding.

## Readiness gate

Full Discovery readiness requires approved identity and safety, completed
onboarding, mapped journeys/architecture/current state, documented or resolved
major contradictions, and José's review of important product-intent assumptions.
Until then Emilio may inspect and learn but stays conservative about strong
product recommendations.

## Maintenance

After an authorized implementation, ask whether the change invalidated the
map. Update affected FACT entries in the same reviewed change when appropriate,
citing evidence and freshness. Never silently alter product direction or
José-approved principles. Periodic staleness checks should target claims whose
sources or implementation have changed, not rewrite the map for activity's sake.
