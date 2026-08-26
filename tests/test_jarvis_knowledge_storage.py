import dataclasses
import json
from pathlib import Path
import tempfile
import unittest

from jarvis.knowledge import EmmaKnowledgeReview, KnowledgeCandidateContent, build_candidate_envelope
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_storage import FileKnowledgeStore, KnowledgeCorrupt, KnowledgeTargetStateChanged, promotion_id
from tests.test_jarvis_knowledge import CID, candidate


class KnowledgeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "knowledge"
        self.store = FileKnowledgeStore(self.root)

    def tearDown(self): self.temporary.cleanup()

    def ready(self, envelope):
        self.store.save_candidate(envelope)
        self.store.transition_candidate(envelope.content.candidate_id, "awaiting_emma_review")
        self.store.transition_candidate(envelope.content.candidate_id, "awaiting_human_authorization")
        review = EmmaKnowledgeReview(envelope.content.candidate_id, envelope.content.revision, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
        authorization = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        self.store.save_review(review)
        self.store.save_authorization(authorization)
        return review, authorization

    def test_unrecorded_authority_cannot_promote(self):
        envelope = build_candidate_envelope(candidate())
        self.store.save_candidate(envelope)
        self.store.transition_candidate(CID, "awaiting_emma_review")
        self.store.transition_candidate(CID, "awaiting_human_authorization")
        review = EmmaKnowledgeReview(CID, 1, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
        authorization = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        with self.assertRaises(Exception): self.store.promote(CID, review, authorization)

    def test_promotion_discovered_from_tuple_without_index_and_restart(self):
        envelope = build_candidate_envelope(candidate())
        review, authorization = self.ready(envelope)
        entry = self.store.promote(CID, review, authorization)
        pid = promotion_id(CID, 1, envelope.content_digest)
        self.assertTrue((self.root / "promotions" / pid / "COMMITTED").is_file())
        self.assertEqual(self.store.get_candidate_status(CID), "accepted")
        restarted = FileKnowledgeStore(self.root)
        self.assertEqual(restarted.get_candidate_status(CID), "accepted")
        self.assertEqual(restarted.get_latest_entry(CID), entry)
        self.assertFalse(any("index" in path.name.lower() or "latest" in path.name.lower() or "pointer" in path.name.lower() for path in self.root.rglob("*")))

    def test_uncommitted_bundle_does_not_change_effective_status(self):
        envelope = build_candidate_envelope(candidate())
        self.ready(envelope)
        pid = promotion_id(CID, 1, envelope.content_digest)
        bundle = self.root / "promotions" / pid; bundle.mkdir(mode=0o700)
        (bundle / "manifest.json").write_text("{}")
        self.assertEqual(FileKnowledgeStore(self.root).get_candidate_status(CID), "awaiting_human_authorization")

    def test_committed_corruption_fails_closed(self):
        envelope = build_candidate_envelope(candidate())
        review, authorization = self.ready(envelope)
        self.store.promote(CID, review, authorization)
        pid = promotion_id(CID, 1, envelope.content_digest)
        (self.root / "promotions" / pid / "knowledge-entry.json").write_text("{}")
        with self.assertRaises(KnowledgeCorrupt): self.store.get_candidate_status(CID)

    def test_expected_state_is_rechecked_under_lock(self):
        first = build_candidate_envelope(candidate())
        review, authorization = self.ready(first); active = self.store.promote(CID, review, authorization)
        second_id = "323e4567-e89b-42d3-a456-426614174000"
        transition = build_candidate_envelope(candidate(candidate_id=second_id, target_knowledge_id=CID, expected_target_revision=1, expected_current_status="conflicted", proposed_entry_status="retired", label="INTENT", research_evidence=(dataclasses.replace(candidate().research_evidence[0], label="INTENT", sources=(dataclasses.replace(candidate().research_evidence[0].sources[0], kind="human_statement"),)),)))
        review2, auth2 = self.ready(transition)
        with self.assertRaises(KnowledgeTargetStateChanged): self.store.promote(second_id, review2, auth2)
        self.assertEqual(self.store.get_candidate_status(second_id), "awaiting_human_authorization")
        self.assertEqual(self.store.get_latest_entry(CID), active)

    def test_retry_after_precommit_partial_is_deterministic(self):
        envelope = build_candidate_envelope(candidate()); review, authorization = self.ready(envelope)
        pid = promotion_id(CID, 1, envelope.content_digest)
        bundle = self.root / "promotions" / pid; bundle.mkdir(mode=0o700)
        entry = self.store.promote(CID, review, authorization)
        self.assertEqual(self.store.get_candidate_status(CID), "accepted")
        self.assertEqual(self.store.promote(CID, review, authorization), entry)


if __name__ == "__main__": unittest.main()
