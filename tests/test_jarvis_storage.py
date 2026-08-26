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


if __name__ == "__main__":
    unittest.main()
