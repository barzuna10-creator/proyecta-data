import dataclasses
import unittest

from jarvis.knowledge import EmmaKnowledgeReview
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization, require_exact_authorities
from tests.test_jarvis_knowledge import candidate
from jarvis.knowledge import build_candidate_envelope


class KnowledgeAuthorizationTests(unittest.TestCase):
    def test_exact_grammar_and_tuple(self):
        envelope = build_candidate_envelope(candidate())
        intent = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        review = EmmaKnowledgeReview(envelope.content.candidate_id, 1, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
        require_exact_authorities(envelope, review, intent)
        with self.assertRaisesRegex(ValueError, "AUTHORIZATION_STALE"):
            require_exact_authorities(envelope, review, dataclasses.replace(intent, content_digest="b" * 64))

    def test_review_is_mandatory_and_exact(self):
        envelope = build_candidate_envelope(candidate())
        intent = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        for verdict, digest in (("CHANGES_REQUIRED", envelope.content_digest), ("PASS", "b" * 64)):
            with self.assertRaisesRegex(ValueError, "REVIEW_STALE"):
                require_exact_authorities(envelope, EmmaKnowledgeReview(envelope.content.candidate_id, 1, digest, verdict, "2026-08-26T00:00:01Z"), intent)

    def test_variants_fail_closed(self):
        for value in ("", "authorize knowledge x", "AUTHORIZE KNOWLEDGE ../x REVISION 1 DIGEST sha256:" + "a" * 64, "AUTHORIZE KNOWLEDGE 123e4567-e89b-42d3-a456-426614174000 REVISION 01 DIGEST sha256:" + "a" * 64):
            with self.assertRaises(ValueError): parse_knowledge_authorization(value)


if __name__ == "__main__": unittest.main()
