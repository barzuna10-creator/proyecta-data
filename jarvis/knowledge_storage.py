"""Immutable storage and crash-atomic promotion bundles for Jarvis knowledge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from jarvis._safe_io import (
    ArtifactCorrupt, UnsafePath, atomic_create, ensure_private_directory,
    exclusive_entity_lock, exclusive_entity_locks, fsync_directory, read_bytes, read_json,
)
from jarvis.knowledge import (
    EmmaKnowledgeReview, KnowledgeAuthorizationIntent, KnowledgeCandidateEnvelope,
    KnowledgeEntry, build_candidate_envelope, candidate_content_from_dict,
    candidate_content_to_dict, knowledge_entry_from_dict, knowledge_entry_to_dict,
    require_candidate_transition, require_entry_transition, validate_repository_binding,
)
from jarvis.knowledge_authorization import require_exact_authorities

_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")


class KnowledgeStorageError(Exception):
    code = "KNOWLEDGE_STORAGE_ERROR"


class KnowledgeNotFound(KnowledgeStorageError): code = "KNOWLEDGE_NOT_FOUND"
class KnowledgeAlreadyExists(KnowledgeStorageError): code = "KNOWLEDGE_ALREADY_EXISTS"
class KnowledgeCorrupt(KnowledgeStorageError): code = "KNOWLEDGE_CORRUPT"
class KnowledgePathUnsafe(KnowledgeStorageError): code = "KNOWLEDGE_PATH_UNSAFE"
class KnowledgeTargetStateChanged(KnowledgeStorageError): code = "KNOWLEDGE_TARGET_STATE_CHANGED"
class KnowledgeTargetRevisionChanged(KnowledgeStorageError): code = "KNOWLEDGE_TARGET_REVISION_CHANGED"
class KnowledgePromotionRecoveryConflict(KnowledgeStorageError): code = "KNOWLEDGE_PROMOTION_RECOVERY_CONFLICT"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def promotion_id(candidate_id: str, revision: int, content_digest: str) -> str:
    identity = f"jarvis-knowledge-promotion-v1\0{candidate_id}\0{revision}\0{content_digest}"
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


class FileKnowledgeStore:
    """Explicit-root, append-only, non-authoritative knowledge store."""

    def __init__(self, root: Path):
        try:
            self.root = ensure_private_directory(Path(root), parents=True)
            self.candidates = ensure_private_directory(self.root / "candidates")
            self.events = ensure_private_directory(self.root / "candidate-events")
            self.reviews = ensure_private_directory(self.root / "reviews")
            self.authorizations = ensure_private_directory(self.root / "authorization-intents")
            self.entries = ensure_private_directory(self.root / "entries")
            self.entry_events = ensure_private_directory(self.root / "entry-events")
            self.promotions = ensure_private_directory(self.root / "promotions")
        except UnsafePath as exc:
            raise KnowledgePathUnsafe(str(exc)) from exc

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or _UUID.fullmatch(value) is None:
            raise ValueError("identifier must be a canonical lowercase UUID")
        return value

    def _entity_dir(self, parent: Path, entity_id: str, *, create: bool) -> Path:
        path = parent / self._id(entity_id)
        if create:
            try: return ensure_private_directory(path)
            except UnsafePath as exc: raise KnowledgePathUnsafe(str(exc)) from exc
        if not path.exists(): raise KnowledgeNotFound(entity_id)
        try: return ensure_private_directory(path)
        except UnsafePath as exc: raise KnowledgePathUnsafe(str(exc)) from exc

    def save_candidate(self, envelope: KnowledgeCandidateEnvelope) -> None:
        verified = build_candidate_envelope(envelope.content)
        if envelope != verified: raise KnowledgeCorrupt("candidate digest mismatch")
        with exclusive_entity_lock(self.root, envelope.content.candidate_id):
            directory = self._entity_dir(self.candidates, envelope.content.candidate_id, create=True)
            payload = _canonical({"content": candidate_content_to_dict(envelope.content), "digest_algorithm": "sha256", "content_digest": envelope.content_digest})
            try: atomic_create(directory / f"{envelope.content.revision:08d}.json", payload, duplicate_error=KnowledgeAlreadyExists)
            except UnsafePath as exc: raise KnowledgePathUnsafe(str(exc)) from exc
            self._append_normal_event(envelope, None, "draft")

    def _candidate_revisions(self, candidate_id: str) -> tuple[int, ...]:
        directory = self._entity_dir(self.candidates, candidate_id, create=False)
        values = []
        for path in directory.iterdir():
            if path.is_symlink(): raise KnowledgePathUnsafe("unsafe candidate entry")
            match = re.fullmatch(r"([0-9]{8})\.json", path.name)
            if match and path.is_file() and int(match.group(1)) > 0: values.append(int(match.group(1)))
        return tuple(sorted(values))

    def get_candidate(self, candidate_id: str, revision: int) -> KnowledgeCandidateEnvelope:
        path = self._entity_dir(self.candidates, candidate_id, create=False) / f"{revision:08d}.json"
        try: value = read_json(path, not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        except UnsafePath as exc: raise KnowledgePathUnsafe(str(exc)) from exc
        try:
            envelope = build_candidate_envelope(candidate_content_from_dict(value["content"]))
            if value["digest_algorithm"] != "sha256" or value["content_digest"] != envelope.content_digest: raise ValueError
        except (KeyError, TypeError, ValueError) as exc: raise KnowledgeCorrupt("malformed candidate") from exc
        return envelope

    def get_latest_candidate(self, candidate_id: str) -> KnowledgeCandidateEnvelope:
        revisions = self._candidate_revisions(candidate_id)
        if not revisions: raise KnowledgeNotFound(candidate_id)
        return self.get_candidate(candidate_id, revisions[-1])

    def _event_dir(self, candidate_id: str, *, create: bool) -> Path:
        return self._entity_dir(self.events, candidate_id, create=create)

    def _normal_events(self, candidate_id: str) -> list[dict[str, Any]]:
        try: directory = self._event_dir(candidate_id, create=False)
        except KnowledgeNotFound: return []
        result = []
        for path in sorted(directory.iterdir()):
            if not re.fullmatch(r"[0-9]{8}\.json", path.name):
                if path.is_symlink(): raise KnowledgePathUnsafe("unsafe event entry")
                continue
            try: result.append(read_json(path, not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt))
            except UnsafePath as exc: raise KnowledgePathUnsafe(str(exc)) from exc
        previous_digest = None
        previous_status = None
        for sequence, event in enumerate(result, 1):
            core = {key: event[key] for key in ("sequence", "candidate_id", "revision", "content_digest", "previous_status", "status", "previous_event_digest")}
            if event.get("sequence") != sequence or event.get("previous_status") != previous_status or event.get("previous_event_digest") != previous_digest or event.get("event_digest") != hashlib.sha256(_canonical(core)).hexdigest():
                raise KnowledgeCorrupt("candidate event chain invalid")
            previous_status, previous_digest = event["status"], event["event_digest"]
        return result

    def _append_normal_event(self, envelope: KnowledgeCandidateEnvelope, previous: str | None, status: str) -> None:
        events = self._normal_events(envelope.content.candidate_id)
        if (events[-1]["status"] if events else None) != previous: raise KnowledgeCorrupt("candidate state changed")
        require_candidate_transition(previous, status, label=envelope.content.label)
        sequence = len(events) + 1
        prior_digest = events[-1]["event_digest"] if events else None
        core = {"sequence": sequence, "candidate_id": envelope.content.candidate_id, "revision": envelope.content.revision, "content_digest": envelope.content_digest, "previous_status": previous, "status": status, "previous_event_digest": prior_digest}
        event = {**core, "event_digest": hashlib.sha256(_canonical(core)).hexdigest()}
        directory = self._event_dir(envelope.content.candidate_id, create=True)
        atomic_create(directory / f"{sequence:08d}.json", _canonical(event), duplicate_error=KnowledgeAlreadyExists)

    def transition_candidate(self, candidate_id: str, status: str) -> None:
        with exclusive_entity_lock(self.root, self._id(candidate_id)):
            envelope = self.get_latest_candidate(candidate_id)
            current = self.get_candidate_status(candidate_id, include_promotion=False)
            self._append_normal_event(envelope, current, status)

    def get_candidate_status(self, candidate_id: str, *, include_promotion: bool = True) -> str:
        envelope = self.get_latest_candidate(candidate_id)
        events = self._normal_events(candidate_id)
        status = events[-1]["status"] if events else "draft"
        if include_promotion and status == "awaiting_human_authorization":
            pid = promotion_id(candidate_id, envelope.content.revision, envelope.content_digest)
            bundle = self.promotions / pid
            marker = bundle / "COMMITTED"
            if marker.exists():
                self._validate_committed_bundle(bundle, envelope)
                return "accepted"
        return status

    def save_review(self, review: EmmaKnowledgeReview) -> None:
        self._id(review.candidate_id)
        if _DIGEST.fullmatch(review.content_digest) is None: raise ValueError("invalid digest")
        directory = self._entity_dir(self.reviews, review.candidate_id, create=True)
        atomic_create(directory / f"{review.revision:08d}-{review.content_digest}.json", _canonical({"candidate_id": review.candidate_id, "revision": review.revision, "content_digest": review.content_digest, "verdict": review.verdict, "reviewed_at": review.reviewed_at, "findings": list(review.findings)}), duplicate_error=KnowledgeAlreadyExists)

    def save_authorization(self, intent: KnowledgeAuthorizationIntent) -> str:
        identity = f"{intent.candidate_id}:{intent.revision}:{intent.content_digest}"
        identifier = hashlib.sha256(identity.encode("ascii")).hexdigest()
        atomic_create(self.authorizations / f"{identifier}.json", _canonical({"candidate_id": intent.candidate_id, "revision": intent.revision, "content_digest": intent.content_digest}), duplicate_error=KnowledgeAlreadyExists)
        return identifier

    def _require_recorded_authorities(self, review: EmmaKnowledgeReview, intent: KnowledgeAuthorizationIntent) -> None:
        review_path = self._entity_dir(self.reviews, review.candidate_id, create=False) / f"{review.revision:08d}-{review.content_digest}.json"
        expected_review = {"candidate_id": review.candidate_id, "revision": review.revision, "content_digest": review.content_digest, "verdict": review.verdict, "reviewed_at": review.reviewed_at, "findings": list(review.findings)}
        stored_review = read_json(review_path, not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        if stored_review != expected_review: raise ValueError("KNOWLEDGE_REVIEW_STALE")
        identity = f"{intent.candidate_id}:{intent.revision}:{intent.content_digest}"
        authorization_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
        stored_intent = read_json(self.authorizations / f"{authorization_id}.json", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        if stored_intent != {"candidate_id": intent.candidate_id, "revision": intent.revision, "content_digest": intent.content_digest}:
            raise ValueError("KNOWLEDGE_AUTHORIZATION_STALE")

    def _all_committed_entries(self, knowledge_id: str) -> list[KnowledgeEntry]:
        self._id(knowledge_id)
        found: list[KnowledgeEntry] = []
        for bundle in self.promotions.iterdir():
            if bundle.is_symlink(): raise KnowledgePathUnsafe("unsafe promotion directory")
            if not re.fullmatch(r"[0-9a-f]{64}", bundle.name) or not bundle.is_dir(): continue
            if not (bundle / "COMMITTED").exists(): continue
            manifest = read_json(bundle / "manifest.json", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
            if manifest.get("target_knowledge_id") != knowledge_id: continue
            entry_value = read_json(bundle / "knowledge-entry.json", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
            entry = knowledge_entry_from_dict(entry_value)
            self._validate_bundle_files(bundle, manifest)
            found.append(entry)
        found.sort(key=lambda item: item.revision)
        for index, entry in enumerate(found, 1):
            if entry.revision != index: raise KnowledgeCorrupt("knowledge revision gap or fork")
        return found

    def get_latest_entry(self, knowledge_id: str) -> KnowledgeEntry:
        values = self._all_committed_entries(knowledge_id)
        if not values: raise KnowledgeNotFound(knowledge_id)
        return values[-1]

    def _validate_transition_evidence(self, envelope: KnowledgeCandidateEnvelope, target: KnowledgeEntry | None) -> None:
        content = envelope.content
        require_entry_transition(target.status if target else None, content.proposed_entry_status)
        if content.proposed_entry_status == "conflicted" and target and target.knowledge_id not in content.contradicts:
            raise ValueError("KNOWLEDGE_TRANSITION_EVIDENCE_INVALID")
        if content.proposed_entry_status == "superseded" and target and target.knowledge_id not in content.supersedes:
            raise ValueError("KNOWLEDGE_TRANSITION_EVIDENCE_INVALID")
        if content.proposed_entry_status == "retired" and content.label != "INTENT":
            raise ValueError("KNOWLEDGE_TRANSITION_EVIDENCE_INVALID")
        if content.proposed_entry_status == "stale" and content.label != "FACT":
            raise ValueError("KNOWLEDGE_TRANSITION_EVIDENCE_INVALID")

    def promote(self, candidate_id: str, review: EmmaKnowledgeReview, authorization: KnowledgeAuthorizationIntent) -> KnowledgeEntry:
        envelope = self.get_latest_candidate(candidate_id)
        content = envelope.content
        target_id = content.target_knowledge_id or content.candidate_id
        with exclusive_entity_locks(self.root, (content.candidate_id, target_id)):
            envelope = self.get_latest_candidate(candidate_id)
            content = envelope.content
            if self.get_candidate_status(candidate_id) == "accepted": return self.get_latest_entry(target_id)
            if self.get_candidate_status(candidate_id, include_promotion=False) != "awaiting_human_authorization": raise ValueError("KNOWLEDGE_CANDIDATE_NOT_AUTHORIZABLE")
            require_candidate_transition("awaiting_human_authorization", "accepted", label=content.label)
            require_exact_authorities(envelope, review, authorization)
            self._require_recorded_authorities(review, authorization)
            if content.repository_binding:
                issues = validate_repository_binding(content.repository_binding)
                if issues: raise ValueError(issues[0].code)
            target = None
            if content.target_knowledge_id:
                try: target = self.get_latest_entry(target_id)
                except KnowledgeNotFound as exc: raise KnowledgeTargetRevisionChanged("target missing") from exc
                if target.revision != content.expected_target_revision: raise KnowledgeTargetRevisionChanged("target revision changed")
                if target.status != content.expected_current_status: raise KnowledgeTargetStateChanged("target status changed")
            self._validate_transition_evidence(envelope, target)
            entry = KnowledgeEntry("1.0", target_id, 1 if target is None else target.revision + 1, content.created_at, content.proposed_entry_status, content.label, content.claim, content.applicability, content.repository_binding, content.research_evidence, content.based_on, content.contradicts, content.supersedes, content.candidate_id, content.revision, envelope.content_digest)  # type: ignore[arg-type]
            return self._commit_promotion(envelope, entry)

    def _commit_promotion(self, envelope: KnowledgeCandidateEnvelope, entry: KnowledgeEntry) -> KnowledgeEntry:
        pid = promotion_id(envelope.content.candidate_id, envelope.content.revision, envelope.content_digest)
        bundle = ensure_private_directory(self.promotions / pid)
        normal_events = self._normal_events(envelope.content.candidate_id)
        sequence = len(normal_events) + 1
        previous_digest = normal_events[-1]["event_digest"] if normal_events else None
        event_core = {"sequence": sequence, "candidate_id": envelope.content.candidate_id, "revision": envelope.content.revision, "content_digest": envelope.content_digest, "previous_status": "awaiting_human_authorization", "status": "accepted", "previous_event_digest": previous_digest}
        event = {**event_core, "event_digest": hashlib.sha256(_canonical(event_core)).hexdigest()}
        entry_value = knowledge_entry_to_dict(entry)
        event_bytes, entry_bytes = _canonical(event), _canonical(entry_value)
        manifest = {"schema_version": "1.0", "promotion_id": pid, "candidate_id": envelope.content.candidate_id, "candidate_revision": envelope.content.revision, "candidate_content_digest": envelope.content_digest, "target_knowledge_id": entry.knowledge_id, "expected_target_revision": envelope.content.expected_target_revision, "expected_current_status": envelope.content.expected_current_status, "resulting_entry_revision": entry.revision, "proposed_entry_status": entry.status, "candidate_event_sha256": hashlib.sha256(event_bytes).hexdigest(), "knowledge_entry_sha256": hashlib.sha256(entry_bytes).hexdigest()}
        artifacts = {"manifest.json": _canonical(manifest), "candidate-event.json": event_bytes, "knowledge-entry.json": entry_bytes}
        for name, payload in artifacts.items():
            path = bundle / name
            if path.exists():
                try: existing = read_bytes(path, not_found_error=KnowledgeNotFound)
                except (UnsafePath, ArtifactCorrupt) as exc: raise KnowledgePromotionRecoveryConflict("unsafe partial promotion") from exc
                if existing != payload: raise KnowledgePromotionRecoveryConflict("partial promotion differs")
            else:
                atomic_create(path, payload, duplicate_error=KnowledgeAlreadyExists)
        fsync_directory(bundle)
        marker_payload = _canonical({"schema_version": "1.0", "manifest_sha256": hashlib.sha256(artifacts["manifest.json"]).hexdigest()})
        marker = bundle / "COMMITTED"
        if marker.exists():
            if read_bytes(marker, not_found_error=KnowledgeNotFound) != marker_payload: raise KnowledgePromotionRecoveryConflict("commit marker differs")
        else: atomic_create(marker, marker_payload, duplicate_error=KnowledgeAlreadyExists)
        fsync_directory(bundle); fsync_directory(self.promotions)
        self._validate_committed_bundle(bundle, envelope)
        return entry

    def _validate_bundle_files(self, bundle: Path, manifest: dict[str, Any]) -> None:
        event = read_bytes(bundle / "candidate-event.json", not_found_error=KnowledgeNotFound)
        entry = read_bytes(bundle / "knowledge-entry.json", not_found_error=KnowledgeNotFound)
        if hashlib.sha256(event).hexdigest() != manifest.get("candidate_event_sha256") or hashlib.sha256(entry).hexdigest() != manifest.get("knowledge_entry_sha256"):
            raise KnowledgeCorrupt("promotion artifact digest mismatch")
        marker = read_json(bundle / "COMMITTED", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        manifest_bytes = read_bytes(bundle / "manifest.json", not_found_error=KnowledgeNotFound)
        if marker.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest(): raise KnowledgeCorrupt("promotion manifest digest mismatch")

    def _validate_committed_bundle(self, bundle: Path, envelope: KnowledgeCandidateEnvelope) -> None:
        manifest = read_json(bundle / "manifest.json", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        expected = (envelope.content.candidate_id, envelope.content.revision, envelope.content_digest)
        if (manifest.get("candidate_id"), manifest.get("candidate_revision"), manifest.get("candidate_content_digest")) != expected:
            raise KnowledgeCorrupt("promotion tuple mismatch")
        if manifest.get("promotion_id") != promotion_id(*expected): raise KnowledgeCorrupt("promotion identity mismatch")
        self._validate_bundle_files(bundle, manifest)
        event = read_json(bundle / "candidate-event.json", not_found_error=KnowledgeNotFound, corrupt_error=KnowledgeCorrupt)
        normal_events = self._normal_events(envelope.content.candidate_id)
        event_core = {key: event.get(key) for key in ("sequence", "candidate_id", "revision", "content_digest", "previous_status", "status", "previous_event_digest")}
        expected_core = {"sequence": len(normal_events) + 1, "candidate_id": envelope.content.candidate_id, "revision": envelope.content.revision, "content_digest": envelope.content_digest, "previous_status": "awaiting_human_authorization", "status": "accepted", "previous_event_digest": normal_events[-1]["event_digest"] if normal_events else None}
        if event_core != expected_core or event.get("event_digest") != hashlib.sha256(_canonical(event_core)).hexdigest():
            raise KnowledgeCorrupt("accepted event does not extend candidate chain")
