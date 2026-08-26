import io
import json
import unittest
from unittest import mock

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
