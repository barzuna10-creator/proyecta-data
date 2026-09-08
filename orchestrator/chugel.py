"""Chugel V1 -- deterministic persistence and gate/state enforcement for
Zentra Mission Records. This module implements exactly the design in
orchestrator/CHUGEL_V1.md (Increment #5, already independently reviewed by
Emma), plus the repository-state extension explicitly authorized for
Increment #6.

Chugel is ordinary deterministic code, not an LLM-based reasoning agent
(agents/AGENT_STANDARD.md's explicit scope exclusion): no `agents/chugel/`
directory, no `CONTRACT.md`, no judgment, no authority beyond what is
mechanically derivable from its inputs. Every public function here either
persists a Mission Record mutation that `validate_mission_record()` and,
for `transition()`, `can_transition()` have already accepted, or refuses
and raises without writing anything. No function in this module invokes
David, Emilio, or Emma, touches git/GitHub/CI/Render, or reads a free-text
field to make a decision.

Scope-change sequencing (documenting Emma's non-blocking P3 finding from
orchestrator/CHUGEL_V1.md's final review, per explicit instruction rather
than fixed with new code): accepting a proposed scope change via
`decide_scope_change()` appends a new `mission_definition_history` entry,
but this does NOT itself authorize that new scope for execution. If
`human_gates.scope_authorization` was already `"approved"` for an older
mission-definition version, `validate_mission_record()`'s own existing
`_check_stale_approvals` will correctly refuse (`STALE_APPROVAL`) any
further write on the resulting record until a *separate* `decide_gate()`
call re-approves `scope_authorization` for the new, current version. This
module never invents, infers, or silently carries forward an older
approval -- a stale scope authorization stays stale by design, and a
caller must expect and perform that second, explicit `decide_gate()` call
before the mission can proceed past `SCOPE_AWAITING_AUTHORIZATION`-derived
work for the new scope.
"""

from __future__ import annotations

import contextlib
import copy
import datetime
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

from orchestrator.validator import (
    CANONICAL_SHA_RE,
    DEPLOY_BLOCKED_CLASSIFICATIONS,
    DISPATCH_RETRYABLE_CLASSIFICATIONS,
    HUMAN_DECIDER,
    validate_mission_record,
)
from orchestrator.state_machine import can_transition

try:
    import fcntl
except ImportError:  # pragma: no cover -- POSIX-only, matching this module's
    # existing O_NOFOLLOW/_fsync_directory portability stance.
    fcntl = None


# --- exception taxonomy ------------------------------------------------
# Kept deliberately small: every failure mode maps to exactly one of these.

class ChugelError(Exception):
    """Base class for every exception this module raises."""


class MissionNotFound(ChugelError):
    """mission_id is well-formed, but no record exists at its path."""


class MissionRecordCorrupt(ChugelError):
    """The on-disk file exists but is not valid JSON."""


class MissionRecordInvalid(ChugelError):
    """The on-disk file is valid JSON but fails validate_mission_record()."""

    def __init__(self, message: str, errors: tuple) -> None:
        super().__init__(message)
        self.errors = errors


class MissionRecordPathUnsafe(ChugelError):
    """A symlink (or another unsafe filesystem entry) was found where a
    regular Mission Record file was expected."""


class MissionRecordAlreadyExists(ChugelError):
    """create_mission() found something already at the destination path."""


class MissionValidationFailed(ChugelError):
    """A mutation was rejected by validate_mission_record() before being
    written -- the on-disk file is guaranteed unchanged."""

    def __init__(self, message: str, errors: tuple) -> None:
        super().__init__(message)
        self.errors = errors


class MissionTransitionRejected(ChugelError):
    """A transition() call was rejected by can_transition() -- the on-disk
    file is guaranteed unchanged."""

    def __init__(self, message: str, reasons: tuple) -> None:
        super().__init__(message)
        self.reasons = reasons


class DispatchNotEligible(ChugelError):
    """reserve_dispatch() refused before any write: wrong state for this
    role/attempt, evidence for this attempt already exists, or a live
    (non-FINALIZED) ledger entry for this exact (role, attempt) already
    exists and is not a durably-recorded retryable result. The caller must
    not infer *why* redispatch is unsafe from this exception alone beyond
    what it reports -- an unknown-provenance reservation and a genuine
    state mismatch both raise this identically, both fail closed the same
    way."""


class DispatchEntryNotFound(ChugelError):
    """mark_dispatch_in_flight()/record_dispatch_result()/finalize_dispatch()
    found no ledger entry for the given invocation_id, or found one whose
    current status does not permit the requested transition. The caller
    holds a stale or already-processed invocation_id."""


class EvidenceRejectionNotEligible(ChugelError):
    """An explicit evidence rejection did not match one unresolved,
    completed dispatch or would contradict already-persisted evidence."""


class DeployNotEligible(ChugelError):
    """M4: a deploy.* mutator was called while the mission's current state
    (or, for begin_deploy_observation(), a structurally-required prior
    field) does not permit it. Fails closed before any write, exactly like
    DispatchNotEligible."""


class DeployRecoveryNotEligible(ChugelError):
    """M4: reopen_deploy_observation_window() was called while the mission
    is not BLOCKED, or while deploy.recovery_status is already
    'exhausted' -- refuses immediately, writes nothing, in either case."""


class DeployVerificationReservationStale(ChugelError):
    """M4: finalize_deploy_verification() was called with an invocation_id
    that no longer matches deploy.dispatch.invocation_id -- a stale or
    already-finalized reservation."""


# --- module-level constants ---------------------------------------------

_MISSIONS_DIR = Path(__file__).resolve().parent / "missions"

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "mission_record.schema.json"
with open(_SCHEMA_PATH, encoding="utf-8") as _schema_file:
    _CANONICAL_SCHEMA = json.load(_schema_file)

# Loaded from the canonical schema itself, never duplicated by hand, so this
# module's path safety can never silently drift from mission_record.schema.json.
_MISSION_ID_PATTERN = re.compile(_CANONICAL_SCHEMA["properties"]["mission_id"]["pattern"])

_GATE_NAMES = ("scope_authorization", "publish_authorization", "merge_authorization")

_ATOMIC_SCOPE_REAUTHORIZATION_STATES = frozenset(
    {"INTAKE", "SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED"}
)

# Mirrors agent_invocation.py's require_eligible_invocation() expected-state
# mapping exactly -- duplicated here (not imported) because chugel.py must
# not depend on agent_invocation.py, which already depends on chugel.py.
_MISSION_ROLE_EXPECTED_STATE = {
    ("emilio", 0): "BUILDING",
    ("emilio", 1): "CORRECTING",
    ("emma", 0): "REVIEWING",
    ("emma", 1): "REVIEWING",
}

# os.O_NOFOLLOW is POSIX-only; on a platform without it this degrades to the
# pre-open os.path.islink()/Path.is_symlink() check alone (see _read_mission_record).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


# --- small pure helpers ---------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_mission_id(mission_id: Any) -> None:
    if not isinstance(mission_id, str) or not _MISSION_ID_PATTERN.fullmatch(mission_id):
        raise ValueError(f"mission_id {mission_id!r} is not a valid UUID")


def _mission_path(mission_id: str) -> Path:
    _validate_mission_id(mission_id)
    return _MISSIONS_DIR / f"{mission_id}.json"


def _default_placeholder_repository() -> dict:
    """Structurally valid, explicitly unconfirmed. isolation_confirmed is
    always False here -- never a value a caller could mistake for a real
    isolation check having already run. See record_repository_state()."""
    return {
        "worktree_path": "(unconfirmed)",
        "branch": "(unconfirmed)",
        "base_sha": "0" * 40,
        "isolation_confirmed": False,
    }


def _default_not_requested_gate() -> dict:
    return {
        "status": "not_requested",
        "requested_at": None,
        "decided_at": None,
        "decided_by": None,
        "decision_ref": None,
        "approved_for": None,
    }


# --- persistence core: read -----------------------------------------------

def _read_mission_record(mission_id: str) -> dict:
    """Fails closed at every step: unsafe path, missing file, corrupt JSON,
    and schema/cross-field invalidity are each a distinct, specific
    exception. Never partially interprets or repairs anything it reads."""
    path = _mission_path(mission_id)

    # Checked before any open() -- closes the ordinary (non-race) case.
    if path.is_symlink():
        raise MissionRecordPathUnsafe(
            f"mission {mission_id}: refusing to follow symlink at {path}"
        )
    if not path.exists():
        raise MissionNotFound(f"mission {mission_id}: no record found at {path}")

    # O_NOFOLLOW on the actual open() call is what closes the TOCTOU window
    # between the is_symlink() check above and this open -- if something
    # swapped a symlink in during that window, this open fails instead of
    # following it. Real, load-bearing protection here, not merely a
    # documented intention (closes Emma's non-blocking P3 on
    # orchestrator/CHUGEL_V1.md's final review).
    try:
        fd = os.open(str(path), os.O_RDONLY | _O_NOFOLLOW)
    except OSError as exc:
        raise MissionRecordPathUnsafe(
            f"mission {mission_id}: unsafe path at open time ({exc})"
        ) from exc

    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise MissionRecordCorrupt(
            f"mission {mission_id}: could not read file ({exc})"
        ) from exc

    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissionRecordCorrupt(
            f"mission {mission_id}: file is not valid JSON ({exc})"
        ) from exc

    result = validate_mission_record(record)
    if not result.valid:
        raise MissionRecordInvalid(
            f"mission {mission_id}: on-disk record fails validation",
            result.errors,
        )
    return record


# --- persistence core: atomic write ---------------------------------------

def _fsync_directory(dir_path: Path) -> None:
    """Best-effort durability hardening beyond this module's stated threat
    model (process crash, not power loss) -- see orchestrator/CHUGEL_V1.md
    section 5's corrective addition. POSIX-only by construction; a no-op on
    any other platform, which is a documented portability boundary, not a
    silent gap."""
    if os.name != "posix":
        return
    fd = os.open(str(dir_path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_mission_record(record: dict) -> None:
    """Write the full record atomically: temp file in the same directory,
    fsync its descriptor, os.replace() onto the final path, then fsync the
    directory (POSIX only). A reader never observes a torn write. A tmp
    file left behind by a genuine mid-write crash is an accepted,
    non-corrupting artifact (orchestrator/CHUGEL_V1.md section 13); this
    function itself always cleans its own tmp file up on any failure it
    can actually observe."""
    _MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    mission_id = record["mission_id"]
    final_path = _mission_path(mission_id)
    payload = json.dumps(record, indent=2, sort_keys=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{mission_id}.json.tmp-", dir=str(_MISSIONS_DIR)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    _fsync_directory(_MISSIONS_DIR)


# --- persistence core: cross-process mission-record write lock ------------

def _mission_lock_path(mission_id: str) -> Path:
    _validate_mission_id(mission_id)
    return _MISSIONS_DIR / f".{mission_id}.reservation.lock"


# M4: a SEPARATE, dedicated per-mission lock file from the one above --
# never the same fd/path as _mission_lock_path(). _mission_lock() is
# acquired-and-released around each single, short mission-record
# read-modify-write; this second lock is held by orchestrator/
# deploy_verifier.py for the ENTIRE duration of one production-verification
# run (which includes bounded HTTP polling and git subprocess calls, well
# outside any _mission_lock() critical section) -- see
# reserve_deploy_verification()'s own docstring below.
def _deploy_verification_lock_path(mission_id: str) -> Path:
    _validate_mission_id(mission_id)
    return _MISSIONS_DIR / f".{mission_id}.deploy_verification.lock"


# Holds the open fd for every mission this process currently holds the
# deploy-verification lock for, keyed by mission_id -- populated only by a
# successful reserve_deploy_verification() and consumed only by
# finalize_deploy_verification(). This is what lets reserve_deploy_verification()
# keep its literal `str | None` return type (the design's own required
# signature) while still keeping the lock's fd open, and therefore the
# kernel-level exclusion live, for the whole run: the fd is not local to
# either call, it lives here in between. If this process dies with an
# entry still present, the kernel releases the underlying flock() on its
# own (exactly like _mission_lock()) -- this dict itself holds no
# durable state and is never read to make a decision, only to find the fd
# to close.
_DEPLOY_VERIFICATION_LOCK_FDS: dict[str, int] = {}


@contextlib.contextmanager
def _mission_lock(mission_id: str):
    """Cross-process exclusive lock serializing EVERY read-modify-write
    mutation this module performs against one Mission Record -- not just
    reserve_dispatch(). A real OS-level advisory lock (fcntl.flock on a
    dedicated lock file), not a process-local primitive: it is effective
    between two independent `python3` processes contending for the same
    Mission Record, which plain atomic os.replace() alone does not
    provide -- os.replace() prevents a torn write, it is not
    compare-and-swap and does nothing to stop two concurrent readers from
    both computing a conflicting mutation and both successfully writing
    (a lost update).

    Generalized from the original reservation-only lock (Emma's P2-1
    finding, autonomous-runner corrective cycle): a dispatch reservation
    written under this lock could previously still be silently lost if a
    concurrent transition()/decide_gate()/record_builder_evidence()/etc.
    call raced it outside any lock at all, re-reading the pre-reservation
    record and overwriting the reservation on write. Every public mutator
    in this module now acquires this exact same per-mission lock around
    its own read-modify-write critical section, so there is exactly one
    serialization point per mission, and no conflicting mutation --
    dispatch-ledger or otherwise -- can race another. No mutator in this
    module ever calls another lock-acquiring mutator while already
    holding this lock (each critical section calls only the private,
    lock-free _read_mission_record()/_write_mission_record() and pure
    helpers) -- this is a single, non-reentrant, non-nested acquisition
    per call, so it cannot deadlock against itself.

    The kernel releases this lock automatically if the holding process
    dies while it is held, so a crash mid-mutation can never permanently
    block future mutations -- the record's own persisted content (re-read
    fresh, under the lock, by every mutator) remains the sole source of
    truth; this lock only prevents two racing writers from both believing
    they were first. POSIX-only, matching this module's existing
    O_NOFOLLOW/_fsync_directory portability stance -- a no-op (no
    exclusion at all) on a platform without fcntl, which is a documented
    portability boundary, never a silent safety claim."""
    _MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _mission_lock_path(mission_id)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- M3: cross-mission merge serialization ---------------------------------

_MERGE_LOCK_NAME = ".merge.lock"


def _merge_lock_path() -> Path:
    return _MISSIONS_DIR / _MERGE_LOCK_NAME


@contextlib.contextmanager
def merge_serialization_lock():
    """Cross-process, cross-MISSION exclusive lock serializing the one
    real concurrency hazard M3 exists to close: two missions' real
    `gh pr merge` attempts racing the same base branch at literally the
    same instant (orchestrator/merge_executor.py's own call site is the
    only intended caller). Every other publish/merge step -- push, PR
    creation, CI polling, and the pre-merge gating re-verification --
    is already per-branch and safe by construction (see
    merge_executor.py's own module docstring), and stays outside this
    lock. What IS held under it, at that one call site: the mutating
    `gh pr merge` subprocess call itself, and nothing else. Its
    immediate post-merge re-read, any M3 merge-recovery-hardening
    reconciliation of an ambiguous local outcome (bounded, read-only
    `gh pr view` polling against GitHub's own authoritative PR state --
    see merge_executor.py's own `_reconcile_ambiguous_merge_outcome()`),
    and every resulting Chugel persistence (`_block()`'s BLOCKED write,
    or the eventual MERGED write) all happen AFTER this lock is
    released -- none of that work is mutating `gh pr merge` traffic, so
    holding a lock whose only purpose is serializing that one specific
    mutation across it would add real latency to the OTHER mission's own
    merge attempt without closing any additional hazard. Those later
    Chugel writes remain safe on their own terms: each is still
    serialized against other mutators of that same mission by the
    separate, per-mission `_mission_lock()` chugel.transition() acquires
    internally, which this lock never nests with (it is fully released
    before that call happens, so there is no ordering to reason about
    between the two). Held time under THIS lock is therefore just the
    one real merge-mutation attempt -- deliberately the shortest
    duration that still closes the hazard it exists for, not "the call
    site" as a whole.

    Deliberately NOT `_mission_lock()`, and deliberately not a durable,
    Chugel-recorded reservation naming which mission currently holds it:
    `_mission_lock()` is per-mission (each mission only ever contends
    with its own past writers), but the base branch is ONE resource
    shared by every mission, so this needs a single, global lock file
    instead. A JSON-recorded "who holds the merge slot" field was
    considered and rejected during M3 design review: it would duplicate
    exactly the kind of decision-relevant state this module exists to be
    the sole owner of ("no second state engine"), and would need its own
    liveness/staleness bookkeeping to avoid a dead holder blocking
    forever. This lock needs none of that: it is a pure ordering
    primitive with zero mission-identifying content, so it cannot
    desync from any Mission Record, and -- exactly like `_mission_lock()`
    above -- the kernel releases it automatically if the holding process
    dies while it is held, so a crash mid-merge can never permanently
    block a future merge attempt by any mission. The actual outcome of a
    contended merge is still decided entirely by merge_executor.py's own
    pre-merge re-verification (head SHA / CI / mergeStateStatus), re-run
    fresh by whichever caller acquires this lock second -- this lock only
    prevents two `gh pr merge` invocations from ever being in flight at
    the same instant, it does not itself judge whether a merge is safe.

    POSIX-only, matching this module's existing O_NOFOLLOW/
    `_fsync_directory`/`_mission_lock` portability stance -- a no-op (no
    exclusion at all) on a platform without fcntl, which is a documented
    portability boundary, never a silent safety claim."""
    _MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _merge_lock_path()
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _find_ledger_index(ledger: list, invocation_id: str) -> int:
    for idx, entry in enumerate(ledger):
        if isinstance(entry, dict) and entry.get("invocation_id") == invocation_id:
            return idx
    return -1


def _finalize_ledger_entry_for_evidence(ledger: list, invocation_id: Any) -> list:
    """Pure helper: if `invocation_id` names a RESULT_RECORDED ledger
    entry, returns a new ledger list with that entry marked FINALIZED;
    otherwise returns `ledger` unchanged (covers callers/tests that never
    used the ledger at all -- fully backward compatible)."""
    if not isinstance(invocation_id, str) or not invocation_id:
        return ledger
    idx = _find_ledger_index(ledger, invocation_id)
    if idx == -1 or ledger[idx].get("status") != "RESULT_RECORDED":
        return ledger
    updated = list(ledger)
    entry = dict(updated[idx])
    entry["status"] = "FINALIZED"
    entry["updated_at"] = _now()
    updated[idx] = entry
    return updated


# --- public operations ------------------------------------------------

def create_mission(
    intent_text: str,
    mission_definition: dict,
    *,
    mission_id: str | None = None,
    repository: dict | None = None,
) -> dict:
    """Creates and persists a new INTAKE record. This is the sole creation
    path for a mission's *initial* Mission Definition -- there is no
    separate tenth operation for it (corrective decision, Increment #6):
    creation atomically establishes mission_definition_history[0] with
    version=1, source="david_intake", based_on_proposal_id=None. Fails
    closed (MissionRecordAlreadyExists) if anything -- a regular file, a
    symlink, or any other entry -- already occupies the destination path.

    `mission_definition` must supply exactly the schema's content fields
    for a mission_definition_version entry that this function does not
    itself derive: 'outcome', 'scope', 'non_goals', 'acceptance_criteria',
    'authorized_by', 'authorized_at', 'authorization_decision_ref'. As with
    decide_scope_change(), 'version', 'source', and 'based_on_proposal_id'
    are always set by this function -- never trusted from the caller,
    since all three are mechanically derivable from the fact that this is
    a fresh mission's very first version, not a matter of caller judgment.
    'authorized_by' is hard-refused before any read/write unless it is
    literally HUMAN_DECIDER, exactly like decide_gate()/decide_scope_change()
    -- the same defense-in-depth already established for every other human
    attribution field in this module.

    IMPORTANT AUTHORITY BOUNDARY: establishing the initial mission
    definition here is NOT the same thing as authorizing it for execution.
    human_gates.scope_authorization is always written at its schema
    default ('not_requested') by this function, exactly as before this
    correction -- creating a mission definition never silently approves
    the gate that authorizes building against it. A separate, explicit
    decide_gate(mission_id, 'scope_authorization', ...) call, carrying its
    own José attribution, is still required before the mission can
    legally proceed to states that require it (see
    orchestrator/CHUGEL_V1.md and this module's docstring on scope-change
    sequencing, which applies identically to the very first version).

    `repository`, if omitted, defaults to a structurally valid but
    explicitly unconfirmed placeholder (isolation_confirmed always False)
    -- it never implies isolation has already been established. Call
    record_repository_state() once real worktree/branch/base-SHA values
    and an actual isolation confirmation exist."""
    if not isinstance(intent_text, str) or not intent_text.strip():
        raise ValueError("intent_text must be a non-empty string")
    if mission_definition.get("authorized_by") != HUMAN_DECIDER:
        raise ValueError(
            f"create_mission() refuses: mission_definition['authorized_by'] must be "
            f"the literal {HUMAN_DECIDER!r}, got {mission_definition.get('authorized_by')!r}"
        )

    new_id = mission_id if mission_id is not None else str(uuid.uuid4())

    with _mission_lock(new_id):
        path = _mission_path(new_id)
        if path.is_symlink() or path.exists():
            raise MissionRecordAlreadyExists(
                f"mission {new_id}: something already exists at {path}"
            )
        return _create_mission_locked(new_id, intent_text, mission_definition, repository)


def _create_mission_locked(
    new_id: str, intent_text: str, mission_definition: dict, repository: dict | None
) -> dict:
    """The existence-check-then-write critical section of create_mission(),
    factored out only so its body can sit inside the `with _mission_lock`
    block above without an extra indentation level -- called from exactly
    one place, under the lock, with the explicit-mission_id collision
    check already done by the caller under the same lock acquisition."""
    timestamp = _now()
    initial_definition = {
        "version": 1,
        "outcome": mission_definition["outcome"],
        "scope": mission_definition["scope"],
        "non_goals": mission_definition.get("non_goals", []),
        "acceptance_criteria": mission_definition["acceptance_criteria"],
        "source": "david_intake",
        "based_on_proposal_id": None,
        "authorized_by": mission_definition["authorized_by"],
        "authorized_at": mission_definition["authorized_at"],
        "authorization_decision_ref": mission_definition["authorization_decision_ref"],
    }
    record = {
        "schema_version": "1.0.0",
        "mission_id": new_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "state": "INTAKE",
        "state_reason": "mission created",
        "state_history": [
            {
                "from_state": None,
                "to_state": "INTAKE",
                "at": timestamp,
                "actor": "jose",
                "reason": "mission created",
            }
        ],
        "intent": {"raw_text": intent_text, "captured_at": timestamp},
        "mission_definition_history": [initial_definition],
        "proposed_scope_changes": [],
        # Establishing the mission definition above never approves this --
        # always the schema default, see the authority-boundary note above.
        "human_gates": {name: _default_not_requested_gate() for name in _GATE_NAMES},
        "repository": (
            copy.deepcopy(repository)
            if repository is not None
            else _default_placeholder_repository()
        ),
        "builder_evidence": [],
        "reviewer_evidence": [],
        "dispatch_ledger": [],
        "corrective_cycle_count": 0,
        "publish": {
            "commit_sha": None,
            "pushed_at": None,
            "pr_url": None,
            "pr_number": None,
            "ci_runs": [],
        },
        "merge": {"merge_commit_sha": None, "merged_at": None},
        # M4 (Automatic Production Deployment Observation & Verification):
        # the entire deploy object is initialized here, at INTAKE, exactly
        # like every other field above -- never partially built up later.
        # deploy.dispatch is a distinct production-verification execution
        # reservation guarded by orchestrator/deploy_verifier.py's own
        # dedicated per-mission lock file, unrelated to the top-level
        # dispatch_ledger[] used for Emilio/Emma agent dispatch (see the
        # schema's own deploy/deploy_dispatch_reservation descriptions).
        "deploy": {
            "expected_sha": None,
            "deploy_confirmed_at": None,
            "health_check": {"checked_at": None, "status_code": None, "body_summary": None},
            "version_check": {"checked_at": None, "status_code": None, "body_summary": None},
            "observation_started_at": None,
            "observed_sha": None,
            "ancestry_verified": None,
            "recovery_count": 0,
            "recovery_status": "available",
            "recovery_history": [],
            "last_blocked_classification": None,
            "dispatch": {
                "invocation_id": None,
                "reserved_at": None,
                "status": None,
                "last_skip_observed_at": None,
            },
        },
        "budget": {
            "configured": None,
            "consumed": {"unit": "tokens", "amount": 0},
            "per_agent_consumed": {"david": None, "emilio": None, "emma": None},
            "exhausted": False,
        },
        # M5 (Learning & Knowledge Continuity): initialized fully here,
        # exactly like deploy above -- never partially built up later.
        # See record_knowledge_derivation_completed()/
        # record_knowledge_derivation_attempt_failed() for the only two
        # mutators of this object; neither ever touches state/state_history.
        "knowledge_derivation": {
            "status": "pending",
            "attempt_count": 0,
            "last_attempted_at": None,
            "last_error": None,
            "completed_at": None,
            "candidate_ids": [],
        },
    }

    result = validate_mission_record(record)
    if not result.valid:
        raise MissionValidationFailed(
            f"mission {new_id}: freshly created record failed validation", result.errors
        )

    _write_mission_record(record)
    return record


def get_mission(mission_id: str) -> dict:
    """Read-only. Raises MissionNotFound / MissionRecordCorrupt /
    MissionRecordInvalid / MissionRecordPathUnsafe as appropriate."""
    return _read_mission_record(mission_id)


def list_missions() -> list[dict]:
    """Return a bounded, read-only index of canonical Mission Record files.

    Candidate names are exactly ``<schema-valid mission_id>.json``. Directories
    and non-regular entries are ignored; a canonical symlink or unreadable,
    corrupt, invalid, or identity-mismatched record is represented only by a
    stable non-sensitive error code. No record payload is returned.
    """
    if not _MISSIONS_DIR.exists() or _MISSIONS_DIR.is_symlink():
        return []

    listings: list[dict] = []
    try:
        entries = sorted(_MISSIONS_DIR.iterdir(), key=lambda item: item.name)
    except OSError:
        return []

    for path in entries:
        if path.suffix != ".json" or not _MISSION_ID_PATTERN.fullmatch(path.stem):
            continue
        mission_id = path.stem
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            listings.append({
                "mission_id": mission_id, "readable": False,
                "state": None, "updated_at": None, "error_code": "MISSION_PATH_UNSAFE",
            })
            continue
        if not stat.S_ISREG(mode):
            continue

        try:
            record = _read_mission_record(mission_id)
            if record["mission_id"] != mission_id:
                raise MissionRecordInvalid("mission identity does not match filename", ())
        except MissionRecordCorrupt:
            code = "MISSION_RECORD_CORRUPT"
        except MissionRecordInvalid:
            code = "MISSION_RECORD_INVALID"
        except (MissionRecordPathUnsafe, MissionNotFound, OSError):
            code = "MISSION_PATH_UNSAFE"
        else:
            listings.append({
                "mission_id": mission_id, "readable": True,
                "state": record["state"], "updated_at": record["updated_at"],
                "error_code": None,
            })
            continue
        listings.append({
            "mission_id": mission_id, "readable": False,
            "state": None, "updated_at": None, "error_code": code,
        })
    return listings


def record_repository_state(mission_id: str, repository: dict) -> dict:
    """The only operation that replaces the placeholder repository object
    written by create_mission() with real worktree/branch/base-SHA/
    isolation values. Whether the resulting record can subsequently
    transition to BUILDING is decided entirely by the existing
    validate_mission_record()/can_transition() evidence checks -- this
    function does not itself decide isolation is real, it only records
    what the caller asserts, subject to the same validation every other
    mutation goes through."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = copy.deepcopy(record)
        mutated["repository"] = copy.deepcopy(repository)
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: repository-state update failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def record_builder_evidence(mission_id: str, evidence: dict) -> dict:
    """Appends one entry to builder_evidence[]. If evidence['attempt'] == 1,
    atomically sets corrective_cycle_count to 1 in the same mutation/write
    (never a separate call) -- see orchestrator/CHUGEL_V1.md section 11.
    An attempt == 0 entry never changes corrective_cycle_count.

    If evidence carries an 'invocation_id' matching a RESULT_RECORDED
    dispatch_ledger entry, that entry is finalized in the same
    validate/write -- completed evidence and dispatch finalization are one
    atomic operation, never two, so a crash cannot leave evidence
    persisted with its reservation still open (or vice versa). Evidence
    with no matching ledger entry (including every caller that predates
    the ledger) is written exactly as before, ledger untouched."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = copy.deepcopy(record)
        mutated["builder_evidence"] = mutated["builder_evidence"] + [copy.deepcopy(evidence)]
        if evidence.get("attempt") == 1:
            mutated["corrective_cycle_count"] = 1
        mutated["dispatch_ledger"] = _finalize_ledger_entry_for_evidence(
            mutated.get("dispatch_ledger") or [], evidence.get("invocation_id")
        )
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: builder evidence append failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def record_reviewer_evidence(mission_id: str, evidence: dict) -> dict:
    """Appends one entry to reviewer_evidence[]. Never reads verdict/
    findings content to make a decision -- stored verbatim, validated by
    the existing cross-field checks exactly like every other field.

    Finalizes the matching dispatch_ledger entry atomically with the
    evidence write, exactly like record_builder_evidence() -- see that
    function's docstring."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = copy.deepcopy(record)
        mutated["reviewer_evidence"] = mutated["reviewer_evidence"] + [copy.deepcopy(evidence)]
        mutated["dispatch_ledger"] = _finalize_ledger_entry_for_evidence(
            mutated.get("dispatch_ledger") or [], evidence.get("invocation_id")
        )
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: reviewer evidence append failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


# --- dispatch reservation ledger --------------------------------------

def reserve_dispatch(mission_id: str, *, role: str, attempt: int) -> tuple[dict, str]:
    """The only path to durably reserving a provider dispatch before it
    happens. No supported code path may call an adapter's invoke() for
    (mission_id, role, attempt) without first calling this function and
    receiving back the invocation_id it reserved.

    Atomic under a cross-process exclusive lock (_mission_lock): reads
    the record fresh, checks state-machine eligibility for (role,
    attempt), checks no evidence for this attempt already exists, and
    checks the ledger has no live (non-FINALIZED) entry for this exact
    (role, attempt) unless that entry is a durably-recorded retryable
    result -- in which case it is finalized in the same write that
    reserves the fresh attempt. All of this happens under one lock
    acquisition, so two racing processes can never both believe they
    reserved the same slot: the second to acquire the lock re-reads the
    first's already-written reservation and fails closed.

    Generates and returns a fresh invocation_id -- the caller never
    supplies one, so a reservation identity can never be forged or
    predicted outside this function. Fails closed (DispatchNotEligible)
    without writing anything if any check fails."""
    if role not in ("emilio", "emma"):
        raise ValueError(f"role {role!r} is not one of ('emilio', 'emma')")
    if type(attempt) is not int or attempt not in (0, 1):
        raise ValueError(f"attempt must be exactly the integer 0 or 1, got {attempt!r}")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)

        expected_state = _MISSION_ROLE_EXPECTED_STATE[(role, attempt)]
        if record.get("state") != expected_state:
            raise DispatchNotEligible(
                f"mission {mission_id}: {role} attempt {attempt} requires state "
                f"{expected_state!r}, got {record.get('state')!r}"
            )

        evidence_field = "builder_evidence" if role == "emilio" else "reviewer_evidence"
        if any(
            isinstance(entry, dict) and entry.get("attempt") == attempt
            for entry in record.get(evidence_field) or []
        ):
            raise DispatchNotEligible(
                f"mission {mission_id}: {evidence_field} already contains attempt "
                f"{attempt}; refusing a duplicate provider invocation"
            )

        if role == "emma":
            # Emma's independence check (consume_emma_result()) can only
            # ever run meaningfully against a builder attempt that itself
            # persisted enough infrastructure identity -- a historical
            # builder_evidence entry from before this ledger existed has
            # none. Refusing the dispatch *before* it happens (not merely
            # before the independence check fires, after a wasted real
            # provider call) is a stronger guarantee than the pre-ledger
            # design offered, not a weaker one.
            builder_entry = next(
                (
                    entry for entry in record.get("builder_evidence") or []
                    if isinstance(entry, dict) and entry.get("attempt") == attempt
                ),
                None,
            )
            if (
                not isinstance(builder_entry, dict)
                or builder_entry.get("invocation_id") is None
                or builder_entry.get("provider") is None
                or (
                    builder_entry.get("provider_session_id") is None
                    and builder_entry.get("provider_conversation_id") is None
                )
            ):
                raise DispatchNotEligible(
                    f"mission {mission_id}: builder_evidence attempt {attempt} lacks "
                    "invocation_id/provider and at least one persisted provider "
                    "identity -- Emma's independence check could never succeed "
                    "against it, so no dispatch is reserved"
                )

        ledger = list(record.get("dispatch_ledger") or [])
        live_index = None
        for idx, entry in enumerate(ledger):
            if not isinstance(entry, dict):
                continue
            if entry.get("role") != role or entry.get("attempt") != attempt:
                continue
            if entry.get("status") == "FINALIZED":
                continue
            live_index = idx
            break

        if live_index is not None:
            live_entry = ledger[live_index]
            if (
                live_entry.get("status") != "RESULT_RECORDED"
                or live_entry.get("result_classification") not in DISPATCH_RETRYABLE_CLASSIFICATIONS
            ):
                raise DispatchNotEligible(
                    f"mission {mission_id}: an existing dispatch reservation for "
                    f"role={role!r} attempt={attempt!r} has unresolved or "
                    "non-retryable execution provenance; refusing automatic redispatch"
                )
            superseded = dict(live_entry)
            superseded["status"] = "FINALIZED"
            superseded["updated_at"] = _now()
            ledger[live_index] = superseded

        invocation_id = str(uuid.uuid4())
        timestamp = _now()
        ledger.append({
            "role": role,
            "attempt": attempt,
            "invocation_id": invocation_id,
            "provider": None,
            "model": None,
            "status": "RESERVED",
            "result_classification": None,
            "reserved_at": timestamp,
            "updated_at": timestamp,
        })

        mutated = copy.deepcopy(record)
        mutated["dispatch_ledger"] = ledger
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: dispatch reservation failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated, invocation_id


def mark_dispatch_in_flight(
    mission_id: str, invocation_id: str, *, provider: str, model: str | None = None
) -> dict:
    """Transition one RESERVED ledger entry to IN_FLIGHT, recording which
    provider was actually routed to. Called immediately before the single
    authorized adapter.invoke() call.

    Acquires the same per-mission _mission_lock as every other mutator
    (Emma's P2-1 finding): invocation_id still uniquely names an entry
    only the process that reserved it can know, so there is no
    lost-update race between two callers of *this* function for the same
    invocation_id -- but without the lock, a concurrent, unrelated
    mutation (decide_gate(), transition(), etc.) on the same mission could
    still read the record before this write and overwrite it afterward,
    silently discarding the IN_FLIGHT transition. The lock closes that
    window uniformly, the same way it does for every other mutator."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        ledger = list(record.get("dispatch_ledger") or [])
        idx = _find_ledger_index(ledger, invocation_id)
        if idx == -1 or ledger[idx].get("status") != "RESERVED":
            raise DispatchEntryNotFound(
                f"mission {mission_id}: no RESERVED dispatch_ledger entry for "
                f"invocation_id {invocation_id!r}"
            )
        entry = dict(ledger[idx])
        entry["status"] = "IN_FLIGHT"
        entry["provider"] = provider
        entry["model"] = model
        entry["updated_at"] = _now()
        ledger[idx] = entry

        mutated = copy.deepcopy(record)
        mutated["dispatch_ledger"] = ledger
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: dispatch in-flight marker failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


_DIAGNOSTIC_ELIGIBLE_CLASSIFICATIONS = frozenset({"failed", "timeout", "invalid_output"})


def record_dispatch_result(
    mission_id: str, invocation_id: str, *, outcome: str, diagnostic: dict | None = None
) -> dict:
    """Transition one IN_FLIGHT ledger entry to RESULT_RECORDED, durably
    capturing the raw provider outcome before any evidence is
    constructed or written. This is the checkpoint that lets a later
    restart distinguish 'we know exactly what happened' from 'execution
    is unknown' -- and, for a retryable outcome, is exactly what
    reserve_dispatch() reads to authorize a fresh attempt.

    Structured Allow-Listed Diagnostics: when outcome is one of
    failed/timeout/invalid_output and the caller supplies a non-empty
    `diagnostic` dict (an adapter's own closed-reason-code classification
    of exactly what happened -- see orchestrator/adapters/
    codex_cli_adapter.py's and claude_cli_adapter.py's own `_result()`
    call sites, and orchestrator/wiring.py's _select_and_dispatch(),
    which is the one place `diagnostic` reaches this function), it is
    persisted verbatim onto this ledger entry, surviving both this
    process's exit and the adapter's own ephemeral-temp-directory cleanup
    that happens before this function is ever called. For any other
    outcome (completed, unavailable), or when `diagnostic` is None/empty,
    nothing is added.

    Design history (why this is a closed dict, not free text): an
    earlier corrective attempt persisted a raw, sanitized `error_detail`
    string here instead, redacting known credential/token shapes with
    regex before writing. Three independent review rounds each found a
    new secret shape the redaction missed (Bearer-scheme-only ->
    underscore-compound env-var names -> JSON-quoted keys and non-Bearer
    auth schemes) -- the structural signature of a deny-list against
    unbounded free text, which can encode a credential in unboundedly
    many shapes and can never be proven complete. This design removes
    the free text from the trust boundary entirely instead: the adapter
    that already knows exactly which of its own known failure branches
    it is in classifies that branch into a closed `reason_code` (the
    mission record schema's own enum -- this function performs no
    enum-membership check of its own; validate_mission_record() below is
    the single enforcement point, exactly like every other field this
    module writes) plus a handful of individually-typed safe fields
    (byte counts, exit codes, boolean flags, and Python exception class
    NAMES -- never an exception's message, which is exactly the kind of
    interpolated free text this redesign exists to avoid). There is
    nothing left here to sanitize: a caller cannot construct a
    `diagnostic` dict this function or the schema will accept that
    smuggles arbitrary text through, because no field is typed to allow
    it.

    Acquires _mission_lock, same reason as mark_dispatch_in_flight()."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        ledger = list(record.get("dispatch_ledger") or [])
        idx = _find_ledger_index(ledger, invocation_id)
        if idx == -1 or ledger[idx].get("status") != "IN_FLIGHT":
            raise DispatchEntryNotFound(
                f"mission {mission_id}: no IN_FLIGHT dispatch_ledger entry for "
                f"invocation_id {invocation_id!r}"
            )
        entry = dict(ledger[idx])
        entry["status"] = "RESULT_RECORDED"
        entry["result_classification"] = outcome
        if outcome in _DIAGNOSTIC_ELIGIBLE_CLASSIFICATIONS and isinstance(diagnostic, dict) and diagnostic:
            entry["diagnostic"] = diagnostic
        entry["updated_at"] = _now()
        ledger[idx] = entry

        mutated = copy.deepcopy(record)
        mutated["dispatch_ledger"] = ledger
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: dispatch result recording failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def finalize_dispatch(mission_id: str, invocation_id: str) -> dict:
    """Transition one RESULT_RECORDED ledger entry to FINALIZED with no
    other mutation -- the path for a non-completed outcome, which writes
    no evidence. A completed outcome is instead finalized atomically
    together with its evidence write inside record_builder_evidence()/
    record_reviewer_evidence(); this function must not be called for an
    invocation_id already finalized that way.

    Acquires _mission_lock, same reason as mark_dispatch_in_flight()."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        ledger = list(record.get("dispatch_ledger") or [])
        idx = _find_ledger_index(ledger, invocation_id)
        if idx == -1 or ledger[idx].get("status") != "RESULT_RECORDED":
            raise DispatchEntryNotFound(
                f"mission {mission_id}: no RESULT_RECORDED dispatch_ledger entry for "
                f"invocation_id {invocation_id!r}"
            )
        if ledger[idx].get("result_classification") == "completed":
            raise DispatchEntryNotFound(
                f"mission {mission_id}: completed dispatch {invocation_id!r} must be "
                "finalized atomically with evidence or an explicit evidence rejection"
            )
        entry = dict(ledger[idx])
        entry["status"] = "FINALIZED"
        entry["updated_at"] = _now()
        ledger[idx] = entry

        mutated = copy.deepcopy(record)
        mutated["dispatch_ledger"] = ledger
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: dispatch finalization failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_evidence_rejection(
    mission_id: str,
    invocation_id: str,
    *,
    role: str,
    attempt: int,
    rejection_code: str,
) -> dict:
    """Atomically close a completed dispatch whose evidence Chugel rejected.

    The provider result remains ``completed`` forever.  This operation records
    only the stable disposition/code, never rejected provider payload.  It is
    deliberately ineligible for an ambiguous crash window: the caller must
    identify the exact live invocation and the role/attempt it just tried to
    persist.  A later reservation may reuse that schema slot with a fresh
    invocation_id; durable attempt accounting still counts both entries.
    """
    if role not in ("emilio", "emma"):
        raise ValueError(f"role {role!r} is not one of ('emilio', 'emma')")
    if type(attempt) is not int or attempt not in (0, 1):
        raise ValueError(f"attempt must be exactly the integer 0 or 1, got {attempt!r}")
    if rejection_code != "MISSION_EVIDENCE_VALIDATION_FAILED":
        raise ValueError(f"unsupported evidence rejection code {rejection_code!r}")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != _MISSION_ROLE_EXPECTED_STATE[(role, attempt)]:
            raise EvidenceRejectionNotEligible(
                f"mission {mission_id}: state does not match role={role!r} attempt={attempt}"
            )
        ledger = list(record.get("dispatch_ledger") or [])
        idx = _find_ledger_index(ledger, invocation_id)
        if idx == -1:
            raise EvidenceRejectionNotEligible(
                f"mission {mission_id}: dispatch {invocation_id!r} does not exist"
            )
        current = ledger[idx]
        if (
            current.get("role") != role
            or current.get("attempt") != attempt
            or current.get("status") != "RESULT_RECORDED"
            or current.get("result_classification") != "completed"
        ):
            raise EvidenceRejectionNotEligible(
                f"mission {mission_id}: dispatch {invocation_id!r} is not the exact "
                "unresolved completed role/attempt"
            )
        evidence_field = "builder_evidence" if role == "emilio" else "reviewer_evidence"
        if any(
            isinstance(evidence, dict) and evidence.get("invocation_id") == invocation_id
            for evidence in record.get(evidence_field) or []
        ):
            raise EvidenceRejectionNotEligible(
                f"mission {mission_id}: dispatch {invocation_id!r} already has persisted evidence"
            )

        entry = dict(current)
        entry["status"] = "FINALIZED"
        entry["evidence_disposition"] = "rejected"
        entry["evidence_rejection_code"] = rejection_code
        entry["updated_at"] = _now()
        ledger[idx] = entry
        mutated = copy.deepcopy(record)
        mutated["dispatch_ledger"] = ledger
        mutated["updated_at"] = _now()
        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: evidence rejection failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_publish_commit(mission_id: str, commit_sha: str) -> dict:
    """Persist the infrastructure-observed publication commit identity.

    This operation records identity only; it never runs git, pushes, creates
    or updates a pull request, or grants merge authorization. Publication is
    eligible either in PUBLISHING before any remote effect, or in
    MERGE_AWAITING_AUTHORIZATION for legacy crash repair. The first canonical SHA becomes
    immutable, so neither a duplicate call nor a conflicting caller-controlled
    value can rewrite the identity later.

    The SHA is checked before reading the Mission Record. After the validated
    read, the complete mutation is assembled in memory, canonically validated
    once, and written once through Chugel's existing atomic writer.
    """
    if not isinstance(commit_sha, str) or CANONICAL_SHA_RE.fullmatch(commit_sha) is None:
        raise ValueError("commit_sha must be a canonical 40-character lowercase SHA")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") not in {"PUBLISHING", "MERGE_AWAITING_AUTHORIZATION"}:
            raise ValueError(
                f"mission {mission_id}: publication identity may only be recorded in "
                f"state 'PUBLISHING' or 'MERGE_AWAITING_AUTHORIZATION', got {record.get('state')!r}"
            )
        existing_sha = (record.get("publish") or {}).get("commit_sha")
        if existing_sha is not None:
            raise ValueError(
                f"mission {mission_id}: publication commit identity is already recorded"
            )

        mutated = copy.deepcopy(record)
        mutated["publish"] = dict(mutated["publish"])
        mutated["publish"]["commit_sha"] = commit_sha
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: publication commit update failed validation",
                result.errors,
            )

        _write_mission_record(mutated)
        return mutated


def record_publish_pr(mission_id: str, pr_url: str, pr_number: int) -> dict:
    """Persist the infrastructure-observed pull-request identity. Mission
    004 addition, same first-write-wins/state-gated pattern as
    record_publish_commit() above -- eligible only while PUBLISHING (the
    PR is opened, or found already open, before CI is ever polled), and
    the first recorded (pr_url, pr_number) pair is immutable: neither a
    duplicate call nor a conflicting value can rewrite it later. This is
    the durable idempotency signal orchestrator/publish_executor.py's
    check-before-create step reads before ever calling `gh pr create`
    again."""
    if not isinstance(pr_url, str) or not pr_url:
        raise ValueError("pr_url must be a non-empty string")
    if type(pr_number) is not int or pr_number < 1:
        raise ValueError("pr_number must be a positive integer")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "PUBLISHING":
            raise ValueError(
                f"mission {mission_id}: PR identity may only be recorded in "
                f"state 'PUBLISHING', got {record.get('state')!r}"
            )
        existing_number = (record.get("publish") or {}).get("pr_number")
        if existing_number is not None:
            raise ValueError(f"mission {mission_id}: PR identity is already recorded")

        mutated = copy.deepcopy(record)
        mutated["publish"] = dict(mutated["publish"])
        mutated["publish"]["pr_url"] = pr_url
        mutated["publish"]["pr_number"] = pr_number
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: PR identity update failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def record_ci_run(mission_id: str, *, run_id: str, conclusion: str) -> dict:
    """Append one observed CI run result. Mission 004 addition. Eligible
    only while CI_PENDING -- unlike record_publish_pr()/
    record_publish_commit(), this is append-only, not first-write-wins,
    since orchestrator/publish_executor.py's bounded poll may durably
    record more than one intermediate observation (e.g. "pending" before
    a later "success"/"failure") before reaching a terminal conclusion."""
    if conclusion not in ("pending", "success", "failure", "cancelled", "timed_out"):
        raise ValueError(f"conclusion {conclusion!r} is not a recognized CI conclusion")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "CI_PENDING":
            raise ValueError(
                f"mission {mission_id}: a CI run may only be recorded in "
                f"state 'CI_PENDING', got {record.get('state')!r}"
            )

        mutated = copy.deepcopy(record)
        mutated["publish"] = dict(mutated["publish"])
        mutated["publish"]["ci_runs"] = mutated["publish"]["ci_runs"] + [{
            "run_id": run_id, "conclusion": conclusion, "checked_at": _now(),
        }]
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: CI run append failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def record_merge_commit(mission_id: str, merge_commit_sha: str) -> dict:
    """Persist the infrastructure-observed merge commit identity. Mission
    004 addition, same first-write-wins/state-gated/immutable pattern as
    record_publish_commit() -- eligible only while MERGING, and the first
    recorded SHA is immutable. Sets merge.merged_at in the same write, so
    a reader never observes a non-null merge_commit_sha with a null
    merged_at or vice versa."""
    if not isinstance(merge_commit_sha, str) or CANONICAL_SHA_RE.fullmatch(merge_commit_sha) is None:
        raise ValueError("merge_commit_sha must be a canonical 40-character lowercase SHA")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "MERGING":
            raise ValueError(
                f"mission {mission_id}: merge commit identity may only be recorded in "
                f"state 'MERGING', got {record.get('state')!r}"
            )
        existing_sha = (record.get("merge") or {}).get("merge_commit_sha")
        if existing_sha is not None:
            raise ValueError(f"mission {mission_id}: merge commit identity is already recorded")

        mutated = copy.deepcopy(record)
        mutated["merge"] = dict(mutated["merge"])
        mutated["merge"]["merge_commit_sha"] = merge_commit_sha
        mutated["merge"]["merged_at"] = _now()
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: merge commit update failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def _apply_gate_decision(record: dict, gate_name: str, decision: dict) -> dict:
    """Return a fresh record with one gate replaced; never reads or writes."""
    mutated = copy.deepcopy(record)
    mutated["human_gates"] = dict(mutated["human_gates"])
    mutated["human_gates"][gate_name] = copy.deepcopy(decision)
    mutated["updated_at"] = _now()
    return mutated


def decide_gate(mission_id: str, gate_name: str, decision: dict) -> dict:
    """The only path to setting a human_gates.<name> status. Hard-refuses
    before any read/write unless decision['decided_by'] is literally
    HUMAN_DECIDER -- string equality, no normalization, no alias list."""
    if gate_name not in _GATE_NAMES:
        raise ValueError(f"gate_name {gate_name!r} is not one of {_GATE_NAMES}")
    if decision.get("decided_by") != HUMAN_DECIDER:
        raise ValueError(
            f"decide_gate() refuses: decided_by must be the literal "
            f"{HUMAN_DECIDER!r}, got {decision.get('decided_by')!r}"
        )

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = _apply_gate_decision(record, gate_name, decision)

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: gate decision failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def propose_scope_change(mission_id: str, proposal: dict) -> dict:
    """Appends a proposal at status 'pending_human_decision' only -- this
    function structurally cannot write an already-'accepted'/'rejected'
    proposal; only decide_scope_change() can change a proposal's status."""
    if proposal.get("status") != "pending_human_decision":
        raise ValueError(
            "propose_scope_change() only ever writes status='pending_human_decision'; "
            "use decide_scope_change() to accept or reject a proposal"
        )

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        proposal_id = proposal.get("proposal_id")
        if any(
            existing.get("proposal_id") == proposal_id
            for existing in record["proposed_scope_changes"]
            if isinstance(existing, dict)
        ):
            raise ValueError(
                f"mission {mission_id}: proposal_id {proposal_id!r} already exists"
            )
        mutated = copy.deepcopy(record)
        mutated["proposed_scope_changes"] = mutated["proposed_scope_changes"] + [
            copy.deepcopy(proposal)
        ]
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: proposed scope change failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def decide_scope_change(mission_id: str, proposal_id: str, decision: dict) -> dict:
    """The only path to moving a proposal to 'accepted'/'rejected'. Hard-
    refuses before any read/write unless decision['decided_by'] is
    literally HUMAN_DECIDER. On acceptance, atomically both marks the
    proposal accepted and appends the resulting mission_definition_history
    entry in the same mutation/write -- never two separate calls, since a
    record with only one half of that change would fail
    validate_mission_record()'s existing consistency checks anyway.

    decision['mission_definition_entry'] must carry every
    mission_definition_version field this function does not itself derive
    (outcome, scope, non_goals, acceptance_criteria, authorized_by,
    authorized_at, authorization_decision_ref) -- 'version', 'source', and
    'based_on_proposal_id' are always set by this function, never trusted
    from the caller (even if present in the payload, they are overwritten),
    since all three are mechanically derivable from the fact that this is
    a re-plan accepted from an existing proposal (monotonic count;
    source is always "david_replan" here, exactly as create_mission()'s
    source is always "david_intake"; based_on_proposal_id is always the
    proposal_id this call is already scoped to) rather than a human
    decision requiring caller judgment.

    Accepting a scope change here does NOT itself authorize the new scope
    for execution -- see this module's docstring on scope-change
    sequencing and orchestrator/CHUGEL_V1.md."""
    if decision.get("decided_by") != HUMAN_DECIDER:
        raise ValueError(
            f"decide_scope_change() refuses: decided_by must be the literal "
            f"{HUMAN_DECIDER!r}, got {decision.get('decided_by')!r}"
        )
    status = decision.get("status")
    if status not in ("accepted", "rejected"):
        raise ValueError(
            "decide_scope_change() requires decision['status'] to be 'accepted' or 'rejected'"
        )

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = _apply_scope_change_decision(record, proposal_id, decision)

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: scope-change decision failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated


def _apply_scope_change_decision(record: dict, proposal_id: str, decision: dict) -> dict:
    """Return a fresh record with one still-pending proposal decided.

    This pure helper never reads, validates, or writes. Terminal proposal
    decisions are immutable: only ``pending_human_decision`` may become
    accepted or rejected.
    """
    mutated = copy.deepcopy(record)
    status = decision["status"]

    proposals = list(mutated["proposed_scope_changes"])
    index = next(
        (i for i, p in enumerate(proposals) if p.get("proposal_id") == proposal_id), None
    )
    if index is None:
        raise ValueError(
            f"mission {mutated['mission_id']}: no proposal with proposal_id={proposal_id!r}"
        )

    proposal = copy.deepcopy(proposals[index])
    if proposal.get("status") != "pending_human_decision":
        raise ValueError(
            f"mission {mutated['mission_id']}: proposal {proposal_id!r} is already terminal "
            f"with status {proposal.get('status')!r}"
        )
    proposal["status"] = status
    proposal["decided_by"] = decision["decided_by"]
    proposal["decided_at"] = decision.get("decided_at")

    if status == "accepted":
        if "mission_definition_entry" not in decision:
            raise ValueError(
                "decide_scope_change() acceptance requires decision['mission_definition_entry']"
            )
        entry = copy.deepcopy(decision["mission_definition_entry"])
        entry["version"] = len(mutated["mission_definition_history"]) + 1
        entry["source"] = "david_replan"
        entry["based_on_proposal_id"] = proposal_id
        proposal["resulting_mission_definition_version"] = entry["version"]
        mutated["mission_definition_history"] = mutated["mission_definition_history"] + [entry]
    else:
        proposal["resulting_mission_definition_version"] = None

    proposals[index] = proposal
    mutated["proposed_scope_changes"] = proposals
    mutated["updated_at"] = _now()
    return mutated


def decide_scope_change_and_reauthorize(
    mission_id: str,
    proposal_id: str,
    scope_decision: dict,
    gate_decision: dict,
) -> dict:
    """Atomically accept a pending scope change and authorize its version.

    The two decisions are applied to one in-memory record, validated once,
    and written once. This operation is pre-execution only; it never carries
    build/review evidence across mission-definition versions or rewinds state.
    """
    if scope_decision.get("status") != "accepted":
        raise ValueError(
            "decide_scope_change_and_reauthorize() requires "
            "scope_decision['status'] == 'accepted'"
        )
    if gate_decision.get("status") != "approved":
        raise ValueError(
            "decide_scope_change_and_reauthorize() requires "
            "gate_decision['status'] == 'approved'"
        )
    if scope_decision.get("decided_by") != HUMAN_DECIDER:
        raise ValueError(
            "decide_scope_change_and_reauthorize() requires scope_decision "
            f"attributed to {HUMAN_DECIDER!r}"
        )
    if gate_decision.get("decided_by") != HUMAN_DECIDER:
        raise ValueError(
            "decide_scope_change_and_reauthorize() requires gate_decision "
            f"attributed to {HUMAN_DECIDER!r}"
        )

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") not in _ATOMIC_SCOPE_REAUTHORIZATION_STATES:
            raise ValueError(
                f"mission {mission_id}: atomic scope reauthorization is not allowed in "
                f"state {record.get('state')!r}"
            )
        if record.get("builder_evidence") != [] or record.get("reviewer_evidence") != []:
            raise ValueError(
                f"mission {mission_id}: atomic scope reauthorization refuses to carry "
                "builder/reviewer evidence across mission-definition versions"
            )
        if record.get("corrective_cycle_count") != 0:
            raise ValueError(
                f"mission {mission_id}: atomic scope reauthorization requires "
                "corrective_cycle_count == 0"
            )

        mutated = _apply_scope_change_decision(record, proposal_id, scope_decision)
        mutated = _apply_gate_decision(mutated, "scope_authorization", gate_decision)

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: atomic scope-change reauthorization failed validation",
                result.errors,
            )

        _write_mission_record(mutated)
        return mutated


# --- M4: production deploy observation & verification ---------------------
# Every mutator below follows this module's existing pattern exactly:
# read fresh under _mission_lock, deepcopy, mutate, validate_mission_record(),
# atomic write. None of them ever calls a Render API, triggers a deploy, or
# performs a rollback/redeploy/restart/config mutation -- this is read-only
# observation/verification bookkeeping only, matching orchestrator/
# deploy_verifier.py's own read-only design.

MAX_DEPLOY_RECOVERY_ATTEMPTS = 3


def begin_deploy_observation(mission_id: str) -> dict:
    """The only path from MERGED to DEPLOY_PENDING. Reads
    merge.merge_commit_sha (already required, structurally, for MERGED --
    see validator._evidence_merged_or_later() -- so its absence here would
    mean the record was already internally invalid; guarded anyway, never
    assumed). Sets deploy.expected_sha and deploy.observation_started_at in
    the SAME write that performs the MERGED -> DEPLOY_PENDING transition --
    never two separate calls, so a crash between them is impossible by
    construction. No gate: this is the automatic, system-attributed
    ("chugel") edge the M4 design explicitly authorizes -- merge_authorization
    already the human authority over the production-affecting action."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "MERGED":
            raise DeployNotEligible(
                f"mission {mission_id}: begin_deploy_observation() requires state "
                f"'MERGED', got {record.get('state')!r}"
            )
        merge_sha = (record.get("merge") or {}).get("merge_commit_sha")
        if not merge_sha:
            raise DeployNotEligible(
                f"mission {mission_id}: merge.merge_commit_sha is missing at MERGED -- "
                "structurally impossible given the MERGED evidence check, refusing anyway"
            )

        check = can_transition(record, "DEPLOY_PENDING")
        if not check.allowed:
            raise MissionTransitionRejected(
                f"mission {mission_id}: 'MERGED' -> 'DEPLOY_PENDING' not allowed",
                check.reasons,
            )

        mutated = copy.deepcopy(record)
        timestamp = _now()
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["expected_sha"] = merge_sha
        mutated["deploy"]["observation_started_at"] = timestamp
        mutated["state_history"] = mutated["state_history"] + [{
            "from_state": mutated["state"], "to_state": "DEPLOY_PENDING",
            "at": timestamp, "actor": "chugel",
            "reason": "begin production deploy observation",
        }]
        mutated["state"] = "DEPLOY_PENDING"
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: begin_deploy_observation() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def _deploy_http_check_payload(*, checked_at, status_code, body_summary) -> dict:
    return {"checked_at": checked_at, "status_code": status_code, "body_summary": body_summary}


def record_deploy_version_check(
    mission_id: str, *, checked_at: str, status_code: int | None, body_summary: str | None
) -> dict:
    """Overwritable (not first-write-wins), unlike record_publish_commit()/
    record_merge_commit() -- orchestrator/deploy_verifier.py's bounded
    /version poll may durably record more than one intermediate
    observation before ancestry is ever confirmed. Eligible while
    DEPLOY_PENDING (the /version poll itself) or VERIFYING_PRODUCTION (a
    later defensive re-check during the /health phase)."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") not in ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"):
            raise DeployNotEligible(
                f"mission {mission_id}: record_deploy_version_check() requires state "
                f"'DEPLOY_PENDING' or 'VERIFYING_PRODUCTION', got {record.get('state')!r}"
            )
        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["version_check"] = _deploy_http_check_payload(
            checked_at=checked_at, status_code=status_code, body_summary=body_summary
        )
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_version_check() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_deploy_health_check(
    mission_id: str, *, checked_at: str, status_code: int | None, body_summary: str | None
) -> dict:
    """Overwritable, same reason as record_deploy_version_check(). Eligible
    only while VERIFYING_PRODUCTION -- /health is never polled before
    ancestry has already been confirmed."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "VERIFYING_PRODUCTION":
            raise DeployNotEligible(
                f"mission {mission_id}: record_deploy_health_check() requires state "
                f"'VERIFYING_PRODUCTION', got {record.get('state')!r}"
            )
        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["health_check"] = _deploy_http_check_payload(
            checked_at=checked_at, status_code=status_code, body_summary=body_summary
        )
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_health_check() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_deploy_ancestry_result(mission_id: str, *, observed_sha: str, ancestry_verified: bool) -> dict:
    """Not explicitly named in the M4 design's own enumerated mutator list,
    but structurally required: deploy.observed_sha/deploy.ancestry_verified
    must be written by *something*, under the same state-eligibility-guard
    discipline as every other deploy.* writer here -- this is that writer,
    named and shaped to match record_deploy_version_check()/
    record_deploy_health_check() exactly (overwritable, single state guard,
    no side transition). Eligible only while DEPLOY_PENDING -- ancestry is
    resolved during the /version poll phase, before the DEPLOY_PENDING ->
    VERIFYING_PRODUCTION transition, and is never re-decided afterward by
    this function (a later defensive re-check during VERIFYING_PRODUCTION,
    if the verifier chooses to do one, is recorded as a BLOCKED
    classification via record_deploy_blocked(), never by overwriting this
    field after the fact)."""
    if not isinstance(observed_sha, str) or CANONICAL_SHA_RE.fullmatch(observed_sha) is None:
        raise ValueError("observed_sha must be a canonical 40-character lowercase SHA")
    if type(ancestry_verified) is not bool:
        raise ValueError("ancestry_verified must be a bool")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "DEPLOY_PENDING":
            raise DeployNotEligible(
                f"mission {mission_id}: record_deploy_ancestry_result() requires state "
                f"'DEPLOY_PENDING', got {record.get('state')!r}"
            )
        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["observed_sha"] = observed_sha
        mutated["deploy"]["ancestry_verified"] = ancestry_verified
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_ancestry_result() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_deploy_confirmed(mission_id: str, *, confirmed_at: str) -> dict:
    """The only path from VERIFYING_PRODUCTION to COMPLETED. Sets
    deploy.deploy_confirmed_at in the same write that performs the
    transition -- never two separate calls. validator._evidence_completed()
    independently re-verifies the full evidence contract (ancestry,
    version_check, health_check) before allowing this transition to
    actually land; this function does not itself decide that evidence is
    sufficient, exactly like every other transition-performing mutator in
    this module."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "VERIFYING_PRODUCTION":
            raise DeployNotEligible(
                f"mission {mission_id}: record_deploy_confirmed() requires state "
                f"'VERIFYING_PRODUCTION', got {record.get('state')!r}"
            )

        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["deploy_confirmed_at"] = confirmed_at
        timestamp = _now()
        mutated["state_history"] = mutated["state_history"] + [{
            "from_state": mutated["state"], "to_state": "COMPLETED",
            "at": timestamp, "actor": "chugel", "reason": "production deploy verified",
        }]
        mutated["state"] = "COMPLETED"
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_confirmed() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_deploy_blocked(mission_id: str, *, classification: str, reason: str) -> dict:
    """The ONLY writer of deploy.last_blocked_classification -- the
    structured field orchestrator/deploy_recovery_policy.py's
    recovery_action_for() keys off, never parsed from state_history's own
    free-text `reason`. Eligible while DEPLOY_PENDING or
    VERIFYING_PRODUCTION; performs the transition to BLOCKED, actor
    'chugel', in the same write."""
    if classification not in DEPLOY_BLOCKED_CLASSIFICATIONS:
        raise ValueError(
            f"classification {classification!r} is not one of {sorted(DEPLOY_BLOCKED_CLASSIFICATIONS)}"
        )

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") not in ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"):
            raise DeployNotEligible(
                f"mission {mission_id}: record_deploy_blocked() requires state "
                f"'DEPLOY_PENDING' or 'VERIFYING_PRODUCTION', got {record.get('state')!r}"
            )

        check = can_transition(record, "BLOCKED")
        if not check.allowed:
            raise MissionTransitionRejected(
                f"mission {mission_id}: {record.get('state')!r} -> 'BLOCKED' not allowed",
                check.reasons,
            )

        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["last_blocked_classification"] = classification
        timestamp = _now()
        mutated["state_history"] = mutated["state_history"] + [{
            "from_state": mutated["state"], "to_state": "BLOCKED",
            "at": timestamp, "actor": "chugel", "reason": reason,
        }]
        mutated["state"] = "BLOCKED"
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_blocked() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def reopen_deploy_observation_window(mission_id: str, *, decided_by: str, acknowledgement: str) -> dict:
    """The exceptional recovery path -- requires the same literal,
    current-turn HUMAN_DECIDER attribution as decide_gate()/create_mission(),
    even though this is deliberately NOT one of the three normal
    human_gates (the M4 design's own reasoning: merge_authorization is
    already the human authority over the production-affecting action).
    Refuses immediately, before any read/write beyond the initial fresh
    read, if deploy.recovery_status is already 'exhausted' -- no fourth
    reopen is ever possible.

    Appends one entry to deploy.recovery_history (append-only) capturing
    the FULL PRIOR window's evidence verbatim, then resets every
    current-window field (expected_sha is the one field left untouched --
    immutable once set by begin_deploy_observation()). Always transitions
    BLOCKED -> DEPLOY_PENDING, regardless of whether BLOCKED was entered
    from DEPLOY_PENDING or VERIFYING_PRODUCTION -- a fresh window always
    restarts from the /version poll, never resumes mid-/health."""
    if decided_by != HUMAN_DECIDER:
        raise ValueError(
            f"reopen_deploy_observation_window() refuses: decided_by must be the literal "
            f"{HUMAN_DECIDER!r}, got {decided_by!r}"
        )
    if not isinstance(acknowledgement, str) or not acknowledgement.strip():
        raise ValueError("acknowledgement must be a non-empty string")

    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") != "BLOCKED":
            raise DeployRecoveryNotEligible(
                f"mission {mission_id}: reopen_deploy_observation_window() requires state "
                f"'BLOCKED', got {record.get('state')!r}"
            )
        deploy = record.get("deploy") or {}
        if deploy.get("recovery_status") != "available":
            raise DeployRecoveryNotEligible(
                f"mission {mission_id}: deploy.recovery_status is "
                f"{deploy.get('recovery_status')!r}, not 'available' -- no further reopen "
                "is permitted"
            )

        check = can_transition(record, "DEPLOY_PENDING")
        if not check.allowed:
            raise MissionTransitionRejected(
                f"mission {mission_id}: 'BLOCKED' -> 'DEPLOY_PENDING' not allowed",
                check.reasons,
            )

        timestamp = _now()
        recovery_entry = {
            "recovered_at": timestamp,
            "decided_by": decided_by,
            "acknowledgement": acknowledgement,
            "prior_blocked_reason": deploy.get("last_blocked_classification"),
            "prior_observation_started_at": deploy.get("observation_started_at"),
            "prior_observed_sha": deploy.get("observed_sha"),
            "prior_ancestry_verified": deploy.get("ancestry_verified"),
            "prior_version_check": copy.deepcopy(deploy.get("version_check")),
            "prior_health_check": copy.deepcopy(deploy.get("health_check")),
        }

        new_count = deploy.get("recovery_count", 0) + 1

        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["recovery_history"] = mutated["deploy"]["recovery_history"] + [recovery_entry]
        mutated["deploy"]["observation_started_at"] = timestamp
        mutated["deploy"]["observed_sha"] = None
        mutated["deploy"]["ancestry_verified"] = None
        mutated["deploy"]["version_check"] = _deploy_http_check_payload(
            checked_at=None, status_code=None, body_summary=None
        )
        mutated["deploy"]["health_check"] = _deploy_http_check_payload(
            checked_at=None, status_code=None, body_summary=None
        )
        mutated["deploy"]["deploy_confirmed_at"] = None
        mutated["deploy"]["last_blocked_classification"] = None
        mutated["deploy"]["recovery_count"] = new_count
        if new_count >= MAX_DEPLOY_RECOVERY_ATTEMPTS:
            mutated["deploy"]["recovery_status"] = "exhausted"

        mutated["state_history"] = mutated["state_history"] + [{
            "from_state": mutated["state"], "to_state": "DEPLOY_PENDING",
            "at": timestamp, "actor": "chugel",
            "reason": f"deploy observation window reopened by {decided_by}: {acknowledgement}",
        }]
        mutated["state"] = "DEPLOY_PENDING"
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: reopen_deploy_observation_window() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def reserve_deploy_verification(mission_id: str) -> str | None:
    """Kernel-proven, single-live-runner reservation for one production
    verification run. Under _mission_lock (the ordinary mission-record
    lock), checks state eligibility and then attempts a NON-BLOCKING
    exclusive fcntl.flock() on a SEPARATE, dedicated per-mission lock file
    (_deploy_verification_lock_path(), never the same path _mission_lock()
    itself uses). On BlockingIOError (a live process already holds it) --
    returns None without mutating the record at all; the caller should
    skip this dispatch cycle (optionally calling
    record_deploy_verification_skip()). On success, this process now holds
    the lock: its fd is kept open in this module's own
    _DEPLOY_VERIFICATION_LOCK_FDS, not released until
    finalize_deploy_verification() runs or this process dies (kernel
    auto-release, exactly like _mission_lock()) -- so the exclusion is
    real and kernel-enforced for the ENTIRE verification run, including
    the bounded HTTP polling and git subprocess calls orchestrator/
    deploy_verifier.py performs far outside this function's own
    _mission_lock critical section. A fresh invocation_id (UUID4) is
    written to deploy.dispatch in the same write that acquires the lock."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record.get("state") not in ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"):
            raise DeployNotEligible(
                f"mission {mission_id}: reserve_deploy_verification() requires state "
                f"'DEPLOY_PENDING' or 'VERIFYING_PRODUCTION', got {record.get('state')!r}"
            )

        _MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = _deploy_verification_lock_path(mission_id)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return None

        invocation_id = str(uuid.uuid4())
        timestamp = _now()
        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["dispatch"] = {
            "invocation_id": invocation_id,
            "reserved_at": timestamp,
            "status": "reserved",
            "last_skip_observed_at": mutated["deploy"]["dispatch"].get("last_skip_observed_at"),
        }
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise MissionValidationFailed(
                f"mission {mission_id}: reserve_deploy_verification() failed validation", result.errors
            )
        _write_mission_record(mutated)
        _DEPLOY_VERIFICATION_LOCK_FDS[mission_id] = fd
        return invocation_id


def finalize_deploy_verification(mission_id: str, invocation_id: str) -> dict:
    """Releases the kernel-level lock reserve_deploy_verification() took
    (closing the fd this module has been holding open in
    _DEPLOY_VERIFICATION_LOCK_FDS since reservation) and marks
    deploy.dispatch.status 'completed', but ONLY if invocation_id still
    matches deploy.dispatch.invocation_id -- a stale or already-finalized
    invocation_id fails closed (DeployVerificationReservationStale) without
    mutating the record. The lock release happens regardless of whether
    this process is the one that originally reserved it in THIS process's
    own fd table (a fresh process finalizing a reservation from before its
    own restart holds no fd here to release -- nothing to do beyond the
    record write in that case, which is fine: the kernel already released
    the previous process's lock the moment that process exited)."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        dispatch = (record.get("deploy") or {}).get("dispatch") or {}
        if dispatch.get("invocation_id") != invocation_id:
            raise DeployVerificationReservationStale(
                f"mission {mission_id}: no live deploy-verification reservation matches "
                f"invocation_id {invocation_id!r}"
            )

        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["dispatch"] = dict(mutated["deploy"]["dispatch"])
        mutated["deploy"]["dispatch"]["status"] = "completed"
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: finalize_deploy_verification() failed validation", result.errors
            )
        _write_mission_record(mutated)

        fd = _DEPLOY_VERIFICATION_LOCK_FDS.pop(mission_id, None)
        if fd is not None:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return mutated


def record_deploy_verification_skip(mission_id: str) -> dict:
    """Sets ONLY deploy.dispatch.last_skip_observed_at -- a single,
    overwritable scalar, never a growing list. STRICTLY DIAGNOSTIC: this
    field must never be read by any completion/failure/recovery/timeout/
    authority-decision logic anywhere in this codebase -- it exists solely
    so a human or a test can observe that a dispatch cycle was skipped due
    to contention, nothing more."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        mutated = copy.deepcopy(record)
        mutated["deploy"] = dict(mutated["deploy"])
        mutated["deploy"]["dispatch"] = dict(mutated["deploy"]["dispatch"])
        mutated["deploy"]["dispatch"]["last_skip_observed_at"] = _now()
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_deploy_verification_skip() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


# --- M5: learning & knowledge continuity -----------------------------
# Both mutators below follow this module's exact deepcopy-then-validate-
# then-write pattern (see record_deploy_blocked() above). Neither ever
# touches record["state"]/record["state_history"] -- knowledge_derivation
# is bookkeeping about a side, non-authoritative candidate-submission
# pipeline (jarvis.knowledge_storage's own draft -> awaiting_emma_review
# -> awaiting_human_authorization -> accepted chain), never a second state
# engine. _mission_lock() is acquired, held, and released ONLY inside
# these two functions for this entire feature -- the orchestrating
# function in jarvis/mission_coordinator.py (derive_knowledge_for_completed_
# mission()) never acquires it itself, directly or by wrapping a `with`
# block around a call to either of these: _mission_lock() is non-reentrant
# (see its own docstring above), so a caller that already held it and then
# called one of these would deadlock on every single invocation.

MAX_KNOWLEDGE_DERIVATION_ATTEMPTS = 5


def record_knowledge_derivation_completed(mission_id: str, *, candidate_ids) -> dict:
    """Marks knowledge_derivation as durably finished for this mission,
    recording exactly which candidate_ids were (idempotently) submitted.
    Eligible regardless of current status EXCEPT it is a no-op (returns
    the record unchanged, never raises) if status is already "completed"
    -- this is the sole idempotency marker
    jarvis.mission_coordinator.derive_knowledge_for_completed_mission()
    consults to short-circuit an already-finished derivation, so calling
    it again after completion must never raise."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record["knowledge_derivation"]["status"] == "completed":
            return record

        mutated = copy.deepcopy(record)
        mutated["knowledge_derivation"] = dict(mutated["knowledge_derivation"])
        mutated["knowledge_derivation"]["status"] = "completed"
        mutated["knowledge_derivation"]["completed_at"] = _now()
        mutated["knowledge_derivation"]["candidate_ids"] = list(candidate_ids)
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_knowledge_derivation_completed() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def record_knowledge_derivation_attempt_failed(mission_id: str, *, error_summary: str) -> dict:
    """Records one failed knowledge-derivation attempt: increments
    attempt_count and sets last_attempted_at/last_error. If the resulting
    attempt_count reaches MAX_KNOWLEDGE_DERIVATION_ATTEMPTS, also sets
    status="stalled" in this SAME atomic write (proactive, not inferred
    later -- mirrors reopen_deploy_observation_window()'s own pattern of
    setting its exhaustion marker in the same call that reaches the cap).
    A no-op (returns the record unchanged, never raises) if status is
    already "completed" -- a stray failure recorded after the fact must
    never un-complete a mission's derivation."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)
        if record["knowledge_derivation"]["status"] == "completed":
            return record

        mutated = copy.deepcopy(record)
        mutated["knowledge_derivation"] = dict(mutated["knowledge_derivation"])
        new_count = mutated["knowledge_derivation"]["attempt_count"] + 1
        mutated["knowledge_derivation"]["attempt_count"] = new_count
        mutated["knowledge_derivation"]["last_attempted_at"] = _now()
        mutated["knowledge_derivation"]["last_error"] = str(error_summary)
        if new_count >= MAX_KNOWLEDGE_DERIVATION_ATTEMPTS:
            mutated["knowledge_derivation"]["status"] = "stalled"
        mutated["updated_at"] = _now()

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: record_knowledge_derivation_attempt_failed() failed validation", result.errors
            )
        _write_mission_record(mutated)
        return mutated


def transition(mission_id: str, target_state: str, *, actor: str, reason: str) -> dict:
    """The only path to changing state/appending to state_history.
    can_transition() is checked against the freshly-read, pre-mutation
    record; only if allowed does this function append the state_history
    entry, set state, and run the full post-mutation
    validate_mission_record() check before writing."""
    with _mission_lock(mission_id):
        record = _read_mission_record(mission_id)

        check = can_transition(record, target_state)
        if not check.allowed:
            raise MissionTransitionRejected(
                f"mission {mission_id}: {record.get('state')!r} -> {target_state!r} not allowed",
                check.reasons,
            )

        mutated = copy.deepcopy(record)
        timestamp = _now()
        mutated["state_history"] = mutated["state_history"] + [
            {
                "from_state": mutated["state"],
                "to_state": target_state,
                "at": timestamp,
                "actor": actor,
                "reason": reason,
            }
        ]
        mutated["state"] = target_state
        mutated["updated_at"] = timestamp

        result = validate_mission_record(mutated)
        if not result.valid:
            raise MissionValidationFailed(
                f"mission {mission_id}: transition mutation failed validation", result.errors
            )

        _write_mission_record(mutated)
        return mutated
