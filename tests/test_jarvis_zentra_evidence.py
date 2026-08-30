"""jarvis/zentra_evidence.py -- Mission 005's policy loader and
allow-listed evidence gatherer. No test here ever authorizes anything;
these only prove the read-only mechanics: exact allow-list matching,
fail-closed policy validation, and real commit-bound content reads."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from jarvis.repository_freshness import FreshnessBlobNotFound
from jarvis.zentra_evidence import (
    ZentraSource,
    ZentraSourceNotAllowed,
    ZentraSourcesPolicy,
    ZentraSourcesPolicyInvalid,
    gather_evidence,
    load_policy,
)

_REAL_POLICY_PATH = Path(__file__).resolve().parents[1] / "jarvis" / "zentra_sources_policy.json"


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _write_policy(directory: Path, payload: dict) -> Path:
    path = directory / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_payload(**repo_overrides):
    repository = {
        "key": "backend", "host": "github.com", "owner": "barzuna10-creator", "name": "proyecta-data",
        "authorized_ref": "refs/heads/main", "authorized_commit_sha": "a" * 40,
        "sources": [{"path": "AGENTS.md", "tier": "canonical", "kind": "repository_file"}],
    }
    repository.update(repo_overrides)
    return {
        "schema_version": "1.0",
        "repositories": [repository],
    }


class RealPolicyFileTests(unittest.TestCase):
    """The actual, bundled, git-tracked policy this codebase ships --
    proves the manifest and its schema are mutually consistent, and that
    the specific 10-file allow-list and authorized commit José approved
    are exactly what loads."""

    def test_the_real_bundled_policy_loads_and_validates(self):
        policy = load_policy()
        self.assertEqual("barzuna10-creator", policy.owner)
        self.assertEqual("proyecta-data", policy.name)
        self.assertEqual(2, len(policy.repositories))
        self.assertEqual({"github.com"}, {repository.host for repository in policy.repositories})
        self.assertEqual("refs/remotes/origin/main", policy.authorized_ref)
        self.assertEqual("65dd75b4fc28ef8320470d608e8501dd36056eb3", policy.authorized_commit_sha)

    def test_the_real_policy_has_exactly_the_ten_authorized_paths(self):
        policy = load_policy()
        paths = {(repo.key, source.path) for repo in policy.repositories for source in repo.sources}
        self.assertEqual(9, len(paths))
        self.assertEqual(paths, {
            ("backend","AGENTS.md"), ("backend","docs/zentra/MASTER_ROADMAP.md"),
            ("backend","docs/zentra/JARVIS_V1_ORCHESTRATION.md"), ("backend","orchestrator/CHUGEL_V1.md"),
            ("backend","agents/jarvis/CONTRACT.md"), ("backend","DEPLOYMENT.md"),
            ("backend",".github/workflows/backend-ci.yml"), ("frontend","README.md"),
            ("frontend","package.json"),
        })

    def test_the_real_policy_classifies_all_nine_as_canonical(self):
        policy = load_policy()
        sources = [s for r in policy.repositories for s in r.sources]
        self.assertEqual(9, len([s for s in sources if s.tier == "canonical"]))
        self.assertFalse([s for s in sources if s.tier == "complementary"])

    def test_source_entries_have_no_leftover_supersedes_field(self):
        # Round 1 of independent review flagged an earlier `supersedes_path`
        # policy field as declared-but-inert (parsed, cross-validated, and
        # never consumed by anything). Removed entirely rather than wired
        # up half-way -- the real supersede mechanism (see
        # tests/test_jarvis_knowledge_retrieval.py's
        # SupersedeMechanismTests) is a normal candidate operation using
        # jarvis.knowledge's existing, already-tested transition/supersedes
        # machinery, not a policy-file concept at all.
        policy = load_policy()
        for repository in policy.repositories:
          for source in repository.sources:
            self.assertFalse(hasattr(source, "supersedes_path"))


class PolicyFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_fails_closed(self):
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(self.dir / "does-not-exist.json")

    def test_malformed_json_fails_closed(self):
        path = self.dir / "policy.json"
        path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(path)

    def test_missing_required_field_fails_closed(self):
        payload = _base_payload()
        del payload["repositories"][0]["authorized_commit_sha"]
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_short_commit_sha_fails_closed(self):
        payload = _base_payload(authorized_commit_sha="a" * 39)
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_uppercase_commit_sha_fails_closed(self):
        payload = _base_payload(authorized_commit_sha="A" * 40)
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_invalid_tier_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"][0]["tier"] = "definitely-canonical-trust-me"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_glob_path_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"][0]["path"] = "docs/*.md"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_path_traversal_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"][0]["path"] = "../../etc/passwd"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_absolute_path_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"][0]["path"] = "/etc/passwd"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_duplicate_path_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"].append(dict(payload["repositories"][0]["sources"][0]))
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_over_max_sources_fails_closed(self):
        payload = _base_payload()
        payload["repositories"][0]["sources"] = [
            {"path": f"file{i}.md", "tier": "canonical", "kind": "repository_file"} for i in range(26)
        ]
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_a_removed_supersedes_path_field_is_now_rejected_as_unknown(self):
        # supersedes_path existed briefly, was found inert by independent
        # review, and was removed. A policy that still tries to set it
        # must fail closed as an unrecognized property, not be silently
        # accepted and ignored.
        payload = _base_payload()
        payload["repositories"][0]["sources"][0]["supersedes_path"] = "docs/some-other-file.md"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))

    def test_unknown_additional_property_fails_closed(self):
        payload = _base_payload()
        payload["unexpected_field"] = "should not be here"
        with self.assertRaises(ZentraSourcesPolicyInvalid):
            load_policy(_write_policy(self.dir, payload))


class GatherEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "scratch-repo"
        self.repo.mkdir()
        _run("git", "init", "-q", "-b", "main", cwd=self.repo)
        _run("git", "config", "user.email", "scratch@example.invalid", cwd=self.repo)
        _run("git", "config", "user.name", "scratch", cwd=self.repo)
        (self.repo / "AGENTS.md").write_text("Zentra is a construction-cost platform.", encoding="utf-8")
        _run("git", "add", "AGENTS.md", cwd=self.repo)
        _run("git", "commit", "-q", "-m", "seed", cwd=self.repo)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), check=True, capture_output=True, text=True
        ).stdout.strip()
        self.policy = ZentraSourcesPolicy(
            owner="barzuna10-creator", name="proyecta-data", authorized_ref="refs/heads/main",
            authorized_commit_sha=self.sha,
            sources=(ZentraSource("AGENTS.md", "canonical", "repository_file"),),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_real_committed_content_and_stamps_exact_provenance(self):
        evidence, matched = gather_evidence(
            "AGENTS.md", repository_root=self.repo, evidence_id="agents_overview",
            claim="AGENTS.md describes Zentra as a construction-cost platform.", policy=self.policy,
        )
        self.assertEqual("FACT", evidence.label)
        self.assertEqual("canonical", matched.tier)
        source = evidence.sources[0]
        self.assertEqual("AGENTS.md", source.locator)
        self.assertEqual(self.sha, source.commit_sha)
        self.assertEqual("repository_file", source.kind)
        self.assertEqual(64, len(source.excerpt_sha256))

    def test_a_path_not_on_the_policy_is_refused(self):
        with self.assertRaises(ZentraSourceNotAllowed):
            gather_evidence(
                "not-on-the-allowlist.md", repository_root=self.repo, evidence_id="x",
                claim="x", policy=self.policy,
            )

    def test_close_but_not_exact_path_is_refused_no_prefix_or_glob_matching(self):
        for near_miss in ("AGENTS.md/", "agents.md", "AGENTS.MD", "./AGENTS.md", "AGENTS.md ", " AGENTS.md"):
            with self.subTest(near_miss=near_miss):
                with self.assertRaises(ZentraSourceNotAllowed):
                    gather_evidence(near_miss, repository_root=self.repo, evidence_id="x", claim="x", policy=self.policy)

    def test_a_path_on_the_policy_but_absent_at_the_authorized_commit_fails_closed(self):
        policy = ZentraSourcesPolicy(
            owner="barzuna10-creator", name="proyecta-data", authorized_ref="refs/heads/main",
            authorized_commit_sha=self.sha,
            sources=(ZentraSource("MISSING.md", "canonical", "repository_file"),),
        )
        with self.assertRaises(FreshnessBlobNotFound):
            gather_evidence("MISSING.md", repository_root=self.repo, evidence_id="x", claim="x", policy=policy)


if __name__ == "__main__":
    unittest.main()
