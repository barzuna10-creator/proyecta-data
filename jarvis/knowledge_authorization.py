"""Strict review and human-authorization binding for trusted knowledge."""

from __future__ import annotations

import re

from jarvis.knowledge import (
    EmmaKnowledgeReview, KnowledgeAuthorizationIntent, KnowledgeCandidateEnvelope,
)

_COMMAND = re.compile(
    r"\AAUTHORIZE KNOWLEDGE ([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}) "
    r"REVISION ([1-9][0-9]*) DIGEST sha256:([0-9a-f]{64})\Z"
)


def render_knowledge_authorization(envelope: KnowledgeCandidateEnvelope) -> str:
    return (
        f"AUTHORIZE KNOWLEDGE {envelope.content.candidate_id} "
        f"REVISION {envelope.content.revision} DIGEST sha256:{envelope.content_digest}"
    )


def parse_knowledge_authorization(command: str) -> KnowledgeAuthorizationIntent:
    match = _COMMAND.fullmatch(command) if isinstance(command, str) else None
    if match is None:
        raise ValueError("KNOWLEDGE_AUTHORIZATION_GRAMMAR_INVALID")
    return KnowledgeAuthorizationIntent(match.group(1), int(match.group(2)), match.group(3))


def require_exact_authorities(
    envelope: KnowledgeCandidateEnvelope,
    review: EmmaKnowledgeReview,
    authorization: KnowledgeAuthorizationIntent,
) -> None:
    expected = (envelope.content.candidate_id, envelope.content.revision, envelope.content_digest)
    if review.verdict != "PASS" or (review.candidate_id, review.revision, review.content_digest) != expected:
        raise ValueError("KNOWLEDGE_REVIEW_STALE")
    if (authorization.candidate_id, authorization.revision, authorization.content_digest) != expected:
        raise ValueError("KNOWLEDGE_AUTHORIZATION_STALE")
