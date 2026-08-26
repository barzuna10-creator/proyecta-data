from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from jarvis.repository_freshness import (
    FreshnessGitUnavailable,
    FreshnessOutputInvalid,
    FreshnessRefInvalid,
    FreshnessRepositoryUnsafe,
    FreshnessResolutionFailed,
    FreshnessTimeout,
    RepositoryFreshnessResolver,
)


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


class ScratchRepositoryTestCase(unittest.TestCase):
    """Every test in this module operates on a disposable, freshly
    initialized scratch git repository created here -- never this actual
    project checkout."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "scratch-repo"
        self.repo.mkdir()
        _run("git", "init", "-q", "-b", "main", cwd=self.repo)
        _run("git", "config", "user.email", "scratch@example.invalid", cwd=self.repo)
        _run("git", "config", "user.name", "scratch", cwd=self.repo)
        (self.repo / "file.txt").write_text("one", encoding="utf-8")
        _run("git", "add", "file.txt", cwd=self.repo)
        _run("git", "commit", "-q", "-m", "first", cwd=self.repo)
        self.first_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), check=True, capture_output=True, text=True
        ).stdout.strip()

    def tearDown(self):
        self.temporary.cleanup()


class ConstructionValidationTests(unittest.TestCase):
    def test_relative_root_rejected(self):
        with self.assertRaises(FreshnessRepositoryUnsafe):
            RepositoryFreshnessResolver(Path("relative/path"))

    def test_missing_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FreshnessRepositoryUnsafe):
                RepositoryFreshnessResolver(Path(tmp) / "does-not-exist")

    def test_directory_without_git_entry_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FreshnessRepositoryUnsafe):
                RepositoryFreshnessResolver(Path(tmp))

    def test_symlinked_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"; real.mkdir(); (real / ".git").mkdir()
            link = Path(tmp) / "link"; link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(FreshnessRepositoryUnsafe):
                RepositoryFreshnessResolver(link)


class RefValidationReuseTests(ScratchRepositoryTestCase):
    """Proves resolve_commit() calls the single shared validator -- never
    a second implementation -- and rejects before any subprocess runs."""

    def test_invalid_ref_rejected_before_any_subprocess_call(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        for bad_ref in ("HEAD", "main", "refs/heads/a..b", "refs/heads/-x", "refs/heads/a.lock", "refs/tags/v1"):
            with self.subTest(ref=bad_ref):
                with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
                    with self.assertRaises(FreshnessRefInvalid):
                        resolver.resolve_commit(bad_ref)
                    run.assert_not_called()


class RealGitResolutionTests(ScratchRepositoryTestCase):
    def test_real_ref_resolves_to_the_real_commit(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        self.assertEqual(resolver.resolve_commit("refs/heads/main"), self.first_sha)

    def test_no_cache_reflects_a_new_commit_immediately(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        self.assertEqual(resolver.resolve_commit("refs/heads/main"), self.first_sha)
        (self.repo / "file.txt").write_text("two", encoding="utf-8")
        _run("git", "commit", "-q", "-am", "second", cwd=self.repo)
        second_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), check=True, capture_output=True, text=True
        ).stdout.strip()
        self.assertNotEqual(second_sha, self.first_sha)
        self.assertEqual(resolver.resolve_commit("refs/heads/main"), second_sha)

    def test_nonexistent_ref_fails_resolution(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with self.assertRaises(FreshnessResolutionFailed):
            resolver.resolve_commit("refs/heads/does-not-exist")

    def test_exact_argv_and_shell_false_and_end_of_options(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=(self.first_sha + "\n").encode(), stderr=b"")
            resolver.resolve_commit("refs/heads/main")
        args, kwargs = run.call_args
        argv = args[0]
        self.assertEqual(argv[0], str(Path("/usr/bin/git")))
        self.assertEqual(argv[1], "rev-parse")
        self.assertEqual(argv[2], "--verify")
        self.assertEqual(argv[3], "--end-of-options")
        self.assertEqual(argv[4], "refs/heads/main^{commit}")
        self.assertEqual(len(argv), 5)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], str(resolver._repository_root))
        self.assertEqual(kwargs["timeout"], 2.0)
        self.assertIs(kwargs["check"], False)

    def test_environment_is_a_replacement_not_a_merge(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=(self.first_sha + "\n").encode(), stderr=b"")
            resolver.resolve_commit("refs/heads/main")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env, {
            "LC_ALL": "C", "LANG": "C", "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin",
        })
        # Explicitly prove this is not os.environ merged with overrides:
        # a variable that is virtually certain to be present in the real
        # process environment (PWD is set by every POSIX shell) must be
        # absent from the constructed subprocess environment.
        import os
        self.assertNotIn("PWD", env)
        self.assertNotEqual(set(env), set(os.environ) | set(env))


class TrustedRootSubstitutionTests(ScratchRepositoryTestCase):
    def test_root_replaced_with_symlink_after_construction_fails_closed_with_zero_subprocess_calls(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        real_path = resolver._repository_root
        elsewhere = Path(self.temporary.name) / "elsewhere"; elsewhere.mkdir(); (elsewhere / ".git").mkdir()
        import shutil
        shutil.rmtree(real_path)
        real_path.symlink_to(elsewhere, target_is_directory=True)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            with self.assertRaises(FreshnessRepositoryUnsafe):
                resolver.resolve_commit("refs/heads/main")
            run.assert_not_called()


class GitInvocationFailureTaxonomyTests(ScratchRepositoryTestCase):
    def test_missing_executable_maps_to_git_unavailable(self):
        resolver = RepositoryFreshnessResolver(self.repo, git_executable=Path("/nonexistent/git"))
        with self.assertRaises(FreshnessGitUnavailable):
            resolver.resolve_commit("refs/heads/main")

    def test_timeout_maps_to_freshness_timeout_and_no_hang(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2.0)):
            started = time.time()
            with self.assertRaises(FreshnessTimeout):
                resolver.resolve_commit("refs/heads/main")
            self.assertLess(time.time() - started, 1.0)

    def test_non_zero_exit_maps_to_resolution_failed(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"fatal: bad revision\n")
            with self.assertRaises(FreshnessResolutionFailed):
                resolver.resolve_commit("refs/heads/main")

    def test_oversized_stdout_maps_to_output_invalid(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"a" * 129, stderr=b"")
            with self.assertRaises(FreshnessOutputInvalid):
                resolver.resolve_commit("refs/heads/main")

    def test_oversized_stderr_maps_to_output_invalid(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=(self.first_sha + "\n").encode(), stderr=b"e" * 4097)
            with self.assertRaises(FreshnessOutputInvalid):
                resolver.resolve_commit("refs/heads/main")

    def test_malformed_non_hex_stdout_maps_to_output_invalid(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"not-a-sha\n", stderr=b"")
            with self.assertRaises(FreshnessOutputInvalid):
                resolver.resolve_commit("refs/heads/main")

    def test_non_ascii_stdout_maps_to_output_invalid(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=("a" * 39 + "é\n").encode("utf-8"), stderr=b"")
            with self.assertRaises(FreshnessOutputInvalid):
                resolver.resolve_commit("refs/heads/main")

    def test_extra_trailing_bytes_maps_to_output_invalid(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=(self.first_sha + "\nEXTRA").encode(), stderr=b"")
            with self.assertRaises(FreshnessOutputInvalid):
                resolver.resolve_commit("refs/heads/main")

    def test_no_retry_on_failure(self):
        resolver = RepositoryFreshnessResolver(self.repo)
        with mock.patch("jarvis.repository_freshness.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"")
            with self.assertRaises(FreshnessResolutionFailed):
                resolver.resolve_commit("refs/heads/main")
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
