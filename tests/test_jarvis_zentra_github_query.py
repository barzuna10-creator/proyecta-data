from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest

from jarvis.zentra_github_query import GitHubQueryError, ReadOnlyGitHubQuery


class GitHubQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def _fake(self, body: str) -> Path:
        path = self.root / "gh"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_only_fixed_read_only_commands_and_allowlisted_repository(self):
        log = self.root / "argv"
        fake = self._fake(f'printf "%s\\n" "$@" >> "{log}"\nprintf \'[]\'')
        query = ReadOnlyGitHubQuery(fake, frozenset({"github.com/barzuna10-creator/proyecta-data"}))
        result = query.observe("github.com/barzuna10-creator/proyecta-data")
        self.assertEqual((), result.pull_requests); self.assertEqual((), result.workflow_runs)
        text = log.read_text()
        self.assertIn("pr\nlist\n--repo\ngithub.com/barzuna10-creator/proyecta-data", text)
        self.assertIn("run\nlist\n--repo\ngithub.com/barzuna10-creator/proyecta-data", text)
        self.assertNotRegex(text, r"\b(create|merge|close|rerun|cancel|delete|edit)\b")
        with self.assertRaises(GitHubQueryError): query.observe("attacker/repo")

    def test_malformed_or_oversized_output_fails_closed(self):
        malformed = self._fake("printf 'not-json'")
        with self.assertRaises(GitHubQueryError):
            ReadOnlyGitHubQuery(malformed, frozenset({"github.com/o/r"})).observe("github.com/o/r")

    def test_schema_drift_fails_closed_instead_of_passing_raw_payload(self):
        fake = self._fake("printf '[{\"number\":1,\"unexpected\":\"payload\"}]'")
        with self.assertRaises(GitHubQueryError):
            ReadOnlyGitHubQuery(fake, frozenset({"github.com/o/r"})).observe("github.com/o/r")

    def test_environment_does_not_forward_token_or_host_routing(self):
        fake = self._fake("[ -z \"$GH_TOKEN$GITHUB_TOKEN$GH_HOST$GH_CONFIG_DIR$XDG_CONFIG_HOME\" ] || exit 9\nprintf '[]'")
        query = ReadOnlyGitHubQuery(fake, frozenset({"github.com/o/r"}), inherited_environment={
            "GH_TOKEN":"secret", "GITHUB_TOKEN":"secret", "GH_HOST":"evil.invalid",
            "GH_CONFIG_DIR":"/tmp/hostile", "XDG_CONFIG_HOME":"/tmp/hostile",
        })
        self.assertEqual((), query.observe("github.com/o/r").pull_requests)

    def test_owner_repo_without_literal_github_host_is_rejected(self):
        fake = self._fake("printf '[]'")
        query = ReadOnlyGitHubQuery(fake, frozenset({"github.com/o/r"}))
        with self.assertRaises(GitHubQueryError): query.observe("o/r")
        with self.assertRaises(GitHubQueryError): query.observe("evil.invalid/o/r")
