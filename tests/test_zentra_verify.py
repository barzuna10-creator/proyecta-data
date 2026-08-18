"""Tests for scripts/zentra_verify.py using synthetic Git repositories only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zentra_verify.py"


def run(command: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {command}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


class SyntheticRepository(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        run(["git", "init", "-b", "main"], self.repo)
        run(["git", "config", "user.name", "Zentra Test"], self.repo)
        run(["git", "config", "user.email", "zentra-test@example.invalid"], self.repo)
        (self.repo / "README.md").write_text("synthetic\n", encoding="utf-8")
        run(["git", "add", "README.md"], self.repo)
        run(["git", "commit", "-m", "synthetic base"], self.repo)
        self.base = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def tearDown(self):
        self.temporary.cleanup()

    def verify(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.repo),
                "--expected-base",
                self.base,
                *extra,
            ],
            self.repo,
            check=False,
        )

    def branch(self):
        run(["git", "switch", "-c", "codex/synthetic-task"], self.repo)

    def test_rejects_main(self):
        result = self.verify("--allow", "README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("protected branch is checked out: main", result.stderr)

    def test_clean_isolated_branch_passes(self):
        self.branch()
        result = self.verify("--allow", "README.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ZENTRA VERIFY: PASS", result.stdout)

    def test_rejects_unknown_base(self):
        self.branch()
        result = run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.repo),
                "--expected-base",
                "0" * 40,
                "--allow",
                "README.md",
            ],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected base is not a local Git object", result.stderr)

    def test_rejects_symbolic_head_as_base(self):
        self.branch()
        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", "HEAD", "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly 40 hexadecimal characters", result.stderr)

    def test_rejects_branch_name_as_base(self):
        self.branch()
        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", "main", "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly 40 hexadecimal characters", result.stderr)

    def test_rejects_tag_as_base(self):
        self.branch()
        run(["git", "tag", "synthetic-base"], self.repo)
        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", "synthetic-base", "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly 40 hexadecimal characters", result.stderr)

    def test_rejects_abbreviated_sha_as_base(self):
        self.branch()
        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", self.base[:12], "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly 40 hexadecimal characters", result.stderr)

    def test_rejects_revision_expression_as_base(self):
        self.branch()
        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", f"{self.base}^{{commit}}", "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly 40 hexadecimal characters", result.stderr)

    def test_rejects_non_ancestor_commit(self):
        run(["git", "switch", "-c", "synthetic-other"], self.repo)
        (self.repo / "other.txt").write_text("other\n", encoding="utf-8")
        run(["git", "add", "other.txt"], self.repo)
        run(["git", "commit", "-m", "other history"], self.repo)
        other = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        run(["git", "switch", "-c", "codex/synthetic-task", self.base], self.repo)

        result = run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--expected-base", other, "--allow", "README.md"],
            self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not an ancestor of HEAD", result.stderr)

    def test_rejects_detached_head(self):
        run(["git", "switch", "--detach", self.base], self.repo)
        result = self.verify("--allow", "README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("detached HEAD", result.stderr)

    def test_dirty_state_requires_explicit_permission(self):
        self.branch()
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        result = self.verify("--allow", "README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("worktree is dirty", result.stderr)

        allowed_result = self.verify("--allow-dirty", "--allow", "README.md")
        self.assertEqual(allowed_result.returncode, 0, allowed_result.stderr)

    def test_rejects_out_of_scope_path(self):
        self.branch()
        (self.repo / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        result = self.verify("--allow-dirty", "--allow", "docs/**")
        self.assertEqual(result.returncode, 1)
        self.assertIn("out-of-scope changed path: unexpected.txt", result.stderr)

    def test_rejects_database_even_when_scope_allows_it(self):
        self.branch()
        database = self.repo / "database"
        database.mkdir()
        (database / "test.db").write_bytes(b"synthetic-not-a-real-database")
        result = self.verify("--allow-dirty", "--allow", "database/**")
        self.assertEqual(result.returncode, 1)
        self.assertIn("database artifact changed: database/test.db", result.stderr)

    def test_rejects_database_variants_at_any_depth_and_case(self):
        self.branch()
        variants = (
            "nested/CATALOG.DB",
            "nested/catalog.db-wal",
            "nested/catalog.DB-SHM",
            "nested/catalog.db-journal",
            "deep/catalog.sqlite",
            "deep/catalog.SQLITE-WAL",
            "deep/catalog.sqlite-shm",
            "deep/catalog.sqlite-journal",
            "other/catalog.sqlite3",
            "other/catalog.SQLITE3-WAL",
            "other/catalog.sqlite3-shm",
            "other/catalog.sqlite3-journal",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"database artifact changed: {path}", result.stderr)
                target.unlink()

    def test_rejects_env_file_even_when_scope_allows_it(self):
        self.branch()
        (self.repo / ".env.local").write_text("SYNTHETIC=true\n", encoding="utf-8")
        result = self.verify("--allow-dirty", "--allow", "*")
        self.assertEqual(result.returncode, 1)
        self.assertIn("secret-like file changed: .env.local", result.stderr)

    def test_rejects_intentional_env_file_forms(self):
        self.branch()
        variants = (
            ".env",
            "config/.ENV.production",
            "config/.env-local",
            "nested/.Env_test",
            "nested/.env.development.local",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("SYNTHETIC=true\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"secret-like file changed: {path}", result.stderr)
                target.unlink()

    def test_allows_non_env_dotfiles_with_similar_prefixes(self):
        self.branch()
        variants = (
            ".environment.md",
            ".envoy.py",
            ".envelope.txt",
            ".envrc",
            ".envbackup",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.write_text("ordinary file\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 0, result.stderr)
                target.unlink()

    def test_rejects_standard_credential_stores_and_credential_files(self):
        self.branch()
        variants = (
            ".git-credentials",
            "home/.AWS/CREDENTIALS",
            "home/.config/gcloud/application_default_credentials.json",
            "config/database_credentials.yaml",
            "config/oauth-credentials.JSON",
            "home/.docker/config.json",
            "home/.KUBE/config",
            "home/.Azure/accessTokens.json",
            "nested/CREDENTIALS",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic-not-a-real-credential\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"secret-like file changed: {path}", result.stderr)
                target.unlink()

    def test_allows_ordinary_credentials_boundary_names(self):
        self.branch()
        variants = (
            "docs/credentials-design.md",
            "src/credentials_parser.py",
            "tests/test_credentials.py",
            "docs/application-default-credentials.md",
            "docs/database_credentials.md",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("ordinary source or documentation\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 0, result.stderr)
                target.unlink()

    def test_rejects_secret_like_variants_by_defined_naming_class(self):
        self.branch()
        variants = (
            "TOKEN.JSON",
            "nested/access-token.txt",
            "nested/refresh_token.YAML",
            "nested/auth_token.json",
            "config/CREDENTIALS.YML",
            "config/service_account.json",
            "config/service-account-credentials.yaml",
            "config/auth_credentials.json",
            "config/authentication-credentials.toml",
            "config/client_secret.ini",
            "config/api-key.txt",
            "keys/id_rsa",
            "keys/ID_DSA",
            "keys/id_ecdsa",
            "keys/ID_ED25519",
            "keys/private-key",
            "keys/server.PPK",
            "keys/signing.KEY",
            "keys/identity.P12",
            "keys/identity.PFX",
            "keys/java.JKS",
            "config/.ENV",
            "config/.Env-Local",
            "nested/Secrets/ordinary-name.md",
            "nested/CREDENTIALS/ordinary_name.py",
            "home/.NETRC",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic-not-a-real-secret\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"secret-like file changed: {path}", result.stderr)
                target.unlink()

    def test_allows_ordinary_token_auth_credential_and_key_names(self):
        self.branch()
        variants = (
            "docs/token-design.md",
            "docs/token-reference.txt.md",
            "src/token_parser.py",
            "tests/test_tokenizer.py",
            "docs/auth-flow.md",
            "src/auth_service.py",
            "tests/test_auth.py",
            "docs/credential-model.md",
            "src/credential_validator.py",
            "tests/test_credentials_validation.py",
            "docs/key-rotation.md",
            "src/key_factory.py",
            "tests/test_api_key_parser.py",
            "docs/service-account-design.md",
            "src/oauth_client.py",
            "docs/monkey-behavior.md",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("ordinary source or documentation\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 0, result.stderr)
                target.unlink()

    def test_rejects_infrastructure_even_when_scope_allows_it(self):
        self.branch()
        workflows = self.repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: synthetic\n", encoding="utf-8")
        result = self.verify("--allow-dirty", "--allow", ".github/**")
        self.assertEqual(result.returncode, 1)
        self.assertIn("deployment/infrastructure file changed: .github/workflows/ci.yml", result.stderr)

    def test_rejects_infrastructure_variants_at_depth_and_case(self):
        self.branch()
        variants = (
            "nested/Render.YAML",
            "deep/VERCEL.JSON",
            "config/PROCFILE",
            "containers/Dockerfile.prod",
            "containers/DOCKERFILE-prod",
            "containers/DOCKER-COMPOSE.prod.YML",
            "containers/compose.override.yaml",
            "project/Infra/config.txt",
            "project/INFRASTRUCTURE/config.txt",
            "project/Deployment/config.txt",
            "project/TERRAFORM/config.txt",
            "nested/.GitHub/Workflows/ci.yml",
            "modules/main.TF",
            "modules/prod.TFVARS",
        )
        for path in variants:
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic\n", encoding="utf-8")
                result = self.verify("--allow-dirty", "--allow", "**")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"deployment/infrastructure file changed: {path}", result.stderr)
                target.unlink()

    def test_detects_staged_change(self):
        self.branch()
        (self.repo / "README.md").write_text("staged\n", encoding="utf-8")
        run(["git", "add", "README.md"], self.repo)
        result = self.verify("--allow-dirty", "--allow", "README.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed=README.md", result.stdout)

    def test_detects_deletion(self):
        self.branch()
        (self.repo / "README.md").unlink()
        result = self.verify("--allow-dirty", "--allow", "README.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed=README.md", result.stdout)

    def test_rename_cannot_hide_protected_source(self):
        self.branch()
        protected = self.repo / "catalog.sqlite3-wal"
        protected.write_bytes(b"synthetic")
        run(["git", "add", "catalog.sqlite3-wal"], self.repo)
        run(["git", "commit", "-m", "synthetic protected fixture"], self.repo)
        self.base = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        run(["git", "mv", "catalog.sqlite3-wal", "catalog.txt"], self.repo)

        result = self.verify("--allow-dirty", "--allow", "*")
        self.assertEqual(result.returncode, 1)
        self.assertIn("database artifact changed: catalog.sqlite3-wal", result.stderr)

    def test_rejects_branch_attached_to_another_worktree(self):
        self.branch()
        with tempfile.TemporaryDirectory() as linked_parent:
            linked = Path(linked_parent) / "linked"
            run(
                ["git", "worktree", "add", "--force", "--force", str(linked), "codex/synthetic-task"],
                self.repo,
            )
            try:
                result = self.verify("--allow", "README.md")
                self.assertEqual(result.returncode, 1)
                self.assertIn("current branch is also attached to another worktree", result.stderr)
            finally:
                run(["git", "worktree", "remove", "--force", str(linked)], self.repo)

    def test_verifier_preserves_refs_index_and_status(self):
        self.branch()
        (self.repo / "README.md").write_text("staged\n", encoding="utf-8")
        run(["git", "add", "README.md"], self.repo)
        (self.repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        index_path = Path(run(["git", "rev-parse", "--git-path", "index"], self.repo).stdout.strip())
        if not index_path.is_absolute():
            index_path = self.repo / index_path
        before = {
            "refs": run(["git", "show-ref"], self.repo).stdout,
            "head": run(["git", "rev-parse", "HEAD"], self.repo).stdout,
            "status": run(["git", "status", "--porcelain=v1"], self.repo).stdout,
            "index": index_path.read_bytes(),
        }

        result = self.verify("--allow-dirty", "--allow", "README.md", "--allow", "untracked.txt")
        self.assertEqual(result.returncode, 0, result.stderr)

        after = {
            "refs": run(["git", "show-ref"], self.repo).stdout,
            "head": run(["git", "rev-parse", "HEAD"], self.repo).stdout,
            "status": run(["git", "status", "--porcelain=v1"], self.repo).stdout,
            "index": index_path.read_bytes(),
        }
        self.assertEqual(after, before)

    def test_detects_committed_changes_since_base(self):
        self.branch()
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "policy.md").write_text("policy\n", encoding="utf-8")
        run(["git", "add", "docs/policy.md"], self.repo)
        run(["git", "commit", "-m", "add policy"], self.repo)

        result = self.verify("--allow", "docs/**")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed=docs/policy.md", result.stdout)

    def test_requires_declared_scope(self):
        self.branch()
        result = self.verify()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no changed-file scope declared", result.stderr)


if __name__ == "__main__":
    unittest.main()
