"""M8 (Command Center Live Operational Visibility) -- the 16-test
consolidated acceptance list from JARVIS_M8_DESIGN_V6.md (final,
reviewed 0 P0/0 P1/0 P2 across six design rounds). Real
orchestrator.chugel Mission Records over real HTTP against a real
ThreadingHTTPServer, exactly like tests/test_jarvis_control_plane_server.py
-- no mocking of the HTTP layer itself, no mocking of Chugel's own file
storage. The two exceptions: `_build_projection`/Chugel's own
`list_missions` are wrapped (never replaced) purely to COUNT calls or to
inject deterministic pause points for the two cache-concurrency tests
(#15/#16) -- never to fake business logic."""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import orchestrator.chugel as chugel
import orchestrator.validator as validator

import jarvis.control_plane_server as control_plane_server
from jarvis import mission_query
from jarvis.control_plane_server import (
    ControlPlaneConfig,
    _ProjectionCache,
    _STATE_TO_STAGE,
    _gate_id_for,
    _stage_for_mission,
    build_server,
)

_TOKEN = "t" * 40
_CONFIRM = "I authorize this action now"
_BASE_SHA = "adc797671637050bbca5152d0306fcf6664d0d98"


# --- shared fixtures (own copies -- deliberately not importing private
# test helpers cross-file, so this module stays self-contained) ----------

def _mission_definition_payload():
    return {
        "outcome": "ship the thing", "scope": ["do the thing"], "non_goals": [],
        "acceptance_criteria": ["it works"], "authorized_by": "jose",
        "authorized_at": "2026-08-19T12:00:00Z", "authorization_decision_ref": "ref-intake-1",
    }


def _create_intake_mission(intent_text="algo", **kwargs):
    return chugel.create_mission(intent_text, _mission_definition_payload(), **kwargs)


def _gate_decision(approved_for=None):
    return {
        "status": "approved", "requested_at": "2026-08-19T12:10:00Z",
        "decided_at": "2026-08-19T12:10:00Z", "decided_by": "jose",
        "decision_ref": "ref-1", "approved_for": approved_for or {"mission_definition_version": 1},
    }


def _artifact():
    return {"mode": "commit", "commit_sha": "a" * 40, "patch_path": None, "patch_sha256": None, "patch_byte_size": None}


def _builder_evidence(attempt=0, invocation_id=None, provider=None):
    evidence = {
        "attempt": attempt, "invoked_at": "2026-08-19T12:00:00Z", "artifact": _artifact(),
        "changed_files": [], "checks": [], "skipped_checks": [], "risks": [], "assumptions": [],
        "rollback_notes": "none",
        "safety_confirmation": {
            "no_existing_work_altered": True, "no_main_change": True, "no_remote_action": True,
            "no_production_access": True, "no_protected_path_change": True, "complete_diff_inspected": True,
        },
        "handoff_document_ref": None, "conclusion": {"text": "done", "label": "FACT"},
    }
    if invocation_id is not None:
        evidence.update({
            "invocation_id": invocation_id, "provider": provider,
            "provider_session_id": None, "provider_conversation_id": "builder-thread",
        })
    return evidence


def _reviewer_evidence(attempt=0, verdict="PASS", findings=None):
    art = _artifact()
    return {
        "attempt": attempt, "invoked_at": "2026-08-19T12:05:00Z",
        "artifact_identity_confirmed_at_start": art,
        "artifact_identity_confirmed_before_conclusion": art,
        "rechecked_commands": [], "findings": findings or [], "verdict": verdict,
        "blocked_reason": "boom -- SECRET_TOKEN_XYZ leaked in stderr" if verdict == "BLOCKED" else None,
    }


def _isolate_repository(mid):
    chugel.record_repository_state(mid, {
        "worktree_path": "/tmp/m8-synthetic-worktree", "branch": "overnight/synthetic",
        "base_sha": "b" * 40, "isolation_confirmed": True,
    })


def _authorize_scope(mid):
    chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
    chugel.decide_gate(mid, "scope_authorization", _gate_decision())
    chugel.transition(mid, "AUTHORIZED", actor="jose", reason="scope approved")


def _mission_ready_for_building(mid):
    _isolate_repository(mid)
    _authorize_scope(mid)
    chugel.transition(mid, "BUILDING", actor="chugel", reason="isolated build starts")


class M8ProjectionTestCase(unittest.TestCase):
    """Same fixture discipline as
    tests/test_jarvis_control_plane_server.py's own
    ControlPlaneServerTestCase, duplicated (not imported) so this module
    can freely override config/cache/clock per test class without
    touching the shared base."""

    extra_config_kwargs: dict = {}

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self.workspace_root = Path(self._tmpdir.name).resolve() / "workspace"
        self.workspace_root.mkdir()
        subprocess.run(["git", "init"], cwd=self.workspace_root, check=True, capture_output=True)
        self.config = ControlPlaneConfig(
            host="127.0.0.1", port=0, token=_TOKEN,
            store_root=str(Path(self._tmpdir.name) / "jarvis"),
            mission_repository_root=str(self.workspace_root),
            **self.extra_config_kwargs,
        )
        self.server = build_server(self.config)
        self._before_serve()
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _before_serve(self):
        """Hook for subclasses to swap in a custom cache/clock before the
        server thread starts accepting connections."""

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        worker = getattr(self.server.supervisor, "_worker", None)
        if worker is not None:
            worker.join(timeout=5)
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


# --- #1: fixture exposes findings/checks/agents/publish for real -------

class RealDataExposureTests(M8ProjectionTestCase):
    def test_findings_checks_agents_publish_are_real_not_hardcoded(self):
        mid = _create_intake_mission("algo")["mission_id"]
        _mission_ready_for_building(mid)

        # dispatch en curso -- Emilio reserved and IN_FLIGHT, never
        # finalized, so it stays "live" for the rest of this fixture.
        _, invocation_id = chugel.reserve_dispatch(mid, role="emilio", attempt=0)
        chugel.mark_dispatch_in_flight(mid, invocation_id, provider="codex", model="o3")

        # A plain (no invocation_id) builder_evidence entry -- enough to
        # satisfy REVIEWING's own evidence requirement, but carrying no
        # invocation_id, so it does NOT finalize the IN_FLIGHT entry above.
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="builder finished")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="checks complete")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="review starts")

        findings = [
            {"id": "f-p0", "severity": "P0", "summary": "unsafe write to production config",
             "file": "orchestrator/chugel.py", "line_range": "10-20", "category": "SAFETY"},
            {"id": "f-p1", "severity": "P1", "summary": "missing test coverage",
             "file": "jarvis/status.py", "line_range": None, "category": "TESTING"},
        ]
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="BLOCKED", findings=findings))

        chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="test setup")
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="test setup")
        chugel.record_publish_pr(mid, "https://github.com/example/repo/pull/7", 7)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="test setup")
        chugel.record_ci_run(mid, run_id="run-1", conclusion="success")

        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)

        mission_findings = [f for f in projection["findings"] if f["missionId"] == mid]
        self.assertEqual(
            {"P0", "P1"}, {f["severity"] for f in mission_findings},
        )
        self.assertIn(
            {"missionId": mid, "id": "f-p0", "severity": "P0",
             "summary": "unsafe write to production config", "file": "orchestrator/chugel.py",
             "lineRange": "10-20", "category": "SAFETY"},
            mission_findings,
        )

        mission_checks = [c for c in projection["checks"] if c["missionId"] == mid]
        self.assertEqual(
            [{"missionId": mid, "runId": "run-1", "conclusion": "success", "recordedAt": mock.ANY}],
            mission_checks,
        )

        mission_entry = next(m for m in projection["missions"] if m["id"] == mid)
        self.assertEqual(
            {"prUrl": "https://github.com/example/repo/pull/7", "prNumber": 7,
             "lastCiRun": {"runId": "run-1", "conclusion": "success", "recordedAt": mock.ANY}},
            mission_entry["publish"],
        )

        mission_agents = [a for a in projection["agents"] if a["missionId"] == mid]
        self.assertEqual(1, len(mission_agents))
        self.assertEqual("emilio", mission_agents[0]["role"])
        self.assertEqual("codex", mission_agents[0]["provider"])
        self.assertEqual("o3", mission_agents[0]["model"])


# --- #2: top-level `git` field is gone -----------------------------------

class GitFieldRemovedTests(M8ProjectionTestCase):
    def test_git_field_is_absent(self):
        _create_intake_mission("algo")
        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        self.assertNotIn("git", projection)


# --- #3: findings never leak free text; summary truncation --------------

class FindingsSanitizationTests(M8ProjectionTestCase):
    def test_summary_truncated_with_visible_suffix_and_no_free_text_leak(self):
        mid = _create_intake_mission("algo")["mission_id"]
        _mission_ready_for_building(mid)
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="t")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="t")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="t")

        long_summary = "x" * 600
        findings = [{
            "id": "f-long", "severity": "P1", "summary": long_summary,
            "file": None, "line_range": None, "category": "TESTING",
        }]
        chugel.record_reviewer_evidence(
            mid, _reviewer_evidence(attempt=0, verdict="CHANGES_REQUIRED", findings=findings),
        )
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="synthetic block with a free-text reason")

        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)

        [finding] = [f for f in projection["findings"] if f["missionId"] == mid]
        self.assertEqual(500, len(finding["summary"]))
        self.assertTrue(finding["summary"].endswith("…"))
        self.assertEqual("x" * 499 + "…", finding["summary"])

        # blocked_reason (real free text on reviewer_evidence) and the
        # transition's own `reason` (real free text on state_history) must
        # never appear anywhere in the serialized payload.
        rendered = json.dumps(projection)
        self.assertNotIn("SECRET_TOKEN_XYZ", rendered)
        self.assertNotIn("synthetic block with a free-text reason", rendered)


# --- #4: activity ordering + cap -----------------------------------------

class ActivityFeedTests(M8ProjectionTestCase):
    extra_config_kwargs = {"projection_activity_limit": 3}

    def test_activity_sorted_descending_and_capped(self):
        for _ in range(5):
            _create_intake_mission(str(uuid.uuid4()))
        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        self.assertEqual(3, len(projection["activity"]))
        occurred_ats = [entry["occurredAt"] for entry in projection["activity"]]
        self.assertEqual(sorted(occurred_ats, reverse=True), occurred_ats)


# --- #5: stage/correctionRounds, BLOCKED resolves via priorState --------

class StageAndCorrectionRoundsTests(M8ProjectionTestCase):
    def test_two_correction_rounds_stay_categorically_in_progress(self):
        mid = _create_intake_mission("algo")["mission_id"]
        _mission_ready_for_building(mid)
        for _ in range(2):
            chugel.transition(mid, "BLOCKED", actor="chugel", reason="t")
            chugel.transition(mid, "CORRECTING", actor="chugel", reason="t")
            chugel.transition(mid, "BLOCKED", actor="chugel", reason="t")
            chugel.transition(mid, "VERIFYING", actor="chugel", reason="t")

        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        mission_entry = next(m for m in projection["missions"] if m["id"] == mid)
        self.assertEqual("IN_PROGRESS", mission_entry["stage"])
        self.assertEqual(2, mission_entry["correctionRounds"])

    def test_blocked_mission_exposes_prior_states_stage_not_the_literal_blocked(self):
        mid = _create_intake_mission("algo")["mission_id"]
        _mission_ready_for_building(mid)
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="t")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="t")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="t")
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="PASS"))
        chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="t")
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="t")
        chugel.record_publish_pr(mid, "https://example.com/pr/1", 1)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="t")
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="synthetic block")

        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        mission_entry = next(m for m in projection["missions"] if m["id"] == mid)
        self.assertEqual("PUBLISHING", mission_entry["stage"])
        self.assertNotEqual("BLOCKED", mission_entry["stage"])


# --- #6: cache hit within TTL, recompute after expiry --------------------

class ProjectionCacheTtlTests(M8ProjectionTestCase):
    def setUp(self):
        self._clock_value = 1000.0
        super().setUp()

    def _clock(self):
        return self._clock_value

    def _before_serve(self):
        self.server.projection_cache = _ProjectionCache(  # type: ignore[attr-defined]
            self.config.projection_cache_ttl_seconds, clock=self._clock,
        )

    def test_two_requests_within_ttl_share_one_scan_then_recompute_after_expiry(self):
        _create_intake_mission("algo")
        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted:
            status1, projection1 = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status1)
            first_calls = counted.call_count
            self._clock_value += 0.1  # well within the default 2s TTL
            status2, projection2 = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status2)
            self.assertEqual(first_calls, counted.call_count)
            self.assertEqual(projection1, projection2)

            self._clock_value += self.config.projection_cache_ttl_seconds + 0.5
            status3, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status3)
            self.assertGreater(counted.call_count, first_calls)


# --- #7: no authority regression -- byte-identical to base ---------------

class AuthorityByteIdenticalTests(unittest.TestCase):
    def _segment(self, source: str, name: str):
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
                return ast.get_source_segment(source, node)
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(source, node)
        return None

    def test_authorize_by_kind_and_handle_authorize_are_byte_identical_to_base(self):
        base_source = subprocess.run(
            ["git", "show", f"{_BASE_SHA}:jarvis/control_plane_server.py"],
            check=True, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        ).stdout
        current_source = Path(control_plane_server.__file__).read_text()
        for name in ("_AUTHORIZE_BY_KIND", "_handle_authorize"):
            base_segment = self._segment(base_source, name)
            current_segment = self._segment(current_source, name)
            self.assertIsNotNone(base_segment, f"{name} missing from base")
            self.assertEqual(base_segment, current_segment, f"{name} diverged from base")


# --- #8: bounded performance with N missions ------------------------------

class PerformanceTests(M8ProjectionTestCase):
    def test_projection_responds_within_bound_for_500_missions(self):
        for _ in range(500):
            _create_intake_mission(str(uuid.uuid4()))
        start = time.perf_counter()
        status, projection = self._request("GET", "/v1/command-center/projection")
        elapsed = time.perf_counter() - start
        self.assertEqual(200, status)
        self.assertEqual(500, len(projection["missions"]))
        self.assertLess(elapsed, 5.0, f"projection took {elapsed:.2f}s for 500 missions")


# --- #9: client-side error handling / HTML-sanitization is a frontend
# concern verified by source-level review (see command-center/app.js --
# every server-derived string is rendered exclusively via `textContent`,
# 401 renders the token-setup screen, disconnect renders a persistent
# banner keeping the last known `lastSync`). No JS test runner exists in
# this repository and this milestone does not introduce one (stdlib-only
# philosophy, consistent with the rest of the codebase) -- documented as
# an explicit scope decision in the implementation report.

# --- #10: full hermetic suite -- run separately, see implementation report.

# --- #11: POST to an unrecognized route is still a clean 404, and never
# invalidates the cache -----------------------------------------------

class UnrecognizedRouteTests(M8ProjectionTestCase):
    def setUp(self):
        self._clock_value = 2000.0
        super().setUp()

    def _clock(self):
        return self._clock_value

    def _before_serve(self):
        self.server.projection_cache = _ProjectionCache(  # type: ignore[attr-defined]
            self.config.projection_cache_ttl_seconds, clock=self._clock,
        )

    def test_post_to_unknown_route_is_404_and_does_not_invalidate_cache(self):
        _create_intake_mission("algo")
        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted:
            status, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            after_first = counted.call_count

            status, body = self._request("POST", "/v1/not-a-real-route", {"anything": "goes"})
            self.assertEqual(404, status)
            self.assertEqual({"error": "not found"}, body)

            self._clock_value += 0.1
            status, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            self.assertEqual(after_first, counted.call_count)  # still a cache hit


# --- #12: zero missions is a valid, fully-empty projection ---------------

class ZeroMissionsTests(M8ProjectionTestCase):
    def test_empty_chugel_produces_a_valid_projection(self):
        status, projection = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        for key in ("missions", "objectives", "gates", "blockedMissions", "agents",
                    "findings", "checks", "activity", "knowledge"):
            self.assertEqual([], projection[key], key)


# --- #13: state -> stage mapping exhaustiveness --------------------------

class StageMappingExhaustivenessTests(unittest.TestCase):
    def test_every_real_state_has_a_stage_mapping_entry(self):
        self.assertEqual(set(validator.STATES), set(_STATE_TO_STAGE.keys()))

    def test_stage_for_mission_never_raises_for_any_real_state_or_prior_state(self):
        for state in validator.STATES:
            _stage_for_mission(state, None)
            for prior_state in validator.STATES:
                self.assertIn(_stage_for_mission(state, prior_state), set(_STATE_TO_STAGE.values()))


# --- #14: structural cache invalidation across all 5 write endpoints ----

class StructuralInvalidationTests(M8ProjectionTestCase):
    def setUp(self):
        self._clock_value = 3000.0
        super().setUp()

    def _clock(self):
        return self._clock_value

    def _before_serve(self):
        self.server.projection_cache = _ProjectionCache(  # type: ignore[attr-defined]
            self.config.projection_cache_ttl_seconds, clock=self._clock,
        )

    def _prime_cache_and_get_call_count(self, counted):
        status, _ = self._request("GET", "/v1/command-center/projection")
        self.assertEqual(200, status)
        return counted.call_count

    def _assert_write_invalidates(self, method, path, body):
        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted:
            before = self._prime_cache_and_get_call_count(counted)
            self._clock_value += 0.1  # still within TTL
            status, resp = self._request(method, path, body)
            self.assertLess(status, 300, resp)
            self._clock_value += 0.1  # still within TTL
            status, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            self.assertGreater(counted.call_count, before)

    def test_proposals_endpoint_invalidates(self):
        self._assert_write_invalidates(
            "POST", "/v1/proposals", {"objective": "do a thing", "proposalId": str(uuid.uuid4())},
        )

    def test_conversation_endpoint_invalidates(self):
        from orchestrator.jarvis_conversation import ConversationTurnResult, DraftFieldSuggestion
        turn = ConversationTurnResult(
            reply="ok", turn_kind="PROPOSAL",
            suggestion=DraftFieldSuggestion(
                outcome="ship it", scope=["do it"], non_goals=None,
                acceptance_criteria=["done"], open_questions=None,
            ),
            objective_decomposition=None,
        )
        with mock.patch("jarvis.control_plane_server.jarvis_conversation.converse", return_value=turn):
            self._assert_write_invalidates("POST", "/v1/conversation", {"message": "please build this"})

    def test_authorize_endpoint_invalidates(self):
        mid = _create_intake_mission("algo")["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")
        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted:
            before = self._prime_cache_and_get_call_count(counted)
            status, projection = self._request("GET", "/v1/command-center/projection")
            gate = next(g for g in projection["gates"] if g["missionId"] == mid)
            self._clock_value += 0.1
            status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
                "gateId": gate["id"], "missionId": mid, "expectedRevision": gate["revision"],
                "action": "authorize", "confirmation": _CONFIRM,
            })
            self.assertEqual(200, status, body)
            self._clock_value += 0.1
            status, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            self.assertGreater(counted.call_count, before)

    def test_resume_endpoint_invalidates(self):
        mid = _create_intake_mission("algo")["mission_id"]
        _mission_ready_for_building(mid)
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="t")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="t")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="t")
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="PASS"))
        chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="t")
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="t")
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="test setup")
        # PUBLISHING is a real member of _RESUMABLE_PRIOR_STATES -- a plain
        # resume (no exceptional reopen) is legal from here.
        self._assert_write_invalidates(
            "POST", f"/v1/missions/{mid}/resume", {"confirmation": _CONFIRM},
        )

    def test_reopen_deploy_observation_endpoint_invalidates(self):
        mid = _create_intake_mission("algo")["mission_id"]
        # Drive to a BLOCKED mission whose prior state is a deploy state,
        # then exhaust the recovery budget classification so reopen is the
        # eligible path -- mirrors
        # tests/test_jarvis_control_plane_server.py's own deploy-recovery
        # fixtures.
        _mission_ready_for_building(mid)
        chugel.record_builder_evidence(mid, _builder_evidence(attempt=0))
        chugel.transition(mid, "VERIFYING", actor="chugel", reason="t")
        chugel.transition(mid, "AWAITING_REVIEW", actor="chugel", reason="t")
        chugel.transition(mid, "REVIEWING", actor="chugel", reason="t")
        chugel.record_reviewer_evidence(mid, _reviewer_evidence(attempt=0, verdict="PASS"))
        chugel.transition(mid, "PUBLISH_AWAITING_AUTHORIZATION", actor="chugel", reason="t")
        chugel.transition(mid, "PUBLISHING", actor="chugel", reason="t")
        chugel.record_publish_pr(mid, "https://example.com/pr/2", 2)
        chugel.transition(mid, "CI_PENDING", actor="chugel", reason="t")
        chugel.transition(mid, "MERGE_AWAITING_AUTHORIZATION", actor="chugel", reason="t")
        chugel.record_publish_commit(mid, "a" * 40)
        chugel.decide_gate(mid, "merge_authorization", _gate_decision(approved_for={"head_sha": "a" * 40}))
        chugel.transition(mid, "MERGING", actor="chugel", reason="t")
        chugel.record_merge_commit(mid, "b" * 40)
        chugel.transition(mid, "MERGED", actor="chugel", reason="t")
        chugel.begin_deploy_observation(mid)
        chugel.transition(mid, "BLOCKED", actor="chugel", reason="synthetic block")
        self._assert_write_invalidates(
            "POST", f"/v1/missions/{mid}/reopen-deploy-observation",
            {"confirmation": _CONFIRM, "acknowledgement": "reviewed and safe to reopen"},
        )


# --- #15: real cross-thread concurrency -- no stale publish -------------

class ConcurrentInvalidationTests(M8ProjectionTestCase):
    def test_recompute_finishing_after_a_concurrent_write_never_publishes_stale_data(self):
        mid = _create_intake_mission("algo")["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")

        scan_started = threading.Event()
        writer_done = threading.Event()
        real_build_projection = control_plane_server._build_projection

        def _paused_build_projection(store, config):
            scan_started.set()
            writer_done.wait(timeout=5)
            return real_build_projection(store, config)

        results: dict[str, tuple[int, dict]] = {}

        def _slow_get():
            results["stale_response"] = self._request("GET", "/v1/command-center/projection")

        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted, \
             mock.patch.object(control_plane_server, "_build_projection", side_effect=_paused_build_projection):
            reader = threading.Thread(target=_slow_get)
            reader.start()
            self.assertTrue(scan_started.wait(timeout=5))

            # Deliberately NOT the HTTP projection endpoint here -- the
            # reader thread above is already blocked inside the (patched)
            # projection scan, and the cache is still cold, so a second
            # concurrent GET would ALSO block on the same patched
            # function, deadlocking this test. The gate id/expectedRevision
            # are cheap, direct reads via the same mission_query seam
            # _build_projection() itself uses -- not a second HTTP call.
            gate_id = _gate_id_for(mid, "scope_authorization")
            expected_revision = mission_query.get_mission_status(mid).updated_at
            status, body = self._request("POST", f"/v1/gates/{gate_id}/authorize", {
                "gateId": gate_id, "missionId": mid, "expectedRevision": expected_revision,
                "action": "authorize", "confirmation": _CONFIRM,
            })
            self.assertEqual(200, status, body)

            writer_done.set()
            reader.join(timeout=5)
            calls_after_stale_reader = counted.call_count

            stale_status, stale_projection = results["stale_response"]
            self.assertEqual(200, stale_status)
            stale_mission = next(m for m in stale_projection["missions"] if m["id"] == mid)
            # The reader's OWN response reflects the world as of when its
            # scan started (before the authorization) -- still
            # SCOPE_AWAITING_AUTHORIZATION. This is correct and expected:
            # that request legitimately began before the write. The
            # invariant under test is not about THIS response, but about
            # whether its (now-known-stale) result gets published into the
            # cache for OTHER, later callers to receive.
            self.assertEqual("SCOPING", stale_mission["stage"])

            # The real assertion (Correction 10/V4-V5): the stale
            # in-flight recompute above finished and returned AFTER a
            # real write bumped the generation counter -- per the CAS
            # protocol, `publish_if_current()` must have refused to
            # publish that now-stale result. Confirmed here at the level
            # the protocol actually operates on (the cache never serving
            # a hit for data older than the last successful write), not
            # by re-deriving Chugel's own, separately-async supervisor
            # timing (decide_gate() alone never transitions
            # SCOPE_AWAITING_AUTHORIZATION -> AUTHORIZED; that state
            # transition is mission_supervisor's own job and is out of
            # scope for a projection-cache test). A fresh GET right after
            # must therefore be a real cache-miss (another call to
            # chugel.list_missions()), never a hit serving the discarded
            # stale payload.
            status, _ = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            self.assertGreater(counted.call_count, calls_after_stale_reader)

    def test_failed_write_never_increments_generation(self):
        cache = self.server.projection_cache
        generation_before = cache.current_generation()
        status, body = self._request("POST", "/v1/proposals", {"objective": "", "proposalId": "not-a-uuid"})
        self.assertEqual(400, status, body)
        self.assertEqual(generation_before, cache.current_generation())


# --- #16: read-side generation comparison, not just TTL ------------------

class ReadSideGenerationTests(M8ProjectionTestCase):
    def setUp(self):
        self._clock_value = 4000.0
        super().setUp()

    def _clock(self):
        return self._clock_value

    def _before_serve(self):
        self.server.projection_cache = _ProjectionCache(  # type: ignore[attr-defined]
            self.config.projection_cache_ttl_seconds, clock=self._clock,
        )

    def test_write_between_two_gets_within_ttl_forces_a_real_miss(self):
        mid = _create_intake_mission("algo")["mission_id"]
        chugel.transition(mid, "SCOPE_AWAITING_AUTHORIZATION", actor="jose", reason="scope ready")

        with mock.patch.object(chugel, "list_missions", wraps=chugel.list_missions) as counted:
            status, projection = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            after_first = counted.call_count

            gate = next(g for g in projection["gates"] if g["missionId"] == mid)
            self._clock_value += 0.1  # still within TTL
            status, body = self._request("POST", f"/v1/gates/{gate['id']}/authorize", {
                "gateId": gate["id"], "missionId": mid, "expectedRevision": gate["revision"],
                "action": "authorize", "confirmation": _CONFIRM,
            })
            self.assertEqual(200, status, body)

            self._clock_value += 0.1  # still well within TTL
            status, projection2 = self._request("GET", "/v1/command-center/projection")
            self.assertEqual(200, status)
            # This is the acceptance criterion itself (#16): within the
            # same TTL window, a successful write forces the *next* GET to
            # be a real miss (a fresh chugel.list_missions() scan), not a
            # cache hit -- generation comparison on the read side, not
            # just TTL. (The mission's own state transition out of
            # SCOPE_AWAITING_AUTHORIZATION is driven asynchronously by the
            # supervisor's own drain pass, not synchronously by
            # decide_gate() -- out of scope for this cache test.)
            self.assertGreater(counted.call_count, after_first)


if __name__ == "__main__":
    unittest.main()
