"""M4 (Automatic Production Deployment Observation & Verification) --
drives DEPLOY_PENDING -> VERIFYING_PRODUCTION -> COMPLETED, or fails
closed to BLOCKED with one of 7 exhaustive failure classifications.

Read-only observation/verification ONLY: this module never calls a
Render API, never triggers a deploy, and never performs a rollback/
redeploy/restart/config mutation. It only reads GET /health, GET
/version, and (locally, read-only) `git fetch`/`git cat-file`/`git
merge-base` against Jarvis's own existing git working copy -- the same
convention orchestrator/merge_executor.py already uses for `git`/`gh`
subprocess calls (fixed argv, shell=False, bounded timeout,
`except (OSError, subprocess.TimeoutExpired)`).

Every mission-record mutation goes through orchestrator/chugel.py's own
M4 mutators -- this module never writes a Mission Record field directly,
never re-implements Chugel's validate/write/lock discipline.

`run()` always terminates within its own call: budget/attempt-bounded
polling loops mean a single invocation ends in exactly one of SKIPPED
(another live process already holds the per-mission verification lock),
BLOCKED (one of the 7 classifications, transitioned via
chugel.record_deploy_blocked()), or COMPLETED (chugel.record_deploy_confirmed()
already ran) -- never a fourth "call me again to keep going" status. A
crash mid-run leaves the record exactly where the last successful Chugel
write left it; the next invocation re-derives everything from scratch
(observation_started_at, expected_sha, and whatever DEPLOY_PENDING/
VERIFYING_PRODUCTION evidence already exists) and consumes only the
REMAINING total budget, never a fresh one.

Deviation from the frozen design, documented here rather than silently
omitted: the design's own defensive VERIFYING_PRODUCTION-phase re-check
of /version (which could, if it ever found a *different* commit not
satisfying the same ancestry relationship, classify
IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK) is explicitly optional in the
design ("as a defensive re-check, if you choose to re-verify /version").
This implementation does not perform that extra re-check -- the
IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK classification exists in the
schema/recovery-policy vocabulary (never removed, since
orchestrator/deploy_recovery_policy.py's table already accounts for it)
but this module never produces it. A future increment can add that
re-check without touching anything else here."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from orchestrator import chugel
from orchestrator.validator import CANONICAL_SHA_RE

try:
    import fcntl
except ImportError:  # pragma: no cover -- POSIX-only, matching chugel.py's
    # own portability stance.
    fcntl = None

# --- bounds -----------------------------------------------------------

TOTAL_OBSERVATION_BUDGET_SECONDS = 900.0
_VERSION_POLL_MAX_ATTEMPTS = 30
_VERSION_POLL_INTERVAL_SECONDS = 10.0
_HEALTH_POLL_MAX_ATTEMPTS = 6
_HEALTH_POLL_INTERVAL_SECONDS = 5.0
_HTTP_TIMEOUT_SECONDS = 10
_GIT_UNSHALLOW_TIMEOUT_SECONDS = 20.0
_GIT_FETCH_TIMEOUT_SECONDS = 15.0
_GIT_SHORT_TIMEOUT_SECONDS = 10.0
_ANCESTRY_LOCK_POLL_INTERVAL_SECONDS = 2.0
_MAX_OUTPUT_BYTES = 65536


class DeployVerifierError(Exception):
    pass


@dataclass(frozen=True)
class VerifierResult:
    status: str  # "SKIPPED" | "BLOCKED" | "ADVANCED" | "COMPLETED"
    state: str
    reason: str = ""


# --- small pure/IO helpers ---------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _elapsed_seconds(observation_started_at: str) -> float:
    started = _parse_timestamp(observation_started_at)
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - started).total_seconds()


def _remaining_budget(observation_started_at: str) -> float:
    return TOTAL_OBSERVATION_BUDGET_SECONDS - _elapsed_seconds(observation_started_at)


def _http_get_json(url: str, *, timeout: float) -> tuple[int | None, dict | None, str | None]:
    """Returns (status_code, body, error_summary). The body is parsed
    REGARDLESS of status code -- a 503 with a valid, well-formed JSON body
    reporting status='degraded' is a real, structurally-valid response,
    never a transport error, per api/main.py's own /health contract.
    Never raises."""
    try:
        response = requests.get(url, timeout=timeout)
    except requests.exceptions.Timeout:
        return None, None, f"timeout after {timeout}s"
    except requests.exceptions.RequestException as exc:
        return None, None, f"request error ({type(exc).__name__})"

    try:
        body = response.json()
    except ValueError:
        return response.status_code, None, "response body is not valid JSON"
    if not isinstance(body, dict):
        return response.status_code, None, f"expected a JSON object, got {type(body).__name__}"
    return response.status_code, body, None


def _body_summary(body: dict | None, error: str | None) -> str:
    if body is not None:
        try:
            return json.dumps(body, sort_keys=True)[:500]
        except (TypeError, ValueError):
            return "unserializable body"
    return error or "no response"


# --- git subprocess helper, matching orchestrator/merge_executor.py's
# exact convention: fixed argv, shell=False, bounded timeout, the same
# narrow except clause. -----------------------------------------------

def _run_git(argv: list[str], *, cwd: str, timeout: float):
    try:
        result = subprocess.run(
            argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(result.stdout) > _MAX_OUTPUT_BYTES or len(result.stderr) > _MAX_OUTPUT_BYTES:
        return None
    return result


def _git_resolves(sha: str, *, git_repo_path: str, git_executable: str) -> bool:
    result = _run_git(
        [git_executable, "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=git_repo_path, timeout=_GIT_SHORT_TIMEOUT_SECONDS,
    )
    return result is not None and result.returncode == 0


# --- ancestry proof, guarded by a GLOBAL (not per-mission) lock --------

_ANCESTRY_MATCH = "match"
_ANCESTRY_DEFINITIVE_NON_ANCESTOR = "definitive_non_ancestor"
_ANCESTRY_UNVERIFIABLE = "unverifiable"
_ANCESTRY_BUDGET_EXHAUSTED = "budget_exhausted"


def _global_ancestry_lock_path() -> Path:
    # Deliberately reads chugel._MISSIONS_DIR fresh on every call (never
    # cached at import time) so tests that redirect it to a temp directory
    # (exactly like tests/test_orchestrator_chugel.py's ChugelTestCase)
    # transparently redirect this module's own lock file too.
    return chugel._MISSIONS_DIR / ".deploy_verifier.ancestry.lock"


def _verify_ancestry(
    expected_sha: str, observed_sha: str, *,
    git_repo_path: str, git_executable: str, remaining_budget_seconds: float,
) -> str:
    """Held ONLY for the bounded git-subprocess sequence itself (never
    during HTTP polling or sleep), max ~40s. The wait to ACQUIRE the lock
    is itself bounded by remaining_budget_seconds -- if that wait alone
    exhausts the mission's remaining total budget, this is budget
    exhaustion, never ANCESTRY_UNVERIFIABLE (a caller must check for
    _ANCESTRY_BUDGET_EXHAUSTED and classify OBSERVATION_BUDGET_EXHAUSTED,
    not treat it as a garden-variety unverifiable result)."""
    chugel._MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _global_ancestry_lock_path()
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)

    deadline = time.monotonic() + max(remaining_budget_seconds, 0.0)
    acquired = False
    try:
        if fcntl is None:
            acquired = True
        else:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_ANCESTRY_LOCK_POLL_INTERVAL_SECONDS)

        if not acquired:
            return _ANCESTRY_BUDGET_EXHAUSTED

        shallow_path = Path(git_repo_path) / ".git" / "shallow"
        already_fetched = False
        if shallow_path.exists():
            unshallow = _run_git(
                [git_executable, "fetch", "--unshallow", "origin", "main"],
                cwd=git_repo_path, timeout=_GIT_UNSHALLOW_TIMEOUT_SECONDS,
            )
            already_fetched = True
            if unshallow is None or unshallow.returncode != 0:
                return _ANCESTRY_UNVERIFIABLE
            # Structural re-verify -- never trust the exit code alone.
            if shallow_path.exists():
                return _ANCESTRY_UNVERIFIABLE

        resolved = {sha: _git_resolves(sha, git_repo_path=git_repo_path, git_executable=git_executable)
                    for sha in (expected_sha, observed_sha)}
        if not all(resolved.values()) and not already_fetched:
            _run_git(
                [git_executable, "fetch", "origin", "main"],
                cwd=git_repo_path, timeout=_GIT_FETCH_TIMEOUT_SECONDS,
            )
            resolved = {sha: _git_resolves(sha, git_repo_path=git_repo_path, git_executable=git_executable)
                        for sha in (expected_sha, observed_sha)}
        if not all(resolved.values()):
            return _ANCESTRY_UNVERIFIABLE

        merge_base = _run_git(
            [git_executable, "merge-base", "--is-ancestor", expected_sha, observed_sha],
            cwd=git_repo_path, timeout=_GIT_SHORT_TIMEOUT_SECONDS,
        )
        if merge_base is None:
            return _ANCESTRY_UNVERIFIABLE
        if merge_base.returncode == 0:
            return _ANCESTRY_MATCH
        if merge_base.returncode == 1:
            return _ANCESTRY_DEFINITIVE_NON_ANCESTOR
        return _ANCESTRY_UNVERIFIABLE
    finally:
        if acquired and fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- the two poll phases ------------------------------------------------

def _block(mission_id: str, classification: str, reason: str) -> VerifierResult:
    chugel.record_deploy_blocked(mission_id, classification=classification, reason=reason)
    return VerifierResult("BLOCKED", "BLOCKED", classification)


def _run_deploy_pending_phase(
    mission_id: str, *, base_url: str, expected_sha: str,
    git_repo_path: str, git_executable: str, observation_started_at: str, requests_timeout: float,
) -> VerifierResult:
    observed_any_identity = False
    last_definitive_non_ancestor = False
    last_unverifiable = False

    for attempt in range(1, _VERSION_POLL_MAX_ATTEMPTS + 1):
        remaining = _remaining_budget(observation_started_at)
        if remaining <= 0:
            return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                           "production observation budget exhausted while polling /version")

        status_code, body, error = _http_get_json(f"{base_url}/version", timeout=requests_timeout)
        checked_at = _now_iso()
        try:
            chugel.record_deploy_version_check(
                mission_id, checked_at=checked_at, status_code=status_code,
                body_summary=_body_summary(body, error),
            )
        except chugel.ChugelError:
            pass

        commit = body.get("commit") if isinstance(body, dict) else None
        if (
            isinstance(commit, str)
            and commit
            and commit != "unknown"
            and CANONICAL_SHA_RE.fullmatch(commit)
        ):
            observed_any_identity = True
            remaining = _remaining_budget(observation_started_at)
            if remaining <= 0:
                return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                               "production observation budget exhausted before ancestry could be checked")
            ancestry = _verify_ancestry(
                expected_sha, commit, git_repo_path=git_repo_path, git_executable=git_executable,
                remaining_budget_seconds=remaining,
            )
            if ancestry == _ANCESTRY_MATCH:
                chugel.record_deploy_ancestry_result(mission_id, observed_sha=commit, ancestry_verified=True)
                chugel.transition(mission_id, "VERIFYING_PRODUCTION", actor="chugel",
                                   reason="production identity ancestry confirmed")
                return VerifierResult("ADVANCED", "VERIFYING_PRODUCTION")
            if ancestry == _ANCESTRY_DEFINITIVE_NON_ANCESTOR:
                last_definitive_non_ancestor = True
                last_unverifiable = False
            elif ancestry == _ANCESTRY_BUDGET_EXHAUSTED:
                return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                               "production observation budget exhausted acquiring the ancestry lock")
            else:  # unverifiable
                last_definitive_non_ancestor = False
                last_unverifiable = True
        # A malformed/missing/"unknown" commit does not overwrite the last
        # RESOLVABLE-observation classification flags above -- only a
        # well-formed identity that ancestry was actually evaluated
        # against ever updates them.

        if attempt < _VERSION_POLL_MAX_ATTEMPTS:
            remaining = _remaining_budget(observation_started_at)
            if remaining <= 0:
                return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                               "production observation budget exhausted while polling /version")
            time.sleep(min(_VERSION_POLL_INTERVAL_SECONDS, remaining))

    if last_definitive_non_ancestor:
        return _block(mission_id, "IDENTITY_MISMATCH_DEFINITIVE",
                       "the observed production commit is definitively not a descendant of "
                       "the expected merge commit")
    if last_unverifiable or observed_any_identity:
        return _block(mission_id, "ANCESTRY_UNVERIFIABLE",
                       "a well-formed commit identity was observed but ancestry could never "
                       "be conclusively computed within budget")
    return _block(mission_id, "IDENTITY_NEVER_OBSERVED",
                   "no well-formed commit identity was ever observed from /version")


def _run_verifying_production_phase(
    mission_id: str, *, base_url: str, observation_started_at: str, requests_timeout: float,
) -> VerifierResult:
    last_degraded = False

    for attempt in range(1, _HEALTH_POLL_MAX_ATTEMPTS + 1):
        remaining = _remaining_budget(observation_started_at)
        if remaining <= 0:
            return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                           "production observation budget exhausted while polling /health")

        status_code, body, error = _http_get_json(f"{base_url}/health", timeout=requests_timeout)
        checked_at = _now_iso()
        try:
            chugel.record_deploy_health_check(
                mission_id, checked_at=checked_at, status_code=status_code,
                body_summary=_body_summary(body, error),
            )
        except chugel.ChugelError:
            pass

        if status_code == 200 and isinstance(body, dict):
            chugel.record_deploy_confirmed(mission_id, confirmed_at=checked_at)
            return VerifierResult("COMPLETED", "COMPLETED")

        last_degraded = isinstance(body, dict) and body.get("status") == "degraded"

        if attempt < _HEALTH_POLL_MAX_ATTEMPTS:
            remaining = _remaining_budget(observation_started_at)
            if remaining <= 0:
                return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                               "production observation budget exhausted while polling /health")
            time.sleep(min(_HEALTH_POLL_INTERVAL_SECONDS, remaining))

    if last_degraded:
        return _block(mission_id, "HEALTH_DEGRADED", "production /health last reported status='degraded'")
    return _block(mission_id, "HEALTH_UNREACHABLE_OR_MALFORMED",
                   "production /health never returned a parseable, healthy response within budget")


# --- entrypoint ----------------------------------------------------------

def run(
    mission_id: str, *,
    base_url: str,
    git_repo_path: str,
    git_executable: str = "git",
    requests_timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> VerifierResult:
    record = chugel.get_mission(mission_id)
    state = record["state"]
    if state not in ("DEPLOY_PENDING", "VERIFYING_PRODUCTION"):
        raise ValueError(
            f"mission {mission_id}: deploy_verifier.run() requires DEPLOY_PENDING or "
            f"VERIFYING_PRODUCTION, got {state!r}"
        )

    deploy = record["deploy"]
    observation_started_at = deploy.get("observation_started_at")
    if not observation_started_at:
        raise DeployVerifierError(
            f"mission {mission_id}: deploy.observation_started_at is missing -- "
            "begin_deploy_observation() must run before deploy_verifier.run()"
        )

    if _remaining_budget(observation_started_at) <= 0:
        return _block(mission_id, "OBSERVATION_BUDGET_EXHAUSTED",
                       "production observation budget exhausted before any further poll work")

    invocation_id = chugel.reserve_deploy_verification(mission_id)
    if invocation_id is None:
        try:
            chugel.record_deploy_verification_skip(mission_id)
        except chugel.ChugelError:
            pass
        return VerifierResult("SKIPPED", state,
                               "another live process already holds the deploy-verification lock")

    try:
        record = chugel.get_mission(mission_id)
        state = record["state"]
        expected_sha = record["deploy"]["expected_sha"]

        if state == "DEPLOY_PENDING":
            outcome = _run_deploy_pending_phase(
                mission_id, base_url=base_url, expected_sha=expected_sha,
                git_repo_path=git_repo_path, git_executable=git_executable,
                observation_started_at=observation_started_at, requests_timeout=requests_timeout,
            )
            if outcome.status != "ADVANCED":
                return outcome

        return _run_verifying_production_phase(
            mission_id, base_url=base_url,
            observation_started_at=observation_started_at, requests_timeout=requests_timeout,
        )
    finally:
        try:
            chugel.finalize_deploy_verification(mission_id, invocation_id)
        except chugel.ChugelError:
            pass
