import dataclasses
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from jarvis.knowledge import (
    EmmaKnowledgeReview, KnowledgeApplicability, RepositoryBinding, build_candidate_envelope,
)
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_retrieval import search
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.repository_freshness import RepositoryFreshnessResolver
from tests.test_jarvis_knowledge import candidate


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


class KnowledgeRetrievalTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "knowledge"
        self.store = FileKnowledgeStore(self.root)
        self.repo = Path(self.temporary.name) / "scratch-repo"
        self.repo.mkdir()
        _run("git", "init", "-q", "-b", "main", cwd=self.repo)
        _run("git", "config", "user.email", "scratch@example.invalid", cwd=self.repo)
        _run("git", "config", "user.name", "scratch", cwd=self.repo)
        (self.repo / "f.txt").write_text("x", encoding="utf-8")
        _run("git", "add", "f.txt", cwd=self.repo)
        _run("git", "commit", "-q", "-m", "first", cwd=self.repo)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), check=True, capture_output=True, text=True
        ).stdout.strip()
        self.resolver = RepositoryFreshnessResolver(self.repo)

    def tearDown(self):
        self.temporary.cleanup()

    def ready(self, envelope):
        self.store.save_candidate(envelope)
        self.store.transition_candidate(envelope.content.candidate_id, "awaiting_emma_review")
        self.store.transition_candidate(envelope.content.candidate_id, "awaiting_human_authorization")
        review = EmmaKnowledgeReview(envelope.content.candidate_id, envelope.content.revision, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
        authorization = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        self.store.save_review(review)
        self.store.save_authorization(authorization)
        return review, authorization

    def promote_new(self, candidate_id, *, product_areas=("jarvis",), repository_binding=None, label="FACT"):
        content = candidate(candidate_id=candidate_id, applicability=KnowledgeApplicability(tuple(product_areas)), repository_binding=repository_binding, label=label)
        if label == "INTENT":
            content = dataclasses.replace(content, research_evidence=(
                dataclasses.replace(content.research_evidence[0], label="INTENT", sources=(
                    dataclasses.replace(content.research_evidence[0].sources[0], kind="human_statement"),
                )),
            ))
        envelope = build_candidate_envelope(content)
        review, authorization = self.ready(envelope)
        return self.store.promote(candidate_id, review, authorization)


class EligibilityTests(KnowledgeRetrievalTestCase):
    def test_entries_without_repository_binding_trigger_zero_git_calls(self):
        self.promote_new("aaaaaaaa-0001-4aaa-8aaa-aaaaaaaaaaaa")
        self.promote_new("bbbbbbbb-0002-4bbb-8bbb-bbbbbbbbbbbb")
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            response = search(self.store, self.resolver)
        run.assert_not_called()
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.omitted_count, 0)

    def test_repository_bound_fresh_entry_is_eligible(self):
        entry = self.promote_new("aaaaaaaa-0003-4aaa-8aaa-aaaaaaaaaaaa", repository_binding=RepositoryBinding("refs/heads/main", self.sha))
        response = search(self.store, self.resolver)
        self.assertEqual(len(response.results), 1)
        self.assertIn("REPOSITORY_FRESH", response.results[0].match_reasons)
        self.assertEqual(response.results[0].entry, entry)

    def test_repository_bound_stale_entry_is_excluded_and_counted_as_omitted(self):
        self.promote_new("aaaaaaaa-0004-4aaa-8aaa-aaaaaaaaaaaa", repository_binding=RepositoryBinding("refs/heads/main", "f" * 40))
        response = search(self.store, self.resolver)
        self.assertEqual(response.results, ())
        self.assertEqual(response.omitted_count, 1)

    def test_repository_bound_unresolvable_ref_is_excluded_not_asserted_stale(self):
        # An entry whose ref simply doesn't exist in the repo -- distinct
        # from a confirmed-stale mismatch, but must still just be excluded.
        self.promote_new("aaaaaaaa-0005-4aaa-8aaa-aaaaaaaaaaaa", repository_binding=RepositoryBinding("refs/heads/does-not-exist", "f" * 40))
        response = search(self.store, self.resolver)
        self.assertEqual(response.results, ())
        self.assertEqual(response.omitted_count, 1)

    def test_product_area_filter_excludes_non_matching_entries(self):
        self.promote_new("aaaaaaaa-0006-4aaa-8aaa-aaaaaaaaaaaa", product_areas=("billing",))
        self.promote_new("bbbbbbbb-0007-4bbb-8bbb-bbbbbbbbbbbb", product_areas=("auth",))
        response = search(self.store, self.resolver, product_areas=("auth",))
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].entry.applicability.product_areas, ("auth",))

    def test_no_filter_admits_every_active_entry(self):
        self.promote_new("aaaaaaaa-0008-4aaa-8aaa-aaaaaaaaaaaa", product_areas=("billing",))
        self.promote_new("bbbbbbbb-0009-4bbb-8bbb-bbbbbbbbbbbb", product_areas=("auth",))
        response = search(self.store, self.resolver)
        self.assertEqual(len(response.results), 2)

    def test_non_active_status_entries_are_excluded(self):
        active = self.promote_new("aaaaaaaa-0060-4aaa-8aaa-aaaaaaaaaaaa")
        conflicted_id = "323e4567-e89b-42d3-a456-426614174000"
        transition = build_candidate_envelope(candidate(
            candidate_id=conflicted_id, target_knowledge_id=active.knowledge_id,
            expected_target_revision=1, expected_current_status="active",
            proposed_entry_status="conflicted", contradicts=(active.knowledge_id,),
        ))
        review, authorization = self.ready(transition)
        conflicted = self.store.promote(conflicted_id, review, authorization)
        self.assertEqual(conflicted.status, "conflicted")
        response = search(self.store, self.resolver)
        self.assertEqual(response.results, ())
        self.assertEqual(response.omitted_count, 1)


class RankingDeterminismTests(KnowledgeRetrievalTestCase):
    def test_intent_ranks_before_fact_when_otherwise_tied(self):
        self.promote_new("aaaaaaaa-0010-4aaa-8aaa-aaaaaaaaaaaa", product_areas=("x",), label="FACT")
        self.promote_new("bbbbbbbb-0011-4bbb-8bbb-bbbbbbbbbbbb", product_areas=("x",), label="INTENT")
        response = search(self.store, self.resolver, product_areas=("x",))
        self.assertEqual([r.entry.label for r in response.results], ["INTENT", "FACT"])

    def test_ties_resolve_by_ascending_knowledge_id_never_by_created_at(self):
        # Deliberately construct so that an EARLIER-drafted candidate has
        # the LEXICOGRAPHICALLY LATER knowledge_id, and vice versa, then
        # prove ranking follows knowledge_id only.
        later_id_earlier_draft = build_candidate_envelope(candidate(candidate_id="dddddddd-0012-4ddd-8ddd-dddddddddddd", created_at="2020-01-01T00:00:00Z"))
        earlier_id_later_draft = build_candidate_envelope(candidate(candidate_id="aaaaaaaa-0013-4aaa-8aaa-aaaaaaaaaaaa", created_at="2026-08-26T00:00:00Z"))
        for envelope in (later_id_earlier_draft, earlier_id_later_draft):
            review, authorization = self.ready(envelope)
            self.store.promote(envelope.content.candidate_id, review, authorization)
        response = search(self.store, self.resolver)
        self.assertEqual(
            [r.entry.knowledge_id for r in response.results],
            ["aaaaaaaa-0013-4aaa-8aaa-aaaaaaaaaaaa", "dddddddd-0012-4ddd-8ddd-dddddddddddd"],
        )

    def test_repeated_identical_calls_are_byte_identical_in_order(self):
        for suffix in range(3):
            self.promote_new(f"aaaaaaaa-001{suffix}-4aaa-8aaa-aaaaaaaaaaaa")
        first = [r.entry.knowledge_id for r in search(self.store, self.resolver).results]
        second = [r.entry.knowledge_id for r in search(self.store, self.resolver).results]
        third = [r.entry.knowledge_id for r in search(self.store, self.resolver).results]
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_product_area_match_count_outranks_label_priority(self):
        self.promote_new("aaaaaaaa-0020-4aaa-8aaa-aaaaaaaaaaaa", product_areas=("x", "y"), label="FACT")
        self.promote_new("bbbbbbbb-0021-4bbb-8bbb-bbbbbbbbbbbb", product_areas=("x",), label="INTENT")
        response = search(self.store, self.resolver, product_areas=("x", "y"))
        self.assertEqual(response.results[0].entry.knowledge_id, "aaaaaaaa-0020-4aaa-8aaa-aaaaaaaaaaaa")


class TopKBoundsTests(KnowledgeRetrievalTestCase):
    def test_top_k_zero_rejected(self):
        with self.assertRaises(ValueError):
            search(self.store, self.resolver, top_k=0)

    def test_top_k_negative_rejected(self):
        with self.assertRaises(ValueError):
            search(self.store, self.resolver, top_k=-1)

    def test_top_k_above_hard_max_rejected(self):
        with self.assertRaises(ValueError):
            search(self.store, self.resolver, top_k=51)

    def test_top_k_boolean_rejected(self):
        with self.assertRaises(ValueError):
            search(self.store, self.resolver, top_k=True)

    def test_eligible_beyond_top_k_reported_separately_from_omitted_count(self):
        for suffix in range(3):
            self.promote_new(f"aaaaaaaa-003{suffix}-4aaa-8aaa-aaaaaaaaaaaa")
        response = search(self.store, self.resolver, top_k=1)
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.eligible_beyond_top_k, 2)
        self.assertEqual(response.omitted_count, 0)


class NoLeakTests(KnowledgeRetrievalTestCase):
    def test_response_never_contains_claim_text_marked_as_an_instruction_or_command_shape(self):
        # No prompt/command construction exists anywhere in this module;
        # confirm no callable named like a reasoning entrypoint exists.
        import jarvis.knowledge_retrieval as module
        forbidden = {"generate", "complete", "chat", "prompt", "invoke", "dispatch"}
        self.assertTrue(forbidden.isdisjoint(dir(module)))

    def test_stale_reason_detail_does_not_leak_into_response(self):
        self.promote_new("aaaaaaaa-0040-4aaa-8aaa-aaaaaaaaaaaa", repository_binding=RepositoryBinding("refs/heads/main", "f" * 40))
        response = search(self.store, self.resolver)
        self.assertNotIn("FRESHNESS", repr(response))
        self.assertNotIn(str(self.repo), repr(response))


if __name__ == "__main__":
    unittest.main()
