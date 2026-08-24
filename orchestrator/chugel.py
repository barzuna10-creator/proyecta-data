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

import copy
import datetime
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from orchestrator.validator import CANONICAL_SHA_RE, HUMAN_DECIDER, validate_mission_record
from orchestrator.state_machine import can_transition


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
    path = _mission_path(new_id)
    if path.is_symlink() or path.exists():
        raise MissionRecordAlreadyExists(
            f"mission {new_id}: something already exists at {path}"
        )

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
        "corrective_cycle_count": 0,
        "publish": {
            "commit_sha": None,
            "pushed_at": None,
            "pr_url": None,
            "pr_number": None,
            "ci_runs": [],
        },
        "merge": {"merge_commit_sha": None, "merged_at": None},
        "deploy": {
            "expected_sha": None,
            "deploy_confirmed_at": None,
            "health_check": {"checked_at": None, "status_code": None, "body_summary": None},
            "version_check": {"checked_at": None, "status_code": None, "body_summary": None},
        },
        "budget": {
            "configured": None,
            "consumed": {"unit": "tokens", "amount": 0},
            "per_agent_consumed": {"david": None, "emilio": None, "emma": None},
            "exhausted": False,
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


def record_repository_state(mission_id: str, repository: dict) -> dict:
    """The only operation that replaces the placeholder repository object
    written by create_mission() with real worktree/branch/base-SHA/
    isolation values. Whether the resulting record can subsequently
    transition to BUILDING is decided entirely by the existing
    validate_mission_record()/can_transition() evidence checks -- this
    function does not itself decide isolation is real, it only records
    what the caller asserts, subject to the same validation every other
    mutation goes through."""
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
    An attempt == 0 entry never changes corrective_cycle_count."""
    record = _read_mission_record(mission_id)
    mutated = copy.deepcopy(record)
    mutated["builder_evidence"] = mutated["builder_evidence"] + [copy.deepcopy(evidence)]
    if evidence.get("attempt") == 1:
        mutated["corrective_cycle_count"] = 1
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
    the existing cross-field checks exactly like every other field."""
    record = _read_mission_record(mission_id)
    mutated = copy.deepcopy(record)
    mutated["reviewer_evidence"] = mutated["reviewer_evidence"] + [copy.deepcopy(evidence)]
    mutated["updated_at"] = _now()

    result = validate_mission_record(mutated)
    if not result.valid:
        raise MissionValidationFailed(
            f"mission {mission_id}: reviewer evidence append failed validation", result.errors
        )

    _write_mission_record(mutated)
    return mutated


def record_publish_commit(mission_id: str, commit_sha: str) -> dict:
    """Persist the infrastructure-observed publication commit identity.

    This operation records identity only; it never runs git, pushes, creates
    or updates a pull request, or grants merge authorization. Publication is
    eligible only while the mission awaits merge authorization, after the
    publication/CI lifecycle has completed. The first canonical SHA becomes
    immutable, so neither a duplicate call nor a conflicting caller-controlled
    value can rewrite the identity later.

    The SHA is checked before reading the Mission Record. After the validated
    read, the complete mutation is assembled in memory, canonically validated
    once, and written once through Chugel's existing atomic writer.
    """
    if not isinstance(commit_sha, str) or CANONICAL_SHA_RE.fullmatch(commit_sha) is None:
        raise ValueError("commit_sha must be a canonical 40-character lowercase SHA")

    record = _read_mission_record(mission_id)
    if record.get("state") != "MERGE_AWAITING_AUTHORIZATION":
        raise ValueError(
            f"mission {mission_id}: publication identity may only be recorded in "
            f"state 'MERGE_AWAITING_AUTHORIZATION', got {record.get('state')!r}"
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


def transition(mission_id: str, target_state: str, *, actor: str, reason: str) -> dict:
    """The only path to changing state/appending to state_history.
    can_transition() is checked against the freshly-read, pre-mutation
    record; only if allowed does this function append the state_history
    entry, set state, and run the full post-mutation
    validate_mission_record() check before writing."""
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
