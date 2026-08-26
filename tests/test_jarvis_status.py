import copy
import json
from pathlib import Path
import unittest

from jarvis.status import UnknownMissionState, classify_mission_state, project_mission_status
from tests.test_orchestrator_chugel import _create_intake_mission, ChugelTestCase


class MissionStatusTests(ChugelTestCase):
    def test_every_canonical_state_has_exactly_one_bucket(self):
        schema_path = Path(__file__).resolve().parents[1] / "orchestrator" / "schemas" / "mission_record.schema.json"
        states = json.loads(schema_path.read_text(encoding="utf-8"))["properties"]["state"]["enum"]
        expected = {
            "waiting_on_jose": {"SCOPE_AWAITING_AUTHORIZATION", "PUBLISH_AWAITING_AUTHORIZATION",
                                "MERGE_AWAITING_AUTHORIZATION"},
            "blocked": {"BLOCKED"},
            "terminal": {"COMPLETED", "FAILED", "CANCELLED", "ROLLED_BACK"},
        }
        expected["running"] = set(states) - set().union(*expected.values())
        actual = {bucket: {state for state in states if classify_mission_state(state) == bucket}
                  for bucket in ("running", "waiting_on_jose", "blocked", "terminal")}
        self.assertEqual(actual, expected)
        self.assertEqual(sum(len(values) for values in actual.values()), len(states))

    def test_unknown_state_fails_closed(self):
        with self.assertRaises(UnknownMissionState):
            classify_mission_state("FUTURE_STATE")

    def test_projection_is_allow_listed_and_detached(self):
        record = _create_intake_mission("TOP SECRET INTENT")
        record["future_payload"] = {"secret": "must-not-pass"}
        status = project_mission_status(record)
        rendered = repr(status)
        self.assertNotIn("TOP SECRET", rendered)
        self.assertNotIn("future_payload", rendered)
        self.assertNotIn("worktree_path", rendered)
        self.assertNotIn("dispatch", rendered)
        original = copy.deepcopy(status)
        record["repository"]["branch"] = "mutated"
        record["human_gates"]["scope_authorization"]["status"] = "approved"
        self.assertEqual(status, original)

    def test_human_action_is_derived_only_from_state(self):
        record = _create_intake_mission("intent")
        record["state"] = "SCOPE_AWAITING_AUTHORIZATION"
        self.assertEqual(project_mission_status(record).human_action_required,
                         "scope_authorization")
        self.assertEqual(project_mission_status(record).bucket, "waiting_on_jose")


if __name__ == "__main__":
    unittest.main()
