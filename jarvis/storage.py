"""Non-authoritative, immutable local storage for Jarvis Phase 0 artifacts."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol

from jarvis._safe_io import (
    ArtifactCorrupt, UnsafePath, atomic_create, ensure_private_directory, read_json,
)

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
    try:
        ensure_private_directory(path)
    except UnsafePath as exc:
        raise StoragePathUnsafe(str(exc)) from exc


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
        try:
            atomic_create(path, payload, duplicate_error=duplicate_error)
        except UnsafePath as exc:
            raise StoragePathUnsafe(str(exc)) from exc

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            return read_json(path, not_found_error=DraftNotFound, corrupt_error=StoredArtifactCorrupt)
        except UnsafePath as exc:
            raise StoragePathUnsafe(str(exc)) from exc
        except ArtifactCorrupt as exc:
            raise StoredArtifactCorrupt(str(exc)) from exc

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
