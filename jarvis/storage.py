"""Non-authoritative, immutable local storage for Jarvis Phase 0 artifacts."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Protocol

from jarvis.drafts import build_draft_envelope
from jarvis.models import (
    AuthorizationIntent,
    DraftEnvelope,
    envelope_to_dict,
    mission_draft_from_dict,
)

_DRAFT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class JarvisStorageError(Exception):
    pass


class DraftNotFound(JarvisStorageError):
    pass


class DraftAlreadyExists(JarvisStorageError):
    pass


class StoredArtifactCorrupt(JarvisStorageError):
    pass


class StoragePathUnsafe(JarvisStorageError):
    pass


class AuthorizationIntentAlreadyRecorded(JarvisStorageError):
    pass


class JarvisStore(Protocol):
    def save_draft(self, envelope: DraftEnvelope) -> None: ...
    def get_draft(self, draft_id: str, revision: int) -> DraftEnvelope: ...
    def get_latest_draft(self, draft_id: str) -> DraftEnvelope: ...
    def list_draft_revisions(self, draft_id: str) -> tuple[int, ...]: ...
    def record_authorization_intent(self, intent: AuthorizationIntent) -> str: ...


def _validate_draft_id(draft_id: str) -> None:
    if not isinstance(draft_id, str) or _DRAFT_ID.fullmatch(draft_id) is None:
        raise ValueError("draft_id must be a canonical lowercase UUID")


def _validate_revision(revision: int) -> None:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be a positive integer")


def _validate_and_chmod_directory(path: Path) -> None:
    """Validate and chmod one directory through the same no-follow fd.

    A path-level is_symlink/is_dir check followed by os.chmod(path) has a
    TOCTOU window: an attacker can replace the directory with a symlink after
    validation and chmod will follow it. On the supported POSIX storage path,
    require O_NOFOLLOW and O_DIRECTORY, validate the opened inode with fstat,
    and mutate permissions only through that descriptor. Platforms unable to
    provide this identity-preserving primitive fail closed.
    """
    if not _O_NOFOLLOW or not _O_DIRECTORY or not hasattr(os, "fchmod"):
        raise StoragePathUnsafe(
            "safe directory validation requires O_NOFOLLOW, O_DIRECTORY, and fchmod"
        )
    try:
        descriptor = os.open(str(path), os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY)
    except OSError as exc:
        raise StoragePathUnsafe(f"could not safely open directory {path}: {exc}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise StoragePathUnsafe(f"expected directory inode: {path}")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


class FileJarvisStore:
    """Storage outside Chugel; callers must supply the explicit root."""

    def __init__(self, root: Path):
        supplied = Path(root)
        if supplied.exists() and supplied.is_symlink():
            raise StoragePathUnsafe("storage root must not be a symlink")
        supplied.mkdir(parents=True, exist_ok=True, mode=0o700)
        _validate_and_chmod_directory(supplied)
        self.root = supplied.resolve()
        self._drafts = self.root / "drafts"
        self._intents = self.root / "authorization-intents"
        for directory in (self._drafts, self._intents):
            if directory.exists() and directory.is_symlink():
                raise StoragePathUnsafe(f"storage directory must not be a symlink: {directory}")
            directory.mkdir(mode=0o700)
            _validate_and_chmod_directory(directory)

    def _draft_directory(self, draft_id: str, *, create: bool) -> Path:
        _validate_draft_id(draft_id)
        directory = self._drafts / draft_id
        if directory.exists() and directory.is_symlink():
            raise StoragePathUnsafe("draft directory must not be a symlink")
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.exists():
            raise DraftNotFound(draft_id)
        _validate_and_chmod_directory(directory)
        return directory

    def _draft_path(self, draft_id: str, revision: int, *, create_dir: bool) -> Path:
        _validate_revision(revision)
        return self._draft_directory(draft_id, create=create_dir) / f"{revision:08d}.json"

    @staticmethod
    def _atomic_create(path: Path, payload: bytes, *, duplicate_error: type[Exception]) -> None:
        if path.is_symlink():
            raise StoragePathUnsafe(f"refusing symlink target: {path}")
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
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> dict:
        if path.is_symlink():
            raise StoragePathUnsafe(f"refusing symlink file: {path}")
        try:
            fd = os.open(str(path), os.O_RDONLY | _O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise DraftNotFound(str(path)) from exc
        except OSError as exc:
            raise StoragePathUnsafe(f"could not safely open {path}: {exc}") from exc
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoredArtifactCorrupt(f"invalid stored JSON at {path}") from exc
        if not isinstance(value, dict):
            raise StoredArtifactCorrupt(f"stored artifact at {path} is not an object")
        return value

    def save_draft(self, envelope: DraftEnvelope) -> None:
        verified = build_draft_envelope(envelope.draft)
        if envelope.digest_algorithm != "sha256" or envelope.digest != verified.digest:
            raise StoredArtifactCorrupt("refusing to store an envelope with an invalid digest")
        path = self._draft_path(envelope.draft.draft_id, envelope.draft.revision, create_dir=True)
        payload = json.dumps(
            envelope_to_dict(envelope), ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        self._atomic_create(path, payload, duplicate_error=DraftAlreadyExists)

    def get_draft(self, draft_id: str, revision: int) -> DraftEnvelope:
        path = self._draft_path(draft_id, revision, create_dir=False)
        value = self._read_json(path)
        try:
            draft = mission_draft_from_dict(value["draft"])
            algorithm = value["digest_algorithm"]
            digest = value["digest"]
        except (KeyError, TypeError, ValueError) as exc:
            raise StoredArtifactCorrupt(f"malformed draft envelope at {path}") from exc
        verified = build_draft_envelope(draft)
        if algorithm != "sha256" or not isinstance(digest, str) or digest != verified.digest:
            raise StoredArtifactCorrupt(f"draft envelope digest mismatch at {path}")
        return DraftEnvelope(draft=draft, digest_algorithm="sha256", digest=digest)

    def list_draft_revisions(self, draft_id: str) -> tuple[int, ...]:
        directory = self._draft_directory(draft_id, create=False)
        revisions: list[int] = []
        for path in directory.iterdir():
            if path.is_symlink():
                raise StoragePathUnsafe(f"refusing symlink in draft directory: {path}")
            match = re.fullmatch(r"([0-9]{8})\.json", path.name)
            if match and path.is_file():
                revision = int(match.group(1))
                if revision >= 1:
                    revisions.append(revision)
        return tuple(sorted(revisions))

    def get_latest_draft(self, draft_id: str) -> DraftEnvelope:
        revisions = self.list_draft_revisions(draft_id)
        if not revisions:
            raise DraftNotFound(draft_id)
        return self.get_draft(draft_id, revisions[-1])

    def record_authorization_intent(self, intent: AuthorizationIntent) -> str:
        _validate_draft_id(intent.draft_id)
        _validate_revision(intent.revision)
        if intent.digest_algorithm != "sha256" or _DIGEST.fullmatch(intent.digest) is None:
            raise ValueError("authorization intent must carry a lowercase SHA-256 digest")
        identity = (
            f"{intent.draft_id}:{intent.revision}:{intent.digest_algorithm}:{intent.digest}"
        )
        intent_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
        payload = {
            "authorization_intent_id": intent_id,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "intent": {
                "draft_id": intent.draft_id,
                "revision": intent.revision,
                "digest_algorithm": intent.digest_algorithm,
                "digest": intent.digest,
            },
            "effect": "none_phase_0",
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._atomic_create(
            self._intents / f"{intent_id}.json",
            rendered,
            duplicate_error=AuthorizationIntentAlreadyRecorded,
        )
        return intent_id
