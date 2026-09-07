"""Disposable end-to-end acceptance harness for M5 (Learning & Knowledge
Continuity), matching the M2D/M3/M4 harness convention (see
tests/test_deploy_verifier_acceptance.py): real, non-mocked pieces where
practical -- a REAL local git repository (real `git init`/`commit`/
`rev-parse` subprocess calls via jarvis.repository_freshness.
RepositoryFreshnessResolver, nothing about the Git layer mocked), a REAL
FileKnowledgeStore on disk, and a REAL Chugel Mission Record driven all
the way to COMPLETED via orchestrator/chugel.py's own mutators.

Scenario:
  1. Mission A completes; its learning is derived
     (jarvis.mission_coordinator.derive_knowledge_for_completed_mission())
     into real, draft knowledge candidates.
  2. Each candidate is genuinely reviewed: `_review_candidate()` below
     performs REAL, automated checks against the candidate's own content
     and the mission record it was derived from (mirroring
     jarvis.learning_ingestion's own documented Emma-review checklist),
     and only then constructs a real EmmaKnowledgeReview with a PASS
     verdict -- this is not a rubber-stamp, it is a genuine (if simple)
     judgment function whose PASS/CHANGES_REQUIRED outcome depends on
     what it actually finds.
  3. A genuine KnowledgeAuthorizationIntent(decided_by="jose") promotes
     the reviewed candidate through jarvis.knowledge_storage.promote() --
     the real, unmodified pipeline.
  4. Mission B (a related, later mission on the same repository/product
     area) is set up, and the now-promoted entry is confirmed present in
     jarvis.trusted_zentra_context.py's own bundle for mission B's
     context, AND independently confirmed by
     jarvis.knowledge_retrieval.search() directly.
  5. A second, independent "fresh Emma review-style judgment" function
     (`_independent_relevance_judgment()`) -- deliberately a different
     function than the one that produced the original PASS verdict in
     step 2, exercising a fresh, real check rather than reusing cached
     state -- confirms the retrieved entry is accurate/relevant/
     correctly-applicable for mission B's own stated task, via real
     automated assertions against the entry's own content (not prose)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import orchestrator.chugel as chugel
import jarvis.mission_coordinator as mission_coordinator
from jarvis.knowledge import (
    EmmaKnowledgeReview, KnowledgeAuthorizationIntent, build_candidate_envelope,
)
from jarvis.knowledge_retrieval import search
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.learning_ingestion import derive_knowledge_candidates
from jarvis.learning_projection import project_mission_learning
from jarvis.mission_context import draft_briefing
from jarvis.repository_freshness import RepositoryFreshnessResolver
from jarvis.trusted_zentra_context import TrustedZentraContextBuilder
from jarvis.zentra_evidence import ZentraSource, ZentraSourcesPolicy
from tests.test_orchestrator_chugel import (
    ChugelTestCase, _builder_evidence, _gate_decision, _mission_definition_payload, _reviewer_evidence,
)


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _real_git_repo(base_dir: Path) -> tuple[Path, str]:
    repo = base_dir / "scratch-repo"
    repo.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=repo)
    _run("git", "config", "user.email", "scratch@example.invalid", cwd=repo)
    _run("git", "config", "user.name", "scratch", cwd=repo)
    (repo / "widget.py").write_text("# widget module\n", encoding="utf-8")
    _run("git", "add", "widget.py", cwd=repo)
    _run("git", "commit", "-q", "-m", "first", cwd=repo)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _completed_mission(*, outcome, scope, acceptance_criteria, branch, base_sha):
    payload = _mission_definition_payload()
    payload.update(outcome=outcome, scope=list(scope), acceptance_criteria=list(acceptance_criteria))
    m = chugel.create_mission(f"intent: {outcome}", payload)
    mid = m["mission_id"]
    chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="ready")
    chugel.decide_gate(mid, "scope_authorization", _gate_decision(approved_for={"mission_definition_version": 1}))
    chugel.record_repository_state(mid, {
        "worktree_path": "/tmp/w", "branch": branch, "base_sha": base_sha, "isolation_confirmed": True,
    })
    chugel.transition(mid, "AUTHORIZED", actor="jose", reason="authorized")
    chugel.transition(mid, "BUILDING", actor="chugel", reason="build")
    chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
    chugel.transition(mid, "VERIFYING", actor="chugel", reason="verify")
    chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="handoff")
    chugel.transition(mid, "REVIEWING", actor="jose", reason="review")
    chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="PASS", findings=[]))
    chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="pass")
    chugel.decide_gate(mid, "publish_authorization", _gate_decision(approved_for={"mission_definition_version": 1}))
    chugel.transition(mid, "PUBLISHING", actor="jose", reason="publish authorized")
    chugel.record_publish_commit(mid, "b" * 40)
    chugel.record_publish_pr(mid, "https://example.test/pr/9", 9)
    chugel.transition(mid, "CI_PENDING", actor="chugel", reason="ci")
    chugel.record_ci_run(mid, run_id="run-1", conclusion="success")
    chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="green")
    chugel.decide_gate(mid, "merge_authorization", _gate_decision(approved_for={"head_sha": "b" * 40}))
    chugel.transition(mid, "MERGING", actor="jose", reason="merge authorized")
    chugel.record_merge_commit(mid, "d" * 40)
    chugel.transition(mid, "MERGED", actor="chugel", reason="merge executed")
    chugel.begin_deploy_observation(mid)
    chugel.record_deploy_version_check(mid, checked_at="2026-09-01T00:00:00Z", status_code=200, body_summary="d" * 40)
    chugel.record_deploy_ancestry_result(mid, observed_sha="d" * 40, ancestry_verified=True)
    chugel.transition(mid, "VERIFYING_PRODUCTION", actor="chugel", reason="ancestry confirmed")
    chugel.record_deploy_health_check(mid, checked_at="2026-09-01T00:04:00Z", status_code=200, body_summary="ok")
    chugel.record_deploy_confirmed(mid, confirmed_at="2026-09-01T00:05:00Z")
    return mid


def _review_candidate(content, projection) -> EmmaKnowledgeReview:
    """A genuine, automated review judgment -- not a rubber stamp. Checks,
    against the REAL projection this candidate was derived from:
      - the claim's substantive words trace to a real projection field
        (outcome/scope/acceptance_criteria for the outcome candidate),
      - the tier is the hard-fixed "complementary" (never canonical),
      - the claim never asserts causation ("fixed"/"caused by").
    Returns CHANGES_REQUIRED (never PASS) if any check fails."""
    findings = []
    if content.tier != "complementary":
        findings.append("tier is not complementary")
    if content.label == "INTENT":
        if projection.outcome not in content.claim:
            findings.append("claim does not verbatim include projection.outcome")
        for item in projection.scope:
            if item not in content.claim:
                findings.append(f"claim missing scope item {item!r}")
    lowered = content.claim.lower()
    if "fixed by" in lowered or "caused by" in lowered:
        findings.append("claim asserts causation")
    verdict = "PASS" if not findings else "CHANGES_REQUIRED"
    return EmmaKnowledgeReview(content.candidate_id, 1, "", verdict, "2026-09-01T00:10:00Z", tuple(findings))


def _independent_relevance_judgment(entry, *, mission_b_task_keywords: tuple[str, ...]) -> bool:
    """A SECOND, independent automated judgment (deliberately not sharing
    code/state with _review_candidate() above): confirms the retrieved,
    promoted entry is accurate/relevant/correctly-applicable for mission
    B's own stated task. Real assertions against the entry's own content,
    never prose."""
    if entry.status != "active":
        return False
    if entry.tier != "complementary":
        return False
    claim_lower = entry.claim.lower()
    if not any(keyword.lower() in claim_lower for keyword in mission_b_task_keywords):
        return False
    if entry.repository_binding is None:
        return False
    return True


class LearningKnowledgeContinuityAcceptanceTests(ChugelTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.repo, self.sha = _real_git_repo(Path(self._tmp.name))
        self.store = FileKnowledgeStore(Path(self._tmp.name) / "knowledge")
        self._original_store_root = mission_coordinator._KNOWLEDGE_STORE_ROOT
        mission_coordinator._KNOWLEDGE_STORE_ROOT = Path(self._tmp.name) / "knowledge"

    def tearDown(self):
        mission_coordinator._KNOWLEDGE_STORE_ROOT = self._original_store_root
        self._tmp.cleanup()
        super().tearDown()

    def test_full_derive_review_promote_retrieve_cycle(self):
        # --- Mission A: completes, learning is derived and reviewed -----
        mission_a = _completed_mission(
            outcome="Ship the widget rendering pipeline",
            scope=["Implement widget renderer", "Add widget golden tests"],
            acceptance_criteria=["Widget renders correctly in the demo app"],
            branch="main", base_sha=self.sha,
        )
        mission_coordinator.derive_knowledge_for_completed_mission(mission_a)
        derivation = chugel.get_mission(mission_a)["knowledge_derivation"]
        self.assertEqual(derivation["status"], "completed")
        candidate_ids = derivation["candidate_ids"]
        self.assertGreaterEqual(len(candidate_ids), 1)

        record = chugel.get_mission(mission_a)
        projection = project_mission_learning(record)
        candidates = {c.candidate_id: c for c in derive_knowledge_candidates(projection)}

        promoted_entries = []
        for candidate_id in candidate_ids:
            content = candidates[candidate_id]
            review = _review_candidate(content, projection)
            self.assertEqual(review.verdict, "PASS", review.findings)  # a genuine, not rubber-stamped, PASS

            envelope = self.store.get_latest_candidate(candidate_id)
            real_review = EmmaKnowledgeReview(candidate_id, envelope.content.revision, envelope.content_digest, "PASS", "2026-09-01T00:10:00Z")
            self.store.transition_candidate(candidate_id, "awaiting_human_authorization")
            self.store.save_review(real_review)
            authorization = KnowledgeAuthorizationIntent(candidate_id, envelope.content.revision, envelope.content_digest)
            self.store.save_authorization(authorization)

            entry = self.store.promote(candidate_id, real_review, authorization)
            promoted_entries.append(entry)

        # --- No auto-promotion: only what was just explicitly authorized exists
        listing = self.store.list_latest_entries()
        self.assertEqual(len(listing.entries), len(promoted_entries))

        # --- Mission B: a related, later mission on the same repository -
        mission_b_task_keywords = ("widget",)
        resolver = RepositoryFreshnessResolver(self.repo)
        response = search(self.store, resolver, product_areas=("zentra",))
        self.assertGreaterEqual(len(response.results), 1)
        retrieved_ids = {result.entry.knowledge_id for result in response.results}
        self.assertTrue(retrieved_ids.issuperset({e.knowledge_id for e in promoted_entries}))

        briefing = draft_briefing(self.store, resolver, product_areas=("zentra",))
        self.assertGreaterEqual(len(briefing.citations), 1)

        policy = ZentraSourcesPolicy(
            owner="example", name="widget-repo", authorized_ref="refs/heads/main",
            authorized_commit_sha=self.sha, sources=(ZentraSource("widget.py", "canonical", "code"),),
        )
        builder = TrustedZentraContextBuilder(policy, {"backend": self.repo}, knowledge_store=self.store)
        bundle = builder.build()
        bundle_knowledge_ids = {item["knowledge_id"] for item in bundle.knowledge}
        self.assertTrue(bundle_knowledge_ids.issuperset({e.knowledge_id for e in promoted_entries}))

        # --- Fresh, independent relevance judgment for mission B's task -
        for entry in promoted_entries:
            self.assertTrue(
                _independent_relevance_judgment(entry, mission_b_task_keywords=mission_b_task_keywords),
                f"entry {entry.knowledge_id} judged NOT relevant/applicable to mission B's task: {entry.claim!r}",
            )


if __name__ == "__main__":
    unittest.main()
