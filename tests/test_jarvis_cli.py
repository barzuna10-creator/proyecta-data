import io
import json
import subprocess
import unittest
from unittest import mock
import tempfile
from pathlib import Path

from jarvis.knowledge import EmmaKnowledgeReview, build_candidate_envelope
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_storage import FileKnowledgeStore, KnowledgeNotFound
from tests.test_jarvis_knowledge import CID, candidate

from jarvis.cli import main
from jarvis.mission_query import MissionListing, MissionQueryError
from jarvis import mission_query
from tests.test_orchestrator_chugel import ChugelTestCase


class JarvisCliTests(unittest.TestCase):
    def test_missions_is_deterministic_json(self):
        output = io.StringIO()
        listing = MissionListing("m", True, "INTAKE", "running", "2026-01-01T00:00:00Z", None)
        with mock.patch("jarvis.cli.mission_query.list_missions", return_value=(listing,)):
            self.assertEqual(main(["missions"], output=output), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["mission_id"], "m")
        self.assertEqual(json.loads(output.getvalue())[0]["bucket"], "running")

    def test_status_failure_is_concise_nonzero_and_does_not_leak_exception(self):
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch(
            "jarvis.cli.mission_query.get_mission_status",
            side_effect=MissionQueryError("MISSION_RECORD_CORRUPT"),
        ):
            result = main(["status", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
                          output=output, error=error)
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "ERROR MISSION_RECORD_CORRUPT\n")

    def test_invalid_and_missing_status_use_stable_errors(self):
        for code in ("INVALID_MISSION_ID", "MISSION_NOT_FOUND", "MISSION_PATH_UNSAFE"):
            error = io.StringIO()
            with mock.patch("jarvis.cli.mission_query.get_mission_status",
                            side_effect=MissionQueryError(code)):
                self.assertEqual(main(["status", "x"], error=error, output=io.StringIO()), 2)
            self.assertEqual(error.getvalue(), f"ERROR {code}\n")

    def test_status_requires_exact_command_shape(self):
        with self.assertRaises(SystemExit):
            main(["what is Emilio doing"], output=io.StringIO())
        with self.assertRaises(SystemExit):
            main(["status"], output=io.StringIO())

    def test_knowledge_show_is_exact_id_read_only_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"; store = FileKnowledgeStore(root)
            envelope = build_candidate_envelope(candidate()); store.save_candidate(envelope)
            store.transition_candidate(CID, "awaiting_emma_review"); store.transition_candidate(CID, "awaiting_human_authorization")
            review = EmmaKnowledgeReview(CID, 1, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
            auth = parse_knowledge_authorization(render_knowledge_authorization(envelope)); store.save_review(review); store.save_authorization(auth); store.promote(CID, review, auth)
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = io.StringIO(); self.assertEqual(main(["knowledge", "show", CID, "--store-root", str(root)], output=output), 0)
            self.assertEqual(json.loads(output.getvalue())["knowledge_id"], CID)
            after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_knowledge_show_failure_is_concise(self):
        with tempfile.TemporaryDirectory() as temporary:
            error = io.StringIO()
            self.assertEqual(main(["knowledge", "show", CID, "--store-root", temporary], output=io.StringIO(), error=error), 2)
            self.assertEqual(error.getvalue(), "ERROR KNOWLEDGE_NOT_FOUND\n")

    def test_knowledge_search_is_deterministic_read_only_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"; store = FileKnowledgeStore(root)
            repo = Path(temporary) / "scratch-repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
            envelope = build_candidate_envelope(candidate()); store.save_candidate(envelope)
            store.transition_candidate(CID, "awaiting_emma_review"); store.transition_candidate(CID, "awaiting_human_authorization")
            review = EmmaKnowledgeReview(CID, 1, envelope.content_digest, "PASS", "2026-08-26T00:00:01Z")
            auth = parse_knowledge_authorization(render_knowledge_authorization(envelope)); store.save_review(review); store.save_authorization(auth); store.promote(CID, review, auth)
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = io.StringIO()
            code = main(["knowledge", "search", "--store-root", str(root), "--repository-root", str(repo)], output=output)
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["results"][0]["entry"]["knowledge_id"], CID)
            self.assertEqual(payload["omitted_count"], 0)
            self.assertEqual(payload["eligible_beyond_top_k"], 0)
            after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_knowledge_search_invalid_top_k_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"; FileKnowledgeStore(root)
            repo = Path(temporary) / "scratch-repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
            error = io.StringIO()
            code = main(["knowledge", "search", "--store-root", str(root), "--repository-root", str(repo), "--top-k", "0"], output=io.StringIO(), error=error)
            self.assertEqual(code, 2)
            self.assertEqual(error.getvalue(), "ERROR KNOWLEDGE_SEARCH_TOP_K_INVALID\n")

    def test_knowledge_search_missing_repository_root_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"; FileKnowledgeStore(root)
            error = io.StringIO()
            code = main(["knowledge", "search", "--store-root", str(root), "--repository-root", str(Path(temporary) / "not-a-repo")], output=io.StringIO(), error=error)
            self.assertEqual(code, 2)
            self.assertEqual(error.getvalue(), "ERROR FRESHNESS_REPOSITORY_UNSAFE\n")

    def test_no_knowledge_list_command_exists(self):
        with self.assertRaises(SystemExit):
            main(["knowledge", "list"], output=io.StringIO())

    def test_knowledge_search_requires_exact_flags_no_free_text(self):
        with self.assertRaises(SystemExit):
            main(["knowledge", "search", "find me stuff about auth"], output=io.StringIO())


def _run_git(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


class ProposeSourceTests(unittest.TestCase):
    """End-to-end: real scratch git repo, real jarvis.zentra_evidence
    read (git show <sha>:<path>), real FileKnowledgeStore.save_candidate()
    -- only jarvis.cli.load_policy() is mocked, to point the CLI at a
    synthetic allow-list/commit instead of the real bundled one, keeping
    this test hermetic."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "scratch-repo"
        self.repo.mkdir()
        _run_git("git", "init", "-q", "-b", "main", cwd=self.repo)
        _run_git("git", "config", "user.email", "scratch@example.invalid", cwd=self.repo)
        _run_git("git", "config", "user.name", "scratch", cwd=self.repo)
        (self.repo / "AGENTS.md").write_text("Zentra is a construction-cost platform.", encoding="utf-8")
        _run_git("git", "add", "AGENTS.md", cwd=self.repo)
        _run_git("git", "commit", "-q", "-m", "seed", cwd=self.repo)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), check=True, capture_output=True, text=True
        ).stdout.strip()
        self.store_root = Path(self.temporary.name) / "knowledge"

        from jarvis.zentra_evidence import ZentraSource, ZentraSourcesPolicy
        self.policy = ZentraSourcesPolicy(
            owner="barzuna10-creator", name="proyecta-data", authorized_ref="refs/heads/main",
            authorized_commit_sha=self.sha,
            sources=(ZentraSource("AGENTS.md", "canonical", "repository_file"),),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _propose(self, extra_args=()):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch("jarvis.cli.load_policy", return_value=self.policy):
            code = main([
                "knowledge", "propose-source",
                "--store-root", str(self.store_root),
                "--repository-root", str(self.repo),
                "--candidate-id", CID,
                "--evidence-id", "agents_overview",
                "--path", "AGENTS.md",
                "--claim", "AGENTS.md describes Zentra as a construction-cost platform.",
                "--product-area", "zentra",
                *extra_args,
            ], output=output, error=error)
        return code, output.getvalue(), error.getvalue()

    def test_creates_an_awaiting_review_candidate_with_real_provenance(self):
        code, out, err = self._propose()
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(CID, payload["candidate_id"])
        self.assertEqual(1, payload["revision"])
        self.assertEqual("canonical", payload["tier"])
        self.assertEqual(self.sha, payload["commit_sha"])
        self.assertEqual(64, len(payload["content_digest"]))

        store = FileKnowledgeStore(self.store_root)
        self.assertEqual("draft", store.get_candidate_status(CID))
        envelope = store.get_latest_candidate(CID)
        self.assertEqual("canonical", envelope.content.tier)
        self.assertEqual(self.sha, envelope.content.repository_binding.expected_commit_sha)
        self.assertEqual(1, len(envelope.content.research_evidence))
        self.assertEqual("FACT", envelope.content.research_evidence[0].label)
        self.assertEqual(self.sha, envelope.content.research_evidence[0].sources[0].commit_sha)

    def test_a_path_off_the_allowlist_is_a_clean_error_not_a_crash(self):
        code, out, err = self._propose(["--path", "some/other/file.md"])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertEqual("ERROR ZENTRA_SOURCE_NOT_ALLOWED\n", err)

    def test_it_never_advances_past_draft_status_on_its_own(self):
        # propose-source only ever calls save_candidate() -- it must never
        # itself call transition_candidate(), save_review(), save_authorization(),
        # or promote(). Verified by construction: status stays "draft".
        self._propose()
        store = FileKnowledgeStore(self.store_root)
        self.assertEqual("draft", store.get_candidate_status(CID))
        with self.assertRaises(KnowledgeNotFound):
            store.get_latest_entry(CID)


class JarvisCliFailureIntegrationTests(ChugelTestCase):
    def _status_error(self, mission_id):
        output, error = io.StringIO(), io.StringIO()
        result = main(["status", mission_id], output=output, error=error)
        self.assertEqual(output.getvalue(), "")
        return result, error.getvalue()

    def test_invalid_missing_and_corrupt_records_fail_cleanly(self):
        self.assertEqual(self._status_error("not-an-id"),
                         (2, "ERROR INVALID_MISSION_ID\n"))
        missing = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        self.assertEqual(self._status_error(missing),
                         (2, "ERROR MISSION_NOT_FOUND\n"))
        corrupt = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        mission_query.chugel._MISSIONS_DIR.mkdir()
        (mission_query.chugel._MISSIONS_DIR / f"{corrupt}.json").write_text(
            "{internal-secret", encoding="utf-8"
        )
        self.assertEqual(self._status_error(corrupt),
                         (2, "ERROR MISSION_RECORD_CORRUPT\n"))


if __name__ == "__main__":
    unittest.main()
