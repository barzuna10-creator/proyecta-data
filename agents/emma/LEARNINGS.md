# Emma's Learning Ledger

Learnings record QA decisions and feedback that may improve Emma's future
reviews: missed defects, escaped regressions, reopened work, false-positive
findings, disagreements with Emilio or José, recurring defect classes, and
review-process lessons explicitly confirmed by José. They are auditable and
provisional. One isolated outcome is not automatically a universal
preference, identity change, permission, or permanent principle.

## Entry schema

Each entry must contain:

- **ID:** stable `QL-YYYY-NNN` identifier.
- **Date:** ISO `YYYY-MM-DD`.
- **Context:** the reviewed task and decision boundary.
- **Emma finding/recommendation:** what Emma reported and why.
- **José decision/feedback:** the human response, quoted or faithfully
  summarized.
- **Principle learned:** narrow lesson supported by this event.
- **Future application:** when the lesson should affect later reviews.
- **Confidence/status:** `candidate`, `reinforced`, `contradicted`, `retired`,
  or `promoted`, plus an honest confidence rationale.
- **Evidence:** links to reviewed artifacts, commits, decisions, or results.

## Promotion and correction

- Promotion to `PRINCIPLES.md` requires José's explicit acceptance.
- Contradictory evidence is recorded; it is never silently overwritten.
- A human override does not count as Emma failure by itself. The outcome and
  reasoning determine the lesson.
- Learnings never grant authority or weaken `/AGENTS.md` or
  `/docs/zentra/REVIEWER_QA_V1.md`.

## Entries

No learnings have been recorded yet. Identity-scaffolding decisions belong in
the accepted initial principles only where the human explicitly stated them.
