import copy
import datetime
import json
from pathlib import Path
import unittest

import jarvis.mission_supervisor as mission_supervisor
import jarvis.status as status
from jarvis.mission_coordinator import DEFAULT_CI_POLL_TIMEOUT_SECONDS
from jarvis.status import UnknownMissionState, classify_mission_state, compute_staleness, project_mission_status
from orchestrator.adapters.claude_cli_adapter import DEFAULT_TIMEOUT_SECONDS as EMMA_DISPATCH_TIMEOUT_SECONDS
from orchestrator.adapters.codex_cli_adapter import DEFAULT_TIMEOUT_SECONDS as EMILIO_DISPATCH_TIMEOUT_SECONDS
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


_NOW = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(seconds: float) -> str:
    return _iso(_NOW - datetime.timedelta(seconds=seconds))


def _record(state, *, updated_at=None, dispatch_ledger=None):
    """A schema-shaped Mission Record (via the real _create_intake_mission()
    fixture) with only `state`/`updated_at`/`dispatch_ledger` overridden --
    compute_staleness()/project_mission_status() never touch anything else
    about it, so mutating these three fields directly (rather than driving
    a mission through real chugel transitions/dispatch reservations) is
    sufficient and far simpler."""
    record = _create_intake_mission("watchdog fixture")
    record["state"] = state
    record["updated_at"] = updated_at if updated_at is not None else _ago(0)
    record["dispatch_ledger"] = dispatch_ledger or []
    return record


def _in_flight_entry(role, *, updated_at):
    return {
        "role": role,
        "attempt": 0,
        "invocation_id": "11111111-1111-1111-1111-111111111111",
        "provider": role,
        "model": "test-model",
        "status": "IN_FLIGHT",
        "result_classification": None,
        "reserved_at": updated_at,
        "updated_at": updated_at,
    }


class TimeoutConstantsDriftGuardTests(ChugelTestCase):
    """jarvis/status.py deliberately duplicates these three numbers as
    local constants rather than importing them, to respect two
    structural walls tests/test_jarvis_foundation_boundaries.py enforces
    (jarvis.mission_coordinator has exactly one allowed importer,
    mission_supervisor.py; no jarvis production module may import
    orchestrator.adapters at all). Test files are not scanned by either
    boundary test, so these drift-guard tests import the three real
    values directly and pin them against jarvis/status.py's local
    constants -- the same declare-and-test-pin pattern already used for
    _GATE_WAITING_STATES vs. mission_supervisor.GATE_WAITING_STATES."""

    def test_local_emilio_timeout_matches_the_real_codex_adapter_constant(self):
        self.assertEqual(status._EMILIO_DISPATCH_TIMEOUT_SECONDS, EMILIO_DISPATCH_TIMEOUT_SECONDS)

    def test_local_emma_timeout_matches_the_real_claude_adapter_constant(self):
        self.assertEqual(status._EMMA_DISPATCH_TIMEOUT_SECONDS, EMMA_DISPATCH_TIMEOUT_SECONDS)

    def test_local_ci_poll_timeout_matches_the_real_mission_coordinator_constant(self):
        self.assertEqual(status.DEFAULT_CI_POLL_TIMEOUT_SECONDS, DEFAULT_CI_POLL_TIMEOUT_SECONDS)


class GateWaitingStatesCrossCheckTests(ChugelTestCase):
    """jarvis/status.py declares its own _GATE_WAITING_STATES rather than
    importing jarvis/mission_supervisor.py's GATE_WAITING_STATES (which
    would be a real import cycle -- see jarvis/status.py's own comment).
    This test is the declare-and-test-pin cross-check that keeps the two
    from silently drifting apart."""

    def test_gate_waiting_states_matches_mission_supervisor(self):
        self.assertEqual(status._GATE_WAITING_STATES, mission_supervisor.GATE_WAITING_STATES)


class ComputeStalenessGateWaitingTests(ChugelTestCase):
    def test_gate_waiting_state_is_always_normal_regardless_of_age(self):
        for state in sorted(status._GATE_WAITING_STATES):
            record = _record(state, updated_at=_ago(365 * 24 * 3600))
            self.assertEqual(
                compute_staleness(record, now=_NOW), "NORMAL",
                f"gate-waiting state {state} must stay NORMAL no matter how old",
            )


class ComputeStalenessDispatchBoundaryTests(ChugelTestCase):
    """Emilio (BUILDING/CORRECTING) and Emma (REVIEWING) IN_FLIGHT dispatch,
    at the exact NORMAL/WATCH boundary (== timeout, still NORMAL: the
    policy is "NORMAL <= timeout") and the exact WATCH/STALLED boundary
    (== 2x timeout, already STALLED: the policy is "STALLED >= 2x
    timeout")."""

    CASES = (
        ("BUILDING", "emilio", EMILIO_DISPATCH_TIMEOUT_SECONDS),
        ("CORRECTING", "emilio", EMILIO_DISPATCH_TIMEOUT_SECONDS),
        ("REVIEWING", "emma", EMMA_DISPATCH_TIMEOUT_SECONDS),
    )

    def test_at_or_under_timeout_is_normal(self):
        for state, role, timeout in self.CASES:
            for elapsed in (0.0, timeout / 2, timeout):
                ledger = [_in_flight_entry(role, updated_at=_ago(elapsed))]
                record = _record(state, dispatch_ledger=ledger)
                self.assertEqual(
                    compute_staleness(record, now=_NOW), "NORMAL",
                    f"{state}/{role} at {elapsed}s (timeout={timeout}) should be NORMAL",
                )

    def test_just_over_timeout_is_watch(self):
        for state, role, timeout in self.CASES:
            ledger = [_in_flight_entry(role, updated_at=_ago(timeout + 1))]
            record = _record(state, dispatch_ledger=ledger)
            self.assertEqual(compute_staleness(record, now=_NOW), "WATCH")

    def test_just_under_double_timeout_is_still_watch(self):
        for state, role, timeout in self.CASES:
            ledger = [_in_flight_entry(role, updated_at=_ago(timeout * 2 - 1))]
            record = _record(state, dispatch_ledger=ledger)
            self.assertEqual(compute_staleness(record, now=_NOW), "WATCH")

    def test_at_or_over_double_timeout_is_stalled(self):
        for state, role, timeout in self.CASES:
            for elapsed in (timeout * 2, timeout * 3):
                ledger = [_in_flight_entry(role, updated_at=_ago(elapsed))]
                record = _record(state, dispatch_ledger=ledger)
                self.assertEqual(
                    compute_staleness(record, now=_NOW), "STALLED",
                    f"{state}/{role} at {elapsed}s (timeout={timeout}) should be STALLED",
                )


class ComputeStalenessRetryBudgetExhaustionTests(ChugelTestCase):
    """The "retry/budget exhaustion" shape: an auto-advanceable dispatch
    state (e.g. BUILDING) with no live IN_FLIGHT ledger entry at all --
    only a FINALIZED/RESULT_RECORDED one, or none -- so the reference
    timestamp falls back to the record's own updated_at rather than a
    ledger entry."""

    def test_no_live_dispatch_entry_falls_back_to_record_updated_at(self):
        finalized = _in_flight_entry("emilio", updated_at=_ago(9999))
        finalized["status"] = "FINALIZED"
        record = _record("BUILDING", updated_at=_ago(EMILIO_DISPATCH_TIMEOUT_SECONDS * 2), dispatch_ledger=[finalized])
        self.assertEqual(compute_staleness(record, now=_NOW), "STALLED")

    def test_empty_ledger_falls_back_to_record_updated_at(self):
        record = _record("BUILDING", updated_at=_ago(EMILIO_DISPATCH_TIMEOUT_SECONDS + 1), dispatch_ledger=[])
        self.assertEqual(compute_staleness(record, now=_NOW), "WATCH")


class ComputeStalenessCiLifecycleTests(ChugelTestCase):
    def test_ci_lifecycle_states_at_boundaries(self):
        timeout = DEFAULT_CI_POLL_TIMEOUT_SECONDS
        for state in sorted(status._CI_LIFECYCLE_STATES):
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(timeout)), now=_NOW), "NORMAL")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(timeout + 1)), now=_NOW), "WATCH")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(timeout * 2 - 1)), now=_NOW), "WATCH")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(timeout * 2)), now=_NOW), "STALLED")


class ComputeStalenessOtherLiveStatesTests(ChugelTestCase):
    """José-approved policy for this bucket is phrased inclusively at the
    WATCH boundary -- "NORMAL < 30min; WATCH >= 30min; STALLED >= 90min"
    -- unlike the dispatch/CI-lifecycle categories' "NORMAL <= timeout;
    WATCH > timeout". Boundaries below match that literal phrasing:
    exactly 30 minutes is already WATCH, one second under is still
    NORMAL."""

    def test_other_live_states_at_30_and_90_minute_boundaries(self):
        watch_at = 30 * 60
        stalled_at = 90 * 60
        for state in sorted(status._OTHER_LIVE_STATES):
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(watch_at - 1)), now=_NOW), "NORMAL")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(watch_at)), now=_NOW), "WATCH")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(stalled_at - 1)), now=_NOW), "WATCH")
            self.assertEqual(
                compute_staleness(_record(state, updated_at=_ago(stalled_at)), now=_NOW), "STALLED")


class ComputeStalenessNonCandidateStatesTests(ChugelTestCase):
    """Terminal, BLOCKED, and the MERGED-family states are never staleness
    candidates by elapsed time, however old -- no automatic action is
    ever expected there regardless of age."""

    def test_never_a_candidate_regardless_of_age(self):
        never_candidates = ("MERGED", "DEPLOY_PENDING", "VERIFYING_PRODUCTION",
                             "COMPLETED", "BLOCKED", "FAILED", "CANCELLED", "ROLLED_BACK")
        for state in never_candidates:
            record = _record(state, updated_at=_ago(365 * 24 * 3600))
            self.assertEqual(compute_staleness(record, now=_NOW), "NORMAL", state)


class ComputeStalenessRestartEquivalenceTests(ChugelTestCase):
    """staleness is a pure function of (record, now) -- a fresh process
    reading the identical record at the identical wall-clock moment must
    produce the identical answer, with no dependency on process memory
    (e.g. mission_supervisor's own in-memory _stalled set, deliberately
    never consulted here)."""

    def test_same_record_and_now_yields_identical_result_across_independent_calls(self):
        ledger = [_in_flight_entry("emilio", updated_at=_ago(EMILIO_DISPATCH_TIMEOUT_SECONDS * 3))]
        record = _record("BUILDING", dispatch_ledger=ledger)
        first = compute_staleness(copy.deepcopy(record), now=_NOW)
        second = compute_staleness(copy.deepcopy(record), now=_NOW)
        self.assertEqual(first, second)
        self.assertEqual(first, "STALLED")

    def test_project_mission_status_restart_equivalence(self):
        ledger = [_in_flight_entry("emma", updated_at=_ago(EMMA_DISPATCH_TIMEOUT_SECONDS * 3))]
        record = _record("REVIEWING", dispatch_ledger=ledger)
        first = project_mission_status(copy.deepcopy(record), now=_NOW)
        second = project_mission_status(copy.deepcopy(record), now=_NOW)
        self.assertEqual(first.staleness, second.staleness)
        self.assertEqual(first.staleness, "STALLED")


class ComputeStalenessHistoricalPwnbfRegressionTests(ChugelTestCase):
    """Direct regression reproducing the real historical PWNBF stall: a
    mission sitting IN_FLIGHT on an Emilio dispatch for 4h43m (17,580s)
    with no progress. Must classify as STALLED -- this is exactly the
    shape Pillar 3 exists to catch."""

    def test_four_hours_forty_three_minutes_stall_is_stalled(self):
        four_h_43m = (4 * 3600) + (43 * 60)
        ledger = [_in_flight_entry("emilio", updated_at=_ago(four_h_43m))]
        record = _record("BUILDING", dispatch_ledger=ledger)
        self.assertEqual(compute_staleness(record, now=_NOW), "STALLED")


class ComputeStalenessInvalidTimestampFailSafeTests(ChugelTestCase):
    """An unparseable or future timestamp must never manufacture a
    WATCH/STALLED classification -- fail-safe, not fail-closed."""

    def test_malformed_updated_at_is_normal(self):
        record = _record("BUILDING", updated_at="not-a-timestamp")
        self.assertEqual(compute_staleness(record, now=_NOW), "NORMAL")

    def test_missing_updated_at_is_normal(self):
        record = _record("BUILDING")
        del record["updated_at"]
        self.assertEqual(compute_staleness(record, now=_NOW), "NORMAL")

    def test_future_updated_at_is_normal(self):
        future = _iso(_NOW + datetime.timedelta(hours=1))
        record = _record("BUILDING", updated_at=future)
        self.assertEqual(compute_staleness(record, now=_NOW), "NORMAL")

    def test_malformed_dispatch_ledger_entry_updated_at_falls_back_safely(self):
        entry = _in_flight_entry("emilio", updated_at="garbage")
        record = _record("BUILDING", updated_at=_ago(0), dispatch_ledger=[entry])
        self.assertEqual(compute_staleness(record, now=_NOW), "NORMAL")


class ProjectMissionStatusExposesStalenessTests(ChugelTestCase):
    def test_staleness_field_is_present_and_correctly_derived(self):
        record = _record("INTAKE", updated_at=_ago(0))
        self.assertEqual(project_mission_status(record, now=_NOW).staleness, "NORMAL")
        stale_record = _record("INTAKE", updated_at=_ago(91 * 60))
        self.assertEqual(project_mission_status(stale_record, now=_NOW).staleness, "STALLED")

    def test_now_defaults_to_real_current_time_when_omitted(self):
        # No `now=` supplied -- project_mission_status() must fall back to
        # the real current UTC clock rather than raising or requiring it.
        # Anchored to the real wall clock (not the fixed _NOW fixture used
        # elsewhere in this file), since that is exactly what is under test.
        real_now = _iso(datetime.datetime.now(datetime.timezone.utc))
        record = _record("INTAKE", updated_at=real_now)
        self.assertEqual(project_mission_status(record).staleness, "NORMAL")


if __name__ == "__main__":
    unittest.main()
