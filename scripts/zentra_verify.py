#!/usr/bin/env python3
"""Read-only safety checks for a Zentra Builder worktree.

The tool only invokes read-only Git commands. It never writes files, updates the
index, changes refs, checks out branches, pushes, merges, deploys, or accesses a
database or secret contents.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


PROTECTED_BRANCHES = {"main"}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk", ".jks", ".keystore")
SECRET_NAMES = {
    ".env",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".dockercfg",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "private-key",
    "private_key",
    "credential",
    "credentials",
}
SECRET_DIRECTORIES = {"secrets", "credentials"}
SECRET_CONFIG_EXTENSIONS = {"json", "yaml", "yml", "toml", "ini", "conf", "config", "txt"}
SECRET_CONFIG_STEMS = {
    "token",
    "access-token",
    "refresh-token",
    "auth-token",
    "authentication-token",
    "bearer-token",
    "oauth-token",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "service-account",
    "service-account-credential",
    "service-account-credentials",
    "auth",
    "authentication",
    "auth-credential",
    "auth-credentials",
    "authentication-credential",
    "authentication-credentials",
    "client-secret",
    "client-secrets",
    "api-key",
    "api-keys",
    "application-default-credentials",
}
STANDARD_CREDENTIAL_PATH_SUFFIXES = {
    (".aws", "credentials"),
    (".docker", "config.json"),
    (".kube", "config"),
    (".azure", "accesstokens.json"),
}
ENV_FILE = re.compile(r"^\.env(?:$|[._-].+)$", re.IGNORECASE)
INFRA_EXACT = {"render.yaml", "vercel.json", "procfile"}
INFRA_DIRECTORIES = {"infrastructure", "infra", "deploy", "deployment", "terraform"}
FULL_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}$")
DATABASE_ARTIFACT = re.compile(r"\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm|journal))?$", re.IGNORECASE)


class GitError(RuntimeError):
    """A read-only Git query could not be completed."""


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    # Prevent optional index refreshes/locks from otherwise read-only Git
    # queries such as `status`. This makes the verifier's Git access explicitly
    # non-mutating, including when another process is using the repository.
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result


def nul_paths(repo: Path, *args: str) -> set[str]:
    output = git(repo, *args).stdout
    return {item for item in output.split("\0") if item}


def changed_paths(repo: Path, base: str, head: str) -> set[str]:
    """Return committed, staged, unstaged, and untracked changed paths."""
    paths = set()
    # Disable rename detection so both sides of a rename are checked. A rename
    # must not hide deletion of a protected source behind a harmless target.
    paths.update(nul_paths(repo, "diff", "--no-renames", "--name-only", "-z", f"{base}..{head}"))
    paths.update(nul_paths(repo, "diff", "--no-renames", "--cached", "--name-only", "-z"))
    paths.update(nul_paths(repo, "diff", "--no-renames", "--name-only", "-z"))
    paths.update(nul_paths(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    return {PurePosixPath(path).as_posix() for path in paths}


def is_secret_like(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix().lower()
    pure = PurePosixPath(normalized)
    name = pure.name
    if name in SECRET_NAMES or ENV_FILE.fullmatch(name) is not None:
        return True
    if name.endswith(SECRET_SUFFIXES):
        return True
    if any(part in SECRET_DIRECTORIES for part in pure.parts[:-1]):
        return True
    if any(
        pure.parts[-len(suffix) :] == suffix
        for suffix in STANDARD_CREDENTIAL_PATH_SUFFIXES
        if len(pure.parts) >= len(suffix)
    ):
        return True

    stem, separator, extension = name.rpartition(".")
    if not separator or extension not in SECRET_CONFIG_EXTENSIONS:
        return False
    # Normalize only conventional word separators. Matching an explicit stem
    # plus a controlled config/data extension avoids treating ordinary names
    # such as `token-design.md` or `auth_service.py` as credentials.
    normalized_stem = re.sub(r"[-_]+", "-", stem).strip("-")
    return normalized_stem in SECRET_CONFIG_STEMS or normalized_stem.endswith("-credentials")


def is_database_artifact(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix().lower()
    parts = PurePosixPath(normalized).parts
    return (
        normalized == "database/proyecta.db"
        or "respaldos" in parts
        or DATABASE_ARTIFACT.search(normalized) is not None
    )


def is_infrastructure(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix().lower()
    pure = PurePosixPath(normalized)
    name = pure.name
    parts = pure.parts
    compose_name = re.match(r"^(?:docker-)?compose(?:[._-].+)?\.ya?ml$", name)
    return (
        name in INFRA_EXACT
        or re.match(r"^dockerfile(?:[._-].+)?$", name) is not None
        or compose_name is not None
        or name.endswith((".tf", ".tfvars"))
        or any(part in INFRA_DIRECTORIES for part in parts[:-1])
        or any(parts[index : index + 2] == (".github", "workflows") for index in range(len(parts) - 1))
    )


def worktree_entries(repo: Path) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` without changing worktree state."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    output = git(repo, "worktree", "list", "--porcelain").stdout
    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def exact_commit(repo: Path, supplied: str) -> tuple[str | None, str | None]:
    """Validate a literal, full object ID naming a commit—never a revision."""
    if FULL_OBJECT_ID.fullmatch(supplied) is None:
        return None, "expected base must be exactly 40 hexadecimal characters"

    object_type = git(repo, "cat-file", "-t", supplied, check=False)
    if object_type.returncode != 0:
        return None, f"expected base is not a local Git object: {supplied}"
    if object_type.stdout.strip() != "commit":
        return None, f"expected base is not a commit object: {supplied}"

    resolved = git(repo, "rev-parse", "--verify", supplied).stdout.strip()
    if resolved.lower() != supplied.lower():
        return None, f"expected base does not exactly identify resolved commit {resolved}"
    return resolved, None


def allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="worktree to inspect (default: current directory)",
    )
    result.add_argument(
        "--expected-base",
        required=True,
        help="exact 40-character hexadecimal commit object ID authorized as the task base",
    )
    result.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="allowed changed path glob; repeat for each scope entry",
    )
    result.add_argument(
        "--allow-dirty",
        action="store_true",
        help="report but do not fail on expected uncommitted task changes",
    )
    return result


def verify(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    repo = args.repo.resolve()
    errors: list[str] = []
    notes: list[str] = []

    if not repo.is_dir():
        raise GitError(f"repository path is not a directory: {repo}")

    inside = git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip()
    if inside != "true":
        raise GitError(f"not a Git worktree: {repo}")

    top_level = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    branch = git(repo, "branch", "--show-current").stdout.strip()
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    resolved_base, base_error = exact_commit(repo, args.expected_base)
    if base_error:
        errors.append(base_error)
        base = args.expected_base
        paths: set[str] = set()
    else:
        assert resolved_base is not None
        base = resolved_base
        ancestry = git(repo, "merge-base", "--is-ancestor", base, head, check=False)
        if ancestry.returncode != 0:
            errors.append(f"expected base {base} is not an ancestor of HEAD {head}")
        paths = changed_paths(repo, base, head)

    if not branch:
        errors.append("detached HEAD: an isolated task branch is required")
    elif branch in PROTECTED_BRANCHES:
        errors.append(f"protected branch is checked out: {branch}")
    else:
        branch_ref = f"refs/heads/{branch}"
        attached_elsewhere = []
        for entry in worktree_entries(repo):
            entry_path = Path(entry.get("worktree", "")).resolve()
            if entry.get("branch") == branch_ref and entry_path != top_level:
                attached_elsewhere.append(str(entry_path))
        for path in attached_elsewhere:
            errors.append(f"current branch is also attached to another worktree: {path}")

    status = git(repo, "status", "--porcelain=v1").stdout
    dirty = bool(status)
    if dirty and not args.allow_dirty:
        errors.append("worktree is dirty; pass --allow-dirty only for expected task changes")
    notes.append(f"branch={branch or '(detached)'}")
    notes.append(f"base={base}")
    notes.append(f"head={head}")
    notes.append(f"dirty={'yes' if dirty else 'no'}")

    if not args.allow:
        errors.append("no changed-file scope declared; provide at least one --allow glob")

    for path in sorted(paths):
        if not allowed(path, args.allow):
            errors.append(f"out-of-scope changed path: {path}")
        if is_database_artifact(path):
            errors.append(f"database artifact changed: {path}")
        if is_secret_like(path):
            errors.append(f"secret-like file changed: {path}")
        if is_infrastructure(path):
            errors.append(f"deployment/infrastructure file changed: {path}")

    notes.append(f"changed_paths={len(paths)}")
    notes.extend(f"changed={path}" for path in sorted(paths))
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        errors, notes = verify(args)
    except GitError as error:
        print(f"ZENTRA VERIFY ERROR: {error}", file=sys.stderr)
        return 2

    for note in notes:
        print(note)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(f"ZENTRA VERIFY: FAIL ({len(errors)} violation(s))", file=sys.stderr)
        return 1

    print("ZENTRA VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
