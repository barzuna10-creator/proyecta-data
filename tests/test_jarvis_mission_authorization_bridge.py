"""jarvis/mission_authorization_bridge.py -- the single bridge from a
validated MissionDraft authorization intent to a real Chugel Mission
Record. Real Chugel Mission Records and a real FileJarvisStore in a
temporary directory; nothing mocked."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import orchestrator.chugel as chugel

from jarvis.drafts import build_draft_envelope
from jarvis.mission_authorization_bridge import (
    DraftAuthorizationAttributionError,
    DraftAuthorizationDivergenceError,
    DraftAuthorizationRefused,
    close_draft_authorization,
)
from jarvis.models import AuthorizationIntent
from jarvis.storage import FileJarvisStore, authorization_intent_id
from tests.test_jarvis_drafts import DRAFT_ID, valid_draft


def _valid_decision(**overrides):
    values = {"decided_by": "jose", "decided_at": "2026-08-29T12:00:00Z"}
    values.update(overrides)
    return values


class MissionAuthorizationBridgeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_missions_dir = chugel._MISSIONS_DIR
        chugel._MISSIONS_DIR = Path(self._tmpdir.name) / "missions"
        self.store = FileJarvisStore(Path(self._tmpdir.name) / "jarvis")
        self.envelope = build_draft_envelope(valid_draft())
        self.store.save_draft(self.envelope)

    def tearDown(self):
        chugel._MISSIONS_DIR = self._original_missions_dir
        self._tmpdir.cleanup()

    def _intent(self, **overrides):
        values = {
            "draft_id": DRAFT_ID, "revision": self.envelope.draft.revision,
            "digest_algorithm": "sha256", "digest": self.envelope.digest,
        }
        values.update(overrides)
        return AuthorizationIntent(**values)


class HappyPathTests(MissionAuthorizationBridgeTestCase):
    def test_creates_exactly_one_mission_matching_the_draft(self):
        result = close_draft_authorization(self.store, self._intent(), _valid_decision())
        self.assertFalse(result.already_effective)
        record = chugel.get_mission(result.mission_id)
        self.assertEqual(record["mission_definition_history"][0]["outcome"], self.envelope.draft.mission_definition.outcome)
        self.assertEqual(record["mission_definition_history"][0]["authorized_by"], "jose")
        self.assertEqual(record["mission_definition_history"][0]["authorization_decision_ref"], result.intent_id)

    def test_second_call_with_same_intent_is_idempotent_no_duplicate_mission(self):
        first = close_draft_authorization(self.store, self._intent(), _valid_decision())
        second = close_draft_authorization(self.store, self._intent(), _valid_decision())
        self.assertEqual(first.mission_id, second.mission_id)
        self.assertTrue(second.already_effective)
        self.assertEqual(1, len(chugel.list_missions()))

    def test_draft_is_marked_authorized_and_leaves_pending_list(self):
        self.assertIn(DRAFT_ID, self.store.list_pending_draft_ids())
        close_draft_authorization(self.store, self._intent(), _valid_decision())
        self.assertNotIn(DRAFT_ID, self.store.list_pending_draft_ids())


class AttributionTests(MissionAuthorizationBridgeTestCase):
    def test_refuses_decision_not_attributed_to_jose(self):
        with self.assertRaises(DraftAuthorizationAttributionError):
            close_draft_authorization(self.store, self._intent(), _valid_decision(decided_by="not-jose"))
        self.assertEqual(0, len(chugel.list_missions()))

    def test_missing_decided_by_refused(self):
        with self.assertRaises(DraftAuthorizationAttributionError):
            close_draft_authorization(self.store, self._intent(), {"decided_at": "2026-08-29T12:00:00Z"})
        self.assertEqual(0, len(chugel.list_missions()))


class FailClosedTests(MissionAuthorizationBridgeTestCase):
    def test_stale_revision_refused_no_mission_created(self):
        with self.assertRaises(DraftAuthorizationRefused):
            close_draft_authorization(self.store, self._intent(revision=999), _valid_decision())
        self.assertEqual(0, len(chugel.list_missions()))

    def test_digest_mismatch_refused_no_mission_created(self):
        with self.assertRaises(DraftAuthorizationRefused):
            close_draft_authorization(self.store, self._intent(digest="0" * 64), _valid_decision())
        self.assertEqual(0, len(chugel.list_missions()))

    def test_draft_with_open_questions_is_not_authorization_ready(self):
        import dataclasses
        not_ready = dataclasses.replace(valid_draft(), draft_id="223e4567-e89b-42d3-a456-426614174777", open_questions=("What is the deadline?",))
        envelope = build_draft_envelope(not_ready)
        self.store.save_draft(envelope)
        intent = AuthorizationIntent(
            draft_id=not_ready.draft_id, revision=1, digest_algorithm="sha256", digest=envelope.digest,
        )
        with self.assertRaises(DraftAuthorizationRefused):
            close_draft_authorization(self.store, intent, _valid_decision())
        self.assertEqual(0, len(chugel.list_missions()))

    def test_unknown_draft_id_propagates_not_found(self):
        from jarvis.storage import DraftNotFound
        intent = AuthorizationIntent(
            draft_id="999e4567-e89b-42d3-a456-426614174999", revision=1,
            digest_algorithm="sha256", digest="a" * 64,
        )
        with self.assertRaises(DraftNotFound):
            close_draft_authorization(self.store, intent, _valid_decision())
        self.assertEqual(0, len(chugel.list_missions()))


class RestartSafetyTests(MissionAuthorizationBridgeTestCase):
    def test_intent_already_recorded_but_mission_not_yet_created_completes_on_retry(self):
        """Simulates a crash between record_authorization_intent() and
        chugel.create_mission() by calling record_authorization_intent()
        directly first, then relying on close_draft_authorization() to
        finish the job on the next attempt -- exactly what a second
        process would observe after a real crash."""
        intent = self._intent()
        self.store.record_authorization_intent(intent)  # simulate the crash point
        self.assertEqual(0, len(chugel.list_missions()))
        result = close_draft_authorization(self.store, intent, _valid_decision())
        self.assertFalse(result.already_effective)
        self.assertEqual(1, len(chugel.list_missions()))


class AuthorizationEffectReverificationTests(MissionAuthorizationBridgeTestCase):
    """Emma P2: the fast idempotent path must re-verify the recorded
    effect against the canonical Mission Record through mission_query --
    never trust the local effect record alone."""

    def test_genuine_already_effective_mission_still_reads_back_cleanly(self):
        intent = self._intent()
        first = close_draft_authorization(self.store, intent, _valid_decision())
        self.assertFalse(first.already_effective)
        second = close_draft_authorization(self.store, intent, _valid_decision())
        self.assertTrue(second.already_effective)
        self.assertEqual(first.mission_id, second.mission_id)
        self.assertEqual(1, len(chugel.list_missions()))

    def test_effect_naming_a_mission_that_no_longer_exists_fails_closed(self):
        """Simulates divergence/corruption directly: an authorization
        effect is recorded (as the bridge itself would, deterministically)
        naming a mission_id that was never actually created in Chugel --
        exactly what a corrupted or manually-tampered local store would
        produce. The fast path must refuse to report success."""
        from jarvis.mission_authorization_bridge import _derived_mission_id

        intent = self._intent()
        intent_id = authorization_intent_id(intent)
        # Must be the exact deterministic derivation, or the pre-existing
        # "existing_effect != mission_id" check would fire instead of the
        # divergence check this test targets.
        fabricated_mission_id = _derived_mission_id(intent_id)
        self.store.record_authorization_effect(intent_id, fabricated_mission_id)
        self.assertEqual(0, len(chugel.list_missions()))  # confirms it never really existed

        with self.assertRaises(DraftAuthorizationDivergenceError):
            close_draft_authorization(self.store, intent, _valid_decision())
        # still refuses even after the failed attempt -- no mission was
        # silently created as a side effect of the divergence check
        self.assertEqual(0, len(chugel.list_missions()))


if __name__ == "__main__":
    unittest.main()
