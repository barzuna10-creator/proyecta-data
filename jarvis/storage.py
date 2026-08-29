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


class ProposalContentMismatch(JarvisStorageError):
    """The same proposal_id was submitted before with different content.

    proposal_id is only ever an idempotency key over a request, never an
    identity -- fail closed rather than silently accept a second, different
    payload under the same key, and never overwrite the first."""
    pass


class AuthorizationEffectMismatch(JarvisStorageError):
    """The same authorization intent_id already has a durably recorded
    effect (mission_id) that disagrees with what the caller is about to
    record -- refuse to trust either value rather than pick one."""
    pass


class _ProposalAlreadyRecorded(JarvisStorageError):
    """Internal-only duplicate-create signal, caught within the same
    method that raises it -- never escapes record_proposal()."""
    pass


class _AuthorizationEffectAlreadyRecorded(JarvisStorageError):
    """Internal-only duplicate-create signal, caught within the same
    method that raises it -- never escapes record_authorization_effect()."""
    pass


def authorization_intent_id(intent: AuthorizationIntent) -> str:
    """The deterministic identity of one authorization intent -- a pure
    function of draft_id/revision/digest_algorithm/digest, nothing else.
    Shared by record_authorization_intent() (below) and
    jarvis.mission_authorization_bridge, which needs to compute this same
    id before it knows whether record_authorization_intent() will report
    it as new or already-recorded."""
    if intent.digest_algorithm != "sha256" or _DIGEST.fullmatch(intent.digest) is None:
        raise ValueError("authorization intent must carry a lowercase SHA-256 digest")
    _validate_draft_id(intent.draft_id)
    _validate_revision(intent.revision)
    identity = f"{intent.draft_id}:{intent.revision}:{intent.digest_algorithm}:{intent.digest}"
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


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
        self._proposals = self.root / "proposals"
        self._effects = self.root / "authorization-effects"
        self._authorized = self.root / "authorized-drafts"
        for directory in (self._drafts, self._intents, self._proposals, self._effects, self._authorized):
            if directory.exists() and directory.is_symlink():
                raise StoragePathUnsafe(f"storage directory must not be a symlink: {directory}")
            # exist_ok=True: FileJarvisStore must be re-openable against an
            # already-populated root -- this is exactly what happens on
            # every Control Plane server restart, not just first creation.
            directory.mkdir(mode=0o700, exist_ok=True)
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
        intent_id = authorization_intent_id(intent)
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

    def record_proposal(self, proposal_id: str, content_digest: str, draft_id: str) -> str:
        """proposal_id is a durable idempotency key over a request, never
        an identity: the caller-supplied value is only ever compared
        against what -- if anything -- was already recorded for it.

        Same proposal_id + same content_digest, any number of times,
        any number of process restarts in between: returns the same
        draft_id every time, writes nothing new after the first call.
        Same proposal_id + a different content_digest: raises
        ProposalContentMismatch, never overwrites the first record --
        the caller (jarvis.control_plane_server) turns this into a 409,
        and no draft is created or mutated as a result of the mismatched
        call."""
        _validate_draft_id(proposal_id)
        _validate_draft_id(draft_id)
        if _DIGEST.fullmatch(content_digest) is None:
            raise ValueError("content_digest must be a lowercase SHA-256 hex digest")
        payload = {
            "proposal_id": proposal_id,
            "content_digest": content_digest,
            "draft_id": draft_id,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path = self._proposals / f"{proposal_id}.json"
        try:
            self._atomic_create(path, rendered, duplicate_error=_ProposalAlreadyRecorded)
        except _ProposalAlreadyRecorded:
            existing = self._read_json(path)
            if existing.get("content_digest") != content_digest:
                raise ProposalContentMismatch(proposal_id) from None
            return existing["draft_id"]
        return draft_id

    def get_proposal(self, proposal_id: str) -> dict | None:
        _validate_draft_id(proposal_id)
        path = self._proposals / f"{proposal_id}.json"
        try:
            return self._read_json(path)
        except DraftNotFound:
            return None

    def record_authorization_effect(self, intent_id: str, mission_id: str) -> str:
        """intent_id -> mission_id, recorded exactly once per intent_id,
        ever. The presence of this record -- not any in-memory state -- is
        what jarvis.mission_authorization_bridge trusts to know a draft
        authorization has already produced a Mission Record, including
        across a crash between chugel.create_mission() succeeding and this
        call ever being reached."""
        if _DIGEST.fullmatch(intent_id) is None:
            raise ValueError("intent_id must be a lowercase SHA-256 hex digest")
        _validate_draft_id(mission_id)
        payload = {
            "authorization_intent_id": intent_id,
            "mission_id": mission_id,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path = self._effects / f"{intent_id}.json"
        try:
            self._atomic_create(path, rendered, duplicate_error=_AuthorizationEffectAlreadyRecorded)
        except _AuthorizationEffectAlreadyRecorded:
            existing = self._read_json(path)
            if existing.get("mission_id") != mission_id:
                raise AuthorizationEffectMismatch(intent_id) from None
            return existing["mission_id"]
        return mission_id

    def get_authorization_effect(self, intent_id: str) -> str | None:
        if _DIGEST.fullmatch(intent_id) is None:
            raise ValueError("intent_id must be a lowercase SHA-256 hex digest")
        path = self._effects / f"{intent_id}.json"
        try:
            value = self._read_json(path)
        except DraftNotFound:
            return None
        return value["mission_id"]

    def mark_draft_authorized(self, draft_id: str) -> None:
        """Idempotent: recorded at most once per draft_id, safe to call
        again on a retry. Used only to keep already-authorized drafts out
        of the projection's pending-gates list -- never consulted for any
        authorization decision itself (that is exclusively
        get_authorization_effect(), keyed by intent_id)."""
        _validate_draft_id(draft_id)
        payload = {
            "draft_id": draft_id,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            self._atomic_create(self._authorized / f"{draft_id}.json", rendered, duplicate_error=DraftAlreadyExists)
        except DraftAlreadyExists:
            pass

    def list_draft_ids(self) -> tuple[str, ...]:
        drafts = []
        for path in self._drafts.iterdir():
            if path.is_symlink():
                raise StoragePathUnsafe(f"refusing symlink in drafts root: {path}")
            if path.is_dir() and _DRAFT_ID.fullmatch(path.name):
                drafts.append(path.name)
        return tuple(sorted(drafts))

    def list_pending_draft_ids(self) -> tuple[str, ...]:
        authorized = set()
        for path in self._authorized.iterdir():
            if path.is_symlink():
                raise StoragePathUnsafe(f"refusing symlink in authorized-drafts root: {path}")
            if path.is_file():
                match = re.fullmatch(r"([0-9a-f-]{36})\.json", path.name)
                if match:
                    authorized.add(match.group(1))
        return tuple(draft_id for draft_id in self.list_draft_ids() if draft_id not in authorized)
