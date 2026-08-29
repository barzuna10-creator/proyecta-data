"""Jarvis Mission 003B's sole subprocess-capable module.

Resolves a validated Git ref to its current commit SHA via a narrowly-
scoped, read-only ``git rev-parse`` invocation, and (Mission 005) reads
one exact, already-committed blob via an equally narrow, read-only
``git show <sha>:<path>`` invocation. No other Jarvis production module
may import or use ``subprocess`` -- see
``tests/test_jarvis_foundation_boundaries.py``.

Repository-root TOCTOU residual (accepted, V1): construction-time and
immediate pre-invocation path validation materially reduce -- but do not
eliminate -- the risk of a swapped repository directory, because Python's
standard ``subprocess.run(cwd=...)`` accepts a path string, not an
already-open, identity-bound file descriptor (verified directly:
``subprocess.run(cwd=<fd>)`` raises ``TypeError``, there is no fd-based
API to close this gap the way ``jarvis._safe_io`` closes it for regular
files). A narrow race exists between this process's final
``is_symlink()``/``resolve()`` check and the point where the child
process's own ``chdir()`` actually runs. Exploiting it requires an
attacker who already has write access to the parent directory topology of
the user's own configured git checkout -- a threat model in which far
more direct damage is already possible. This is an accepted, documented
V1 residual risk, not a closed guarantee, and it is not compensated for
by granting this module any broader filesystem or subprocess authority
than the single, fixed, narrowly-argued ``git rev-parse`` call below."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

from jarvis.knowledge import RepositoryBinding, validate_repository_binding

_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_DUMMY_SHA = "a" * 40  # syntactically valid placeholder; only the ref half
                        # of validate_repository_binding()'s result is used

_MAX_STDOUT_BYTES = 128
_MAX_STDERR_BYTES = 4096
_MAX_BLOB_BYTES = 262_144  # 256 KiB -- generous for any single text doc on the allow-list, still a hard bound
_TIMEOUT_SECONDS = 2.0
_FIXED_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
}
# A single path component's allowed character set -- deliberately
# independent of jarvis.knowledge's ref regex, since this validates a
# file PATH, not a ref name. A leading "." is permitted (real repo paths
# legitimately start with one, e.g. ".github/workflows/..."); the
# forbidden components ("", ".", "..") are rejected by name below, not by
# forbidding a leading dot outright.
_BLOB_PATH_COMPONENT = re.compile(r"\A[A-Za-z0-9_.\-]{1,255}\Z")


class FreshnessError(Exception):
    code = "FRESHNESS_ERROR"


class FreshnessRefInvalid(FreshnessError): code = "FRESHNESS_REF_INVALID"
class FreshnessRepositoryUnsafe(FreshnessError): code = "FRESHNESS_REPOSITORY_UNSAFE"
class FreshnessGitUnavailable(FreshnessError): code = "FRESHNESS_GIT_UNAVAILABLE"
class FreshnessTimeout(FreshnessError): code = "FRESHNESS_TIMEOUT"
class FreshnessResolutionFailed(FreshnessError): code = "FRESHNESS_RESOLUTION_FAILED"
class FreshnessOutputInvalid(FreshnessError): code = "FRESHNESS_OUTPUT_INVALID"
class FreshnessCommitShaInvalid(FreshnessError): code = "FRESHNESS_COMMIT_SHA_INVALID"
class FreshnessPathInvalid(FreshnessError): code = "FRESHNESS_PATH_INVALID"
class FreshnessBlobNotFound(FreshnessError): code = "FRESHNESS_BLOB_NOT_FOUND"


def _validate_ref(repository_ref: str) -> None:
    """Reuse the single, already-approved, unified ref-validation
    algorithm verbatim -- never a second implementation. The dummy SHA
    below is a fixed, syntactically valid placeholder used only so the
    shared validator's unrelated commit-SHA check never fires; only
    REPOSITORY_REF_INVALID issues are consulted here."""
    issues = validate_repository_binding(RepositoryBinding(repository_ref, _DUMMY_SHA))
    if any(issue.code == "REPOSITORY_REF_INVALID" for issue in issues):
        raise FreshnessRefInvalid(repository_ref)


def _validate_blob_path(path: str) -> None:
    """Structural validation only -- a real allow-list check (exact
    match against a small, explicit, hand-authored set) is the caller's
    responsibility (jarvis.zentra_evidence), never this function's. This
    exists as an independent second layer: even a caller with a bug in
    its own allow-list check cannot use this method to read outside a
    plain, relative, traversal-free path."""
    if not isinstance(path, str) or not 1 <= len(path) <= 4096:
        raise FreshnessPathInvalid(path)
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise FreshnessPathInvalid(path)
    components = path.split("/")
    for component in components:
        if component in ("", ".", ".."):
            raise FreshnessPathInvalid(path)
        if _BLOB_PATH_COMPONENT.fullmatch(component) is None:
            raise FreshnessPathInvalid(path)


def _validate_commit_sha(commit_sha: str) -> None:
    if not isinstance(commit_sha, str) or _COMMIT_SHA.fullmatch(commit_sha) is None:
        raise FreshnessCommitShaInvalid(commit_sha)


class RepositoryFreshnessResolver:
    """Resolves a validated repository_ref to its live commit SHA inside
    exactly one fixed, trusted repository checkout."""

    def __init__(self, repository_root: Path, *, git_executable: Path = Path("/usr/bin/git")):
        self._repository_root = self._validated_root(Path(repository_root))
        self._git_executable = git_executable

    @staticmethod
    def _validated_root(path: Path) -> Path:
        if not path.is_absolute():
            raise FreshnessRepositoryUnsafe("repository_root must be absolute")
        if path.is_symlink():
            raise FreshnessRepositoryUnsafe("repository_root must not be a symlink")
        if not path.is_dir():
            raise FreshnessRepositoryUnsafe("repository_root must be an existing directory")
        if not (path / ".git").exists():
            raise FreshnessRepositoryUnsafe("repository_root must contain a .git entry")
        return path.resolve()

    def resolve_commit(self, repository_ref: str) -> str:
        """Return the lowercase 40-hex commit SHA repository_ref currently
        resolves to, or raise a specific FreshnessError subtype. Performs
        no Git write, no fetch, no network access, and no retry."""
        _validate_ref(repository_ref)

        # Immediate pre-invocation revalidation -- see the module-level
        # TOCTOU residual note. This is a check-then-act reduction of
        # risk, not an elimination of it.
        current = self._validated_root(self._repository_root)
        if current != self._repository_root:
            raise FreshnessRepositoryUnsafe("repository_root changed since construction")

        argv = [
            str(self._git_executable), "rev-parse", "--verify", "--end-of-options",
            f"{repository_ref}^{{commit}}",
        ]
        try:
            result = subprocess.run(
                argv,
                shell=False,
                cwd=str(self._repository_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_TIMEOUT_SECONDS,
                check=False,
                env=dict(_FIXED_ENV),
            )
        except FileNotFoundError as exc:
            raise FreshnessGitUnavailable(str(self._git_executable)) from exc
        except subprocess.TimeoutExpired as exc:
            raise FreshnessTimeout(str(exc)) from exc
        except OSError as exc:
            raise FreshnessGitUnavailable(str(exc)) from exc

        if result.returncode != 0:
            raise FreshnessResolutionFailed(f"exit status {result.returncode}")
        if len(result.stdout) > _MAX_STDOUT_BYTES or len(result.stderr) > _MAX_STDERR_BYTES:
            raise FreshnessOutputInvalid("output exceeded bounded size")
        try:
            decoded = result.stdout.decode("ascii")
        except UnicodeDecodeError as exc:
            raise FreshnessOutputInvalid("stdout was not ASCII") from exc
        match = re.fullmatch(r"([0-9a-f]{40})\n?", decoded)
        if match is None:
            raise FreshnessOutputInvalid("stdout did not match exactly one commit SHA")
        return match.group(1)

    def read_blob(self, commit_sha: str, path: str) -> bytes:
        """Return the exact bytes of the file at `path` as committed at
        `commit_sha`, via `git show <sha>:<path>` -- the git OBJECT
        DATABASE, never the working tree. This is deliberate: an
        untracked or locally-modified file at that path in the working
        tree is invisible here, only what was actually committed at that
        exact SHA is ever returned. Performs no Git write, no fetch, no
        network access, and no retry. Raises a specific FreshnessError
        subtype on any failure; never returns partial or best-effort
        content."""
        _validate_commit_sha(commit_sha)
        _validate_blob_path(path)

        current = self._validated_root(self._repository_root)
        if current != self._repository_root:
            raise FreshnessRepositoryUnsafe("repository_root changed since construction")

        argv = [str(self._git_executable), "show", "--end-of-options", f"{commit_sha}:{path}"]
        try:
            result = subprocess.run(
                argv,
                shell=False,
                cwd=str(self._repository_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_TIMEOUT_SECONDS,
                check=False,
                env=dict(_FIXED_ENV),
            )
        except FileNotFoundError as exc:
            raise FreshnessGitUnavailable(str(self._git_executable)) from exc
        except subprocess.TimeoutExpired as exc:
            raise FreshnessTimeout(str(exc)) from exc
        except OSError as exc:
            raise FreshnessGitUnavailable(str(exc)) from exc

        if result.returncode != 0:
            if len(result.stdout) > _MAX_BLOB_BYTES:
                raise FreshnessOutputInvalid("output exceeded bounded size")
            raise FreshnessBlobNotFound(f"{commit_sha}:{path}")
        if len(result.stdout) > _MAX_BLOB_BYTES or len(result.stderr) > _MAX_STDERR_BYTES:
            raise FreshnessOutputInvalid("output exceeded bounded size")
        return result.stdout
