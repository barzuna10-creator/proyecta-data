"""M2A -- Per-Mission Workspace Provisioning.

Real `git worktree` primitives for giving each mission its own isolated
checkout, replacing the single shared repository_root M1's architecture
assumed. Deliberately standalone and unwired: nothing in this module is
called from any live mission lifecycle path yet (jarvis/mission_coordinator.py,
jarvis/mission_supervisor.py, jarvis/mission_write.py are all untouched by
this change) -- see the M2 Design V1 / M2A implementation-readiness
report for the full rationale and phased rollout plan.

Design invariants, all deliberately mirroring patterns this codebase
already established for orchestrator/chugel.py:
- Every identity (path, branch) is DERIVED, deterministically, from
  `mission_id` alone -- a caller never supplies a worktree path or
  branch name for this module to trust. This is what makes path
  collision structurally impossible, not just conventionally avoided.
- Every real filesystem/git decision is made from the registry
  (`git worktree list --porcelain`), never from a bare `Path.exists()`
  check alone -- a plain existing directory and a real registered
  worktree are different facts, and conflating them is exactly the
  class of bug that could delete or silently trust the wrong thing.
- `mission_id` is validated against the SAME canonical pattern
  orchestrator/chugel.py itself reads from
  orchestrator/schemas/mission_record.schema.json (not a duplicated,
  driftable regex) before it is ever used to build a path.
- No raw `git` stderr, and no Python exception text, is ever persisted
  or logged as a "reason" -- only a fixed, closed reason_code. Every
  raised exception's own human-readable message is for a developer
  reading a traceback, never something any caller is allowed to
  branch on or durably record.
- No destructive action (`git worktree remove --force`, or treating a
  path as "safe to reuse") is ever taken without first confirming, via
  the registry, that the path is unambiguously this exact mission's own
  worktree (registered path AND registered branch both match this
  mission's own deterministic identity). A path that merely LOOKS right
  is never enough.
- Symlinks are never trusted: a symlink sitting at a mission's
  deterministic path is always treated as ambiguous/foreign, never as
  "the mission's own worktree happens to be reached via a symlink" --
  regardless of what it appears to point to.

Corrective round (closing a real, independently-reviewed TOCTOU P0 in
the original provisioning path -- a bare `Path.is_symlink()` check,
followed moments later by handing the same path STRING to `git worktree
add` as a subprocess argument, left a real window where a symlink
raced into place in between could make git write a full checkout to an
attacker-chosen location outside base_root/missions/): every directory
this module creates or trusts as a fresh destination is now reached via
an `openat`-style chain -- `os.open(..., O_DIRECTORY | O_NOFOLLOW,
dir_fd=...)` at every level, base_root -> missions -> mission_id, each
step relative to a file descriptor obtained from the PREVIOUS,
already-verified step, never by re-resolving a bare string path more
than once. `O_NOFOLLOW` makes each step fail closed immediately if a
symlink has been substituted at that exact component, whether that is
the leaf (the original P0) or an intermediate directory like `missions/`
itself (a race the original code had no defense against at all).
Verified empirically (scratch, not merely reasoned about) against real
symlink substitution at both the parent and leaf level, real concurrent
`os.mkdir()` races for the same mission_id (kernel-atomic: exactly one
of N simultaneous callers ever succeeds), and real `git worktree add`
behavior against a pre-existing empty directory (accepted, used
directly) versus a non-empty one (refused) -- see the M2A corrective
design report for the full experiment log.

Prevention happens BEFORE the side effect (the verified fd chain is
established, and the destination's identity captured, before `git
worktree add` is ever spawned) -- a post-add identity re-check exists
only as defense-in-depth on top of that, never as the primary
mechanism, and its own failure NEVER triggers any cleanup/deletion: an
identity that cannot be reconfirmed after `git worktree add` ran is, by
definition, a path whose ownership this module can no longer vouch for,
and this module's own governing rule -- ownership must be unambiguous
before ANY destructive or corrective action -- applies to itself here
exactly as it applies to remove_mission_worktree().

Honest, stated residual limitation: this closes the demonstrated
vulnerability class completely (nothing can make the destination `git
worktree add` STARTS writing into be anything other than the exact,
empty, freshly fd-verified directory this module created). It cannot,
and structurally cannot from outside git's own process, guarantee the
safety of every individual file git itself writes DURING its own
internal, multi-file population of that directory once started -- that
is git's own internal filesystem-safety responsibility, the same
boundary remove_mission_worktree() already relies on git's own
"points back to" validation for on the deletion side.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - fail-closed platform branch
    fcntl = None

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "mission_record.schema.json"
with open(_SCHEMA_PATH, encoding="utf-8") as _schema_file:
    _CANONICAL_SCHEMA = json.load(_schema_file)
# Read directly from the canonical schema (the same source
# orchestrator/chugel.py's own _MISSION_ID_PATTERN reads) -- never a
# separately hand-written regex that could silently drift from it.
MISSION_ID_PATTERN = re.compile(_CANONICAL_SCHEMA["properties"]["mission_id"]["pattern"])

_MISSIONS_SUBDIR = "missions"


class WorkspaceProvisionError(Exception):
    """Raised by provision_mission_worktree() -- always fail-closed: no
    partial/ambiguous state is ever left for a caller to misinterpret as
    success. `reason_code` is the only part of this exception any caller
    may durably record or branch on; `args`/str(exc) is for a human
    reading a traceback only."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class WorkspaceLeaseError(Exception):
    pass


@dataclass
class WorkspaceSupervisorLease:
    """Exclusive process-wide owner of mission-worktree coordination."""

    fd: int
    root_fd: int
    path: Path
    base_root: Path
    root_identity: tuple[int, int]
    git_identity: tuple[int, int]
    lock_identity: tuple[int, int]
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                fcntl.flock(self.root_fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            os.close(self.root_fd)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _open_canonical_absolute_directory(path: Path) -> tuple[int, Path]:
    """Open every component with O_NOFOLLOW; aliases are not trusted config."""
    raw = Path(path)
    if not raw.is_absolute() or str(raw) != os.path.normpath(str(raw)):
        raise WorkspaceLeaseError("UNSAFE_BASE_ROOT")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in raw.parts[1:]:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd, raw
    except BaseException:
        os.close(fd)
        raise


def validate_workspace_supervisor_lease(
    lease: WorkspaceSupervisorLease, base_root: Path,
) -> None:
    if lease._closed:
        raise WorkspaceLeaseError("LEASE_NOT_HELD")
    try:
        fd, root = _open_canonical_absolute_directory(base_root)
    except (OSError, WorkspaceLeaseError) as exc:
        raise WorkspaceLeaseError("UNSAFE_BASE_ROOT") from exc
    try:
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        git_fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            git_info = os.fstat(git_fd)
            lock_fd = os.open(
                lease.path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=git_fd,
            )
            try:
                lock_info = os.fstat(lock_fd)
            finally:
                os.close(lock_fd)
        finally:
            os.close(git_fd)
    except OSError as exc:
        raise WorkspaceLeaseError("LEASE_REPOSITORY_MISMATCH") from exc
    finally:
        os.close(fd)
    if (
        root != lease.base_root
        or identity != lease.root_identity
        or (git_info.st_dev, git_info.st_ino) != lease.git_identity
        or (lock_info.st_dev, lock_info.st_ino) != lease.lock_identity
        or not stat.S_ISREG(lock_info.st_mode)
        or lock_info.st_nlink != 1
        or lock_info.st_uid != os.geteuid()
        or stat.S_IMODE(lock_info.st_mode) != 0o600
    ):
        raise WorkspaceLeaseError("LEASE_REPOSITORY_MISMATCH")


def acquire_workspace_supervisor_lease(base_root: Path) -> WorkspaceSupervisorLease:
    """Acquire the sole non-blocking supervisor lease under a trusted real .git dir."""
    if fcntl is None:
        raise WorkspaceLeaseError("POSIX_FLOCK_UNAVAILABLE")
    try:
        root_fd, root = _open_canonical_absolute_directory(base_root)
        root_info = os.fstat(root_fd)
        root_identity = (root_info.st_dev, root_info.st_ino)
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            os.close(root_fd)
            raise WorkspaceLeaseError("LEASE_ALREADY_HELD") from exc
        try:
            git_fd = os.open(".git", os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            git_info = os.fstat(git_fd)
            git_identity = (git_info.st_dev, git_info.st_ino)
        except BaseException:
            fcntl.flock(root_fd, fcntl.LOCK_UN)
            os.close(root_fd)
            raise
    except WorkspaceLeaseError:
        raise
    except OSError as exc:
        raise WorkspaceLeaseError("UNSAFE_GIT_DIRECTORY") from exc
    name = "jarvis-workspace-supervisor.lock"
    lease_ready = False
    try:
        try:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=git_fd)
            created = False
        except FileNotFoundError:
            try:
                fd = os.open(
                    name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=git_fd,
                )
                created = True
            except OSError as exc:
                raise WorkspaceLeaseError("UNSAFE_LEASE_FILE") from exc
        except OSError as exc:
            raise WorkspaceLeaseError("UNSAFE_LEASE_FILE") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise WorkspaceLeaseError("UNSAFE_LEASE_FILE")
            if created and stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(fd, 0o600)
                info = os.fstat(fd)
                if stat.S_IMODE(info.st_mode) != 0o600:
                    raise WorkspaceLeaseError("UNSAFE_LEASE_FILE")
            elif not created and stat.S_IMODE(info.st_mode) != 0o600:
                raise WorkspaceLeaseError("UNSAFE_LEASE_FILE")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                raise WorkspaceLeaseError("LEASE_ALREADY_HELD") from exc
            lease_ready = True
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(git_fd)
        if not lease_ready:
            fcntl.flock(root_fd, fcntl.LOCK_UN)
            os.close(root_fd)
    lock_info = os.fstat(fd)
    return WorkspaceSupervisorLease(
        fd=fd, root_fd=root_fd, path=root / ".git" / name, base_root=root,
        root_identity=root_identity, git_identity=git_identity,
        lock_identity=(lock_info.st_dev, lock_info.st_ino),
    )


PROVISION_FAILURE_REASONS = frozenset({
    "INVALID_MISSION_ID",
    "BASE_ROOT_NOT_A_GIT_REPOSITORY",
    "GIT_EXECUTABLE_NOT_FOUND",
    "WORKTREE_LIST_FAILED",
    # A component of the base_root -> missions -> mission_id chain could
    # not be confirmed, via a real O_NOFOLLOW-guarded open, as a genuine
    # directory at the moment it was needed -- covers a symlink at ANY
    # level (not only the leaf), whether present from the start or
    # substituted mid-race; this module cannot and does not distinguish
    # those two causes, both are refused identically.
    "PATH_IS_SYMLINK",
    "PATH_EXISTS_UNRECOGNIZED",
    "BRANCH_MISMATCH_ON_RESUME",
    "HEAD_MISMATCH_ON_RESUME",
    "WORKTREE_ADD_FAILED",
    "POST_ADD_VERIFICATION_FAILED",
    # Defense-in-depth only, never the primary mechanism: the
    # destination's (st_dev, st_ino) identity, captured immediately
    # before `git worktree add` was spawned, did not match what a fresh
    # O_NOFOLLOW re-open confirms afterward. Ownership is unconfirmed --
    # this NEVER triggers cleanup/deletion of any kind.
    "POST_ADD_IDENTITY_MISMATCH",
})

RemovalOutcome = Literal["removed", "already_absent", "removal_failed"]
REMOVAL_OUTCOMES = frozenset({"removed", "already_absent", "removal_failed"})

# Round-1 Emma security review, P2, closed: previously declared but
# never actually returned by anything -- every real failure branch
# collapsed to the same opaque "removal_failed" string. remove_mission_worktree()
# now returns (outcome, reason_code), and every one of these is a real,
# reachable, individually-tested branch -- see
# ReasonCodeExhaustivenessTests.test_every_removal_failure_reason_is_reachable.
REMOVAL_FAILURE_REASONS = frozenset({
    "INVALID_MISSION_ID",
    "BASE_ROOT_NOT_A_GIT_REPOSITORY",
    "GIT_EXECUTABLE_NOT_FOUND",
    "WORKTREE_LIST_FAILED",
    "PATH_IS_SYMLINK",
    "BRANCH_IDENTITY_MISMATCH",
    "WORKTREE_REMOVE_FAILED",
    "WORKTREE_REMOVE_DID_NOT_TAKE_EFFECT",
    # Round-2 Emma security review, P2, closed: the leaf's (st_dev,
    # st_ino) identity, captured via the same O_NOFOLLOW fd chain
    # provisioning uses, changed between this function's first
    # confirmation (registry lookup + branch match) and its final
    # re-confirmation immediately before invoking `git worktree remove`.
    # Never triggers any cleanup of its own -- see
    # remove_mission_worktree()'s own docstring for the full rationale
    # and the honestly-documented trust boundary this does NOT close.
    "LEAF_IDENTITY_CHANGED_BEFORE_REMOVAL",
})

OrphanClassification = Literal["OWNED_ACTIVE", "OWNED_TERMINAL_CLEANED", "AMBIGUOUS_UNRECOGNIZED"]
ORPHAN_CLASSIFICATIONS = frozenset({"OWNED_ACTIVE", "OWNED_TERMINAL_CLEANED", "AMBIGUOUS_UNRECOGNIZED"})

# M2A's own deliberately conservative, narrow definition of "this
# mission's worktree is safe to reclaim automatically" -- the schema's
# literal terminal-bucket states only (COMPLETED/FAILED/CANCELLED/
# ROLLED_BACK), NOT the full workspace-ownership-release table
# (jarvis/mission_coordinator.py's _REPOSITORY_ROOT_OWNING_STATES's
# complement, which also includes MERGED/DEPLOY_PENDING/
# VERIFYING_PRODUCTION as "no longer actively dispatching" but not yet
# schema-terminal). This module lives in orchestrator/ and must not
# import jarvis/ (the established dependency direction runs the other
# way; doing so would risk exactly the kind of import-layering violation
# tests/test_jarvis_foundation_boundaries.py exists to catch) -- so
# rather than duplicate that broader, authority-adjacent table here,
# M2A reconciliation only auto-reclaims the narrowest, most
# unambiguously-safe set, cross-pinned by a test against
# jarvis.status.classify_mission_state()'s own "terminal" bucket. A
# MERGED-but-not-COMPLETED mission's worktree is left alone by M2A --
# not a bug, a deliberately conservative scope boundary; wiring a
# broader, lifecycle-aware policy is explicitly deferred to whichever
# later phase actually calls this module from jarvis-side code (which
# CAN safely make that judgment).
RECLAIMABLE_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "ROLLED_BACK"})


@dataclass(frozen=True)
class WorktreeRegistryEntry:
    path: Path
    head: str | None
    branch: str | None  # None for a detached-HEAD or bare entry -- never assumed present.


@dataclass(frozen=True)
class OrphanReport:
    path: Path
    mission_id: str | None  # None when the path's shape itself doesn't parse as ours.
    classification: OrphanClassification
    removal_outcome: RemovalOutcome | None  # only set when classification == OWNED_TERMINAL_CLEANED
    removal_reason_code: str | None = None  # set only when removal_outcome == "removal_failed"


def _validate_mission_id(mission_id: str) -> None:
    if not isinstance(mission_id, str) or not MISSION_ID_PATTERN.fullmatch(mission_id):
        raise WorkspaceProvisionError("INVALID_MISSION_ID", f"mission_id {mission_id!r} is not schema-valid")


def derive_worktree_path(mission_id: str, base_root: Path) -> Path:
    """Pure, deterministic, no I/O. The mission_id IS the path identity
    -- two different missions can never collide on a path by
    construction, never merely by convention."""
    _validate_mission_id(mission_id)
    return Path(base_root).resolve() / _MISSIONS_SUBDIR / mission_id


def derive_branch_name(mission_id: str) -> str:
    """Pure, deterministic, no I/O."""
    _validate_mission_id(mission_id)
    return f"mission/{mission_id}"


def _require_git_executable(git_executable: str) -> None:
    import shutil
    if shutil.which(git_executable) is None and not Path(git_executable).is_file():
        raise WorkspaceProvisionError("GIT_EXECUTABLE_NOT_FOUND", f"{git_executable!r} not found")


def _require_base_root(base_root: Path, git_executable: str) -> Path:
    resolved = Path(base_root).resolve()
    if not (resolved / ".git").exists():
        raise WorkspaceProvisionError(
            "BASE_ROOT_NOT_A_GIT_REPOSITORY", f"{resolved} has no .git -- not a real git repository"
        )
    return resolved


def _list_registered_worktrees(base_root: Path, git_executable: str) -> dict[Path, WorktreeRegistryEntry]:
    """Ground truth for "what does git itself believe exists" -- every
    provisioning/removal/reconciliation decision in this module reads
    this, never a bare filesystem existence check alone."""
    try:
        result = subprocess.run(
            [git_executable, "-C", str(base_root), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
    except OSError as exc:
        raise WorkspaceProvisionError("WORKTREE_LIST_FAILED", f"git worktree list could not run: {exc.__class__.__name__}") from exc
    if result.returncode != 0:
        raise WorkspaceProvisionError("WORKTREE_LIST_FAILED", f"git worktree list exited {result.returncode}")

    entries: dict[Path, WorktreeRegistryEntry] = {}
    current_path: Path | None = None
    current_head: str | None = None
    current_branch: str | None = None

    def _flush():
        if current_path is not None:
            entries[current_path] = WorktreeRegistryEntry(path=current_path, head=current_head, branch=current_branch)

    for line in result.stdout.splitlines():
        if line == "":
            _flush()
            current_path, current_head, current_branch = None, None, None
            continue
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):]).resolve()
        elif line.startswith("HEAD "):
            current_head = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current_branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        # "bare", "detached", "prunable ..." and any other porcelain
        # line this module doesn't currently need are deliberately
        # ignored, not misparsed.
    _flush()  # the last record has no trailing blank line
    return entries


def _open_dir_nofollow(name: str, *, dir_fd: int) -> int:
    """openat()-equivalent: opens `name` strictly relative to `dir_fd`
    (never re-resolving a bare string path from the filesystem root),
    refusing to follow a symlink at the final component. This is the
    one primitive every identity confirmation in this module's
    provisioning path is built from -- a real, held file descriptor
    naming exactly what was checked, not a path string that could be
    re-resolved differently a moment later."""
    return os.open(name, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _open_root_nofollow(resolved_root: Path) -> int:
    return os.open(str(resolved_root), os.O_DIRECTORY | os.O_NOFOLLOW)


def _mkdir_or_confirm_real_dir(name: str, *, dir_fd: int, mode: int = 0o700) -> tuple[str, int | None]:
    """Atomically create `name` relative to `dir_fd` (`os.mkdir` with
    `dir_fd=` fails with FileExistsError if ANYTHING -- directory, file,
    or symlink -- already occupies that exact name; this is a kernel
    guarantee, verified empirically: of N real concurrent callers racing
    the identical mkdir, exactly one ever succeeds). If something is
    already there, confirm -- via the same O_NOFOLLOW open, never by
    trusting the mere fact that mkdir failed -- whether it is a real,
    safely-reachable directory (a legitimate prior or concurrent
    provisioner) or something this module cannot vouch for (a symlink,
    or corrupted/foreign content).

    Returns:
      ("created", fd)            -- freshly created by this call, fd-verified.
      ("existing_real_dir", fd)  -- already existed, fd-confirmed as a real directory.
      ("ambiguous", None)        -- could not be confirmed safe, whether freshly
                                     swapped mid-race or already ambiguous when
                                     this call started; the two are
                                     deliberately indistinguishable to every
                                     caller -- both are refused identically."""
    try:
        os.mkdir(name, mode=mode, dir_fd=dir_fd)
        just_created = True
    except FileExistsError:
        just_created = False

    try:
        fd = _open_dir_nofollow(name, dir_fd=dir_fd)
    except OSError:
        return "ambiguous", None
    return ("created" if just_created else "existing_real_dir"), fd


def provision_mission_worktree(
    mission_id: str, *, base_root: Path, base_sha: str, git_executable: str = "git",
) -> Path:
    """Real, side-effecting: creates a real git worktree for this
    mission, or -- idempotently -- confirms one already correctly
    exists (the crash-mid-provision resume case, and the legitimate
    concurrent-provisioner-for-the-same-mission_id case). Always
    fail-closed: never silently reuses, deletes, or overwrites anything
    it cannot unambiguously confirm is this exact mission's own,
    correctly provisioned worktree.

    Every directory this function creates or trusts as a fresh
    destination is reached via a dir_fd chain (base_root -> missions ->
    mission_id), each level opened O_NOFOLLOW relative to the PREVIOUS
    already-verified level -- see the module's own top-of-file
    docstring for the full rationale and the corrective-round design
    this replaced."""
    _validate_mission_id(mission_id)
    _require_git_executable(git_executable)
    resolved_base_root = _require_base_root(base_root, git_executable)
    path = derive_worktree_path(mission_id, resolved_base_root)
    branch = derive_branch_name(mission_id)

    try:
        base_fd = _open_root_nofollow(resolved_base_root)
    except OSError as exc:
        raise WorkspaceProvisionError("PATH_IS_SYMLINK", f"{resolved_base_root} could not be opened as a real directory: {exc.__class__.__name__}") from exc
    try:
        missions_status, missions_fd = _mkdir_or_confirm_real_dir(_MISSIONS_SUBDIR, dir_fd=base_fd, mode=0o755)
        if missions_fd is None:
            raise WorkspaceProvisionError("PATH_IS_SYMLINK", f"{resolved_base_root / _MISSIONS_SUBDIR} could not be confirmed as a real, unswapped directory")
        try:
            leaf_status, leaf_fd = _mkdir_or_confirm_real_dir(mission_id, dir_fd=missions_fd, mode=0o700)
            if leaf_fd is None:
                raise WorkspaceProvisionError("PATH_IS_SYMLINK", f"{path} could not be confirmed as a real, unswapped directory")

            if leaf_status == "existing_real_dir":
                # A real, fd-confirmed directory already sits here --
                # either a prior provisioning attempt (crash-mid-provision
                # resume) or a genuinely concurrent provisioner for the
                # SAME mission_id that won the atomic mkdir race. Either
                # way, the registry -- not this fd check alone -- is what
                # decides whether it is correctly provisioned.
                os.close(leaf_fd)
                registry = _list_registered_worktrees(resolved_base_root, git_executable)
                existing = registry.get(path)
                if existing is None:
                    # fd-confirmed real directory, but git's own registry
                    # does not recognize it as a worktree -- ambiguous
                    # foreign content (or a still-in-progress concurrent
                    # provisioner that has not yet reached `git worktree
                    # add`); never reused, never overwritten. A caller
                    # that legitimately expects a concurrent provisioner
                    # to finish shortly is responsible for its own retry
                    # policy -- this function makes exactly one honest
                    # attempt and reports what it can currently confirm.
                    raise WorkspaceProvisionError(
                        "PATH_EXISTS_UNRECOGNIZED",
                        f"{path} exists as a real directory but is not (yet) a registered git worktree of {resolved_base_root}",
                    )
                if existing.branch != branch:
                    raise WorkspaceProvisionError(
                        "BRANCH_MISMATCH_ON_RESUME",
                        f"{path} is already a registered worktree, but on branch {existing.branch!r}, expected {branch!r}",
                    )
                if existing.head != base_sha:
                    raise WorkspaceProvisionError(
                        "HEAD_MISMATCH_ON_RESUME",
                        f"{path} is already a registered worktree, but at HEAD {existing.head!r}, expected {base_sha!r}",
                    )
                return path

            # leaf_status == "created": a fresh, empty, fd-verified
            # directory -- capture its identity before releasing the fd
            # chain and spawning git, so a post-add re-check has
            # something real to compare against.
            pre_stat = os.fstat(leaf_fd)
            pre_identity = (pre_stat.st_dev, pre_stat.st_ino)
            os.close(leaf_fd)
        finally:
            os.close(missions_fd)
    finally:
        os.close(base_fd)

    # The fd chain above is what PREVENTS the vulnerability -- by the
    # time we reach here, `path` names a real, empty, freshly-verified
    # directory this call itself just created; nothing between the
    # verification above and this exact next line does any other I/O
    # that could widen the remaining, structurally-irreducible window
    # (see module docstring's "honest residual limitation").
    try:
        result = subprocess.run(
            [git_executable, "-C", str(resolved_base_root), "worktree", "add", str(path), "-b", branch, base_sha],
            capture_output=True, text=True, timeout=120,
        )
    except OSError as exc:
        raise WorkspaceProvisionError("WORKTREE_ADD_FAILED", f"git worktree add could not run: {exc.__class__.__name__}") from exc
    if result.returncode != 0:
        raise WorkspaceProvisionError("WORKTREE_ADD_FAILED", f"git worktree add exited {result.returncode}")

    # Defense-in-depth ONLY -- never the primary mechanism (that was the
    # fd chain above, which ran BEFORE git was ever spawned). A mismatch
    # here means something changed the destination's identity during
    # git's own run; ownership can no longer be vouched for, and this
    # deliberately NEVER attempts any cleanup or removal in that case --
    # an unconfirmed identity is exactly the ambiguous case this whole
    # module's governing rule refuses to act destructively on.
    try:
        post_base_fd = _open_root_nofollow(resolved_base_root)
        try:
            post_missions_fd = _open_dir_nofollow(_MISSIONS_SUBDIR, dir_fd=post_base_fd)
            try:
                post_leaf_fd = _open_dir_nofollow(mission_id, dir_fd=post_missions_fd)
                try:
                    post_stat = os.fstat(post_leaf_fd)
                finally:
                    os.close(post_leaf_fd)
            finally:
                os.close(post_missions_fd)
        finally:
            os.close(post_base_fd)
    except OSError as exc:
        raise WorkspaceProvisionError(
            "POST_ADD_IDENTITY_MISMATCH",
            f"{path} could not be re-confirmed as a real directory after git worktree add: {exc.__class__.__name__}",
        ) from exc
    if (post_stat.st_dev, post_stat.st_ino) != pre_identity:
        raise WorkspaceProvisionError(
            "POST_ADD_IDENTITY_MISMATCH",
            f"{path}'s filesystem identity changed during git worktree add -- ownership unconfirmed, no cleanup attempted",
        )

    # Existing registry-based post-add verification (branch/HEAD match),
    # unchanged from before the corrective round -- both the identity
    # check above AND this one must pass.
    post_registry = _list_registered_worktrees(resolved_base_root, git_executable)
    confirmed = post_registry.get(path)
    if confirmed is None or confirmed.branch != branch or confirmed.head != base_sha:
        raise WorkspaceProvisionError(
            "POST_ADD_VERIFICATION_FAILED",
            f"git worktree add reported success but the registry does not confirm {path} at {branch}@{base_sha}",
        )
    return path


def verify_mission_worktree(
    mission_id: str, *, base_root: Path, git_executable: str = "git",
) -> WorktreeRegistryEntry:
    """Lifecycle-neutral verification of the deterministic registered worktree.

    This deliberately does not compare HEAD with the original base SHA: later
    lifecycle phases legitimately advance HEAD. Phase-specific policy belongs
    to the Jarvis coordinator.
    """
    _validate_mission_id(mission_id)
    _require_git_executable(git_executable)
    resolved = _require_base_root(base_root, git_executable)
    path = derive_worktree_path(mission_id, resolved)
    expected_branch = derive_branch_name(mission_id)
    identity, reason = _capture_leaf_identity(resolved, mission_id)
    if identity is None:
        raise WorkspaceProvisionError(reason or "PATH_IS_SYMLINK", "worktree identity unavailable")
    registry = _list_registered_worktrees(resolved, git_executable)
    entry = registry.get(path)
    if entry is None:
        raise WorkspaceProvisionError("PATH_EXISTS_UNRECOGNIZED", "worktree is not registered")
    if entry.branch != expected_branch:
        raise WorkspaceProvisionError("BRANCH_MISMATCH_ON_RESUME", "registered branch differs")
    if not isinstance(entry.head, str) or re.fullmatch(r"[0-9a-f]{40}", entry.head) is None:
        raise WorkspaceProvisionError("HEAD_MISMATCH_ON_RESUME", "registered HEAD is not canonical")
    return entry


def _capture_leaf_identity(resolved_base_root: Path, mission_id: str) -> tuple[tuple[int, int] | None, str | None]:
    """The same O_NOFOLLOW fd chain provisioning uses (base_root ->
    missions -> mission_id), but read-only: proves, at this exact
    instant, that the mission's deterministic path is a real,
    unswapped, fd-verified directory, and returns its (st_dev, st_ino)
    identity. Returns (None, reason_code) on any failure -- a missing
    `missions/` or missing leaf is reported the same as any other
    verification failure here (the caller, remove_mission_worktree(),
    is the one that already knows via the registry whether "missing" is
    expected; this helper makes no absence/presence judgment of its
    own, it only ever confirms or refuses to confirm an identity)."""
    try:
        base_fd = _open_root_nofollow(resolved_base_root)
    except OSError:
        return None, "PATH_IS_SYMLINK"
    try:
        try:
            missions_fd = _open_dir_nofollow(_MISSIONS_SUBDIR, dir_fd=base_fd)
        except OSError:
            return None, "PATH_IS_SYMLINK"
        try:
            try:
                leaf_fd = _open_dir_nofollow(mission_id, dir_fd=missions_fd)
            except OSError:
                return None, "PATH_IS_SYMLINK"
            try:
                st = os.fstat(leaf_fd)
                return (st.st_dev, st.st_ino), None
            finally:
                os.close(leaf_fd)
        finally:
            os.close(missions_fd)
    finally:
        os.close(base_fd)


def remove_mission_worktree(
    mission_id: str, *, base_root: Path, git_executable: str = "git",
) -> tuple[RemovalOutcome, str | None]:
    """Never raises -- a closed three-way outcome, exactly mirroring
    dispatch_ledger's own closed-status pattern, paired with a
    reason_code that is populated whenever (and only when) outcome is
    "removal_failed" -- a member of REMOVAL_FAILURE_REASONS, never raw
    text (Round-1 Emma review, P2: this vocabulary was previously
    declared but never actually returned; every branch below now
    returns its own real, distinct member). `already_absent` is
    determined explicitly, from the registry, BEFORE any removal is
    attempted -- `git worktree remove` on a path git does not recognize
    as a worktree fails with the same generic error whether the path
    never existed, was already removed, or is a completely foreign
    directory that merely happens to sit at this mission's deterministic
    path -- so that ambiguity is resolved here, deliberately, rather
    than papered over by treating any such failure as harmless.

    `already_absent` means precisely "no registered worktree exists at
    this path" -- a true, registry-derived fact -- NOT "the path is
    clear" or "safe to reprovision." A stray, foreign, non-worktree
    directory at the deterministic path correctly yields
    `already_absent` here (there is genuinely no worktree for this
    function to remove) while being left completely untouched (removal
    only ever acts on what the registry itself recognizes as ours);
    provision_mission_worktree()'s own independent PATH_EXISTS_UNRECOGNIZED
    check is what refuses to reuse that same stray path later -- the two
    functions deliberately make separate, narrow claims rather than one
    conflated "is this path okay" answer.

    Round-2 Emma security review hardening: this function now performs
    the SAME O_NOFOLLOW fd-chain identity confirmation
    (base_root -> missions -> mission_id) provisioning uses, TWICE --
    once right after the initial registry lookup confirms this is a
    real, branch-matching worktree, and again immediately before
    invoking `git worktree remove`, comparing the two (st_dev, st_ino)
    captures and re-confirming the registry's branch match fresh a
    second time. Any mismatch, or any fd-chain verification failure at
    either point, fails closed with a real, closed reason_code and --
    per this function's own governing rule -- NEVER attempts any manual
    cleanup (`rm`, `shutil.rmtree`, `unlink`, or equivalent) of its own;
    the only destructive action this module ever takes is delegating to
    `git worktree remove` itself, and only once ownership has been
    reconfirmed as unambiguous immediately beforehand.

    Honest, stated residual trust boundary -- this does NOT, and cannot,
    structurally eliminate the deletion-side race the way provisioning's
    fd chain eliminates ITS race: `git worktree remove <path>` is an
    external process that necessarily re-resolves the path STRING
    itself, one more time, at the moment it actually acts -- there is no
    way to hand git a destination by file descriptor instead. The gap
    between this function's own last fd-chain confirmation and git's own
    internal resolution is real and irreducible from outside git's
    process, exactly like provisioning's own documented residual
    limitation. What closes THIS gap, specifically for deletion (and NOT
    available to provisioning, which is creating new content with no
    pre-existing identity to check against), is git's own internal
    consistency check: `git worktree remove` refuses to act unless the
    target's own on-disk `.gitdir` pointer resolves back to the exact
    worktree administrative record git itself is removing -- confirmed
    directly, twice now, by independent adversarial testing (see
    RemovalRaceAdversarialTests): racing a symlink into this
    mission's own path in the remaining window, pointed at a second,
    real, unrelated worktree OR at a real non-worktree directory, does
    not enable deletion of that foreign target -- git refuses regardless
    of --force, and the foreign target survives completely untouched.
    This function's own fd-chain re-confirmation narrows the window as
    far as is achievable from outside git's process; git's own identity
    check is what closes what remains."""
    try:
        _validate_mission_id(mission_id)
        _require_git_executable(git_executable)
        resolved_base_root = _require_base_root(base_root, git_executable)
    except WorkspaceProvisionError as exc:
        return "removal_failed", exc.reason_code

    path = derive_worktree_path(mission_id, resolved_base_root)
    branch = derive_branch_name(mission_id)

    if path.is_symlink():
        # Early gate, BEFORE the registry lookup: a foreign symlink
        # planted at this exact deterministic path is never a
        # registered git worktree, so without this check it would fall
        # straight through to "not in the registry" -> already_absent
        # below -- silently conflating "a symlink is sitting here,
        # unregistered" with "genuinely nothing is here at all". Two
        # different facts; only the second one is ever "already_absent".
        return "removal_failed", "PATH_IS_SYMLINK"

    try:
        registry = _list_registered_worktrees(resolved_base_root, git_executable)
    except WorkspaceProvisionError:
        return "removal_failed", "WORKTREE_LIST_FAILED"

    existing = registry.get(path)
    if existing is None:
        # Explicitly confirmed absent from git's own registry -- the
        # ONLY basis on which this function ever reports
        # "already_absent". A bare Path.exists() check is deliberately
        # never used for this determination (see module docstring).
        return "already_absent", None

    if existing.branch != branch:
        # Registered, but not unambiguously THIS mission's own worktree
        # -- never delete something whose identity cannot be confirmed,
        # no matter how suggestive the path alone looks.
        return "removal_failed", "BRANCH_IDENTITY_MISMATCH"

    # First fd-chain identity confirmation: proves, at this instant,
    # that the path the registry just told us about is a real,
    # unswapped directory -- and captures its identity as a baseline.
    first_identity, reason_code = _capture_leaf_identity(resolved_base_root, mission_id)
    if first_identity is None:
        return "removal_failed", reason_code

    # Second, final fd-chain confirmation, immediately before the
    # destructive call, with no other I/O in between except the fresh
    # registry re-check right below -- mirrors provisioning's own
    # "verify right before the side effect" discipline exactly.
    second_identity, reason_code = _capture_leaf_identity(resolved_base_root, mission_id)
    if second_identity is None:
        return "removal_failed", reason_code
    if second_identity != first_identity:
        # The leaf's real, physical identity changed between our two
        # confirmations -- something swapped it (deleted and recreated,
        # even if the replacement is itself a real, non-symlink
        # directory). Ownership of THIS identity was never
        # reconfirmed; fail closed. No manual cleanup is ever attempted
        # here -- this deliberately leaves resolution to a human/operator,
        # exactly like every other ambiguous case in this module.
        return "removal_failed", "LEAF_IDENTITY_CHANGED_BEFORE_REMOVAL"

    try:
        fresh_registry = _list_registered_worktrees(resolved_base_root, git_executable)
    except WorkspaceProvisionError:
        return "removal_failed", "WORKTREE_LIST_FAILED"
    fresh_existing = fresh_registry.get(path)
    if fresh_existing is None or fresh_existing.branch != branch:
        # The registry itself changed underneath us between the first
        # and final confirmations -- same fail-closed treatment as the
        # original checks above, re-applied fresh.
        return "removal_failed", "BRANCH_IDENTITY_MISMATCH"

    try:
        result = subprocess.run(
            [git_executable, "-C", str(resolved_base_root), "worktree", "remove", "--force", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except OSError:
        return "removal_failed", "WORKTREE_REMOVE_FAILED"
    if result.returncode != 0:
        return "removal_failed", "WORKTREE_REMOVE_FAILED"

    # Confirm the registry actually reflects the removal -- never trust
    # the exit code alone, the same discipline as provisioning's
    # post-add verification.
    try:
        post_registry = _list_registered_worktrees(resolved_base_root, git_executable)
    except WorkspaceProvisionError:
        return "removal_failed", "WORKTREE_LIST_FAILED"
    if path in post_registry:
        return "removal_failed", "WORKTREE_REMOVE_DID_NOT_TAKE_EFFECT"
    return "removed", None


def find_and_reconcile_orphaned_worktrees(
    *, base_root: Path, list_missions, git_executable: str = "git",
) -> tuple[OrphanReport, ...]:
    """Read-only classification of every registered worktree under
    base_root's missions/ subdirectory, cross-referenced against Chugel's
    own disclosed list_missions() read seam -- plus real cleanup, but
    ONLY for the narrowest, unambiguously-safe RECLAIMABLE_TERMINAL_STATES
    set (see that constant's own docstring for why this is deliberately
    conservative). Every other case -- a mission still doing real work,
    an unparseable path, a mission_id Chugel does not recognize at all,
    or a Chugel read failure -- is reported but never touched.

    `list_missions` is injected (rather than importing orchestrator.chugel
    directly) so this module has no hard dependency on Chugel's own
    import surface; the real production caller passes
    orchestrator.chugel.list_missions.

    Run once, at process startup (matching mission_supervisor.py's own
    "not a poll loop" design principle) -- never a recurring poll. Not
    yet wired to any real startup path in M2A; this function exists,
    tested, standalone."""
    resolved_base_root = _require_base_root(base_root, git_executable)
    missions_root = (resolved_base_root / _MISSIONS_SUBDIR).resolve()
    registry = _list_registered_worktrees(resolved_base_root, git_executable)

    known_missions = {m["mission_id"]: m for m in list_missions()}

    reports: list[OrphanReport] = []
    for path in registry:
        if path == resolved_base_root:
            continue  # the primary checkout itself, not a mission worktree
        try:
            path.relative_to(missions_root)
        except ValueError:
            continue  # not under missions/ at all -- not this module's concern
        candidate_mission_id = path.name

        if not MISSION_ID_PATTERN.fullmatch(candidate_mission_id):
            reports.append(OrphanReport(path=path, mission_id=None, classification="AMBIGUOUS_UNRECOGNIZED", removal_outcome=None))
            continue

        listing = known_missions.get(candidate_mission_id)
        if listing is None or not listing.get("readable", False):
            # Unknown to Chugel, or Chugel itself cannot confirm this
            # mission's real state -- fail closed exactly like
            # jarvis/mission_coordinator.py's own
            # _mission_occupying_repository_root() treats an unreadable
            # record: undetermined is never treated as free/safe.
            reports.append(OrphanReport(path=path, mission_id=candidate_mission_id, classification="AMBIGUOUS_UNRECOGNIZED", removal_outcome=None))
            continue

        if listing.get("state") not in RECLAIMABLE_TERMINAL_STATES:
            reports.append(OrphanReport(path=path, mission_id=candidate_mission_id, classification="OWNED_ACTIVE", removal_outcome=None))
            continue

        outcome, reason_code = remove_mission_worktree(candidate_mission_id, base_root=resolved_base_root, git_executable=git_executable)
        reports.append(OrphanReport(
            path=path, mission_id=candidate_mission_id, classification="OWNED_TERMINAL_CLEANED",
            removal_outcome=outcome, removal_reason_code=reason_code,
        ))

    return tuple(reports)
