import io
import json
import unittest
from unittest import mock
import tempfile
from pathlib import Path

from jarvis.knowledge import EmmaKnowledgeReview, build_candidate_envelope
from jarvis.knowledge_authorization import parse_knowledge_authorization, render_knowledge_authorization
from jarvis.knowledge_storage import FileKnowledgeStore
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
