"""Shared fail-closed filesystem primitives for non-authoritative Jarvis stores."""

from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Iterator, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by patched tests
    fcntl = None  # type: ignore[assignment]

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
MAX_JSON_BYTES = 1_048_576


class SafeIOError(Exception):
    pass


class UnsafePath(SafeIOError):
    pass


class ArtifactCorrupt(SafeIOError):
    pass


class LockUnavailable(SafeIOError):
    pass


class LockReentryError(SafeIOError):
    pass


def ensure_private_directory(path: Path, *, parents: bool = False) -> Path:
    path = Path(path)
    if not O_NOFOLLOW or not O_DIRECTORY or not hasattr(os, "fchmod"):
        raise UnsafePath("safe directory validation is unavailable")
    if path.exists() and path.is_symlink():
        raise UnsafePath("directory must not be a symlink")
    path.mkdir(parents=parents, exist_ok=True, mode=0o700)
    try:
        fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW | O_DIRECTORY)
    except OSError as exc:
        raise UnsafePath("could not safely open directory") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise UnsafePath("unsafe directory inode")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return path.resolve()


def fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW | O_DIRECTORY)
    except OSError as exc:
        raise UnsafePath("could not safely open directory for fsync") from exc
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_create(path: Path, payload: bytes, *, duplicate_error: type[Exception]) -> None:
    path = Path(path)
    ensure_private_directory(path.parent)
    if path.is_symlink():
        raise UnsafePath("refusing symlink target")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise duplicate_error(str(path)) from exc
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_bytes(path: Path, *, not_found_error: type[Exception], max_bytes: int = MAX_JSON_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink():
        raise UnsafePath("refusing symlink file")
    try:
        fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise not_found_error(str(path)) from exc
    except OSError as exc:
        raise UnsafePath("could not safely open file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise UnsafePath("unsafe file inode")
        if info.st_size > max_bytes:
            raise ArtifactCorrupt("stored artifact exceeds size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise ArtifactCorrupt("stored artifact exceeds size limit")
        return payload
    finally:
        os.close(fd)


def read_json(path: Path, *, not_found_error: type[Exception], corrupt_error: type[Exception]) -> dict:
    try:
        value = json.loads(read_bytes(path, not_found_error=not_found_error).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ArtifactCorrupt) as exc:
        raise corrupt_error("invalid stored JSON") from exc
    if not isinstance(value, dict):
        raise corrupt_error("stored artifact is not an object")
    return value


_registry_lock = threading.Lock()
_held: set[tuple[int, str, str]] = set()


def _canonical_lock_id(entity_id: str) -> str:
    import re
    if not isinstance(entity_id, str) or re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", entity_id
    ) is None:
        raise ValueError("entity_id must be a canonical lowercase UUID")
    return entity_id


@contextlib.contextmanager
def exclusive_entity_locks(root: Path, entity_ids: Sequence[str]) -> Iterator[None]:
    if fcntl is None or not O_NOFOLLOW:
        raise LockUnavailable("POSIX flock and O_NOFOLLOW are required")
    identifiers = sorted({_canonical_lock_id(item) for item in entity_ids})
    if not identifiers:
        raise ValueError("at least one entity lock is required")
    trusted_root = ensure_private_directory(Path(root), parents=True)
    lock_root = ensure_private_directory(trusted_root / ".locks")
    keys = {(os.getpid(), str(trusted_root), item) for item in identifiers}
    with _registry_lock:
        if _held.intersection(keys):
            raise LockReentryError("entity lock is not reentrant")
        _held.update(keys)
    descriptors: list[int] = []
    try:
        parent_fd = os.open(str(lock_root), os.O_RDONLY | O_NOFOLLOW | O_DIRECTORY)
        try:
            for identifier in identifiers:
                name = f"{identifier}.lock"
                try:
                    fd = os.open(name, os.O_CREAT | os.O_RDWR | O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                except OSError as exc:
                    raise LockUnavailable("could not safely open entity lock") from exc
                try:
                    info = os.fstat(fd)
                    linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid()
                        or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
                    ):
                        raise LockUnavailable("unsafe entity lock inode")
                    os.fchmod(fd, 0o600)
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    descriptors.append(fd)
                except BaseException:
                    os.close(fd)
                    raise
        finally:
            os.close(parent_fd)
        yield
    finally:
        for fd in reversed(descriptors):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        with _registry_lock:
            _held.difference_update(keys)


@contextlib.contextmanager
def exclusive_entity_lock(root: Path, entity_id: str) -> Iterator[None]:
    with exclusive_entity_locks(root, (entity_id,)):
        yield
