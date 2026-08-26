import unittest
from unittest import mock

from jarvis import mission_query
from tests.test_orchestrator_chugel import _create_intake_mission, ChugelTestCase


class MissionQueryTests(unittest.TestCase):
    def test_listing_converts_only_bounded_fields_to_frozen_values(self):
        bounded = {"mission_id": "a", "readable": False, "state": None,
                   "updated_at": None, "error_code": "MISSION_RECORD_CORRUPT"}
        with mock.patch.object(mission_query.chugel, "list_missions", return_value=[bounded]):
            result = mission_query.list_missions()
        self.assertEqual(result[0].error_code, "MISSION_RECORD_CORRUPT")
        self.assertIsNone(result[0].bucket)
        with self.assertRaises(Exception):
            result[0].state = "BUILDING"

    def test_status_uses_only_get_mission(self):
        record = {
            "mission_id": "m", "state": "INTAKE", "updated_at": "2026-01-01T00:00:00Z",
            "mission_definition_history": [], "corrective_cycle_count": 0,
            "repository": {"branch": "(unconfirmed)", "base_sha": "0" * 40,
                           "isolation_confirmed": False, "worktree_path": "secret"},
            "human_gates": {name: {"status": "not_requested"} for name in
                            ("scope_authorization", "publish_authorization", "merge_authorization")},
            "builder_evidence": [], "reviewer_evidence": [], "intent": {"raw_text": "secret"},
        }
        with mock.patch.object(mission_query.chugel, "get_mission", return_value=record) as get:
            result = mission_query.get_mission_status("m")
        get.assert_called_once_with("m")
        self.assertEqual(result.state, "INTAKE")
        self.assertEqual(result.bucket, "running")
        self.assertNotIn("secret", repr(result))


class MissionQueryReadOnlyTests(ChugelTestCase):
    def test_list_and_status_leave_canonical_bytes_identical(self):
        record = _create_intake_mission("must remain byte-identical")
        path = mission_query.chugel._MISSIONS_DIR / f"{record['mission_id']}.json"
        before = path.read_bytes()

        listing = mission_query.list_missions()
        status = mission_query.get_mission_status(record["mission_id"])

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(tuple(m.mission_id for m in listing), (record["mission_id"],))
        self.assertEqual(status.mission_id, record["mission_id"])


if __name__ == "__main__":
    unittest.main()
