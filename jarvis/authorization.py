"""Strict parsing of non-executing Jarvis authorization intents."""

from __future__ import annotations

import re

from jarvis.drafts import build_draft_envelope, validate_mission_draft
from jarvis.models import (
    AuthorizationCheck,
    AuthorizationIntent,
    DraftEnvelope,
    ValidationIssue,
)

AUTHORIZATION_PATTERN = re.compile(
    r"\AAUTHORIZE JARVIS MISSION DRAFT "
    r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}) "
    r"REVISION ([1-9][0-9]*) SHA256 ([0-9a-f]{64})\Z"
)


class AuthorizationSyntaxError(ValueError):
    pass


def render_authorization_command(envelope: DraftEnvelope) -> str:
    return (
        f"AUTHORIZE JARVIS MISSION DRAFT {envelope.draft.draft_id} "
        f"REVISION {envelope.draft.revision} SHA256 {envelope.digest}"
    )


def parse_authorization_command(command: str) -> AuthorizationIntent:
    if not isinstance(command, str):
        raise AuthorizationSyntaxError("authorization command must be text")
    # A terminal may supply exactly one line ending. General whitespace is
    # deliberately not stripped because it is authority-bearing syntax.
    if command.endswith("\r\n"):
        command = command[:-2]
    elif command.endswith("\n"):
        command = command[:-1]
    match = AUTHORIZATION_PATTERN.fullmatch(command)
    if match is None:
        raise AuthorizationSyntaxError("authorization command does not match the exact grammar")
    draft_id, revision, digest = match.groups()
    return AuthorizationIntent(
        draft_id=draft_id,
        revision=int(revision),
        digest_algorithm="sha256",
        digest=digest,
    )


def validate_authorization_intent(
    intent: AuthorizationIntent,
    *,
    current: DraftEnvelope,
) -> AuthorizationCheck:
    reasons: list[ValidationIssue] = []
    result = validate_mission_draft(current.draft)
    if not result.valid:
        reasons.append(ValidationIssue("DRAFT_CORRUPT", "current draft is invalid"))
        return AuthorizationCheck(False, tuple(reasons))
    recomputed = build_draft_envelope(current.draft)
    if current.digest_algorithm != "sha256" or current.digest != recomputed.digest:
        reasons.append(ValidationIssue("DRAFT_CORRUPT", "stored draft digest does not verify"))
    if current.draft.open_questions:
        reasons.append(ValidationIssue(
            "DRAFT_NOT_AUTHORIZATION_READY",
            "a draft with open questions is not authorization-ready",
            "$.open_questions",
        ))
    if intent.draft_id != current.draft.draft_id:
        reasons.append(ValidationIssue("DRAFT_NOT_FOUND", "authorization names a different draft"))
    if intent.revision != current.draft.revision:
        reasons.append(ValidationIssue(
            "REVISION_NOT_CURRENT", "authorization revision is stale", "$.revision"
        ))
    if intent.digest_algorithm != current.digest_algorithm or intent.digest != current.digest:
        reasons.append(ValidationIssue("DIGEST_MISMATCH", "authorization digest does not match"))
    return AuthorizationCheck(not reasons, tuple(reasons))
