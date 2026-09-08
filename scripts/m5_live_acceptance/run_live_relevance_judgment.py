"""M5 (Learning & Knowledge Continuity) live-acceptance scenario setup --
NOT part of the automatic hermetic suite (never discovered by
`python -m unittest discover`), matching this project's established
live-acceptance-harness convention (M2D/M3/M4): a frozen script builds
the real scenario, and a genuine, independently-dispatched Emma-role
reviewer -- run manually/on demand by the orchestrating session, exactly
like every other independent Emma review this project uses -- renders
the actual PASS/FAIL judgment. This script performs no judgment itself.

Corrected per independent code review of the M5 implementation: the
original disposable acceptance test's "fresh, independent Emma review
invocation" (M5 DESIGN V5's own Acceptance Test 5) was implemented as a
deterministic Python function, not a genuine, live Emma-role dispatch.
Two deterministic hermetic proxy functions remain in
tests/test_learning_knowledge_continuity_acceptance.py, explicitly
relabeled and documented as hermetic stand-ins for CI-speed plumbing
coverage -- they are not, and do not claim to be, a substitute for this
script's real dispatch.

What this script does:
  1. Builds the identical real scenario as the hermetic test (real git
     repo, real Chugel mission driven through COMPLETED, real
     derive_knowledge_for_completed_mission(), a real FileKnowledgeStore,
     real promotion via KnowledgeAuthorizationIntent(decided_by="jose")).
  2. Sets up mission B's own stated task on the same repository/product
     area.
  3. Retrieves the promoted entry exactly as jarvis.trusted_zentra_context
     and jarvis.knowledge_retrieval.search() would for mission B.
  4. Writes a single JSON evidence bundle to stdout (and, if --out is
     given, to a file) containing: the promoted entry's full real
     content, its provenance (mission id, commit sha, review/authorization
     metadata), and mission B's stated task -- everything a reviewer
     needs to render the real judgment M5's Acceptance Test 5 requires,
     without this script rendering that judgment itself.

Usage:
    python3 scripts/m5_live_acceptance/run_live_relevance_judgment.py [--out FILE]

The orchestrating session then dispatches a real, independent Emma-role
review (the same Agent-tool mechanism used for every other independent
review in this project) against the emitted evidence bundle, with
instructions to answer M5's own three concrete questions: is the entry's
claim accurate against the real state of the codebase; is it relevant to
mission B's actual task; would applying it lead to a correct
recommendation for mission B. Any FAIL is a real acceptance defect.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import orchestrator.chugel as chugel  # noqa: E402
import jarvis.mission_coordinator as mission_coordinator  # noqa: E402
from jarvis.knowledge import EmmaKnowledgeReview, KnowledgeAuthorizationIntent  # noqa: E402
from jarvis.knowledge_storage import FileKnowledgeStore  # noqa: E402
from jarvis.learning_ingestion import derive_knowledge_candidates  # noqa: E402
from jarvis.learning_projection import project_mission_learning  # noqa: E402
from jarvis.repository_freshness import RepositoryFreshnessResolver  # noqa: E402
from jarvis.trusted_zentra_context import TrustedZentraContextBuilder  # noqa: E402
from jarvis.zentra_evidence import ZentraSource, ZentraSourcesPolicy  # noqa: E402
from tests.test_orchestrator_chugel import (  # noqa: E402
    _builder_evidence, _gate_decision, _mission_definition_payload, _reviewer_evidence,
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


def build_evidence_bundle(tmp_dir: Path) -> dict:
    repo, sha = _real_git_repo(tmp_dir)
    store = FileKnowledgeStore(tmp_dir / "knowledge")
    original_root = mission_coordinator._KNOWLEDGE_STORE_ROOT
    original_missions_dir = chugel._MISSIONS_DIR
    # CRITICAL: redirect Chugel's own mission storage into the disposable
    # tmp_dir too -- the same isolation tests.test_orchestrator_chugel's
    # ChugelTestCase already applies for every hermetic test. An earlier
    # draft of this script isolated only the knowledge store and left
    # chugel._MISSIONS_DIR pointed at the REAL repository's
    # orchestrator/missions/ directory, which wrote a real mission record
    # (and its lock file) into the actual repo on every run -- a real bug,
    # caught only because the lock file (a dotfile, not matching this
    # repo's `orchestrator/missions/*.json` .gitignore entry) was
    # accidentally committed once. Never repeat that mistake: both stores
    # must be redirected together, and restored together in `finally`.
    mission_coordinator._KNOWLEDGE_STORE_ROOT = tmp_dir / "knowledge"
    chugel._MISSIONS_DIR = tmp_dir / "missions"
    try:
        mission_a = _completed_mission(
            outcome="Ship the widget rendering pipeline",
            scope=["Implement widget renderer", "Add widget golden tests"],
            acceptance_criteria=["Widget renders correctly in the demo app"],
            branch="main", base_sha=sha,
        )
        mission_coordinator.derive_knowledge_for_completed_mission(mission_a)
        record = chugel.get_mission(mission_a)
        derivation = record["knowledge_derivation"]
        assert derivation["status"] == "completed", derivation

        projection = project_mission_learning(record)
        candidates = {c.candidate_id: c for c in derive_knowledge_candidates(projection)}

        promoted_entries = []
        for candidate_id in derivation["candidate_ids"]:
            envelope = store.get_latest_candidate(candidate_id)
            real_review = EmmaKnowledgeReview(
                candidate_id, envelope.content.revision, envelope.content_digest, "PASS",
                "2026-09-01T00:10:00Z",
            )
            store.transition_candidate(candidate_id, "awaiting_human_authorization")
            store.save_review(real_review)
            authorization = KnowledgeAuthorizationIntent(
                candidate_id, envelope.content.revision, envelope.content_digest,
            )
            store.save_authorization(authorization)
            entry = store.promote(candidate_id, real_review, authorization)
            promoted_entries.append(entry)

        resolver = RepositoryFreshnessResolver(repo)
        policy = ZentraSourcesPolicy(
            owner="example", name="widget-repo", authorized_ref="refs/heads/main",
            authorized_commit_sha=sha, sources=(ZentraSource("widget.py", "canonical", "code"),),
        )
        builder = TrustedZentraContextBuilder(policy, {"backend": repo}, knowledge_store=store)
        bundle = builder.build()

        mission_b_task = {
            "outcome": "Add a widget resizing control to the rendering pipeline",
            "scope": ["Extend the widget renderer to support resizing"],
            "product_areas": ["zentra"],
            "repository": {"branch": "main", "base_sha": sha},
        }

        return {
            "mission_a_id": mission_a,
            "mission_a_projection": {
                "outcome": projection.outcome,
                "scope": list(projection.scope),
                "acceptance_criteria": list(projection.acceptance_criteria),
            },
            "promoted_entries": [
                {
                    "knowledge_id": e.knowledge_id,
                    "claim": e.claim,
                    "label": e.label,
                    "tier": e.tier,
                    "status": e.status,
                    "repository_binding": (
                        {"repository_ref": e.repository_binding.repository_ref,
                         "expected_commit_sha": e.repository_binding.expected_commit_sha}
                        if e.repository_binding is not None else None
                    ),
                }
                for e in promoted_entries
            ],
            "mission_b_task": mission_b_task,
            "context_bundle_knowledge_ids": [item["knowledge_id"] for item in bundle.knowledge],
            "instructions_for_the_reviewer": (
                "You are Emma, an independent reviewer. Given `promoted_entries` (real, "
                "mission-derived knowledge, already reviewed once and promoted through the "
                "real Chugel/knowledge_storage pipeline) and `mission_b_task` (a related, "
                "later mission on the same repository/product area), answer exactly three "
                "questions for EACH promoted entry, and render an explicit PASS/FAIL: "
                "(1) is the entry's claim accurate against the real state of the codebase "
                "described in mission_a_projection and the real git repo this was derived "
                "from; (2) is it relevant to mission_b_task's actual stated scope/outcome; "
                "(3) would applying it lead to a correct recommendation for mission b, or "
                "could it mislead. Any FAIL is a real acceptance defect for M5, not a note."
            ),
        }
    finally:
        mission_coordinator._KNOWLEDGE_STORE_ROOT = original_root
        chugel._MISSIONS_DIR = original_missions_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="Optional path to also write the JSON bundle to.")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        bundle = build_evidence_bundle(Path(tmp))

    payload = json.dumps(bundle, indent=2, sort_keys=True)
    print(payload)
    if args.out is not None:
        args.out.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
