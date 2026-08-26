import dataclasses
import json
from pathlib import Path
import tempfile
import unittest

from jarvis._safe_io import MAX_JSON_BYTES
from jarvis.knowledge import EmmaKnowledgeReview, KnowledgeCandidateContent, build_candidate_envelope
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_storage import (
    FileKnowledgeStore, KnowledgeCorrupt, KnowledgeEntryListing, KnowledgeTargetStateChanged, promotion_id,
)
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


def _hex_between(lo: str, hi: str) -> str:
    """Return a 64-hex-char string that sorts strictly between ``lo`` and
    ``hi`` (which must already satisfy lo < hi). Used to place a synthetic
    corrupt bundle at an exact, deterministic relative position among real
    sha256-named promotion bundles without brute-forcing a collision."""
    mid = (int(lo, 16) + int(hi, 16)) // 2
    return f"{mid:064x}"


class KnowledgeListingTests(unittest.TestCase):
    """Adversarial coverage for FileKnowledgeStore.list_latest_entries():
    one unsafe/corrupt/malformed knowledge chain or bundle must omit only
    that knowledge, never hide unrelated valid knowledge -- mirroring the
    exact chugel.list_missions() principle from Mission 002."""

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

    def promote_new(self, candidate_id, *, claim="A claim"):
        """Promote a fresh, independent piece of 'new knowledge' -- for
        target_knowledge_id=None the resulting knowledge_id equals the
        candidate_id, giving each call a distinct, real, digest-verified
        promotion bundle at its own genuine promotion_id() location."""
        envelope = build_candidate_envelope(candidate(candidate_id=candidate_id, claim=claim))
        review, authorization = self.ready(envelope)
        entry = self.store.promote(candidate_id, review, authorization)
        bundle = self.root / "promotions" / promotion_id(candidate_id, 1, envelope.content_digest)
        return entry, bundle

    def synthetic_corrupt_bundle(self, name):
        """A bundle directory at an EXACTLY chosen 64-hex name, carrying a
        COMMITTED marker but no manifest.json -- fails cleanly inside pass
        1 (KnowledgeNotFound) regardless of where it sorts relative to
        real, digest-derived promotion bundles."""
        bundle = self.root / "promotions" / name
        bundle.mkdir(mode=0o700)
        (bundle / "COMMITTED").write_bytes(b"{}")
        return bundle

    def test_empty_store_returns_empty_listing(self):
        self.assertEqual(self.store.list_latest_entries(), KnowledgeEntryListing((), 0))

    def test_multiple_distinct_ids_all_returned(self):
        a_id, b_id, c_id = ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        a, _ = self.promote_new(a_id); b, _ = self.promote_new(b_id); c, _ = self.promote_new(c_id)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 0)
        self.assertEqual(set(listing.entries), {a, b, c})

    def test_multiple_revisions_returns_only_the_latest(self):
        active, bundle = self.promote_new(CID)
        second_id = "323e4567-e89b-42d3-a456-426614174000"
        transition = build_candidate_envelope(candidate(
            candidate_id=second_id, target_knowledge_id=CID, expected_target_revision=1,
            expected_current_status="active", proposed_entry_status="conflicted",
            contradicts=(CID,),
        ))
        review2, auth2 = self.ready(transition)
        latest = self.store.promote(second_id, review2, auth2)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 0)
        self.assertEqual(listing.entries, (latest,))
        self.assertEqual(latest.revision, 2)

    def test_cross_id_isolation_corruption_first_in_sort_order(self):
        a, _ = self.promote_new("aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa")
        b, _ = self.promote_new("bbbbbbbb-2222-4bbb-8bbb-bbbbbbbbbbbb")
        # Guaranteed (overwhelming probability) to sort before any real sha256 digest.
        self.synthetic_corrupt_bundle("0" * 64)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(set(listing.entries), {a, b})

    def test_cross_id_isolation_corruption_last_in_sort_order(self):
        a, _ = self.promote_new("aaaaaaaa-3333-4aaa-8aaa-aaaaaaaaaaaa")
        b, _ = self.promote_new("bbbbbbbb-4444-4bbb-8bbb-bbbbbbbbbbbb")
        # Guaranteed (overwhelming probability) to sort after any real sha256 digest.
        self.synthetic_corrupt_bundle("f" * 64)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(set(listing.entries), {a, b})

    def test_cross_id_isolation_corruption_interleaved_between_valid_entries(self):
        a, _ = self.promote_new("aaaaaaaa-5555-4aaa-8aaa-aaaaaaaaaaaa")
        b, _ = self.promote_new("bbbbbbbb-6666-4bbb-8bbb-bbbbbbbbbbbb")
        pid_a = promotion_id(a.candidate_id, 1, a.candidate_content_digest)
        pid_b = promotion_id(b.candidate_id, 1, b.candidate_content_digest)
        lo, hi = sorted((pid_a, pid_b))
        self.synthetic_corrupt_bundle(_hex_between(lo, hi))
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(set(listing.entries), {a, b})

    def test_symlinked_bundle_directory_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-7777-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-8888-4bbb-8bbb-bbbbbbbbbbbb")
        symlink_name = "0" * 64
        (self.root / "promotions" / symlink_name).symlink_to(bundle_b, target_is_directory=True)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(set(listing.entries), {a, b})

    def test_symlinked_manifest_json_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-9999-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-0000-4bbb-8bbb-bbbbbbbbbbbb")
        target = self.root / "outside-manifest.json"; target.write_text("{}", encoding="utf-8")
        (bundle_b / "manifest.json").unlink()
        (bundle_b / "manifest.json").symlink_to(target)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_symlinked_candidate_event_json_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-1010-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-1111-4bbb-8bbb-bbbbbbbbbbbb")
        target = self.root / "outside-event.json"; target.write_text("{}", encoding="utf-8")
        (bundle_b / "candidate-event.json").unlink()
        (bundle_b / "candidate-event.json").symlink_to(target)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_symlinked_knowledge_entry_json_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-1212-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-1313-4bbb-8bbb-bbbbbbbbbbbb")
        target = self.root / "outside-entry.json"; target.write_text("{}", encoding="utf-8")
        (bundle_b / "knowledge-entry.json").unlink()
        (bundle_b / "knowledge-entry.json").symlink_to(target)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_oversized_inner_artifact_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-1414-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-1515-4bbb-8bbb-bbbbbbbbbbbb")
        (bundle_b / "knowledge-entry.json").unlink()
        (bundle_b / "knowledge-entry.json").write_bytes(b"{" + b" " * (MAX_JSON_BYTES + 1) + b"}")
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_corrupt_json_syntax_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-1616-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-1717-4bbb-8bbb-bbbbbbbbbbbb")
        (bundle_b / "manifest.json").unlink()
        (bundle_b / "manifest.json").write_text("{not valid json", encoding="utf-8")
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_revision_gap_or_fork_isolated(self):
        a, _ = self.promote_new("aaaaaaaa-1818-4aaa-8aaa-aaaaaaaaaaaa")
        active, _ = self.promote_new("bbbbbbbb-1919-4bbb-8bbb-bbbbbbbbbbbb")
        second_id = "323e4567-e89b-42d3-a456-426614174000"
        transition = build_candidate_envelope(candidate(
            candidate_id=second_id, target_knowledge_id=active.knowledge_id, expected_target_revision=1,
            expected_current_status="active", proposed_entry_status="conflicted", contradicts=(active.knowledge_id,),
        ))
        review2, auth2 = self.ready(transition)
        self.store.promote(second_id, review2, auth2)
        bundle_rev2 = self.root / "promotions" / promotion_id(second_id, 1, transition.content_digest)
        (bundle_rev2 / "knowledge-entry.json").unlink()
        forged = dataclasses.replace(active, revision=1)  # forged: claims revision 1 again, not 2
        from jarvis.knowledge import knowledge_entry_to_dict
        import hashlib, json as _json
        payload = _json.dumps(knowledge_entry_to_dict(forged), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (bundle_rev2 / "knowledge-entry.json").write_bytes(payload)
        manifest_path = bundle_rev2 / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["knowledge_entry_sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_bytes = _json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest_path.unlink(); manifest_path.write_bytes(manifest_bytes)
        marker_path = bundle_rev2 / "COMMITTED"
        marker = {"schema_version": "1.0", "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}
        marker_bytes = _json.dumps(marker, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        marker_path.unlink(); marker_path.write_bytes(marker_bytes)
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_malformed_manifest_missing_target_id_never_becomes_a_knowledge_id(self):
        a, _ = self.promote_new("aaaaaaaa-2020-4aaa-8aaa-aaaaaaaaaaaa")
        bundle = self.synthetic_corrupt_bundle("1" * 64)
        (bundle / "manifest.json").write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))
        self.assertNotIn("1" * 64, [entry.knowledge_id for entry in listing.entries])

    def test_malformed_manifest_non_uuid_target_id_never_becomes_a_knowledge_id(self):
        a, _ = self.promote_new("aaaaaaaa-2121-4aaa-8aaa-aaaaaaaaaaaa")
        bundle = self.synthetic_corrupt_bundle("2" * 64)
        (bundle / "manifest.json").write_text(json.dumps({"target_knowledge_id": "not-a-uuid"}), encoding="utf-8")
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        self.assertEqual(listing.entries, (a,))

    def test_malformed_bundle_directory_names_and_uncommitted_bundles_never_surface(self):
        a, _ = self.promote_new("aaaaaaaa-2222-4aaa-8aaa-aaaaaaaaaaaa")
        (self.root / "promotions" / "not-a-hex-name").mkdir(mode=0o700)
        (self.root / "promotions" / "not-a-hex-name" / "COMMITTED").write_bytes(b"{}")
        uncommitted = self.root / "promotions" / ("3" * 64)
        uncommitted.mkdir(mode=0o700)
        (uncommitted / "manifest.json").write_text(json.dumps({"target_knowledge_id": CID}), encoding="utf-8")
        listing = self.store.list_latest_entries()
        # The malformed-name directory is never even a shape candidate; the
        # uncommitted bundle has no COMMITTED marker, so neither is counted
        # as an omission -- only genuinely committed-but-unsafe bundles are.
        self.assertEqual(listing.omitted_count, 0)
        self.assertEqual(listing.entries, (a,))

    def test_no_corruption_or_path_detail_leaks_into_the_listing(self):
        a, _ = self.promote_new("aaaaaaaa-2323-4aaa-8aaa-aaaaaaaaaaaa")
        b, bundle_b = self.promote_new("bbbbbbbb-2424-4bbb-8bbb-bbbbbbbbbbbb")
        (bundle_b / "manifest.json").unlink()
        (bundle_b / "manifest.json").write_text("SECRET_CORRUPTION_DETAIL_MUST_NOT_LEAK", encoding="utf-8")
        listing = self.store.list_latest_entries()
        self.assertEqual(listing.omitted_count, 1)
        rendered = repr(listing)
        self.assertNotIn("SECRET_CORRUPTION_DETAIL_MUST_NOT_LEAK", rendered)
        self.assertNotIn(str(bundle_b), rendered)


if __name__ == "__main__": unittest.main()
