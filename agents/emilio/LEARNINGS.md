# Emilio's Learning Ledger

Learnings record engineering decisions and feedback that may improve Emilio's
future work: missed bugs, Reviewer findings, architectural decisions, failed
approaches, regression patterns, performance discoveries, repository
conventions, testing lessons, and technical decisions explicitly confirmed by
José. They are auditable and provisional. One isolated decision is not
automatically a universal preference, identity change, permission, or permanent
principle.

## Entry schema

Each entry must contain:

- **ID:** stable `EL-YYYY-NNN` identifier.
- **Date:** ISO `YYYY-MM-DD`.
- **Context:** task and decision boundary.
- **Emilio recommendation:** what Emilio proposed and why.
- **José decision/feedback:** the human response, quoted or faithfully
  summarized.
- **Principle learned:** narrow lesson supported by this event.
- **Future application:** when the lesson should affect later work.
- **Confidence/status:** `candidate`, `reinforced`, `contradicted`, `retired`, or
  `promoted`, plus an honest confidence rationale.
- **Evidence:** links to reviewed artifacts, commits, decisions, or results.

## Promotion and correction

- Promotion to `PRINCIPLES.md` requires José's explicit acceptance.
- Contradictory evidence is recorded; it is never silently overwritten.
- A human override does not count as Emilio failure by itself. The outcome and
  reasoning determine the lesson.
- Learnings never grant authority or weaken `/AGENTS.md`.

## Entries

No learnings have been recorded yet. Identity-scaffolding decisions belong in
the accepted initial principles only where the human explicitly stated them.
