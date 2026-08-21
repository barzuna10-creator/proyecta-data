"""Deterministic, LLM-free validation for Zentra Mission Records.

This is Chugel's validation core (Zentra Autonomous Engineering V1, Level 2,
Increment #4, including the Part A hardening step authorized after the
corrective cycle). Pure Python plus the `jsonschema` package (explicitly
authorized as a project dependency for exactly this purpose -- see
requirements.txt): no I/O beyond loading the already-committed schema file
at import time, no network calls, no subprocess execution, no agent
invocation, no filesystem mutation of anything. `validate_mission_record(
record) -> ValidationResult` is the one entry point; every other function
here is a private helper it composes.

Two-layer architecture, in this exact order:

1. **Structural validation** against the canonical, already-committed
   `orchestrator/schemas/mission_record.schema.json` (required fields,
   types, patterns, enums, `additionalProperties: false`, the whole
   declarative spec) -- delegated entirely to `jsonschema.Draft7Validator`,
   matching the schema file's own declared `$schema` draft. This module
   never hand-reimplements any JSON Schema rule.
2. **Cross-field/semantic invariants** that JSON Schema alone cannot
   express -- see `orchestrator/MISSION_RECORD.md`'s "A note on what JSON
   Schema can and cannot enforce" and Emma's Increment #3/#4 independent
   reviews, which identified exactly this gap.

A record is `valid` only if BOTH layers pass. If structural validation
finds anything, cross-field validation is skipped entirely for that
call -- a structurally broken record cannot be meaningfully cross-checked,
and running the cross-field checks on it would only produce confusing,
redundant noise on top of the real structural errors. Every structural
failure is converted into the same `ValidationError` shape the cross-field
checks already use (stable code, JSON-pointer-style path, human message)
-- a caller never needs to parse jsonschema's own English messages or know
that library exists.

Because a record can still reach the cross-field layer with individually
well-typed-but-unexpectedly-shaped nested content (structural validation
guarantees the schema's declared shape, not every invariant this module
checks), every cross-field check still navigates defensively with `.get()`
and never raises -- belt and suspenders, not a substitute for the
structural layer."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jsonschema


# --- stable literal vocabularies -------------------------------------------
# Mirrors orchestrator/schemas/mission_record.schema.json's enums. Kept here
# (not re-derived from the schema file at import time) so this module has no
# dependency on a JSON Schema library or parser to know its own vocabulary.

STATES = frozenset({
    "INTAKE", "SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED", "BUILDING",
    "VERIFYING", "AWAITING_REVIEW", "REVIEWING", "CHANGES_REQUIRED",
    "CORRECTING", "PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING",
    "CI_PENDING", "MERGE_AWAITING_AUTHORIZATION", "MERGING", "MERGED",
    "DEPLOY_PENDING", "VERIFYING_PRODUCTION", "COMPLETED", "BLOCKED",
    "FAILED", "CANCELLED", "ROLLED_BACK",
})

VERDICTS = frozenset({
    "PASS", "PASS_WITH_NON_BLOCKING_FINDINGS", "CHANGES_REQUIRED", "BLOCKED",
})

GATE_STATUSES = frozenset({"not_requested", "pending", "approved", "rejected"})
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
GATE_NAMES = ("scope_authorization", "publish_authorization", "merge_authorization")

# The only literal value ever accepted for a human decision-maker, per
# agents/AGENT_STANDARD.md section 18 and every CONTRACT.md's precedence
# chain: no agent name is ever valid here.
HUMAN_DECIDER = "jose"

SUPPORTED_SCHEMA_VERSION = "1.0.0"

# Canonical state-transition table, encoding orchestrator/MISSION_RECORD.md's
# "Canonical state vocabulary" table. MISSION_RECORD.md documents each
# state's meaning, owner, and minimum entry evidence but (correctly, per its
# own scope) does not itself enumerate the legal edges between states --
# that omission is what this increment exists to close. The BLOCKED-resume
# edges in particular are a reasoned judgment call, not a literal quote from
# any source document; see the Increment #4 handoff for that flagged as an
# explicit assumption.
TRANSITIONS = frozenset({
    (None, "INTAKE"),
    ("INTAKE", "SCOPE_AWAITING_AUTHORIZATION"),
    ("INTAKE", "BLOCKED"),
    ("SCOPE_AWAITING_AUTHORIZATION", "AUTHORIZED"),
    ("SCOPE_AWAITING_AUTHORIZATION", "CANCELLED"),
    ("SCOPE_AWAITING_AUTHORIZATION", "BLOCKED"),
    ("AUTHORIZED", "BUILDING"),
    ("BUILDING", "VERIFYING"),
    ("BUILDING", "BLOCKED"),
    ("BUILDING", "FAILED"),
    ("VERIFYING", "AWAITING_REVIEW"),
    ("VERIFYING", "BLOCKED"),
    ("AWAITING_REVIEW", "REVIEWING"),
    ("REVIEWING", "PUBLISH_AWAITING_AUTHORIZATION"),
    ("REVIEWING", "CHANGES_REQUIRED"),
    ("REVIEWING", "BLOCKED"),
    ("CHANGES_REQUIRED", "CORRECTING"),
    ("CHANGES_REQUIRED", "BLOCKED"),
    ("CORRECTING", "VERIFYING"),
    ("CORRECTING", "BLOCKED"),
    ("CORRECTING", "FAILED"),
    ("PUBLISH_AWAITING_AUTHORIZATION", "PUBLISHING"),
    ("PUBLISH_AWAITING_AUTHORIZATION", "CANCELLED"),
    ("PUBLISH_AWAITING_AUTHORIZATION", "BLOCKED"),
    ("PUBLISHING", "CI_PENDING"),
    ("PUBLISHING", "BLOCKED"),
    ("CI_PENDING", "MERGE_AWAITING_AUTHORIZATION"),
    ("CI_PENDING", "BLOCKED"),
    ("MERGE_AWAITING_AUTHORIZATION", "MERGING"),
    ("MERGE_AWAITING_AUTHORIZATION", "CANCELLED"),
    ("MERGE_AWAITING_AUTHORIZATION", "BLOCKED"),
    ("MERGING", "MERGED"),
    ("MERGING", "BLOCKED"),
    ("MERGED", "DEPLOY_PENDING"),
    ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"),
    ("DEPLOY_PENDING", "BLOCKED"),
    ("VERIFYING_PRODUCTION", "COMPLETED"),
    ("VERIFYING_PRODUCTION", "BLOCKED"),
    ("COMPLETED", "ROLLED_BACK"),
    ("BLOCKED", "INTAKE"),
    ("BLOCKED", "SCOPE_AWAITING_AUTHORIZATION"),
    ("BLOCKED", "AUTHORIZED"),
    ("BLOCKED", "BUILDING"),
    ("BLOCKED", "VERIFYING"),
    ("BLOCKED", "AWAITING_REVIEW"),
    ("BLOCKED", "REVIEWING"),
    ("BLOCKED", "CORRECTING"),
    ("BLOCKED", "PUBLISH_AWAITING_AUTHORIZATION"),
    ("BLOCKED", "PUBLISHING"),
    ("BLOCKED", "CI_PENDING"),
    ("BLOCKED", "MERGE_AWAITING_AUTHORIZATION"),
    ("BLOCKED", "MERGING"),
    ("BLOCKED", "DEPLOY_PENDING"),
    ("BLOCKED", "VERIFYING_PRODUCTION"),
    ("BLOCKED", "FAILED"),
    ("BLOCKED", "CANCELLED"),
})


@dataclass(frozen=True)
class ValidationError:
    """One structured, stable-coded validation failure.

    `code` is the field a future Chugel state machine reads to decide what
    to do -- it must never need to parse `message` (free-form, for humans)
    to make a decision."""

    code: str
    message: str
    path: str


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[ValidationError, ...]

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


# --- structural layer (JSON Schema) -----------------------------------------
# The canonical schema is the single source of truth for structural shape;
# it is loaded once, read-only, at import time, and never modified or
# reinterpreted here. Draft7Validator is used explicitly because the schema
# file itself declares "$schema": ".../draft-07/schema#" -- this module
# follows that declaration rather than guessing or auto-detecting it.

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "mission_record.schema.json"
with open(_SCHEMA_PATH, encoding="utf-8") as _schema_file:
    _CANONICAL_SCHEMA = json.load(_schema_file)


# --- format checkers ---------------------------------------------------
# The canonical schema declares exactly two distinct "format" keywords
# (verified by inspection: `grep '"format"' orchestrator/schemas/
# mission_record.schema.json` returns exactly "date-time", used via the
# shared #/definitions/timestamp, and "uri", used once for
# publish.pr_url). Only these two are registered -- jsonschema silently
# skips a "format" value with no registered checker rather than raising,
# so this list only ever needs to grow if the canonical schema itself
# grows a new format keyword, never speculatively.
#
# `formats=()` starts the checker with none of jsonschema's own built-in
# format checks active -- this module registers exactly the two formats
# below and nothing else, so behavior never silently depends on whichever
# optional format-validation libraries happen to be installed.
_FORMAT_CHECKER = jsonschema.FormatChecker(formats=())


@_FORMAT_CHECKER.checks("date-time")
def _check_date_time_format(value: Any) -> bool:
    """RFC 3339 timestamp, matching orchestrator/schemas/
    mission_record.schema.json's own definitions.timestamp description
    ("RFC 3339 UTC timestamp, e.g. 2026-08-19T12:00:00Z"). Stdlib only
    (datetime.fromisoformat, available and sufficient in this project's
    Python version -- no new dependency). Requires a real calendar date, a
    time component, AND an explicit UTC offset: a bare date or a naive
    datetime with no offset is not a timestamp under this schema's stated
    intent, even though fromisoformat() would otherwise accept either.
    Read-only -- never mutates or normalizes `value`, only reports whether
    it is acceptable as-is."""
    if not isinstance(value, str):
        return True  # "type": "string" is enforced separately; a format
        # checker only judges values of the right type to begin with.
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


_URI_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*$")


@_FORMAT_CHECKER.checks("uri")
def _check_uri_format(value: Any) -> bool:
    """Small, deterministic, stdlib-only URI syntax check (urllib.parse +
    a scheme-grammar regex) appropriate for the one field the canonical
    schema currently types as "format": "uri" (publish.pr_url). Purely
    syntactic: no network request, no DNS resolution, no check that the
    destination exists -- only that the string has the shape of a URI
    (a valid scheme, no raw whitespace, and something after the scheme).
    Read-only -- never mutates or normalizes `value`."""
    if not isinstance(value, str):
        return True
    if not value or any(ch.isspace() for ch in value):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if not parsed.scheme or not _URI_SCHEME_PATTERN.match(parsed.scheme):
        return False
    return bool(parsed.netloc or parsed.path or parsed.query or parsed.fragment)


_STRUCTURAL_VALIDATOR = jsonschema.Draft7Validator(_CANONICAL_SCHEMA, format_checker=_FORMAT_CHECKER)


def _schema_pointer(absolute_path) -> str:
    """Renders a jsonschema error's absolute_path (a deque of str/int
    keys) as the same '$.foo[0].bar'-style pointer the cross-field checks
    already use, so a caller never sees two different path conventions."""
    pointer = "$"
    for part in absolute_path:
        pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
    return pointer


def _structural_error_code(pointer: str, err) -> str:
    """Stable, machine-readable code per JSON Schema validator keyword --
    never requires parsing jsonschema's own English message. The
    schema_version/const case keeps the same code the cross-field layer
    already used before this hardening step, for continuity. A `format`
    failure gets its own format-specific code (e.g.
    SCHEMA_FORMAT_DATE_TIME_VIOLATION), derived from `err.validator_value`
    -- itself a structured attribute jsonschema always sets for a format
    failure, never something parsed out of the human-readable message."""
    if pointer == "$.schema_version" and err.validator == "const":
        return "UNSUPPORTED_SCHEMA_VERSION"
    if err.validator == "format":
        format_name = str(err.validator_value).upper().replace("-", "_")
        return f"SCHEMA_FORMAT_{format_name}_VIOLATION"
    return f"SCHEMA_{str(err.validator).upper()}_VIOLATION"


def _structural_errors(record: Any) -> list[ValidationError]:
    """Runs the record through the canonical JSON Schema, converting every
    jsonschema.exceptions.ValidationError into this module's own
    ValidationError shape. Read-only: jsonschema validation never mutates
    the instance it checks, and this function performs no I/O of its own
    (the schema was already loaded once at import time; format checks are
    pure stdlib computations, never network or filesystem access)."""
    errors = []
    raw_errors = sorted(
        _STRUCTURAL_VALIDATOR.iter_errors(record),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    for err in raw_errors:
        pointer = _schema_pointer(err.absolute_path)
        errors.append(ValidationError(
            _structural_error_code(pointer, err),
            err.message,
            pointer,
        ))
    return errors


def validate_mission_record(record: Any) -> ValidationResult:
    """The one public entry point. Structural validation (layer 1, against
    the canonical schema) always runs first; if it finds anything,
    cross-field validation (layer 2) is skipped for this call -- a
    structurally broken record cannot be meaningfully cross-checked. Never
    raises on malformed input, including a record that is not even a JSON
    object -- the schema's own top-level "type": "object" requirement
    catches that case through the same structural path, no special-casing
    needed here."""
    structural = _structural_errors(record)
    if structural:
        return ValidationResult(errors=tuple(structural))

    errors: list[ValidationError] = []
    errors += _check_schema_version(record)
    errors += _check_state_history_consistency(record)
    errors += _check_reviewer_verdict_consistency(record)
    errors += _check_attempt_sequencing(record)
    errors += _check_artifact_identity_consistency(record)
    errors += _check_human_gates_consistency(record)
    errors += _check_corrective_cycle_consistency(record)
    errors += _check_mission_definition_history_consistency(record)
    errors += _check_state_evidence_consistency(record)
    return ValidationResult(errors=tuple(errors))


def evidence_errors_for_state(record: dict, state: str) -> tuple[ValidationError, ...]:
    """Public so orchestrator/state_machine.py can ask 'if the record were
    in `state`, would its minimum entry evidence (orchestrator/
    MISSION_RECORD.md's state table) actually be satisfied?' without
    mutating `record` or duplicating the per-state rules."""
    hypothetical = dict(record)
    hypothetical["state"] = state
    return tuple(_check_state_evidence_consistency(hypothetical))


# --- individual invariant checks -------------------------------------------

def _check_schema_version(record: dict) -> list[ValidationError]:
    version = record.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        return [ValidationError(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"schema_version {version!r} is not supported (expected {SUPPORTED_SCHEMA_VERSION!r}); "
            "a future schema version must not be leniently reinterpreted by this validator",
            "$.schema_version",
        )]
    return []


def _check_state_history_consistency(record: dict) -> list[ValidationError]:
    errors: list[ValidationError] = []
    state = record.get("state")
    history = record.get("state_history")

    if state not in STATES:
        errors.append(ValidationError(
            "UNKNOWN_STATE", f"state {state!r} is not in the canonical vocabulary", "$.state",
        ))

    if not isinstance(history, list) or not history:
        errors.append(ValidationError(
            "EMPTY_STATE_HISTORY",
            "state_history must contain at least the initial transition into INTAKE",
            "$.state_history",
        ))
        return errors

    entries = [e for e in history if isinstance(e, dict)]
    if entries and entries[0].get("from_state") is not None:
        errors.append(ValidationError(
            "STATE_HISTORY_BAD_ORIGIN",
            "the first state_history entry must have from_state == null",
            "$.state_history[0].from_state",
        ))

    for i, entry in enumerate(entries):
        path = f"$.state_history[{i}]"
        from_state = entry.get("from_state")
        to_state = entry.get("to_state")

        if i > 0 and from_state != entries[i - 1].get("to_state"):
            errors.append(ValidationError(
                "STATE_HISTORY_DISCONTINUOUS",
                f"state_history[{i}].from_state ({from_state!r}) does not match "
                f"state_history[{i - 1}].to_state ({entries[i - 1].get('to_state')!r})",
                path + ".from_state",
            ))

        if (from_state, to_state) not in TRANSITIONS:
            errors.append(ValidationError(
                "ILLEGAL_STATE_TRANSITION",
                f"transition {from_state!r} -> {to_state!r} is not in the canonical transition table",
                path,
            ))

    if entries and entries[-1].get("to_state") != state:
        errors.append(ValidationError(
            "STATE_MISMATCH_WITH_HISTORY",
            "record.state does not match state_history[-1].to_state",
            "$.state",
        ))

    return errors


def _finding_severities(entry: dict) -> list[str]:
    findings = entry.get("findings")
    if not isinstance(findings, list):
        return []
    return [f.get("severity") for f in findings if isinstance(f, dict)]


def _check_reviewer_verdict_consistency(record: dict) -> list[ValidationError]:
    """Derived exactly from docs/zentra/REVIEWER_QA_V1.md's Outcomes and
    Severity model sections, not guessed:

    - a P0 finding means the review 'cannot safely conclude' -- BLOCKED is
      the only legal verdict alongside one.
    - PASS requires 'no actionable findings remain' -- an empty list.
    - PASS WITH NON-BLOCKING NOTES allows only 'clearly identified P3
      notes' -- P0/P1/P2 are never legal alongside it.
    - CHANGES REQUIRED requires 'one or more P1/P2 findings' -- it is not
      legal with zero P1/P2 findings present."""
    errors: list[ValidationError] = []
    entries = record.get("reviewer_evidence")
    if not isinstance(entries, list):
        return errors

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        verdict = entry.get("verdict")
        severities = _finding_severities(entry)
        path = f"$.reviewer_evidence[{idx}]"

        if "P0" in severities and verdict != "BLOCKED":
            errors.append(ValidationError(
                "VERDICT_SEVERITY_MISMATCH_P0",
                "a P0 finding requires verdict BLOCKED (docs/zentra/REVIEWER_QA_V1.md severity model)",
                path + ".verdict",
            ))
        if verdict == "PASS" and severities:
            errors.append(ValidationError(
                "VERDICT_SEVERITY_MISMATCH_PASS",
                "PASS requires an empty findings list",
                path + ".findings",
            ))
        if verdict == "PASS_WITH_NON_BLOCKING_FINDINGS" and any(s != "P3" for s in severities):
            errors.append(ValidationError(
                "VERDICT_SEVERITY_MISMATCH_NON_BLOCKING",
                "PASS_WITH_NON_BLOCKING_FINDINGS allows only P3 findings",
                path + ".findings",
            ))
        if verdict == "CHANGES_REQUIRED" and not any(s in ("P1", "P2") for s in severities):
            errors.append(ValidationError(
                "VERDICT_SEVERITY_MISMATCH_CHANGES_REQUIRED",
                "CHANGES_REQUIRED requires at least one P1 or P2 finding",
                path + ".findings",
            ))

    return errors


def _check_attempt_sequencing(record: dict) -> list[ValidationError]:
    """builder_evidence/reviewer_evidence attempt numbers must form exactly
    [], [0], or [0, 1] -- never duplicated, never starting at 1."""
    errors: list[ValidationError] = []
    for field_name in ("builder_evidence", "reviewer_evidence"):
        entries = record.get(field_name)
        if not isinstance(entries, list):
            continue
        attempts = [e.get("attempt") for e in entries if isinstance(e, dict)]
        path = f"$.{field_name}"

        if len(attempts) != len(set(attempts)):
            errors.append(ValidationError(
                "DUPLICATE_ATTEMPT_NUMBER",
                f"{field_name} contains duplicate attempt numbers: {attempts}",
                path,
            ))
        elif attempts == [1]:
            errors.append(ValidationError(
                "CORRECTIVE_ATTEMPT_WITHOUT_INITIAL",
                f"{field_name} has an attempt=1 entry with no preceding attempt=0 entry",
                path,
            ))
        elif attempts not in ([], [0], [0, 1]):
            errors.append(ValidationError(
                "ILLEGAL_ATTEMPT_SEQUENCE",
                f"{field_name} attempt sequence {attempts} is not one of [], [0], [0, 1]",
                path,
            ))
    return errors


def _artifact_identity_equal(a: Any, b: Any) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a == b
    keys = ("mode", "commit_sha", "patch_path", "patch_sha256", "patch_byte_size")
    return all(a.get(k) == b.get(k) for k in keys)


def _check_artifact_identity_consistency(record: dict) -> list[ValidationError]:
    """Derived from docs/zentra/REVIEWER_QA_V1.md step 9: 'Immediately
    before concluding, validate the same artifact identity again. Any
    commit, patch digest, byte-size, or worktree change invalidates the
    review and requires BLOCKED with a fresh handoff.' Also checks that
    Emma actually reviewed the artifact the matching builder_evidence
    attempt produced -- a stronger, structurally-derived check beyond that
    literal quote, aimed at the exact 'wrong commit reviewed' risk this
    increment was asked to close."""
    errors: list[ValidationError] = []
    builder_entries = record.get("builder_evidence")
    builder_by_attempt = {
        e.get("attempt"): e.get("artifact")
        for e in builder_entries if isinstance(builder_entries, list) and isinstance(e, dict)
    } if isinstance(builder_entries, list) else {}

    reviewer_entries = record.get("reviewer_evidence")
    if not isinstance(reviewer_entries, list):
        return errors

    for idx, entry in enumerate(reviewer_entries):
        if not isinstance(entry, dict):
            continue
        path = f"$.reviewer_evidence[{idx}]"
        start = entry.get("artifact_identity_confirmed_at_start")
        before = entry.get("artifact_identity_confirmed_before_conclusion")
        verdict = entry.get("verdict")

        if not _artifact_identity_equal(start, before) and verdict != "BLOCKED":
            errors.append(ValidationError(
                "ARTIFACT_IDENTITY_DRIFT_DURING_REVIEW",
                "artifact identity changed between start and conclusion of this review; "
                "docs/zentra/REVIEWER_QA_V1.md requires verdict BLOCKED in this case",
                path,
            ))

        builder_artifact = builder_by_attempt.get(entry.get("attempt"))
        if builder_artifact is not None and not _artifact_identity_equal(start, builder_artifact):
            errors.append(ValidationError(
                "ARTIFACT_IDENTITY_MISMATCH_WITH_BUILDER",
                f"reviewer_evidence[{idx}] reviewed a different artifact than the "
                f"matching builder_evidence attempt actually produced",
                path + ".artifact_identity_confirmed_at_start",
            ))

    return errors


def _check_human_gates_consistency(record: dict) -> list[ValidationError]:
    """Absence of authorization means NOT AUTHORIZED (agents/
    AGENT_STANDARD.md, Precedence). Only `jose` may ever appear as a
    decider. A gate's status must not contradict its own decision
    metadata (e.g. 'pending' with a decision already recorded)."""
    errors: list[ValidationError] = []
    gates = record.get("human_gates")
    if not isinstance(gates, dict):
        return [ValidationError("MALFORMED_RECORD", "human_gates is missing or not an object", "$.human_gates")]

    for name in GATE_NAMES:
        gate = gates.get(name)
        if not isinstance(gate, dict):
            errors.append(ValidationError(
                "MALFORMED_RECORD", f"human_gates.{name} is missing or not an object", f"$.human_gates.{name}",
            ))
            continue

        path = f"$.human_gates.{name}"
        status = gate.get("status")
        decided_by = gate.get("decided_by")
        decided_at = gate.get("decided_at")
        decision_ref = gate.get("decision_ref")
        approved_for = gate.get("approved_for")
        requested_at = gate.get("requested_at")

        if decided_by is not None and decided_by != HUMAN_DECIDER:
            errors.append(ValidationError(
                "GATE_APPROVER_NOT_HUMAN",
                f"human_gates.{name}.decided_by is {decided_by!r}; only {HUMAN_DECIDER!r} "
                "may decide a gate -- no agent name is ever valid here",
                path + ".decided_by",
            ))

        if status == "approved":
            if decided_by != HUMAN_DECIDER or not decided_at or not decision_ref or not approved_for:
                errors.append(ValidationError(
                    "GATE_APPROVED_WITHOUT_EVIDENCE",
                    f"human_gates.{name} is 'approved' without complete decision evidence",
                    path,
                ))
        elif status == "pending":
            if decided_by or decided_at or decision_ref or approved_for:
                errors.append(ValidationError(
                    "GATE_CONTRADICTORY_METADATA",
                    f"human_gates.{name} is 'pending' but already carries decision evidence",
                    path,
                ))
        elif status == "not_requested":
            if decided_by or decided_at or decision_ref or approved_for or requested_at:
                errors.append(ValidationError(
                    "GATE_CONTRADICTORY_METADATA",
                    f"human_gates.{name} is 'not_requested' but already carries request/decision evidence",
                    path,
                ))
        elif status == "rejected":
            if bool(decided_by) != bool(decided_at):
                errors.append(ValidationError(
                    "GATE_CONTRADICTORY_METADATA",
                    f"human_gates.{name} is 'rejected' with incomplete decision evidence "
                    "(decided_by and decided_at must both be present or both be absent)",
                    path,
                ))
        elif status not in GATE_STATUSES:
            errors.append(ValidationError(
                "MALFORMED_RECORD", f"human_gates.{name}.status {status!r} is not recognized", path + ".status",
            ))

    errors += _check_stale_approvals(record, gates)
    return errors


def _check_stale_approvals(record: dict, gates: dict) -> list[ValidationError]:
    """An approval only authorizes the exact identity recorded in
    `approved_for` at decision time. If the thing it approved has since
    changed, the approval is stale and must be treated as not live.

    `human_gates.scope_authorization` semantics -- EXPLICIT HUMAN DESIGN
    DECISION (José, Increment #4 corrective cycle, resolving the ambiguity
    Emma's independent review identified as P2): this gate represents
    authorization for the CURRENT active mission definition
    (`mission_definition_history[-1]`), not permanently the initial
    `david_intake` version. When David proposes a re-plan and José accepts
    it, `human_gates.scope_authorization` must be updated so that
    `approved_for.mission_definition_version` refers to the new, current
    version -- the previous authorization remains auditable through
    `mission_definition_history`'s own per-version `authorized_by`/
    `authorized_at`/`authorization_decision_ref` fields (append-only,
    never overwritten), but it is no longer the ACTIVE authorization.
    Consequently: an approved scope_authorization whose approved_for still
    names an older version than the current one is stale and must fail
    closed, exactly as an approval for a stale artifact SHA already does
    below for merge_authorization."""
    errors: list[ValidationError] = []

    merge_gate = gates.get("merge_authorization") or {}
    if merge_gate.get("status") == "approved":
        approved_for = merge_gate.get("approved_for") or {}
        publish = record.get("publish") or {}
        expected_head = approved_for.get("head_sha")
        actual_head = publish.get("commit_sha")
        if expected_head is not None and actual_head is not None and expected_head != actual_head:
            errors.append(ValidationError(
                "STALE_APPROVAL",
                "merge_authorization was approved for a head_sha that no longer matches "
                "the currently published commit",
                "$.human_gates.merge_authorization.approved_for",
            ))

    scope_gate = gates.get("scope_authorization") or {}
    if scope_gate.get("status") == "approved":
        approved_for = scope_gate.get("approved_for") or {}
        history = record.get("mission_definition_history")
        entries = [e for e in history if isinstance(e, dict)] if isinstance(history, list) else []
        current_version = entries[-1].get("version") if entries else None
        expected_version = approved_for.get("mission_definition_version")
        if expected_version is not None and current_version is not None and expected_version != current_version:
            errors.append(ValidationError(
                "STALE_APPROVAL",
                "scope_authorization was approved for a mission_definition_version that is "
                "not the current/latest authorized version -- per José's explicit decision, "
                "this gate tracks the CURRENT active mission definition, not a fixed one",
                "$.human_gates.scope_authorization.approved_for",
            ))

    return errors


def _check_corrective_cycle_consistency(record: dict) -> list[ValidationError]:
    """AGENTS.md: exactly one bounded corrective cycle. corrective_cycle_count
    must agree with whether an attempt=1 builder_evidence entry actually
    exists, and that entry must only exist because a preceding
    CHANGES_REQUIRED verdict triggered it."""
    errors: list[ValidationError] = []
    count = record.get("corrective_cycle_count")
    builder_entries = record.get("builder_evidence")
    builder_attempts = {
        e.get("attempt") for e in builder_entries if isinstance(builder_entries, list) and isinstance(e, dict)
    } if isinstance(builder_entries, list) else set()
    has_corrective_build = 1 in builder_attempts

    if not isinstance(count, int) or isinstance(count, bool) or count not in (0, 1):
        errors.append(ValidationError(
            "MALFORMED_RECORD", f"corrective_cycle_count {count!r} must be exactly 0 or 1", "$.corrective_cycle_count",
        ))
        return errors

    if count == 1 and not has_corrective_build:
        errors.append(ValidationError(
            "CORRECTIVE_CYCLE_COUNT_INCONSISTENT",
            "corrective_cycle_count is 1 but no attempt=1 builder_evidence entry exists",
            "$.corrective_cycle_count",
        ))
    if count == 0 and has_corrective_build:
        errors.append(ValidationError(
            "CORRECTIVE_CYCLE_COUNT_INCONSISTENT",
            "an attempt=1 builder_evidence entry exists but corrective_cycle_count is 0",
            "$.corrective_cycle_count",
        ))

    if has_corrective_build:
        reviewer_entries = record.get("reviewer_evidence")
        first_review = next(
            (e for e in reviewer_entries if isinstance(e, dict) and e.get("attempt") == 0),
            None,
        ) if isinstance(reviewer_entries, list) else None
        if first_review is None or first_review.get("verdict") != "CHANGES_REQUIRED":
            errors.append(ValidationError(
                "CORRECTIVE_CYCLE_WITHOUT_TRIGGER",
                "a corrective (attempt=1) build exists without a preceding CHANGES_REQUIRED "
                "verdict from the initial review",
                "$.builder_evidence",
            ))

    return errors


def _check_mission_definition_history_consistency(record: dict) -> list[ValidationError]:
    """Versions must be a monotonic 1..N sequence with no gaps or
    reordering; every entry's authorized_by must be the human decider,
    never an agent; a re-planning entry must trace back to a genuinely
    accepted proposal -- and, symmetrically (Emma P2, Increment #4
    corrective cycle), every ACCEPTED proposal must itself carry complete
    José-attributed decision evidence and must actually correspond to a
    real appended history entry, not just claim to."""
    errors: list[ValidationError] = []
    history = record.get("mission_definition_history")
    if not isinstance(history, list):
        return [ValidationError("MALFORMED_RECORD", "mission_definition_history is not a list", "$.mission_definition_history")]

    entries = [e for e in history if isinstance(e, dict)]
    versions = [e.get("version") for e in entries]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        errors.append(ValidationError(
            "MISSION_DEFINITION_VERSION_NOT_MONOTONIC",
            f"mission_definition_history versions {versions} are not a monotonic 1..N sequence",
            "$.mission_definition_history",
        ))

    proposals_raw = record.get("proposed_scope_changes")
    proposals = [p for p in proposals_raw if isinstance(p, dict)] if isinstance(proposals_raw, list) else []
    proposals_by_id = {p.get("proposal_id"): p for p in proposals}
    history_entry_by_proposal_id: dict = {}

    for idx, entry in enumerate(entries):
        path = f"$.mission_definition_history[{idx}]"
        if entry.get("authorized_by") != HUMAN_DECIDER:
            errors.append(ValidationError(
                "SCOPE_AUTHORIZED_BY_NON_HUMAN",
                f"mission_definition_history[{idx}].authorized_by is {entry.get('authorized_by')!r}; "
                f"only {HUMAN_DECIDER!r} may authorize scope -- an agent can never appear here",
                path + ".authorized_by",
            ))

        if entry.get("source") == "david_replan":
            proposal_id = entry.get("based_on_proposal_id")
            history_entry_by_proposal_id[proposal_id] = entry
            proposal = proposals_by_id.get(proposal_id)
            if proposal is None:
                errors.append(ValidationError(
                    "SCOPE_VERSION_ORPHANED_PROPOSAL",
                    f"mission_definition_history[{idx}] references proposal {proposal_id!r}, "
                    "which does not exist in proposed_scope_changes",
                    path + ".based_on_proposal_id",
                ))
            elif proposal.get("status") != "accepted" or proposal.get("resulting_mission_definition_version") != entry.get("version"):
                errors.append(ValidationError(
                    "SCOPE_VERSION_PROPOSAL_MISMATCH",
                    f"mission_definition_history[{idx}] is not consistently linked to an "
                    "accepted, matching proposal",
                    path,
                ))

    for p_idx, proposal in enumerate(proposals):
        p_path = f"$.proposed_scope_changes[{p_idx}]"
        status = proposal.get("status")
        proposal_id = proposal.get("proposal_id")

        if status == "accepted":
            if proposal.get("decided_by") != HUMAN_DECIDER or not proposal.get("decided_at"):
                errors.append(ValidationError(
                    "PROPOSAL_ACCEPTED_WITHOUT_JOSE_EVIDENCE",
                    f"proposed_scope_changes[{p_idx}] is 'accepted' but does not record complete "
                    f"decision evidence attributed to {HUMAN_DECIDER!r} (decided_by and decided_at)",
                    p_path,
                ))

            resulting_version = proposal.get("resulting_mission_definition_version")
            if resulting_version is not None:
                matching_entry = history_entry_by_proposal_id.get(proposal_id)
                if matching_entry is None or matching_entry.get("version") != resulting_version:
                    errors.append(ValidationError(
                        "PROPOSAL_ACCEPTED_WITHOUT_RESULTING_VERSION",
                        f"proposed_scope_changes[{p_idx}] claims resulting_mission_definition_version "
                        f"{resulting_version!r}, but no matching mission_definition_history entry exists",
                        p_path + ".resulting_mission_definition_version",
                    ))

        elif status in ("pending_human_decision", "rejected"):
            if history_entry_by_proposal_id.get(proposal_id) is not None:
                errors.append(ValidationError(
                    "SCOPE_VERSION_PROPOSAL_MISMATCH",
                    f"proposed_scope_changes[{p_idx}] has status {status!r} but is linked to a "
                    "mission_definition_history entry -- only an accepted proposal may become "
                    "the active mission definition",
                    p_path,
                ))

    return errors


def _check_state_evidence_consistency(record: dict) -> list[ValidationError]:
    state = record.get("state")
    checker = _STATE_EVIDENCE_CHECKERS.get(state)
    return checker(record) if checker else []


def _evidence_authorized(record: dict) -> list[ValidationError]:
    gate = ((record.get("human_gates") or {}).get("scope_authorization")) or {}
    if gate.get("status") != "approved":
        return [ValidationError(
            "STATE_EVIDENCE_MISSING", "AUTHORIZED requires human_gates.scope_authorization to be approved", "$.state",
        )]
    return []


def _evidence_building(record: dict) -> list[ValidationError]:
    errors = []
    repo = record.get("repository") or {}
    if not repo.get("isolation_confirmed"):
        errors.append(ValidationError(
            "STATE_EVIDENCE_MISSING", "BUILDING requires repository.isolation_confirmed", "$.state",
        ))
    if not record.get("mission_definition_history"):
        errors.append(ValidationError(
            "STATE_EVIDENCE_MISSING", "BUILDING requires at least one mission_definition_history entry", "$.state",
        ))
    return errors


def _evidence_reviewing(record: dict) -> list[ValidationError]:
    builder_entries = record.get("builder_evidence")
    if not isinstance(builder_entries, list) or not builder_entries:
        return [ValidationError(
            "STATE_EVIDENCE_MISSING", "REVIEWING requires at least one builder_evidence entry to review", "$.state",
        )]
    return []


def _evidence_merging(record: dict) -> list[ValidationError]:
    gate = ((record.get("human_gates") or {}).get("merge_authorization")) or {}
    if gate.get("status") != "approved":
        return [ValidationError(
            "STATE_EVIDENCE_MISSING", "MERGING requires human_gates.merge_authorization to be approved", "$.state",
        )]
    return []


def _evidence_merged_or_later(record: dict) -> list[ValidationError]:
    merge = record.get("merge") or {}
    if not merge.get("merge_commit_sha"):
        return [ValidationError(
            "STATE_EVIDENCE_MISSING", "this state requires merge.merge_commit_sha to be recorded", "$.state",
        )]
    return []


def _evidence_verifying_production(record: dict) -> list[ValidationError]:
    errors = _evidence_merged_or_later(record)
    deploy = record.get("deploy") or {}
    if not deploy.get("expected_sha"):
        errors.append(ValidationError(
            "STATE_EVIDENCE_MISSING", "VERIFYING_PRODUCTION requires deploy.expected_sha", "$.state",
        ))
    return errors


def _evidence_completed(record: dict) -> list[ValidationError]:
    errors = _evidence_merged_or_later(record)
    deploy = record.get("deploy") or {}
    if not deploy.get("deploy_confirmed_at"):
        errors.append(ValidationError(
            "STATE_EVIDENCE_MISSING", "COMPLETED requires deploy.deploy_confirmed_at", "$.state",
        ))
    version_check = deploy.get("version_check") or {}
    if not version_check.get("body_summary"):
        errors.append(ValidationError(
            "STATE_EVIDENCE_MISSING", "COMPLETED requires deploy.version_check evidence", "$.state",
        ))
    return errors


_STATE_EVIDENCE_CHECKERS = {
    "AUTHORIZED": _evidence_authorized,
    "BUILDING": _evidence_building,
    "REVIEWING": _evidence_reviewing,
    "MERGING": _evidence_merging,
    "MERGED": _evidence_merged_or_later,
    "DEPLOY_PENDING": _evidence_merged_or_later,
    "VERIFYING_PRODUCTION": _evidence_verifying_production,
    "COMPLETED": _evidence_completed,
}
