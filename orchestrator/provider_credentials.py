"""Fail-closed bootstrap for dedicated provider credentials.

This module is the sole composition boundary between infrastructure secret
injection and provider adapters.  It never logs, hashes, persists, serializes,
or places a credential in an exception.  The caller must run it once at the
start of a short-lived provider worker, before any SDK subprocess exists.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any


class ProviderCredentialError(Exception):
    """Credential bootstrap failed without exposing a secret value."""


_BOOTSTRAP_CODEX = "ZENTRA_CODEX_API_KEY"
_BOOTSTRAP_CLAUDE = "ZENTRA_CLAUDE_API_KEY"
_BOOTSTRAP_NAMES = frozenset({_BOOTSTRAP_CODEX, _BOOTSTRAP_CLAUDE})

# The SDKs both start from os.environ.copy().  Reducing the dedicated worker's
# own environment is therefore the only supported way to make their inherited
# base environment an allow-list rather than the user's normal shell.
_TRUSTED_PATH_COMPONENTS = ("/usr/bin", "/bin")
_APPROVED_CANONICAL_SYSTEM_DIRECTORIES = frozenset(
    {Path("/usr/bin"), Path("/bin")}
)
_TRUSTED_WORKER_ENV = {
    "PATH": ":".join(_TRUSTED_PATH_COMPONENTS),
    "LANG": "C",
    "LC_ALL": "C",
}
_REQUIRED_RUNTIME_BINARIES = ("/bin/sh", "/usr/bin/git")
_TRUSTED_TEMP_CANDIDATES = ("/private/tmp", "/tmp")

_CREDENTIAL_LIKE_NAME = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_-]?KEY|"
    r"ACCESS[_-]?KEY|AUTH(?:ORIZATION)?(?:_|$))",
    re.IGNORECASE,
)


def validate_dedicated_key(value: Any, *, provider: str) -> str:
    """Return a usable in-memory key or fail without echoing its value."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ProviderCredentialError(
            f"dedicated {provider} credential is missing or malformed"
        )
    return value


def require_minimized_worker_environment(
    environment: MutableMapping[str, str] | None = None,
) -> None:
    """Refuse before SDK construction unless the worker is already minimal.

    Both pinned SDKs start their child environment from ``os.environ`` and
    then overlay adapter options.  This guard is therefore called by every
    adapter invocation, including direct construction paths: an unsanitized
    worker can never reach either SDK's subprocess boundary.
    """
    env = os.environ if environment is None else environment
    _validate_trusted_runtime_paths()
    if dict(env) != _TRUSTED_WORKER_ENV:
        unexpected = sorted(set(env) - set(_TRUSTED_WORKER_ENV))
        mismatched = sorted(
            name for name in set(env) & set(_TRUSTED_WORKER_ENV)
            if env[name] != _TRUSTED_WORKER_ENV[name]
        )
        raise ProviderCredentialError(
            "provider worker environment is not the infrastructure-owned canonical "
            "environment; unexpected name(s): "
            + (", ".join(unexpected) if unexpected else "none")
            + "; mismatched trusted name(s): "
            + (", ".join(mismatched) if mismatched else "none")
        )


def trusted_worker_environment() -> dict[str, str]:
    """Return a fresh copy of the fixed, non-secret worker environment."""
    _validate_trusted_runtime_paths()
    return dict(_TRUSTED_WORKER_ENV)


def trusted_system_temp_root() -> str:
    """Return a canonical sticky OS temp root, never a parent-env override."""
    for raw_path in _TRUSTED_TEMP_CANDIDATES:
        candidate = Path(raw_path)
        try:
            resolved = candidate.resolve(strict=True)
            mode = resolved.stat().st_mode
        except OSError:
            continue
        if (
            resolved == candidate
            and resolved.is_dir()
            and resolved.stat().st_uid == 0
            and mode & stat.S_ISVTX
            and mode & stat.S_IWOTH
        ):
            return str(resolved)
    raise ProviderCredentialError("no trusted canonical system temporary root is available")


def validate_invocation_temp_directory(raw_path: str | Path) -> Path:
    """Validate one freshly-created, private, invocation-owned directory."""
    candidate = Path(raw_path)
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ProviderCredentialError("invocation temporary directory is unavailable") from exc
    if (
        resolved != candidate
        or resolved.parent != Path(trusted_system_temp_root())
        or not resolved.is_dir()
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProviderCredentialError(
            "invocation temporary directory failed ownership or permission validation"
        )
    return resolved


def _validate_trusted_runtime_paths(
    *,
    path_components: tuple[str, ...] = _TRUSTED_PATH_COMPONENTS,
    required_binaries: tuple[str, ...] = _REQUIRED_RUNTIME_BINARIES,
    approved_directories: frozenset[Path] = _APPROVED_CANONICAL_SYSTEM_DIRECTORIES,
    trusted_uid: int = 0,
) -> None:
    """Validate fixed runtime paths, including narrow merged-/usr mappings.

    The optional arguments exist only so tests can model system layouts in a
    synthetic repository-independent filesystem. Production callers always use
    the root-owned, explicitly approved constants above.

    Directory aliases and executable aliases may each contain at most one
    direct symlink. This intentionally supports ``/bin -> /usr/bin`` followed
    by ``/usr/bin/sh -> dash`` without accepting a generic chain that can leave
    trusted system space and later return to it.
    """
    normalized_approved = frozenset(
        Path(os.path.abspath(directory)) for directory in approved_directories
    )
    canonical_directories: dict[Path, Path] = {}
    for raw_directory in path_components:
        directory = Path(raw_directory)
        try:
            lexical_metadata = directory.lstat()
            parent_metadata = directory.parent.lstat()
        except OSError as exc:
            raise ProviderCredentialError("trusted runtime PATH is unavailable") from exc
        if (
            not directory.is_absolute()
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != trusted_uid
            or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ProviderCredentialError("trusted runtime PATH failed canonical safety validation")

        if stat.S_ISLNK(lexical_metadata.st_mode):
            if lexical_metadata.st_uid != trusted_uid:
                raise ProviderCredentialError(
                    "trusted runtime PATH failed canonical safety validation"
                )
            canonical = _direct_symlink_target(directory)
            if canonical not in canonical_directories.values():
                raise ProviderCredentialError(
                    "trusted runtime PATH alias target was not independently validated"
                )
        else:
            canonical = directory

        try:
            canonical_metadata = canonical.lstat()
        except OSError as exc:
            raise ProviderCredentialError("trusted runtime PATH is unavailable") from exc
        if (
            canonical not in normalized_approved
            or not stat.S_ISDIR(canonical_metadata.st_mode)
            or canonical_metadata.st_uid != trusted_uid
            or canonical_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not os.access(canonical, os.X_OK)
        ):
            raise ProviderCredentialError("trusted runtime PATH failed canonical safety validation")
        canonical_directories[directory] = canonical

    resolved_directories = frozenset(canonical_directories.values())
    for raw_binary in required_binaries:
        binary = Path(raw_binary)
        canonical_parent = canonical_directories.get(binary.parent)
        if not binary.is_absolute() or canonical_parent is None:
            raise ProviderCredentialError("required trusted runtime binary failed validation")
        canonical_source = canonical_parent / binary.name
        try:
            lexical_metadata = canonical_source.lstat()
        except OSError as exc:
            raise ProviderCredentialError("required trusted runtime binary is unavailable") from exc

        if stat.S_ISLNK(lexical_metadata.st_mode):
            if lexical_metadata.st_uid != trusted_uid:
                raise ProviderCredentialError(
                    "required trusted runtime binary failed validation"
                )
            resolved = _direct_symlink_target(canonical_source)
        else:
            resolved = canonical_source

        try:
            metadata = resolved.lstat()
        except OSError as exc:
            raise ProviderCredentialError("required trusted runtime binary is unavailable") from exc
        if (
            resolved.parent not in resolved_directories
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != trusted_uid
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not os.access(resolved, os.X_OK)
        ):
            raise ProviderCredentialError("required trusted runtime binary failed validation")


def _direct_symlink_target(source: Path) -> Path:
    """Return one lexical symlink target without following another symlink."""
    try:
        raw_value = os.readlink(source)
    except OSError as exc:
        raise ProviderCredentialError("trusted runtime symlink is unavailable") from exc
    absolute = raw_value.startswith(os.sep)
    components = raw_value.split(os.sep)
    if absolute:
        components = components[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise ProviderCredentialError("trusted runtime symlink target is not direct")
    raw_target = Path(raw_value)
    if not raw_target.is_absolute():
        raw_target = source.parent / raw_target
    return Path(os.path.abspath(raw_target))


class ProviderCredentials:
    """Non-serializable, redacted holder for two explicit in-memory keys."""

    __slots__ = ("_codex_api_key", "_claude_api_key")

    def __init__(self, *, codex_api_key: str, claude_api_key: str) -> None:
        self._codex_api_key = validate_dedicated_key(
            codex_api_key, provider="Codex"
        )
        self._claude_api_key = validate_dedicated_key(
            claude_api_key, provider="Claude"
        )

    @property
    def codex_api_key(self) -> str:
        return self._codex_api_key

    @property
    def claude_api_key(self) -> str:
        return self._claude_api_key

    def __repr__(self) -> str:
        return "ProviderCredentials(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("ProviderCredentials cannot be serialized")


def load_provider_credentials(
    environment: MutableMapping[str, str] | None = None,
) -> ProviderCredentials:
    """Consume bootstrap secrets once and minimize the worker environment.

    Unexpected credential-like variable *names* are rejected before values are
    read.  On the valid path each bootstrap secret is obtained exactly once by
    ``pop`` and is therefore absent before any adapter or SDK subprocess can be
    constructed.  Every non-allow-listed ambient variable is then removed.
    """
    env = os.environ if environment is None else environment
    unexpected = sorted(
        name
        for name in env
        if name not in _BOOTSTRAP_NAMES and _CREDENTIAL_LIKE_NAME.search(name)
    )
    if unexpected:
        raise ProviderCredentialError(
            "unexpected credential-like ambient variable name(s): "
            + ", ".join(unexpected)
        )

    try:
        codex_key = env.pop(_BOOTSTRAP_CODEX)
        claude_key = env.pop(_BOOTSTRAP_CLAUDE)
    except KeyError as exc:
        raise ProviderCredentialError(
            "both dedicated provider bootstrap credentials are required"
        ) from None

    credentials = ProviderCredentials(
        codex_api_key=codex_key,
        claude_api_key=claude_key,
    )
    env.clear()
    env.update(trusted_worker_environment())
    return credentials


def build_provider_adapters(credentials: ProviderCredentials) -> dict[str, object]:
    """Construct one-shot worker proxies, never in-process provider adapters."""
    if not isinstance(credentials, ProviderCredentials):
        raise ProviderCredentialError("validated ProviderCredentials are required")
    from orchestrator.provider_worker import ProviderWorkerInvoker

    return {
        "codex": ProviderWorkerInvoker(
            provider="codex", api_key=credentials.codex_api_key
        ),
        "claude": ProviderWorkerInvoker(
            provider="claude", api_key=credentials.claude_api_key
        ),
    }
