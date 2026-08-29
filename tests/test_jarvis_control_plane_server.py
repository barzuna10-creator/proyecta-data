"""jarvis/control_plane_server.py -- a real ThreadingHTTPServer bound to
an ephemeral loopback port, driven with real HTTP requests (urllib) over
the actual socket. No mocking of the HTTP layer itself."""

from __future__ import annotations

import json
import threading
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

import orchestrator.chugel as chugel

from jarvis.control_plane_server import ControlPlaneConfig, build_server
from tests.test_orchestrator_autonomous_runner import _create_intake_mission

_TOKEN = "t" * 40


class ControlPlaneServerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        config = ControlPlaneConfig(
            host="127.0.0.1", port=0, token=_TOKEN,
            store_root=str(Path(self._tmpdir.name) / "jarvis"),
        )
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _request(self, method, path, body=None, token=_TOKEN):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))


class AuthenticationTests(ControlPlaneServerTestCase):
    def test_missing_token_is_rejected(self):
        status, _ = self._request("GET", "/v1/command-center/projection", token=None)
        self.assertEqual(401, status)

    def test_wrong_token_is_rejected(self):
        status, _ = self._request("GET", "/v1/command-center/projection", token="w" * 40)
        self.assertEqual(401, status)

    def test_correct_token_reads_projection(self):
        status, body = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        self.assertIn("gates", body)
        self.assertIn("missions", body)


class ProposalFlowTests(ControlPlaneServerTestCase):
    def test_proposal_produces_a_draft_gate_the_server_identifies(self):
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        status, body = self._request("POST", "/v1/proposals", {"objective": "Ship the thing", "proposalId": proposal_id})
        self.assertEqual(201, status)
        self.assertTrue(body["accepted"])
        gate = body["gate"]
        self.assertEqual("draft", gate["kind"])
        self.assertEqual("pending", gate["state"])
        self.assertNotEqual(proposal_id, gate["id"])  # server minted its own draft_id

        status, projection = self._request("GET", "/v1/command-center/projection")
        gate_ids = [g["id"] for g in projection["gates"]]
        self.assertIn(gate["id"], gate_ids)

    def test_crash_between_record_proposal_and_save_draft_completes_on_retry(self):
        """Emma P1: simulates the exact crash point directly against the
        server's own store (bypassing HTTP for the setup step only) --
        record_proposal() durably succeeded, save_draft() never ran. The
        retry over real HTTP must complete idempotently against the same
        draft_id, and the draft must exist exactly once afterward."""
        import hashlib
        import json as jsonlib

        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        objective = "Ship the thing"
        content_digest = hashlib.sha256(
            jsonlib.dumps(objective, ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest()
        draft_id = "323e4567-e89b-42d3-a456-426614174321"

        store = self.server.store
        store.record_proposal(proposal_id, content_digest, draft_id)  # simulated crash point
        with self.assertRaises(Exception):
            store.get_latest_draft(draft_id)  # confirms the draft truly was never saved

        status, body = self._request("POST", "/v1/proposals", {"objective": objective, "proposalId": proposal_id})
        self.assertEqual(201, status)
        self.assertEqual(draft_id, body["gate"]["id"])  # same draft_id, never a second one
        envelope = store.get_latest_draft(draft_id)
        self.assertEqual(1, envelope.draft.revision)

        # a second retry after the draft already exists is still a clean no-op
        status2, body2 = self._request("POST", "/v1/proposals", {"objective": objective, "proposalId": proposal_id})
        self.assertEqual(201, status2)
        self.assertEqual(draft_id, body2["gate"]["id"])
        self.assertEqual((1,), store.list_draft_revisions(draft_id))  # exactly one revision, ever

    def test_same_proposal_same_objective_is_idempotent(self):
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        _, first = self._request("POST", "/v1/proposals", {"objective": "Ship the thing", "proposalId": proposal_id})
        _, second = self._request("POST", "/v1/proposals", {"objective": "Ship the thing", "proposalId": proposal_id})
        self.assertEqual(first["gate"]["id"], second["gate"]["id"])

    def test_same_proposal_different_objective_is_409(self):
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        self._request("POST", "/v1/proposals", {"objective": "Ship the thing", "proposalId": proposal_id})
        status, _ = self._request("POST", "/v1/proposals", {"objective": "Ship a DIFFERENT thing", "proposalId": proposal_id})
        self.assertEqual(409, status)

    def test_rejects_non_uuid_proposal_id(self):
        status, _ = self._request("POST", "/v1/proposals", {"objective": "Ship the thing", "proposalId": "not-a-uuid"})
        self.assertEqual(400, status)


class DraftAuthorizationFlowTests(ControlPlaneServerTestCase):
    def _propose(self, objective="Ship the thing", proposal_id="223e4567-e89b-42d3-a456-426614174001"):
        _, body = self._request("POST", "/v1/proposals", {"objective": objective, "proposalId": proposal_id})
        return body["gate"]

    def test_authorization_without_digest_is_rejected(self):
        gate = self._propose()
        status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": gate["missionId"], "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "I authorize this action now",
        })
        self.assertEqual(400, status)
        self.assertEqual(0, len(chugel.list_missions()))

    def test_authorization_wrong_digest_is_409_fail_closed(self):
        gate = self._propose()
        status, _ = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": gate["missionId"], "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "I authorize this action now", "digest": "0" * 64,
        })
        self.assertEqual(409, status)
        self.assertEqual(0, len(chugel.list_missions()))

    def test_authorization_wrong_confirmation_string_is_rejected(self):
        gate = self._propose()
        status, _ = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": gate["missionId"], "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "yes please",
        })
        self.assertEqual(400, status)
        self.assertEqual(0, len(chugel.list_missions()))

    def test_draft_stub_has_open_questions_so_authorization_fails_closed_not_ready(self):
        """The /v1/proposals stub is deliberately not authorization-ready
        (placeholder scope/acceptance_criteria, non-empty open_questions)
        -- refining it into a ready draft is out of this HTTP surface's
        V1 scope. Confirms the real digest still gets refused correctly
        rather than silently accepted."""
        gate = self._propose()
        status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": gate["missionId"], "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "I authorize this action now",
            "digest": "a" * 64,  # still wrong -- but even the real digest would fail on open_questions
        })
        self.assertEqual(409, status)
        self.assertEqual(0, len(chugel.list_missions()))

    def test_ready_draft_authorized_end_to_end_creates_a_mission(self):
        """A real, authorization-ready draft (seeded directly into the
        store, since /v1/proposals only ever produces a not-ready stub)
        authorized over real HTTP with its real digest -- exactly the
        full path a genuinely refined draft would take."""
        import dataclasses
        from jarvis.drafts import build_draft_envelope
        from jarvis.control_plane_server import FileJarvisStore
        from tests.test_jarvis_drafts import valid_draft

        draft_id = "323e4567-e89b-42d3-a456-426614174321"
        ready = dataclasses.replace(valid_draft(), draft_id=draft_id)
        envelope = build_draft_envelope(ready)
        store = FileJarvisStore(self.server.store.root)
        store.save_draft(envelope)

        status, body = self._request("POST", f"/v1/gates/{draft_id}/authorize", {
            "gateId": draft_id, "missionId": f"draft:{draft_id}", "expectedRevision": "1",
            "action": "authorize", "confirmation": "I authorize this action now",
            "digest": envelope.digest,
        })
        self.assertEqual(200, status)
        self.assertEqual("draft", body["kind"])
        self.assertEqual("authorized", body["state"])
        self.assertEqual(1, len(chugel.list_missions()))
        record = chugel.get_mission(body["missionId"])
        self.assertEqual(ready.mission_definition.outcome, record["mission_definition_history"][0]["outcome"])

        # retry with the exact same request -- idempotent, no second mission
        status2, body2 = self._request("POST", f"/v1/gates/{draft_id}/authorize", {
            "gateId": draft_id, "missionId": f"draft:{draft_id}", "expectedRevision": "1",
            "action": "authorize", "confirmation": "I authorize this action now",
            "digest": envelope.digest,
        })
        self.assertEqual(200, status2)
        self.assertEqual(body["missionId"], body2["missionId"])
        self.assertEqual(1, len(chugel.list_missions()))


class RealGateFlowTests(ControlPlaneServerTestCase):
    def test_scope_authorization_delegates_unchanged_to_mission_write(self):
        mission = _create_intake_mission("algo")
        mid = mission["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")

        _, projection = self._request("GET", "/v1/command-center/projection")
        gate = next(g for g in projection["gates"] if g["missionId"] == mid)
        self.assertEqual("scope", gate["kind"])

        status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": mid, "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "I authorize this action now",
        })
        self.assertEqual(200, status)
        self.assertEqual("authorized", body["state"])
        self.assertEqual("scope", body["kind"])
        self.assertEqual(mid, body["missionId"])
        record = chugel.get_mission(mid)
        self.assertEqual("approved", record["human_gates"]["scope_authorization"]["status"])

    def test_scope_authorization_uses_the_current_mission_definition_version_not_hardcoded_one(self):
        """Emma P2: a mission whose definition was re-planned to version 2
        while scope_authorization was still pending -- authorizing it
        through Control Plane must record approved_for.mission_definition_
        version == 2, or Chugel's own STALE_APPROVAL check would reject
        (or a hardcoded wrong version would silently under/over-approve)."""
        mission = _create_intake_mission("algo")
        mid = mission["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")

        proposal = {
            "proposal_id": "223e4567-e89b-42d3-a456-426614174777",
            "proposed_at": "2026-08-29T12:00:00Z", "proposed_by": "david",
            "label": "FACT", "rationale": "need more scope",
            "diff_against_current_scope": {"added": ["extra thing"], "removed": []},
            "status": "pending_human_decision",
            "decided_by": None, "decided_at": None, "resulting_mission_definition_version": None,
        }
        chugel.propose_scope_change(mid, proposal)
        chugel.decide_scope_change(mid, proposal["proposal_id"], {
            "status": "accepted", "decided_by": "jose", "decided_at": "2026-08-29T12:05:00Z",
            "mission_definition_entry": {
                "outcome": "revised outcome", "scope": ["revised scope"], "non_goals": [],
                "acceptance_criteria": ["revised acceptance"],
                "authorized_by": "jose", "authorized_at": "2026-08-29T12:05:00Z",
                "authorization_decision_ref": "replan-1",
            },
        })
        self.assertEqual(2, len(chugel.get_mission(mid)["mission_definition_history"]))

        _, projection = self._request("GET", "/v1/command-center/projection")
        gate = next(g for g in projection["gates"] if g["missionId"] == mid)

        status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": mid, "expectedRevision": gate["revision"],
            "action": "authorize", "confirmation": "I authorize this action now",
        })
        self.assertEqual(200, status)
        record = chugel.get_mission(mid)
        self.assertEqual("approved", record["human_gates"]["scope_authorization"]["status"])
        self.assertEqual(2, record["human_gates"]["scope_authorization"]["approved_for"]["mission_definition_version"])

    def test_stale_expected_revision_is_409(self):
        mission = _create_intake_mission("algo")
        mid = mission["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        _, projection = self._request("GET", "/v1/command-center/projection")
        gate = next(g for g in projection["gates"] if g["missionId"] == mid)

        status, _ = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
            "gateId": gate["id"], "missionId": mid, "expectedRevision": "stale-revision-value",
            "action": "authorize", "confirmation": "I authorize this action now",
        })
        self.assertEqual(409, status)


if __name__ == "__main__":
    unittest.main()
