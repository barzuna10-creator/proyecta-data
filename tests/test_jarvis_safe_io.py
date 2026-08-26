import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from jarvis import _safe_io

ID1 = "123e4567-e89b-42d3-a456-426614174000"
ID2 = "223e4567-e89b-42d3-a456-426614174000"


def _hold(root, queue):
    from jarvis._safe_io import exclusive_entity_lock
    with exclusive_entity_lock(Path(root), ID1):
        queue.put("locked")
        time.sleep(30)


def _multi(root, ids, start, queue):
    from jarvis._safe_io import exclusive_entity_locks
    start.wait()
    with exclusive_entity_locks(Path(root), ids):
        queue.put(ids)


class SafeIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "store"

    def tearDown(self): self.temporary.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX lock contract")
    def test_non_reentrant_and_owner_only_persistent_lock(self):
        with _safe_io.exclusive_entity_lock(self.root, ID1):
            with self.assertRaises(_safe_io.LockReentryError):
                with _safe_io.exclusive_entity_lock(self.root, ID1): pass
        lock = self.root / ".locks" / f"{ID1}.lock"
        self.assertTrue(lock.exists())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX lock contract")
    def test_process_death_releases_kernel_lock(self):
        queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=_hold, args=(str(self.root), queue))
        process.start(); self.assertEqual(queue.get(timeout=5), "locked")
        process.terminate(); process.join(5)
        with _safe_io.exclusive_entity_lock(self.root, ID1): pass

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_lock_fails_closed(self):
        _safe_io.ensure_private_directory(self.root, parents=True)
        locks = _safe_io.ensure_private_directory(self.root / ".locks")
        target = self.root / "target"; target.write_text("x")
        (locks / f"{ID1}.lock").symlink_to(target)
        with self.assertRaises(_safe_io.LockUnavailable):
            with _safe_io.exclusive_entity_lock(self.root, ID1): pass

    def test_unsupported_platform_fails_closed(self):
        with mock.patch.object(_safe_io, "fcntl", None):
            with self.assertRaises(_safe_io.LockUnavailable):
                with _safe_io.exclusive_entity_lock(self.root, ID1): pass

    @unittest.skipUnless(os.name == "posix", "POSIX lock contract")
    def test_opposite_multi_lock_orders_do_not_deadlock(self):
        # Store initialization is a parent operation.  Pre-creating the trusted
        # lock parent also prevents a forked TemporaryDirectory finalizer in a
        # short-lived worker from making this test about fixture lifetime.
        _safe_io.ensure_private_directory(self.root, parents=True)
        _safe_io.ensure_private_directory(self.root / ".locks")
        start = multiprocessing.Event(); queue = multiprocessing.Queue()
        processes = [multiprocessing.Process(target=_multi, args=(str(self.root), ids, start, queue)) for ids in ((ID1, ID2), (ID2, ID1))]
        for process in processes: process.start()
        start.set()
        results = [queue.get(timeout=5), queue.get(timeout=5)]
        for process in processes: process.join(5)
        self.assertFalse(any(process.is_alive() for process in processes))
        self.assertCountEqual(results, [(ID1, ID2), (ID2, ID1)])


if __name__ == "__main__": unittest.main()
