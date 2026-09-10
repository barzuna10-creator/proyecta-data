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

This module never calls mission_coordinator.advance() itself, and never
imports jarvis.mission_coordinator: it only relays reads (mission_query),
explicit human gate authorizations (mission_write /
mission_authorization_bridge), and -- Mission 006 -- a bare notify() to
jarvis.mission_supervisor.MissionSupervisor, from exactly the places
above where a real human authorization decision has just been durably
recorded (the draft-authorization branch, the real scope/publish/merge
gate branch, and -- M6 -- the resume-from-BLOCKED and reopen-deploy-
observation-window branches, each gated by the same literal, current-
turn HUMAN_DECIDER attribution as every other branch here). notify()
carries no payload and makes no decision of its own; the supervisor
re-derives whatever is actually eligible from Chugel's own state on its
own background worker thread. In particular, _handle_conversation() --
which can create or revise a draft, but never authorizes anything -- has
no access to the supervisor at all: it is not passed one, and POST
/v1/conversation's dispatch does not construct one for it (see
tests/test_jarvis_control_plane_server.py's boundary coverage).
Dispatching Emilio/Emma, running publish_executor/merge_executor,
therefore only ever happens as the automatic, in-process consequence of a
real, already-materialized human authorization -- never of conversation
alone, and never automatically retried around a gate/BLOCKED/terminal
state (jarvis/mission_supervisor.py's own state classification).

M6 (BLOCKED-State Resume Wiring & Unified Recovery Contract): two new,
fully separate HTTP handlers -- _handle_resume() (POST
/v1/missions/{id}/resume, wrapping jarvis.mission_write.resume_from_blocked())
and _handle_reopen_deploy_observation() (POST
/v1/missions/{id}/reopen-deploy-observation, wrapping jarvis.mission_write.
reopen_deploy_observation_window()) -- give Mission 004/M4's already-built,
already-gated resume/reopen functions their first real HTTP callers.
Neither is addressed by gate_id, neither is added to _AUTHORIZE_BY_KIND
(an earlier design round explicitly rejected that: wrong addressing
scheme, no room for reopen's additional `acknowledgement` field, and it
would let DeployRecoveryRequired fall through to the generic 500 handler
instead of surfacing actionable guidance) -- _AUTHORIZE_BY_KIND and
_handle_authorize() are untouched by M6, byte-for-byte. Both new handlers
catch jarvis.mission_write.ResumeNotEligible/DeployRecoveryRequired/
DeployReopenNotEligible themselves and translate them to _ApiError
before this module's own generic `except Exception` in _dispatch() could
ever see them."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orchestrator import jarvis_conversation
from orchestrator.cli_provider_adapters import build_cli_subscription_adapters
from orchestrator.deploy_recovery_policy import RecoveryAction
from orchestrator.validator import HUMAN_DECIDER

from jarvis import mission_query, mission_write
from jarvis.drafts import build_draft_envelope, revise_mission_draft
from jarvis.objective_dependencies import dependency_status, failed_root_cause_for
from jarvis.knowledge_storage import FileKnowledgeStore
from jarvis.mission_authorization_bridge import (
    close_draft_authorization,
    DraftAuthorizationAttributionError,
    DraftAuthorizationDivergenceError,
    DraftAuthorizationRefused,
)
from jarvis.mission_context import draft_briefing
from jarvis.mission_supervisor import MissionSupervisor
from jarvis.mission_workspace import MissionWorkspaceManager
from orchestrator.workspace import acquire_workspace_supervisor_lease
from jarvis.models import (
    AuthorizationIntent, DraftChanges, MissionDefinitionDraft, MissionDraft,
    Objective, ObjectiveDecompositionEntry,
)
from jarvis.objectives import build_objective_envelope
from jarvis.repository_freshness import RepositoryFreshnessResolver
from jarvis.trusted_zentra_context import TrustedZentraContextBuilder
from jarvis.zentra_evidence import load_policy
from jarvis.zentra_github_query import ReadOnlyGitHubQuery
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

# Jarvis God Mode M1 -- fixed namespace for deterministically deriving
# each decomposition item's draft_id from (objective_id, index). Never
# random: this is what makes converging an Objective's decomposition
# after a crash idempotent -- see _converge_objective_decomposition()'s
# own docstring.
_DECOMPOSITION_DRAFT_ID_NAMESPACE = uuid.UUID("7c6b5a49-3e2d-4f1a-9b8c-0d1e2f3a4b5c")
# Decision #1 (approved, M1 decisions): fixed, hard-coded, never
# configurable -- exactly 2-4 items or the decomposition is not acted
# on at all (the turn falls through to the single-draft OBJECTIVE path
# unchanged, decision #2).
_OBJECTIVE_DECOMPOSITION_MIN_ITEMS = 2
_OBJECTIVE_DECOMPOSITION_MAX_ITEMS = 4


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
        zentra_backend_root: str | None = None, zentra_frontend_root: str | None = None,
        mission_repository_root: str | None = None, mission_branch: str | None = None,
        mission_pr_title: str | None = None, git_executable: str = "git", gh_executable: str = "gh",
        mission_concurrency: int = 2, build_review_deadline: float = 3600.0,
        ci_poll_timeout_seconds: float = 1800.0, ci_poll_interval_seconds: float = 30.0,
        projection_cache_ttl_seconds: float = 2.0, projection_activity_limit: int = 100,
        projection_finding_summary_max_chars: int = 500,
        business_context_base_url: str | None = None, business_context_token: str | None = None,
        business_context_cache_ttl_seconds: float = 300.0,
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
        self.zentra_backend_root = Path(zentra_backend_root) if zentra_backend_root else None
        self.zentra_frontend_root = Path(zentra_frontend_root) if zentra_frontend_root else None
        # Mission 006: the single repository this V1, single-active-mission
        # supervisor drives publish_executor/merge_executor against, once a
        # mission reaches PUBLISHING/MERGING via a real human authorization.
        # Optional and independently defaulted -- unset means the
        # supervisor still performs the INTAKE mechanical transition and
        # everything up to (and reporting) a gate correctly; it would only
        # fail, per-mission, if a real PUBLISHING/MERGING step were ever
        # reached without these configured, exactly like calling
        # mission_coordinator.advance() with a fake repository always has.
        self.mission_repository_root = mission_repository_root
        self.mission_branch = mission_branch
        self.mission_pr_title = mission_pr_title
        self.git_executable = git_executable
        self.gh_executable = gh_executable
        if isinstance(mission_concurrency, bool) or not 1 <= mission_concurrency <= 8:
            raise ValueError("CONTROL_PLANE_MISSION_CONCURRENCY must be 1..8")
        durations = (build_review_deadline, ci_poll_timeout_seconds, ci_poll_interval_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("mission timeouts must be finite positive numbers")
        self.mission_concurrency = mission_concurrency
        self.build_review_deadline = build_review_deadline
        self.ci_poll_timeout_seconds = ci_poll_timeout_seconds
        self.ci_poll_interval_seconds = ci_poll_interval_seconds
        # M8 (Command Center Live Operational Visibility): the projection
        # cache's TTL, and the payload-size limits (A4/A2 in the M8
        # design) applied purely in the projection layer -- never a
        # relaxation of any Mission Record schema limit.
        if not math.isfinite(projection_cache_ttl_seconds) or projection_cache_ttl_seconds < 0:
            raise ValueError("projection_cache_ttl_seconds must be a finite, non-negative number")
        if isinstance(projection_activity_limit, bool) or not isinstance(projection_activity_limit, int) or projection_activity_limit < 0:
            raise ValueError("projection_activity_limit must be a non-negative integer")
        if (
            isinstance(projection_finding_summary_max_chars, bool)
            or not isinstance(projection_finding_summary_max_chars, int)
            or projection_finding_summary_max_chars < 1
        ):
            raise ValueError("projection_finding_summary_max_chars must be a positive integer")
        self.projection_cache_ttl_seconds = projection_cache_ttl_seconds
        self.projection_activity_limit = projection_activity_limit
        self.projection_finding_summary_max_chars = projection_finding_summary_max_chars
        # M9 (Read-Only Business Context Expansion), Section 8: optional,
        # independently defaulted like the zentra_* roots above -- unset
        # means the `businessContext` projection key still appears (see
        # _business_context_projection()) with `available: False` and an
        # honest reason, never a crash, never a silently omitted key.
        # Always routed through jarvis.business_context (Layer 2/3) --
        # never a separate ad-hoc connection to the product backend.
        if business_context_cache_ttl_seconds < 300.0:
            raise ValueError("business_context_cache_ttl_seconds must be >= 300 (M9 Correction 9 of V3)")
        self.business_context_base_url = business_context_base_url or None
        self.business_context_token = business_context_token or None
        self.business_context_cache_ttl_seconds = business_context_cache_ttl_seconds


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
    try:
        concurrency = int(env.get("CONTROL_PLANE_MISSION_CONCURRENCY", "2"))
        build_deadline = float(env.get("CONTROL_PLANE_BUILD_REVIEW_DEADLINE_SECONDS", "3600"))
        ci_timeout = float(env.get("CONTROL_PLANE_CI_TIMEOUT_SECONDS", "1800"))
        ci_interval = float(env.get("CONTROL_PLANE_CI_POLL_INTERVAL_SECONDS", "30"))
        projection_cache_ttl = float(env.get("CONTROL_PLANE_PROJECTION_CACHE_TTL_SECONDS", "2"))
        projection_activity_limit = int(env.get("CONTROL_PLANE_PROJECTION_ACTIVITY_LIMIT", "100"))
        projection_finding_summary_max_chars = int(
            env.get("CONTROL_PLANE_PROJECTION_FINDING_SUMMARY_MAX_CHARS", "500")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("mission concurrency/timeouts are invalid") from exc
    return ControlPlaneConfig(
        host=host, port=port, token=token, store_root=store_root,
        knowledge_store_root=env.get("CONTROL_PLANE_KNOWLEDGE_STORE_ROOT") or None,
        zentra_repository_root=env.get("CONTROL_PLANE_ZENTRA_REPOSITORY_ROOT") or None,
        zentra_backend_root=env.get("CONTROL_PLANE_ZENTRA_BACKEND_ROOT") or None,
        zentra_frontend_root=env.get("CONTROL_PLANE_ZENTRA_FRONTEND_ROOT") or None,
        mission_repository_root=env.get("CONTROL_PLANE_MISSION_REPOSITORY_ROOT") or None,
        mission_branch=env.get("CONTROL_PLANE_MISSION_BRANCH") or None,
        mission_pr_title=env.get("CONTROL_PLANE_MISSION_PR_TITLE") or None,
        git_executable=env.get("CONTROL_PLANE_GIT_EXECUTABLE") or "git",
        gh_executable=env.get("CONTROL_PLANE_GH_EXECUTABLE") or "gh",
        mission_concurrency=concurrency, build_review_deadline=build_deadline,
        ci_poll_timeout_seconds=ci_timeout, ci_poll_interval_seconds=ci_interval,
        projection_cache_ttl_seconds=projection_cache_ttl,
        projection_activity_limit=projection_activity_limit,
        projection_finding_summary_max_chars=projection_finding_summary_max_chars,
        business_context_base_url=env.get("BUSINESS_CONTEXT_BASE_URL") or None,
        business_context_token=env.get("BUSINESS_CONTEXT_TOKEN") or None,
        business_context_cache_ttl_seconds=float(env.get("BUSINESS_CONTEXT_CACHE_TTL_SECONDS", "300")),
    )


class _ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _ControlPlaneHTTPServer(ThreadingHTTPServer):
    def server_close(self) -> None:
        supervisor = getattr(self, "supervisor", None)
        if supervisor is not None:
            supervisor.close()
        super().server_close()


# M8 (Command Center Live Operational Visibility) -- Correction 1 (V2)/
# Correction 7 (V3): a pure, categorical (never ordered, never numeric)
# `state -> stage` presentation mapping. This is deliberately NOT a
# second state machine: Chugel's own orchestrator/validator.py::STATES
# and TRANSITIONS remain the one and only authority over what a mission
# IS and what it may become next; this dict only decides which
# kanban-style column a given real state is drawn in. Every one of the
# 22 real states has an entry -- tests/test_jarvis_control_plane_server.py's
# StageMappingExhaustivenessTests compares this dict's keys against
# `set(orchestrator.validator.STATES)` directly (never a hand-maintained
# count) so an unmapped future state fails a test instead of silently
# falling through. "BLOCKED" itself IS a key here (so the exhaustiveness
# check covers it too) but a BLOCKED mission's *exposed* `stage` is never
# this literal value -- see `_stage_for_mission()` below, which always
# resolves a BLOCKED mission's stage via its own `priorState` instead.
_STATE_TO_STAGE: dict[str, str] = {
    "INTAKE": "INTAKE",
    "SCOPE_AWAITING_AUTHORIZATION": "SCOPING",
    "AUTHORIZED": "IN_PROGRESS",
    "BUILDING": "IN_PROGRESS",
    "VERIFYING": "IN_PROGRESS",
    "AWAITING_REVIEW": "IN_PROGRESS",
    "REVIEWING": "IN_PROGRESS",
    "CHANGES_REQUIRED": "IN_PROGRESS",
    "CORRECTING": "IN_PROGRESS",
    "PUBLISH_AWAITING_AUTHORIZATION": "PUBLISHING",
    "PUBLISHING": "PUBLISHING",
    "CI_PENDING": "PUBLISHING",
    "MERGE_AWAITING_AUTHORIZATION": "PUBLISHING",
    "MERGING": "PUBLISHING",
    "MERGED": "PUBLISHING",
    "DEPLOY_PENDING": "DEPLOYING",
    "VERIFYING_PRODUCTION": "DEPLOYING",
    "COMPLETED": "DONE",
    "BLOCKED": "BLOCKED",
    "FAILED": "FAILED",
    "CANCELLED": "FAILED",
    "ROLLED_BACK": "FAILED",
}


def _stage_for_mission(state: str, prior_state: str | None) -> str:
    """The single function that ever decides `mission.stage` -- a pure
    presentation lookup, never a decision. A BLOCKED mission always
    resolves via its own `priorState` (Correction 1/7): the mapping is
    looked up a second time, on `prior_state`, so the user sees which
    part of the real lifecycle the mission froze in, never the
    uninformative literal "BLOCKED". Falls back to the `BLOCKED` stage
    itself only if `prior_state` is missing/unrecognized (defensive --
    every real BLOCKED mission has a real prior_state derived from its
    own state_history by jarvis/status.py, but this function never
    raises regardless)."""
    if state == "BLOCKED":
        return _STATE_TO_STAGE.get(prior_state, _STATE_TO_STAGE["BLOCKED"])
    return _STATE_TO_STAGE.get(state, "FAILED")


def _correction_rounds(timeline: tuple) -> int:
    """A count, never a position: how many times this mission's own
    state_history recorded a transition INTO CORRECTING. Derived purely
    from `status.timeline` (already-allow-listed state_transition events,
    jarvis/status.py's own compute_mission_timeline()) -- no new read, no
    second traversal of state_history itself."""
    return sum(
        1 for event in timeline if event.kind == "state_transition" and event.to_state == "CORRECTING"
    )


class _ProjectionCache:
    """Short-TTL, generation-guarded (compare-and-swap) cache for the
    Command Center projection payload (M8 design V1/V2/V3/V4/V5/V6,
    A8 + Corrections 3/8/10/12).

    `_ControlPlaneHTTPServer` is a real `ThreadingHTTPServer` -- every
    request is served on its own thread -- so this must be correct under
    genuinely concurrent readers and writers, not merely under a
    single-threaded test. `_lock` here protects ONLY the tiny
    (generation, cached_*) bookkeeping below; it is never held across a
    real Chugel scan (`_build_projection()`), which keeps running fully
    in parallel across threads exactly as it always has.

    Protocol:
      - A "hit" requires BOTH: TTL not expired, AND
        `cached_generation == current generation` (Correction 12 -- the
        read side must compare generation too, or incrementing it on
        write would be a no-op).
      - Recomputing (cache miss): read the generation `g` BEFORE
        scanning Chugel (no lock held during the scan itself); after
        scanning, publish the result into the cache ONLY if the
        generation is STILL `g` (Correction 10, compare-and-swap). If a
        write invalidated the cache while this thread was scanning, the
        just-computed (now-stale) result is still returned to THIS
        request -- it was correct at the moment it was computed -- but
        is never published for a future request to see.
      - Invalidating (any successful POST, via `_dispatch()`'s own
        `finally`): increment the generation under the lock -- O(1),
        never blocks a concurrent scan in progress.
    """

    def __init__(self, ttl_seconds: float, *, clock=time.monotonic):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._generation = 0
        self._cached_generation: int | None = None
        self._cached_at: float | None = None
        self._cached_payload: dict | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self) -> dict | None:
        now = self._clock()
        with self._lock:
            if (
                self._cached_payload is not None
                and self._cached_at is not None
                and (now - self._cached_at) < self._ttl_seconds
                and self._cached_generation == self._generation
            ):
                return self._cached_payload
        return None

    def publish_if_current(self, payload: dict, generation_at_start: int) -> None:
        now = self._clock()
        with self._lock:
            if generation_at_start == self._generation:
                self._cached_payload = payload
                self._cached_generation = generation_at_start
                self._cached_at = now


def _get_projection(store: FileJarvisStore, config: ControlPlaneConfig, cache: "_ProjectionCache") -> dict:
    hit = cache.get()
    if hit is not None:
        return hit
    generation_at_start = cache.current_generation()
    payload = _build_projection(store, config)
    cache.publish_if_current(payload, generation_at_start)
    return payload


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


# Jarvis God Mode M1 -- Objective decomposition. Reuses the exact same
# MissionDraft creation primitives _handle_conversation() already uses
# for a single draft (build_draft_envelope()/store.save_draft()) N times
# instead of once -- no new draft-authority mechanism, no second state
# engine. The Objective itself carries zero execution state (see
# jarvis/models.py's own Objective docstring): the only thing this
# module ever does with one is decide whether/how many MissionDrafts to
# converge from its own already-persisted decomposition.


def _derived_decomposition_draft_id(objective_id: str, index: int) -> str:
    return str(uuid.uuid5(_DECOMPOSITION_DRAFT_ID_NAMESPACE, f"{objective_id}:{index}"))


def _decomposition_item_has_real_content(item: jarvis_conversation.DecompositionItemSuggestion) -> bool:
    """Same discipline as has_real_content below, applied per item: a
    decomposition item that is really just a title with nothing else is
    not a real proposal for that piece of work."""
    return bool(item.outcome) or bool(item.scope) or bool(item.acceptance_criteria)


def _decomposition_is_actionable(
    items: tuple[jarvis_conversation.DecompositionItemSuggestion, ...] | None,
) -> bool:
    """The fixed, non-LLM count/content check (decision #1, approved):
    exactly 2-4 items, hard-coded here, never read from configuration or
    from anything the model returned about its own count. A decomposition
    outside this range, or with even one placeholder-only item, is never
    acted on -- the turn falls through entirely to the single-draft
    OBJECTIVE path (decision #2), unchanged from M0."""
    return (
        items is not None
        and _OBJECTIVE_DECOMPOSITION_MIN_ITEMS <= len(items) <= _OBJECTIVE_DECOMPOSITION_MAX_ITEMS
        and all(_decomposition_item_has_real_content(item) for item in items)
    )


def _objective_decomposition_entries(
    objective_id: str, items: tuple[jarvis_conversation.DecompositionItemSuggestion, ...],
) -> tuple[ObjectiveDecompositionEntry, ...]:
    """Converts each model-proposed item into a durable
    ObjectiveDecompositionEntry -- reusing _merged_definition_and_open_questions(None, ...)
    per item (a brand-new draft, exactly like a fresh single-draft
    OBJECTIVE turn), so a decomposition item with an unaddressed field
    gets the exact same placeholder discipline a lone draft already
    gets, never a different one."""
    entries = []
    for index, item in enumerate(items):
        draft_id = _derived_decomposition_draft_id(objective_id, index)
        suggestion = jarvis_conversation.DraftFieldSuggestion(
            outcome=item.outcome, scope=item.scope, non_goals=item.non_goals,
            acceptance_criteria=item.acceptance_criteria, open_questions=item.open_questions,
        )
        definition, open_questions = _merged_definition_and_open_questions(None, suggestion)
        # M7 (Program-Level Planning Depth) -- translate each proposed
        # ordinal reference (depends_on_index, positional WITHIN this same
        # `items` list -- the LLM cannot know a real draft_id yet, see
        # DecompositionItemSuggestion's own docstring) into the exact same
        # deterministic real draft_id this loop derives for every entry,
        # in the SAME pass, before persisting. An out-of-range index
        # simply derives a draft_id that matches none of this
        # decomposition's own entries -- validate_objective() then rejects
        # it as DECOMPOSITION_DEPENDENCY_DANGLING, the identical treatment
        # a stray external draft_id would get. dict.fromkeys(...) dedupes
        # while preserving first-seen order, satisfying the schema's own
        # uniqueItems constraint on depends_on even if the model repeats
        # an index.
        depends_on = tuple(dict.fromkeys(
            _derived_decomposition_draft_id(objective_id, dep_index)
            for dep_index in item.depends_on_index
        ))
        entries.append(ObjectiveDecompositionEntry(
            draft_id=draft_id, title=item.title, rationale=(item.outcome or item.title),
            outcome=definition.outcome, scope=definition.scope, non_goals=definition.non_goals,
            acceptance_criteria=definition.acceptance_criteria, open_questions=open_questions,
            depends_on=depends_on,
        ))
    return tuple(entries)


def _converge_objective_decomposition(store: FileJarvisStore, objective: Objective) -> tuple[dict, ...]:
    """Phase 2 of the two-phase durable-intent-then-effect pattern (same
    discipline jarvis.mission_authorization_bridge already uses for
    draft-authorization -> Mission Record): `objective` (phase 1) is
    already durably persisted by the time this runs. This function's
    only job is to ensure every entry in its `decomposition` has a real
    MissionDraft -- creating exactly the ones that do not exist yet,
    never touching one that already does. Idempotent and crash-safe by
    construction: draft_id is deterministic (uuid5 of objective_id+index,
    never random), so calling this any number of times, from any
    trigger -- the original decomposition turn, or a later, unrelated
    projection read that happens to notice this objective is
    "decomposed" -- converges to the exact same N drafts, never
    duplicating one. Returns each entry's current draft gate projection,
    in decomposition order."""
    gates = []
    for entry in objective.decomposition:
        try:
            envelope = store.get_latest_draft(entry.draft_id)
        except DraftNotFound:
            now = _now()
            draft = MissionDraft(
                schema_version="1.0.0", draft_id=entry.draft_id, revision=1,
                created_at=now, updated_at=now, raw_intent=entry.rationale,
                mission_definition=MissionDefinitionDraft(
                    outcome=entry.outcome, scope=entry.scope, non_goals=entry.non_goals,
                    acceptance_criteria=entry.acceptance_criteria,
                ),
                research_evidence=(), risks=(), open_questions=entry.open_questions,
                repository_context=None,
                # M7: the one and only place a MissionDraft's objective_id
                # is ever set to a real value -- every other MissionDraft
                # construction site in this module leaves it at its
                # dataclass default (None).
                objective_id=objective.objective_id,
            )
            draft_envelope = build_draft_envelope(draft)
            try:
                store.save_draft(draft_envelope)
            except DraftAlreadyExists:
                pass  # a concurrent/earlier convergence pass just completed it
            envelope = store.get_latest_draft(entry.draft_id)
        gates.append(_draft_gate_projection(entry.draft_id, envelope))
    return tuple(gates)


def _handle_objective_decomposition(
    store: FileJarvisStore, message: str,
    items: tuple[jarvis_conversation.DecompositionItemSuggestion, ...],
) -> dict:
    from jarvis._safe_io import exclusive_entity_lock
    objective_id = str(uuid.uuid4())
    with exclusive_entity_lock(store.root, objective_id):
        entries = _objective_decomposition_entries(objective_id, items)
        now = _now()
        objective = Objective(
            schema_version="1.0.0", objective_id=objective_id, revision=1,
            created_at=now, updated_at=now, raw_intent=message,
            priority="unset", status="decomposed", decomposition=entries,
        )
        envelope = build_objective_envelope(objective)
        store.save_objective(envelope)  # phase 1: durable intent, before any draft exists
        gates = _converge_objective_decomposition(store, objective)  # phase 2: converge
    return {"objectiveId": objective_id, "decomposition": list(gates)}


# Jarvis God Mode M0 -- the single, fixed, non-LLM authority table for
# whether a conversational turn's classification permits a MissionDraft
# write. Deliberately a plain frozenset literal, not derived from prompt
# text, model confidence, or anything orchestrator.jarvis_conversation
# returns beyond the turn_kind string itself: the model classifies:
# this module alone decides what that classification is allowed to do.
# Approved by Jose (M0 Implementation Readiness Review): exactly
# PROPOSAL and OBJECTIVE may create/revise a draft; every other value --
# including AUTHORIZATION_ATTEMPT, which is purely informational and
# never a real authorization no matter how it is phrased -- may not.
# AMBIGUOUS (orchestrator.jarvis_conversation's own fail-closed default
# for anything absent/unknown/malformed) is deliberately absent from
# this set, never added as a fallback permission.
_DRAFT_PERMITTING_TURN_KINDS = frozenset({"PROPOSAL", "OBJECTIVE"})


def _turn_kind_permits_draft(turn_kind: str) -> bool:
    return turn_kind in _DRAFT_PERMITTING_TURN_KINDS


def _handle_conversation(
    store: FileJarvisStore, body: dict, *,
    knowledge_store: FileKnowledgeStore | None = None,
    zentra_resolver: RepositoryFreshnessResolver | None = None,
    trusted_context_builder: TrustedZentraContextBuilder | None = None,
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
        trusted_context = trusted_context_builder.build().to_prompt_payload() if trusted_context_builder is not None else None
    except Exception as exc:
        # Fail closed before the subscription dispatch. Never answer as if
        # current evidence had been consulted when context construction failed.
        raise _ApiError(503, "trusted Zentra context unavailable") from exc
    try:
        turn = jarvis_conversation.converse(
            history, current_fields, trusted_citations=trusted_citations,
            trusted_zentra_context=trusted_context,
        )
    except jarvis_conversation.SubscriptionAuthRequired as exc:
        raise _ApiError(503, f"conversational dispatch unavailable: {exc}") from exc
    except jarvis_conversation.JarvisConversationError as exc:
        raise _ApiError(502, f"conversational turn failed: {exc}") from exc

    # Jarvis God Mode M1 -- exactly like turn_kind_permits below, this is
    # a fixed, non-LLM check applied to what the model proposed, not a
    # decision the model itself makes. OBJECTIVE is the only turn_kind
    # that can even reach this branch (the model is only ever instructed
    # to populate objective_decomposition when turn_kind is OBJECTIVE --
    # see _SYSTEM_TASK -- but this check does not trust that instruction
    # was followed; it re-verifies turn_kind itself here). A decomposition
    # that is not [2,4] actionable items falls through unchanged to the
    # single-draft path below -- decision #2 (approved): no Objective is
    # ever created for a non-decomposed OBJECTIVE turn.
    if turn.turn_kind == "OBJECTIVE" and _decomposition_is_actionable(turn.objective_decomposition):
        decomposition_result = _handle_objective_decomposition(store, message, turn.objective_decomposition)
        return {
            "reply": turn.reply, "draftId": None, "gate": None,
            "objectiveId": decomposition_result["objectiveId"],
            "decomposition": decomposition_result["decomposition"],
        }

    gate = None
    suggestion = turn.suggestion
    # Jarvis God Mode M0 -- turn_kind_permits is a NEW, additional gate
    # layered in front of (never instead of) the pre-existing field-content
    # heuristic below. Before M0, "did the model fill in some fields?"
    # was the only guard, and a RECOMMENDATION or ANALYSIS_REQUEST turn
    # could satisfy it just as easily as a genuine PROPOSAL/OBJECTIVE --
    # the exact gap the M0 Implementation Readiness Review traced as the
    # root cause of an unwanted Human Gate appearing from a purely
    # informational turn. turn_kind_permits closes that gap. The
    # field-content heuristic itself is kept unchanged and ANDed with it,
    # not replaced -- removing it would reopen the placeholder-only-draft
    # bug an earlier independent review already found and fixed (see
    # test_an_entirely_empty_suggestion_object_creates_no_draft): a
    # PROPOSAL/OBJECTIVE turn whose suggestion is null, {}, or otherwise
    # carries no real content must still create no draft. A Human Gate
    # now requires BOTH "the turn was the kind of turn that may propose a
    # draft" AND "the model actually proposed something" -- strictly more
    # restrictive than either check alone, per the review's own invariant
    # that this gate may only ever narrow, never widen, when it can write.
    turn_kind_permits = _turn_kind_permits_draft(turn.turn_kind)
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
    has_proposal = turn_kind_permits and (has_real_content or has_open_questions_signal)
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

    return {
        "reply": turn.reply, "draftId": (gate["id"] if gate else draft_id), "gate": gate,
        "objectiveId": None, "decomposition": None,
    }


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


def _timeline_event_dict(event) -> dict:
    """Verification Hardening V1, Pillar 4: JSON-safe serialization of a
    jarvis.status.TimelineEvent -- a flat dict of exactly its own
    allow-listed fields, nothing more. No new data is added or inferred
    here; this is pure wire-format shaping of an already-derived,
    already allow-listed projection."""
    return {
        "at": event.at,
        "kind": event.kind,
        "fromState": event.from_state,
        "toState": event.to_state,
        "actor": event.actor,
        "role": event.role,
        "attempt": event.attempt,
        "provider": event.provider,
        "model": event.model,
        "status": event.status,
        "resultClassification": event.result_classification,
        "reasonCode": event.reason_code,
    }


def _blocked_mission_entry(mission_id: str, status) -> dict:
    """M6: one entry of the new, separate `blockedMissions` list --
    deliberately NOT folded into `gates` (reserved for the three one-time
    authorization decisions; resume/reopen is a structurally different
    kind of "needs attention" signal). Exactly the pinned field set:
    missionId, state (always the literal "BLOCKED"), priorState, and
    lastBlockedClassification (populated only for the deploy-verification
    case, None otherwise -- status.deploy_last_blocked_classification is
    already None for every other case, per jarvis/status.py's own
    allow-list discipline)."""
    return {
        "missionId": mission_id,
        "state": "BLOCKED",
        "priorState": status.prior_state,
        "lastBlockedClassification": status.deploy_last_blocked_classification,
    }


def _truncate_summary(summary: str, max_chars: int) -> str:
    """M8, Correction 2 (V2): a NEW limit introduced by this projection
    layer -- never a reuse of a Mission Record schema limit, since
    `finding.summary` has no `maxLength` in
    orchestrator/schemas/mission_record.schema.json (only `minLength: 1`).
    The Mission Record itself is never touched or re-validated here; this
    only shapes what leaves the process over HTTP. A visible truncation
    suffix so a caller never mistakes a cut-off summary for the complete
    one."""
    if len(summary) <= max_chars:
        return summary
    suffix = "…"
    keep = max(0, max_chars - len(suffix))
    return summary[:keep] + suffix


def _publish_projection(publish) -> dict:
    """M8, A1: per-mission `publish` sub-object, replacing the old
    top-level, always-fake `git` field entirely. Sourced from
    jarvis.status.PublishStatus (itself an allow-listed projection of
    record["publish"]) -- never commit_sha/pushed_at, which have no
    operational-visibility purpose here."""
    last_ci_run = None
    if publish.ci_runs:
        latest = publish.ci_runs[-1]
        last_ci_run = {"runId": latest.run_id, "conclusion": latest.conclusion, "recordedAt": latest.checked_at}
    return {"prUrl": publish.pr_url, "prNumber": publish.pr_number, "lastCiRun": last_ci_run}


def _findings_for_mission(mission_id: str, status, max_summary_chars: int) -> list[dict]:
    """M8, A2: exposes jarvis.status.MissionStatus.reviewer[].findings --
    already computed by mission_query.get_mission_status() on every
    request, previously discarded entirely. Exactly the allow-listed
    FindingStatus fields (id/severity/summary/file/lineRange/category) --
    never `blocked_reason` or any other reviewer_evidence free-text
    field. `lineRange` is exposed as the schema's own real shape
    (string | null), never a fabricated [int, int] tuple."""
    findings: list[dict] = []
    for reviewer_attempt in status.reviewer:
        for finding in reviewer_attempt.findings:
            findings.append({
                "missionId": mission_id,
                "id": finding.finding_id,
                "severity": finding.severity,
                "summary": _truncate_summary(finding.summary, max_summary_chars),
                "file": finding.file,
                "lineRange": finding.line_range,
                "category": finding.category,
            })
    return findings


def _checks_for_mission(mission_id: str, status) -> list[dict]:
    """M8, A3: the full `publish.ci_runs[]` history per mission (not just
    the latest, which `publish.lastCiRun` already covers) -- lets a
    caller see retries/flakes, not only the final conclusion."""
    return [
        {"missionId": mission_id, "runId": run.run_id, "conclusion": run.conclusion, "recordedAt": run.checked_at}
        for run in status.publish.ci_runs
    ]


# M8, A5: the closed set of dispatch_ledger statuses this projection
# treats as "still live" for the `agents` summary -- deliberately
# reusing the exact concept orchestrator/chugel.py's own reservation
# logic already treats as "a live (non-FINALIZED) ledger entry exists"
# (see e.g. chugel.py's reserve_dispatch()/mark_dispatch_in_flight()
# docstrings) rather than inventing a new "in progress" classification.
# RESERVED/IN_FLIGHT/RESULT_RECORDED are all still live in that sense;
# FINALIZED is the one terminal status for a ledger entry.
_LIVE_DISPATCH_STATUSES = frozenset({"RESERVED", "IN_FLIGHT", "RESULT_RECORDED"})


def _active_agent_for_mission(mission_id: str, status) -> dict | None:
    """M8, A5: the most recent dispatch_ledger event for this mission
    (already present in `status.timeline`, jarvis/status.py's own
    allow-listed merge of state_history + dispatch_ledger) whose role is
    emilio/emma and whose status is still live. Pure computation over an
    already-fetched projection -- no new read. None (never a fabricated
    entry) when no such event exists."""
    candidates = [
        event for event in status.timeline
        if event.kind == "dispatch" and event.role in ("emilio", "emma") and event.status in _LIVE_DISPATCH_STATUSES
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda event: event.at)
    return {
        "missionId": mission_id, "role": latest.role, "provider": latest.provider,
        "model": latest.model, "since": latest.at,
    }


# M9 (Read-Only Business Context Expansion), Section 8: the small, fixed
# set of "headline" queries exposed in the Command Center projection --
# never the full allow-list, and never any query not already declared in
# jarvis.business_context.QUERIES (fail-closed the same way Jarvis's own
# conversational use of that module is).
_BUSINESS_CONTEXT_HEADLINE_QUERIES = ("matching_failure_summary", "selection_accuracy")

# Keyed by (base_url, token) so a config change (e.g. a rotated token)
# gets a fresh client/cache rather than silently reusing a stale one --
# never keyed on anything that could collide across genuinely different
# deployments.
_business_context_clients: dict[tuple[str, str], "Any"] = {}


def _get_business_context_client(config: ControlPlaneConfig):
    if not config.business_context_base_url or not config.business_context_token:
        return None
    key = (config.business_context_base_url, config.business_context_token)
    client = _business_context_clients.get(key)
    if client is None:
        from jarvis.business_context import BusinessContextClient, UrllibBusinessContextTransport
        transport = UrllibBusinessContextTransport(config.business_context_base_url)
        client = BusinessContextClient(
            transport, config.business_context_token,
            cache_ttl_seconds=config.business_context_cache_ttl_seconds,
        )
        _business_context_clients[key] = client
    return client


def _business_context_projection(config: ControlPlaneConfig) -> dict:
    """Additive M8 projection extension (M9 Section 8): the SAME Layer 2/3
    (jarvis.business_context) the rest of Jarvis uses -- never a separate
    ad-hoc connection to the product backend. `data_as_of`/`computed_at`
    are ALWAYS present on every query entry, even when unavailable (both
    null, with an explicit error_code, never silently omitted). No gate,
    no new authority -- purely additional read-only projection content."""
    from jarvis.business_context import BusinessContextError, observe

    client = _get_business_context_client(config)
    if client is None:
        return {
            "available": False,
            "reason": "BUSINESS_CONTEXT_BASE_URL/BUSINESS_CONTEXT_TOKEN not configured",
            "queries": {},
        }
    queries: dict[str, dict] = {}
    for name in _BUSINESS_CONTEXT_HEADLINE_QUERIES:
        try:
            observado = observe(client, name)
        except BusinessContextError:
            queries[name] = {
                "value": None, "data_as_of": None, "computed_at": _now(),
                "freshness": "unavailable", "evidence_status": "sin_evidencia_suficiente",
                "provenance": "OBSERVADO", "error_code": "BUSINESS_CONTEXT_UNAVAILABLE",
            }
            continue
        queries[name] = {
            "value": observado.value,
            "data_as_of": observado.data_as_of,
            "computed_at": observado.computed_at,
            "freshness": observado.freshness,
            "evidence_status": observado.evidence_status,
            "provenance": observado.provenance,
            "error_code": None,
        }
    return {"available": True, "reason": None, "queries": queries}


def _build_projection(store: FileJarvisStore, config: ControlPlaneConfig) -> dict:
    listings = mission_query.list_missions()
    gates: list[dict] = []
    missions: list[dict] = []
    blocked_missions: list[dict] = []
    # M8: cross-mission aggregations, built incrementally in the SAME
    # loop as `missions`/`gates`/`blocked_missions` above -- no second
    # pass over `listings`, no new I/O beyond what this loop already does.
    findings: list[dict] = []
    checks: list[dict] = []
    activity: list[dict] = []
    agents: list[dict] = []
    knowledge: list[dict] = []
    for item in listings:
        # Verification Hardening V1, Pillar 3 (Progress Watchdog):
        # `status` is already fetched here for the gate-projection logic
        # below -- reused, not re-fetched, to also expose `staleness` on
        # the mission dict itself. An unreadable listing, or one whose
        # full read fails, has no determinable staleness -- "NORMAL"
        # here means "undetermined", the same fail-safe default
        # jarvis.status.compute_staleness() itself returns for an
        # unparseable timestamp, never a manufactured stall.
        status = None
        if item.readable:
            try:
                status = mission_query.get_mission_status(item.mission_id)
            except mission_query.MissionQueryError:
                status = None
        # M5 (Learning & Knowledge Continuity): small, additive, read-only
        # field on this existing endpoint -- no new architecture, no new
        # authorization. Lets a human notice a "stalled" mission (whose
        # automatic knowledge-derivation retries have stopped) and
        # manually call jarvis.mission_coordinator.
        # derive_knowledge_for_completed_mission(mission_id) directly; only
        # ever fetched for a COMPLETED, readable mission, since that is
        # the only state this feature ever touches.
        knowledge_derivation_status = None
        if item.readable and item.state == "COMPLETED":
            try:
                derivation = mission_query.get_knowledge_derivation_status(item.mission_id)
                knowledge_derivation_status = derivation["status"]
            except mission_query.MissionQueryError:
                knowledge_derivation_status = None
        mission_dict = {
            "id": item.mission_id,
            "title": item.mission_id,
            "phase": item.state or "unknown",
            # M8, Correction 1 (V2)/Correction 7 (V3): `progress` (always
            # hardcoded 0) is removed entirely, replaced by `stage` (a
            # categorical, non-numeric, non-ordered presentation label --
            # see _stage_for_mission()'s own docstring) and
            # `correctionRounds` (an honest count, 0 by default, never a
            # placeholder). An unreadable/unfetchable mission (status is
            # None) has no fetched state to categorize honestly -- `stage`
            # falls back to the same mapping keyed on item.state directly
            # (still exhaustive over all 22 real states) and
            # `correctionRounds` is 0 (no timeline was fetched).
            "stage": (
                _stage_for_mission(status.state, status.prior_state) if status is not None
                else _STATE_TO_STAGE.get(item.state, "FAILED")
            ),
            "correctionRounds": _correction_rounds(status.timeline) if status is not None else 0,
            "status": "active" if item.bucket == "running" else ("blocked" if item.bucket in ("blocked", "terminal") else "active"),
            "updatedAt": item.updated_at or "",
            "staleness": status.staleness if status is not None else "NORMAL",
            # Verification Hardening V1, Pillar 4 (Structured Progress /
            # Timeline Projection): same reused `status` fetch as
            # `staleness` above -- no new read. An unreadable/unfetchable
            # mission gets an empty timeline, never a fabricated one.
            "timeline": [_timeline_event_dict(e) for e in status.timeline] if status is not None else [],
            "knowledgeDerivationStatus": knowledge_derivation_status,
            # M8, A1: replaces the old top-level, always-fake `git` field.
            # An unreadable/unfetchable mission has no publish info to
            # report honestly -- the same all-null shape a mission whose
            # own record["publish"] is still at its INTAKE defaults would
            # produce, never a distinct "unknown" shape.
            "publish": (
                _publish_projection(status.publish) if status is not None
                else {"prUrl": None, "prNumber": None, "lastCiRun": None}
            ),
        }
        # M6: exactly one new field, exposed ONLY for a currently-BLOCKED
        # mission whose prior state was a deploy-verification state --
        # never health_check/version_check/recovery_history/last_error or
        # any other free-text/HTTP-response-body deploy content (see
        # jarvis/status.py's own MissionStatus.deploy_last_blocked_classification
        # docstring). Omitted entirely (no "deploy" key at all) for every
        # other mission, rather than present-but-null, so its mere
        # presence is itself a meaningful, narrow signal.
        if status is not None and status.deploy_last_blocked_classification is not None:
            mission_dict["deploy"] = {"lastBlockedClassification": status.deploy_last_blocked_classification}
        missions.append(mission_dict)
        if status is not None:
            gate = _pending_real_gate(item.mission_id, status)
            if gate is not None:
                gates.append(gate)
            # M6: a separate, additive "needs attention" signal -- every
            # currently-BLOCKED mission, regardless of whether it has a
            # deploy-verification classification to show. Deliberately
            # never folded into `gates` (see _blocked_mission_entry()'s
            # own docstring).
            if item.state == "BLOCKED":
                blocked_missions.append(_blocked_mission_entry(item.mission_id, status))
            # M8: cross-mission aggregations -- all pure computation over
            # the SAME already-fetched `status`, no new read per mission.
            findings.extend(_findings_for_mission(
                item.mission_id, status, config.projection_finding_summary_max_chars,
            ))
            checks.extend(_checks_for_mission(item.mission_id, status))
            activity.extend(
                {**_timeline_event_dict(event), "missionId": item.mission_id, "occurredAt": event.at}
                for event in status.timeline
            )
            active_agent = _active_agent_for_mission(item.mission_id, status)
            if active_agent is not None:
                agents.append(active_agent)
        if knowledge_derivation_status is not None:
            knowledge.append({"missionId": item.mission_id, "status": knowledge_derivation_status})
    for draft_id in store.list_pending_draft_ids():
        envelope = store.get_latest_draft(draft_id)
        gates.append(_draft_gate_projection(draft_id, envelope))
    # Jarvis God Mode M1 -- read-only, additive. No Command Center
    # Objectives UI is built in M1 (out of scope) -- this key exists so
    # an Objective's existence and decomposition are genuinely
    # observable over the same HTTP surface everything else already is,
    # without requiring a dedicated endpoint. Also where the crash-safety
    # convergence pass (_converge_objective_decomposition()) actually
    # gets a chance to run again after a restart: no new background
    # worker, no scheduler -- an ordinary projection read (already
    # polled periodically by any real caller) is what completes any
    # decomposition interrupted mid-convergence, deterministically and
    # idempotently, every time this is called.
    # M7 (Program-Level Planning Depth): built once, from the SAME
    # `listings` tuple already fetched above -- no new I/O. Keyed by
    # origin_draft_id (never mission_id itself), since that is the only
    # identifier a decomposition entry's own `depends_on` ever references.
    draft_id_to_mission_state: dict[str, str | None] = {
        item.origin_draft_id: item.state for item in listings if item.origin_draft_id is not None
    }
    objectives: list[dict] = []
    for objective_id in store.list_objective_ids():
        envelope = store.get_latest_objective(objective_id)
        objective = envelope.objective
        entry_gates = (
            _converge_objective_decomposition(store, objective) if objective.status == "decomposed" else ()
        )
        # M7: every entry of THIS objective's own decomposition is already
        # fully available in `objective.decomposition` -- no second read,
        # no jarvis.objective_query needed here (that seam exists for
        # jarvis.mission_supervisor, which does not otherwise touch
        # jarvis.storage/jarvis.objectives at all; this module already
        # holds `store` directly).
        depends_on_by_draft_id = {entry.draft_id: entry.depends_on for entry in objective.decomposition}
        decomposition_projection = []
        for entry, gate in zip(objective.decomposition, entry_gates):
            status = dependency_status(entry.depends_on, draft_id_to_mission_state, depends_on_by_draft_id)
            item = {"draftId": gate["id"], "title": entry.title, "dependsOn": list(entry.depends_on), "dependencyStatus": status}
            if status == "failed":
                item["failedRootCause"] = failed_root_cause_for(
                    entry.draft_id, draft_id_to_mission_state, depends_on_by_draft_id,
                )
            decomposition_projection.append(item)
        objectives.append({
            "id": objective.objective_id,
            "status": objective.status,
            "priority": objective.priority,
            "summary": objective.raw_intent[:240],
            "updatedAt": objective.updated_at,
            "decomposition": decomposition_projection,
        })
    now = _now()
    # M8, A4: a single cross-mission activity feed, fused from the
    # `timeline[]` already computed above for every readable mission --
    # pure computation on every request, no new storage. Sorted by
    # `occurredAt` descending (most recent first), capped to
    # `config.projection_activity_limit` (default 100) entries.
    activity.sort(key=lambda entry: entry["occurredAt"], reverse=True)
    activity = activity[: config.projection_activity_limit]
    return {
        "sequence": int(datetime.now(timezone.utc).timestamp() * 1000),
        "generatedAt": now,
        "missions": missions,
        # M8, A5: honestly empty ([]) when no mission has a live
        # Emilio/Emma dispatch right now -- never backfilled with
        # fabricated data to avoid an empty list.
        "agents": agents,
        "gates": gates,
        "blockedMissions": blocked_missions,
        "objectives": objectives,
        # M8, A2: exposes jarvis.status.MissionStatus.reviewer[].findings,
        # previously computed on every request and silently discarded.
        "findings": findings,
        # M8, A3: the full publish.ci_runs[] history per mission.
        "checks": checks,
        "activity": activity,
        # M8, A6: existing per-mission knowledgeDerivationStatus,
        # surfaced as a flat top-level list -- no new pipeline.
        "knowledge": knowledge,
        # M9 (Read-Only Business Context Expansion), Section 8: purely
        # additive -- see _business_context_projection()'s own docstring.
        "businessContext": _business_context_projection(config),
        "health": {"chugel": "connected", "stream": "polling", "latencyMs": 0, "lastSync": now},
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


def _handle_authorize(store: FileJarvisStore, supervisor: MissionSupervisor, gate_id: str, body: dict) -> dict:
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
            result = close_draft_authorization(
                store, intent, decision, workspace_base_root=supervisor.workspace_base_root,
            )
        except DraftAuthorizationRefused as exc:
            raise _ApiError(409, "; ".join(reason.code for reason in exc.reasons)) from exc
        except (DraftAuthorizationAttributionError, DraftAuthorizationDivergenceError) as exc:
            raise _ApiError(409, str(exc)) from exc
        # Mission 006: the one and only place a mission's very first wake
        # can originate -- a real, materialized human authorization just
        # created this Mission Record (at INTAKE). notify() itself is
        # inert if nothing is eligible; here it always is, since
        # close_draft_authorization() never progresses the mission itself
        # (module docstring, and mission_coordinator's own Mission 006
        # docstring) -- the supervisor's INTAKE branch is what moves it on.
        supervisor.notify()
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
    # Mission 006: a real scope/publish/merge authorization just
    # materialized -- the sole consequence of this branch (beyond the
    # gate decision mission_write itself already recorded) is telling the
    # supervisor there may be eligible work again; it re-derives what, if
    # anything, from Chugel itself on its own worker thread.
    supervisor.notify()
    now = _now()
    return {
        "id": gate_id, "missionId": mission_id, "kind": kind, "state": "authorized",
        "summary": f"{kind} authorization approved for mission {mission_id}",
        "revision": now, "requestedAt": now,
    }


def _deploy_recovery_api_error(mission_id: str, exc: mission_write.DeployRecoveryRequired) -> _ApiError:
    """M6: translates mission_write.DeployRecoveryRequired's `action`
    (an orchestrator.deploy_recovery_policy.RecoveryAction) into one of
    three distinct, honestly-worded responses -- never a single generic
    message, and never guidance the real code does not actually support.

    EXCEPTIONAL_REOPEN_REQUIRED -- the one case where a next step exists;
    tells the caller exactly which endpoint to call next.
    RECOVERY_EXHAUSTED -- states plainly that no automated path remains.
    Never suggests reopen: orchestrator.chugel.reopen_deploy_observation_window()
    itself refuses unconditionally once deploy.recovery_status is
    'exhausted' (fail-closed at the Chugel layer -- see
    docs/zentra/RECOVERY_CONTRACT_V1.md), so suggesting it here would be
    dishonest, not merely unhelpful.
    DENY -- states plainly that policy defines no automated recovery path
    for this classification. Deliberately does NOT suggest reopen, even
    though (real, pre-existing M4 behavior, documented in
    docs/zentra/RECOVERY_CONTRACT_V1.md, not something M6 fixes) a direct
    call to /reopen-deploy-observation is not itself gated on
    last_blocked_classification and would currently succeed for some DENY
    cases -- this asymmetry is not something to paper over with guidance
    that would make it look like a supported path."""
    if exc.action == RecoveryAction.EXCEPTIONAL_REOPEN_REQUIRED:
        return _ApiError(409, (
            f"mission {mission_id}: plain resume is not safe for deploy-observation "
            f"classification {exc.classification!r} -- call "
            f"POST /v1/missions/{mission_id}/reopen-deploy-observation with a real "
            "acknowledgement before this mission can move forward again"
        ))
    if exc.action == RecoveryAction.RECOVERY_EXHAUSTED:
        return _ApiError(409, (
            f"mission {mission_id}: deploy-observation recovery budget is exhausted -- "
            "no automated recovery path remains for this mission"
        ))
    return _ApiError(409, (
        f"mission {mission_id}: no automated recovery path is defined by policy for "
        f"deploy-observation classification {exc.classification!r}"
    ))


def _handle_resume(mission_id: str, supervisor: MissionSupervisor, body: dict) -> dict:
    """POST /v1/missions/{id}/resume -- jarvis.mission_write.resume_from_blocked()'s
    only HTTP caller (M6). Attribution is never taken from the request
    body -- exactly like _handle_authorize() above, decided_by is always
    the literal HUMAN_DECIDER, constructed here, never trusted from the
    caller; the literal confirmation phrase is the one thing the caller
    must supply. Never addressed by gate_id, never routed through
    _AUTHORIZE_BY_KIND -- see this module's own docstring."""
    if body.get("confirmation") != _CONFIRMATION:
        raise _ApiError(400, "explicit current authorization required")
    if mission_id not in {item.mission_id for item in mission_query.list_missions()}:
        raise _ApiError(404, "mission not found")
    decision = {
        "decided_by": HUMAN_DECIDER,
        "decided_at": _now(),
        "decision_ref": f"control-plane-resume:{mission_id}",
    }
    try:
        record = mission_write.resume_from_blocked(mission_id, decision)
    except mission_write.DeployRecoveryRequired as exc:
        raise _deploy_recovery_api_error(mission_id, exc) from exc
    except mission_write.ResumeNotEligible as exc:
        raise _ApiError(409, str(exc)) from exc
    supervisor.notify()
    return {"missionId": mission_id, "state": record["state"], "resumedAt": _now()}


def _handle_reopen_deploy_observation(mission_id: str, supervisor: MissionSupervisor, body: dict) -> dict:
    """POST /v1/missions/{id}/reopen-deploy-observation --
    jarvis.mission_write.reopen_deploy_observation_window()'s only HTTP
    caller (M6) -- the exceptional recovery path, never reachable through
    /resume. Same fixed, never-caller-supplied attribution as
    _handle_resume() above; the one thing this endpoint additionally
    requires from the caller is a real, non-empty `acknowledgement`
    string -- validated here (fail fast on an obviously-empty request)
    before ever reaching the deeper validation the real functions already
    perform (jarvis.mission_write.reopen_deploy_observation_window() ->
    orchestrator.chugel.reopen_deploy_observation_window(), which is the
    one that actually fails closed on a not-BLOCKED mission or an
    exhausted deploy.recovery_status)."""
    if body.get("confirmation") != _CONFIRMATION:
        raise _ApiError(400, "explicit current authorization required")
    acknowledgement = body.get("acknowledgement")
    if not isinstance(acknowledgement, str) or not acknowledgement.strip():
        raise _ApiError(400, "acknowledgement is required to reopen the deploy observation window")
    if mission_id not in {item.mission_id for item in mission_query.list_missions()}:
        raise _ApiError(404, "mission not found")
    try:
        record = mission_write.reopen_deploy_observation_window(
            mission_id, decided_by=HUMAN_DECIDER, acknowledgement=acknowledgement,
        )
    except mission_write.DeployReopenNotEligible as exc:
        raise _ApiError(409, str(exc)) from exc
    supervisor.notify()
    return {"missionId": mission_id, "state": record["state"], "reopenedAt": _now()}


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

    def _trusted_context_builder(self) -> TrustedZentraContextBuilder | None:
        return self.server.trusted_context_builder  # type: ignore[attr-defined]

    def _supervisor(self) -> MissionSupervisor:
        return self.server.supervisor  # type: ignore[attr-defined]

    def _projection_cache(self) -> "_ProjectionCache":
        return self.server.projection_cache  # type: ignore[attr-defined]

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
        # Jarvis God Mode M0 -- GET /v1/health is the ONLY route ever
        # dispatched before _authenticated(), and deliberately the only
        # route in this entire handler with no dependency on _config(),
        # _store(), _supervisor(), or any other server-constructed
        # object: it exists solely so a launchd-driven external process
        # supervisor (which has no bearer token and no reason to be
        # handed one) can ask "is the Control Plane's own HTTP process
        # alive?" without that question ever touching Chugel, the
        # store, adapters, or any business/mission state. Approved scope
        # (M0 Implementation Readiness Review): HTTP status plus a bare
        # liveness indicator only -- never missions, drafts, agents,
        # tokens/secrets, configuration, filesystem paths, Git state,
        # business data, or trusted context. Do not add fields here
        # without re-checking that boundary.
        if method == "GET" and self.path == "/v1/health":
            self._write_json(200, {"status": "ok"})
            return
        if not self._authenticated():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        # M8, Corrections 10/11/13/14: `response_status` is declared and
        # initialized to None BEFORE the `try` -- deliberately, so that
        # ANY branch reached inside the `try` (including the 404
        # fallback, now itself moved inside the try so the `finally`
        # below covers it too) can assign it, and so the `finally` itself
        # can never raise an UnboundLocalError even if some future branch
        # forgot to assign it (the `is not None` guard below is fail-safe
        # toward "never invalidate", never toward "crash").
        response_status: int | None = None
        try:
            if method == "GET" and self.path == "/v1/command-center/projection":
                response_status = 200
                self._write_json(response_status, _get_projection(self._store(), self._config(), self._projection_cache()))
                return
            if method == "POST" and self.path == "/v1/proposals":
                body = self._read_json_body()
                response_status = 201
                self._write_json(response_status, _handle_proposals(self._store(), body))
                return
            if method == "POST" and self.path == "/v1/conversation":
                body = self._read_json_body()
                response_status = 200
                self._write_json(response_status, _handle_conversation(
                    self._store(), body,
                    knowledge_store=self._knowledge_store(), zentra_resolver=self._zentra_resolver(),
                    trusted_context_builder=self._trusted_context_builder(),
                ))
                return
            match = re.fullmatch(r"/v1/gates/([^/]+)/authorize", self.path)
            if method == "POST" and match is not None:
                gate_id = match.group(1)
                if _UUID.fullmatch(gate_id) is None:
                    raise _ApiError(400, "gate id must be a canonical UUID")
                body = self._read_json_body()
                response_status = 200
                self._write_json(response_status, _handle_authorize(self._store(), self._supervisor(), gate_id, body))
                return
            # M6 -- addressed by mission_id, never gate_id, never routed
            # through _AUTHORIZE_BY_KIND (see this module's own docstring).
            match = re.fullmatch(r"/v1/missions/([^/]+)/resume", self.path)
            if method == "POST" and match is not None:
                mission_id = match.group(1)
                body = self._read_json_body()
                response_status = 200
                self._write_json(response_status, _handle_resume(mission_id, self._supervisor(), body))
                return
            match = re.fullmatch(r"/v1/missions/([^/]+)/reopen-deploy-observation", self.path)
            if method == "POST" and match is not None:
                mission_id = match.group(1)
                body = self._read_json_body()
                response_status = 200
                self._write_json(response_status, _handle_reopen_deploy_observation(mission_id, self._supervisor(), body))
                return
            response_status = int(HTTPStatus.NOT_FOUND)
            self._write_json(response_status, {"error": "not found"})
        except _ApiError as exc:
            response_status = exc.status
            self._write_json(response_status, {"error": exc.message})
        except Exception:  # noqa: BLE001 -- final fail-closed net: never leak internals or
            # crash the request thread on an exception this module did not
            # anticipate (including any chugel-side validation failure this
            # module has no direct import of and therefore cannot name).
            response_status = int(HTTPStatus.INTERNAL_SERVER_ERROR)
            self._write_json(response_status, {"error": "Control Plane request failed"})
        finally:
            # M8, Correction 8 (V3)/10/11/13/14: the SINGLE, structural
            # cache-invalidation point -- any successful (2xx) POST
            # invalidates the projection cache, covering every write
            # route that exists today (_handle_proposals,
            # _handle_conversation/_handle_objective_decomposition,
            # _handle_authorize, _handle_resume,
            # _handle_reopen_deploy_observation) AND any future write
            # route, with no per-handler bookkeeping to remember. A GET
            # (including the projection GET itself) never invalidates; a
            # failed write (4xx/5xx, including the 404 fallback above)
            # never invalidates, since nothing was actually mutated.
            if response_status is not None and method == "POST" and 200 <= response_status < 300:
                self._projection_cache().invalidate()

    def do_GET(self) -> None:  # noqa: N802 -- stdlib method name
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 -- stdlib method name
        self._dispatch("POST")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 -- stdlib signature
        pass  # never log request contents (may carry the bearer token or objective text)


def build_server(config: ControlPlaneConfig) -> ThreadingHTTPServer:
    server = _ControlPlaneHTTPServer((config.host, config.port), _Handler)
    server.config = config  # type: ignore[attr-defined]
    server.store = FileJarvisStore(config.store_root)  # type: ignore[attr-defined]
    # M8: one cache instance per server process, never per-request --
    # its generation counter/TTL bookkeeping must persist across requests
    # to mean anything.
    server.projection_cache = _ProjectionCache(config.projection_cache_ttl_seconds)  # type: ignore[attr-defined]
    # Constructed once at startup, not per-request: a misconfigured path
    # here must fail server startup loudly, not be swallowed inside a
    # live conversation turn.
    server.knowledge_store = (  # type: ignore[attr-defined]
        FileKnowledgeStore(config.knowledge_store_root) if config.knowledge_store_root else None
    )
    server.zentra_resolver = (  # type: ignore[attr-defined]
        RepositoryFreshnessResolver(config.zentra_repository_root) if config.zentra_repository_root else None
    )
    context_roots: dict[str, Path] = {}
    if config.zentra_backend_root is not None:
        context_roots["backend"] = config.zentra_backend_root
    if config.zentra_frontend_root is not None:
        context_roots["frontend"] = config.zentra_frontend_root
    if context_roots:
        policy = load_policy()
        discovered_gh = config.gh_executable if Path(config.gh_executable).is_absolute() else shutil.which(config.gh_executable)
        if not discovered_gh:
            raise ValueError("configured GitHub CLI executable is unavailable")
        github_query = ReadOnlyGitHubQuery(
            Path(discovered_gh).resolve(),
            frozenset(f"{repository.host}/{repository.owner}/{repository.name}" for repository in policy.repositories),
        )
        server.trusted_context_builder = TrustedZentraContextBuilder(  # type: ignore[attr-defined]
            policy, context_roots, knowledge_store=server.knowledge_store, github_query=github_query,
        )
    else:
        server.trusted_context_builder = None  # type: ignore[attr-defined]
    # Mission 006: constructed once per server, never per-request, exactly
    # like store/knowledge_store/zentra_resolver above. The INTAKE
    # mechanical transition (mission_coordinator's own Mission 006 branch)
    # needs no adapter at all, so the supervisor is always constructed --
    # but real subscription-CLI adapters (never the API-key-backed ones --
    # see orchestrator/wiring.py's own enforcement of this, unchanged
    # here) are only ever wired in when an operator has explicitly set
    # CONTROL_PLANE_MISSION_REPOSITORY_ROOT, the same opt-in signal that
    # makes a real PUBLISHING/MERGING step possible at all. Unset (every
    # existing test's ControlPlaneConfig, and any deployment that has not
    # opted in): adapters={}, so a mission that ever reaches AUTHORIZED
    # fails closed with a recorded, caught WiringError (see
    # jarvis/mission_supervisor.py's _drain_pass()) instead of silently
    # attempting a real CLI dispatch -- this is what previously kept
    # every existing control-plane test free of any real Emilio/Emma
    # invocation, and must stay true now that notify() is wired in.
    real_adapters = config.mission_repository_root is not None
    lease = None
    manager = None
    if real_adapters:
        base_root = Path(config.mission_repository_root)
        lease = acquire_workspace_supervisor_lease(base_root)
        manager = MissionWorkspaceManager(base_root, git_executable=config.git_executable, lease=lease)
    server.supervisor = MissionSupervisor(  # type: ignore[attr-defined]
        adapter_factory=build_cli_subscription_adapters if real_adapters else (lambda: {}),
        max_concurrency=config.mission_concurrency if real_adapters else 1,
        lease=lease,
        # M7 (Program-Level Planning Depth): the exact same FileJarvisStore
        # instance already constructed above for drafts/objectives -- no
        # second store, no new root, no new I/O beyond what
        # jarvis.objective_query's own two functions need.
        objective_store=server.store,
        advance_kwargs=dict(
            repository_root=config.mission_repository_root or str(Path.cwd()),
            branch=config.mission_branch or "overnight/mission",
            pr_title=config.mission_pr_title or "Mission",
            git_executable=config.git_executable,
            gh_executable=config.gh_executable,
            workspace_manager=manager,
            build_review_deadline=config.build_review_deadline,
            ci_poll_timeout_seconds=config.ci_poll_timeout_seconds,
            ci_poll_interval_seconds=config.ci_poll_interval_seconds,
        ),
    )
    return server


def main() -> None:
    config = load_config()
    server = build_server(config)
    # One-shot, non-recurring (see MissionSupervisor.recover_on_startup()'s
    # own docstring) -- exactly once per process start, here and nowhere
    # else in this module.
    server.supervisor.recover_on_startup()  # type: ignore[attr-defined]
    print(f"Jarvis Control Plane listening on http://{config.host}:{config.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
