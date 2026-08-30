import dataclasses
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from jarvis.authorization import parse_authorization_command, render_authorization_command
from jarvis.drafts import build_draft_envelope
from jarvis.storage import (
    AuthorizationIntentAlreadyRecorded,
    DraftAlreadyExists,
    FileJarvisStore,
    StoragePathUnsafe,
    StoredArtifactCorrupt,
)
from jarvis import _safe_io
from tests.test_jarvis_drafts import DRAFT_ID, valid_draft


class JarvisStorageTests(unittest.TestCase):
    def test_storage_uses_shared_hardened_primitives(self):
        import inspect
        import jarvis.storage
        self.assertIn("from jarvis._safe_io import", inspect.getsource(jarvis.storage))
        self.assertIsNotNone(_safe_io.atomic_create)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "jarvis"
        self.store = FileJarvisStore(self.root)
        self.envelope = build_draft_envelope(valid_draft())

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip_and_immutable_revision(self):
        self.store.save_draft(self.envelope)
        self.assertEqual(self.envelope, self.store.get_draft(DRAFT_ID, 1))
        with self.assertRaises(DraftAlreadyExists):
            self.store.save_draft(self.envelope)

    def test_reopening_an_existing_store_root_does_not_raise(self):
        """Control Plane V1: a server restart re-opens FileJarvisStore
        against its already-populated root -- this must never raise."""
        self.store.save_draft(self.envelope)
        reopened = FileJarvisStore(self.root)
        self.assertEqual(self.envelope, reopened.get_draft(DRAFT_ID, 1))

    def test_multiple_revisions_and_latest(self):
        self.store.save_draft(self.envelope)
        second = build_draft_envelope(dataclasses.replace(
            valid_draft(), revision=2, updated_at="2026-08-25T20:00:01Z"
        ))
        self.store.save_draft(second)
        self.assertEqual((1, 2), self.store.list_draft_revisions(DRAFT_ID))
        self.assertEqual(2, self.store.get_latest_draft(DRAFT_ID).draft.revision)

    def test_invalid_id_and_revision_are_rejected_before_path_use(self):
        for bad_id in ("../../escape", "/tmp/escape", "NOT-A-UUID"):
            with self.assertRaises(ValueError):
                self.store.get_draft(bad_id, 1)
        with self.assertRaises(ValueError):
            self.store.get_draft(DRAFT_ID, True)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_draft_file_is_refused(self):
        directory = self.root / "drafts" / DRAFT_ID
        directory.mkdir(mode=0o700)
        target = self.root / "outside.json"
        target.write_text("{}", encoding="utf-8")
        (directory / "00000001.json").symlink_to(target)
        with self.assertRaises(StoragePathUnsafe):
            self.store.get_draft(DRAFT_ID, 1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_draft_directory_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "drafts" / DRAFT_ID).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StoragePathUnsafe):
            self.store.list_draft_revisions(DRAFT_ID)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
        "descriptor-based no-follow directory validation requires POSIX",
    )
    def test_directory_swap_after_mkdir_never_chmods_symlink_target(self):
        """Exercise the former mkdir -> chmod(path) TOCTOU boundary.

        The replacement happens inside mkdir's return boundary: code before
        mkdir cannot observe it, while the very next storage operation sees a
        symlink. The old ordering followed that link with os.chmod before its
        final safety check; descriptor-based validation must fail first.
        """
        directory = self.store.root / "drafts" / DRAFT_ID
        parked_directory = self.store.root / "parked-draft-directory"
        external_target = self.store.root / "external-target"
        external_target.mkdir(mode=0o755)
        os.chmod(external_target, 0o755)
        original_mkdir = Path.mkdir
        swapped = False

        def racing_mkdir(path, *args, **kwargs):
            nonlocal swapped
            result = original_mkdir(path, *args, **kwargs)
            if path == directory and not swapped:
                swapped = True
                path.rename(parked_directory)
                path.symlink_to(external_target, target_is_directory=True)
            return result

        with mock.patch.object(Path, "mkdir", new=racing_mkdir):
            with self.assertRaises(StoragePathUnsafe):
                self.store.save_draft(self.envelope)

        self.assertTrue(swapped, "test must hit the post-mkdir race boundary")
        self.assertTrue(directory.is_symlink())
        self.assertEqual(0o755, external_target.stat().st_mode & 0o777)

    def test_digest_corruption_is_detected(self):
        self.store.save_draft(self.envelope)
        path = self.root / "drafts" / DRAFT_ID / "00000001.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["digest"] = "b" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(StoredArtifactCorrupt):
            self.store.get_draft(DRAFT_ID, 1)

    def test_truncated_json_is_detected(self):
        self.store.save_draft(self.envelope)
        path = self.root / "drafts" / DRAFT_ID / "00000001.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(StoredArtifactCorrupt):
            self.store.get_draft(DRAFT_ID, 1)

    def test_authorization_intent_is_append_only_and_replay_fails(self):
        intent = parse_authorization_command(render_authorization_command(self.envelope))
        intent_id = self.store.record_authorization_intent(intent)
        self.assertEqual(64, len(intent_id))
        with self.assertRaises(AuthorizationIntentAlreadyRecorded):
            self.store.record_authorization_intent(intent)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_owner_only_permissions(self):
        self.store.save_draft(self.envelope)
        draft_path = self.root / "drafts" / DRAFT_ID / "00000001.json"
        self.assertEqual(0o700, self.root.stat().st_mode & 0o777)
        self.assertEqual(0o600, draft_path.stat().st_mode & 0o777)

    def test_concurrent_creation_has_one_winner(self):
        outcomes = []

        def save():
            try:
                self.store.save_draft(self.envelope)
                outcomes.append("saved")
            except DraftAlreadyExists:
                outcomes.append("exists")

        threads = [threading.Thread(target=save) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(["saved", "exists"], outcomes)


class ProposalIdempotencyTests(unittest.TestCase):
    """Control Plane V1: proposal_id is only ever an idempotency key over a
    request, never an identity -- the server always mints draft_id."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FileJarvisStore(Path(self.temporary.name) / "jarvis")

    def tearDown(self):
        self.temporary.cleanup()

    def test_same_proposal_same_content_returns_same_draft_id_and_writes_once(self):
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        draft_id = "323e4567-e89b-42d3-a456-426614174002"
        digest = "a" * 64
        returned_first = self.store.record_proposal(proposal_id, digest, draft_id)
        returned_second = self.store.record_proposal(proposal_id, digest, "999e4567-e89b-42d3-a456-426614174999")
        self.assertEqual(draft_id, returned_first)
        self.assertEqual(draft_id, returned_second)  # second call's draft_id argument is ignored

    def test_same_proposal_different_content_fails_closed(self):
        from jarvis.storage import ProposalContentMismatch
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        draft_id = "323e4567-e89b-42d3-a456-426614174002"
        self.store.record_proposal(proposal_id, "a" * 64, draft_id)
        with self.assertRaises(ProposalContentMismatch):
            self.store.record_proposal(proposal_id, "b" * 64, "999e4567-e89b-42d3-a456-426614174999")
        # the original association is untouched
        self.assertEqual(draft_id, self.store.get_proposal(proposal_id)["draft_id"])

    def test_survives_restart_via_a_fresh_store_instance(self):
        proposal_id = "223e4567-e89b-42d3-a456-426614174001"
        draft_id = "323e4567-e89b-42d3-a456-426614174002"
        digest = "a" * 64
        self.store.record_proposal(proposal_id, digest, draft_id)
        reopened = FileJarvisStore(self.store.root)  # simulates a fresh process
        self.assertEqual(
            draft_id,
            reopened.record_proposal(proposal_id, digest, "999e4567-e89b-42d3-a456-426614174999"),
        )

    def test_unknown_proposal_returns_none(self):
        self.assertIsNone(self.store.get_proposal("223e4567-e89b-42d3-a456-426614174001"))


class AuthorizationEffectTests(unittest.TestCase):
    """The durable idempotency signal jarvis.mission_authorization_bridge
    relies on to never create a mission twice for the same intent."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FileJarvisStore(Path(self.temporary.name) / "jarvis")

    def tearDown(self):
        self.temporary.cleanup()

    def test_recording_twice_with_same_mission_id_is_idempotent(self):
        intent_id = "c" * 64
        mission_id = "423e4567-e89b-42d3-a456-426614174003"
        self.assertEqual(mission_id, self.store.record_authorization_effect(intent_id, mission_id))
        self.assertEqual(mission_id, self.store.record_authorization_effect(intent_id, mission_id))

    def test_recording_twice_with_different_mission_id_fails_closed(self):
        from jarvis.storage import AuthorizationEffectMismatch
        intent_id = "c" * 64
        self.store.record_authorization_effect(intent_id, "423e4567-e89b-42d3-a456-426614174003")
        with self.assertRaises(AuthorizationEffectMismatch):
            self.store.record_authorization_effect(intent_id, "523e4567-e89b-42d3-a456-426614174004")

    def test_unrecorded_effect_returns_none(self):
        self.assertIsNone(self.store.get_authorization_effect("c" * 64))

    def test_mark_draft_authorized_removes_it_from_pending(self):
        self.store.save_draft(build_draft_envelope(valid_draft()))
        self.assertIn(DRAFT_ID, self.store.list_pending_draft_ids())
        self.store.mark_draft_authorized(DRAFT_ID)
        self.assertNotIn(DRAFT_ID, self.store.list_pending_draft_ids())
        self.store.mark_draft_authorized(DRAFT_ID)  # idempotent, no error


class ObjectiveStorageTests(unittest.TestCase):
    """Jarvis God Mode M1. Deliberate mirror of JarvisStorageTests above,
    for the Objective artifact type."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "jarvis"
        self.store = FileJarvisStore(self.root)
        from jarvis.objectives import build_objective_envelope
        from tests.test_jarvis_objectives import OBJECTIVE_ID, proposed_objective
        self.OBJECTIVE_ID = OBJECTIVE_ID
        self.build_objective_envelope = build_objective_envelope
        self.proposed_objective = proposed_objective
        self.envelope = build_objective_envelope(proposed_objective())

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip_and_immutable_revision(self):
        from jarvis.storage import ObjectiveAlreadyExists
        self.store.save_objective(self.envelope)
        self.assertEqual(self.envelope, self.store.get_objective(self.OBJECTIVE_ID, 1))
        with self.assertRaises(ObjectiveAlreadyExists):
            self.store.save_objective(self.envelope)

    def test_reopening_an_existing_store_root_does_not_raise(self):
        self.store.save_objective(self.envelope)
        reopened = FileJarvisStore(self.root)
        self.assertEqual(self.envelope, reopened.get_objective(self.OBJECTIVE_ID, 1))

    def test_multiple_revisions_and_latest(self):
        self.store.save_objective(self.envelope)
        second = self.build_objective_envelope(dataclasses.replace(
            self.proposed_objective(), revision=2, updated_at="2026-08-30T20:00:01Z", status="closed",
        ))
        self.store.save_objective(second)
        self.assertEqual((1, 2), self.store.list_objective_revisions(self.OBJECTIVE_ID))
        self.assertEqual(2, self.store.get_latest_objective(self.OBJECTIVE_ID).objective.revision)

    def test_unknown_objective_id_raises_not_found(self):
        from jarvis.storage import ObjectiveNotFound
        with self.assertRaises(ObjectiveNotFound):
            self.store.get_latest_objective("923e4567-e89b-42d3-a456-426614174099")

    def test_list_objective_ids(self):
        self.assertEqual((), self.store.list_objective_ids())
        self.store.save_objective(self.envelope)
        self.assertEqual((self.OBJECTIVE_ID,), self.store.list_objective_ids())

    def test_save_objective_rejects_a_tampered_digest(self):
        from jarvis.storage import StoredArtifactCorrupt
        import dataclasses as dc
        tampered = dc.replace(self.envelope, digest="f" * 64)
        with self.assertRaises(StoredArtifactCorrupt):
            self.store.save_objective(tampered)


if __name__ == "__main__":
    unittest.main()
