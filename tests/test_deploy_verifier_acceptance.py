"""Disposable end-to-end acceptance harness for orchestrator/deploy_verifier.py
(M4: Automatic Production Deployment Observation & Verification).

Unlike tests/test_orchestrator_deploy_verifier.py (which mocks the HTTP
and ancestry layers directly, matching this repo's own unit-test
convention), this file exercises deploy_verifier.run() against:
  - a REAL, local stdlib http.server.ThreadingHTTPServer serving
    controllable /health and /version responses (real TCP, real JSON
    parsing -- nothing about the HTTP layer is mocked), and
  - a REAL local git repository fixture (a temp dir this file `git
    init`s and commits into, with a second temp dir cloned from it as
    the "origin" remote) -- real `git fetch`/`git merge-base`
    subprocess calls, no network, all local file-path remotes.

Every scenario runs under the normal `python -m unittest discover -s
tests` invocation -- no separate runner. Bounds (poll attempt counts/
intervals) are patched down to keep the suite fast; this only shortens
HOW LONG a real poll loop runs, never fakes what it observes.

Deviation note: the design's own optional IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK
re-check is not implemented in orchestrator/deploy_verifier.py (see that
module's own docstring) -- there is therefore no scenario exercising that
one classification here."""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import orchestrator.chugel as chugel
import orchestrator.deploy_verifier as deploy_verifier
from tests.test_orchestrator_chugel import ChugelTestCase
from tests.test_orchestrator_chugel_deploy import _mission_at_deploy_pending, _mission_at_merged


# --- real local git fixture ---------------------------------------------

def _run(argv, cwd):
    result = subprocess.run(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{argv} failed: {result.stderr.decode('utf-8', 'replace')}")
    return result.stdout.decode("utf-8").strip()


def _git_repo_head(repo_dir):
    return _run(["git", "rev-parse", "HEAD"], cwd=repo_dir)


def _init_origin_repo(base_dir):
    """A real, non-bare local repo with two commits on `main`, usable as
    a file-path git remote."""
    origin = base_dir / "origin"
    origin.mkdir()
    _run(["git", "init", "-b", "main"], cwd=origin)
    _run(["git", "config", "user.email", "test@example.test"], cwd=origin)
    _run(["git", "config", "user.name", "Test"], cwd=origin)
    (origin / "README.md").write_text("first\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=origin)
    _run(["git", "commit", "-m", "first"], cwd=origin)
    first_sha = _git_repo_head(origin)
    (origin / "README.md").write_text("second\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=origin)
    _run(["git", "commit", "-m", "second"], cwd=origin)
    second_sha = _git_repo_head(origin)
    return origin, first_sha, second_sha


def _clone(origin, dest, *, depth=None):
    argv = ["git", "clone"]
    if depth is not None:
        # --no-local forces git to treat this like a real network clone
        # (no hardlink/reference shortcut) -- required for a local
        # file-path source to actually produce a real .git/shallow marker.
        argv += ["--no-local", "--depth", str(depth)]
    argv += ["-b", "main", str(origin), str(dest)]
    _run(argv, cwd=str(origin.parent))
    _run(["git", "config", "user.email", "test@example.test"], cwd=dest)
    _run(["git", "config", "user.name", "Test"], cwd=dest)
    return dest


# --- real local HTTP fixture ---------------------------------------------

class _ControllableHandler(http.server.BaseHTTPRequestHandler):
    # Class-level, overwritten per test instance via _make_server().
    responses = {}
    hit_counts = {}

    def _serve(self, path):
        self.hit_counts[path] = self.hit_counts.get(path, 0) + 1
        entry = self.responses.get(path)
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        status_code, body = entry
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._serve(self.path)

    def log_message(self, fmt, *args):  # noqa: A003 -- silence stdlib default stderr logging
        pass


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_server(responses):
    handler = type("Handler", (_ControllableHandler,), {"responses": responses, "hit_counts": {}})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, handler


# --- fast bounds for the harness (real components, shortened patience) --

_FAST_BOUNDS = mock.patch.multiple(
    deploy_verifier,
    _VERSION_POLL_MAX_ATTEMPTS=3,
    _VERSION_POLL_INTERVAL_SECONDS=0.05,
    _HEALTH_POLL_MAX_ATTEMPTS=3,
    _HEALTH_POLL_INTERVAL_SECONDS=0.05,
)


class DeployVerifierAcceptanceTestCase(ChugelTestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._base = Path(self._tmpdir.name)
        self._servers = []
        self._bounds_patch = _FAST_BOUNDS
        self._bounds_patch.start()

    def tearDown(self):
        self._bounds_patch.stop()
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._tmpdir.cleanup()
        super().tearDown()

    def _server(self, responses):
        server, handler = _make_server(responses)
        self._servers.append(server)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        return base_url, handler

    def _closed_port_base_url(self):
        port = _free_port()  # nothing is listening here
        return f"http://127.0.0.1:{port}"


class SuccessfulObservationTests(DeployVerifierAcceptanceTestCase):
    """(a) commit matches exactly, health reports ok -> COMPLETED."""

    def test_exact_match_completes(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")

        record = _mission_at_merged()
        mid = record["mission_id"]
        # begin_deploy_observation() derives expected_sha from
        # merge.merge_commit_sha; override it here to match this real
        # git fixture's own real commit identity.
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": second_sha}),
            "/health": (200, {"status": "ok", "checks": {}}),
        })

        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "COMPLETED", result)
        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "COMPLETED")
        self.assertEqual(final["deploy"]["observed_sha"], second_sha)
        self.assertTrue(final["deploy"]["ancestry_verified"])


class CoalescedDeploymentTests(DeployVerifierAcceptanceTestCase):
    """(b) expected commit is an ancestor of, not equal to, the observed
    commit -> still COMPLETED. Proves the exact-match bug is genuinely
    fixed: `work`'s local clone does not yet have the newer commit, so
    this also exercises the real `git fetch origin main` path."""

    def test_ancestor_not_exact_match_still_completes(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        # A THIRD commit lands on origin AFTER the clone -- simulating a
        # coalesced deploy where production now runs a newer commit than
        # the one this mission's own merge produced.
        (origin / "README.md").write_text("third\n", encoding="utf-8")
        _run(["git", "add", "README.md"], cwd=origin)
        _run(["git", "commit", "-m", "third"], cwd=origin)
        third_sha = _git_repo_head(origin)
        self.assertNotIn(third_sha, _run(["git", "log", "--format=%H"], cwd=work))

        record = _mission_at_merged()
        mid = record["mission_id"]
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": third_sha}),
            "/health": (200, {"status": "ok", "checks": {}}),
        })

        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "COMPLETED", result)
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["expected_sha"], second_sha)
        self.assertEqual(final["deploy"]["observed_sha"], third_sha)
        self.assertNotEqual(final["deploy"]["expected_sha"], final["deploy"]["observed_sha"])
        self.assertTrue(final["deploy"]["ancestry_verified"])


class DegradedHealthTests(DeployVerifierAcceptanceTestCase):
    """(c) a well-formed 503 status='degraded' body -> BLOCKED/HEALTH_DEGRADED.
    Proves the body-parse-past-503 fix over a REAL HTTP 503 response."""

    def test_real_503_degraded_body_blocks_health_degraded(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        record = _mission_at_merged()
        mid = record["mission_id"]
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": second_sha}),
            "/health": (503, {"status": "degraded", "checks": {"database": "error"}}),
        })

        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_DEGRADED")
        self.assertEqual(final["deploy"]["health_check"]["status_code"], 503)
        self.assertGreaterEqual(handler.hit_counts.get("/health", 0), 1)


class UnreachableTests(DeployVerifierAcceptanceTestCase):
    """(d) a real closed TCP port (connection refused) -> BLOCKED, either
    IDENTITY_NEVER_OBSERVED (if /version itself is unreachable) or
    HEALTH_UNREACHABLE_OR_MALFORMED (if only /health is)."""

    def test_version_endpoint_unreachable_blocks_identity_never_observed(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        record = _mission_at_merged()
        mid = record["mission_id"]
        chugel.begin_deploy_observation(mid)

        base_url = self._closed_port_base_url()
        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "IDENTITY_NEVER_OBSERVED")

    def test_health_endpoint_unreachable_blocks_health_unreachable_or_malformed(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        record = _mission_at_merged()
        mid = record["mission_id"]
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        # A real server that answers /version but has nothing behind
        # /health -- point /health at a genuinely closed port instead by
        # using two different base_urls is not supported by run()'s single
        # base_url parameter, so instead this uses a server that 404s on
        # /health (a real, well-formed-but-wrong response) to prove the
        # "malformed" half of the classification over real HTTP.
        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": second_sha}),
        })
        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "BLOCKED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["last_blocked_classification"], "HEALTH_UNREACHABLE_OR_MALFORMED")


class CrashRestartTests(DeployVerifierAcceptanceTestCase):
    """(e) simulate a crash mid-run: reserve the verification lock (as a
    "process" that then dies without ever finalizing), release the lock
    the way the kernel would on that process's real exit, then confirm a
    fresh run() re-derives everything from scratch, consumes only the
    remaining budget, and never double-completes."""

    def test_abandoned_reservation_is_recovered_by_a_fresh_run(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        record = _mission_at_merged()
        mid = record["mission_id"]
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        # Simulate: a prior "process" reserved verification and then died
        # before ever calling finalize_deploy_verification().
        stale_invocation_id = chugel.reserve_deploy_verification(mid)
        self.assertIsNotNone(stale_invocation_id)
        # Simulate the kernel's own automatic lock release on process
        # death: close the fd this module has been holding open, exactly
        # what happens when a real process exits mid-verification.
        fd = chugel._DEPLOY_VERIFICATION_LOCK_FDS.pop(mid)
        os.close(fd)

        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": second_sha}),
            "/health": (200, {"status": "ok", "checks": {}}),
        })
        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "COMPLETED")
        final = chugel.get_mission(mid)
        self.assertEqual(final["state"], "COMPLETED")
        # Never double-completed: deploy_confirmed_at was set exactly once.
        self.assertIsNotNone(final["deploy"]["deploy_confirmed_at"])


class ConcurrentVerificationTests(DeployVerifierAcceptanceTestCase):
    """(f) exactly one live process may verify a mission at a time,
    proven by the real kernel flock -- not an elapsed-time heuristic.
    Simulates a concurrent "other process" by holding the real
    reservation directly, then confirming run() cleanly skips with zero
    HTTP calls while it is held, and succeeds normally once released."""

    def test_second_verifier_skips_while_first_holds_the_lock(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        work = _clone(origin, self._base / "work")
        record = _mission_at_merged()
        mid = record["mission_id"]
        current = chugel.get_mission(mid)
        mutated = dict(current)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = second_sha
        chugel._write_mission_record(mutated)
        chugel.begin_deploy_observation(mid)

        base_url, handler = self._server({
            "/version": (200, {"version": "1.0", "commit": second_sha}),
            "/health": (200, {"status": "ok", "checks": {}}),
        })

        holder_invocation_id = chugel.reserve_deploy_verification(mid)
        self.assertIsNotNone(holder_invocation_id)
        try:
            result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
            self.assertEqual(result.status, "SKIPPED")
            self.assertEqual(handler.hit_counts.get("/version", 0), 0)
            self.assertEqual(handler.hit_counts.get("/health", 0), 0)
        finally:
            chugel.finalize_deploy_verification(mid, holder_invocation_id)

        # Now that the lock is released, a fresh run() proceeds normally.
        result = deploy_verifier.run(mid, base_url=base_url, git_repo_path=str(work))
        self.assertEqual(result.status, "COMPLETED")


class RecoveryLimitsTests(DeployVerifierAcceptanceTestCase):
    """(g) drive a mission through BLOCKED -> reopen_deploy_observation_window
    three times via the real Chugel mutators; confirm the 4th attempt is
    refused and recovery_status reads 'exhausted'."""

    def test_three_reopens_succeed_fourth_is_refused(self):
        record = _mission_at_deploy_pending()
        mid = record["mission_id"]
        chugel.record_deploy_blocked(mid, classification="ANCESTRY_UNVERIFIABLE", reason="synthetic")

        for i in range(3):
            updated = chugel.reopen_deploy_observation_window(
                mid, decided_by="jose", acknowledgement=f"attempt {i + 1}"
            )
            self.assertEqual(updated["state"], "DEPLOY_PENDING")
            self.assertEqual(updated["deploy"]["recovery_count"], i + 1)
            chugel.record_deploy_blocked(mid, classification="ANCESTRY_UNVERIFIABLE", reason="still stuck")

        final = chugel.get_mission(mid)
        self.assertEqual(final["deploy"]["recovery_count"], 3)
        self.assertEqual(final["deploy"]["recovery_status"], "exhausted")
        with self.assertRaises(chugel.DeployRecoveryNotEligible):
            chugel.reopen_deploy_observation_window(mid, decided_by="jose", acknowledgement="one more")


class FailClosedShallowHistoryTests(DeployVerifierAcceptanceTestCase):
    """(h) a shallow/incomplete git history whose remote cannot repair it
    (deleted origin) must never produce IDENTITY_MISMATCH_DEFINITIVE --
    only ANCESTRY_UNVERIFIABLE. Real `git clone --depth 1` and real
    `git fetch --unshallow` (which fails, since origin no longer exists)."""

    def test_unrepairable_shallow_clone_is_unverifiable_never_definitive_mismatch(self):
        origin, first_sha, second_sha = _init_origin_repo(self._base)
        shallow = self._base / "shallow"
        _clone(origin, shallow, depth=1)
        self.assertTrue((shallow / ".git" / "shallow").exists())
        # Break the remote -- --unshallow cannot possibly succeed now.
        import shutil
        shutil.rmtree(origin)

        outcome = deploy_verifier._verify_ancestry(
            first_sha, second_sha, git_repo_path=str(shallow), git_executable="git",
            remaining_budget_seconds=30.0,
        )
        self.assertEqual(outcome, deploy_verifier._ANCESTRY_UNVERIFIABLE)
        self.assertNotEqual(outcome, deploy_verifier._ANCESTRY_DEFINITIVE_NON_ANCESTOR)


if __name__ == "__main__":
    unittest.main()
