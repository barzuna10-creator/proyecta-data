"""Control Plane V1 -- the only HTTP surface between Jarvis Private Command
Center and the Agent System. stdlib only, no new dependency. Bind is
hard-refused to anything but loopback.

mission_query is the sole reader; mission_write and
jarvis.mission_authorization_bridge are the sole writers. This module
itself performs no write of its own beyond what it delegates to those --
in particular it never constructs a `decided_by` value for anything other
than the literal HUMAN_DECIDER, and only inside the draft branch of
_handle_authorize(), which has just finished checking authentication,
CSRF/origin (enforced upstream by the Command Center's own Express
server -- this process only ever receives already-CSRF-checked, already-
authenticated requests over loopback), the literal confirmation string,
and an exact digest/revision match against the current on-disk draft.
Authentication (the bearer token below) is a precondition for reaching
that handler at all, never a substitute for the confirmation/digest
checks inside it.

Like jarvis.mission_authorization_bridge, this module never imports
orchestrator.chugel directly -- it is not one of the three disclosed
Chugel seams (mission_query.py / mission_write.py / mission_coordinator.py,
enforced by tests/test_jarvis_foundation_boundaries.py) -- every Chugel
effect is obtained exclusively through mission_query/mission_write/
mission_authorization_bridge.

No mission_coordinator.advance() anywhere in this module: V1 only relays
reads (mission_query) and explicit human gate authorizations
(mission_write / mission_authorization_bridge). Dispatching Emilio/Emma,
running publish_executor/merge_executor, remains an out-of-band, human-
supervised action -- exactly as it has been for every mission run this
far in Mission 004."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orchestrator import jarvis_conversation
from orchestrator.validator import HUMAN_DECIDER

from jarvis import mission_query, mission_write
from jarvis.drafts import build_draft_envelope, revise_mission_draft
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.mission_authorization_bridge import (
    close_draft_authorization,
    DraftAuthorizationAttributionError,
    DraftAuthorizationDivergenceError,
    DraftAuthorizationRefused,
)
from jarvis.mission_context import draft_briefing
from jarvis.models import AuthorizationIntent, DraftChanges, MissionDefinitionDraft, MissionDraft
from jarvis.repository_freshness import RepositoryFreshnessResolver
from jarvis.storage import (
    DraftAlreadyExists, DraftNotFound, FileJarvisStore, ProposalContentMismatch,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_BODY_BYTES = 65536
_CONFIRMATION = "I authorize this action now"

_AUTHORIZE_BY_KIND = {
    "scope": mission_write.authorize_scope,
    "publish": mission_write.authorize_publish,
    "merge": mission_write.authorize_merge,
}
# Never used to derive an id used as a *lock* key (only draft_id and
# mission_id are ever lock keys, both real Chugel/store UUIDs) -- purely a
# stable, opaque, recomputed-fresh-every-time identifier so an existing
# gate can be addressed by a literal UUID over HTTP without Chugel itself
# needing any new persisted concept of "gate id".
_GATE_ID_NAMESPACE = uuid.UUID("0f1e2d3c-4b5a-4968-8778-899a0b1c2d3e")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _advanced_timestamp(previous: str) -> str:
    """revise_mission_draft() requires updated_at to strictly advance past
    the previous revision's -- timestamps here are second-granularity, so
    two conversation turns within the same wall-clock second would
    otherwise collide. Never returns a timestamp <= previous."""
    now = _now()
    if now > previous:
        return now
    from datetime import timedelta
    bumped = datetime.strptime(previous, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(seconds=1)
    return bumped.strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_text_digest(text: str) -> str:
    rendered = json.dumps(text, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _gate_id_for(mission_id: str, gate_name: str) -> str:
    return str(uuid.uuid5(_GATE_ID_NAMESPACE, f"{mission_id}:{gate_name}"))


class ControlPlaneConfig:
    def __init__(
        self, *, host: str, port: int, token: str, store_root: str,
        knowledge_store_root: str | None = None, zentra_repository_root: str | None = None,
    ):
        if host not in _LOOPBACK_HOSTS:
            raise ValueError("Control Plane must bind to a loopback host")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("CONTROL_PLANE_TOKEN must be at least 32 characters")
        if isinstance(port, bool) or not isinstance(port, int) or not (0 <= port <= 65535):
            raise ValueError("CONTROL_PLANE_PORT must be a valid TCP port")
        self.host = host
        self.port = port
        self.token = token
        self.store_root = Path(store_root)
        # Both optional, and deliberately independent of each other's
        # presence at this layer -- Mission 005's citation wiring is a
        # pure addition. Unset (either or both): the conversational turn
        # behaves exactly as it did before this feature existed, with no
        # citations, no error, no degraded mode. Never auto-provisioned,
        # never inferred from another setting.
        self.knowledge_store_root = Path(knowledge_store_root) if knowledge_store_root else None
        self.zentra_repository_root = Path(zentra_repository_root) if zentra_repository_root else None


def load_config(env: dict | None = None) -> ControlPlaneConfig:
    env = os.environ if env is None else env
    host = env.get("CONTROL_PLANE_HOST", "127.0.0.1")
    port_raw = env.get("CONTROL_PLANE_PORT", "4318")
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("CONTROL_PLANE_PORT must be an integer") from exc
    token = env.get("CONTROL_PLANE_TOKEN")
    if not token:
        raise ValueError("CONTROL_PLANE_TOKEN is required")
    store_root = env.get("CONTROL_PLANE_STORE_ROOT")
    if not store_root:
        raise ValueError("CONTROL_PLANE_STORE_ROOT is required")
    return ControlPlaneConfig(
        host=host, port=port, token=token, store_root=store_root,
        knowledge_store_root=env.get("CONTROL_PLANE_KNOWLEDGE_STORE_ROOT") or None,
        zentra_repository_root=env.get("CONTROL_PLANE_ZENTRA_REPOSITORY_ROOT") or None,
    )


class _ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _stub_mission_draft(draft_id: str, objective: str) -> MissionDraft:
    now = _now()
    return MissionDraft(
        schema_version="1.0.0",
        draft_id=draft_id,
        revision=1,
        created_at=now,
        updated_at=now,
        raw_intent=objective,
        mission_definition=MissionDefinitionDraft(
            outcome=objective[:4000],
            scope=("To be scoped before authorization",),
            non_goals=(),
            acceptance_criteria=("To be defined before authorization",),
        ),
        research_evidence=(),
        risks=(),
        open_questions=("Scope and acceptance criteria are placeholders pending refinement.",),
        repository_context=None,
    )


def _merged_definition_and_open_questions(
    existing: MissionDraft | None, suggestion: jarvis_conversation.DraftFieldSuggestion,
) -> tuple[MissionDefinitionDraft, tuple[str, ...]]:
    """Merges Jarvis's *proposed* field suggestion onto the current draft
    (or sensible placeholders for a brand-new one, same placeholders
    _stub_mission_draft() already uses) -- never trusts a suggested field
    that is None (meaning "the conversation hasn't addressed this yet")
    or an empty scope/acceptance_criteria (schema requires at least one
    entry; an empty list from the model is treated the same as absent,
    fail-soft rather than producing an invalid draft). open_questions is
    the one field where an explicit empty list IS meaningful (it is what
    marks a draft authorization-ready), so it alone is checked with
    `is not None`, never truthiness."""
    existing_definition = existing.mission_definition if existing else None
    existing_open_questions = existing.open_questions if existing else (
        "Objective not yet fully understood.",
    )
    outcome = suggestion.outcome if suggestion.outcome is not None else (
        existing_definition.outcome if existing_definition else "To be defined during scoping"
    )
    scope = tuple(suggestion.scope) if suggestion.scope else (
        existing_definition.scope if existing_definition else ("To be scoped before authorization",)
    )
    non_goals = tuple(suggestion.non_goals) if suggestion.non_goals is not None else (
        existing_definition.non_goals if existing_definition else ()
    )
    acceptance_criteria = tuple(suggestion.acceptance_criteria) if suggestion.acceptance_criteria else (
        existing_definition.acceptance_criteria if existing_definition else ("To be defined before authorization",)
    )
    open_questions = tuple(suggestion.open_questions) if suggestion.open_questions is not None else existing_open_questions
    definition = MissionDefinitionDraft(
        outcome=outcome, scope=scope, non_goals=non_goals, acceptance_criteria=acceptance_criteria,
    )
    return definition, open_questions


def _handle_conversation(
    store: FileJarvisStore, body: dict, *,
    knowledge_store: FileKnowledgeStore | None = None,
    zentra_resolver: RepositoryFreshnessResolver | None = None,
) -> dict:
    """Jarvis's own natural-language understanding of one conversation
    turn, producing a reply and -- never automatically, only when Jarvis's
    own dispatch proposes one -- an updated MissionDraft revision. This
    never authorizes anything: the draft this saves is exactly as
    unauthorized as the one /v1/proposals produces, subject to the exact
    same digest/revision-exact authorization flow in _handle_authorize().

    knowledge_store/zentra_resolver are optional (Mission 005): when both
    are configured, already-authorized trusted knowledge is surfaced to
    the model as read-only citations via jarvis.mission_context.draft_briefing()
    -- unmodified, unmediated by this function beyond passing its output
    through. When either is absent, this turn behaves exactly as it did
    before Mission 005 existed: no citations, no error, no degraded mode."""
    message = body.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise _ApiError(400, "message rejected")
    history_raw = body.get("history") or []
    if not isinstance(history_raw, list) or len(history_raw) > 200:
        raise _ApiError(400, "history rejected")
    history: list[dict] = []
    for item in history_raw:
        if not isinstance(item, dict) or item.get("role") not in ("user", "jarvis") or not isinstance(item.get("text"), str):
            raise _ApiError(400, "history entry rejected")
        history.append({"role": item["role"], "text": item["text"][:4000]})
    history.append({"role": "user", "text": message})

    draft_id = body.get("draftId")
    current_envelope = None
    if draft_id is not None:
        if not isinstance(draft_id, str) or _UUID.fullmatch(draft_id) is None:
            raise _ApiError(400, "draftId must be a canonical UUID or null")
        try:
            current_envelope = store.get_latest_draft(draft_id)
        except DraftNotFound as exc:
            raise _ApiError(404, "draft not found") from exc

    current_fields = None
    if current_envelope is not None:
        md = current_envelope.draft.mission_definition
        current_fields = {
            "outcome": md.outcome, "scope": list(md.scope), "non_goals": list(md.non_goals),
            "acceptance_criteria": list(md.acceptance_criteria),
            "open_questions": list(current_envelope.draft.open_questions),
        }

    trusted_citations: tuple[dict, ...] = ()
    if knowledge_store is not None and zentra_resolver is not None:
        # Fixed, general area for this conversational surface -- not
        # caller-supplied, not derived from `message`. Every source on
        # the Zentra allow-list is authored under this one area (see
        # jarvis/zentra_sources_policy.json); narrower areas remain
        # available to other callers of draft_briefing() should a more
        # targeted use ever need them, but this endpoint only ever asks
        # the broad question.
        briefing = draft_briefing(knowledge_store, zentra_resolver, product_areas=("zentra",))
        trusted_citations = tuple(
            {"knowledgeId": c.knowledge_id, "claim": c.claim, "label": c.label, "tier": c.tier}
            for c in briefing.citations
        )

    try:
        turn = jarvis_conversation.converse(history, current_fields, trusted_citations=trusted_citations)
    except jarvis_conversation.SubscriptionAuthRequired as exc:
        raise _ApiError(503, f"conversational dispatch unavailable: {exc}") from exc
    except jarvis_conversation.JarvisConversationError as exc:
        raise _ApiError(502, f"conversational turn failed: {exc}") from exc

    gate = None
    suggestion = turn.suggestion
    # A suggestion object where every field is empty in the same sense
    # _merged_definition_and_open_questions() itself treats as "this
    # field contributes nothing" means the same thing as suggestion:
    # null -- nothing was actually proposed. This check MUST mirror that
    # function's own per-field semantics exactly (truthiness for
    # outcome/scope/acceptance_criteria -- an empty list is schema-invalid
    # and treated as absent; `is not None` for non_goals/open_questions,
    # where an explicit empty list/tuple is meaningful, e.g. it is what
    # marks a draft authorization-ready). A naive `is not None` check on
    # every field would let a suggestion like
    # {outcome: None, scope: None, non_goals: None,
    #  acceptance_criteria: None, open_questions: []} -- which merges to
    # NO real content but a non-null open_questions -- spuriously create
    # or revise a draft filled entirely with placeholder strings while
    # signaling "ready" (open_questions=()), a live-observed shape.
    has_real_content = suggestion is not None and any((
        bool(suggestion.outcome), bool(suggestion.scope),
        suggestion.non_goals is not None, bool(suggestion.acceptance_criteria),
    ))
    # open_questions=[] alone (no other field carrying real content) is
    # only meaningful as a genuine "ready" signal when revising a draft
    # that ALREADY has real prior content to be ready about -- for a
    # brand-new draft (current_envelope is None) it would merge to an
    # authorization-ready gate made entirely of placeholder strings
    # ("To be defined during scoping", etc.), which is never a real
    # proposal regardless of what open_questions says.
    has_open_questions_signal = (
        suggestion is not None and suggestion.open_questions is not None
        and current_envelope is not None
    )
    has_proposal = has_real_content or has_open_questions_signal
    if has_proposal:
        from jarvis._safe_io import exclusive_entity_lock
        lock_id = draft_id or str(uuid.uuid4())
        with exclusive_entity_lock(store.root, lock_id):
            # Re-read the latest draft here, inside the lock, rather than
            # trusting the `current_envelope` captured before the (up to
            # ~2min) converse() dispatch ran. A concurrent turn on the same
            # draftId could have revised it in between; merging onto a
            # stale envelope would silently clobber that revision (a
            # lost-update race). This re-read is the actual revision base.
            latest_envelope = None
            if draft_id is not None:
                try:
                    latest_envelope = store.get_latest_draft(draft_id)
                except DraftNotFound:
                    latest_envelope = None
            if latest_envelope is None:
                definition, open_questions = _merged_definition_and_open_questions(None, turn.suggestion)
                now = _now()
                draft = MissionDraft(
                    schema_version="1.0.0", draft_id=lock_id, revision=1, created_at=now, updated_at=now,
                    raw_intent=message, mission_definition=definition, research_evidence=(),
                    risks=(), open_questions=open_questions, repository_context=None,
                )
                envelope = build_draft_envelope(draft)
            else:
                definition, open_questions = _merged_definition_and_open_questions(
                    latest_envelope.draft, turn.suggestion,
                )
                revised = revise_mission_draft(
                    latest_envelope.draft,
                    updated_at=_advanced_timestamp(latest_envelope.draft.updated_at),
                    changes=DraftChanges(mission_definition=definition, open_questions=open_questions),
                )
                envelope = build_draft_envelope(revised)
            store.save_draft(envelope)
        gate = _draft_gate_projection(envelope.draft.draft_id, envelope)

    return {"reply": turn.reply, "draftId": (gate["id"] if gate else draft_id), "gate": gate}


def _draft_gate_projection(draft_id: str, envelope) -> dict:
    draft = envelope.draft
    return {
        "id": draft.draft_id,
        "missionId": f"draft:{draft.draft_id}",
        "kind": "draft",
        "state": "pending",
        "summary": draft.mission_definition.outcome[:240],
        "revision": str(draft.revision),
        "requestedAt": draft.created_at,
        "digest": envelope.digest,
    }


def _pending_real_gate(mission_id: str, status) -> dict | None:
    action = status.human_action_required
    kind = {"scope_authorization": "scope", "publish_authorization": "publish", "merge_authorization": "merge"}.get(action)
    if kind is None:
        return None
    return {
        "id": _gate_id_for(mission_id, action),
        "missionId": mission_id,
        "kind": kind,
        "state": "pending",
        "summary": f"{kind} authorization pending for mission {mission_id}",
        "revision": status.updated_at,
        "requestedAt": status.updated_at,
    }


def _build_projection(store: FileJarvisStore) -> dict:
    listings = mission_query.list_missions()
    gates: list[dict] = []
    missions: list[dict] = []
    for item in listings:
        missions.append({
            "id": item.mission_id,
            "title": item.mission_id,
            "phase": item.state or "unknown",
            "progress": 0,
            "status": "active" if item.bucket == "running" else ("blocked" if item.bucket in ("blocked", "terminal") else "active"),
            "updatedAt": item.updated_at or "",
        })
        if item.readable:
            try:
                status = mission_query.get_mission_status(item.mission_id)
            except mission_query.MissionQueryError:
                continue
            gate = _pending_real_gate(item.mission_id, status)
            if gate is not None:
                gates.append(gate)
    for draft_id in store.list_pending_draft_ids():
        envelope = store.get_latest_draft(draft_id)
        gates.append(_draft_gate_projection(draft_id, envelope))
    now = _now()
    return {
        "sequence": int(datetime.now(timezone.utc).timestamp() * 1000),
        "generatedAt": now,
        "missions": missions,
        "agents": [],
        "gates": gates,
        "findings": [],
        "checks": [],
        "activity": [],
        "knowledge": [],
        "health": {"chugel": "connected", "stream": "polling", "latencyMs": 0, "lastSync": now},
        "git": {"branch": "", "commit": "", "pullRequest": None, "ci": "passing"},
    }


def _handle_proposals(store: FileJarvisStore, body: dict) -> dict:
    objective = body.get("objective")
    proposal_id = body.get("proposalId")
    if not isinstance(objective, str) or not objective.strip() or len(objective) > 3000:
        raise _ApiError(400, "objective rejected")
    if not isinstance(proposal_id, str) or _UUID.fullmatch(proposal_id) is None:
        raise _ApiError(400, "proposalId must be a canonical UUID")

    content_digest = _canonical_text_digest(objective)
    from jarvis._safe_io import exclusive_entity_lock
    with exclusive_entity_lock(store.root, proposal_id):
        existing = store.get_proposal(proposal_id)
        if existing is not None and existing["content_digest"] == content_digest:
            draft_id = existing["draft_id"]
        else:
            draft_id = str(uuid.uuid4())  # server always mints the draft identity
            try:
                store.record_proposal(proposal_id, content_digest, draft_id)
            except ProposalContentMismatch as exc:
                raise _ApiError(409, "proposalId already used with different content") from exc

        # Crash recovery: a prior attempt may have durably recorded the
        # proposal (above, or on an earlier call that reached this exact
        # point) without ever completing save_draft() -- draft_id is
        # deterministically re-derivable from (draft_id, objective) alone,
        # so this always reconstructs the byte-identical draft rather than
        # inventing a second one. save_draft() is itself create-once
        # atomic, so a second attempt racing here is still safe.
        try:
            store.get_latest_draft(draft_id)
        except DraftNotFound:
            draft = _stub_mission_draft(draft_id, objective)
            envelope = build_draft_envelope(draft)
            try:
                store.save_draft(envelope)
            except DraftAlreadyExists:
                pass  # another concurrent retry just completed it
    envelope = store.get_latest_draft(draft_id)
    return {"accepted": True, "gate": _draft_gate_projection(draft_id, envelope)}


def _handle_authorize(store: FileJarvisStore, gate_id: str, body: dict) -> dict:
    mission_id = body.get("missionId")
    expected_revision = body.get("expectedRevision")
    action = body.get("action")
    confirmation = body.get("confirmation")
    digest = body.get("digest")

    if action != "authorize" or confirmation != _CONFIRMATION:
        raise _ApiError(400, "explicit current authorization required")
    if not isinstance(mission_id, str) or not isinstance(expected_revision, str):
        raise _ApiError(400, "missionId/expectedRevision required")

    if isinstance(mission_id, str) and mission_id.startswith("draft:") and mission_id == f"draft:{gate_id}":
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise _ApiError(400, "digest is required to authorize a draft")
        try:
            revision = int(expected_revision)
        except ValueError as exc:
            raise _ApiError(400, "expectedRevision must be an integer for a draft") from exc
        intent = AuthorizationIntent(
            draft_id=gate_id, revision=revision, digest_algorithm="sha256", digest=digest,
        )
        decision = {
            "decided_by": HUMAN_DECIDER,
            "decided_at": _now(),
            "decision_ref": f"control-plane-draft-authorization:{gate_id}:{revision}:{digest}",
        }
        try:
            result = close_draft_authorization(store, intent, decision)
        except DraftAuthorizationRefused as exc:
            raise _ApiError(409, "; ".join(reason.code for reason in exc.reasons)) from exc
        except (DraftAuthorizationAttributionError, DraftAuthorizationDivergenceError) as exc:
            raise _ApiError(409, str(exc)) from exc
        now = _now()
        return {
            "id": gate_id, "missionId": result.mission_id, "kind": "draft", "state": "authorized",
            "summary": f"draft {gate_id} authorized as mission {result.mission_id}",
            "revision": str(revision), "requestedAt": now,
        }

    # Real Chugel gate (scope/publish/merge) -- unchanged semantics,
    # delegated verbatim to jarvis.mission_write.
    listings = {item.mission_id for item in mission_query.list_missions()}
    if mission_id not in listings:
        raise _ApiError(409, "gate state changed; review the current state")
    try:
        status = mission_query.get_mission_status(mission_id)
    except mission_query.MissionQueryError as exc:
        raise _ApiError(409, "gate state changed; review the current state") from exc
    action_name = status.human_action_required
    kind = {"scope_authorization": "scope", "publish_authorization": "publish", "merge_authorization": "merge"}.get(action_name)
    if kind is None or _gate_id_for(mission_id, action_name) != gate_id:
        raise _ApiError(409, "gate state changed; review the current state")
    if expected_revision != status.updated_at:
        raise _ApiError(409, "gate state changed; review the current state")
    decision = {
        "decided_by": HUMAN_DECIDER,
        "decided_at": _now(),
        "decision_ref": f"control-plane-gate-authorization:{gate_id}",
        "requested_at": status.updated_at,
        "status": "approved",
        # The mission's CURRENT mission_definition_version, read fresh
        # above via mission_query -- never hardcoded. orchestrator/
        # validator.py's _check_stale_approvals() enforces this exactly
        # for scope_authorization (an approval naming an old version is a
        # STALE_APPROVAL, e.g. after a David re-plan bumped the version
        # while this gate was still pending); authorize_merge() overrides
        # this field internally with head_sha regardless of what is passed
        # here; publish_authorization has no analogous staleness check
        # today, but there is no reason to hand it a wrong version either.
        "approved_for": {"mission_definition_version": status.mission_definition_version},
    }
    try:
        _AUTHORIZE_BY_KIND[kind](mission_id, decision)
    except mission_write.GateNotYetEligible as exc:
        raise _ApiError(409, str(exc)) from exc
    now = _now()
    return {
        "id": gate_id, "missionId": mission_id, "kind": kind, "state": "authorized",
        "summary": f"{kind} authorization approved for mission {mission_id}",
        "revision": now, "requestedAt": now,
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "JarvisControlPlane/1"

    def _config(self) -> ControlPlaneConfig:
        return self.server.config  # type: ignore[attr-defined]

    def _store(self) -> FileJarvisStore:
        return self.server.store  # type: ignore[attr-defined]

    def _knowledge_store(self) -> FileKnowledgeStore | None:
        return self.server.knowledge_store  # type: ignore[attr-defined]

    def _zentra_resolver(self) -> RepositoryFreshnessResolver | None:
        return self.server.zentra_resolver  # type: ignore[attr-defined]

    def _authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        supplied = header[len("Bearer "):]
        return hmac.compare_digest(supplied, self._config().token)

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header)
        except (TypeError, ValueError):
            raise _ApiError(400, "Content-Length required")
        if length < 0 or length > _MAX_BODY_BYTES:
            raise _ApiError(400, "request body rejected")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _ApiError(400, "invalid JSON body") from exc
        if not isinstance(value, dict):
            raise _ApiError(400, "request body must be a JSON object")
        return value

    def _dispatch(self, method: str) -> None:
        if not self._authenticated():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            if method == "GET" and self.path == "/v1/command-center/projection":
                self._write_json(200, _build_projection(self._store()))
                return
            if method == "POST" and self.path == "/v1/proposals":
                body = self._read_json_body()
                self._write_json(201, _handle_proposals(self._store(), body))
                return
            if method == "POST" and self.path == "/v1/conversation":
                body = self._read_json_body()
                self._write_json(200, _handle_conversation(
                    self._store(), body,
                    knowledge_store=self._knowledge_store(), zentra_resolver=self._zentra_resolver(),
                ))
                return
            match = re.fullmatch(r"/v1/gates/([^/]+)/authorize", self.path)
            if method == "POST" and match is not None:
                gate_id = match.group(1)
                if _UUID.fullmatch(gate_id) is None:
                    raise _ApiError(400, "gate id must be a canonical UUID")
                body = self._read_json_body()
                self._write_json(200, _handle_authorize(self._store(), gate_id, body))
                return
        except _ApiError as exc:
            self._write_json(exc.status, {"error": exc.message})
            return
        except Exception:  # noqa: BLE001 -- final fail-closed net: never leak internals or
            # crash the request thread on an exception this module did not
            # anticipate (including any chugel-side validation failure this
            # module has no direct import of and therefore cannot name).
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Control Plane request failed"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802 -- stdlib method name
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 -- stdlib method name
        self._dispatch("POST")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 -- stdlib signature
        pass  # never log request contents (may carry the bearer token or objective text)


def build_server(config: ControlPlaneConfig) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.host, config.port), _Handler)
    server.config = config  # type: ignore[attr-defined]
    server.store = FileJarvisStore(config.store_root)  # type: ignore[attr-defined]
    # Constructed once at startup, not per-request: a misconfigured path
    # here must fail server startup loudly, not be swallowed inside a
    # live conversation turn.
    server.knowledge_store = (  # type: ignore[attr-defined]
        FileKnowledgeStore(config.knowledge_store_root) if config.knowledge_store_root else None
    )
    server.zentra_resolver = (  # type: ignore[attr-defined]
        RepositoryFreshnessResolver(config.zentra_repository_root) if config.zentra_repository_root else None
    )
    return server


def main() -> None:
    config = load_config()
    server = build_server(config)
    print(f"Jarvis Control Plane listening on http://{config.host}:{config.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
