"""Jarvis Mission 003B's sole subprocess-capable module.

Resolves a validated Git ref to its current commit SHA via exactly one
narrowly-scoped, read-only ``git rev-parse`` invocation. No other Jarvis
production module may import or use ``subprocess`` -- see
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
_TIMEOUT_SECONDS = 2.0
_FIXED_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
}


class FreshnessError(Exception):
    code = "FRESHNESS_ERROR"


class FreshnessRefInvalid(FreshnessError): code = "FRESHNESS_REF_INVALID"
class FreshnessRepositoryUnsafe(FreshnessError): code = "FRESHNESS_REPOSITORY_UNSAFE"
class FreshnessGitUnavailable(FreshnessError): code = "FRESHNESS_GIT_UNAVAILABLE"
class FreshnessTimeout(FreshnessError): code = "FRESHNESS_TIMEOUT"
class FreshnessResolutionFailed(FreshnessError): code = "FRESHNESS_RESOLUTION_FAILED"
class FreshnessOutputInvalid(FreshnessError): code = "FRESHNESS_OUTPUT_INVALID"


def _validate_ref(repository_ref: str) -> None:
    """Reuse the single, already-approved, unified ref-validation
    algorithm verbatim -- never a second implementation. The dummy SHA
    below is a fixed, syntactically valid placeholder used only so the
    shared validator's unrelated commit-SHA check never fires; only
    REPOSITORY_REF_INVALID issues are consulted here."""
    issues = validate_repository_binding(RepositoryBinding(repository_ref, _DUMMY_SHA))
    if any(issue.code == "REPOSITORY_REF_INVALID" for issue in issues):
        raise FreshnessRefInvalid(repository_ref)


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
