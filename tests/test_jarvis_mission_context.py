"""jarvis/mission_context.py -- draft_briefing() and the data-flow
non-overlap guarantee with jarvis/mission_proposal.py's persisted
MissionDefinition (Corrective #3's Fix 4/5 residual)."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from jarvis.knowledge import EmmaKnowledgeReview, KnowledgeApplicability, build_candidate_envelope
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.mission_context import draft_briefing
from jarvis.mission_proposal import JoseDecisions, build_mission_definition
from jarvis.repository_freshness import RepositoryFreshnessResolver
from tests.test_jarvis_knowledge import candidate


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


class MissionContextTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileKnowledgeStore(Path(self.tmp.name) / "knowledge")
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _run("git", "init", "-q", "-b", "main", cwd=self.repo)
        _run("git", "config", "user.email", "x@example.invalid", cwd=self.repo)
        _run("git", "config", "user.name", "x", cwd=self.repo)
        (self.repo / "f.txt").write_text("1")
        _run("git", "add", "f.txt", cwd=self.repo)
        _run("git", "commit", "-q", "-m", "one", cwd=self.repo)
        self.resolver = RepositoryFreshnessResolver(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def _promote(self, candidate_id, claim):
        content = candidate(candidate_id=candidate_id, claim=claim, applicability=KnowledgeApplicability(("jarvis",)))
        envelope = build_candidate_envelope(content)
        self.store.save_candidate(envelope)
        self.store.transition_candidate(candidate_id, "awaiting_emma_review")
        self.store.transition_candidate(candidate_id, "awaiting_human_authorization")
        review = EmmaKnowledgeReview(candidate_id, envelope.content.revision, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
        authorization = parse_knowledge_authorization(render_knowledge_authorization(envelope))
        self.store.save_review(review)
        self.store.save_authorization(authorization)
        return self.store.promote(candidate_id, review, authorization)


class DraftBriefingTests(MissionContextTestCase):
    def test_returns_citations_for_matching_entries(self):
        self._promote("aaaaaaaa-0001-4aaa-8aaa-aaaaaaaaaaaa", "A prior established constraint.")
        briefing = draft_briefing(self.store, self.resolver, product_areas=("jarvis",))
        self.assertEqual(len(briefing.citations), 1)
        self.assertEqual(briefing.citations[0].claim, "A prior established constraint.")

    def test_no_citations_when_nothing_matches(self):
        briefing = draft_briefing(self.store, self.resolver, product_areas=("billing",))
        self.assertEqual(briefing.citations, ())


class KnowledgeIsolationFromMissionDefinitionTests(MissionContextTestCase):
    def test_briefing_content_never_appears_in_a_built_mission_definition(self):
        self._promote("bbbbbbbb-0002-4bbb-8bbb-bbbbbbbbbbbb",
                       "UNIQUE_KNOWLEDGE_MARKER_STRING_XYZ must never leak into a proposal.")
        briefing = draft_briefing(self.store, self.resolver, product_areas=("jarvis",))
        self.assertTrue(briefing.citations)

        decisions = JoseDecisions(
            outcome="ship the thing", scope=("do the thing",),
            non_goals=(), acceptance_criteria=("it works",),
        )
        definition = build_mission_definition("a fresh objective", decisions)

        rendered = " ".join([
            definition["outcome"], " ".join(definition["scope"]),
            " ".join(definition["non_goals"]), " ".join(definition["acceptance_criteria"]),
        ])
        self.assertNotIn("UNIQUE_KNOWLEDGE_MARKER_STRING_XYZ", rendered)


if __name__ == "__main__":
    unittest.main()
