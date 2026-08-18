# Zentra V1 Handoff

## Authorized task

<!-- Quote or faithfully restate the human-authorized task. -->

## Acceptance criteria

- [ ]

## Repository state

- Worktree path:
- Branch:
- Base SHA:
- Head SHA:
- Initial status:
- Final status:

## Review artifact identity

- Mode: `immutable commit` / `captured patch`
- Immutable commit SHA (if applicable):
- Captured patch path (if applicable):
- Patch capture procedure (if applicable):
- Patch byte size (if applicable):
- Patch SHA-256 (if applicable):
- Builder verification time:
- Builder revalidation result immediately before handoff:

<!-- Base/head SHAs alone do not identify uncommitted work. A captured patch
must include every tracked, staged, unstaged, untracked, binary, deleted, and
renamed path in review scope. Any post-capture edit requires a new artifact and
digest. -->

## Changed files

| File | Reason |
|---|---|
| | |

## Tests and checks executed

| Command | Working directory | Exit status | Exact result |
|---|---|---:|---|
| | | | |

## Skipped or unavailable checks

| Check | Reason | Residual risk |
|---|---|---|
| None | N/A | N/A |

## Risks

-

## Assumptions

-

## Rollback notes

<!-- Describe how a human can reverse only this task's changes without
discarding unrelated work. Do not perform the rollback as part of handoff. -->

-

## Safety confirmation

- [ ] No existing user checkout or user work was reset, cleaned, stashed,
      deleted, or overwritten.
- [ ] No direct change was made to `main`.
- [ ] No push, pull request, merge, or deployment occurred.
- [ ] No production data, database, secret, credential, or infrastructure was
      accessed or modified.
- [ ] No protected or out-of-scope file changed.
- [ ] The complete diff was inspected.
- [ ] Review identity is an authorized immutable commit or a complete captured
      patch whose SHA-256 and byte size were revalidated before handoff.

## Builder conclusion

<!-- Summarize what the evidence establishes and name every limitation. This is
not approval; independent Reviewer/QA decides the review outcome. -->
