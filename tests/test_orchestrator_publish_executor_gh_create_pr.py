"""orchestrator/publish_executor.py::_create_pr() -- the exact regression
this fix targets: `gh pr create` does not support `--json` (unlike `gh pr
view`/`gh pr list`). Two independent proofs:

1. An argv-contract test: _create_pr() never includes "--json" in the
   argv it passes to `gh pr create`, proven against real subprocess.run
   argv capture (no string/substring guessing).
2. A real, genuinely-executable fake `gh` script (mirroring the
   CodexCliAdapter/ClaudeCliAdapter test convention) that itself exits
   non-zero if handed `--json` on `pr create`, and otherwise emits a
   plain-text URL on `pr create` and a real `--json`-shaped payload on
   `pr list` -- proving _create_pr() both avoids the invalid flag and
   correctly recovers number/url/state afterward via _find_existing_pr(),
   never by parsing `gh pr create`'s own stdout."""

from __future__ import annotations

import stat
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from textwrap import dedent

from orchestrator import publish_executor


class ArgvContractTests(unittest.TestCase):
    def test_create_pr_never_passes_json_flag(self):
        captured = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if "create" in argv:
                return mock.Mock(returncode=0, stdout=b"https://example.invalid/pr/5\n", stderr=b"")
            # the post-create _find_existing_pr() call
            return mock.Mock(
                returncode=0,
                stdout=b'[{"number": 5, "url": "https://example.invalid/pr/5", "state": "OPEN"}]',
                stderr=b"",
            )

        with mock.patch.object(publish_executor, "_run", side_effect=fake_run):
            result = publish_executor._create_pr(
                "branch", "main", "title", gh_executable="gh", repository_root="/tmp/repo",
            )

        create_argv = next(argv for argv in captured if "create" in argv)
        self.assertNotIn("--json", create_argv)
        self.assertEqual(result, {"number": 5, "url": "https://example.invalid/pr/5", "state": "OPEN"})


def _write_fake_gh(tmp_dir: Path) -> str:
    """A real, executable fake `gh`: fails loudly if `pr create` is ever
    given `--json` (the exact bug), emits a plain-text URL on a valid `pr
    create`, and answers `pr list --json number,url,state` for the
    post-create lookup."""
    script_path = tmp_dir / "fake_gh.py"
    script_path.write_text(dedent("""
        #!/usr/bin/env python3
        import sys, json

        argv = sys.argv[1:]

        if argv[:2] == ["pr", "create"]:
            if "--json" in argv:
                print("unknown flag: --json", file=sys.stderr)
                print("Usage: gh pr create [flags]", file=sys.stderr)
                sys.exit(1)
            print("https://example.invalid/pr/42")
            sys.exit(0)

        if argv[:2] == ["pr", "list"]:
            assert "--json" in argv, "pr list must still request structured output"
            idx = argv.index("--json")
            fields = argv[idx + 1].split(",")
            entry = {"number": 42, "url": "https://example.invalid/pr/42", "state": "OPEN"}
            print(json.dumps([{k: entry[k] for k in fields}]))
            sys.exit(0)

        print(f"unexpected fake gh invocation: {argv}", file=sys.stderr)
        sys.exit(2)
    """).strip() + "\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


class RealExecutableFakeGhTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_real_fake_gh_rejects_json_and_create_pr_still_succeeds(self):
        gh = _write_fake_gh(Path(self._tmpdir.name))
        result = publish_executor._create_pr(
            "branch", "main", "title", gh_executable=gh, repository_root=self._tmpdir.name,
        )
        self.assertEqual(result, {"number": 42, "url": "https://example.invalid/pr/42", "state": "OPEN"})


if __name__ == "__main__":
    unittest.main()
